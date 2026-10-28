"""SQLite store: schema, light migrations and connection helpers.

SQLite is the authoritative source for task identity, inputs, messages,
attempts, versions and saves. Test helpers never query it directly for
behaviour; they observe the public CLI.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cta import __version__

SCHEMA_VERSION = 5

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
    paused INTEGER NOT NULL DEFAULT 0,
    phase TEXT NOT NULL DEFAULT 'created',
    brief_version INTEGER NOT NULL DEFAULT 0,
    snapshot_hash TEXT,
    last_run_at TEXT,
    materials_version TEXT NOT NULL DEFAULT '',
    pricing_version TEXT NOT NULL DEFAULT '',
    current_review_id TEXT,
    current_save_id TEXT
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

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent TEXT NOT NULL DEFAULT 'main',
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    name TEXT,
    attempt_id TEXT,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(task_id, agent, seq)
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    cost_cents INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    sim_start_ms INTEGER,
    sim_end_ms INTEGER,
    real_started_at TEXT,
    real_finished_at TEXT,
    error TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_id TEXT,
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    nature TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_requests (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_id TEXT,
    purpose TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cache_hit_tokens INTEGER,
    cache_miss_tokens INTEGER,
    elapsed_ms INTEGER,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    response_model TEXT,
    mock INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS knowledge_loads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent TEXT NOT NULL DEFAULT 'main',
    knowledge_id TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    loaded_at TEXT NOT NULL,
    UNIQUE(task_id, agent, knowledge_id)
);

CREATE TABLE IF NOT EXISTS material_snapshots (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    material_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_json TEXT NOT NULL,
    loaded_at TEXT NOT NULL,
    PRIMARY KEY (task_id, material_id)
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE IF NOT EXISTS clarifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    questions_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, hash)
);

CREATE TABLE IF NOT EXISTS outputs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_md TEXT,
    input_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, kind, version)
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    output_id TEXT NOT NULL,
    output_version INTEGER NOT NULL,
    attempt_id TEXT,
    status TEXT NOT NULL,
    verdict TEXT,
    completeness TEXT,
    findings_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    evidence_gaps_json TEXT NOT NULL DEFAULT '[]',
    requests_used INTEGER NOT NULL DEFAULT 0,
    readbacks_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(review_id, finding_id)
);

CREATE TABLE IF NOT EXISTS saves (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    output_id TEXT NOT NULL,
    output_version INTEGER NOT NULL,
    attempt_id TEXT,
    status TEXT NOT NULL,
    files_json TEXT NOT NULL DEFAULT '[]',
    verified INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    additions = {
        "phase": "TEXT NOT NULL DEFAULT 'created'",
        "brief_version": "INTEGER NOT NULL DEFAULT 0",
        "snapshot_hash": "TEXT",
        "last_run_at": "TEXT",
        "materials_version": "TEXT NOT NULL DEFAULT ''",
        "pricing_version": "TEXT NOT NULL DEFAULT ''",
        "current_review_id": "TEXT",
        "current_save_id": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
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
