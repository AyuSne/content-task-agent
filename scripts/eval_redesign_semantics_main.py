"""Prepare labelled scripted histories and continue them with the real CLI."""

import argparse
import copy
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from cta.config import load_env_file, redact_secret

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/eval/redesign-semantics-main-v1.json"


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def command(directory, data, mode, label, args, environment):
    started = datetime.now(UTC)
    clock = time.monotonic()
    write_json(directory / f"{label}-started.json", {"mode": mode, "arguments": args, "started_at": started.isoformat()})
    process = subprocess.run(
        [sys.executable, "-m", "cta", "--mode", mode, "--data-dir", str(data), *args],
        cwd=ROOT, env=environment, capture_output=True, encoding="utf-8", errors="replace", check=False,
    )
    secret = environment.get("DEEPSEEK_API_KEY", "")
    stdout = redact_secret(process.stdout, secret)
    stderr = redact_secret(process.stderr, secret)
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = None
    write_json(directory / f"{label}.json", {
        "mode": mode, "arguments": args, "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(), "wall_seconds": round(time.monotonic() - clock, 3),
        "exit_code": process.returncode, "stdout": parsed, "stdout_raw": stdout, "stderr": stderr,
    })
    if process.returncode != 0 or parsed is None:
        raise RuntimeError(f"CLI failed; see {directory / (label + '.json')}")
    return parsed


def seed(root):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "cases.json", fixture["cases"])
    for case in fixture["cases"]:
        directory = root / case["id"]
        directory.mkdir()
        data = directory / "data"
        script_path = directory / "scripted-model.json"
        script = copy.deepcopy(fixture["script"])
        script["segments"][0]["voiceover"] = case["voiceover"]
        items = [
            {"name": "propose_brief", "arguments": fixture["brief"]},
            {"name": "submit_script", "arguments": {"brief_version": 1, "script": script}},
        ]
        review = {"channel": "review", "name": "report_review", "arguments": fixture["review"]}
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", CTA_SIM_START="2026-10-29T00:00:00+08:00",
                   DEEPSEEK_API_KEY="scripted-fixture-only", CTA_MODEL_SCRIPT=str(script_path))
        script_path.write_text(json.dumps(items + [review], ensure_ascii=False), encoding="utf-8")
        created = command(directory, data, "mock", "seed-create", [
            "create", "--topic", "校园剪辑挑战", "--budget", "10", "--deadline", "2026-10-30T12:00:00+08:00",
            "--audience", "大学生", "--requirement", "突出自动卡点", "--new",
        ], env)
        task_id = created["task_id"]
        command(directory, data, "mock", "seed-script", ["run", task_id, "--max-rounds", "2"], env)
        view = command(directory, data, "mock", "seed-script-inspect", ["inspect", task_id], env)
        script_row = view["scripts"][-1]
        ref = {"id": script_row["output_id"], "version": script_row["version"]}
        items.append({"name": "submit_campaign", "arguments": {"script_ref": ref, "campaign": fixture["campaign"]}})
        script_path.write_text(json.dumps(items + [review], ensure_ascii=False), encoding="utf-8")
        command(directory, data, "mock", "seed-campaign", ["resume", task_id, "--max-rounds", "1"], env)
        command(directory, data, "mock", "seed-review", ["review", task_id], env)
        command(directory, data, "mock", "seed-inspect", ["inspect", task_id], env)


def run_real(root, case_id, env_file):
    directory = root / case_id
    data = directory / "data"
    real = data / "real"
    for started in directory.glob("real-*-started.json"):
        if not started.with_name(started.name.replace("-started.json", ".json")).exists():
            raise RuntimeError(f"Interrupted command requires manual inspection before continuation: {started}")
    if not real.exists():
        real.mkdir()
        with sqlite3.connect(data / "mock" / "tasks.db") as source, sqlite3.connect(real / "tasks.db") as target:
            source.backup(target)
        write_json(directory / "real-seed.json", {"source": "labelled mock task database", "method": "sqlite backup",
                                                   "note": "All pre-existing requests and initial review are scripted."})
    load_env_file(env_file)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", CTA_SIM_START="2026-10-29T00:00:00+08:00")
    env.pop("CTA_MODEL_SCRIPT", None)
    if env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com") != "https://api.deepseek.com":
        raise ValueError("Real run requires direct https://api.deepseek.com")
    if env.get("DEEPSEEK_MODEL", "deepseek-flash") != "deepseek-flash":
        raise ValueError("Real run requires deepseek-flash")
    task_id = json.loads((directory / "seed-create.json").read_text(encoding="utf-8"))["stdout"]["task_id"]
    previous = sorted(directory.glob("real-resume-[0-9][0-9].json"))
    if previous:
        last = json.loads(previous[-1].read_text(encoding="utf-8"))["stdout"]
        if (last is None or last["status"] != "in_progress" or last.get("run", {}).get("requests") == 0
                or last.get("run", {}).get("stop_reason") == "save_unknown"):
            raise RuntimeError("Previous run stopped; inspect its records before deciding whether to continue")
    index = len(previous) + 1
    while True:
        document = command(directory, data, "real", f"real-resume-{index:02}",
                           ["resume", task_id, "--max-rounds", "1"], env)
        command(directory, data, "real", f"real-inspect-{index:02}", ["inspect", task_id], env)
        print(f"{case_id} round {index}: {document['status']} {document.get('run', {}).get('stop_reason')}", flush=True)
        if document["status"] != "in_progress" or document.get("run", {}).get("requests") == 0:
            break
        if document.get("run", {}).get("stop_reason") == "save_unknown":
            break
        index += 1


def verify(root, case_id):
    directory = root / case_id
    data = directory / "data"
    task_id = json.loads((directory / "seed-create.json").read_text(encoding="utf-8"))["stdout"]["task_id"]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", DEEPSEEK_API_KEY="not-used-by-export")
    env.pop("CTA_MODEL_SCRIPT", None)
    command(directory, data, "real", "real-export", ["export", task_id], env)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("root", type=Path)
    real_parser = sub.add_parser("real")
    real_parser.add_argument("root", type=Path)
    real_parser.add_argument("case", choices=["actual-defect", "false-positive"])
    real_parser.add_argument("--env-file", required=True, type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("root", type=Path)
    verify_parser.add_argument("case", choices=["actual-defect", "false-positive"])
    args = parser.parse_args()
    if args.action == "seed":
        seed(args.root.resolve())
    elif args.action == "real":
        run_real(args.root.resolve(), args.case, args.env_file)
    else:
        verify(args.root.resolve(), args.case)


if __name__ == "__main__":
    main()
