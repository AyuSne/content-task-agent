"""Regenerate deterministic ticket-05 evidence with real separate CLI processes.

Scenarios: missing tool receipt, generation interruption/recovery, pause/resume
and an unknown save that must not be replayed.
"""

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
DATA = HERE / "data"
TICKET03_SCRIPT = HERE.parent / "ticket-03" / "script.json"

REVIEW_PASS = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "五项质量要求均满足；材料、脚本、制作安排和待确认项一致。",
    "findings": [],
    "evidence_gaps": [],
}


def _base_env() -> dict:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CTA_FAULT_CRASH", None)
    env.pop("CTA_FAIL_SAVE", None)
    return env


def _invoke(*args: str, script: Path, crash: str | None = None):
    env = _base_env()
    env["CTA_MODEL_SCRIPT"] = str(script)
    if crash:
        env["CTA_FAULT_CRASH"] = crash
    return subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def run(*args: str, script: Path, crash: str | None = None) -> dict:
    result = _invoke(*args, script=script, crash=crash)
    assert result.returncode == 0, (args, result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def crash(*args: str, script: Path, point: str) -> str:
    result = _invoke(*args, script=script, crash=point)
    assert result.returncode == 70, (args, result.returncode, result.stdout, result.stderr)
    return (
        f"command: python -m cta {' '.join(args)}\n"
        f"CTA_FAULT_CRASH={point}\n"
        f"returncode: {result.returncode}\n"
        f"stdout: {result.stdout[:2000]}\n"
        f"stderr: {result.stderr[:2000]}\n"
    )


def write(name: str, body) -> None:
    path = HERE / name
    if isinstance(body, str):
        path.write_text(body, encoding="utf-8")
    else:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ticket03_main_script() -> list[dict]:
    return [copy.deepcopy(item) for item in json.loads(TICKET03_SCRIPT.read_text(encoding="utf-8"))]


def _generation_retry_script() -> list[dict]:
    items = _ticket03_main_script()
    generation_index = next(index for index, item in enumerate(items) if item.get("name") == "generate_script")
    items.insert(generation_index + 1, copy.deepcopy(items[generation_index]))
    return items


def _receipt_script() -> list[dict]:
    return [
        {"type": "tool", "name": "search_materials", "arguments": {"query": "自动卡点", "mode": "basic"}},
        {"type": "tool", "name": "search_materials", "arguments": {"query": "自动卡点", "mode": "basic"}},
        {
            "type": "tool",
            "name": "propose_brief",
            "arguments": json.loads(TICKET03_SCRIPT.read_text(encoding="utf-8"))[1]["arguments"],
        },
    ]


def _create(topic: str) -> dict:
    return run(
        "create",
        "--topic", topic,
        "--budget", "10",
        "--deadline", "2026-10-30T12:00:00+08:00",
        "--audience", "大学生",
        "--requirement", "突出自动卡点",
        script=HERE / "receipt-script.json",
    )


def missing_receipt_scenario() -> None:
    script = HERE / "receipt-script.json"
    created = _create("缺回执场景")
    write("01-create-missing-receipt.json", created)
    task = created["task_id"]
    write("02-crash-missing-receipt.txt", crash("run", task, script=script, point="after_tool_request"))
    before = run("inspect", task, script=script)
    write("03-inspect-before-resume.json", before)
    resumed = run("resume", task, script=script)
    write("04-resume-missing-receipt.json", resumed)
    assert resumed["progress"]["phase"] == "brief_ready"
    assert resumed["run"]["recovery"]["pending_calls"] == 1
    assert resumed["run"]["recovery"]["interrupted_calls"][0]["tool"] == "search_materials"


def generation_scenario() -> None:
    script = HERE / "generation-retry-script.json"
    created = _create("生成中断场景")
    write("05-create-generation.json", created)
    task = created["task_id"]
    brief = run("run", task, script=script)
    write("06-run-brief.json", brief)
    write("07-crash-generation.txt", crash("run", task, script=script, point="generation_after_start"))
    before = run("inspect", task, script=script)
    write("08-inspect-generation-crash.json", before)
    resumed = run("resume", task, script=script)
    write("09-resume-generation.json", resumed)
    assert resumed["progress"]["phase"] == "draft_ready"
    assert resumed["resources"]["sim_spent_cents"] == 260
    after = run("inspect", task, script=script)
    write("10-inspect-generation-resumed.json", after)
    assert [attempt["status"] for attempt in after["attempts"]] == ["success", "interrupted", "success"]

    reviewed = run("review", task, script=script)
    write("11-review.json", reviewed)
    assert reviewed["review"]["verdict"] == "pass"
    write("12-crash-save.txt", crash("save", task, script=script, point="save_after_record"))
    save_crash = run("inspect", task, script=script)
    write("13-inspect-save-crash.json", save_crash)
    unknown = run("resume", task, script=script)
    write("14-resume-save-unknown.json", unknown)
    assert unknown["progress"]["phase"] == "save_unknown"

    blocked = _invoke("save", task, script=script)
    write(
        "15-save-blocked.json",
        {"returncode": blocked.returncode, **json.loads(blocked.stdout)},
    )
    assert blocked.returncode == 4
    final = run("inspect", task, script=script)
    write("16-inspect-save-unknown.json", final)


def pause_scenario() -> None:
    script = HERE / "pause-script.json"
    created = _create("暂停续跑场景")
    write("17-create-pause.json", created)
    task = created["task_id"]
    brief = run("run", task, script=script)
    write("18-pause-run-brief.json", brief)
    paused = run("pause", task, script=script)
    write("19-pause.json", paused)
    assert paused["progress"]["paused"] is True
    paused_run = run("run", task, script=script)
    write("20-run-while-paused.json", paused_run)
    assert paused_run["run"]["stop_reason"] == "paused"
    resumed = run("resume", task, script=script)
    write("21-resume-after-pause.json", resumed)
    assert resumed["progress"]["paused"] is False
    assert resumed["progress"]["sim_elapsed_ms"] == 10000


def main() -> int:
    if DATA.exists():
        assert DATA == HERE / "data"
        shutil.rmtree(DATA)
    receipt = _receipt_script()
    (HERE / "receipt-script.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    main_script = _generation_retry_script()
    main_script.append({"channel": "review", "type": "tool", "name": "report_review", "arguments": REVIEW_PASS})
    (HERE / "generation-retry-script.json").write_text(
        json.dumps(main_script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pause_script = _ticket03_main_script()
    pause_script.append({"channel": "review", "type": "tool", "name": "report_review", "arguments": REVIEW_PASS})
    (HERE / "pause-script.json").write_text(
        json.dumps(pause_script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    missing_receipt_scenario()
    generation_scenario()
    pause_scenario()
    print("ticket-05 evidence regenerated", DATA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
