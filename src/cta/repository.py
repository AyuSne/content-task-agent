"""Persistence helpers shared by the loop, service and later review/export work."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime

from cta.config import utc_now_iso


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(8)


def next_seq(conn: sqlite3.Connection, task_id: str, agent: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM messages WHERE task_id = ? AND agent = ?",
        (task_id, agent),
    ).fetchone()
    return int(row["seq"]) + 1


def append_message(
    conn: sqlite3.Connection,
    task_id: str,
    role: str,
    content: str | None,
    *,
    agent: str = "main",
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
    attempt_id: str | None = None,
    meta: dict | None = None,
) -> int:
    seq = next_seq(conn, task_id, agent)
    conn.execute(
        """
        INSERT INTO messages (task_id, agent, seq, role, content, tool_calls_json,
                              tool_call_id, name, attempt_id, created_at, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            agent,
            seq,
            role,
            content,
            None if tool_calls is None else json.dumps(tool_calls, ensure_ascii=False),
            tool_call_id,
            name,
            attempt_id,
            utc_now_iso(),
            json.dumps(meta or {}, ensure_ascii=False),
        ),
    )
    return seq


def read_messages(conn: sqlite3.Connection, task_id: str, agent: str = "main") -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM messages WHERE task_id = ? AND agent = ? ORDER BY seq",
        (task_id, agent),
    ).fetchall()


def api_messages(conn: sqlite3.Connection, task_id: str, agent: str = "main") -> list[dict]:
    result = []
    for row in read_messages(conn, task_id, agent):
        message: dict = {"role": row["role"]}
        if row["content"] is not None:
            message["content"] = row["content"]
        if row["tool_calls_json"]:
            message["tool_calls"] = json.loads(row["tool_calls_json"])
        if row["tool_call_id"]:
            message["tool_call_id"] = row["tool_call_id"]
        if row["name"]:
            message["name"] = row["name"]
        result.append(message)
    return result


def find_pending_tool_calls(conn: sqlite3.Connection, task_id: str, agent: str = "main") -> list[dict]:
    """Return tool calls that have no matching tool result (interrupted round)."""
    rows = read_messages(conn, task_id, agent)
    answered = {row["tool_call_id"] for row in rows if row["role"] == "tool" and row["tool_call_id"]}
    pending = []
    for row in rows:
        if row["role"] == "assistant" and row["tool_calls_json"]:
            for call in json.loads(row["tool_calls_json"]):
                if call["id"] not in answered:
                    pending.append(call)
    return pending


def start_attempt(
    conn: sqlite3.Connection,
    *,
    task,
    kind: str,
    mode: str,
    cost_cents: int,
    duration_ms: int,
    pricing_version: str,
    meta: dict | None = None,
) -> str:
    attempt_id = new_id("a_")
    start_ms = int(task["sim_elapsed_ms"])
    end_ms = start_ms + duration_ms
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO attempts (id, task_id, kind, mode, status, cost_cents, duration_ms,
                              sim_start_ms, sim_end_ms, real_started_at, error, meta_json, created_at)
        VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            attempt_id,
            task["id"],
            kind,
            mode,
            cost_cents,
            duration_ms,
            start_ms,
            end_ms,
            now,
            json.dumps(meta or {}, ensure_ascii=False),
            now,
        ),
    )
    conn.execute(
        "UPDATE tasks SET sim_elapsed_ms = ?, updated_at = ? WHERE id = ?",
        (end_ms, now, task["id"]),
    )
    conn.execute(
        """
        INSERT INTO ledger (task_id, attempt_id, category, amount_cents, unit, source, nature, note, created_at)
        VALUES (?, ?, 'simulated_tool', ?, 'CNY_cent', ?, 'incurred', ?, ?)
        """,
        (
            task["id"],
            attempt_id,
            cost_cents,
            f"pricing:{pricing_version}:{mode}",
            f"attempt {mode}",
            now,
        ),
    )
    return attempt_id


def finish_attempt(
    conn: sqlite3.Connection,
    attempt_id: str,
    *,
    status: str,
    error: str | None = None,
    meta: dict | None = None,
) -> None:
    now = utc_now_iso()
    if meta is None:
        conn.execute(
            "UPDATE attempts SET status = ?, error = ?, real_finished_at = ? WHERE id = ?",
            (status, error, now, attempt_id),
        )
    else:
        conn.execute(
            "UPDATE attempts SET status = ?, error = ?, real_finished_at = ?, meta_json = ? WHERE id = ?",
            (status, error, now, json.dumps(meta, ensure_ascii=False), attempt_id),
        )


def record_llm_request(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    purpose: str,
    response,
    started_at: datetime,
    finished_at: datetime,
    mock: bool,
    attempt_id: str | None = None,
    meta: dict | None = None,
) -> str:
    request_id = new_id("r_")
    usage = response.usage or {}
    status = "error" if response.error else "success"
    conn.execute(
        """
        INSERT INTO llm_requests (id, task_id, attempt_id, purpose, model, status,
                                  prompt_tokens, completion_tokens, total_tokens,
                                  cache_hit_tokens, cache_miss_tokens, elapsed_ms,
                                  started_at, finished_at, error, response_model, mock, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            task_id,
            attempt_id,
            purpose,
            response.model,
            status,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            usage.get("prompt_cache_hit_tokens"),
            usage.get("prompt_cache_miss_tokens"),
            response.elapsed_ms,
            started_at.isoformat(),
            finished_at.isoformat(),
            response.error,
            response.model,
            1 if mock else 0,
            json.dumps(meta or {}, ensure_ascii=False),
        ),
    )
    return request_id


def latest_brief(conn: sqlite3.Connection, task_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM briefs WHERE task_id = ? ORDER BY version DESC LIMIT 1", (task_id,)
    ).fetchone()
    if row is None:
        return None
    return {"version": row["version"], "content": json.loads(row["content_json"]), "created_at": row["created_at"]}


def task_spent(conn: sqlite3.Connection, task_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM ledger WHERE task_id = ? AND nature = 'incurred'",
        (task_id,),
    ).fetchone()
    return int(row["total"])


def task_llm_usage(conn: sqlite3.Connection, task_id: str) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS requests,
               COALESCE(SUM(CASE WHEN mock = 0 THEN 1 ELSE 0 END), 0) AS real_requests,
               COALESCE(SUM(CASE WHEN mock = 1 THEN 1 ELSE 0 END), 0) AS mock_requests,
               COALESCE(SUM(CASE WHEN prompt_tokens IS NULL THEN 1 ELSE 0 END), 0) AS unknown_usage_requests,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
               COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,
               COALESCE(SUM(elapsed_ms), 0) AS elapsed_ms
        FROM llm_requests WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    return {
        "requests": int(row["requests"]),
        "real_requests": int(row["real_requests"]),
        "mock_requests": int(row["mock_requests"]),
        "unknown_usage_requests": int(row["unknown_usage_requests"]),
        "prompt_tokens": int(row["prompt_tokens"]),
        "completion_tokens": int(row["completion_tokens"]),
        "cache_hit_tokens": int(row["cache_hit_tokens"]),
        "cache_miss_tokens": int(row["cache_miss_tokens"]),
        "elapsed_ms": int(row["elapsed_ms"]),
    }


def snapshot_state(conn: sqlite3.Connection, task_id: str) -> dict:
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    brief = latest_brief(conn, task_id)
    questions = conn.execute(
        "SELECT questions_json FROM clarifications WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)
    ).fetchone()
    return {
        "phase": task["phase"],
        "status": task["status"],
        "input_version": task["input_version"],
        "brief_version": task["brief_version"],
        "sim_elapsed_ms": task["sim_elapsed_ms"],
        "used_rounds": task["used_rounds"],
        "spent_cents": task_spent(conn, task_id),
        "brief_goal": (brief or {}).get("content", {}).get("goal"),
        "open_questions": [] if questions is None else json.loads(questions["questions_json"]),
        "save": None,
    }


def append_snapshot(conn: sqlite3.Connection, task_id: str, state: dict) -> tuple[bool, str]:
    import hashlib

    payload = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    last = conn.execute(
        "SELECT hash FROM snapshots WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)
    ).fetchone()
    if last is not None and last["hash"] == digest:
        return False, digest
    conn.execute(
        "INSERT OR IGNORE INTO snapshots (task_id, hash, content_json, created_at) VALUES (?, ?, ?, ?)",
        (task_id, digest, payload, utc_now_iso()),
    )
    append_message(
        conn,
        task_id,
        "user",
        f"[状态快照] {json.dumps(state, ensure_ascii=False)}",
        meta={"kind": "snapshot", "hash": digest},
    )
    conn.execute("UPDATE tasks SET snapshot_hash = ? WHERE id = ?", (digest, task_id))
    return True, digest


def iso_now() -> datetime:
    return datetime.now(UTC)
