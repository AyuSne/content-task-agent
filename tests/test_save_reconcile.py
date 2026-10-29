"""Ticket 08: save reconciliation, file repair and honest unknown states."""

import json
from pathlib import Path

from conftest import cli_json, run_cli_process
from test_review_save import REVIEW_PASS, _draft_ready, _review_item, _save


def _complete_task(model_script, cli_env):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    code, saved = _save(task["task_id"])
    assert code == 0
    assert saved["status"] == "complete"
    return task, saved


def test_reconcile_does_not_turn_explicit_failure_into_free_success(cli_env, model_script, monkeypatch):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    monkeypatch.setenv("CTA_FAIL_SAVE", "1")
    code, failed = _save(task["task_id"])
    assert code == 4
    assert failed["save"]["status"] == "failed"
    before = cli_json("inspect", task["task_id"])
    monkeypatch.delenv("CTA_FAIL_SAVE")
    reconciled = cli_json("reconcile", task["task_id"])
    assert reconciled["status"] == "incomplete"
    after = cli_json("inspect", task["task_id"])
    assert after["attempts"] == before["attempts"]
    assert after["save"]["status"] == "failed"


def test_export_repair_keeps_resource_infeasible_task_incomplete(cli_env, model_script):
    task, saved = _complete_task(model_script, cli_env)
    updated = cli_json("update", task["task_id"], "--budget", "2")
    assert updated["status"] == "incomplete"
    campaign = next(item for item in saved["save"]["files"] if item["path"].endswith("campaign-v1.json"))
    Path(campaign["path"]).unlink()
    reconciled = cli_json("reconcile", task["task_id"])
    assert reconciled["status"] == "incomplete"
    assert reconciled["resources"]["sim_spent_cents"] == 230


def test_export_repair_restores_missing_file_without_new_attempt(cli_env, model_script):
    task, saved = _complete_task(model_script, cli_env)
    report = next(item for item in saved["save"]["files"] if item["path"].endswith("execution-report.md"))
    Path(report["path"]).unlink()
    before = cli_json("inspect", task["task_id"])
    save_attempts = len([item for item in before["attempts"] if item["kind"] == "save_campaign"])
    spent = before["resources"]["sim_spent_cents"]

    repaired = cli_json("export", task["task_id"], "--repair")
    assert repaired["verified"] is True
    assert Path(report["path"]).is_file()
    inspected = cli_json("inspect", task["task_id"])
    assert len([item for item in inspected["attempts"] if item["kind"] == "save_campaign"]) == save_attempts
    assert inspected["resources"]["sim_spent_cents"] == spent
    latest = inspected["saves"][-1]
    assert latest["verified"] is True
    assert any(item["path"].endswith("execution-report.md") for item in latest["files"])


def test_reconcile_repairs_corrupted_export_from_authoritative_output(cli_env, model_script):
    task, saved = _complete_task(model_script, cli_env)
    campaign_file = next(item for item in saved["save"]["files"] if item["path"].endswith("campaign-v1.json"))
    Path(campaign_file["path"]).write_text("{\"corrupted\": true}\n", encoding="utf-8")
    before = cli_json("inspect", task["task_id"])
    attempts_before = len(before["attempts"])

    reconciled = cli_json("reconcile", task["task_id"])
    assert reconciled["status"] == "complete"
    assert reconciled["reconcile"]["verified"] is True
    assert reconciled["reconcile"]["completed"] is False  # already complete
    restored = json.loads(Path(campaign_file["path"]).read_text(encoding="utf-8"))
    output = cli_json("inspect", task["task_id"])["outputs"][-1]["content"]
    assert restored == output
    inspected = cli_json("inspect", task["task_id"])
    assert len(inspected["attempts"]) == attempts_before
    assert inspected["resources"]["sim_spent_cents"] == 230


def test_crash_after_save_record_reconciles_without_duplicate_business_attempt(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    data_dir = cli_env / "data"
    crashed = run_cli_process(
        "save", task["task_id"], data_dir=data_dir, extra_env={"CTA_FAULT_SAVE_AFTER_RECORD": "1"}
    )
    assert crashed.returncode == 70, crashed.stderr
    unknown = cli_json("status", task["task_id"])
    assert unknown["status"] == "in_progress"
    assert unknown["save"]["status"] == "unknown"
    assert unknown["save"]["verified"] is False

    reconciled = cli_json("reconcile", task["task_id"])
    assert reconciled["status"] == "complete"
    assert reconciled["reconcile"]["verified"] is True
    assert reconciled["save"]["verified"] is True
    assert reconciled["resources"]["sim_spent_cents"] == 230
    inspected = cli_json("inspect", task["task_id"])
    save_attempts = [item for item in inspected["attempts"] if item["kind"] == "save_campaign"]
    assert len(save_attempts) == 1
    assert save_attempts[0]["status"] == "success"


def test_reconcile_without_save_record_is_not_applicable(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    reconciled = cli_json("reconcile", task["task_id"])
    assert reconciled["status"] == "not_applicable"
    assert "不能据此声称完成" in reconciled["message"]


def test_resume_first_reconciles_unknown_save_without_new_model_or_business_attempt(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    crashed = run_cli_process("save", task["task_id"], data_dir=cli_env / "data",
                              extra_env={"CTA_FAULT_SAVE_AFTER_RECORD": "1"})
    assert crashed.returncode == 70
    before = cli_json("inspect", task["task_id"])
    resumed = cli_json("resume", task["task_id"])
    assert resumed["status"] == "complete"
    assert resumed["save"]["verified"] is True
    after = cli_json("inspect", task["task_id"])
    assert len(after["attempts"]) == len(before["attempts"])
    assert len(after["llm_requests"]) == len(before["llm_requests"])
    assert after["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]
