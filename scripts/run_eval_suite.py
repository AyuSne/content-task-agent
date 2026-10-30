"""Run fresh real end-to-end cases through separate CLI processes; assert outcomes, not exit codes alone."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checks_for(case: dict, facts: dict) -> dict:
    resources, progress = facts["resources"], facts["progress"]
    calls = [c["id"] for m in facts["messages"] if m["role"] == "assistant" for c in m["tool_calls"]]
    replies = [m["tool_call_id"] for m in facts["messages"] if m["role"] == "tool"]
    checks = {
        "expected_status": facts["status"] == case["expected_status"],
        "ledger_matches": resources["sim_spent_cents"] == sum(x["amount_cents"] for x in facts["ledger"]),
        "clock_matches": progress["sim_elapsed_ms"] == sum(x["duration_ms"] for x in facts["attempts"]),
        "rounds_bounded": progress["used_rounds"] <= progress["max_rounds"],
        "message_pairing": calls == replies and len(calls) == len(set(calls)),
        "real_only": resources["real_model_usage"]["mock_requests"] == 0,
        "requests_finished": all(r["status"] != "running" for r in facts["llm_requests"]),
    }
    if case.get("no_model_requests"):
        checks["no_model_requests"] = not facts["llm_requests"]
        checks["no_simulated_spend"] = resources["sim_spent_cents"] == 0
    if facts["input"]["budget_cents"] is not None:
        checks["within_budget"] = resources["sim_spent_cents"] <= facts["input"]["budget_cents"]
    if facts["input"]["deadline"] is not None:
        checks["within_deadline"] = datetime.fromisoformat(progress["sim_now"]) <= datetime.fromisoformat(
            facts["input"]["deadline"]
        )
    if facts["status"] == "complete":
        save, review = facts["save"], facts["review"]
        output = next(item for item in facts["outputs"]
                      if item["kind"] == "campaign" and item["version"] == save["output_version"])
        checks["current_verified_save"] = save["verified"] and save["output_version"] == output["version"]
        checks["current_review"] = review["status"] == "complete" and review["output_version"] == output["version"]
        checks["saved_hashes"] = all(
            Path(f["path"]).is_file() and hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest() == f["sha256"]
            for f in save["files"]
        )
        checks["requested_duration"] = output["content"]["script"]["duration_seconds"] == case["duration_s"]
    else:
        checks["not_falsely_saved"] = facts["save"] is None or not facts["save"]["verified"]
    return checks


def run_case(case: dict, defaults: dict, target: Path, resume_attempts: int) -> dict:
    target.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", CTA_SIM_START="2026-10-29T00:00:00+08:00")
    for key in ("CTA_MODEL_SCRIPT", "CTA_FAULT_CRASH", "CTA_FAIL_SEARCH", "CTA_FAIL_SAVE", "CTA_MAX_ROUNDS"):
        env.pop(key, None)
    inputs = {**defaults, **case}
    inputs["requirements"] = defaults["requirements"] + case["requirements"]
    manifest = {"case": case, "input": inputs, "commands": [], "started_unix": time.time()}
    write(target / "manifest.json", manifest)

    def cli(label: str, *args: str, fault=None):
        call_env = dict(env)
        if fault:
            call_env["CTA_FAULT_CRASH"] = fault
        argv = [sys.executable, "-m", "cta", "--mode", "real", "--data-dir", str(target / "data"), *args]
        start = time.time()
        process = subprocess.run(argv, cwd=ROOT, env=call_env, capture_output=True, text=True, encoding="utf-8", check=False)
        (target / f"{label}.json").write_text(process.stdout, encoding="utf-8")
        if process.stderr:
            (target / f"{label}.stderr.txt").write_text(process.stderr, encoding="utf-8")
        manifest["commands"].append({"label": label, "argv": argv, "exit_code": process.returncode,
                                     "wall_seconds": round(time.time() - start, 3), "fault": fault})
        write(target / "manifest.json", manifest)
        print(f"{case['id']}/{label}: exit={process.returncode}", flush=True)
        if process.returncode not in (0, 70):
            raise RuntimeError(f"CLI failed: {target / (label + '.json')}")
        return json.loads(process.stdout) if process.stdout.strip() else None, process.returncode

    argv = ["create", "--new", "--topic", inputs["topic"], "--budget", inputs["budget"],
            "--deadline", inputs["deadline"], "--audience", inputs["audience"]]
    for requirement in inputs["requirements"]:
        argv += ["--requirement", requirement]
    created, _ = cli("01-create", *argv)
    task_id = created["task_id"]
    result, code = cli("02-run", "run", task_id, fault=case.get("fault"))
    for index in range(resume_attempts):
        if code != 70 and result.get("run", {}).get("stop_reason") != "model_error":
            break
        cli(f"03-before-resume-{index}", "inspect", task_id)
        # Keep the requested crash armed until observed; connection errors do not count as injected crashes.
        observed = any(c["exit_code"] == 70 for c in manifest["commands"])
        result, code = cli(f"04-resume-{index}", "resume", task_id,
                           fault=case.get("fault") if not observed else None)
    facts, _ = cli("05-inspect", "inspect", task_id)
    cli("06-export", "export", task_id)
    checks = checks_for(case, facts)
    if case.get("fault"):
        checks["crash_observed"] = any(c["exit_code"] == 70 for c in manifest["commands"])
        before = next((json.loads((target / f"{c['label']}.json").read_text(encoding="utf-8"))
                       for c in manifest["commands"] if c["label"].startswith("03-before-resume")
                       and (target / f"{c['label']}.json").is_file()), None)
        if checks["crash_observed"] and before is not None:
            checks["recovery_did_not_duplicate_save"] = len([a for a in facts["attempts"] if a["kind"] == "save_campaign"]) == 1
    summary = {"case": case["id"], "task_id": task_id, "status": facts["status"], "reason": facts["reason"],
               "checks": checks, "passed": all(checks.values()), "progress": facts["progress"],
               "resources": facts["resources"], "evidence": str(target)}
    manifest.update({"finished_unix": time.time(), "task_id": task_id})
    write(target / "manifest.json", manifest)
    write(target / "result.json", summary)
    print(f"{case['id']}: {facts['status']} passed={summary['passed']}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "fixtures/eval/2026-10-29.v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", help="Run only the named case IDs")
    parser.add_argument("--resume-attempts", type=int, default=2,
                        help="Visible CLI resumes after model connection errors or the requested crash; no new task quota")
    args = parser.parse_args()
    suite = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.case and set(args.case) - {c["id"] for c in suite["cases"]}:
        parser.error("--case contains an unknown case ID")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"version": suite["version"], "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "results": []}
    for case in suite["cases"]:
        if args.case and case["id"] not in args.case:
            continue
        result["results"].append(run_case(case, suite["defaults"], args.output / case["id"], args.resume_attempts))
        write(args.output / "summary.json", result)
    return 0 if all(r["passed"] for r in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
