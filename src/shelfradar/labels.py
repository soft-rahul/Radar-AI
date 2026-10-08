"""Human ground truth: pick answers to label (stratified, fixed seed) and score the analyzer against them."""

import json
import random
from collections import defaultdict
from pathlib import Path

from shelfradar.analyzer import MentionFinder
from shelfradar.models import AIAnswer, Status

LABELS_PATH = Path(__file__).resolve().parents[2] / "study" / "labels.json"


def answer_key(a: AIAnswer) -> str:
    return f"{a.question_id}|{a.engine.value}|{a.variant}|{a.sample}"


def sample_for_labelling(answers: list[AIAnswer], n: int = 30, seed: int = 7) -> list[AIAnswer]:
    """Round-robin across engine × language groups so every combination is represented."""
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[AIAnswer]] = defaultdict(list)
    for a in answers:
        if a.status is Status.OK:
            groups[(a.engine.value, a.variant)].append(a)
    for items in groups.values():
        rng.shuffle(items)
    order = sorted(groups)
    picked: list[AIAnswer] = []
    while len(picked) < n and any(groups.values()):
        for g in order:
            if groups[g] and len(picked) < n:
                picked.append(groups[g].pop())
    return picked


def score(answers: list[AIAnswer], labels: dict[str, dict], finder: MentionFinder) -> dict:
    """Precision/recall of brand mentions, counted per (answer, brand) pair."""
    by_key = {answer_key(a): a for a in answers}
    tp = fp = fn = 0
    misses, false_alarms = [], []
    for key, label in labels.items():
        if key not in by_key:
            continue
        truth = set(label.get("brands", []))
        found = {m.brand for m in finder.find(by_key[key].text)}
        tp += len(truth & found)
        for b in sorted(found - truth):
            fp += 1
            false_alarms.append({"answer": key, "brand": b})
        for b in sorted(truth - found):
            fn += 1
            misses.append({"answer": key, "brand": b})
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"answers": len([k for k in labels if k in by_key]), "true_positives": tp,
            "false_positives": fp, "false_negatives": fn, "precision": round(precision, 4),
            "recall": round(recall, 4), "misses": misses, "false_alarms": false_alarms,
            "other_brands_written_in": sorted({o.strip() for lab in labels.values()
                                               for o in lab.get("other", "").split(",") if o.strip()})}


def load_labels(path: Path = LABELS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def score_verified(answers: list[AIAnswer], labels: dict[str, dict], finder: MentionFinder,
                   brand_names: list[str]) -> dict:
    """Score from the 'verify' labelling mode: the human marks each highlighted find right or wrong
    and types any brand they noticed that was not highlighted.
    Precision is fully measured. Recall only counts misses the labeller noticed, so it is an upper bound."""
    by_key = {answer_key(a): a for a in answers}
    known = {n.casefold(): n for n in brand_names}
    right = wrong = noticed_misses = 0
    false_alarms, misses, outside_list = [], [], set()
    for key, label in labels.items():
        if key not in by_key:
            continue
        right += len(label.get("right", []))
        for b in label.get("wrong", []):
            wrong += 1
            false_alarms.append({"answer": key, "brand": b})
        found = {m.brand for m in finder.find(by_key[key].text)}
        for name in (o.strip() for o in label.get("other", "").split(",")):
            if not name:
                continue
            brand = known.get(name.casefold())
            if brand and brand not in found:
                noticed_misses += 1
                misses.append({"answer": key, "brand": brand})
            elif not brand:
                outside_list.add(name)
    precision = right / (right + wrong) if right + wrong else 1.0
    recall = right / (right + noticed_misses) if right + noticed_misses else 1.0
    return {"mode": "verify", "answers": len([k for k in labels if k in by_key]),
            "true_positives": right, "false_positives": wrong, "false_negatives": noticed_misses,
            "precision": round(precision, 4), "recall_upper_bound": round(recall, 4),
            "misses": misses, "false_alarms": false_alarms,
            "other_brands_written_in": sorted(outside_list)}
