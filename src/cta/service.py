"""Task intake and query use cases exposed by the CLI."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from cta.config import Config, to_utc_iso, utc_now_iso
from cta.db import open_store, transaction
from cta.identity import Intake, RawInput, dedupe_key, normalize
from cta.repository import latest_brief, task_llm_usage

STATUS_NEEDS_INPUT = "needs_input"
STATUS_IN_PROGRESS = "in_progress"
STATUS_INCOMPLETE = "incomplete"
STATUS_COMPLETE = "complete"
MAX_ROUNDS = 12


class TaskNotFound(Exception):
    def __init__(self, task_id: str):
        super().__init__(f"task not found: {task_id}")
        self.task_id = task_id


class TaskService:
    def __init__(self, config: Config):
        self.config = config

    def create(self, raw: RawInput, *, explicit_new: bool = False) -> dict:
        intake = normalize(raw, self.config.sim_start, self.config.mode)
        self.config.exports_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        key = None if explicit_new else dedupe_key(self.config.mode, intake.fingerprint)
        with open_store(self.config.db_path) as conn, transaction(conn):
            existing = None
            if key is not None:
                existing = conn.execute("SELECT * FROM tasks WHERE dedupe_key = ?", (key,)).fetchone()
            if existing is not None:
                return self._view(conn, existing, duplicate=True)

            task_id = "t_" + secrets.token_hex(6)
            status = STATUS_IN_PROGRESS if intake.valid else STATUS_NEEDS_INPUT
            reason = "已创建，尚未开始执行；当前没有已生成产物。" if intake.valid else intake.failure_summary()
            conn.execute(
                """
                INSERT INTO tasks (id, mode, fingerprint, dedupe_key, status, reason,
                                   input_version, current_output_id, created_at, updated_at,
                                   sim_elapsed_ms, sim_start, used_rounds, paused)
                VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, 0, ?, 0, 0)
                """,
                (
                    task_id,
                    self.config.mode,
                    intake.fingerprint,
                    key,
                    status,
                    reason,
                    now,
                    now,
                    self.config.sim_start.isoformat(),
                ),
            )
            self._insert_input(conn, task_id, intake, now)
            task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._view(conn, task, duplicate=False)

    def _insert_input(self, conn, task_id: str, intake: Intake, now: str) -> None:
        conn.execute(
            """
            INSERT INTO task_inputs (task_id, version, topic, budget_cents, deadline,
                                     audience, requirements_json, raw_json,
                                     validation_json, interpretations_json, created_at)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                intake.topic,
                intake.budget_cents,
                None if intake.deadline_utc is None else to_utc_iso(intake.deadline_utc),
                intake.audience,
                json.dumps(intake.requirements, ensure_ascii=False),
                json.dumps(intake.raw.as_dict(), ensure_ascii=False),
                json.dumps([e.as_dict() for e in intake.errors], ensure_ascii=False),
                json.dumps(intake.interpretations, ensure_ascii=False),
                now,
            ),
        )

    def status(self, task_id: str) -> dict:
        with open_store(self.config.db_path) as conn:
            task = self._require_task(conn, task_id)
            return self._view(conn, task, duplicate=False)

    def inspect(self, task_id: str) -> dict:
        with open_store(self.config.db_path) as conn:
            task = self._require_task(conn, task_id)
            view = self._view(conn, task, duplicate=False)
            view["briefs"] = [
                {"version": row["version"], "content": json.loads(row["content_json"]), "created_at": row["created_at"]}
                for row in conn.execute("SELECT * FROM briefs WHERE task_id = ? ORDER BY version", (task_id,))
            ]
            view["knowledge"] = [
                {
                    "knowledge_id": row["knowledge_id"],
                    "version": row["version"],
                    "sha256": row["sha256"],
                    "chars": len(row["content"]),
                    "loaded_at": row["loaded_at"],
                }
                for row in conn.execute("SELECT * FROM knowledge_loads WHERE task_id = ? ORDER BY id", (task_id,))
            ]
            view["messages"] = [
                {
                    "seq": row["seq"],
                    "role": row["role"],
                    "content": row["content"],
                    "tool_calls": json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else [],
                    "tool_call_id": row["tool_call_id"],
                    "name": row["name"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute(
                    "SELECT * FROM messages WHERE task_id = ? AND agent = 'main' ORDER BY seq", (task_id,)
                )
            ]
            view["attempts"] = [
                dict(row) for row in conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY created_at, id", (task_id,))
            ]
            view["ledger"] = [dict(row) for row in conn.execute("SELECT * FROM ledger WHERE task_id = ? ORDER BY id", (task_id,))]
            view["llm_requests"] = [
                dict(row) for row in conn.execute("SELECT * FROM llm_requests WHERE task_id = ? ORDER BY started_at, id", (task_id,))
            ]
            view["clarifications"] = [
                {"questions": json.loads(row["questions_json"]), "created_at": row["created_at"]}
                for row in conn.execute("SELECT * FROM clarifications WHERE task_id = ? ORDER BY id", (task_id,))
            ]
            return view

    def list_tasks(self) -> dict:
        with open_store(self.config.db_path) as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC, id DESC").fetchall()
            tasks = [self._summary(row) for row in rows]
        return {"mode": self.config.mode, "count": len(tasks), "tasks": tasks}

    @staticmethod
    def _require_task(conn, task_id: str):
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise TaskNotFound(task_id)
        return task

    def _summary(self, task) -> dict:
        return {
            "task_id": task["id"],
            "mode": task["mode"],
            "status": task["status"],
            "reason": task["reason"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }

    def _view(self, conn, task, *, duplicate: bool) -> dict:
        input_row = conn.execute(
            "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?",
            (task["id"], task["input_version"]),
        ).fetchone()
        spent = self._sim_spent(conn, task["id"])
        brief = latest_brief(conn, task["id"])
        return {
            "task_id": task["id"],
            "mode": task["mode"],
            "status": task["status"],
            "reason": self.config.redact(task["reason"]),
            "duplicate": duplicate,
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "input": self._input_view(task, input_row),
            "validation": {
                "errors": json.loads(input_row["validation_json"]),
                "interpretations": json.loads(input_row["interpretations_json"]),
            },
            "brief": brief,
            "plan": self._plan_view(task),
            "gaps": self._gaps(conn, task, brief),
            "mode_notice": (
                "Mock 仅验证流程、状态与确定性规则，不代表真实模型决策或语义质量。"
                if task["mode"] == "mock"
                else "真实模型运行；小样本观察不代表生产质量或统计显著结论。"
            ),
            "progress": self._progress_view(task),
            "resources": self._resources_view(conn, task["id"], input_row, spent),
            "model_config": self.config.model_config_public(),
            "artifacts": {},
        }

    @staticmethod
    def _plan_view(task) -> dict:
        brief_done = int(task["brief_version"]) > 0
        phase = task["phase"]
        draft_current = phase == "brief_ready"
        return {
            "version": "plan-2026-10-29.v1",
            "steps": [
                {"step": "brief", "title": "业务简报", "status": "done" if brief_done else "current"},
                {"step": "campaign_draft", "title": "完整活动方案草稿", "status": "current" if draft_current else "pending"},
                {"step": "review", "title": "内容核验", "status": "pending"},
                {"step": "save", "title": "核实保存与导出", "status": "pending"},
            ],
        }

    @staticmethod
    def _gaps(conn, task, brief: dict | None) -> list[dict]:
        gaps = []
        for item in (brief or {}).get("content", {}).get("unknowns", []):
            gaps.append({"source": "brief", **item})
        latest = conn.execute(
            "SELECT questions_json FROM clarifications WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task["id"],)
        ).fetchone()
        if latest is not None:
            for item in json.loads(latest["questions_json"]):
                gaps.append({"source": "clarification", **item})
        return gaps

    @staticmethod
    def _input_view(task, input_row) -> dict:
        budget_cents = input_row["budget_cents"]
        return {
            "version": input_row["version"],
            "topic": input_row["topic"],
            "budget": None if budget_cents is None else f"{budget_cents / 100:.2f}",
            "budget_cents": budget_cents,
            "deadline": input_row["deadline"],
            "audience": input_row["audience"],
            "requirements": json.loads(input_row["requirements_json"]),
            "raw": json.loads(input_row["raw_json"]),
            "fingerprint": task["fingerprint"],
        }

    @staticmethod
    def _progress_view(task) -> dict:
        sim_start = datetime.fromisoformat(task["sim_start"])
        sim_now = sim_start + timedelta(milliseconds=task["sim_elapsed_ms"])
        current_output = {"output_id": task["current_output_id"]} if task["current_output_id"] else None
        return {
            "sim_start": task["sim_start"],
            "sim_now": sim_now.isoformat(),
            "sim_elapsed_ms": task["sim_elapsed_ms"],
            "used_rounds": task["used_rounds"],
            "max_rounds": MAX_ROUNDS,
            "paused": bool(task["paused"]),
            "current_output": current_output,
            "save": None,
            "phase": task["phase"],
            "brief_version": task["brief_version"],
            "last_run_at": task["last_run_at"],
        }

    @staticmethod
    def _resources_view(conn, task_id: str, input_row, spent: int) -> dict:
        budget_cents = input_row["budget_cents"]
        usage = task_llm_usage(conn, task_id)
        return {
            "budget_cents": budget_cents,
            "sim_spent_cents": spent,
            "remaining_cents": None if budget_cents is None else budget_cents - spent,
            "real_requests": usage["real_requests"],
            "mock_requests": usage["mock_requests"],
            "model_requests": usage["requests"],
            "real_usage": usage,
            "real_cost": None,
        }

    @staticmethod
    def _sim_spent(conn, task_id: str) -> int:
        table = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ledger'").fetchone()
        if table is None:
            return 0
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM ledger WHERE task_id = ? AND nature = 'incurred'",
            (task_id,),
        ).fetchone()
        return int(row["total"])
