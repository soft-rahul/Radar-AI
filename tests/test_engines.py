import httpx
import pytest

from shelfradar.domains import domain_of, unwrap
from shelfradar.engines import blocks_to_text, fetch_answer
from shelfradar.models import Engine, Status, Variant
from shelfradar.serp_client import DEFAULT_CASSETTES, CassetteMissing, SerpClient

MUMBAI_EN = Variant(id="mumbai-en", location="Mumbai, Maharashtra, India", hl="en")
DELHI_HI = Variant(id="delhi-hi", location="Delhi, India", hl="hi")
Q_EN = "best whey protein for beginners in India"
Q_HI = "भारत में शुरुआती लोगों के लिए सबसे अच्छा व्हे प्रोटीन कौन सा है"


def replay() -> SerpClient:
    return SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=DEFAULT_CASSETTES)


def fake_client(responses: dict[str, dict]) -> SerpClient:
    """A record-free live client whose fake SerpApi answers per engine."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.params["engine"]])
    return SerpClient(api_key="k", mode="live", max_searches=10,
                      transport=httpx.MockTransport(handler), retry_delay=0)


# --- Real probe cassettes ----------------------------------------------------------------

@pytest.mark.parametrize("engine", list(Engine))
@pytest.mark.parametrize("variant,query", [(MUMBAI_EN, Q_EN), (DELHI_HI, Q_HI)])
async def test_every_probe_cassette_becomes_a_valid_answer(engine, variant, query):
    async with replay() as client:
        answer = await fetch_answer(client, engine, variant, query)
    assert answer.status is Status.OK, answer.error
    assert len(answer.text) > 500
    assert client.ledger.live_calls == 0


async def test_ai_overview_inline_has_sources_and_organic_ranks():
    async with replay() as client:
        answer = await fetch_answer(client, Engine.AI_OVERVIEW, MUMBAI_EN, Q_EN)
    assert len(answer.sources) == 23 and len(answer.organic) == 9
    assert answer.organic[0].domain == "foodsure.co.in"
    assert "MuscleBlaze" in answer.text


async def test_hindi_ai_overview_uses_page_token_follow_up():
    async with replay() as client:
        answer = await fetch_answer(client, Engine.AI_OVERVIEW, DELHI_HI, Q_HI)
    assert "ai_overview deferred: page_token follow-up" in answer.notes
    assert client.ledger.replayed == 2
    assert answer.organic, "organic ranks come from the first google call"


async def test_hindi_overview_sources_are_unwrapped_from_google_translate():
    async with replay() as client:
        answer = await fetch_answer(client, Engine.AI_OVERVIEW, DELHI_HI, Q_HI)
    assert [s.translated for s in answer.sources] == [True, True, True]
    assert "2xnutrition.com" in {s.domain for s in answer.sources}
    assert all("translate.google" not in s.url for s in answer.sources)


async def test_ai_mode_without_references_is_ok_and_flagged_as_drift():
    async with replay() as client:
        run1 = await fetch_answer(client, Engine.AI_MODE, MUMBAI_EN, Q_EN, sample=0)
        run2 = await fetch_answer(client, Engine.AI_MODE, MUMBAI_EN, Q_EN, sample=1)
    assert run1.status is Status.OK and run1.sources == []
    assert "missing field: references" in run1.notes
    assert len(run2.sources) == 16 and run2.notes == []
    assert run1.text != run2.text


async def test_copilot_table_rows_become_text():
    async with replay() as client:
        answer = await fetch_answer(client, Engine.COPILOT, MUMBAI_EN, Q_EN)
    assert "Nutrabay Gold Vital Whey" in answer.text
    assert "copilot: not localised (no location/hl)" in answer.notes


async def test_unrecorded_sample_raises_cassette_missing():
    async with replay() as client:
        with pytest.raises(CassetteMissing):
            await fetch_answer(client, Engine.COPILOT, MUMBAI_EN, Q_EN, sample=7)


# --- Synthetic edge cases ----------------------------------------------------------------

async def test_no_ai_overview_is_no_ai_block_not_error():
    async with fake_client({"google": {"organic_results": [
            {"position": 1, "link": "https://www.amazon.in/x", "title": "x"}]}}) as client:
        answer = await fetch_answer(client, Engine.AI_OVERVIEW, MUMBAI_EN, Q_EN)
    assert answer.status is Status.NO_AI_BLOCK and answer.organic[0].domain == "amazon.in"


async def test_expired_page_token_is_an_error_with_reason():
    async with fake_client({
        "google": {"ai_overview": {"page_token": "tok"}},
        "google_ai_overview": {"error": "page_token has expired"},
    }) as client:
        answer = await fetch_answer(client, Engine.AI_OVERVIEW, MUMBAI_EN, Q_EN)
    assert answer.status is Status.ERROR and answer.error == "follow-up: page_token has expired"


async def test_serpapi_no_results_message_means_empty_not_failure():
    async with fake_client({"bing_copilot": {
            "error": "Bing Copilot hasn't returned any results for this query."}}) as client:
        answer = await fetch_answer(client, Engine.COPILOT, MUMBAI_EN, Q_EN)
    assert answer.status is Status.NO_AI_BLOCK


async def test_empty_text_blocks_is_no_ai_block():
    async with fake_client({"google_ai_mode": {"text_blocks": []}}) as client:
        answer = await fetch_answer(client, Engine.AI_MODE, MUMBAI_EN, Q_EN)
    assert answer.status is Status.NO_AI_BLOCK


def test_nested_lists_and_tables_flatten_in_order():
    blocks = [
        {"type": "heading", "snippet": "Top picks"},
        {"type": "list", "list": [{"snippet": "A", "list": [{"snippet": "A.1"}]}, {"snippet": "B"}]},
        {"type": "table", "headers": ["Brand", "Protein"], "table": [["X", "24g"]]},
    ]
    assert blocks_to_text(blocks) == "Top picks\nA\nA.1\nB\nBrand | Protein\nX | 24g"


@pytest.mark.parametrize("url,expected", [
    ("https://www.foodsure.co.in/blog?utm=1", "foodsure.co.in"),
    ("https://m.youtube.com/watch?v=1", "youtube.com"),
    ("https://www.google.com/url?q=https://www.nykaa.com/p&sa=U", "nykaa.com"),
    ("https://shop.muscleblaze.com/x", "muscleblaze.com"),
    ("https://translate.google.com/translate?u=https://2xnutrition.com/blogs/a&hl=hi", "2xnutrition.com"),
])
def test_domain_of(url, expected):
    assert domain_of(url) == expected


def test_unwrap_leaves_normal_links_alone():
    assert unwrap("https://example.com/url?q=x") == "https://example.com/url?q=x"
