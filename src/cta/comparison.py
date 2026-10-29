"""Reproducible experiments, separate from the operational CLI.

Each arm gets a new task, the same explicit input and the same initial material
dossier. Baselines execute production tools and guards; their controller is
fixed, while all content comes from recorded model requests. Evaluator costs
are reported separately from the content-generation requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from cta.config import Config, load_config
from cta.db import open_store, transaction
from cta.export import export_task
from cta.identity import RawInput
from cta.loop import (
    _ensure_base_context,
    _guard_generation_path,
    _handle_response,
    _stop_incomplete,
    _task,
    run_task,
)
from cta.materials import load_task_catalog
from cta.model import ModelResponse, ToolCall, create_client
from cta.pricing import load_price_table
from cta.prompts import TOOL_DEFINITIONS
from cta.repository import (
    api_messages,
    append_message,
    count_purpose_requests,
    finish_attempt,
    record_llm_request,
    start_attempt,
)
from cta.review import ReviewError, review_task
from cta.save import SaveBlocked, save_task
from cta.service import TaskService
from cta.strategy import load_strategy_table

VERSION = "comparison-2026-10-29.v2"
METHODS = ("oneshot", "fixed", "enhanced")
SEED_IDS = ("M-001", "M-002", "M-003", "M-004", "M-005", "M-006")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _schema(name: str) -> dict:
    return next(item for item in TOOL_DEFINITIONS if item["function"]["name"] == name)


class Experiment:
    def __init__(self, config: Config, task_id: str):
        self.config = config
        self.task_id = task_id
        self.client = create_client(config)
        self.price = load_price_table(config.project_root)
        self.strategies = load_strategy_table(config.project_root)
        self.trace: list[dict] = []

    def action(self, name: str, arguments: dict) -> dict:
        """The fixed controller uses the actual tool dispatcher and ledger."""
        response = ModelResponse(
            tool_calls=(ToolCall(f"fixed_{len(self.trace)}_{name}", name, json.dumps(arguments, ensure_ascii=False)),),
            finish_reason="tool_calls",
        )
        with open_store(self.config.db_path) as conn:
            task = _task(conn, self.task_id)
            catalog = load_task_catalog(conn, task, self.config.project_root)
            _handle_response(conn, task, response, self.price, catalog, self.config, self.client, self.strategies)
            # A successful state-changing tool may append a snapshot after its result.
            results = [m for m in api_messages(conn, self.task_id) if m.get("tool_call_id") == response.tool_calls[0].id]
            result = json.loads(results[-1]["content"])
        self.trace.append({"controller": "fixed", "tool": name, "arguments": arguments, "result": result})
        return result

    def generate(self, name: str, instruction: str, *, charged: bool = False) -> dict:
        """One model request, one expected content submission, no hidden retry."""
        with open_store(self.config.db_path) as conn:
            task = _task(conn, self.task_id)
            attempt_id = None
            if charged:
                guard = _guard_generation_path(conn, task, "generate_direct", self.price)
                if guard:
                    return guard
                spec = self.price.mode("generate_direct")
                attempt_id = start_attempt(
                    conn, task=task, kind="oneshot_campaign", mode="generate_direct",
                    cost_cents=spec.cost_cents, duration_ms=spec.duration_ms,
                    pricing_version=self.price.version, meta={"experiment": VERSION},
                )
                conn.commit()
            with transaction(conn):
                append_message(conn, self.task_id, "user", instruction, meta={"kind": "comparison_instruction"})
            messages = api_messages(conn, self.task_id)
            started = datetime.now(UTC)
            try:
                response = self.client.complete(
                    messages, [_schema(name)], cursor=count_purpose_requests(conn, self.task_id, "comparison"),
                    channel="comparison",
                )
            except Exception as exc:  # noqa: BLE001 - actual external model attempt, retained with unknown usage
                response = ModelResponse(error=self.config.redact(f"{type(exc).__name__}: {exc}"))
            finished = datetime.now(UTC)
            with transaction(conn):
                record_llm_request(
                    conn, task_id=self.task_id, purpose="comparison", response=response,
                    started_at=started, finished_at=finished, mock=response.mock, attempt_id=attempt_id,
                    meta={"experiment": VERSION, "expected_action": name},
                )
            self.trace.append({
                "controller": "model", "messages": messages, "tools": [_schema(name)],
                "started_at": started.isoformat(), "finished_at": finished.isoformat(),
                "response": {"content": response.content, "tool_calls": [c.as_openai() for c in response.tool_calls],
                             "finish_reason": response.finish_reason, "model": response.model,
                             "usage": response.usage, "elapsed_ms": response.elapsed_ms, "error": response.error},
            })
            valid_call = (not response.error and response.finish_reason != "length" and
                          len(response.tool_calls) == 1 and response.tool_calls[0].name == name)
            if not valid_call:
                error = response.error or "模型未返回唯一完整的预定内容提交"
                with transaction(conn):
                    append_message(conn, self.task_id, "assistant", response.content,
                                   tool_calls=[c.as_openai() for c in response.tool_calls] or None)
                    for call in response.tool_calls:
                        append_message(conn, self.task_id, "tool", json.dumps({"ok": False, "error": error}),
                                       tool_call_id=call.id, name=call.name)
                    if attempt_id:
                        finish_attempt(conn, attempt_id, status="failed", error=error)
                return {"ok": False, "error": error}
            catalog = load_task_catalog(conn, task, self.config.project_root)
            _handle_response(conn, task, response, self.price, catalog, self.config, self.client, self.strategies)
            results = [m for m in api_messages(conn, self.task_id) if m.get("tool_call_id") == response.tool_calls[0].id]
            result = json.loads(results[-1]["content"])
            if attempt_id:
                with transaction(conn):
                    finish_attempt(conn, attempt_id, status="success" if result.get("ok") else "failed",
                                   error=None if result.get("ok") else json.dumps(result, ensure_ascii=False))
            return result


def run_experiment(config: Config, case: dict, method: str, output_dir: Path, *, order: int = 1) -> dict:
    """Public experimental seam; outputs contain all attempted arms, including failures."""
    if method not in METHODS:
        raise ValueError(f"unknown comparison method: {method}")
    started = datetime.now(UTC)
    created = TaskService(config).create(RawInput.from_args(**case), explicit_new=True)
    task_id = created["task_id"]
    with open_store(config.db_path) as conn:
        task = _task(conn, task_id)
        catalog = load_task_catalog(conn, task, config.project_root)
        dossier = [catalog.get(mid).as_dict() for mid in SEED_IDS]
        _ensure_base_context(conn, task, config)
        with transaction(conn):
            append_message(conn, task_id, "user", "[对照共同起始资料] " + json.dumps(dossier, ensure_ascii=False),
                           meta={"kind": "comparison_dossier", "version": VERSION})
            if method != "enhanced":
                append_message(conn, task_id, "system",
                               "[对照执行方式] 本任务由已登记固定控制流程推进，只通过当前提供的工具提交所请求内容。"
                               "共同资料已经给定；保持原事实、质量、预算和授权规则，按每次内容请求直接提交完整结果。",
                               meta={"kind": "comparison_controller", "method": method})
    experiment = Experiment(config, task_id)
    error = None
    if created["status"] == "needs_input":
        error = {"ok": False, "error": "needs_input", "validation": created["validation"]}
    elif method == "enhanced":
        run_task(config, task_id)
    else:
        if method == "fixed":
            brief = experiment.generate(
                "propose_brief", "[固定流程实验] 使用共同资料，只调用 propose_brief 提交完整业务简报；不执行其它动作。",
            )
            if not brief.get("ok"):
                error = brief
            if error is None:
                choice = experiment.action("choose_strategy", {
                    "direction": "functional_demo", "search_mode": "search_basic", "generation_mode": "generate_direct",
                    "search_queries": 0, "rationale": "固定流程预登记：共同资料已提供，采用功能演示与一次直接脚本生成。",
                })
                if not choice.get("ok"):
                    error = choice
            if error is None:
                generated = experiment.action("generate_script", {
                    "strategy": "functional_demo", "generation_mode": "generate_direct",
                    "focus": "固定流程：突出自动卡点，保留资料适用范围和制作素材状态。",
                })
                if not generated.get("ok"):
                    error = generated
        if error is None:
            proposed = experiment.generate(
                "propose_campaign",
                "[一次性完整方案提交] 依据共同资料、用户要求和创作知识，仅调用一次 propose_campaign。"
                + ("将本次真实生成的脚本整合为完整方案。" if method == "fixed" else "直接生成完整活动方案及主脚本。")
                + "按工具结构提供全部章节；素材实际位置和台词与证据一一对应；不新增无依据事实。不做重试或返修。",
                charged=method == "oneshot",
            )
            if not proposed.get("ok"):
                error = proposed
        if error is None:
            try:
                reviewed = review_task(config, task_id)
                if method == "fixed":
                    saved = save_task(config, task_id)
                    if saved.get("save_error"):
                        error = {"ok": False, "error": saved["save_error"]["code"],
                                 "message": saved["save_error"]["message"]}
                elif reviewed["review"]["status"] != "complete" or reviewed["review"]["verdict"] != "pass":
                    error = {"ok": False, "error": "assessment_not_passed", "message": reviewed["review"]["summary"]}
            except (ReviewError, SaveBlocked) as exc:
                error = {"ok": False, "error": exc.code, "message": str(exc)}
        if error is not None:
            with open_store(config.db_path) as conn:
                _stop_incomplete(conn, _task(conn, task_id), {
                    "reason": "对照固定控制流程停止：" + json.dumps(error, ensure_ascii=False),
                    "gaps": [{"gap": str(error.get("error")), "impact": "本实验不动态返修或自动重试"}],
                })
    inspected = TaskService(config).inspect(task_id)
    exported = (export_task(config, task_id) if inspected["outputs"]
                else {"task_id": task_id, "verified": False, "error": "no_output"})
    finished = datetime.now(UTC)
    requests = inspected["llm_requests"]
    seed_json = json.dumps(dossier, ensure_ascii=False, sort_keys=True)
    review_requests = [r for r in requests if r["purpose"] == "review"]
    external_assessment_cents = (sum(a["cost_cents"] for a in inspected["attempts"] if a["kind"] == "review_campaign")
                                 if method == "oneshot" else 0)
    result = {
        "version": VERSION, "mode": config.mode, "method": method, "task_id": task_id, "order": order,
        "case": case, "model_config": config.model_config_public(),
        "material_version": catalog.version, "pricing_version": experiment.price.version,
        "knowledge_versions": inspected["knowledge"],
        "seed_material_ids": list(SEED_IDS), "seed_sha256": hashlib.sha256(seed_json.encode()).hexdigest(),
        "environment_faults": {key: os.environ[key] for key in
                               ("CTA_FAIL_SEARCH", "CTA_FAIL_SAVE", "CTA_FAULT_CRASH") if key in os.environ},
        "status": inspected["status"], "error": error, "resources": inspected["resources"],
        "execution_simulated_cost_cents": inspected["resources"]["sim_spent_cents"] - external_assessment_cents,
        "external_assessment": {"requests": len(review_requests) if method == "oneshot" else 0,
                                "simulated_cost_cents": external_assessment_cents,
                                "nature": "external_evaluation" if method == "oneshot" else "none; review belongs to delivery"},
        "progress": inspected["progress"], "reviews": inspected["reviews"], "export": exported,
        "generation_requests": sum(r["purpose"] != "review" for r in requests),
        "assessment_requests": sum(r["purpose"] == "review" for r in requests),
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "wall_seconds": (finished - started).total_seconds(),
        "dynamic_recovery": "not_applicable" if method == "oneshot" else ("fixed_stop" if method == "fixed" else "main_agent"),
        "cache_condition": "service_cache_uncontrolled; report actual per-request hit/miss; no cold-cache claim",
        "limitations": ["一次性方案的独立核验为共同评价开销，未执行业务保存，动态恢复不适用。",
                        "固定流程无条件返修；核验或工具失败即保留未完成。",
                        "小样本仅描述实际观察，不预设增强方案胜出。"],
    }
    _write(output_dir / "inspect.json", inspected)
    _write(output_dir / "request-trace.json", experiment.trace)
    _write(output_dir / "result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实一次性/固定流程/增强方案的可复现对照")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--data-dir")
    parser.add_argument("--method", choices=(*METHODS, "all", "readback"), required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order", type=int, default=1)
    args = parser.parse_args(argv)
    case = json.loads(args.case.read_text(encoding="utf-8"))
    config = load_config(args.mode, args.data_dir)
    if args.method == "readback":
        from cta.comparison_review import run_readback_comparison

        result = run_readback_comparison(config, case, args.output)
    elif args.method == "all":
        results = [run_experiment(config, case, method, args.output / method, order=index)
                   for index, method in enumerate(METHODS, 1)]
        result = {"version": VERSION, "mode": config.mode, "execution_order": list(METHODS),
                  "case": case, "results": results,
                  "seed_materials_match": len({arm["seed_sha256"] for arm in results}) == 1,
                  "cache_condition": "sequential fixed order; no prewarming; actual usage retained for every request"}
        _write(args.output / "suite.json", result)
    else:
        result = run_experiment(config, case, args.method, args.output, order=args.order)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
