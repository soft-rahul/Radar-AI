"""Prepare the offline demo: rebuild the database from the saved SerpApi and Gemini tapes.
No keys, no searches. Used by `make demo`."""

import asyncio
import subprocess
import sys

from shelfradar.api import DB_PATH, STUDY_DIR
from shelfradar.orchestrator import is_recorded, load_questions, load_study, plan_grid, run_scan
from shelfradar.serp_client import SerpClient
from shelfradar.store import Store


async def rebuild(store: Store) -> int:
    study = load_study(STUDY_DIR / "whey.json")
    client = SerpClient(api_key=None, mode="replay", max_searches=0)
    cells = [c for c in plan_grid(study, load_questions(STUDY_DIR / "questions.json"), samples=3)
             if is_recorded(c, client)]
    run_id = store.create_run(study="whey", mode="replay", planned_cells=len(cells))
    async with client:
        await run_scan(client, store, run_id, cells)
    return run_id


def main() -> int:
    store = Store(DB_PATH)
    done = [r for r in store.list_runs() if r["status"] == "complete" and r["done_cells"] >= 90]
    run_id = done[0]["id"] if done else asyncio.run(rebuild(store))
    print(f"demo run: {run_id} ({store.get_run(run_id)['done_cells']} answers, 0 searches)")
    if not store.factcheck(run_id):
        subprocess.run([sys.executable, "scripts/factcheck.py", "--run", str(run_id), "--mode", "replay"],
                       check=False)
    print("open http://localhost:8765  (dashboard)  ·  http://localhost:8765/docs  (API)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
