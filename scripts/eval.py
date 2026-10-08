"""Score the analyzer against human labels (study/labels.json) and write docs/eval.md.

  uv run python scripts/eval.py
"""

import json
import sys

from shelfradar.analyzer import MentionFinder
from shelfradar.api import DB_PATH, STUDY_DIR
from shelfradar.config import PROJECT_ROOT
from shelfradar.labels import load_labels, score, score_verified
from shelfradar.orchestrator import load_study
from shelfradar.store import Store


def main() -> int:
    saved = load_labels()
    if not saved:
        print("No labels yet: open http://localhost:8765/label.html?run=2 and press Save labels.")
        return 1
    study = load_study(STUDY_DIR / "whey.json")
    answers = Store(DB_PATH).answers(saved["run_id"])
    finder = MentionFinder(study.brands, study.category_terms)
    verify = any("right" in lab for lab in saved["labels"].values())
    result = (score_verified(answers, saved["labels"], finder, [b.name for b in study.brands]) if verify
              else score(answers, saved["labels"], finder))
    recall = result.get("recall", result.get("recall_upper_bound"))
    recall_note = (" (upper bound: only misses the checker noticed are counted)" if verify else "")

    lines = [
        "# Analyzer accuracy against human labels",
        "",
        f"- Answers checked by a human: **{result['answers']}** (run {saved['run_id']}, stratified across "
        "3 engines × 3 languages, fixed seed). "
        + ("Method: the human marked each brand the analyzer found as right or wrong and wrote down any "
           "product or brand name they saw that was not highlighted." if verify
           else "Method: labelled blind to the analyzer's output."),
        f"- Brand mentions counted per (answer, brand) pair: {result['true_positives']} correct, "
        f"{result['false_positives']} false alarms, {result['false_negatives']} misses",
        f"- **Precision {result['precision']:.1%}** (of the brands the analyzer found, how many were really named)",
        f"- **Recall {recall:.1%}**{recall_note} (of the brands really named, how many the analyzer found)",
        "",
        "## Misses",
        *([f"- `{m['answer']}`: {m['brand']}" for m in result["misses"]] or ["- none"]),
        "",
        "## False alarms",
        *([f"- `{f['answer']}`: {f['brand']}" for f in result["false_alarms"]] or ["- none"]),
        "",
        "## Names the checker wrote in that are not in the study's brand list",
        "Product names (e.g. Biozyme) belong to a listed brand and mean the alias list needs extending.",
        *([f"- {b}" for b in result["other_brands_written_in"]] or ["- none"]),
    ]
    audit_path = STUDY_DIR / "coverage_audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        missed = audit["unlisted_brand_mentions_before_fix"]
        total_missed = sum(missed.values())
        lines += [
            "",
            "## Coverage audit (brand list completeness)",
            audit["method"],
            "",
            *[f"- {b}: {n} mentions not counted before the fix" for b, n in missed.items()],
            "",
            f"Recall against every brand actually named, before the fix: "
            f"{result['true_positives']}/{result['true_positives'] + total_missed} = "
            f"**{result['true_positives'] / (result['true_positives'] + total_missed):.1%}**. "
            + audit["fix"],
        ]
    out = PROJECT_ROOT / "docs" / "eval.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwrote {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
