"""Regenerate ticket-01 CLI evidence without BOM, for review and reproduction.

Run from the repository root with the project venv:

    python evidence/ticket-01/generate.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = HERE / "data"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(DATA)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
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


def save(name: str, result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, (name, result.returncode, result.stderr)
    body = json.loads(result.stdout)
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body


def main() -> int:
    if DATA.exists():
        # Only remove the disposable evidence database directory inside this folder.
        assert DATA == HERE / "data"
        import shutil

        shutil.rmtree(DATA)

    save("01-missing.json", run("create"))
    created = save(
        "02-create.json",
        run(
            "create", "--topic", "校园剪辑挑战", "--budget", "10",
            "--deadline", "2026-10-30T12:00:00+08:00",
            "--audience", "大学生", "--requirement", "突出自动卡点",
        ),
    )
    save("03-status.json", run("status", created["task_id"]))
    save(
        "04-duplicate.json",
        run(
            "create", "--topic", "校园剪辑挑战", "--budget", "10.00",
            "--deadline", "2026-10-30T04:00:00Z",
            "--audience", "大学生", "--requirement", "突出自动卡点",
        ),
    )
    save(
        "05-new.json",
        run(
            "create", "--topic", "校园剪辑挑战", "--budget", "10",
            "--deadline", "2026-10-30T12:00:00+08:00",
            "--audience", "大学生", "--requirement", "突出自动卡点", "--new",
        ),
    )
    save("06-list.json", run("list"))
    save(
        "07-real.json",
        run(
            "--mode", "real", "create", "--topic", "校园剪辑挑战",
            "--budget", "10", "--deadline", "2026-10-30T12:00:00+08:00",
        ),
    )
    print("evidence regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
