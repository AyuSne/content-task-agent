"""Paired reviewer experiment: only the ability to read originals differs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from cta.config import Config
from cta.knowledge import load_doc
from cta.materials import load_catalog
from cta.model import ModelResponse, create_client
from cta.prompts import REVIEW_SYSTEM_PROMPT, REVIEW_TOOL_DEFINITIONS
from cta.review import _validate_report, review_report_status


def run_readback_comparison(config: Config, case: dict, output_dir: Path) -> dict:
    """Run each held-out candidate with and without bounded read-only access.

    Correct labels are retained in results, but never enter a model request.
    Pair order rotates to avoid always giving one arm the earlier cache state.
    """
    started = datetime.now(UTC)
    client = create_client(config)
    catalog = load_catalog(config.project_root)
    knowledge = load_doc("content-review", config.project_root)
    cursor = 0
    results, trace = [], []
    for pair_index, pair in enumerate(case["pairs"]):
        arms = ("single", "bounded") if pair_index % 2 == 0 else ("bounded", "single")
        for arm in arms:
            max_requests, max_readbacks = (1, 0) if arm == "single" else (3, 2)
            tools = [t for t in REVIEW_TOOL_DEFINITIONS
                     if arm == "bounded" or t["function"]["name"] == "report_review"]
            payload = {
                "task_input": {"requirements": pair["requirements"]},
                "campaign": pair["campaign"], "evidence": pair["evidence"],
                "reviewed_version": 1,
                "limits": {"max_requests": max_requests, "max_readbacks": max_readbacks},
            }
            messages = [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "system", "content": "[核验工作知识]\n" + knowledge.content},
                {"role": "user", "content": "[待核验方案] " + json.dumps(payload, ensure_ascii=False)},
            ]
            report = None
            readbacks, request_count = 0, 0
            errors, requests = [], []
            for _ in range(max_requests):
                request_count += 1
                request_started = datetime.now(UTC)
                try:
                    response = client.complete(messages, tools, cursor=cursor, channel="comparison_review")
                except Exception as exc:  # noqa: BLE001 - preserve the actual external request attempt
                    response = ModelResponse(error=config.redact(f"{type(exc).__name__}: {exc}"))
                cursor += 1
                request_finished = datetime.now(UTC)
                requests.append({"started_at": request_started.isoformat(), "finished_at": request_finished.isoformat(),
                                 "usage": response.usage, "elapsed_ms": response.elapsed_ms, "model": response.model,
                                 "error": response.error, "mock": response.mock})
                trace.append({"pair_id": pair["id"], "arm": arm, "messages": list(messages), "tools": tools,
                              "response": {"content": response.content, "tool_calls": [c.as_openai() for c in response.tool_calls],
                                           "usage": response.usage, "model": response.model, "error": response.error,
                                           "elapsed_ms": response.elapsed_ms, "finish_reason": response.finish_reason}})
                if response.error:
                    errors.append(response.error)
                    break
                calls = list(response.tool_calls)
                messages.append({"role": "assistant", "content": response.content or "",
                                 **({"tool_calls": [c.as_openai() for c in calls]} if calls else {})})
                if response.finish_reason == "length" or len(calls) != 1:
                    result = {"ok": False, "error": "review_protocol"}
                    for call in calls:
                        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                         "content": json.dumps(result)})
                    if not calls:
                        messages.append({"role": "user", "content": "请调用一个完整核验动作。"})
                    errors.append("review_protocol")
                    continue
                call = calls[0]
                try:
                    arguments = json.loads(call.arguments)
                except json.JSONDecodeError:
                    arguments = None
                if not isinstance(arguments, dict):
                    result = {"ok": False, "error": "invalid_arguments"}
                elif call.name == "read_material":
                    material_id = arguments.get("material_id")
                    if readbacks >= max_readbacks:
                        result = {"ok": False, "error": "readback_limit"}
                    elif material_id not in pair["material_ids"]:
                        readbacks += 1
                        result = {"ok": False, "error": "not_in_task_materials"}
                    else:
                        readbacks += 1
                        result = catalog.read(material_id)
                elif call.name == "report_review":
                    normalized, report_errors = _validate_report(arguments, 1)
                    if report_errors:
                        result = {"ok": False, "error": "invalid_report", "details": report_errors}
                    else:
                        report = normalized
                        result = {"ok": True}
                else:
                    result = {"ok": False, "error": "unknown_tool"}
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                 "content": json.dumps(result, ensure_ascii=False)})
                if report is not None:
                    break
            # Retain final results too, including read replies on the last request.
            trace.append({"pair_id": pair["id"], "arm": arm, "final_messages": messages})
            results.append({
                "pair_id": pair["id"], "arm": arm, "order": len(results) + 1,
                "campaign_sha256": hashlib.sha256(json.dumps(pair["campaign"], sort_keys=True).encode()).hexdigest(),
                "report_status": review_report_status(report), "report": report,
                "requests": request_count, "readbacks": readbacks, "request_records": requests,
                "errors": errors, "expected": pair.get("expected"),
            })
    finished = datetime.now(UTC)
    result = {"version": case["version"], "mode": config.mode, "model_config": config.model_config_public(),
              "material_version": catalog.version, "knowledge_version": knowledge.version,
              "started_at": started.isoformat(), "finished_at": finished.isoformat(),
              "wall_seconds": (finished - started).total_seconds(), "results": results,
              "simulated_cost": "not_applicable: reviewer-only experiment outside content-task budgets",
              "cache_condition": "paired sequential, arm order rotates; service cache is uncontrolled",
              "limitations": ["同一稿件和片段只改变回读权限及随之变化的有限请求上限。",
                              "读回实验不执行改稿或保存；交付影响由发现的问题及额外请求描述。"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (("result.json", result), ("request-trace.json", trace)):
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
