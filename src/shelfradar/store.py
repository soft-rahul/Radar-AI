"""SQLite storage: runs and the raw answers. Everything else is derived on read."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from shelfradar.models import AIAnswer

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    finished_at   TEXT,
    study         TEXT NOT NULL,
    mode          TEXT NOT NULL,
    status        TEXT NOT NULL,          -- running | complete | partial | failed
    planned_cells INTEGER NOT NULL,
    done_cells    INTEGER NOT NULL DEFAULT 0,
    live_calls    INTEGER NOT NULL DEFAULT 0,
    replayed      INTEGER NOT NULL DEFAULT 0,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    question_id TEXT NOT NULL,
    engine      TEXT NOT NULL,
    variant     TEXT NOT NULL,
    sample      INTEGER NOT NULL,
    status      TEXT NOT NULL,
    payload     TEXT NOT NULL,            -- the full AIAnswer as JSON
    PRIMARY KEY (run_id, question_id, engine, variant, sample)
);
CREATE TABLE IF NOT EXISTS factchecks (
    run_id     INTEGER PRIMARY KEY REFERENCES runs(id),
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL              -- verified readings, dropped items, counters
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the API's event loop and its background scan task.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def create_run(self, *, study: str, mode: str, planned_cells: int) -> int:
        cur = self._db.execute(
            "INSERT INTO runs (created_at, study, mode, status, planned_cells) VALUES (?, ?, ?, 'running', ?)",
            (_now(), study, mode, planned_cells))
        self._db.commit()
        return int(cur.lastrowid)

    def add_answer(self, run_id: int, answer: AIAnswer) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, answer.question_id, answer.engine.value, answer.variant, answer.sample,
             answer.status.value, answer.model_dump_json()))
        self._db.execute("UPDATE runs SET done_cells = done_cells + 1 WHERE id = ?", (run_id,))
        self._db.commit()

    def finish_run(self, run_id: int, *, status: str, live_calls: int, replayed: int,
                   note: str | None = None) -> None:
        self._db.execute(
            "UPDATE runs SET status = ?, finished_at = ?, live_calls = ?, replayed = ?, note = ? WHERE id = ?",
            (status, _now(), live_calls, replayed, note, run_id))
        self._db.commit()

    def get_run(self, run_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self) -> list[dict]:
        return [dict(r) for r in self._db.execute("SELECT * FROM runs ORDER BY id DESC")]

    def answers(self, run_id: int) -> list[AIAnswer]:
        rows = self._db.execute(
            "SELECT payload FROM answers WHERE run_id = ? ORDER BY question_id, engine, variant, sample",
            (run_id,))
        return [AIAnswer.model_validate(json.loads(r["payload"])) for r in rows]

    def save_factcheck(self, run_id: int, payload: dict) -> None:
        self._db.execute("INSERT OR REPLACE INTO factchecks VALUES (?, ?, ?)",
                         (run_id, _now(), json.dumps(payload, ensure_ascii=False)))
        self._db.commit()

    def factcheck(self, run_id: int) -> dict | None:
        row = self._db.execute("SELECT payload FROM factchecks WHERE run_id = ?", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def close(self) -> None:
        self._db.close()
