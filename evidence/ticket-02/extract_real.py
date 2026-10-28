"""Extract the already-executed real ticket-02 trial into reviewable evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TRIAL = Path(r"D:\AAA小影科技\.scratch\content-task-agent-build\run-state\real-trial-02")
TASK_ID = "t_fff593487db6"


def load(name: str) -> dict:
    return json.loads((TRIAL / name).read_text(encoding="utf-8-sig"))


def run_inspect() -> dict:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(TRIAL)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "cta", "--mode", "real", "inspect", TASK_ID],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def main() -> int:
    create = load("create.json")
    run = load("run.json")
    inspect = run_inspect()
    (HERE / "04-real-create.json").write_text(json.dumps(create, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (HERE / "05-real-run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "task_id": inspect["task_id"],
        "status": inspect["status"],
        "phase": inspect["progress"]["phase"],
        "used_rounds": inspect["progress"]["used_rounds"],
        "sim_elapsed_ms": inspect["progress"]["sim_elapsed_ms"],
        "sim_spent_cents": inspect["resources"]["sim_spent_cents"],
        "real_usage": inspect["resources"]["real_usage"],
        "attempts": inspect["attempts"],
        "ledger": inspect["ledger"],
        "llm_requests": inspect["llm_requests"],
        "messages": [
            {
                "seq": message["seq"],
                "role": message["role"],
                "name": message["name"],
                "content": message["content"],
                "tool_calls": message["tool_calls"],
            }
            for message in inspect["messages"]
            if message["role"] in ("assistant", "tool")
        ],
    }
    (HERE / "06-real-trace.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("real evidence extracted", TASK_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
