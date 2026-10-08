import json
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from shelfradar.api import create_app
from shelfradar.config import PROJECT_ROOT
from shelfradar.models import Engine, Status
from shelfradar.orchestrator import estimate, is_recorded, load_questions, load_study, plan_grid, run_scan
from shelfradar.serp_client import DEFAULT_CASSETTES, SerpClient
from shelfradar.store import Store

STUDY = load_study(PROJECT_ROOT / "study/whey.json")
QUESTIONS = load_questions(PROJECT_ROOT / "study/questions.json")


def replay_client() -> SerpClient:
    return SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=DEFAULT_CASSETTES)


def test_grid_is_questions_x_engines_x_variants_x_samples():
    cells = plan_grid(STUDY, QUESTIONS, samples=3)
    assert len(cells) == 5 * 3 * 3 * 3 == 135
    hinglish = next(c for c in cells if c.variant.id == "delhi-hinglish" and c.question_id == "q2")
    assert hinglish.query == "kis company ka whey protein sabse accha hai"


def test_estimate_reuses_recorded_cassettes():
    # The 6 Oct study recorded samples 0 and 1 of every cell (90). Sample 2 would be new.
    est = estimate(plan_grid(STUDY, QUESTIONS, samples=3), replay_client())
    assert est.cached == 90 and est.to_fetch == 45
    assert est.follow_up_allowance == 8    # half of the 15 uncached AI Overview cells, rounded up
    assert estimate(plan_grid(STUDY, QUESTIONS, samples=2), replay_client()).searches == 0


def test_unapproved_questions_are_refused(tmp_path):
    path = tmp_path / "q.json"
    path.write_text('{"status": "proposed", "questions": []}')
    with pytest.raises(ValueError, match="not approved"):
        load_questions(path)


async def test_replay_scan_of_recorded_cells_completes_and_saves_every_answer():
    client = replay_client()
    cells = [c for c in plan_grid(STUDY, QUESTIONS, samples=3) if is_recorded(c, client)]
    store = Store(":memory:")
    run_id = store.create_run(study="whey", mode="replay", planned_cells=len(cells))
    async with client:
        status = await run_scan(client, store, run_id, cells)
    answers = store.answers(run_id)
    assert status == "complete" and len(answers) == 90
    assert all(a.status in (Status.OK, Status.NO_AI_BLOCK) for a in answers)
    assert store.get_run(run_id)["live_calls"] == 0


def _fake_serpapi(max_seen: list[int], delay: float = 0.01):
    in_flight = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight
        in_flight += 1
        max_seen[0] = max(max_seen[0], in_flight)
        await asyncio.sleep(delay)
        in_flight -= 1
        return httpx.Response(200, json={"text_blocks": [{"type": "paragraph",
                                                          "snippet": "MuscleBlaze whey"}]})
    return httpx.MockTransport(handler)


async def test_never_more_than_five_requests_at_once():
    peak = [0]
    cells = plan_grid(STUDY, QUESTIONS, samples=1, engines=[Engine.AI_MODE, Engine.COPILOT])
    client = SerpClient(api_key="k" * 20, mode="live", max_searches=100,
                        transport=_fake_serpapi(peak), retry_delay=0)
    store = Store(":memory:")
    run_id = store.create_run(study="whey", mode="live", planned_cells=len(cells))
    async with client:
        assert await run_scan(client, store, run_id, cells, concurrency=5) == "complete"
    assert peak[0] == 5 and len(store.answers(run_id)) == len(cells) == 30


async def test_budget_hit_mid_run_saves_a_partial_run():
    cells = plan_grid(STUDY, QUESTIONS, samples=1, engines=[Engine.AI_MODE])  # 15 cells
    client = SerpClient(api_key="k" * 20, mode="live", max_searches=6,
                        transport=_fake_serpapi([0]), retry_delay=0)
    store = Store(":memory:")
    run_id = store.create_run(study="whey", mode="live", planned_cells=len(cells))
    async with client:
        status = await run_scan(client, store, run_id, cells)
    run = store.get_run(run_id)
    assert status == "partial" and run["status"] == "partial"
    assert run["live_calls"] == 6 and len(store.answers(run_id)) == 6
    assert "budget" in run["note"]


def test_api_replays_recorded_cells_and_reports(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite3")
    with TestClient(app) as http:
        started = http.post("/api/scan", json={"samples": 3}).json()
        assert started["planned_cells"] == 90 and started["unrecorded_cells"] == 45
        run_id = started["run_id"]
        for _ in range(100):
            if http.get(f"/api/runs/{run_id}").json()["status"] != "running":
                break
        report = http.get(f"/api/runs/{run_id}/report").json()
        assert report["run"]["status"] == "complete"
        assert report["overall"][0]["brand"] == "MuscleBlaze"
        assert report["outside_top10"]["sources"] == 184
        csv = http.get(f"/api/runs/{run_id}/export.csv").text
        assert csv.splitlines()[0].startswith("question_id,engine")
        assert len(csv.splitlines()) == 91
        assert http.get("/api/runs/999").status_code == 404
        assert http.get(f"/api/runs/{run_id}/report?focus=Nobody").status_code == 400


def test_dashboard_is_served_and_report_has_every_field_it_reads(tmp_path):
    import re

    app = create_app(db_path=tmp_path / "t.sqlite3")
    with TestClient(app) as http:
        page = http.get("/")
        assert page.status_code == 200 and "ShelfRadar" in page.text
        js = http.get("/app.js").text
        run_id = http.post("/api/scan", json={"samples": 3}).json()["run_id"]
        for _ in range(100):
            if http.get(f"/api/runs/{run_id}").json()["status"] != "running":
                break
        report = http.get(f"/api/runs/{run_id}/report").json()
    used = set(re.findall(r"rep\.(\w+)", js))
    assert used <= set(report), used - set(report)
    assert {"named", "named_first", "own_site_cited"} <= set(report["focus_kpis"])


def test_label_set_is_stratified_blind_and_saves_locally(tmp_path):
    from collections import Counter

    labels = tmp_path / "labels.json"
    app = create_app(db_path=tmp_path / "t.sqlite3", labels_path=labels)
    with TestClient(app) as http:
        run_id = http.post("/api/scan", json={"samples": 2}).json()["run_id"]
        for _ in range(100):
            if http.get(f"/api/runs/{run_id}").json()["status"] != "running":
                break
        data = http.get(f"/api/runs/{run_id}/label-set").json()
        assert len(data["answers"]) == 30
        groups = Counter((a["engine"], a["variant"]) for a in data["answers"])
        assert len(groups) == 9 and min(groups.values()) >= 3          # every engine x language
        assert "mentioned" not in data["answers"][0]                    # blind: no tool output
        again = http.get(f"/api/runs/{run_id}/label-set").json()
        assert [a["key"] for a in again["answers"]] == [a["key"] for a in data["answers"]]  # fixed seed
        key = data["answers"][0]["key"]
        saved = http.post("/api/labels", json={"run_id": run_id, "labels": {key: {"brands": ["Nakpro"], "other": ""}}})
        assert saved.status_code == 200 and json.loads(labels.read_text())["labels"][key]["brands"] == ["Nakpro"]


def test_score_counts_hits_misses_and_false_alarms():
    import json as _json
    from shelfradar.analyzer import MentionFinder
    from shelfradar.labels import score
    from shelfradar.models import AIAnswer

    finder = MentionFinder(STUDY.brands, STUDY.category_terms)
    ans = AIAnswer(engine=Engine.AI_MODE, variant="mumbai-en", query="q", question_id="q1", sample=0,
                   status=Status.OK, text="MuscleBlaze and Nakpro are good. Biozyme is great.")
    labels = {"q1|ai_mode|mumbai-en|0": {"brands": ["MuscleBlaze", "Avvatar"], "other": "Labrada"}}
    s = score([ans], labels, finder)
    assert (s["true_positives"], s["false_positives"], s["false_negatives"]) == (1, 1, 1)
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert s["other_brands_written_in"] == ["Labrada"]


def test_verify_mode_scoring():
    from shelfradar.analyzer import MentionFinder
    from shelfradar.labels import score_verified
    from shelfradar.models import AIAnswer

    finder = MentionFinder(STUDY.brands, STUDY.category_terms)
    ans = AIAnswer(engine=Engine.AI_MODE, variant="mumbai-en", query="q", question_id="q1", sample=0,
                   status=Status.OK, text="MuscleBlaze and Nakpro. Also try Avvatar.")
    labels = {"q1|ai_mode|mumbai-en|0": {"right": ["MuscleBlaze", "Avvatar"], "wrong": ["Nakpro"],
                                         "other": "Wellcore, Biozyme"}}
    s = score_verified([ans], labels, finder, [b.name for b in STUDY.brands])
    assert (s["true_positives"], s["false_positives"], s["false_negatives"]) == (2, 1, 1)
    assert s["precision"] == round(2 / 3, 4) and s["recall_upper_bound"] == round(2 / 3, 4)
    assert s["other_brands_written_in"] == ["Biozyme"]


def test_label_set_offsets_point_at_the_brand_text(tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite3", labels_path=tmp_path / "l.json")
    with TestClient(app) as http:
        run_id = http.post("/api/scan", json={"samples": 2}).json()["run_id"]
        for _ in range(100):
            if http.get(f"/api/runs/{run_id}").json()["status"] != "running":
                break
        data = http.get(f"/api/runs/{run_id}/label-set").json()
    finder_brands = {b.name: [b.name, *b.aliases, *b.ambiguous_aliases] for b in STUDY.brands}
    for a in data["answers"]:
        for f in a["found"]:
            span = a["text"][f["offset"]:f["offset"] + f["length"]].casefold().replace("-", " ")
            assert any(span == alias.casefold().replace("-", " ") for alias in finder_brands[f["brand"]]), span
