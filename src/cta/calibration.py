"""Reviewer calibration harness: six known problem/good pairs, separate from acceptance data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cta.config import Config, find_project_root
from cta.knowledge import load_doc
from cta.materials import load_catalog
from cta.model import ModelResponse, create_client
from cta.prompts import REVIEW_PROMPT_VERSION, REVIEW_SYSTEM_PROMPT, REVIEW_TOOL_DEFINITIONS
from cta.review import MAX_READBACKS, MAX_REQUESTS, _validate_report, review_report_status

DEFAULT_PAIRS = "2026-10-29.v1"


@dataclass
class Evaluation:
    report: dict | None
    requests: int
    readbacks: int
    errors: list[str]
    usage: dict
    messages: list[dict]
    request_records: list[dict]


def _usage_add(total: dict, response: ModelResponse) -> None:
    usage = response.usage or {}
    total["requests"] += 1
    if usage.get("prompt_tokens") is None:
        total["unknown_usage_requests"] += 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "elapsed_ms"):
        value = usage.get(key) if key != "elapsed_ms" else response.elapsed_ms
        if value is not None:
            total[key] = total.get(key, 0) + int(value)


def _evaluate(config: Config, client, catalog, knowledge: str, pair: dict, campaign: dict, state: dict) -> Evaluation:
    payload = {
        "task_input": {
            "topic": "校准样例",
            "audience": "校准",
            "requirements": pair["requirements"],
            "original_requirements": pair["requirements"],
        },
        "brief": None,
        "campaign": campaign,
        "requirements": pair["requirements"],
        "evidence": pair["evidence"],
        "program_checks": {"source": "calibration", "pair": "hidden"},
        "reviewed_version": 1,
        "limits": {"max_requests": MAX_REQUESTS, "max_readbacks": MAX_READBACKS},
    }
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "system", "content": "[核验工作知识]\n\n" + knowledge},
        {"role": "user", "content": "[待核验方案] " + json.dumps(payload, ensure_ascii=False)},
    ]
    readbacks = 0
    errors: list[str] = []
    usage = {
        "requests": 0,
        "unknown_usage_requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "elapsed_ms": 0,
    }
    report = None
    request_records = []
    for _ in range(MAX_REQUESTS):
        try:
            response = client.complete(
                messages,
                REVIEW_TOOL_DEFINITIONS,
                cursor=state["requests"],
                channel="review",
            )
        except Exception as exc:  # noqa: BLE001 - calibration records the failure
            response = ModelResponse(error=config.redact(f"{type(exc).__name__}: {exc}")[:500])
        state["requests"] += 1
        _usage_add(usage, response)
        request_records.append({
            "request": usage["requests"],
            "model": response.model,
            "response_id": response.raw_id,
            "mock": response.mock,
            "usage": response.usage,
            "elapsed_ms": response.elapsed_ms,
            "error": response.error,
            "finish_reason": response.finish_reason,
            "content": response.content,
            "tool_calls": [call.as_openai() for call in response.tool_calls],
        })
        if response.error:
            errors.append(response.error)
            break
        calls = list(response.tool_calls)
        if response.finish_reason == "length" or len(calls) > 1 or not calls:
            assistant = {"role": "assistant", "content": response.content or ""}
            if calls:
                assistant["tool_calls"] = [call.as_openai() for call in calls]
            messages.append(assistant)
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
            if not calls:
                messages.append({"role": "user", "content": "只调用一个 read_material 或 report_review。"})
            errors.append("核验响应必须是一个完整工具调用")
            continue
        call = calls[0]
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            arguments = None
        messages.append({"role": "assistant", "content": response.content or "", "tool_calls": [call.as_openai()]})
        if arguments is None:
            result = {"ok": False, "error": "invalid_arguments", "message": "参数必须是 JSON 对象。"}
        elif call.name == "read_material":
            if readbacks >= MAX_READBACKS:
                result = {"ok": False, "error": "readback_limit", "message": "回读次数已用完。"}
            else:
                readbacks += 1
                result = catalog.read(arguments.get("material_id"))
        elif call.name == "report_review":
            normalized, report_errors = _validate_report(arguments, 1)
            if report_errors:
                result = {"ok": False, "error": "invalid_report", "details": report_errors[:8]}
            else:
                report = normalized
                result = {"ok": True, "message": "核验结果已接收。"}
        else:
            result = {"ok": False, "error": "unknown_tool", "message": f"未知核验工具：{call.name}"}
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )
        if report is not None:
            break
    return Evaluation(
        report=report, requests=usage["requests"], readbacks=readbacks, errors=errors, usage=usage,
        messages=messages, request_records=request_records,
    )


def load_pairs(path: str | Path | None = None, project_root: Path | None = None) -> dict:
    root = project_root or find_project_root()
    selected = Path(path) if path else root / "fixtures" / "calibration" / f"{DEFAULT_PAIRS}.json"
    return json.loads(Path(selected).read_text(encoding="utf-8"))


def _metrics(results: list[dict]) -> dict:
    bad = [item for item in results if item["variant"] == "problem"]
    good = [item for item in results if item["variant"] == "good"]
    evaluable_bad = [item for item in bad if item["report_status"] == "complete"]
    evaluable_good = [item for item in good if item["report_status"] == "complete"]
    misses = [item["pair_id"] for item in evaluable_bad if not item["detected"]]
    false_positives = [item["pair_id"] for item in evaluable_good if item["must_fix"]]
    return {
        "problem_samples": len(bad),
        "good_samples": len(good),
        "evaluable_problem_samples": len(evaluable_bad),
        "evaluable_good_samples": len(evaluable_good),
        "bad_detected": sum(1 for item in evaluable_bad if item["detected"]),
        "missed": misses,
        "false_positives": false_positives,
        "optional_on_good": sum(item["optional"] for item in evaluable_good),
        "incomplete_reviews": sum(1 for item in results if item["report_status"] != "complete"),
    }


def run_calibration(
    config: Config,
    *,
    pairs_path: str | Path | None = None,
    output_path: str | Path | None = None,
    client=None,
) -> dict:
    started = datetime.now(UTC)
    pairs = load_pairs(pairs_path, config.project_root)
    catalog = load_catalog(config.project_root)
    knowledge_doc = load_doc("content-review", config.project_root)
    knowledge = knowledge_doc.content
    client = client or create_client(config)
    state = {"requests": 0}
    results = []
    total_usage = {key: 0 for key in ("requests", "unknown_usage_requests", "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "elapsed_ms")}
    for pair in pairs["pairs"]:
        for variant, key in (("problem", "bad"), ("good", "good")):
            evaluation = _evaluate(config, client, catalog, knowledge, pair, pair[key], state)
            report = evaluation.report
            findings = [] if report is None else report["findings"]
            must_fix = sum(1 for item in findings if item["severity"] == "must_fix")
            detected = any(item["severity"] in ("must_fix", "needs_evidence") for item in findings)
            results.append(
                {
                    "pair_id": pair["id"],
                    "issue": pair["issue"],
                    "variant": variant,
                    "report_status": review_report_status(report),
                    "verdict": None if report is None else report["verdict"],
                    "completeness": None if report is None else report["completeness"],
                    "summary": None if report is None else report["summary"],
                    "evidence_gaps": [] if report is None else report["evidence_gaps"],
                    "findings": findings,
                    "must_fix": must_fix,
                    "optional": sum(1 for item in findings if item["severity"] == "optional"),
                    "detected": detected,
                    "requests": evaluation.requests,
                    "readbacks": evaluation.readbacks,
                    "errors": evaluation.errors,
                    "usage": evaluation.usage,
                    "messages": evaluation.messages,
                    "request_records": evaluation.request_records,
                }
            )
            for key, value in evaluation.usage.items():
                total_usage[key] += value
    metrics = _metrics(results)
    finished = datetime.now(UTC)
    result = {
        "calibration_version": pairs["version"],
        "knowledge_version": f"{knowledge_doc.knowledge_id} {knowledge_doc.version}",
        "prompt_version": REVIEW_PROMPT_VERSION,
        "model_config": config.model_config_public(),
        "mode": config.mode,
        "simulated_cost": "not_applicable: 校准不创建内容任务，不进入任务模拟预算",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "wall_seconds": round((finished - started).total_seconds(), 3),
        "pairs_count": len(pairs["pairs"]),
        "metrics": metrics,
        "usage": total_usage,
        "results": results,
        "limitations": [
            "校准只测评估器对已知问题/合格稿的判断，不证明生产质量或统计显著。",
            "每一对样例的标签来自原材料与明确要求；主观表达评价者记录在 pairs 文件中。",
            "校准集与工作知识样例、正式验收集分开；评估器看不到标签。",
        ],
    }
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["output_path"] = str(path)
    return result
