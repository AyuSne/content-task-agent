"""Independent, bounded content review entry.

The evaluator gets its own context and may only read materials already in the
task. It cannot rewrite, save, search new material or expand the task budget.
"""

from __future__ import annotations

import json
from datetime import datetime

from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.knowledge import load_doc
from cta.materials import load_task_catalog
from cta.model import ModelResponse, create_client
from cta.pricing import load_price_table
from cta.prompts import REVIEW_PROMPT_VERSION, REVIEW_SYSTEM_PROMPT, REVIEW_TOOL_DEFINITIONS
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

MAX_REQUESTS = 3
MAX_READBACKS = 2


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


def _validate_report(report: dict, output_version: int) -> tuple[dict, list[str]]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return {}, ["report_review 必须是 JSON 对象"]
    verdict = report.get("verdict")
    completeness = report.get("completeness")
    if verdict not in ("pass", "fail", "incomplete"):
        errors.append("verdict 必须是 pass/fail/incomplete")
    if completeness not in ("full", "partial"):
        errors.append("completeness 必须是 full/partial")
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary 不能为空")
    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append("findings 必须是数组")
        findings = []
    normalized_findings = []
    seen = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"findings[{index}] 必须是对象")
            continue
        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id or finding_id in seen:
            errors.append(f"findings[{index}].finding_id 必须唯一且非空")
            continue
        seen.add(finding_id)
        severity = finding.get("severity")
        if severity not in ("must_fix", "optional", "needs_evidence"):
            errors.append(f"findings[{index}].severity 无效")
            continue
        for field in ("location", "requirement", "evidence", "issue", "suggestion", "reviewed_version"):
            if not isinstance(finding.get(field), str) or not finding.get(field, "").strip():
                errors.append(f"findings[{index}].{field} 不能为空")
                break
        else:
            if str(finding["reviewed_version"]) not in (str(output_version), f"v{output_version}"):
                errors.append(f"findings[{index}].reviewed_version 必须对应被审版本 {output_version}")
                continue
            normalized_findings.append(
                {
                    "finding_id": finding_id,
                    "severity": severity,
                    "location": finding["location"].strip(),
                    "requirement": finding["requirement"].strip(),
                    "evidence": finding["evidence"].strip(),
                    "issue": finding["issue"].strip(),
                    "suggestion": finding["suggestion"].strip(),
                    "reviewed_version": str(output_version),
                }
            )
    gaps = report.get("evidence_gaps", [])
    if not isinstance(gaps, list) or not all(isinstance(item, str) for item in gaps):
        errors.append("evidence_gaps 必须是字符串数组")
        gaps = []
    if verdict == "pass" and any(item["severity"] == "must_fix" for item in normalized_findings):
        errors.append("verdict=pass 时不能包含 must_fix")
    if verdict == "pass" and any(item["severity"] == "needs_evidence" for item in normalized_findings):
        errors.append("verdict=pass 时不能包含 needs_evidence")
    if verdict == "pass" and gaps:
        errors.append("verdict=pass 时 evidence_gaps 必须为空")
    if verdict == "pass" and completeness != "full":
        errors.append("verdict=pass 需要 completeness=full")
    return {
        "verdict": verdict,
        "completeness": completeness,
        "summary": summary.strip() if isinstance(summary, str) else "",
        "findings": normalized_findings,
        "evidence_gaps": gaps,
    }, errors


def _read_material(conn, task_id: str, catalog, material_id) -> dict:
    if not isinstance(material_id, str) or not material_id:
        return {"ok": False, "error": "invalid_arguments", "message": "material_id 必须是非空字符串。"}
    return catalog.read(material_id)


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
        max_requests = int(spec.max_requests or MAX_REQUESTS)
        max_readbacks = int(spec.max_readbacks or MAX_READBACKS)
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
        input_row = conn.execute(
            "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?", (task["id"], task["input_version"])
        ).fetchone()
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
            "program_checks": _program_checks(output),
            "reviewed_version": output["version"],
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

        try:
            client = create_client(config)
        except Exception as exc:
            finish_attempt(conn, attempt_id, status="failed", error=str(exc)[:500])
            conn.commit()
            raise ReviewError(f"模型不可用：{config.redact(str(exc))}", "model_unavailable") from exc

        requests_used = 0
        readbacks_used = 0
        report = None
        errors: list[str] = []
        while requests_used < max_requests:
            requests_used += 1
            started = datetime.now().astimezone()
            try:
                response = client.complete(
                    messages,
                    REVIEW_TOOL_DEFINITIONS,
                    cursor=count_purpose_requests(conn, task["id"], "review"),
                    channel="review",
                )
            except Exception as exc:  # noqa: BLE001 - boundary records the real error
                response = ModelResponse(error=config.redact(f"{type(exc).__name__}: {exc}")[:800])
            finished = datetime.now().astimezone()
            with transaction(conn):
                record_llm_request(
                    conn,
                    task_id=task["id"],
                    purpose="review",
                    response=response,
                    started_at=started,
                    finished_at=finished,
                    mock=response.mock,
                    attempt_id=attempt_id,
                    meta={"prompt_version": REVIEW_PROMPT_VERSION, "request": requests_used, "context": "isolated_review"},
                )
            if response.error:
                errors.append(response.error)
                break
            calls = list(response.tool_calls)
            if response.finish_reason == "length" or len(calls) > 1 or not calls:
                errors.append("核验响应必须是一个完整工具调用")
                assistant_message = {"role": "assistant", "content": response.content or ""}
                if calls:
                    assistant_message["tool_calls"] = [call.as_openai() for call in calls]
                messages.append(assistant_message)
                with transaction(conn):
                    append_message(
                        conn, task["id"], "assistant", response.content,
                        agent="reviewer",
                        tool_calls=[call.as_openai() for call in calls] or None,
                        attempt_id=attempt_id, meta={"kind": "review_request"},
                    )
                    for call in calls:
                        rejection = {"ok": False, "error": "review_protocol", "message": "只允许一个核验动作。"}
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": call.name,
                                "content": json.dumps(rejection, ensure_ascii=False),
                            }
                        )
                        append_message(
                            conn, task["id"], "tool",
                            json.dumps(rejection, ensure_ascii=False),
                            agent="reviewer", tool_call_id=call.id, name=call.name, attempt_id=attempt_id,
                            meta={"kind": "rejection"},
                        )
                if not calls:
                    feedback_text = "[核验反馈] 本轮没有调用工具；请只调用一个 read_material 或 report_review。"
                    messages.append({"role": "user", "content": feedback_text})
                    with transaction(conn):
                        append_message(
                            conn, task["id"], "user", feedback_text,
                            agent="reviewer", attempt_id=attempt_id, meta={"kind": "runtime_feedback"},
                        )
                continue
            call = calls[0]
            try:
                arguments = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                arguments = None
            if not isinstance(arguments, dict):
                arguments = None
            with transaction(conn):
                append_message(
                    conn, task["id"], "assistant", response.content, agent="reviewer",
                    tool_calls=[call.as_openai()], attempt_id=attempt_id, meta={"kind": "review_request"},
                )
            messages.append({"role": "assistant", "content": response.content or "", "tool_calls": [call.as_openai()]})
            if arguments is None:
                tool_result = {"ok": False, "error": "invalid_arguments", "message": "参数必须是 JSON 对象。"}
            elif call.name == "read_material":
                if readbacks_used >= max_readbacks:
                    tool_result = {"ok": False, "error": "readback_limit", "message": "回读次数已用完。"}
                else:
                    readbacks_used += 1
                    tool_result = _read_material(conn, task["id"], catalog, arguments.get("material_id"))
            elif call.name == "report_review":
                normalized, report_errors = _validate_report(arguments, output["version"])
                if report_errors:
                    tool_result = {"ok": False, "error": "invalid_report", "details": report_errors[:8]}
                else:
                    report = normalized
                    tool_result = {"ok": True, "message": "核验结果已接收。"}
            else:
                tool_result = {"ok": False, "error": "unknown_tool", "message": f"未知核验工具：{call.name}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )
            with transaction(conn):
                append_message(
                    conn, task["id"], "tool", json.dumps(tool_result, ensure_ascii=False),
                    agent="reviewer", tool_call_id=call.id, name=call.name, attempt_id=attempt_id,
                    meta={"kind": "review_tool_result"},
                )
            if report is not None:
                break

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

        review_status = "complete" if report["verdict"] in ("pass", "fail") else "incomplete"
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
        if report["verdict"] == "pass":
            reason = "内容核验完成：通过。"
            phase = "reviewed"
        elif report["verdict"] == "fail":
            reason = "内容核验完成：未通过，仍有必须处理的意见。"
            phase = "reviewed"
        else:
            reason = "内容核验未完成，未形成通过结论。"
            phase = "review_incomplete"
        conn.execute(
            "UPDATE tasks SET phase = ?, reason = ?, updated_at = ? WHERE id = ?",
            (phase, reason, utc_now_iso(), task["id"]),
        )
        conn.commit()
        document = TaskService(config).status(task_id)
        document["review"] = latest_review(conn, task_id)
        return document
