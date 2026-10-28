"""Regenerate deterministic ticket-02 evidence (mock model, real product code).

Run from the repository root:

    python evidence/ticket-02/generate_scripted.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = HERE / "data-scripted"

BRIEF = {
    "goal": "让大学生在社团招新季了解帧剪的自动卡点",
    "audience": "18-24 岁大学生（合成调研样本）",
    "product_facts": [
        {
            "claim": "自动卡点可把视频片段对齐到节拍点",
            "material_id": "M-001",
            "quote": "把用户选中的视频片段对齐到节拍点",
            "scope": "iOS/Android 4.2 及以上；15/30/60 秒成片",
            "use": "主脚本卡点段",
        }
    ],
    "creative_requirements": [{"text": "突出自动卡点", "source": "用户输入"}],
    "assumptions": [{"text": "活动平台为短视频信息流", "basis": "题材常识，待确认"}],
    "suggestions": [{"text": "采用校园社团情境", "basis": "M-003 调研"}],
    "unknowns": [{"question": "是否需要投放预算？", "impact": "影响内容条数", "blocking": False}],
    "sources": [
        {"material_id": "M-001", "used_for": "能力依据"},
        {"material_id": "M-003", "used_for": "受众依据"},
    ],
}

SCRIPT = [
    {"type": "tool", "name": "search_materials", "arguments": {"query": "自动卡点", "mode": "basic"},
     "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
    {"type": "tool", "name": "read_material", "arguments": {"material_id": "M-001"}},
    {"type": "tool", "name": "propose_brief", "arguments": BRIEF},
]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["CTA_MODEL_SCRIPT"] = str(HERE / "script.json")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def write(name: str, body: dict) -> None:
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if DATA.exists():
        assert DATA == HERE / "data-scripted"
        shutil.rmtree(DATA)
    (HERE / "script.json").write_text(json.dumps(SCRIPT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    created = run(
        "create", "--topic", "校园剪辑挑战", "--budget", "10",
        "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
        "--requirement", "突出自动卡点",
    )
    assert created.returncode == 0, created.stderr
    task = json.loads(created.stdout)
    write("01-create.json", task)

    ran = run("run", task["task_id"])
    assert ran.returncode == 0, ran.stderr
    write("02-run.json", json.loads(ran.stdout))

    inspected = run("inspect", task["task_id"])
    assert inspected.returncode == 0, inspected.stderr
    write("03-inspect.json", json.loads(inspected.stdout))
    print("scripted evidence regenerated", task["task_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
