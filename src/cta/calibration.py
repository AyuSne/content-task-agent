"""Reviewer calibration harness: six known problem/good pairs, separate from acceptance data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cta.config import Config, find_project_root
from cta.evaluation import Evaluation, evaluate, review_report_status
from cta.knowledge import load_doc
from cta.materials import load_catalog
from cta.model import create_client
from cta.pricing import load_price_table
from cta.prompts import REVIEW_PROMPT_VERSION, REVIEW_SYSTEM_PROMPT

DEFAULT_PAIRS = "2026-10-29.v2"


def _evaluate(config: Config, client, catalog, knowledge: str, pair: dict, campaign: dict, state: dict, limits) -> Evaluation:
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
        "limits": {"max_requests": limits.max_requests, "max_readbacks": limits.max_readbacks},
    }
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "system", "content": "[核验工作知识]\n\n" + knowledge},
        {"role": "user", "content": "[待核验方案] " + json.dumps(payload, ensure_ascii=False)},
    ]
    evaluation = evaluate(
        messages, client, catalog.read, output_version=1,
        max_requests=limits.max_requests, max_readbacks=limits.max_readbacks,
        cursor=state["requests"], channel="review", redact=config.redact,
    )
    state["requests"] += evaluation.requests
    return evaluation


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
    pricing = load_price_table(config.project_root)
    limits = pricing.mode("review")
    client = client or create_client(config)
    state = {"requests": 0}
    results = []
    total_usage = {key: 0 for key in ("requests", "unknown_usage_requests", "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "elapsed_ms")}
    for pair in pairs["pairs"]:
        for variant, key in (("problem", "bad"), ("good", "good")):
            evaluation = _evaluate(config, client, catalog, knowledge, pair, pair[key], state, limits)
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
        "pricing_version": pricing.version,
        "limits": {"max_requests": limits.max_requests, "max_readbacks": limits.max_readbacks},
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
