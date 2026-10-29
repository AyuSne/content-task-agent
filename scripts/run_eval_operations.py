"""Probe duplicate, idempotent run, edited-file preservation/repair and resource reuse on a fresh saved eval task."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.case_dir = args.case_dir.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.case_dir / "manifest.json").read_text(encoding="utf-8"))
    task_id = manifest["task_id"]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    commands = []

    def cli(label, *arguments):
        argv = [sys.executable, "-m", "cta", "--mode", "real", "--data-dir", str(args.case_dir / "data"), *arguments]
        process = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=False)
        (args.output / f"{label}.json").write_text(process.stdout, encoding="utf-8")
        if process.stderr:
            (args.output / f"{label}.stderr.txt").write_text(process.stderr, encoding="utf-8")
        commands.append({"label": label, "arguments": arguments, "exit_code": process.returncode})
        (args.output / "commands.json").write_text(json.dumps(commands, ensure_ascii=False, indent=2), encoding="utf-8")
        assert process.returncode == 0, (label, process.returncode)
        return json.loads(process.stdout)

    before = cli("01-before", "inspect", task_id)
    assert before["status"] == "complete"
    inputs = manifest["input"]
    arguments = ["create", "--topic", inputs["topic"], "--audience", inputs["audience"],
                 "--budget", inputs["budget"], "--deadline", inputs["deadline"]]
    for requirement in inputs["requirements"]:
        arguments += ["--requirement", requirement]
    default_task = cli("02-default-request", *arguments)
    duplicate = cli("02-duplicate", *arguments)
    assert duplicate["task_id"] == default_task["task_id"] and duplicate["duplicate"]
    assert default_task["task_id"] != task_id  # The evaluated delivery was explicitly created with --new.
    cli("03-idempotent-run", "run", task_id)
    cli("04-idempotent-resume", "resume", task_id)
    campaign = next(Path(f["path"]) for f in before["save"]["files"] if Path(f["path"]).name.startswith("campaign-v"))
    original = campaign.read_bytes()
    edited = original + "\n运营未导入批注：请保留此编辑，直到明确要求修复。\n".encode()
    campaign.write_bytes(edited)
    (args.output / "operator-edited-preserved.md").write_bytes(edited)
    cli("05-export-preserves-edit", "export", task_id)
    cli("06-export-still-preserves-edit", "export", task_id)
    assert campaign.read_bytes() == edited
    invalid = cli("07-after-edit", "inspect", task_id)
    assert invalid["status"] != "complete"
    cli("08-explicit-repair", "export", task_id, "--repair")
    repaired = cli("09-reconcile", "reconcile", task_id)
    assert repaired["status"] == "complete" and repaired["save"]["verified"]
    assert campaign.read_bytes() == original
    updated = cli("10-resource-update", "update", task_id, "--budget", "21")
    assert updated["status"] == "complete" and updated["input"]["version"] == before["input"]["version"] + 1
    cli("11-current-resource-export", "export", task_id)
    after = cli("12-after", "inspect", task_id)
    assert after["status"] == "complete"
    assert after["outputs"] == before["outputs"]
    assert after["resources"]["model_requests"] == before["resources"]["model_requests"]
    assert after["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]
    assert after["progress"]["used_rounds"] == before["progress"]["used_rounds"]
    assert all(hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest() == f["sha256"] for f in after["save"]["files"])
    result = {"task_id": task_id, "checks": {key: True for key in (
        "duplicate_reuses_default_task", "explicit_new_stays_independent", "run_resume_do_not_repeat_work", "exports_preserve_operator_edit",
        "edited_save_not_falsely_complete", "explicit_repair_restores_authority", "resource_update_reuses_content",
        "no_new_model_request", "no_new_simulated_charge", "rounds_preserved", "saved_hashes_match",
    )}, "passed": True}
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
