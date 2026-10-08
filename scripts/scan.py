"""Run the study from the terminal.

  uv run python scripts/scan.py --dry-run                 # estimate only, spends nothing
  uv run python scripts/scan.py --mode record             # real scan; saves every response as a cassette
  uv run python scripts/scan.py --mode replay             # offline, from cassettes only
  uv run python scripts/scan.py --live-demo               # 1 question x 3 engines, ~4 searches, YOUR key

A record scan refuses to start when the estimate exceeds the searches left on the account
or the --max ceiling. Re-running a record scan resumes: recorded cells cost nothing.
"""

import argparse
import asyncio
import json
import math
import sys

from shelfradar.api import DB_PATH, STUDY_DIR
from shelfradar.analyzer import DomainClassifier
from shelfradar.config import load_settings
from shelfradar.models import Engine, Status
from shelfradar.orchestrator import (
    FOLLOW_UP_ALLOWANCE,
    Estimate,
    estimate,
    is_recorded,
    load_questions,
    load_study,
    plan_grid,
    run_scan,
)
from shelfradar.report import build_report
from shelfradar.serp_client import SerpClient
from shelfradar.store import Store


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["record", "replay", "live"], default="replay")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--max", type=int, default=None, help="hard ceiling on searches for this run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--focus", default="MuscleBlaze")
    ap.add_argument("--live-demo", action="store_true",
                    help="fresh answers for one question in English from all 3 engines (~4 searches)")
    ap.add_argument("--question", default="q1", help="question id for --live-demo")
    args = ap.parse_args()
    if args.live_demo:
        args.mode, args.samples, args.max = "live", 1, args.max or 6

    settings = load_settings()
    study = load_study(STUDY_DIR / "whey.json")
    cells = plan_grid(study, load_questions(STUDY_DIR / "questions.json"), samples=args.samples)
    if args.live_demo:
        cells = [c for c in cells if c.question_id == args.question and c.variant.id == "mumbai-en"]
        if not settings.serpapi_key:
            print("--live-demo needs SERPAPI_KEY in .env (your own key). Without a key, use --mode replay.")
            return 1
    ceiling = args.max if args.max is not None else settings.max_searches

    client = SerpClient(api_key=settings.serpapi_key if args.mode in ("record", "live") else None,
                        mode=args.mode, max_searches=ceiling)
    async with client:
        est = estimate(cells, client)
        if args.mode == "live":  # live never reuses tapes: every cell is paid for
            aio = sum(c.engine is Engine.AI_OVERVIEW for c in cells)
            est = Estimate(len(cells), 0, len(cells), math.ceil(aio * FOLLOW_UP_ALLOWANCE))
        left = await client.searches_left() if settings.serpapi_key else None
        print(f"grid: {est.cells} cells · already recorded: {est.cached} · to fetch: {est.to_fetch} "
              f"· AI Overview follow-up allowance: {est.follow_up_allowance}")
        print(f"estimated searches: {est.searches} · run ceiling (--max): {ceiling} · "
              f"searches left on account: {left}")

        if args.dry_run:
            return 0
        if args.mode in ("record", "live"):
            limit = min(ceiling, left) if left is not None else ceiling
            if est.searches > limit:
                print(f"REFUSED: estimate {est.searches} > limit {limit}. Raise --max or reduce the grid.")
                return 2

        if args.mode == "replay":
            cells = [c for c in cells if is_recorded(c, client)]
            print(f"replaying the {len(cells)} recorded cells")

        store = Store(DB_PATH)
        run_id = store.create_run(study="whey", mode=args.mode, planned_cells=len(cells))
        done = 0

        def progress(answer) -> None:
            nonlocal done
            done += 1
            flag = "" if answer.status is Status.OK else f"  <- {answer.status.value} {answer.error or ''}"
            print(f"  [{done:3d}/{len(cells)}] {answer.question_id} {answer.engine.value:11} "
                  f"{answer.variant:15} s{answer.sample}{flag}", flush=True)

        status = await run_scan(client, store, run_id, cells, on_answer=progress)
        print(f"\nrun {run_id}: {status} · live searches: {client.ledger.live_calls} · "
              f"replayed: {client.ledger.replayed} · retries: {client.ledger.retries}")

    domains = DomainClassifier.load(study.brands, STUDY_DIR / "domain_classes.json")
    report = build_report(store.answers(run_id), study, domains, args.focus)
    print(json.dumps({k: report[k] for k in ("status", "overall", "outside_top10", "translated_share")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
