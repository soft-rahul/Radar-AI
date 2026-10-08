"""Deterministic reading of AI answers: mentions, order, citations, visibility with Wilson ranges, gaps.

No LLM here. Every number can be traced back to a line of an answer.
"""

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from shelfradar.models import AIAnswer, Brand, Engine, Status, Study

_DEVANAGARI = r"ऀ-ॿ"
_WORD = rf"A-Za-z0-9{_DEVANAGARI}"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")


# --- Mentions ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Mention:
    brand: str
    offset: int        # in the NFKC-normalised text
    alias: str
    length: int = 0    # characters matched at offset


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    # Whole word only; hyphen and space variants are equivalent ("AS-IT-IS" = "AS IT IS").
    body = r"[\s\-]+".join(re.escape(part) for part in re.split(r"[\s\-]+", _norm(alias).strip()))
    # ALL-CAPS aliases ("AS IT IS", "GNC") must keep their case, or "as it is" in prose would match.
    caps = alias.upper() == alias and any(ch.isalpha() and ch.isascii() for ch in alias)
    return re.compile(rf"(?<![{_WORD}]){body}(?![{_WORD}])", 0 if caps else re.IGNORECASE)


class MentionFinder:
    def __init__(self, brands: list[Brand], category_terms: list[str]):
        self._plain: list[tuple[str, str, re.Pattern[str]]] = []
        self._ambiguous: list[tuple[str, str, re.Pattern[str]]] = []
        for b in brands:
            for alias in [b.name, *b.aliases]:
                self._plain.append((b.name, alias, _alias_pattern(alias)))
            for alias in b.ambiguous_aliases:
                self._ambiguous.append((b.name, alias, _alias_pattern(alias)))
        self._category = re.compile("|".join(re.escape(t) for t in category_terms), re.IGNORECASE)

    @staticmethod
    def normalise(text: str) -> str:
        return _norm(text)

    def find(self, text: str) -> list[Mention]:
        """First mention of each brand, in order of appearance."""
        text = _norm(text)
        first: dict[str, Mention] = {}

        def keep(brand: str, alias: str, offset: int, length: int) -> None:
            if brand not in first or offset < first[brand].offset:
                first[brand] = Mention(brand, offset, alias, length)

        for brand, alias, pattern in self._plain:
            if m := pattern.search(text):
                keep(brand, alias, m.start(), m.end() - m.start())

        if self._ambiguous:
            start = 0
            for sentence in _SENTENCE_SPLIT.split(text):
                at = text.find(sentence, start)
                start = max(at, start)
                if not self._category.search(sentence):
                    continue
                for brand, alias, pattern in self._ambiguous:
                    if m := pattern.search(sentence):
                        keep(brand, alias, at + m.start(), m.end() - m.start())
        return sorted(first.values(), key=lambda m: m.offset)


# --- Citations ---------------------------------------------------------------------------

class DomainClassifier:
    def __init__(self, brands: list[Brand], classes: dict):
        self._brand_of = {d: b.name for b in brands for d in b.domains}
        self._class_of = {d: cls for cls in ("marketplace", "ugc", "media", "search_engine")
                          for d in classes.get(cls, [])}

    @classmethod
    def load(cls, brands: list[Brand], path: Path) -> "DomainClassifier":
        return cls(brands, json.loads(path.read_text(encoding="utf-8")))

    def brand_of(self, domain: str) -> str | None:
        return self._brand_of.get(domain)

    def classify(self, domain: str) -> str:
        if domain in self._brand_of:
            return "brand"
        return self._class_of.get(domain, "other")


# --- One answer --------------------------------------------------------------------------

@dataclass
class AnswerAnalysis:
    answer: AIAnswer
    mentioned: list[str] = field(default_factory=list)      # in order of first appearance
    cited_brands: set[str] = field(default_factory=set)     # brands whose own site is a source
    source_classes: Counter = field(default_factory=Counter)
    translated_sources: int = 0

    @property
    def answered(self) -> bool:
        return self.answer.status is Status.OK


def analyze(answer: AIAnswer, finder: MentionFinder, domains: DomainClassifier) -> AnswerAnalysis:
    result = AnswerAnalysis(answer=answer)
    if answer.status is not Status.OK:
        return result
    result.mentioned = [m.brand for m in finder.find(answer.text)]
    for src in answer.sources:
        result.source_classes[domains.classify(src.domain)] += 1
        if brand := domains.brand_of(src.domain):
            result.cited_brands.add(brand)
        result.translated_sources += src.translated
    return result


# --- Statistics --------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials. Honest even for tiny n."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


@dataclass
class Visibility:
    brand: str
    group: str
    named: int      # answers that named the brand
    answered: int   # answers where an AI block existed (the denominator)
    no_ai_block: int
    low: float = 0.0
    high: float = 1.0
    tier: int = 0

    @property
    def rate(self) -> float:
        return self.named / self.answered if self.answered else 0.0


def visibility(analyses: Iterable[AnswerAnalysis], brands: list[str],
               group_by: Callable[[AIAnswer], str] = lambda a: "all") -> list[Visibility]:
    named: dict[tuple[str, str], int] = Counter()
    answered: dict[str, int] = Counter()
    missing: dict[str, int] = Counter()
    for a in analyses:
        group = group_by(a.answer)
        if not a.answered:
            missing[group] += a.answer.status is Status.NO_AI_BLOCK
            continue
        answered[group] += 1
        for brand in a.mentioned:
            named[(brand, group)] += 1

    rows = []
    for group in sorted(set(answered) | set(missing)):
        for brand in brands:
            row = Visibility(brand, group, named[(brand, group)], answered[group], missing[group])
            row.low, row.high = wilson(row.named, row.answered)
            rows.append(row)
    return rows


def assign_tiers(rows: list[Visibility]) -> list[Visibility]:
    """Rank within a group, but brands whose ranges overlap the tier leader share its tier."""
    by_group: dict[str, list[Visibility]] = defaultdict(list)
    for r in rows:
        by_group[r.group].append(r)
    for group_rows in by_group.values():
        group_rows.sort(key=lambda r: (-r.rate, r.brand))
        tier, leader = 0, None
        for r in group_rows:
            if leader is None or r.high < leader.low:
                tier, leader = tier + 1, r
            r.tier = tier
    return rows


# --- Gaps --------------------------------------------------------------------------------

def citation_gap(analyses: Iterable[AnswerAnalysis], focus: str,
                 domains: DomainClassifier) -> list[tuple[str, str, int]]:
    """Sites the AI cites when it names a rival but not `focus`: the outreach list."""
    counts: Counter = Counter()
    for a in analyses:
        if not a.answered or focus in a.mentioned or not a.mentioned:
            continue
        for src in {s.domain for s in a.answer.sources}:
            if domains.classify(src) != "search_engine" and domains.brand_of(src) != focus:
                counts[src] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))   # ties by name, so reports are stable
    return [(d, domains.classify(d), n) for d, n in ranked]


def organic_vs_ai_gap(analyses: Iterable[AnswerAnalysis], focus: Brand) -> list[dict]:
    """Questions where the brand's own site ranks in Google's top 10 but the AI Overview skips it."""
    gaps = []
    for a in analyses:
        ans = a.answer
        if ans.engine is not Engine.AI_OVERVIEW or not a.answered or focus.name in a.mentioned:
            continue
        ranks = [o.position for o in ans.organic if o.domain in focus.domains and o.position <= 10]
        if ranks:
            gaps.append({"question_id": ans.question_id, "variant": ans.variant,
                         "query": ans.query, "organic_rank": min(ranks)})
    return gaps


def _bare(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc.removeprefix('www.')}{parts.path.rstrip('/')}".lower()


def outside_top10_share(analyses: Iterable[AnswerAnalysis]) -> dict[str, float | int]:
    """Share of AI Overview sources that are not among the same page's top-10 organic results."""
    total = outside_url = outside_domain = 0
    for a in analyses:
        ans = a.answer
        if ans.engine is not Engine.AI_OVERVIEW or not a.answered or not ans.organic:
            continue
        top = [o for o in ans.organic if o.position <= 10]
        top_urls, top_domains = {_bare(o.url) for o in top}, {o.domain for o in top}
        for src in ans.sources:
            total += 1
            outside_url += _bare(src.url) not in top_urls
            outside_domain += src.domain not in top_domains
    return {
        "sources": total,
        "outside_by_url": outside_url / total if total else 0.0,
        "outside_by_domain": outside_domain / total if total else 0.0,
    }
