import json

import pytest

from shelfradar.analyzer import (
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
from shelfradar.config import PROJECT_ROOT
from shelfradar.engines import fetch_answer
from shelfradar.models import AIAnswer, Engine, OrganicResult, Source, Status, Study, Variant
from shelfradar.serp_client import DEFAULT_CASSETTES, SerpClient

STUDY = Study.model_validate(json.loads((PROJECT_ROOT / "study/whey.json").read_text()))
FINDER = MentionFinder(STUDY.brands, STUDY.category_terms)
DOMAINS = DomainClassifier.load(STUDY.brands, PROJECT_ROOT / "study/domain_classes.json")
BRANDS = [b.name for b in STUDY.brands]


def names(text: str) -> list[str]:
    return [m.brand for m in FINDER.find(text)]


# --- Mentions ----------------------------------------------------------------------------

def test_order_of_first_appearance():
    assert names("Try Nakpro, then MuscleBlaze, then Nakpro again.") == ["Nakpro", "MuscleBlaze"]


@pytest.mark.parametrize("text,expected", [
    ("मसलब्लेज़ बायो-जोन व्हे अच्छा है", ["MuscleBlaze"]),
    ("Muscle Blaze Raw Whey", ["MuscleBlaze"]),
    ("AS-IT-IS and AS IT IS are the same", ["AS-IT-IS"]),
    ("Asitis ATOM Beginners Whey | 25g", ["AS-IT-IS"]),
    ("ATOM whey protein for beginners", ["AS-IT-IS"]),
    ("GNC Pro Performance", ["GNC"]),
])
def test_aliases_scripts_and_spellings(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text", [
    "Read the anatomy of a muscle.",                       # 'Atom' inside a word
    "Take it as it is, nothing added.",                     # 'AS IT IS' in prose (case-sensitive)
    "Each atom of the body matters. Drink water.",          # ambiguous alias without a category term
    "Build big muscles with my protein routine.",           # common phrases, not brands
    "gnc is lowercase here",                               # ALL-CAPS brand written lowercase
])
def test_false_positives_are_rejected(text):
    assert names(text) == []


# --- Wilson and tiers --------------------------------------------------------------------

@pytest.mark.parametrize("k,n,low,high", [
    (2, 3, 0.208, 0.939),     # hand-calculated in the Phase 5 plan
    (30, 45, 0.521, 0.787),
    (0, 10, 0.0, 0.278),
    (10, 10, 0.722, 1.0),
])
def test_wilson_matches_hand_calculation(k, n, low, high):
    lo, hi = wilson(k, n)
    assert lo == pytest.approx(low, abs=0.002) and hi == pytest.approx(high, abs=0.002)


def test_wilson_with_no_data_is_completely_uncertain():
    assert wilson(0, 0) == (0.0, 1.0)


def _answer(status=Status.OK, text="", engine=Engine.AI_MODE, sources=(), organic=(), variant="mumbai-en"):
    return AIAnswer(engine=engine, variant=variant, query="q", question_id="q1", status=status,
                    text=text, sources=list(sources), organic=list(organic))


def test_no_ai_block_is_excluded_from_the_denominator():
    answers = [_answer(text="MuscleBlaze is good"), _answer(text="Nakpro only"),
               _answer(status=Status.NO_AI_BLOCK), _answer(status=Status.ERROR)]
    rows = visibility([analyze(a, FINDER, DOMAINS) for a in answers], ["MuscleBlaze"])
    (row,) = rows
    assert (row.named, row.answered, row.no_ai_block) == (1, 2, 1)
    assert row.rate == 0.5


def test_overlapping_ranges_share_a_tier():
    answers = ([_answer(text="MuscleBlaze, Nakpro")] * 9 + [_answer(text="MuscleBlaze")] * 1
               + [_answer(text="Avvatar")] * 10)
    rows = assign_tiers(visibility([analyze(a, FINDER, DOMAINS) for a in answers],
                                   ["MuscleBlaze", "Nakpro", "Wellcore"]))
    tier = {r.brand: r.tier for r in rows}
    assert tier["MuscleBlaze"] == tier["Nakpro"] == 1   # 10/20 vs 9/20: ranges overlap → tie
    assert tier["Wellcore"] == 2                         # 0/20: clearly below


# --- Citations and gaps ------------------------------------------------------------------

def test_domain_classes():
    assert DOMAINS.classify("muscleblaze.com") == "brand"
    assert DOMAINS.brand_of("guardian.in") == "GNC"
    assert DOMAINS.classify("healthkart.com") == "marketplace"
    assert DOMAINS.classify("reddit.com") == "ugc"
    assert DOMAINS.classify("ndtv.in") == "media"
    assert DOMAINS.classify("whey2much.in") == "other"


def _src(domain, translated=False):
    return Source(url=f"https://{domain}/p", domain=domain, translated=translated)


def test_citation_gap_lists_sites_used_when_rivals_win():
    answers = [
        _answer(text="Nakpro is best", sources=[_src("valuelens.in"), _src("google.com")]),
        _answer(text="Avvatar and Nakpro", sources=[_src("valuelens.in"), _src("reddit.com")]),
        _answer(text="MuscleBlaze wins", sources=[_src("healthkart.com")]),   # focus named: ignored
    ]
    gap = citation_gap([analyze(a, FINDER, DOMAINS) for a in answers], "MuscleBlaze", DOMAINS)
    assert gap[0] == ("valuelens.in", "other", 2)
    assert ("reddit.com", "ugc", 1) in gap
    assert all(d != "google.com" and d != "healthkart.com" for d, _, _ in gap)


def test_citation_gap_breaks_ties_by_name():
    answers = [_answer(text="Nakpro is best", sources=[_src("zeta.in"), _src("alpha.in"), _src("mid.in")])]
    gap = citation_gap([analyze(a, FINDER, DOMAINS) for a in answers], "MuscleBlaze", DOMAINS)
    assert [d for d, _, _ in gap] == ["alpha.in", "mid.in", "zeta.in"]


def test_organic_vs_ai_gap():
    focus = next(b for b in STUDY.brands if b.name == "MuscleBlaze")
    skipped = _answer(engine=Engine.AI_OVERVIEW, text="Nakpro is great",
                      organic=[OrganicResult(position=2, url="https://www.muscleblaze.com/x",
                                             domain="muscleblaze.com")])
    named = skipped.model_copy(update={"text": "MuscleBlaze is great"})
    gaps = organic_vs_ai_gap([analyze(a, FINDER, DOMAINS) for a in (skipped, named)], focus)
    assert gaps == [{"question_id": "q1", "variant": "mumbai-en", "query": "q", "organic_rank": 2}]


def test_outside_top10_by_url_and_domain():
    ans = _answer(engine=Engine.AI_OVERVIEW, text="x",
                  organic=[OrganicResult(position=1, url="https://www.a.com/one", domain="a.com")],
                  sources=[Source(url="https://a.com/one/", domain="a.com"),
                           Source(url="https://a.com/two", domain="a.com"),
                           Source(url="https://b.com/x", domain="b.com")])
    share = outside_top10_share([analyze(ans, FINDER, DOMAINS)])
    assert share["sources"] == 3
    assert share["outside_by_url"] == pytest.approx(2 / 3)
    assert share["outside_by_domain"] == pytest.approx(1 / 3)


# --- On the real probe answers -----------------------------------------------------------

async def test_real_probe_answers():
    mumbai = Variant(id="mumbai-en", location="Mumbai, Maharashtra, India", hl="en")
    delhi = Variant(id="delhi-hi", location="Delhi, India", hl="hi", lang="hi")
    async with SerpClient(api_key=None, mode="replay", max_searches=0,
                          cassette_dir=DEFAULT_CASSETTES) as client:
        aio_en = await fetch_answer(client, Engine.AI_OVERVIEW, mumbai,
                                    "best whey protein for beginners in India")
        aio_hi = await fetch_answer(client, Engine.AI_OVERVIEW, delhi,
                                    "भारत में शुरुआती लोगों के लिए सबसे अच्छा व्हे प्रोटीन कौन सा है")
        cop_hi = await fetch_answer(client, Engine.COPILOT, delhi,
                                    "भारत में शुरुआती लोगों के लिए सबसे अच्छा व्हे प्रोटीन कौन सा है")
    en, hi, cop = (analyze(a, FINDER, DOMAINS) for a in (aio_en, aio_hi, cop_hi))
    assert en.mentioned[0] == "MuscleBlaze" and "AS-IT-IS" in en.mentioned
    assert "MuscleBlaze" in en.cited_brands
    assert hi.translated_sources == 3
    assert cop.mentioned == ["Gainz4Ever"]
    assert outside_top10_share([en])["sources"] == 23
