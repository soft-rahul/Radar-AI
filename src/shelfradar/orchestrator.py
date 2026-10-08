"""Run the study grid: questions × engines × language variants × samples, safely and resumably."""

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from shelfradar.engines import fetch_answer, primary_request
from shelfradar.models import AIAnswer, Engine, Study, Variant
from shelfradar.serp_client import BudgetExceeded, SerpClient
from shelfradar.store import Store

# Share of uncached AI Overview cells expected to need a page_token follow-up (probe: 1 of 2).
FOLLOW_UP_ALLOWANCE = 0.5


@dataclass(frozen=True)
class Cell:
    question_id: str
    engine: Engine
    variant: Variant
    query: str
    sample: int


@dataclass
class Estimate:
    cells: int
    cached: int
    to_fetch: int
    follow_up_allowance: int

    @property
    def searches(self) -> int:
        return self.to_fetch + self.follow_up_allowance


def load_study(path: Path) -> Study:
    return Study.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "approved":
        raise ValueError(f"{path} is not approved (status={data.get('status')!r})")
    return data["questions"]


def plan_grid(study: Study, questions: list[dict], *, samples: int = 3,
              engines: list[Engine] | None = None) -> list[Cell]:
    return [
        Cell(q["id"], engine, variant, q[variant.lang]["text"], s)
        for q in questions
        for engine in (engines or list(Engine))
        for variant in study.variants
        for s in range(samples)
    ]


def is_recorded(cell: Cell, client: SerpClient) -> bool:
    serp_engine, params = primary_request(cell.engine, cell.variant, cell.query)
    return client.cassette_path(serp_engine, params, cell.sample).exists()


def estimate(cells: list[Cell], client: SerpClient) -> Estimate:
    cached = aio_uncached = 0
    for c in cells:
        if is_recorded(c, client):
            cached += 1
        elif c.engine is Engine.AI_OVERVIEW:
            aio_uncached += 1
    to_fetch = len(cells) - cached
    return Estimate(len(cells), cached, to_fetch, math.ceil(aio_uncached * FOLLOW_UP_ALLOWANCE))


async def run_scan(client: SerpClient, store: Store, run_id: int, cells: list[Cell], *,
                   concurrency: int = 5,
                   on_answer: Callable[[AIAnswer], None] | None = None) -> str:
    """Fetch every cell, saving each answer as it lands. Returns the final run status."""
    gate = asyncio.Semaphore(concurrency)
    out_of_budget = asyncio.Event()

    async def one(cell: Cell) -> None:
        if out_of_budget.is_set():
            return
        async with gate:
            if out_of_budget.is_set():
                return
            try:
                answer = await fetch_answer(client, cell.engine, cell.variant, cell.query, cell.sample)
            except BudgetExceeded:
                out_of_budget.set()
                return
        answer.question_id = cell.question_id
        store.add_answer(run_id, answer)
        if on_answer:
            on_answer(answer)

    try:
        await asyncio.gather(*(one(c) for c in cells))
    except Exception as exc:
        store.finish_run(run_id, status="failed", live_calls=client.ledger.live_calls,
                         replayed=client.ledger.replayed, note=f"{type(exc).__name__}: {exc}"[:300])
        raise

    status = "partial" if out_of_budget.is_set() else "complete"
    note = "search budget reached; remaining cells skipped" if status == "partial" else None
    store.finish_run(run_id, status=status, live_calls=client.ledger.live_calls,
                     replayed=client.ledger.replayed, note=note)
    return status
