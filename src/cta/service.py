"""Task intake and query use cases exposed by the CLI."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta

from cta import __version__
from cta.config import Config, to_utc_iso, utc_now_iso
from cta.db import open_store, transaction
from cta.identity import Intake, RawInput, dedupe_key, normalize
from cta.materials import load_catalog
from cta.pricing import load_price_table
from cta.prompts import PROMPT_VERSION
from cta.repository import (
    append_message,
    append_snapshot,
    latest_brief,
    latest_output,
    latest_review,
    latest_save,
    pending_todos,
    plan_view,
    snapshot_state,
    task_llm_usage,
    task_spent,
)

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
                                   sim_elapsed_ms, sim_start, used_rounds, paused,
                                   materials_version, pricing_version)
                VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, 0, ?, 0, 0, ?, ?)
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
                    load_catalog(self.config.project_root).version,
                    load_price_table(self.config.project_root).version,
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

    def pause(self, task_id: str) -> dict:
        """Pause explicitly without touching the simulated clock or round count."""
        with open_store(self.config.db_path) as conn:
            task = self._require_task(conn, task_id)
            if task["status"] != STATUS_IN_PROGRESS:
                raise ValueError("只有进行中的任务可以暂停。")
            if not task["paused"]:
                now = utc_now_iso()
                with transaction(conn):
                    conn.execute(
                        "UPDATE tasks SET paused = 1, updated_at = ? WHERE id = ?",
                        (now, task_id),
                    )
                    append_message(
                        conn,
                        task_id,
                        "user",
                        "[暂停] 运营显式暂停；模拟时钟、预算、截止与已用轮数保持不变，由显式续跑继续。",
                        meta={"kind": "pause"},
                    )
                    append_snapshot(conn, task_id, snapshot_state(conn, task_id))
                task = self._require_task(conn, task_id)
            view = self._view(conn, task, duplicate=False)
            view["paused_now"] = True
            return view

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
                dict(row) for row in conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY rowid", (task_id,))
            ]
            view["ledger"] = [dict(row) for row in conn.execute("SELECT * FROM ledger WHERE task_id = ? ORDER BY id", (task_id,))]
            view["llm_requests"] = [
                dict(row) for row in conn.execute("SELECT * FROM llm_requests WHERE task_id = ? ORDER BY started_at, id", (task_id,))
            ]
            view["clarifications"] = [
                {"questions": json.loads(row["questions_json"]), "created_at": row["created_at"]}
                for row in conn.execute("SELECT * FROM clarifications WHERE task_id = ? ORDER BY id", (task_id,))
            ]
            view["outputs"] = [
                {
                    "output_id": row["id"],
                    "version": row["version"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "content": json.loads(row["content_json"]),
                    "content_md": row["content_md"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute("SELECT * FROM outputs WHERE task_id = ? ORDER BY version", (task_id,))
            ]
            generator_count = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'generator'", (task_id,)
            ).fetchone()
            view["generator_messages_count"] = int(generator_count["n"])
            view["generator_messages"] = [
                {
                    "sequence": row["seq"],
                    "role": row["role"],
                    "content": row["content"],
                    "candidate": json.loads(row["meta_json"]).get("candidate"),
                }
                for row in conn.execute(
                    "SELECT * FROM messages WHERE task_id = ? AND agent = 'generator' ORDER BY seq", (task_id,)
                )
            ]
            view["reviews"] = [
                {
                    "review_id": row["id"],
                    "output_version": row["output_version"],
                    "status": row["status"],
                    "verdict": row["verdict"],
                    "completeness": row["completeness"],
                    "findings": json.loads(row["findings_json"]),
                    "summary": row["summary"],
                    "evidence_gaps": json.loads(row["evidence_gaps_json"]),
                    "requests_used": row["requests_used"],
                    "readbacks_used": row["readbacks_used"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute("SELECT * FROM reviews WHERE task_id = ? ORDER BY rowid", (task_id,))
            ]
            view["saves"] = [
                {
                    "save_id": row["id"],
                    "output_version": row["output_version"],
                    "status": row["status"],
                    "files": json.loads(row["files_json"]),
                    "verified": bool(row["verified"]),
                    "verified_at": row["verified_at"],
                    "error": row["error"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute("SELECT * FROM saves WHERE task_id = ? ORDER BY rowid", (task_id,))
            ]
            view["dispositions"] = [
                {
                    "finding_id": row["finding_id"],
                    "decision": row["decision"],
                    "reason": row["reason"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute(
                    "SELECT * FROM dispositions WHERE review_id IN (SELECT id FROM reviews WHERE task_id = ?) ORDER BY id",
                    (task_id,),
                )
            ]
            view["reviewer_knowledge"] = [
                {
                    "knowledge_id": row["knowledge_id"],
                    "version": row["version"],
                    "sha256": row["sha256"],
                    "chars": len(row["content"]),
                }
                for row in conn.execute(
                    "SELECT * FROM knowledge_loads WHERE task_id = ? AND agent = 'reviewer' ORDER BY id", (task_id,)
                )
            ]
            view["material_snapshots"] = [
                {
                    "material_id": row["material_id"],
                    "version": row["version"],
                    "sha256": hashlib.sha256(row["content_json"].encode("utf-8")).hexdigest(),
                    "chars": len(json.loads(row["content_json"]).get("text", "")),
                    "loaded_at": row["loaded_at"],
                }
                for row in conn.execute(
                    "SELECT * FROM material_snapshots WHERE task_id = ? ORDER BY material_id", (task_id,)
                )
            ]
            view["snapshots"] = [
                {"hash": row["hash"], "content": json.loads(row["content_json"]), "created_at": row["created_at"]}
                for row in conn.execute(
                    "SELECT * FROM snapshots WHERE task_id = ? ORDER BY id", (task_id,)
                )
            ]
            view["recoveries"] = [
                {
                    "seq": row["seq"],
                    "summary": json.loads(row["meta_json"]).get("summary"),
                    "created_at": row["created_at"],
                }
                for row in conn.execute(
                    "SELECT * FROM messages WHERE task_id = ? AND agent = 'main' "
                    "AND meta_json LIKE '%recovery_fact%' ORDER BY seq",
                    (task_id,),
                )
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
        spent = task_spent(conn, task["id"])
        brief = latest_brief(conn, task["id"])
        output = latest_output(conn, task["id"])
        review = latest_review(conn, task["id"])
        save = latest_save(conn, task["id"])
        recovery_row = conn.execute(
            "SELECT meta_json, created_at FROM messages WHERE task_id = ? AND agent = 'main' "
            "AND meta_json LIKE '%recovery_fact%' ORDER BY seq DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
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
            "review": review,
            "save": save,
            "plan": plan_view(task, output, review, save),
            "todos": pending_todos(task, output, review, save),
            "recovery": None if recovery_row is None else json.loads(recovery_row["meta_json"]).get("summary"),
            "gaps": self._gaps(conn, task, brief),
            "mode_notice": (
                "Mock 仅验证流程、状态与确定性规则，不代表真实模型决策或语义质量。"
                if task["mode"] == "mock"
                else "真实模型运行；小样本观察不代表生产质量或统计显著结论。"
            ),
            "progress": self._progress_view(task, output, save),
            "resources": self._resources_view(conn, task["id"], input_row, spent),
            "model_config": self.config.model_config_public(),
            "versions": {
                "app": __version__,
                "prompt": PROMPT_VERSION,
                "materials": task["materials_version"],
                "pricing": task["pricing_version"],
            },
            "artifacts": {"campaign_versions": 0 if output is None else output["version"]},
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
    def _progress_view(task, output: dict | None, save: dict | None) -> dict:
        sim_start = datetime.fromisoformat(task["sim_start"])
        sim_now = sim_start + timedelta(milliseconds=task["sim_elapsed_ms"])
        current_output = None
        if output is not None:
            current_output = {
                "output_id": output["output_id"],
                "version": output["version"],
                "status": output["status"],
                "strategy": output["content"].get("strategy", {}).get("name"),
            }
        return {
            "sim_start": task["sim_start"],
            "sim_now": sim_now.isoformat(),
            "sim_elapsed_ms": task["sim_elapsed_ms"],
            "used_rounds": task["used_rounds"],
            "max_rounds": MAX_ROUNDS,
            "paused": bool(task["paused"]),
            "current_output": current_output,
            "save": None
            if save is None
            else {
                "save_id": save["save_id"],
                "output_version": save["output_version"],
                "status": save["status"],
                "verified": save["verified"],
            },
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
