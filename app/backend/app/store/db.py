"""SQLite 연결과 저장소 스키마."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "planner.db"
_KST = timezone(timedelta(hours=9))
_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_nm TEXT NOT NULL,
    movie_cd TEXT,
    tmdb_id INTEGER,
    watch_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    runtime_min INTEGER,
    theater_name TEXT,
    theater_address TEXT,
    memo TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    stage TEXT,
    payload TEXT NOT NULL,
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_run_logs_session_id ON run_logs (session_id);

CREATE TABLE IF NOT EXISTS anomaly_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    run_index INTEGER NOT NULL,
    first_seq INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('normal', 'anomaly', 'undetermined')),
    warning_count INTEGER NOT NULL,
    findings TEXT NOT NULL,
    missing_evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, run_index)
);

CREATE TABLE IF NOT EXISTS anomaly_diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL UNIQUE REFERENCES anomaly_reports (id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'skipped')),
    text TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS delete_session_anomaly_reports
AFTER DELETE ON run_logs
WHEN NOT EXISTS (SELECT 1 FROM run_logs WHERE session_id = OLD.session_id)
BEGIN
    DELETE FROM anomaly_reports WHERE session_id = OLD.session_id;
END;
"""


def db_path() -> Path:
    return Path(os.environ.get("PLANNER_DB_PATH") or _DEFAULT_PATH)


def get_conn() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        conn.close()
        raise
    return conn


def init_db() -> None:
    with closing(get_conn()) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    with closing(get_conn()) as conn, conn:
        conn.executescript(_SCHEMA)
        yield conn


def now_kst_iso() -> str:
    return datetime.now(_KST).isoformat()
