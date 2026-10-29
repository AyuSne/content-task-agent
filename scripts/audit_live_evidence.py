"""Verify real task evidence against public state, files and accounting facts."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from cta.config import load_config
from cta.export import export_task
from cta.save import reconcile_task
from cta.service import TaskService


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audit(folder):
    created = json.loads((folder / "01-create.json").read_text(encoding="utf-8"))
    task_id = created["task_id"]
    config = load_config("real", folder / "data")
    service = TaskService(config)
    before = service.inspect(task_id)
    if before["status"] == "complete":
        reconciliation = reconcile_task(config, task_id)
        write(folder / "audit-reconcile.json", reconciliation)
    exported = export_task(config, task_id)
    write(folder / "audit-export.json", exported)
    current = service.inspect(task_id)
    write(folder / "audited-inspect.json", current)
    spent = current["resources"]["sim_spent_cents"]
    checks = {
        "simulation_ledger_matches": spent == sum(item["amount_cents"] for item in current["ledger"]),
        "simulation_time_matches": current["progress"]["sim_elapsed_ms"] == sum(a["duration_ms"] for a in current["attempts"]),
        "audit_did_not_charge": spent == before["resources"]["sim_spent_cents"],
        "audit_did_not_call_model": len(current["llm_requests"]) == len(before["llm_requests"]),
        "round_limit_preserved": current["progress"]["used_rounds"] <= current["progress"]["max_rounds"],
        "no_mock_requests": current["resources"]["real_model_usage"]["mock_requests"] == 0,
    }
    calls = [call["id"] for m in current["messages"] if m["role"] == "assistant" for call in m["tool_calls"]]
    replies = [m["tool_call_id"] for m in current["messages"] if m["role"] == "tool"]
    checks["main_message_pairing"] = calls == replies
    checks["unique_call_ids"] = len(calls) == len(set(calls))
    checks["no_running_request"] = all(r["status"] != "running" for r in current["llm_requests"])
    if current["status"] == "complete":
        save = current["save"]
        checks["verified_current_save"] = save["verified"] and save["output_version"] == current["outputs"][-1]["version"]
        checks["all_delivery_hashes_match"] = all(
            Path(item["path"]).is_file() and hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
            for item in save["files"]
        )
        checks["within_simulated_budget"] = spent <= current["input"]["budget_cents"]
        checks["within_simulated_deadline"] = (datetime.fromisoformat(current["progress"]["sim_now"])
                                                <= datetime.fromisoformat(current["input"]["deadline"]))
        review = current["review"]
        checks["review_applies_to_current_version"] = (review["status"] == "complete"
                                                       and review["output_version"] == save["output_version"])
        trace = next(item for item in save["files"] if item["path"].endswith("trace.jsonl"))
        trace_saves = [json.loads(line) for line in Path(trace["path"]).read_text(encoding="utf-8").splitlines()
                       if json.loads(line).get("event") == "save"]
        latest_event = next(event for event in trace_saves if event["save_id"] == save["save_id"])
        checks["trace_save_matches_authority"] = latest_event["status"] == save["status"] and latest_event["verified"] == save["verified"]
    result = {
        "case": folder.name, "task_id": task_id, "status": current["status"], "checks": checks,
        "passed": all(checks.values()), "input": current["input"], "progress": current["progress"],
        "resources": current["resources"], "review": current["review"],
        "versions": current["versions"], "source_manifest": str(folder / "manifest.json"),
        "initial_scenario_result": json.loads((folder / "05-inspect.json").read_text(encoding="utf-8"))["status"],
    }
    write(folder / "audit.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("examples/repair-live"))
    args = parser.parse_args()
    results = [audit(path.parent) for path in sorted(args.root.glob("*/01-create.json"))]
    write(args.root / "audit-summary.json", results)
    print(json.dumps([{key: item[key] for key in ("case", "status", "passed")} for item in results], ensure_ascii=False, indent=2))
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
