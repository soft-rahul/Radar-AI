"""Adapters: each SerpApi engine's JSON → one AIAnswer. Nothing downstream reads raw JSON."""

from typing import Any

from shelfradar.domains import domain_of, is_translated, unwrap
from shelfradar.models import AIAnswer, Engine, OrganicResult, Source, Status, Variant
from shelfradar.serp_client import BudgetExceeded, CassetteMissing, SerpClient, SerpClientError

# Fields we rely on when an AI answer is present. Missing ones are reported as schema drift.
EXPECTED = {
    Engine.AI_OVERVIEW: ("text_blocks", "references"),
    Engine.AI_MODE: ("text_blocks", "references"),
    Engine.COPILOT: ("text_blocks", "references"),
}


def google_params(variant: Variant, query: str) -> dict[str, str]:
    return {"q": query, "location": variant.location, "gl": "in", "hl": variant.hl,
            "no_cache": "true"}


def copilot_params(_: Variant, query: str) -> dict[str, str]:
    # Bing Copilot is queried without location/language: the variant only changes the question text.
    return {"q": query, "no_cache": "true"}


SERP_ENGINE = {Engine.AI_OVERVIEW: "google", Engine.AI_MODE: "google_ai_mode",
               Engine.COPILOT: "bing_copilot"}


def primary_request(engine: Engine, variant: Variant, query: str) -> tuple[str, dict[str, str]]:
    """The first SerpApi call for a cell (AI Overview may need one follow-up after it)."""
    params = copilot_params(variant, query) if engine is Engine.COPILOT else google_params(variant, query)
    return SERP_ENGINE[engine], params


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []

    def walk_list(items: list[dict[str, Any]]) -> None:
        for item in items:
            if item.get("snippet"):
                lines.append(item["snippet"])
            walk_list(item.get("list") or [])

    for block in blocks:
        kind = block.get("type")
        if kind == "list":
            walk_list(block.get("list") or [])
        elif kind == "table":
            if block.get("headers"):
                lines.append(" | ".join(map(str, block["headers"])))
            lines.extend(" | ".join(map(str, row)) for row in block.get("table") or [])
        elif block.get("snippet"):
            lines.append(block["snippet"])
    return "\n".join(line.strip() for line in lines if str(line).strip())


def parse_sources(refs: list[dict[str, Any]]) -> list[Source]:
    return [
        Source(title=r.get("title", ""), url=unwrap(r["link"]), domain=domain_of(r["link"]),
               publisher=r.get("source", ""), translated=is_translated(r["link"]))
        for r in refs if r.get("link")
    ]


def parse_organic(results: list[dict[str, Any]]) -> list[OrganicResult]:
    return [
        OrganicResult(position=r["position"], url=r["link"], domain=domain_of(r["link"]),
                      title=r.get("title", ""))
        for r in results if r.get("link") and r.get("position")
    ]


def from_ai_block(answer: AIAnswer, block: dict[str, Any], engine: Engine) -> AIAnswer:
    answer.text = blocks_to_text(block.get("text_blocks") or [])
    if not answer.text and block.get("reconstructed_markdown"):
        answer.text = block["reconstructed_markdown"]
    answer.sources = parse_sources(block.get("references") or [])
    answer.status = Status.OK if answer.text else Status.NO_AI_BLOCK
    if answer.status is Status.OK:
        answer.notes += [f"missing field: {f}" for f in EXPECTED[engine] if f not in block]
    return answer


async def fetch_answer(client: SerpClient, engine: Engine, variant: Variant, query: str,
                       sample: int = 0) -> AIAnswer:
    answer = AIAnswer(engine=engine, variant=variant.id, query=query, sample=sample,
                      status=Status.ERROR)
    try:
        if engine is Engine.AI_OVERVIEW:
            return await _ai_overview(client, answer, variant, query, sample)
        serp_engine, params = primary_request(engine, variant, query)
        body = await client.search(serp_engine, params, sample=sample)
        if engine is Engine.COPILOT:
            answer.notes.append("copilot: not localised (no location/hl)")
    except (BudgetExceeded, CassetteMissing):
        raise
    except SerpClientError as exc:
        answer.error = str(exc)
        return answer

    if body.get("error"):
        return _api_error(answer, body)
    return from_ai_block(answer, body, engine)


async def _ai_overview(client: SerpClient, answer: AIAnswer, variant: Variant, query: str,
                       sample: int) -> AIAnswer:
    body = await client.search("google", google_params(variant, query), sample=sample)
    if body.get("error"):
        return _api_error(answer, body)
    answer.organic = parse_organic(body.get("organic_results") or [])

    block = body.get("ai_overview")
    if not block:
        answer.status = Status.NO_AI_BLOCK
        return answer
    if block.get("page_token") and not block.get("text_blocks"):
        # Google deferred the overview; the token is only valid for about a minute.
        answer.notes.append("ai_overview deferred: page_token follow-up")
        follow = await client.search("google_ai_overview", {"page_token": block["page_token"]},
                                     sample=sample)
        if follow.get("error"):
            return _api_error(answer, follow, prefix="follow-up: ")
        block = follow.get("ai_overview") or follow
    return from_ai_block(answer, block, Engine.AI_OVERVIEW)


def _api_error(answer: AIAnswer, body: dict[str, Any], prefix: str = "") -> AIAnswer:
    message = str(body["error"])
    # SerpApi reports "no results" as an error string; that is an empty answer, not a failure.
    if "hasn't returned any results" in message:
        answer.status = Status.NO_AI_BLOCK
    answer.error = f"{prefix}{message[:200]}"
    return answer
