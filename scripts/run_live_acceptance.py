"""Capture real CLI inputs, outputs and cross-process recovery without choosing model actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("normal", "budget", "recovery", "insufficient"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fault", default="generation_after_start")
    parser.add_argument("--budget", help="本次验收输入中的模拟工具预算")
    parser.add_argument("--resume-attempts", type=int, default=0,
                        help="显式执行有限次CLI续跑，仅用于已记录的模型连接失败；不重置任务限额")
    args = parser.parse_args()
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for key in ("CTA_MODEL_SCRIPT", "CTA_FAULT_CRASH", "CTA_FAIL_SEARCH", "CTA_FAIL_SAVE",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    inputs = {
        "topic": "帧剪校园社团招新短视频推广",
        "budget": args.budget or ("2.70" if args.case == "budget" else "20"),
        "deadline": "2026-10-30T12:00:00+08:00",
        "audience": "18-24岁大学生",
        "requirements": [
            "围绕自动卡点，准备一份含一条30秒主视频的完整内容活动方案。",
            "只使用合成资料可支持的正式能力；说明音乐授权和待制作操作录屏；不作涨粉承诺。",
        ],
    }
    if args.case == "insufficient":
        inputs["requirements"].append("必须承诺AI字幕正式支持粤语，并引用本任务材料证明，不能删改这项要求。")
    manifest = {
        "scenario": args.case, "mode": "real", "input": inputs,
        "main_round_limit_for_new_task": env.get("CTA_MAX_ROUNDS", "application_default"),
        "fault": args.fault if args.case == "recovery" else None,
        "commands": [], "started_at_unix": time.time(),
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    }
    write(target / "manifest.json", manifest)

    def cli(label: str, *command: str, fault=None):
        current_env = dict(env)
        if fault:
            current_env["CTA_FAULT_CRASH"] = fault
        argv = [sys.executable, "-m", "cta", "--mode", "real", "--data-dir", str(target / "data"), *command]
        print(f"{label}: {' '.join(command)}", flush=True)
        started = time.time()
        result = subprocess.run(argv, cwd=ROOT, env=current_env, capture_output=True, text=True, encoding="utf-8", check=False)
        (target / f"{label}.json").write_text(result.stdout, encoding="utf-8")
        if result.stderr:
            (target / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        manifest["commands"].append({"label": label, "argv": argv, "fault": fault,
                                     "exit_code": result.returncode, "wall_seconds": round(time.time() - started, 3)})
        write(target / "manifest.json", manifest)
        print(f"{label}: exit={result.returncode}", flush=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    create_args = ["create", "--new", "--topic", inputs["topic"], "--budget", inputs["budget"],
                   "--deadline", inputs["deadline"], "--audience", inputs["audience"]]
    for requirement in inputs["requirements"]:
        create_args.extend(["--requirement", requirement])
    created = cli("01-create", *create_args)
    task_id = created["task_id"]
    manifest["task_id"] = task_id
    result = cli("02-run", "run", task_id, fault=args.fault if args.case == "recovery" else None)
    if args.case == "recovery":
        for index in range(args.resume_attempts):
            if result is None or result.get("run", {}).get("stop_reason") != "model_error":
                break
            cli(f"pre-fault-{index + 1}-before", "inspect", task_id)
            result = cli(f"pre-fault-{index + 1}-resume", "resume", task_id, fault=args.fault)
        manifest["injected_crash_observed"] = manifest["commands"][-1]["exit_code"] == 70
        if manifest["injected_crash_observed"]:
            cli("03-before-resume", "inspect", task_id)
            result = cli("04-resume", "resume", task_id)
    for index in range(args.resume_attempts):
        if result is None or result.get("run", {}).get("stop_reason") != "model_error":
            break
        cli(f"retry-{index + 1}-before", "inspect", task_id)
        result = cli(f"retry-{index + 1}-resume", "resume", task_id)
    inspected = cli("05-inspect", "inspect", task_id)
    cli("06-export", "export", task_id)
    manifest.update({"finished_at_unix": time.time(), "status": inspected["status"],
                     "progress": inspected["progress"], "resources": inspected["resources"]})
    manifest["file_hashes"] = {
        str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target.rglob("*") if path.is_file() and path.suffix in (".md", ".jsonl")
    }
    write(target / "manifest.json", manifest)
    print(json.dumps({"task_id": task_id, "status": inspected["status"],
                      "progress": inspected["progress"]}, ensure_ascii=False), flush=True)
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
