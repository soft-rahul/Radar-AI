"""HTTP API. Scans started from the API are replay-only: a public demo must never spend credits.
Live and record scans run from the terminal (scripts/scan.py)."""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shelfradar.analyzer import DomainClassifier, MentionFinder
from shelfradar.config import PROJECT_ROOT
from shelfradar.factcheck import load_fact_sheet
from shelfradar.labels import LABELS_PATH, answer_key, load_labels, sample_for_labelling
from shelfradar.orchestrator import is_recorded, load_questions, load_study, plan_grid, run_scan
from shelfradar.report import answers_csv, build_report
from shelfradar.serp_client import DEFAULT_CASSETTES, CassetteMissing, SerpClient
from shelfradar.store import Store

STUDY_DIR = PROJECT_ROOT / "study"
WEB_DIR = PROJECT_ROOT / "web"
DB_PATH = PROJECT_ROOT / "data" / "shelfradar.sqlite3"


class ScanRequest(BaseModel):
    samples: int = 3


class LabelsIn(BaseModel):
    run_id: int
    labeller: str = ""
    labels: dict[str, dict]


def create_app(*, db_path: Path | str = DB_PATH, study_dir: Path = STUDY_DIR,
               cassette_dir: Path = DEFAULT_CASSETTES, labels_path: Path = LABELS_PATH) -> FastAPI:
    app = FastAPI(title="ShelfRadar", version="0.1.0")
    store = Store(db_path)
    study = load_study(study_dir / "whey.json")
    domains = DomainClassifier.load(study.brands, study_dir / "domain_classes.json")
    tasks: set[asyncio.Task] = set()

    def run_or_404(run_id: int) -> dict:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, f"run {run_id} not found")
        return run

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/questions")
    def questions() -> list[dict]:
        return load_questions(study_dir / "questions.json")

    @app.post("/api/scan", status_code=202)
    async def scan(req: ScanRequest) -> dict:
        client = SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=cassette_dir)
        planned = plan_grid(study, load_questions(study_dir / "questions.json"), samples=req.samples)
        # Replay whatever has been recorded; unrecorded cells are reported, not faked.
        cells = [c for c in planned if is_recorded(c, client)]
        if not cells:
            await client.__aexit__(None, None, None)
            raise HTTPException(409, "nothing recorded yet: run scripts/scan.py --mode record first")
        run_id = store.create_run(study="whey", mode="replay", planned_cells=len(cells))

        async def job() -> None:
            async with client:
                try:
                    await run_scan(client, store, run_id, cells)
                except CassetteMissing:
                    pass  # run_scan already marked the run failed, with the missing file named

        task = asyncio.create_task(job())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"run_id": run_id, "planned_cells": len(cells), "unrecorded_cells": len(planned) - len(cells),
                "mode": "replay"}

    @app.get("/api/runs")
    def runs() -> list[dict]:
        return store.list_runs()

    @app.get("/api/runs/{run_id}")
    def run(run_id: int) -> dict:
        return run_or_404(run_id)

    @app.get("/api/runs/{run_id}/report")
    def report(run_id: int, focus: str = "MuscleBlaze") -> dict:
        run = run_or_404(run_id)
        if focus not in {b.name for b in study.brands}:
            raise HTTPException(400, f"unknown brand {focus!r}")
        return {"run": run, **build_report(store.answers(run_id), study, domains, focus,
                                           factcheck=store.factcheck(run_id),
                                           fact_sheet=load_fact_sheet(study_dir / "facts.json"))}

    @app.get("/api/runs/{run_id}/export.csv", response_class=PlainTextResponse)
    def export(run_id: int) -> PlainTextResponse:
        run_or_404(run_id)
        return PlainTextResponse(answers_csv(store.answers(run_id), study, domains),
                                 media_type="text/csv")

    @app.get("/api/runs/{run_id}/label-set")
    def label_set(run_id: int, n: int = 30) -> dict:
        run_or_404(run_id)
        picked = sample_for_labelling(store.answers(run_id), n=n)
        saved = load_labels(labels_path)
        finder = MentionFinder(study.brands, study.category_terms)

        def item(a) -> dict:
            text = finder.normalise(a.text)   # offsets refer to the normalised text
            return {"key": answer_key(a), "engine": a.engine.value, "variant": a.variant,
                    "query": a.query, "text": text,
                    "found": [{"brand": m.brand, "offset": m.offset, "length": m.length}
                              for m in finder.find(text)]}

        return {"brands": [b.name for b in study.brands],
                "saved": saved.get("labels", {}) if saved.get("run_id") == run_id else {},
                "answers": [item(a) for a in picked]}

    @app.post("/api/labels")
    def save_labels(body: LabelsIn, request: Request) -> dict:
        # Writes a file in the repo: only from this machine, never from a hosted copy.
        if request.client is None or request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "labels can only be saved from localhost")
        run_or_404(body.run_id)
        labels_path.write_text(json.dumps(body.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"saved": len(body.labels), "path": str(labels_path.name)}

    # Registered last so /api/* always wins; serves index.html at "/".
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
