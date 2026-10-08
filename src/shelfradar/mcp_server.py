"""ShelfRadar as an MCP server: AI assistants ask "how visible is brand X in AI search?".

Read-only tools work from the saved study (0 searches). `ask_live` spends ~4 SerpApi searches
and only runs when SHELFRADAR_ALLOW_LIVE=1 and the user's own SERPAPI_KEY is set.

Run:  uv run shelfradar-mcp            (stdio, for Claude Desktop / Claude Code / Cursor)
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from shelfradar.analyzer import DomainClassifier, MentionFinder
from shelfradar.api import DB_PATH, STUDY_DIR
from shelfradar.config import load_settings
from shelfradar.engines import fetch_answer
from shelfradar.factcheck import load_fact_sheet
from shelfradar.models import Engine, Study
from shelfradar.orchestrator import is_recorded, load_questions, load_study, plan_grid, run_scan
from shelfradar.report import build_report
from shelfradar.serp_client import SerpClient
from shelfradar.store import Store

ENGINE = {"ai_overview": "Google AI Overview", "ai_mode": "Google AI Mode", "copilot": "Bing Copilot"}
VARIANT = {"mumbai-en": "English", "delhi-hi": "Hindi", "delhi-hinglish": "Hinglish"}


@dataclass
class Context:
    study: Study
    domains: DomainClassifier
    store: Store
    run_id: int


async def _replay_into(store: Store, study: Study) -> int:
    """Fresh clone with no database: rebuild the study from the saved tapes (0 searches)."""
    client = SerpClient(api_key=None, mode="replay", max_searches=0)
    cells = [c for c in plan_grid(study, load_questions(STUDY_DIR / "questions.json"), samples=3)
             if is_recorded(c, client)]
    run_id = store.create_run(study="whey", mode="replay", planned_cells=len(cells))
    async with client:
        await run_scan(client, store, run_id, cells)
    return run_id


@lru_cache(maxsize=1)
def context() -> Context:
    study = load_study(STUDY_DIR / "whey.json")
    store = Store(os.getenv("SHELFRADAR_DB", DB_PATH))
    runs = [r for r in store.list_runs() if r["status"] == "complete" and r["mode"] != "live"]
    if runs:
        run_id = max(runs, key=lambda r: (r["done_cells"], r["id"]))["id"]
    else:
        # asyncio.run cannot nest inside a running event loop (async tools), so rebuild in a worker thread.
        with ThreadPoolExecutor(max_workers=1) as pool:
            run_id = pool.submit(asyncio.run, _replay_into(store, study)).result()
    return Context(study, DomainClassifier.load(study.brands, STUDY_DIR / "domain_classes.json"),
                   store, run_id)


def _resolve(ctx: Context, brand: str) -> str:
    wanted = brand.strip().casefold()
    for b in ctx.study.brands:
        if wanted in {n.casefold() for n in [b.name, *b.aliases]}:
            return b.name
    names = ", ".join(b.name for b in ctx.study.brands)
    # ToolError text reaches the assistant, so it can correct itself; other exceptions stay server-side.
    raise ToolError(f"Unknown brand {brand!r}. Tracked brands: {names}")


def _report(ctx: Context, focus: str) -> dict:
    return build_report(ctx.store.answers(ctx.run_id), ctx.study, ctx.domains, focus,
                        factcheck=ctx.store.factcheck(ctx.run_id),
                        fact_sheet=load_fact_sheet(STUDY_DIR / "facts.json"))


def _share(row: dict | None, answered: int) -> dict:
    if row is None:
        return {"rate": 0.0, "named": 0, "answered": answered}
    return {k: row[k] for k in ("rate", "low", "high", "named", "answered")}


def _pct(x: float) -> str:
    return f"{x:.0%}"


# --- Tool implementations (plain functions, so tests can call them directly) --------------

def list_brands_impl() -> dict:
    ctx = context()
    rep = _report(ctx, ctx.study.brands[0].name)
    return {
        "study": ctx.study.category,
        "run_id": ctx.run_id,
        "answers": rep["answers"],
        "brands": [{"brand": r["brand"], "rate": r["rate"], "low": r["low"], "high": r["high"],
                    "tier": r["tier"]} for r in rep["overall"]],
        "note": "rate = share of AI answers naming the brand; low/high = 95% Wilson range; "
                "brands in the same tier are statistically tied.",
    }


def brand_visibility_impl(brand: str) -> dict:
    ctx = context()
    name = _resolve(ctx, brand)
    rep = _report(ctx, name)
    overall = next((r for r in rep["overall"] if r["brand"] == name), None)
    n = rep["focus_kpis"]["named"]["n"]

    def groups(key: str, labels: dict) -> dict:
        rows = {r["group"]: r for r in rep[key] if r["brand"] == name}
        answered = {r["group"]: r["answered"] for r in rep[key]}
        return {labels[g]: _share(rows.get(g), answered.get(g, 0)) for g in labels if g in answered}

    by_engine, by_language = groups("by_engine", ENGINE), groups("by_variant", VARIANT)
    stance = next((s for s in rep["factcheck"].get("stance", []) if s["brand"] == name), None)
    k = rep["focus_kpis"]
    summary = (
        f"{name} is named in {_pct(k['named']['rate'])} of {n} AI answers "
        f"(95% range {_pct(k['named']['low'])}–{_pct(k['named']['high'])}). "
        "By engine: " + ", ".join(f"{e} {_pct(v['rate'])}" for e, v in by_engine.items()) + ". "
        "By language: " + ", ".join(f"{lang} {_pct(v['rate'])}" for lang, v in by_language.items()) + ". "
        f"Named first in {_pct(k['named_first']['rate'])} of answers; its own website is cited in "
        f"{_pct(k['own_site_cited']['rate'])}."
    )
    return {
        "brand": name, "run_id": ctx.run_id, "summary": summary,
        "overall": _share(overall, n), "tier": overall["tier"] if overall else None,
        "by_engine": by_engine, "by_language": by_language,
        "named_first": k["named_first"], "own_site_cited": k["own_site_cited"],
        "stance": stance,
        "how_to_read": "Differences count only when the 95% ranges do not overlap.",
    }


def citation_gap_impl(brand: str, limit: int = 10) -> dict:
    ctx = context()
    name = _resolve(ctx, brand)
    rep = _report(ctx, name)
    return {
        "brand": name, "run_id": ctx.run_id,
        "explanation": f"Websites the AI cited in answers that named a rival but not {name}. "
                       "Being covered on these sites is the most direct route into those answers.",
        "sites": rep["citation_gap"][:max(1, min(limit, 15))],
        "organic_vs_ai_gap": rep["organic_vs_ai_gap"],
    }


async def ask_live_impl(question_id: str = "q1") -> dict:
    settings = load_settings()
    if os.getenv("SHELFRADAR_ALLOW_LIVE") != "1" or not settings.serpapi_key:
        return {"ran": False,
                "reason": "Live search is off. It spends about 4 SerpApi searches from YOUR account. "
                          "To allow it, set SHELFRADAR_ALLOW_LIVE=1 and SERPAPI_KEY in the server's environment."}
    ctx = context()
    questions = {q["id"]: q for q in load_questions(STUDY_DIR / "questions.json")}
    if question_id not in questions:
        raise ToolError(f"Unknown question {question_id!r}. Choose one of: {', '.join(questions)}")
    query = questions[question_id]["en"]["text"]
    variant = next(v for v in ctx.study.variants if v.id == "mumbai-en")
    finder = MentionFinder(ctx.study.brands, ctx.study.category_terms)
    client = SerpClient(api_key=settings.serpapi_key, mode="live", max_searches=6)
    async with client:
        answers = await asyncio.gather(*(fetch_answer(client, e, variant, query) for e in Engine))
    return {
        "ran": True, "question": query, "searches_spent": client.ledger.live_calls,
        "answers": {ENGINE[a.engine.value]: {"status": a.status.value,
                                             "brands_named_in_order": [m.brand for m in finder.find(a.text)],
                                             "sources": len(a.sources)} for a in answers},
        "note": "One fresh answer per engine: a single roll of the dice. The saved study samples each "
                "question repeatedly; use brand_visibility for the measured rates.",
    }


# --- MCP wiring --------------------------------------------------------------------------

server = MCPServer(
    name="shelfradar",
    title="ShelfRadar",
    instructions=(
        "ShelfRadar measures how often Google AI Overview, Google AI Mode and Bing Copilot name "
        "Indian whey protein brands, in English, Hindi and Hinglish, from repeated SerpApi searches. "
        "Always quote the 95% range with a rate, and treat overlapping ranges as ties."),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)


@server.tool(description="List every tracked brand with its share of AI answers, 95% range and tier.",
             annotations=READ_ONLY)
def list_brands() -> dict:
    return list_brands_impl()


@server.tool(description="How visible a brand is in AI search answers: overall, by engine and by "
                         "language, plus how often it is named first and how the answers treat it. "
                         "Accepts brand names or aliases, e.g. 'MuscleBlaze', 'AS IT IS', 'ON Gold Standard'.",
             annotations=READ_ONLY)
def brand_visibility(brand: str) -> dict:
    return brand_visibility_impl(brand)


@server.tool(description="Outreach list: websites the AI cites when it names a rival but not this brand.",
             annotations=READ_ONLY)
def citation_gap(brand: str, limit: int = 10) -> dict:
    return citation_gap_impl(brand, limit)


@server.tool(description="Ask one study question live to all three engines right now (English, Mumbai). "
                         "Spends about 4 SerpApi searches from the user's own key; off unless "
                         "SHELFRADAR_ALLOW_LIVE=1. question_id is q1..q5.",
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=False))
async def ask_live(question_id: str = "q1") -> dict:
    return await ask_live_impl(question_id)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
