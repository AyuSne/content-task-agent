"""Regenerate deterministic ticket-09 evidence: input updates, adoption and stale results."""

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
COMPLETE_SCRIPT = HERE.parent / "ticket-04" / "script.json"


def run(*args: str, expect: int = 0, script: Path | None = None) -> dict:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if script is not None:
        env["CTA_MODEL_SCRIPT"] = str(script)
    result = subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == expect, (args, result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}


def write(name: str, body: dict) -> None:
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if DATA.exists():
        assert DATA == HERE / "data-scripted"
        shutil.rmtree(DATA)
    created = run("create", "--topic", "校园剪辑挑战", "--budget", "10",
                  "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                  "--requirement", "突出自动卡点")
    task = created["task_id"]
    write("01-create.json", created)
    write("02-resource-update.json", run("update", task, "--budget", "20"))
    write("03-semantic-update.json", run("update", task, "--audience", "研究生"))
    write("04-knowledge-adoption.json", run(
        "adopt-knowledge", task, "--knowledge-id", "campaign-creation", "--version", "2026-10-30.v1"
    ))
    write("05-material-adoption.json", run("adopt-material", task, "--version", "2026-10-30.v1"))
    write("06-inspect.json", run("inspect", task))

    complete = run("create", "--topic", "已完成的校园活动", "--budget", "10",
                   "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
                   "--requirement", "突出自动卡点")
    complete_task = complete["task_id"]
    run("run", complete_task, script=COMPLETE_SCRIPT)
    run("run", complete_task, script=COMPLETE_SCRIPT)
    run("review", complete_task, script=COMPLETE_SCRIPT)
    saved = run("save", complete_task, script=COMPLETE_SCRIPT)
    write("07-complete-before-update.json", saved)
    updated = run("update", complete_task, "--budget", "30")
    write("08-resource-update-after-complete.json", updated)
    reused = run("save", complete_task)
    write("09-save-reused-after-resource-update.json", reused)
    print("ticket-09 evidence regenerated", task, complete_task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
