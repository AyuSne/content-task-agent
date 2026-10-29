"""Replay an explicit operator title edit against a verified real task."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from cta.config import load_config
from cta.service import TaskService


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--title", required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    commands = []

    def cli(label, *arguments):
        argv = [sys.executable, "-m", "cta", "--mode", "real", "--data-dir", str(args.data_dir), *arguments]
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False)
        (args.output / f"{label}.json").write_text(result.stdout, encoding="utf-8")
        if result.stderr:
            (args.output / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        commands.append({"label": label, "argv": argv, "exit_code": result.returncode})
        (args.output / "commands.json").write_text(json.dumps(commands, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{label}: exit={result.returncode}", flush=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    cli("01-reconcile", "reconcile", args.task_id)
    before = TaskService(load_config("real", args.data_dir)).inspect(args.task_id)
    if before["status"] != "complete":
        raise ValueError("Expected a verified base before the explicit operator edit")
    archive = args.output / "base-verified"
    archive.mkdir()
    for record in before["save"]["files"]:
        shutil.copy2(record["path"], archive / Path(record["path"]).name)
    base = before["outputs"][-1]
    markdown = archive / f"campaign-v{base['version']}.md"
    text = markdown.read_text(encoding="utf-8")
    old_title = base["content"]["activity"]["theme"]
    edited = args.output / "operator-edited.md"
    edited.write_text(text.replace(old_title, args.title), encoding="utf-8")
    (args.output / "edit.json").write_text(json.dumps({
        "task_id": args.task_id, "base_output_version": base["version"],
        "edit": "explicit operator title change only", "previous_title": old_title,
        "new_title": args.title, "input_version": before["input"]["version"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    cli("02-import", "import-draft", args.task_id, "--file", str(edited))
    result = cli("03-resume", "resume", args.task_id)
    for index in range(3):
        if result is None or result.get("run", {}).get("stop_reason") != "model_error":
            break
        result = cli(f"retry-{index + 1}", "resume", args.task_id)
    cli("04-inspect", "inspect", args.task_id)
    cli("05-export", "export", args.task_id)


if __name__ == "__main__":
    main()
