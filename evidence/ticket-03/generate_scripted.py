"""Regenerate deterministic ticket-03 evidence (mock model, real product code)."""

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

SCRIPT_CANDIDATE = {
    "strategy": "user_scenario",
    "duration_s": 30,
    "segments": [
        {"index": 1, "time_range": "0-8s", "visual": "社团招新现场", "audio": "现场声与鼓点", "subtitle": "招新季，先把现场拍下来", "material_ids": ["M-005"], "evidence_ids": []},
        {"index": 2, "time_range": "8-22s", "visual": "操作录屏：对齐节拍并逐段微调", "audio": "流行乐", "subtitle": "自动卡点把片段对齐节拍，还能逐段微调", "material_ids": ["M-001"], "evidence_ids": ["E1"]},
        {"index": 3, "time_range": "22-30s", "visual": "成片播放", "audio": "音乐收束", "subtitle": "把招新现场剪成你的节奏", "material_ids": ["M-005"], "evidence_ids": []},
    ],
    "claims": [
        {"id": "E1", "claim": "自动卡点把视频片段对齐到节拍点", "material_id": "M-001", "quote": "把用户选中的视频片段对齐到节拍点", "scope": "iOS/Android 4.2 及以上"}
    ],
    "open_questions": [],
}

CAMPAIGN = {
    "strategy": {
        "name": "user_scenario",
        "rationale": "受众更愿意保留手动调整，用社团招新情境承接自动卡点演示",
        "alternatives": [
            {"name": "tutorial", "why_not": "教程信息密度高，30 秒内难以兼顾情境与操作"},
            {"name": "functional_demo", "why_not": "纯功能演示缺少校园情境"},
        ],
    },
    "activity": {
        "goal": "让 18-24 岁大学生在社团招新季了解并尝试自动卡点",
        "audience": "18-24 岁大学生，重点是社团招新负责人",
        "theme": "把招新现场剪成你的节奏",
        "format": "1 条 30 秒主视频 + 2 条 15 秒切条",
        "content_items": [
            {"title": "主视频", "purpose": "情境 + 操作演示", "duration_s": 30, "platform": "短视频信息流"},
            {"title": "切条 A", "purpose": "卡点前后对比", "duration_s": 15, "platform": "短视频信息流"},
        ],
    },
    "script": {"duration_s": 30, "segments": SCRIPT_CANDIDATE["segments"]},
    "materials": [
        {"name": "校园场景视频素材包", "material_id": "M-005", "status": "existing", "use": "社团招新情境画面", "position": "第 1、3 段", "rights_note": "演员授权已取得，限本题活动使用"},
        {"name": "产品操作录屏", "material_id": "", "status": "to_create", "use": "展示自动卡点操作", "position": "第 2 段", "rights_note": "待录制"},
        {"name": "背景音乐", "material_id": "M-006", "status": "to_acquire", "use": "卡点演示音乐", "position": "第 2 段", "rights_note": "商用授权待购买"},
    ],
    "production_steps": [
        {"step": "录制自动卡点操作", "owner": "内容制作", "timing": "T-3 天", "note": "使用 4.2 正式版"},
        {"step": "获取音乐授权", "owner": "运营", "timing": "T-2 天", "note": "未取得则替换音乐"},
    ],
    "evidence": [
        {"id": "E1", "claim": "自动卡点把视频片段对齐到节拍点，可逐段微调", "material_id": "M-001", "quote": "把用户选中的视频片段对齐到节拍点", "scope": "iOS/Android 4.2 及以上", "use": "主脚本第 2 段"}
    ],
    "open_questions": [{"question": "投放平台清单未确认", "impact": "影响切条画幅", "blocking": False}],
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["CTA_MODEL_SCRIPT"] = str(HERE / "script.json")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def write(name: str, body: dict) -> None:
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if DATA.exists():
        assert DATA == HERE / "data-scripted"
        shutil.rmtree(DATA)
    script = [
        {"type": "tool", "name": "search_materials", "arguments": {"query": "自动卡点", "mode": "basic"}},
        {"type": "tool", "name": "propose_brief", "arguments": BRIEF},
        {"type": "tool", "name": "generate_script", "arguments": {"strategy": "user_scenario", "generation_mode": "generate_direct"}},
        {"channel": "generation", "type": "text", "content": json.dumps(SCRIPT_CANDIDATE, ensure_ascii=False),
         "usage": {"prompt_tokens": 800, "completion_tokens": 400, "total_tokens": 1200}},
        {"type": "tool", "name": "propose_campaign", "arguments": CAMPAIGN},
    ]
    (HERE / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    created = run("create", "--topic", "校园剪辑挑战", "--budget", "10",
                  "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                  "--requirement", "突出自动卡点")
    assert created.returncode == 0, created.stderr
    task = json.loads(created.stdout)
    write("01-create.json", task)
    brief = run("run", task["task_id"])
    assert brief.returncode == 0, brief.stderr
    write("02-brief-run.json", json.loads(brief.stdout))
    draft = run("run", task["task_id"])
    assert draft.returncode == 0, draft.stderr
    write("03-draft-run.json", json.loads(draft.stdout))
    inspected = run("inspect", task["task_id"])
    assert inspected.returncode == 0, inspected.stderr
    write("04-inspect.json", json.loads(inspected.stdout))
    print("scripted evidence regenerated", task["task_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
