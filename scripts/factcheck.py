"""Phase 7: stance + claims for a stored run, verified, saved next to the run.

  uv run python scripts/factcheck.py --run 1 --mode record   # calls Gemini for untaped batches
  uv run python scripts/factcheck.py --run 1 --mode replay   # offline, from LLM tapes
"""

import argparse
import json
import re
import sys

from shelfradar.analyzer import DomainClassifier, MentionFinder
from shelfradar.api import DB_PATH, STUDY_DIR
from shelfradar.config import PROJECT_ROOT, load_settings
from shelfradar.factcheck import cross_engine_conflicts, extract, stance_summary
from shelfradar.llm import FallbackLLM, Gemini, TapedLLM
from shelfradar.orchestrator import load_study
from shelfradar.report import analyze_all
from shelfradar.store import Store

LLM_TAPES = PROJECT_ROOT / "fixtures" / "llm"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--mode", choices=["record", "replay"], default="replay")
    ap.add_argument("--pause", type=float, default=4.0, help="seconds between Gemini calls (free tier)")
    ap.add_argument("--fallback-model", default=None,
                    help="finish untaped batches with this model; taped ones keep their original model")
    args = ap.parse_args()

    settings = load_settings()
    study = load_study(STUDY_DIR / "whey.json")
    store = Store(DB_PATH)
    answers = store.answers(args.run)
    if not answers:
        print(f"run {args.run} has no answers")
        return 1

    inner = (Gemini(settings.gemini_api_key, settings.gemini_model)
             if args.mode == "record" and settings.gemini_api_key else None)
    llm = TapedLLM(inner, LLM_TAPES, args.mode, model=settings.gemini_model)
    if args.fallback_model:
        primary = TapedLLM(None, LLM_TAPES, "replay", model=settings.gemini_model)
        backup_inner = (Gemini(settings.gemini_api_key, args.fallback_model)
                        if args.mode == "record" and settings.gemini_api_key else None)
        backup = TapedLLM(backup_inner, LLM_TAPES, args.mode, model=args.fallback_model)
        llm = FallbackLLM(primary, backup)
    finder = MentionFinder(study.brands, study.category_terms)
    domains = DomainClassifier.load(study.brands, STUDY_DIR / "domain_classes.json")

    result = extract(analyze_all(answers, study, domains), finder, llm, category=study.category,
                     pause=args.pause if args.mode == "record" else 0,
                     retry_after=30.0 if args.mode == "record" else 0)
    taped = [llm.primary, llm.fallback] if isinstance(llm, FallbackLLM) else [llm]
    calls, replayed = sum(t.calls for t in taped), sum(t.replayed for t in taped)
    model_of_answer = {}
    if isinstance(llm, FallbackLLM):
        for prompt, model in llm.model_for_prompt.items():
            for aid in re.findall(r'<answer id="([^"]+)"', prompt):
                model_of_answer[aid] = model
    payload = {
        "readings": {k: v.model_dump() for k, v in result.readings.items()},
        "dropped": result.dropped,
        "failed_batches": result.failed_batches,
        "failures": result.failures,
        "gemini_calls": calls,
        "replayed": replayed,
        "model": settings.gemini_model if not model_of_answer
                 else " + ".join(sorted(set(model_of_answer.values()))),
        "model_of_answer": model_of_answer,
    }
    store.save_factcheck(args.run, payload)

    print(f"readings: {len(result.readings)} · gemini calls: {calls} · replayed: {replayed} · "
          f"failed batches: {result.failed_batches} · dropped by verification: {len(result.dropped)}")
    for reason in result.failures:
        print("  failed:", reason)
    print(json.dumps({"stance": stance_summary(result.readings),
                      "conflicts": cross_engine_conflicts(result.readings),
                      "dropped": result.dropped}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
