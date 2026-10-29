"""One bounded evaluation protocol shared by production and experiments.

The caller supplies already prepared messages, actual read permissions and
persistent recording callbacks. This module owns response validation, legal
message pairing, finite request/read counts and report completion semantics.
Internal callback errors propagate; only external model request errors become
recorded incomplete attempts.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from cta.model import ModelClient, ModelResponse
from cta.prompts import REVIEW_TOOL_DEFINITIONS

MAX_REQUESTS = 3
MAX_READBACKS = 2


@dataclass
class Evaluation:
    report: dict | None
    requests: int
    readbacks: int
    errors: list[str]
    messages: list[dict]
    request_records: list[dict]

    @property
    def usage(self) -> dict:
        totals = {key: 0 for key in (
            "requests", "unknown_usage_requests", "prompt_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "elapsed_ms",
        )}
        for record in self.request_records:
            totals["requests"] += 1
            usage = record["usage"] or {}
            if usage.get("prompt_tokens") is None:
                totals["unknown_usage_requests"] += 1
            for key in totals.keys() - {"requests", "unknown_usage_requests"}:
                value = record["elapsed_ms"] if key == "elapsed_ms" else usage.get(key)
                if value is not None:
                    totals[key] += int(value)
        return totals


def review_report_status(report: dict | None) -> str:
    """A received report is complete only when the whole review reached a verdict."""
    if report is not None and report["verdict"] in ("pass", "fail") and report["completeness"] == "full":
        return "complete"
    return "incomplete"


def validate_report(report: dict, output_version: int) -> tuple[dict, list[str]]:
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


def evaluate(
    messages: list[dict],
    client: ModelClient,
    read_material: Callable[[str], dict],
    *,
    output_version: int,
    max_requests: int = MAX_REQUESTS,
    max_readbacks: int = MAX_READBACKS,
    cursor: int = 0,
    channel: str = "review",
    on_request: Callable[[ModelResponse, datetime, datetime, int], None] | None = None,
    on_message: Callable[[dict], None] | None = None,
    redact: Callable[[str], str] = str,
    protocol_corrections: bool = True,
) -> Evaluation:
    """Run finite read/report actions, recording every actual external attempt.

    ``read_material`` enforces the caller's real material permissions. Request
    and message callbacks run before subsequent actions; their errors propagate
    so an internal persistence bug cannot masquerade as a model rejection.
    With protocol corrections disabled, only a successful original read allows
    another request; all refused actions terminate the evaluation equally.
    """
    history = list(messages)
    tools = [tool for tool in REVIEW_TOOL_DEFINITIONS
             if max_readbacks > 0 or tool["function"]["name"] == "report_review"]
    report = None
    readbacks = 0
    errors, records = [], []

    def append(message):
        history.append(message)
        if on_message is not None:
            on_message(message)

    for index in range(max_requests):
        started = datetime.now(UTC)
        try:
            response = client.complete(history, tools, cursor=cursor + index, channel=channel)
        except Exception as exc:  # noqa: BLE001 - only the external model interaction is converted
            response = ModelResponse(error=redact(f"{type(exc).__name__}: {exc}")[:800])
        finished = datetime.now(UTC)
        record = {
            "request": index + 1, "started_at": started.isoformat(), "finished_at": finished.isoformat(),
            "messages": list(history), "tools": tools,
            "model": response.model, "response_id": response.raw_id, "mock": response.mock,
            "usage": response.usage, "elapsed_ms": response.elapsed_ms, "error": response.error,
            "request_config": response.request_config,
            "finish_reason": response.finish_reason, "content": response.content,
            "tool_calls": [call.as_openai() for call in response.tool_calls],
        }
        records.append(record)
        if on_request is not None:
            on_request(response, started, finished, index + 1)
        if response.error:
            errors.append(response.error)
            break

        calls = list(response.tool_calls)
        append({"role": "assistant", "content": response.content or "",
                **({"tool_calls": [call.as_openai() for call in calls]} if calls else {})})
        if response.finish_reason == "length" or len(calls) != 1:
            errors.append("核验响应必须是一个完整工具调用")
            for call in calls:
                append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                        "content": json.dumps({"ok": False, "error": "review_protocol",
                                               "message": "只允许一个完整核验动作，本轮全部拒绝。"}, ensure_ascii=False)})
            if not calls:
                append({"role": "user", "content": "[核验反馈] 请只调用一个完整的 read_material 或 report_review。"})
            if not protocol_corrections:
                break
            continue

        call = calls[0]
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            result = {"ok": False, "error": "invalid_arguments", "message": "参数必须是 JSON 对象。"}
        elif call.name == "read_material":
            material_id = arguments.get("material_id")
            if not isinstance(material_id, str) or not material_id.strip() or set(arguments) != {"material_id"}:
                result = {"ok": False, "error": "invalid_arguments", "message": "只接受非空字符串 material_id。"}
            elif readbacks >= max_readbacks:
                result = {"ok": False, "error": "readback_limit", "message": "回读次数已用完。"}
            else:
                readbacks += 1
                result = read_material(material_id)
        elif call.name == "report_review":
            normalized, report_errors = validate_report(arguments, output_version)
            if report_errors:
                result = {"ok": False, "error": "invalid_report", "details": report_errors[:8]}
            else:
                report = normalized
                result = {"ok": True, "message": "核验结果已接收。"}
        else:
            result = {"ok": False, "error": "unknown_tool", "message": f"未知核验工具：{call.name}"}
        append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                "content": json.dumps(result, ensure_ascii=False)})
        if report is not None:
            break
        if not protocol_corrections and not (call.name == "read_material" and result.get("ok")):
            errors.append(result["error"])
            break
    if report is None and not errors:
        errors.append("核验请求次数耗尽，未形成完整有效报告。")
    return Evaluation(report, len(records), readbacks, errors, history, records)

