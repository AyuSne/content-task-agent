"""Regenerate deterministic ticket-04 evidence (review pass, verified save, export)."""

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

REVIEW_PASS = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "五项质量要求均满足；材料、脚本、制作安排和待确认项一致。",
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
    script.append({"channel": "review", "type": "tool", "name": "report_review", "arguments": REVIEW_PASS})
    (HERE / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    created = run(
        "create", "--topic", "校园剪辑挑战", "--budget", "10",
        "--deadline", "2026-10-30T12:00:00+08:00", "--audience", "大学生",
        "--requirement", "突出自动卡点",
    )
    write("01-create.json", created)
    task = created["task_id"]
    write("02-brief.json", run("run", task))
    draft = run("run", task)
    write("03-draft.json", draft)
    assert draft["progress"]["phase"] == "draft_ready"
    reviewed = run("review", task)
    write("04-review.json", reviewed)
    assert reviewed["review"]["verdict"] == "pass"
    saved = run("save", task)
    write("05-save.json", saved)
    assert saved["status"] == "complete"
    exported = run("export", task)
    write("06-export.json", exported)
    inspected = run("inspect", task)
    write("07-inspect.json", inspected)

    DELIVERY.mkdir(parents=True, exist_ok=True)
    for item in saved["save"]["files"]:
        source = Path(item["path"])
        (DELIVERY / source.name).write_bytes(source.read_bytes())
    print("ticket-04 evidence regenerated", task, "files", len(saved["save"]["files"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
