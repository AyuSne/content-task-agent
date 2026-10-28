"""Ticket 05: explicit cross-process resume after main-loop and generation interruption.

Seam: CLI in real separate processes against a real on-disk SQLite database.
Faults are injected only at system boundaries (model script, test-only env).
"""

import json

from conftest import cli_json, run_cli_process
from test_campaign_draft import _brief_tool, _create, _tool


def _run_process(*args, data_dir, script, crash=None):
    extra = {"CTA_MODEL_SCRIPT": str(script)}
    if crash is not None:
        extra["CTA_FAULT_CRASH"] = crash
    return run_cli_process(*args, data_dir=data_dir, extra_env=extra)


def _process_json(*args, data_dir, script, crash=None):
    result = _run_process(*args, data_dir=data_dir, script=script, crash=crash)
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    return json.loads(result.stdout)


def _pending_call(inspected):
    answered = {
        message["tool_call_id"]
        for message in inspected["messages"]
        if message["role"] == "tool" and message["tool_call_id"]
    }
    for message in inspected["messages"]:
        if message["role"] == "assistant":
            for call in message["tool_calls"]:
                if call["id"] not in answered:
                    return call
    return None


def _tool_result(inspected, call_id):
    return next(
        message
        for message in inspected["messages"]
        if message["role"] == "tool" and message["tool_call_id"] == call_id
    )


def test_resume_after_missing_receipt_appends_matched_result_and_continues(cli_env, model_script):
    task = _create()
    script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
        ]
    )
    data_dir = cli_env / "data"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="after_tool_request")
    assert crashed.returncode == 70, crashed.stderr

    before = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    pending = _pending_call(before)
    assert pending is not None
    assert pending["function"]["name"] == "search_materials"
    assert before["attempts"] == []
    assert before["resources"]["sim_spent_cents"] == 0

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["progress"]["phase"] == "brief_ready"
    assert resumed["progress"]["used_rounds"] == 3
    assert resumed["resources"]["sim_spent_cents"] == 20
    assert resumed["run"]["explicit_resume"] is True
    recovery = resumed["run"]["recovery"]
    assert recovery["pending_calls"] == 1
    assert [item["tool"] for item in recovery["interrupted_calls"]] == ["search_materials"]

    after = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    interrupted = _tool_result(after, pending["id"])
    assert "interrupted" in interrupted["content"]
    assert interrupted["name"] == "search_materials"
    # The resumed model action produced a real, charged search result after the
    # interrupted call; the legal assistant/tool pairing is preserved.
    results = [
        json.loads(message["content"])
        for message in after["messages"]
        if message["role"] == "tool" and message["name"] == "search_materials"
    ]
    assert [item["ok"] for item in results] == [False, True]
    assert after["attempts"][0]["kind"] == "search_materials"
    assert after["attempts"][0]["status"] == "success"


def test_resume_reuses_completed_search_attempt_without_double_charge(cli_env, model_script):
    task = _create()
    script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
        ]
    )
    data_dir = cli_env / "data"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="after_tool_effect")
    assert crashed.returncode == 70, crashed.stderr

    before = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    pending = _pending_call(before)
    assert pending is not None
    assert len(before["attempts"]) == 1
    attempt_before = before["attempts"][0]
    ledger_before = before["ledger"]
    assert attempt_before["status"] == "success"
    assert attempt_before["cost_cents"] == 20
    assert attempt_before["sim_start_ms"] == 0
    assert attempt_before["sim_end_ms"] == 2000
    assert before["resources"]["sim_spent_cents"] == 20
    assert not any(
        message["role"] == "tool" and message["tool_call_id"] == pending["id"]
        for message in before["messages"]
    )

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["progress"]["phase"] == "brief_ready"
    assert resumed["progress"]["used_rounds"] == 2
    assert resumed["resources"]["sim_spent_cents"] == 20
    assert resumed["run"]["recovery"]["recovered_calls"][0]["tool"] == "search_materials"

    after = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    assert len(after["attempts"]) == 1
    assert after["attempts"][0] == attempt_before
    assert after["ledger"] == ledger_before
    recovered = json.loads(_tool_result(after, pending["id"])["content"])
    assert recovered["ok"] is True
    assert recovered["recovered"] is True
    assert recovered["attempt_id"] == attempt_before["id"]
    assert recovered["cost_cents"] == 20
    assert recovered["duration_ms"] == 2000
