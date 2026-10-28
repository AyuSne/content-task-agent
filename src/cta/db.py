"""SQLite store: schema and connection helpers.

SQLite is the authoritative source for task identity, inputs, messages,
attempts, versions and saves. Test helpers never query it directly for
behaviour; they observe the public CLI.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cta import __version__

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    dedupe_key TEXT UNIQUE,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    input_version INTEGER NOT NULL,
    current_output_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sim_elapsed_ms INTEGER NOT NULL DEFAULT 0,
    sim_start TEXT NOT NULL,
    used_rounds INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks(mode, fingerprint);

CREATE TABLE IF NOT EXISTS task_inputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    topic TEXT,
    budget_cents INTEGER,
    deadline TEXT,
    audience TEXT,
    requirements_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    interpretations_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('app_version', ?)", (__version__,))
    conn.commit()


@contextmanager
def open_store(path: Path):
    conn = connect(path)
    try:
        initialize(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
