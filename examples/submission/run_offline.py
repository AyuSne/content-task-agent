"""Run the current protocol through the public CLI with a scripted model."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("offline-scenario.json")


def write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def response(name: str, arguments: dict, content: str, call_id: str) -> dict:
    return {"name": name, "arguments": arguments, "content": content, "id": call_id}


def run_cli(data: Path, script: Path, *arguments: str, fail_search: bool = False) -> dict:
    environment = dict(os.environ)
    for key in ("CTA_FAULT_CRASH", "CTA_FAIL_SEARCH", "CTA_DATA_DIR", "CTA_STRATEGY_VERSION"):
        environment.pop(key, None)
    environment.update({"CTA_MODE": "mock", "CTA_MODEL_SCRIPT": str(script),
                        "CTA_SIM_START": "2026-10-29T00:00:00+08:00", "CTA_MAX_ROUNDS": "24",
                        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if fail_search:
        environment["CTA_FAIL_SEARCH"] = "1"
    command = [sys.executable, "-m", "cta", "--mode", "mock", "--data-dir", str(data), *arguments]
    completed = subprocess.run(command, check=False, cwd=ROOT, env=environment,
                               capture_output=True, text=True, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"CLI failed ({completed.returncode}): {completed.stdout}\n{completed.stderr}")
    return json.loads(completed.stdout)


def create_arguments(inputs: dict) -> list[str]:
    arguments = ["create"]
    for name in ("topic", "budget", "deadline", "audience"):
        if inputs.get(name) is not None:
            arguments.extend(["--" + name, str(inputs[name])])
    for requirement in inputs.get("requirements", []):
        arguments.extend(["--requirement", requirement])
    return arguments


def finish_case(directory: Path, data: Path, script: Path, task_id: str, result: dict) -> dict:
    inspect = run_cli(data, script, "inspect", task_id)
    exported = run_cli(data, script, "export", task_id)
    assert inspect["resources"]["real_requests"] == 0
    write_json(directory / "result.json", result)
    write_json(directory / "inspect.json", inspect)
    write_json(directory / "export.json", exported)
    for source in (data / "mock" / "exports" / task_id).iterdir():
        if source.is_file():
            (directory / source.name).write_bytes(source.read_bytes())
    return {"case": directory.name, "task_id": task_id, "status": result["status"],
            "sim_spent_cents": inspect["resources"]["sim_spent_cents"],
            "real_requests": 0, "mock_requests": inspect["resources"]["mock_requests"]}


def delivery_case(root: Path, fixture: dict, *, fail_search: bool) -> dict:
    directory = root / ("tool-failure-recovery" if fail_search else "normal")
    data, script = directory / "data", directory / "model-script.json"
    inputs = copy.deepcopy(fixture["input"])
    if fail_search:
        inputs["budget"] = "4.00"
    write_json(directory / "input.json", inputs)
    write_json(script, [])
    created = run_cli(data, script, *create_arguments(inputs))
    write_json(directory / "create.json", created)
    task_id = created["task_id"]
    prefix = []
    if fail_search:
        prefix.append(response("search_materials", {"query": "自动卡点", "mode": "extended"},
                               "先查证自动卡点与微调的产品事实。", "demo_failed_search"))
        write_json(script, prefix)
        failed = run_cli(data, script, "run", task_id, "--max-rounds", "1", fail_search=True)
        write_json(directory / "injected-failure.json", failed)
    prefix.extend([
        response("search_materials", {"query": "自动卡点 微调 品牌 素材", "mode": "extended"},
                 "根据实际检索结果组织方案。", "demo_search"),
        response("update_plan", {"steps": fixture["plan"]}, "记录查证、创作、核验和保存步骤。", "demo_plan"),
        response("estimate_cost", {"actions": ["search_extended", "submit_script", "revision", "review", "save"]},
                 "先估算包含额外检索和返修的路径。", "demo_high_estimate"),
        response("estimate_cost", {"actions": ["submit_script", "review", "save"]},
                 "采用现有证据完成脚本、核验和保存，重新估算。", "demo_feasible_estimate"),
        response("submit_script", {"brief": fixture["brief"], "script": fixture["script"]},
                 "提交完整脚本，并一并提交业务简报。", "demo_script"),
    ])
    write_json(script, prefix)
    run_cli(data, script, "resume" if fail_search else "run", task_id, "--max-rounds", "5")
    inspected = run_cli(data, script, "inspect", task_id)
    script_view = inspected["scripts"][-1]
    script_ref = {"id": script_view["output_id"], "version": script_view["version"]}
    prefix.append(response("submit_campaign", {"script_ref": script_ref, "campaign": fixture["campaign"]},
                           "引用已接收的脚本，提交其余活动方案。", "demo_campaign"))
    write_json(script, prefix)
    run_cli(data, script, "resume", task_id, "--max-rounds", "1")
    inspected = run_cli(data, script, "inspect", task_id)
    campaign_ref = next(json.loads(message["content"])["campaign_ref"]
                        for message in reversed(inspected["messages"])
                        if message["role"] == "tool" and message["name"] == "submit_campaign")
    prefix.extend([
        response("review_campaign", {"campaign_ref": campaign_ref}, "核验当前完整方案。", "demo_review"),
        response("save_campaign", {"campaign_ref": campaign_ref}, "核验完成，保存并核实文件。", "demo_save"),
        {"channel": "review", "name": "report_review", "arguments": fixture["mock_review"]},
    ])
    write_json(script, prefix)
    result = run_cli(data, script, "resume", task_id)
    assert result["status"] == "complete" and result["save"]["verified"]
    before = (result["resources"]["sim_spent_cents"], result["resources"]["model_requests"])
    duplicate = run_cli(data, script, *create_arguments(inputs))
    repeated = run_cli(data, script, "run", task_id)
    assert duplicate["task_id"] == task_id
    assert before == (repeated["resources"]["sim_spent_cents"], repeated["resources"]["model_requests"])
    write_json(directory / "repeat.json", {"same_task_id": True, "before": before,
                                           "after": [repeated["resources"]["sim_spent_cents"],
                                                     repeated["resources"]["model_requests"]]})
    inspected = run_cli(data, script, "inspect", task_id)
    estimates = [json.loads(message["content"])["estimate"] for message in inspected["messages"]
                 if message["role"] == "tool" and message["name"] == "estimate_cost"]
    assert estimates[0]["affordable"] is False and estimates[1]["affordable"] is True
    return finish_case(directory, data, script, task_id, result)


def stopped_case(root: Path, fixture: dict, name: str) -> dict:
    directory = root / name
    data, script = directory / "data", directory / "model-script.json"
    inputs = copy.deepcopy(fixture["input"])
    if name == "missing-budget":
        inputs.pop("budget")
    elif name == "missing-deadline":
        inputs.pop("deadline")
    elif name == "budget-stop":
        inputs["budget"] = "0.10"
    elif name == "deadline-stop":
        inputs["deadline"] = "2026-10-29T00:00:01+08:00"
    write_json(directory / "input.json", inputs)
    write_json(script, [
        response("estimate_cost", {"actions": ["submit_script", "review", "save"]},
                 "核对剩余动作是否满足预算与截止。", "stop_estimate"),
        response("stop_incomplete", {"reason": "现有资源无法覆盖脚本、核验和保存。",
                                     "gaps": [{"gap": "资源不足", "impact": "无法完成交付"}]},
                 "保留实际记录，说明未完成原因。", "stop_result"),
    ])
    created = run_cli(data, script, *create_arguments(inputs))
    write_json(directory / "create.json", created)
    result = run_cli(data, script, "run", created["task_id"])
    expected = "needs_input" if name.startswith("missing-") else "incomplete"
    assert result["status"] == expected
    return finish_case(directory, data, script, created["task_id"], result)


def main() -> None:
    parser = argparse.ArgumentParser(description="当前协议离线演示；不调用真实模型。")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = (args.output or ROOT / ".cta-runs" / "submission-offline" / uuid.uuid4().hex[:12]).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    results = [delivery_case(destination, fixture, fail_search=False),
               delivery_case(destination, fixture, fail_search=True)]
    results.extend(stopped_case(destination, fixture, name)
                   for name in ("missing-budget", "missing-deadline", "budget-stop", "deadline-stop"))
    summary = {"mode": "mock", "real_requests": 0, "results": results}
    write_json(destination / "summary.json", summary)
    print("离线演示完成：Mock，真实模型请求 0 次。")
    for result in results:
        print(f"{result['case']}: {result['status']}，模拟工具费 {result['sim_spent_cents'] / 100:.2f} 元")
    print(f"结果目录：{destination}")


if __name__ == "__main__":
    main()
