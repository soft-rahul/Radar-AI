import json

import pytest
from mcp import Client

from mcp.server.mcpserver.exceptions import ToolError

from shelfradar import mcp_server
from shelfradar.mcp_server import brand_visibility_impl, citation_gap_impl, list_brands_impl, server


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Each test starts from an empty database, as on a fresh clone: the server must rebuild it
    from the saved tapes with zero searches."""
    monkeypatch.setenv("SHELFRADAR_DB", str(tmp_path / "mcp.sqlite3"))
    mcp_server.context.cache_clear()
    yield
    mcp_server.context.cache_clear()


def test_rebuilds_from_tapes_and_lists_brands():
    out = list_brands_impl()
    assert out["answers"] == 90
    assert out["brands"][0]["brand"] == "MuscleBlaze" and out["brands"][0]["tier"] == 1
    assert mcp_server.context().store.get_run(out["run_id"])["live_calls"] == 0


def test_brand_visibility_accepts_aliases_and_reports_ranges():
    out = brand_visibility_impl("as it is")
    assert out["brand"] == "AS-IT-IS"
    assert out["by_engine"]["Bing Copilot"]["named"] == 14 and out["by_engine"]["Bing Copilot"]["answered"] == 30
    assert "95% range" in out["summary"] and out["overall"]["low"] < out["overall"]["rate"] < out["overall"]["high"]


def test_unknown_brand_lists_the_tracked_ones():
    with pytest.raises(ToolError, match="Tracked brands: MuscleBlaze"):
        brand_visibility_impl("Pepsi")


def test_citation_gap_is_capped():
    out = citation_gap_impl("MuscleBlaze", limit=3)
    assert len(out["sites"]) == 3 and out["sites"][0]["domain"] == "mensxp.com"


async def test_ask_live_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SHELFRADAR_ALLOW_LIVE", raising=False)
    out = await mcp_server.ask_live_impl("q1")
    assert out["ran"] is False and "4 SerpApi searches" in out["reason"]


async def test_tools_over_the_mcp_protocol():
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"list_brands", "brand_visibility", "citation_gap", "ask_live"}
        assert tools["brand_visibility"].annotations.read_only_hint is True
        result = await client.call_tool("brand_visibility", {"brand": "Nakpro"})
        payload = result.structured_content or json.loads(result.content[0].text)
        assert payload["brand"] == "Nakpro" and "Bing Copilot 23%" in payload["summary"]


async def test_first_call_from_inside_an_event_loop_can_rebuild_the_database(monkeypatch):
    monkeypatch.setenv("SHELFRADAR_ALLOW_LIVE", "1")
    monkeypatch.setattr(mcp_server, "load_settings", lambda: type("S", (), {"serpapi_key": None})())
    ctx = mcp_server.context()          # called inside a running loop, empty database
    assert ctx.store.get_run(ctx.run_id)["done_cells"] == 90


async def test_unknown_brand_message_reaches_the_assistant():
    async with Client(server) as client:
        result = await client.call_tool("brand_visibility", {"brand": "Pepsi"})
    assert result.is_error and "Tracked brands: MuscleBlaze" in result.content[0].text
