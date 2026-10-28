"""Regenerate deterministic ticket-10 evidence: export, edit, import, re-review, save."""

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
    draft = run("run", task)
    write("01-draft.json", draft)
    exported = run("export", task)
    write("02-export.json", exported)
    markdown = next(Path(item["path"]) for item in exported["files"] if item["path"].endswith("campaign-v1.md"))
    original = markdown.read_text(encoding="utf-8")
    (HERE / "original-v1.md").write_text(original, encoding="utf-8")
    edited = original.replace("把招新现场剪成你的节奏", "把社团招新剪成你的节奏")
    markdown.write_text(edited, encoding="utf-8")
    (HERE / "edited-v1.md").write_text(edited, encoding="utf-8")

    imported = run("import-draft", task, "--file", str(markdown))
    write("03-import.json", imported)
    assert imported["import"]["output_version"] == 2
    reviewed = run("review", task)
    write("04-review-v2.json", reviewed)
    saved = run("save", task)
    write("05-save-v2.json", saved)
    assert saved["status"] == "complete"
    write("06-inspect.json", run("inspect", task))
    print("ticket-10 evidence regenerated", task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
