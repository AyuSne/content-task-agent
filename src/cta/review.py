"""Independent, bounded content review entry.

The evaluator gets its own context and may only read materials already in the
task. It cannot rewrite, save, search new material or expand the task budget.
"""

from __future__ import annotations

import json
from datetime import datetime

from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.evaluation import evaluate, review_report_status
from cta.knowledge import load_doc
from cta.materials import load_task_catalog
from cta.model import create_client
from cta.pricing import load_price_table
from cta.prompts import REVIEW_PROMPT_VERSION, REVIEW_SYSTEM_PROMPT
from cta.repository import (
    append_message,
    count_purpose_requests,
    create_review,
    finish_attempt,
    latest_brief,
    latest_output,
    latest_review,
    record_llm_request,
    start_attempt,
    task_spent,
)
from cta.service import TaskNotFound, TaskService


class ReviewError(Exception):
    def __init__(self, message: str, code: str = "review_blocked"):
        super().__init__(message)
        self.code = code


def _task(conn, task_id: str):
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise TaskNotFound(task_id)
    return task


def _program_checks(output: dict) -> dict:
    campaign = output["content"]
    script = campaign.get("script", {})
    return {
        "required_sections": {
            "strategy": bool(campaign.get("strategy")),
            "activity": bool(campaign.get("activity")),
            "script_segments": len(script.get("segments", [])),
            "materials": len(campaign.get("materials", [])),
            "production_steps": len(campaign.get("production_steps", [])),
            "evidence": len(campaign.get("evidence", [])),
        },
        "blocking_open_questions": sum(
            1 for item in campaign.get("open_questions", []) if item.get("blocking")
        ),
        "output_version": output["version"],
        "output_status": output["status"],
    }


def _guard(conn, task, cost_cents: int, duration_ms: int) -> dict | None:
    input_row = conn.execute(
        "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?", (task["id"], task["input_version"])
    ).fetchone()
    spent = task_spent(conn, task["id"])
    budget = input_row["budget_cents"]
    if budget is not None and spent + cost_cents > budget:
        return {"error": "budget_exceeded", "message": f"预算不足：已发生 {spent} 分，核验需要 {cost_cents} 分。"}
    if input_row["deadline"] is not None:
        deadline = datetime.fromisoformat(input_row["deadline"])
        sim_start = datetime.fromisoformat(task["sim_start"])
        allowed_ms = (deadline - sim_start).total_seconds() * 1000
        if task["sim_elapsed_ms"] + duration_ms > allowed_ms:
            return {"error": "deadline_exceeded", "message": "模拟截止时间不足以完成核验。"}
    return None


def _review_history(conn, task_id: str) -> tuple[list[dict], list[dict]]:
    history = []
    unresolved = []
    for row in conn.execute(
        """
        SELECT reviews.*, outputs.input_version FROM reviews
        JOIN outputs ON outputs.id = reviews.output_id
        WHERE reviews.task_id = ? ORDER BY reviews.rowid
        """,
        (task_id,),
    ):
        dispositions = {
            item["finding_id"]: {"decision": item["decision"], "reason": item["reason"]}
            for item in conn.execute("SELECT * FROM dispositions WHERE review_id = ?", (row["id"],))
        }
        findings = [
            {**finding, "disposition": dispositions.get(finding["finding_id"])}
            for finding in json.loads(row["findings_json"])
        ]
        history.append({
            "review_id": row["id"],
            "output_version": row["output_version"],
            "input_version": row["input_version"],
            "status": row["status"],
            "verdict": row["verdict"],
            "completeness": row["completeness"],
            "summary": row["summary"],
            "findings": findings,
            "evidence_gaps": json.loads(row["evidence_gaps_json"]),
        })
        if row["status"] == "complete" and row["verdict"] == "pass":
            unresolved = []
        unresolved.extend(
            {**finding, "review_id": row["id"], "input_version": row["input_version"]}
            for finding in findings
            if finding["severity"] in ("must_fix", "needs_evidence")
            and (finding["disposition"] is None or finding["disposition"]["decision"] != "rejected")
        )
    return history, unresolved


def review_task(config: Config, task_id: str) -> dict:
    price = load_price_table(config.project_root)
    with open_store(config.db_path) as conn:
        task = _task(conn, task_id)
        output = latest_output(conn, task["id"])
        if output is None:
            raise ReviewError("当前任务还没有可核验的完整方案草稿。", "no_output")
        catalog = load_task_catalog(conn, task, config.project_root)
        existing = latest_review(conn, task["id"])
        if existing is not None and existing["output_id"] == output["output_id"] and existing["status"] == "complete":
            return {**TaskService(config).status(task_id), "duplicate": True, "review": existing}

        spec = price.mode("review")
        max_requests = int(spec.max_requests)
        max_readbacks = int(spec.max_readbacks)
        guard = _guard(conn, task, spec.cost_cents, spec.duration_ms)
        if guard is not None:
            raise ReviewError(guard["message"], guard.get("code", "review_blocked"))
        attempt_id = start_attempt(
            conn,
            task=task,
            kind="review_campaign",
            mode="review",
            cost_cents=spec.cost_cents,
            duration_ms=spec.duration_ms,
            pricing_version=price.version,
            meta={"output_id": output["output_id"], "output_version": output["version"]},
        )
        conn.commit()

        doc = load_doc("content-review", config.project_root)
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_loads (task_id, agent, knowledge_id, version, sha256, content, loaded_at)
            VALUES (?, 'reviewer', ?, ?, ?, ?, ?)
            """,
            (task["id"], doc.knowledge_id, doc.version, doc.sha256, doc.content, utc_now_iso()),
        )
        stored = conn.execute(
            "SELECT * FROM knowledge_loads WHERE task_id = ? AND agent = 'reviewer' AND knowledge_id = ?",
            (task["id"], doc.knowledge_id),
        ).fetchone()
        conn.commit()

        brief = latest_brief(conn, task["id"])
        used_material_ids = {
            item["material_id"]
            for item in output["content"].get("evidence", []) + output["content"].get("materials", [])
            if item.get("material_id")
        }
        if brief is not None:
            used_material_ids.update(
                item["material_id"]
                for item in brief["content"].get("product_facts", []) + brief["content"].get("sources", [])
            )
        input_row = conn.execute(
            "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?", (task["id"], task["input_version"])
        ).fetchone()
        review_history, unresolved_findings = _review_history(conn, task["id"])
        payload = {
            "task_input": {
                "topic": input_row["topic"],
                "audience": input_row["audience"],
                "requirements": json.loads(input_row["requirements_json"]),
                "budget_cents": input_row["budget_cents"],
                "deadline": input_row["deadline"],
            },
            "brief": brief["content"] if brief else None,
            "campaign": output["content"],
            "evidence": [catalog.get(mid).as_dict() for mid in sorted(used_material_ids)],
            "delivery_scope": {
                "deliverable": "campaign_plan",
                "deadline_applies_to": "完成并核实保存活动方案；后续成片制作、音乐采购和发布另列执行排期。",
            },
            "resource_evidence": {
                "budget_nature": "方案准备的模拟工具费用；不是音乐采购、拍摄或投放预算",
                "pricing_version": price.version,
                "modes": [mode.as_dict() for mode in price.modes.values()],
                "spent_cents": task_spent(conn, task["id"]),
                "strategy_estimates": [
                    json.loads(row["detail_json"])
                    for row in conn.execute("SELECT detail_json FROM estimates WHERE task_id = ? ORDER BY rowid", (task["id"],))
                ],
            },
            "program_checks": _program_checks(output),
            "reviewed_version": output["version"],
            "review_history": review_history,
            "unresolved_findings": unresolved_findings,
            "review_scope": {
                "current_input_version": task["input_version"],
                "current_output_input_version": output["input_version"],
                "history_applicability": "历史意见及处置须按当前要求和稿件重新核对；采纳不等于已修复，旧稿通过不直接继承。检查修改及整体关系，保留正确内容；新证据或回归可重新打开旧意见。",
            },
            "limits": {"max_requests": max_requests, "max_readbacks": max_readbacks},
        }
        messages = [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {
                "role": "system",
                "content": f"[核验工作知识] {stored['knowledge_id']} {stored['version']} sha256:{stored['sha256']}\n\n{stored['content']}",
            },
            {"role": "user", "content": "[待核验方案] " + json.dumps(payload, ensure_ascii=False)},
        ]
        with transaction(conn):
            for message in messages:
                append_message(
                    conn, task["id"], message["role"], message["content"], agent="reviewer",
                    attempt_id=attempt_id, meta={"kind": "review_context", "output_version": output["version"]},
                )

        try:
            client = create_client(config)
        except Exception as exc:
            finish_attempt(conn, attempt_id, status="failed", error=str(exc)[:500])
            conn.commit()
            raise ReviewError(f"模型不可用：{config.redact(str(exc))}", "model_unavailable") from exc

        def record_request(response, started, finished, request_number):
            with transaction(conn):
                record_llm_request(
                    conn, task_id=task["id"], purpose="review", response=response,
                    started_at=started, finished_at=finished, mock=response.mock, attempt_id=attempt_id,
                    meta={"prompt_version": REVIEW_PROMPT_VERSION, "request": request_number, "context": "isolated_review"},
                )

        def record_message(message):
            with transaction(conn):
                append_message(
                    conn, task["id"], message["role"], message.get("content"), agent="reviewer",
                    tool_calls=message.get("tool_calls"), tool_call_id=message.get("tool_call_id"),
                    name=message.get("name"), attempt_id=attempt_id,
                    meta={"kind": "review_tool_result" if message["role"] == "tool" else "review_request"},
                )

        evaluation = evaluate(
            messages, client, catalog.read, output_version=output["version"],
            max_requests=max_requests, max_readbacks=max_readbacks,
            cursor=count_purpose_requests(conn, task["id"], "review"), channel="review",
            on_request=record_request, on_message=record_message, redact=config.redact,
        )
        report = evaluation.report
        requests_used, readbacks_used, errors = evaluation.requests, evaluation.readbacks, evaluation.errors

        if report is None:
            create_review(
                conn,
                task_id=task["id"],
                output_id=output["output_id"],
                output_version=output["version"],
                attempt_id=attempt_id,
                status="incomplete",
                verdict=None,
                completeness=None,
                findings=[],
                summary="核验未完成：" + ("; ".join(errors) if errors else "未返回有效结果"),
                evidence_gaps=[],
                requests_used=requests_used,
                readbacks_used=readbacks_used,
            )
            finish_attempt(conn, attempt_id, status="failed", error="review incomplete")
            conn.execute(
                "UPDATE tasks SET phase = 'review_incomplete', reason = ?, updated_at = ? WHERE id = ?",
                ("核验未完成，未形成通过结论。", utc_now_iso(), task["id"]),
            )
            conn.commit()
            document = TaskService(config).status(task_id)
            document["review"] = latest_review(conn, task_id)
            return document

        review_status = review_report_status(report)
        create_review(
            conn,
            task_id=task["id"],
            output_id=output["output_id"],
            output_version=output["version"],
            attempt_id=attempt_id,
            status=review_status,
            verdict=report["verdict"],
            completeness=report["completeness"],
            findings=report["findings"],
            summary=report["summary"],
            evidence_gaps=report["evidence_gaps"],
            requests_used=requests_used,
            readbacks_used=readbacks_used,
        )
        finish_attempt(conn, attempt_id, status="success")
        if review_status == "incomplete":
            reason = "内容核验未完成，未形成通过结论。"
            phase = "review_incomplete"
        elif report["verdict"] == "pass":
            reason = "内容核验完成：通过。"
            phase = "reviewed"
        elif report["verdict"] == "fail":
            reason = "内容核验完成：未通过，仍有必须处理的意见。"
            phase = "reviewed"
        conn.execute(
            "UPDATE tasks SET phase = ?, reason = ?, updated_at = ? WHERE id = ?",
            (phase, reason, utc_now_iso(), task["id"]),
        )
        conn.commit()
        document = TaskService(config).status(task_id)
        document["review"] = latest_review(conn, task_id)
        return document
