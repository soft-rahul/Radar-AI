"""Build the study report from stored answers (always recomputed, never cached)."""

import csv
import io
from collections import Counter, defaultdict

from shelfradar.analyzer import (
    AnswerAnalysis,
    DomainClassifier,
    MentionFinder,
    analyze,
    assign_tiers,
    citation_gap,
    organic_vs_ai_gap,
    outside_top10_share,
    visibility,
    wilson,
)
from shelfradar.factcheck import (
    AnswerReading,
    check_fact_sheet,
    cross_engine_conflicts,
    stance_summary,
)
from shelfradar.models import AIAnswer, Status, Study


def _rows(rows) -> list[dict]:
    return [{"brand": r.brand, "group": r.group, "named": r.named, "answered": r.answered,
             "no_ai_block": r.no_ai_block, "rate": round(r.rate, 4), "low": round(r.low, 4),
             "high": round(r.high, 4), "tier": r.tier} for r in rows]


def analyze_all(answers: list[AIAnswer], study: Study,
                domains: DomainClassifier) -> list[AnswerAnalysis]:
    finder = MentionFinder(study.brands, study.category_terms)
    return [analyze(a, finder, domains) for a in answers]


def _share(k: int, n: int) -> dict:
    low, high = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else 0.0, "low": round(low, 4),
            "high": round(high, 4)}


def factcheck_section(stored: dict | None, fact_sheet: dict, focus: str) -> dict:
    if not stored:
        return {"available": False}
    readings = {k: AnswerReading.model_validate(v) for k, v in stored["readings"].items()}
    focus_stances = [s.stance for r in readings.values() for s in r.stances if s.brand == focus]
    return {
        "focus_recommended": _share(focus_stances.count("recommended"), len(focus_stances)),
        "available": True,
        "model": stored.get("model"),
        "models_note": ", ".join(f"{n} answers by {m}" for m, n in
                                 sorted(Counter(stored.get("model_of_answer", {}).values()).items())),
        "answers_read": len(readings),
        "failed_batches": stored.get("failed_batches", 0),
        "dropped_by_verification": len(stored.get("dropped", [])),
        "dropped_reasons": dict(Counter(d["reason"] for d in stored.get("dropped", [])).most_common()),
        "stance": stance_summary(readings),
        "conflicts": cross_engine_conflicts(readings),
        "fact_sheet_confirmed": bool(fact_sheet.get("confirmed_by_user")),
        "fact_sheet_mismatches": check_fact_sheet(readings, fact_sheet),
    }


def build_report(answers: list[AIAnswer], study: Study, domains: DomainClassifier,
                 focus: str, factcheck: dict | None = None, fact_sheet: dict | None = None) -> dict:
    analyses = analyze_all(answers, study, domains)
    brands = [b.name for b in study.brands]
    focus_brand = next(b for b in study.brands if b.name == focus)

    def seen_rows(rows):
        ranked = sorted(assign_tiers(rows), key=lambda r: (r.group, r.tier, -r.rate, r.brand))
        return [r for r in _rows(ranked) if r["named"] or r["brand"] == focus]

    sources = Counter()
    translated = total_sources = 0
    for a in analyses:
        sources.update(a.source_classes)
        translated += a.translated_sources
        total_sources += len(a.answer.sources) if a.answered else 0

    status = Counter(a.answer.status.value for a in analyses)
    answered = [a for a in analyses if a.answered]
    return {
        "focus": focus,
        "answers": len(analyses),
        "status": dict(status),
        "overall": seen_rows(visibility(analyses, brands)),
        "by_engine": seen_rows(visibility(analyses, brands, lambda a: a.engine.value)),
        "by_variant": seen_rows(visibility(analyses, brands, lambda a: a.variant)),
        "by_engine_variant": seen_rows(
            visibility(analyses, brands, lambda a: f"{a.engine.value}|{a.variant}")),
        "first_mention": dict(Counter(a.mentioned[0] for a in analyses if a.mentioned)),
        "source_classes": dict(sources),
        "translated_share": round(translated / total_sources, 4) if total_sources else 0.0,
        "outside_top10": outside_top10_share(analyses),
        "citation_gap": [{"domain": d, "class": c, "answers": n}
                         for d, c, n in citation_gap(analyses, focus, domains)[:15]],
        "organic_vs_ai_gap": organic_vs_ai_gap(analyses, focus_brand),
        "focus_kpis": {
            "named": _share(sum(focus in a.mentioned for a in answered), len(answered)),
            "named_first": _share(sum(a.mentioned[:1] == [focus] for a in answered), len(answered)),
            "own_site_cited": _share(sum(focus in a.cited_brands for a in answered), len(answered)),
        },
        "dice": dice(analyses),
        "factcheck": factcheck_section(factcheck, fact_sheet or {}, focus),
        "errors": [{"question_id": a.answer.question_id, "engine": a.answer.engine.value,
                    "variant": a.answer.variant, "sample": a.answer.sample, "error": a.answer.error}
                   for a in analyses if a.answer.status is Status.ERROR],
    }


def dice(analyses: list[AnswerAnalysis]) -> list[dict]:
    """Same question, engine and language asked several times: did the brand list change?"""
    groups: dict[tuple, list[AnswerAnalysis]] = defaultdict(list)
    for a in analyses:
        if a.answered:
            groups[(a.answer.question_id, a.answer.engine.value, a.answer.variant)].append(a)
    out = []
    for (qid, engine, variant), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        items.sort(key=lambda a: a.answer.sample)
        lists = [a.mentioned for a in items]
        out.append({"question_id": qid, "engine": engine, "variant": variant,
                    "query": items[0].answer.query, "samples": lists,
                    "identical": all(lst == lists[0] for lst in lists)})
    return out


def answers_csv(answers: list[AIAnswer], study: Study, domains: DomainClassifier) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["question_id", "engine", "variant", "sample", "status", "query", "mentioned_in_order",
                "brands_with_own_site_cited", "sources", "translated_sources", "error"])
    for a in analyze_all(answers, study, domains):
        ans = a.answer
        w.writerow([ans.question_id, ans.engine.value, ans.variant, ans.sample, ans.status.value,
                    ans.query, ";".join(a.mentioned), ";".join(sorted(a.cited_brands)),
                    len(ans.sources), a.translated_sources, ans.error or ""])
    return buf.getvalue()
