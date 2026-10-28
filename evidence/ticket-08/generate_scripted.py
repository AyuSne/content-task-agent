"""Regenerate deterministic ticket-08 evidence: verified save, missing file repair, reconcile."""

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
SCRIPT = HERE.parent / "ticket-04" / "script.json"


def run(*args: str, expect: int = 0) -> dict:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["CTA_MODEL_SCRIPT"] = str(SCRIPT)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
    run("run", task)
    run("run", task)
    run("review", task)
    saved = run("save", task)
    write("01-save.json", saved)
    assert saved["status"] == "complete"

    report = next(item for item in saved["save"]["files"] if item["path"].endswith("execution-report.md"))
    Path(report["path"]).unlink()
    write("02-missing-file.json", {"missing": report["path"]})
    repaired = run("export", task, "--repair")
    write("03-export-repair.json", repaired)
    assert repaired["verified"] is True

    campaign = next(item for item in saved["save"]["files"] if item["path"].endswith("campaign-v1.json"))
    Path(campaign["path"]).write_text("{\"corrupted\": true}\n", encoding="utf-8")
    reconciled = run("reconcile", task)
    write("04-reconcile.json", reconciled)
    assert reconciled["status"] == "complete"
    inspected = run("inspect", task)
    write("05-inspect.json", inspected)
    print("ticket-08 evidence regenerated", task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
