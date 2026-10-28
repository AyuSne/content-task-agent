"""Regenerate deterministic ticket-07 evidence (strategy feedback and resource trades)."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TICKET04_SCRIPT = HERE.parent / "ticket-04" / "script.json"
BASE = json.loads(TICKET04_SCRIPT.read_text(encoding="utf-8"))
SEARCH = BASE[0]
BRIEF = BASE[1]
GENERATE_DIRECT = BASE[2]
GENERATION = BASE[3]
CAMPAIGN = BASE[4]
REVIEW_PASS = BASE[5]


def tool(name: str, arguments: dict) -> dict:
    return {"type": "tool", "name": name, "arguments": arguments}


def run(script_path: Path, data_dir: Path, *args: str, extra_env: dict | None = None) -> dict:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(data_dir)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["CTA_MODEL_SCRIPT"] = str(script_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CTA_FAIL_SAVE", None)
    env.pop("CTA_FAIL_SEARCH", None)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{args} exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout)


def write(name: str, body: dict) -> None:
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_script(name: str, items: list[dict]) -> Path:
    path = HERE / name
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def reset(path: Path) -> None:
    if path.exists():
        assert path.parent == HERE / "data"
        shutil.rmtree(path)


def copy_delivery(saved: dict, folder: str) -> None:
    target = HERE / folder
    target.mkdir(parents=True, exist_ok=True)
    for item in saved["save"]["files"]:
        source = Path(item["path"])
        (target / source.name).write_bytes(source.read_bytes())


def scenario_budget_alternative() -> None:
    script = [
        SEARCH,
        tool(
            "compare_strategies",
            {
                "search_mode": "search_extended",
                "generation_mode": "generate_multi",
                "search_queries": 0,
                "directions": ["user_scenario", "tutorial"],
            },
        ),
        tool(
            "choose_strategy",
            {
                "direction": "user_scenario",
                "search_mode": "search_extended",
                "generation_mode": "generate_multi",
                "search_queries": 0,
                "rationale": "先比较高配路径；若预算不足再改走替代。",
            },
        ),
        tool(
            "choose_strategy",
            {
                "direction": "user_scenario",
                "search_mode": "search_basic",
                "generation_mode": "generate_direct",
                "search_queries": 0,
                "rationale": "高配完整路径超出剩余预算；基本检索加直接生成仍覆盖生成、核验与保存。",
            },
        ),
        BRIEF,
        GENERATE_DIRECT,
        GENERATION,
        CAMPAIGN,
        REVIEW_PASS,
    ]
    script_path = write_script("script-budget-alternative.json", script)
    data = HERE / "data" / "budget-alternative"
    reset(data)

    created = run(script_path, data, "create", "--topic", "校园剪辑挑战", "--budget", "2.50",
                  "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                  "--requirement", "突出自动卡点")
    write("01-create.json", created)
    task = created["task_id"]
    write("02-brief.json", run(script_path, data, "run", task))
    draft = run(script_path, data, "run", task)
    write("03-draft.json", draft)
    assert draft["progress"]["phase"] == "draft_ready"
    reviewed = run(script_path, data, "review", task)
    write("04-review.json", reviewed)
    saved = run(script_path, data, "save", task)
    write("05-save.json", saved)
    assert saved["status"] == "complete", saved
    write("06-inspect.json", run(script_path, data, "inspect", task))
    copy_delivery(saved, "delivery-budget-alternative")
    assert saved["resources"]["sim_spent_cents"] == 230


def scenario_no_path() -> None:
    script = [
        SEARCH,
        tool(
            "compare_strategies",
            {"search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 0},
        ),
        tool(
            "choose_strategy",
            {
                "direction": "functional_demo",
                "search_mode": "search_basic",
                "generation_mode": "generate_direct",
                "search_queries": 0,
                "rationale": "尝试最低成本完整路径。",
            },
        ),
        tool(
            "stop_incomplete",
            {
                "reason": "剩余预算不足以完成生成、核验与保存，且没有更省的可行路径。",
                "gaps": [
                    {"gap": "预算缺口无法覆盖完整交付路径", "impact": "无法形成可交付方案"},
                    {"gap": "现有资料未覆盖关键能力证据", "impact": "需要补查或由用户决定替代方向"},
                ],
            },
        ),
    ]
    script_path = write_script("script-no-path.json", script)
    data = HERE / "data" / "no-path"
    reset(data)

    created = run(script_path, data, "create", "--topic", "校园剪辑挑战", "--budget", "0.50",
                  "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                  "--requirement", "突出自动卡点")
    write("07-nopath-create.json", created)
    run_doc = run(script_path, data, "run", created["task_id"])
    write("08-nopath-run.json", run_doc)
    assert run_doc["status"] == "incomplete"
    write("09-nopath-inspect.json", run(script_path, data, "inspect", created["task_id"]))


def scenario_search_failure() -> None:
    script = [
        SEARCH,
        tool("search_materials", {"query": "自动卡点 能力 范围", "mode": "basic"}),
        BRIEF,
        tool(
            "stop_incomplete",
            {
                "reason": "检索失败后已完成补查并形成简报；本样例按计划停止。",
                "gaps": [{"gap": "未生成完整方案", "impact": "本样例只验证检索失败与补查反馈"}],
            },
        ),
    ]
    script_path = write_script("script-search-failure.json", script)
    data = HERE / "data" / "search-failure"
    reset(data)

    created = run(script_path, data, "create", "--topic", "校园剪辑挑战", "--budget", "10",
                  "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                  "--requirement", "突出自动卡点", extra_env={"CTA_FAIL_SEARCH": "1"})
    write("10-search-failure-create.json", created)
    task = created["task_id"]
    brief = run(script_path, data, "run", task, extra_env={"CTA_FAIL_SEARCH": "1"})
    write("11-search-failure-brief.json", brief)
    assert brief["progress"]["phase"] == "brief_ready"
    stopped = run(script_path, data, "run", task, extra_env={"CTA_FAIL_SEARCH": "1"})
    write("12-search-failure-stop.json", stopped)
    assert stopped["status"] == "incomplete"
    write("13-search-failure-inspect.json", run(script_path, data, "inspect", task, extra_env={"CTA_FAIL_SEARCH": "1"}))


def scenario_improvement() -> None:
    script = [
        SEARCH,
        BRIEF,
        tool(
            "choose_strategy",
            {
                "direction": "user_scenario",
                "search_mode": "search_basic",
                "generation_mode": "generate_direct",
                "search_queries": 0,
                "rationale": "直接生成已经能形成可交付脚本。",
            },
        ),
        GENERATE_DIRECT,
        GENERATION,
        tool(
            "choose_strategy",
            {
                "direction": "user_scenario",
                "search_mode": "search_basic",
                "generation_mode": "generate_multi",
                "search_queries": 0,
                "improvement": True,
                "expected_benefit": "比较三个开场候选，降低单一候选的表达风险",
                "rationale": "基础方案已可行；额外投入有明确改善目标，完整剩余路径仍可行。",
            },
        ),
        tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_multi"}),
        copy.deepcopy(GENERATION),
        copy.deepcopy(GENERATION),
        copy.deepcopy(GENERATION),
        CAMPAIGN,
        REVIEW_PASS,
    ]
    script_path = write_script("script-improvement.json", script)
    data = HERE / "data" / "improvement"
    reset(data)

    created = run(script_path, data, "create", "--topic", "校园剪辑挑战", "--budget", "10",
                  "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                  "--requirement", "突出自动卡点")
    write("14-improvement-create.json", created)
    task = created["task_id"]
    write("15-improvement-brief.json", run(script_path, data, "run", task))
    draft = run(script_path, data, "run", task)
    write("16-improvement-draft.json", draft)
    assert draft["progress"]["phase"] == "draft_ready"
    write("17-improvement-review.json", run(script_path, data, "review", task))
    saved = run(script_path, data, "save", task)
    write("18-improvement-save.json", saved)
    assert saved["status"] == "complete"
    write("19-improvement-inspect.json", run(script_path, data, "inspect", task))
    copy_delivery(saved, "delivery-improvement")


def main() -> int:
    scenario_budget_alternative()
    scenario_no_path()
    scenario_search_failure()
    scenario_improvement()
    print("ticket-07 evidence regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
