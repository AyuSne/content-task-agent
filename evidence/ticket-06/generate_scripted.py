"""Regenerate deterministic ticket-06 evidence: disposition, revision, re-review, save."""

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
DELIVERY = HERE / "delivery"
TICKET03_SCRIPT = HERE.parent / "ticket-03" / "script.json"

REVIEW_FAIL = {
    "verdict": "fail",
    "completeness": "full",
    "summary": "主脚本一处事实超出材料支持范围，必须修改。",
    "findings": [
        {
            "finding_id": "F1",
            "severity": "must_fix",
            "location": "主脚本第 2 段字幕",
            "requirement": "事实支持",
            "evidence": "M-001 只说明对齐节拍与逐段微调，未支持更广承诺",
            "issue": "字幕写入了材料未支持的能力承诺。",
            "suggestion": "收窄为主张—片段—范围一致的表达，并保留逐段微调说明。",
            "reviewed_version": "1",
        }
    ],
    "evidence_gaps": [],
}

REVIEW_PASS = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "返修后事实、要求、素材与制作安排一致。",
    "findings": [],
    "evidence_gaps": [],
}


def run(*args: str) -> dict:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["CTA_MODEL_SCRIPT"] = str(HERE / "script.json")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CTA_FAIL_SAVE", None)
    result = subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, (args, result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def write(name: str, body: dict) -> None:
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if DATA.exists():
        assert DATA == HERE / "data-scripted"
        shutil.rmtree(DATA)
    if DELIVERY.exists():
        assert DELIVERY == HERE / "delivery"
        shutil.rmtree(DELIVERY)
    script = json.loads(TICKET03_SCRIPT.read_text(encoding="utf-8"))
    campaign = script[-1]["arguments"]
    script += [
        {"channel": "review", "type": "tool", "name": "report_review", "arguments": REVIEW_FAIL},
        {
            "type": "tool",
            "name": "record_disposition",
            "arguments": {"finding_id": "F1", "decision": "accepted", "reason": "确实超出材料支持范围，需要收窄"},
        },
        {
            "type": "tool",
            "name": "generate_script",
            "arguments": {"strategy": "user_scenario", "generation_mode": "generate_direct"},
        },
        {
            "channel": "generation",
            "type": "text",
            "content": json.dumps(
                {
                    "strategy": "user_scenario",
                    "duration_s": 30,
                    "segments": campaign["script"]["segments"],
                    "claims": [
                        {
                            "id": "E1",
                            "claim": "自动卡点把视频片段对齐到节拍点，可逐段微调",
                            "material_id": "M-001",
                            "quote": "把用户选中的视频片段对齐到节拍点",
                            "scope": "iOS/Android 4.2 及以上；15/30/60 秒成片",
                        }
                    ],
                    "open_questions": [],
                },
                ensure_ascii=False,
            ),
        },
        {"type": "tool", "name": "propose_campaign", "arguments": campaign},
        {"channel": "review", "type": "tool", "name": "report_review", "arguments": REVIEW_PASS},
    ]
    (HERE / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    created = run(
        "create", "--topic", "校园剪辑挑战", "--budget", "10",
        "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
        "--requirement", "突出自动卡点",
    )
    write("01-create.json", created)
    task = created["task_id"]
    write("02-brief.json", run("run", task))
    write("03-draft-v1.json", run("run", task))
    reviewed = run("review", task)
    write("04-review-v1-fail.json", reviewed)
    assert reviewed["review"]["verdict"] == "fail"
    revised = run("run", task)
    write("05-revision.json", revised)
    assert revised["progress"]["current_output"]["version"] == 2
    re_reviewed = run("review", task)
    write("06-review-v2-pass.json", re_reviewed)
    assert re_reviewed["review"]["verdict"] == "pass"
    saved = run("save", task)
    write("07-save.json", saved)
    assert saved["status"] == "complete"
    inspected = run("inspect", task)
    write("08-inspect.json", inspected)

    DELIVERY.mkdir(parents=True, exist_ok=True)
    for item in saved["save"]["files"]:
        source = Path(item["path"])
        (DELIVERY / source.name).write_bytes(source.read_bytes())
    print("ticket-06 evidence regenerated", task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
