"""Task intake and query use cases exposed by the CLI."""

from __future__ import annotations

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
    latest_brief,
    latest_estimate,
    latest_output,
    latest_review,
    latest_save,
    latest_strategy_selection,
    list_estimates,
    list_stops,
    list_strategy_selections,
    task_llm_usage,
    task_spent,
)
from cta.strategy import ResourceState, compare_strategies, load_strategy_table

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
                                   materials_version, pricing_version, strategy_version)
                VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, 0, ?, 0, 0, ?, ?, ?)
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
                    load_strategy_table(self.config.project_root).version,
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
            view["estimates"] = list_estimates(conn, task_id)
            view["strategy_selections"] = list_strategy_selections(conn, task_id)
            view["stops"] = list_stops(conn, task_id)
            view["strategy"] = self._strategy_view(conn, task)
            view["reviewer_messages"] = [
                {
                    "sequence": row["seq"],
                    "role": row["role"],
                    "content": row["content"],
                    "tool_calls": json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else [],
                    "name": row["name"],
                }
                for row in conn.execute(
                    "SELECT * FROM messages WHERE task_id = ? AND agent = 'reviewer' ORDER BY seq", (task_id,)
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
        strategy = self._strategy_view(conn, task)
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
            "strategy": strategy,
            "plan": self._plan_view(task, output, review, save),
            "gaps": self._gaps(conn, task, brief),
            "mode_notice": (
                "Mock 仅验证流程、状态与确定性规则，不代表真实模型决策或语义质量。"
                if task["mode"] == "mock"
                else "真实模型运行；小样本观察不代表生产质量或统计显著结论。"
            ),
            "progress": self._progress_view(task, output, save),
            "resources": self._resources_view(conn, task, input_row, spent),
            "model_config": self.config.model_config_public(),
            "versions": {
                "app": __version__,
                "prompt": PROMPT_VERSION,
                "materials": task["materials_version"],
                "pricing": task["pricing_version"],
                "strategy": strategy["table_version"],
            },
            "artifacts": {"campaign_versions": 0 if output is None else output["version"]},
        }

    def _strategy_view(self, conn, task) -> dict:
        table = load_strategy_table(self.config.project_root)
        selections = list_strategy_selections(conn, task["id"])
        return {
            "table_version": task["strategy_version"] or table.version,
            "directions": [spec.as_dict() for spec in table.directions.values()],
            "latest_selection": selections[-1] if selections else None,
            "selection_count": len(selections),
            "mode_table_version": task["pricing_version"],
        }

    def strategy_comparison(
        self,
        task_id: str,
        *,
        search_mode: str = "search_basic",
        generation_mode: str = "generate_direct",
        search_queries: int | None = None,
        include_revision: bool = False,
        directions: list[str] | None = None,
    ) -> dict:
        """Read-only strategy comparison for the operator view; never executes or charges."""
        price = load_price_table(self.config.project_root)
        table = load_strategy_table(self.config.project_root)
        with open_store(self.config.db_path) as conn:
            task = self._require_task(conn, task_id)
            input_row = conn.execute(
                "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?",
                (task["id"], task["input_version"]),
            ).fetchone()
            deadline = None if input_row["deadline"] is None else datetime.fromisoformat(input_row["deadline"])
            state = ResourceState(
                incurred_cents=task_spent(conn, task["id"]),
                budget_cents=input_row["budget_cents"],
                sim_start=datetime.fromisoformat(task["sim_start"]),
                sim_elapsed_ms=task["sim_elapsed_ms"],
                deadline=deadline,
            )
            return compare_strategies(
                table,
                price,
                state,
                directions=directions,
                search_mode=search_mode,
                generation_mode=generation_mode,
                search_queries=search_queries,
                include_revision=include_revision,
            )

    @staticmethod
    def _plan_view(task, output: dict | None, review: dict | None, save: dict | None) -> dict:
        brief_done = int(task["brief_version"]) > 0
        phase = task["phase"]
        draft_done = output is not None
        review_done = review is not None and review["status"] == "complete"
        save_done = save is not None and save["verified"]
        return {
            "version": "plan-2026-10-29.v1",
            "steps": [
                {"step": "brief", "title": "业务简报", "status": "done" if brief_done else "current"},
                {
                    "step": "campaign_draft",
                    "title": "完整活动方案草稿",
                    "status": "done" if draft_done else ("current" if phase == "brief_ready" else "pending"),
                },
                {
                    "step": "review",
                    "title": "内容核验",
                    "status": "done" if review_done else ("current" if phase in ("draft_ready", "review_incomplete") else "pending"),
                },
                {
                    "step": "save",
                    "title": "核实保存与导出",
                    "status": "done" if save_done else ("current" if phase in ("reviewed", "save_failed") else "pending"),
                },
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
        for stop in list_stops(conn, task["id"]):
            for item in stop["gaps"]:
                gaps.append(
                    {
                        "source": "stop",
                        "stop_kind": stop["kind"],
                        "question": item.get("gap", ""),
                        "impact": item.get("impact", ""),
                        "blocking": True,
                    }
                )
            if not stop["gaps"]:
                gaps.append(
                    {
                        "source": "stop",
                        "stop_kind": stop["kind"],
                        "question": stop["reason"],
                        "impact": "",
                        "blocking": True,
                    }
                )
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
    def _compact_estimate(row: dict | None) -> dict | None:
        if row is None:
            return None
        return {
            "estimate_id": row["estimate_id"],
            "kind": row["kind"],
            "direction": row["direction"],
            "search_mode": row["search_mode"],
            "generation_mode": row["generation_mode"],
            "search_queries": row["search_queries"],
            "include_revision": row["include_revision"],
            "total_cost_cents": row["total_cost_cents"],
            "total_duration_ms": row["total_duration_ms"],
            "incurred_cents": row["incurred_cents"],
            "remaining_cents": row["remaining_cents"],
            "feasible": row["feasible"],
            "strategy_version": row["strategy_version"],
            "pricing_version": row["pricing_version"],
            "created_at": row["created_at"],
            "nature": "estimated",
        }

    @staticmethod
    def _compact_selection(row: dict | None) -> dict | None:
        if row is None:
            return None
        return {
            "selection_id": row["selection_id"],
            "direction": row["direction"],
            "search_mode": row["search_mode"],
            "generation_mode": row["generation_mode"],
            "search_queries": row["search_queries"],
            "include_revision": row["include_revision"],
            "improvement": row["improvement"],
            "expected_benefit": row["expected_benefit"],
            "rationale": row["rationale"],
            "total_cost_cents": row["total_cost_cents"],
            "total_duration_ms": row["total_duration_ms"],
            "incurred_cents": row["incurred_cents"],
            "remaining_cents": row["remaining_cents"],
            "strategy_version": row["strategy_version"],
            "pricing_version": row["pricing_version"],
            "created_at": row["created_at"],
            "nature": "reserved",
        }

    @staticmethod
    def _activity_costs(conn, task) -> dict:
        output = latest_output(conn, task["id"])
        entries = []
        if output is not None:
            for item in output["content"].get("materials", []):
                status = item.get("status")
                if status in ("to_acquire", "to_create"):
                    entries.append(
                        {
                            "name": item.get("name"),
                            "material_id": item.get("material_id") or None,
                            "status": status,
                            "amount_cents": None,
                            "nature": "unknown",
                            "source": "campaign.materials",
                        }
                    )
        if entries:
            return {
                "category": "activity_cost",
                "unit": "CNY_cent",
                "source": "campaign.materials",
                "status": "unknown",
                "nature": "unknown",
                "entries": entries,
                "reason": "方案含待获取或待制作素材，但任务输入与材料未给出活动预算或已发生支出金额。",
            }
        return {
            "category": "activity_cost",
            "unit": "CNY_cent",
            "source": "task_input/campaign.materials",
            "status": "not_applicable",
            "nature": "not_applicable",
            "entries": [],
            "reason": "任务输入、资料与执行未涉及活动制作或投放预算/实际支出。",
        }

    def _resources_view(self, conn, task, input_row, spent: int) -> dict:
        task_id = task["id"]
        budget_cents = input_row["budget_cents"]
        usage = task_llm_usage(conn, task_id)
        estimate = self._compact_estimate(latest_estimate(conn, task_id))
        reserved = self._compact_selection(latest_strategy_selection(conn, task_id))
        ledger_count = conn.execute(
            "SELECT COUNT(*) AS n FROM ledger WHERE task_id = ? AND nature = 'incurred'", (task_id,)
        ).fetchone()
        activity = self._activity_costs(conn, task)
        unknown = ["real_model_usage.amount"]
        not_applicable: list[str] = []
        if activity["status"] == "unknown":
            unknown.append("activity_costs")
        else:
            not_applicable.append("activity_costs")
        return {
            "budget_cents": budget_cents,
            "sim_spent_cents": spent,
            "remaining_cents": None if budget_cents is None else budget_cents - spent,
            "real_requests": usage["real_requests"],
            "mock_requests": usage["mock_requests"],
            "model_requests": usage["requests"],
            "real_usage": usage,
            "real_cost": None,
            "amount_status": {
                "simulated_tool_fee": "incurred_and_estimated",
                "real_model_usage": "unknown",
                "activity_costs": activity["status"],
            },
            "simulated_tool_fee": {
                "category": "simulated_tool_fee",
                "unit": "CNY_cent",
                "source": f"pricing:{task['pricing_version']}",
                "nature": "incurred",
                "incurred_cents": spent,
                "ledger_entries": int(ledger_count["n"]),
                "estimated": estimate,
                "reserved": reserved,
                "note": "预留与估算是可行性判断，不是实际支出；实际消耗只累计账本中 nature=incurred 的记录。",
            },
            "real_model_usage": {
                "category": "real_model_usage",
                "unit": "request/token",
                "source": "DeepSeek API response usage（openai SDK）",
                "nature": "actual_usage",
                "requests": usage["requests"],
                "real_requests": usage["real_requests"],
                "mock_requests": usage["mock_requests"],
                "unknown_usage_requests": usage["unknown_usage_requests"],
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "cache_hit_tokens": usage["cache_hit_tokens"],
                "cache_miss_tokens": usage["cache_miss_tokens"],
                "elapsed_ms": usage["elapsed_ms"],
                "amount": {
                    "status": "unknown",
                    "value_cents": None,
                    "reason": "没有可靠的单价来源，不把 token 用量换算成金额；未发生真实请求时也不补成已核实零费用。",
                },
            },
            "activity_costs": activity,
            "unknown": unknown,
            "not_applicable": not_applicable,
        }
