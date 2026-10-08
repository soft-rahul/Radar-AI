import pytest

from shelfradar.discovery import (
    Candidate,
    PairProposal,
    QuestionPair,
    dedupe,
    lang_of,
    normalize,
    people_also_ask,
    propose_pairs,
    rank,
    score,
)
from shelfradar.llm import LLMError
from shelfradar.models import Variant
from shelfradar.serp_client import DEFAULT_CASSETTES, SerpClient

BRANDS = ["MuscleBlaze", "Avvatar", "Optimum Nutrition"]


@pytest.mark.parametrize("text,lang,keep", [
    ("Which is no 1 whey protein in India?", "en", True),
    ("best whey protein for beginners", "en", True),
    ("Does Virat Kohli take whey?", "en", False),
    ("sabse achha whey protein kaun sa hai", "en", True),
    ("kis company ka whey protein sabse accha hai", "en", True),
    ("roz kitna protein lena chahiye", "en", False),
    ("whey protein side effects", "en", False),
    ("Avvatar Whey Protein", "en", False),
    ("कौन सी कंपनी का व्हे प्रोटीन सबसे अच्छा है?", "hi", True),
    ("व्हे प्रोटीन कितनी उम्र में लेना चाहिए?", "hi", False),
    ("1 दिन में कितना व्हे प्रोटीन ले सकते हैं?", "hi", False),
])
def test_buying_intent_keeps_choices_and_drops_trivia(text, lang, keep):
    assert (score(text, lang, BRANDS)[0] > 0) is keep


def test_brand_named_questions_are_navigational():
    assert score("Optimum Nutrition vs MuscleBlaze", "en", BRANDS)[1] == "names a brand (navigational)"


def test_normalize_and_dedupe_merge_case_and_punctuation_keeping_paa():
    a = Candidate(text="Best whey protein?", lang="en", origin="autocomplete")
    b = Candidate(text="best  whey protein", lang="en", origin="people_also_ask")
    assert normalize(a.text) == normalize(b.text)
    assert dedupe([a, b]) == [b]


def test_lang_of_detects_devanagari_even_in_mixed_text():
    assert lang_of("best व्हे protein") == "hi"
    assert lang_of("best whey protein") == "en"


async def test_people_also_ask_reads_recorded_google_results_for_free():
    client = SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=DEFAULT_CASSETTES)
    async with client:
        en = await people_also_ask(client, Variant(id="mumbai-en", location="Mumbai, Maharashtra, India",
                                                   hl="en"), "best whey protein for beginners in India")
    ranked = rank(en, BRANDS)
    kept = {c.text for c in ranked if c.score > 0}
    assert "Which is no 1 whey protein in India?" in kept
    assert "Does Virat Kohli take whey?" not in kept
    assert client.ledger.live_calls == 0


class FakeLLM:
    def __init__(self, pairs=None, fail=False):
        self.pairs, self.fail, self.prompt = pairs or [], fail, ""

    def generate_json(self, prompt, schema):
        self.prompt = prompt
        if self.fail:
            raise LLMError("quota")
        return PairProposal(pairs=self.pairs)


def test_propose_pairs_verifies_the_from_search_claim():
    hi = [Candidate(text="नंबर वन व्हे प्रोटीन पाउडर कौन सा है?", lang="hi", origin="people_also_ask",
                    score=2)]
    llm = FakeLLM(pairs=[
        QuestionPair(id="q1", en="Which is no 1 whey protein in India?",
                     hi="नंबर वन व्हे प्रोटीन पाउडर कौन सा है", hi_from_search=True),
        QuestionPair(id="q2", en="Best whey protein under 2000", hi="2000 के अंदर सबसे अच्छा व्हे प्रोटीन",
                     hi_from_search=True),  # the model claims "from search", but it is not
    ])
    pairs = propose_pairs(llm, [], hi, category="whey")
    assert [p.hi_from_search for p in pairs] == [True, False]
    assert "नंबर वन" in llm.prompt


def test_propose_pairs_returns_empty_when_llm_fails():
    assert propose_pairs(FakeLLM(fail=True), [], [], category="whey") == []
