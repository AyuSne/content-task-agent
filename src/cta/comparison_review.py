"""Paired reviewer experiment: only the ability to read originals differs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from cta.config import Config
from cta.evaluation import evaluate, review_report_status
from cta.knowledge import load_doc
from cta.materials import load_catalog
from cta.model import create_client
from cta.prompts import REVIEW_SYSTEM_PROMPT


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
            def read_material(material_id, permitted_ids=pair["material_ids"]):
                if material_id not in permitted_ids:
                    return {"ok": False, "error": "not_in_task_materials"}
                return catalog.read(material_id)

            evaluation = evaluate(
                messages, client, read_material, output_version=1,
                max_requests=max_requests, max_readbacks=max_readbacks,
                cursor=cursor, channel="comparison_review", redact=config.redact,
            )
            cursor += evaluation.requests
            for record in evaluation.request_records:
                trace.append({
                    "pair_id": pair["id"], "arm": arm, "messages": record["messages"], "tools": record["tools"],
                    "response": {key: record[key] for key in (
                        "content", "tool_calls", "usage", "model", "error", "elapsed_ms", "finish_reason",
                    )},
                })
            trace.append({"pair_id": pair["id"], "arm": arm, "final_messages": evaluation.messages})
            results.append({
                "pair_id": pair["id"], "arm": arm, "order": len(results) + 1,
                "campaign_sha256": hashlib.sha256(json.dumps(pair["campaign"], sort_keys=True).encode()).hexdigest(),
                "report_status": review_report_status(evaluation.report), "report": evaluation.report,
                "requests": evaluation.requests, "readbacks": evaluation.readbacks, "request_records": evaluation.request_records,
                "errors": evaluation.errors, "expected": pair.get("expected"),
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
