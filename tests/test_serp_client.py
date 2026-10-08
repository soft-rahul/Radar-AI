import json

import httpx
import pytest

from shelfradar.serp_client import (
    BudgetExceeded,
    CassetteMissing,
    SerpClient,
    SerpClientError,
    fingerprint,
    scrub,
)

KEY = "a" * 64
PARAMS = {"q": "best whey protein", "gl": "in", "hl": "en"}


class FakeSerpApi:
    """Stand-in for serpapi.com that counts calls and can fail on demand."""

    def __init__(self, statuses: list[int] | None = None, body: dict | None = None):
        self.statuses = list(statuses or [])
        self.body = body or {"ai_overview": {"text_blocks": [{"snippet": "MuscleBlaze"}]}}
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        return httpx.Response(status, json=self.body if status == 200 else {"error": "busy"})


def make(tmp_path, mode="record", fake=None, max_searches=10):
    fake = fake or FakeSerpApi()
    client = SerpClient(api_key=KEY, mode=mode, max_searches=max_searches,
                        cassette_dir=tmp_path, transport=httpx.MockTransport(fake), retry_delay=0)
    return client, fake


def test_fingerprint_ignores_key_order_and_no_cache():
    a = fingerprint("google", {"q": "x", "hl": "en", "api_key": "k1"})
    b = fingerprint("google", {"hl": "en", "q": "x", "api_key": "k2", "no_cache": "true"})
    assert a == b


def test_fingerprint_separates_samples_and_engines():
    base = fingerprint("google", PARAMS, 0)
    assert base != fingerprint("google", PARAMS, 1)
    assert base != fingerprint("google_ai_mode", PARAMS, 0)


def test_scrub_ignores_short_secrets_so_json_stays_intact():
    assert scrub({"link": "x"}, "k") == {"link": "x"}


def test_scrub_removes_key_and_any_64_hex():
    other = "f" * 64
    out = scrub({"url": f"https://x?api_key={KEY}", "nested": [other]}, KEY)
    assert KEY not in json.dumps(out) and other not in json.dumps(out)


async def test_record_saves_scrubbed_cassette_then_replays_without_network(tmp_path):
    async with make(tmp_path, fake=FakeSerpApi(body={"echo": KEY}))[0] as client:
        await client.search("google", PARAMS)
    saved = next(tmp_path.rglob("*.json")).read_text()
    assert KEY not in saved and '"engine": "google"' in saved

    replay, fake = make(tmp_path, mode="replay")
    async with replay:
        body = await replay.search("google", PARAMS)
    assert body == {"echo": "REDACTED"}
    assert fake.calls == [] and replay.ledger.replayed == 1 and replay.ledger.live_calls == 0


async def test_replay_without_cassette_names_the_missing_file(tmp_path):
    client = SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=tmp_path)
    async with client:
        with pytest.raises(CassetteMissing, match="SERPAPI_MODE=record"):
            await client.search("google", PARAMS)


async def test_budget_stops_before_calling(tmp_path):
    client, fake = make(tmp_path, mode="live", max_searches=1)
    async with client:
        await client.search("google", PARAMS)
        with pytest.raises(BudgetExceeded):
            await client.search("google", {**PARAMS, "q": "other"})
    assert len(fake.calls) == 1


async def test_retries_once_on_429_then_succeeds(tmp_path):
    client, fake = make(tmp_path, mode="live", fake=FakeSerpApi(statuses=[429, 200]))
    async with client:
        body = await client.search("google", PARAMS)
    assert "ai_overview" in body
    assert len(fake.calls) == 2 and client.ledger.retries == 1 and client.ledger.live_calls == 2


async def test_gives_up_after_second_failure(tmp_path):
    client, _ = make(tmp_path, mode="live", fake=FakeSerpApi(statuses=[503, 503]))
    async with client:
        with pytest.raises(SerpClientError, match="HTTP 503"):
            await client.search("google", PARAMS)


async def test_live_mode_never_writes_cassettes(tmp_path):
    client, _ = make(tmp_path, mode="live")
    async with client:
        await client.search("google", PARAMS)
    assert list(tmp_path.rglob("*.json")) == []


async def test_key_is_sent_to_serpapi_but_never_returned(tmp_path):
    client, fake = make(tmp_path, mode="live", fake=FakeSerpApi(body={"leak": KEY}))
    async with client:
        body = await client.search("google", PARAMS)
    assert fake.calls[0].url.params["api_key"] == KEY
    assert KEY not in json.dumps(body)


def test_non_replay_modes_require_a_key(tmp_path):
    with pytest.raises(SerpClientError, match="SERPAPI_KEY"):
        SerpClient(api_key=None, mode="record", max_searches=1, cassette_dir=tmp_path)


# --- Real probe cassettes (recorded 6 Oct 2026) ---------------------------------------

from shelfradar.serp_client import DEFAULT_CASSETTES

MUMBAI_EN = {"gl": "in", "q": "best whey protein for beginners in India",
             "location": "Mumbai, Maharashtra, India", "hl": "en"}


async def test_real_probe_cassettes_replay_offline():
    client = SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=DEFAULT_CASSETTES)
    async with client:
        google = await client.search("google", {**MUMBAI_EN, "no_cache": "true"})
        run1 = await client.search("google_ai_mode", MUMBAI_EN, sample=0)
        run2 = await client.search("google_ai_mode", MUMBAI_EN, sample=1)
    assert google["ai_overview"]["text_blocks"]
    assert run1["reconstructed_markdown"] != run2["reconstructed_markdown"]
    assert client.ledger.live_calls == 0 and client.ledger.replayed == 3


def test_no_saved_fixture_contains_a_key_shaped_string():
    import re
    from shelfradar.config import PROJECT_ROOT

    hex64 = re.compile(r"\b[a-f0-9]{64}\b")
    leaks = [p for p in (PROJECT_ROOT / "fixtures").rglob("*.json") if hex64.search(p.read_text())]
    assert leaks == []
