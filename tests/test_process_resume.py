"""Ticket 05: explicit cross-process resume after main-loop and generation interruption.

Seam: CLI in real separate processes against a real on-disk SQLite database.
Faults are injected only at system boundaries (model script, test-only env).
"""

import json

from conftest import cli_json, run_cli_process
from test_campaign_draft import (
    CAMPAIGN,
    _brief_then_draft_script,
    _campaign_tool,
    _generation_item,
    _tool,
    _brief_tool,
    _create,
)


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


def _generation_script():
    return [
        _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
        _brief_tool(),
        _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
        _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
        _campaign_tool(),
        _generation_item(),
    ]


def test_interrupted_generation_retries_only_the_bounded_step_with_a_new_attempt(cli_env, model_script):
    task = _create()
    script = model_script(_generation_script())
    data_dir = cli_env / "data"

    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["progress"]["phase"] == "brief_ready"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="generation_after_start")
    assert crashed.returncode == 70, crashed.stderr

    before = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    assert [attempt["kind"] for attempt in before["attempts"]] == ["search_materials", "generate_script"]
    assert before["attempts"][-1]["status"] == "running"
    assert before["generator_messages_count"] == 0
    assert before["resources"]["sim_spent_cents"] == 140
    assert before["outputs"] == []

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["progress"]["phase"] == "draft_ready"
    assert resumed["progress"]["used_rounds"] == 5
    assert resumed["resources"]["sim_spent_cents"] == 260
    recovery = resumed["run"]["recovery"]
    assert [item["tool"] for item in recovery["interrupted_calls"]] == ["generate_script"]
    assert recovery["reused"]["brief_version"] == 1
    assert recovery["reused"]["search_results"] == 1

    after = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    assert [attempt["kind"] for attempt in after["attempts"]] == ["search_materials", "generate_script", "generate_script"]
    assert [attempt["status"] for attempt in after["attempts"]] == ["success", "interrupted", "success"]
    assert [row["amount_cents"] for row in after["ledger"]] == [20, 120, 120]
    generation_requests = [row for row in after["llm_requests"] if row["purpose"] == "script_generation"]
    assert len(generation_requests) == 1
    assert after["generator_messages_count"] == 3
    assert [output["version"] for output in after["outputs"]] == [1]
    assert len([item for item in after["messages"] if item["name"] == "search_materials"]) == 1


def test_complete_generation_candidate_is_recovered_without_regeneration(cli_env, model_script):
    task = _create()
    script = model_script(_generation_script())
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["progress"]["phase"] == "brief_ready"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="generation_after_response")
    assert crashed.returncode == 70, crashed.stderr

    before = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    assert before["attempts"][-1]["status"] == "running"
    assert before["generator_messages_count"] == 3
    generation_request = next(row for row in before["llm_requests"] if row["purpose"] == "script_generation")
    attempt_before = before["attempts"][-1]

    # The resume model script has no second generate_script action: recovery must
    # supply the missing result and the loop must go straight to the campaign.
    resume_script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _campaign_tool(),
            _generation_item(),
        ]
    )
    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=resume_script)
    assert resumed["progress"]["phase"] == "draft_ready"
    assert resumed["progress"]["used_rounds"] == 4
    assert resumed["resources"]["sim_spent_cents"] == 140
    assert [item["tool"] for item in resumed["run"]["recovery"]["recovered_calls"]] == ["generate_script"]

    after = _process_json("inspect", task["task_id"], data_dir=data_dir, script=resume_script)
    assert len(after["attempts"]) == 2
    recovered_attempt = after["attempts"][-1]
    assert recovered_attempt["id"] == attempt_before["id"]
    assert recovered_attempt["status"] == "success"
    assert recovered_attempt["cost_cents"] == attempt_before["cost_cents"]
    assert recovered_attempt["sim_start_ms"] == attempt_before["sim_start_ms"]
    assert recovered_attempt["sim_end_ms"] == attempt_before["sim_end_ms"]
    assert recovered_attempt["real_started_at"] == attempt_before["real_started_at"]
    assert after["attempts"][-1]["status"] == "success"
    generation_requests = [row for row in after["llm_requests"] if row["purpose"] == "script_generation"]
    assert len(generation_requests) == 1
    assert generation_requests[0]["id"] == generation_request["id"]
    assert after["generator_messages_count"] == 3
    assert [output["version"] for output in after["outputs"]] == [1]


def test_incomplete_generation_output_stays_traceable_and_is_not_used_as_a_draft(cli_env, model_script):
    import copy

    from test_campaign_draft import SCRIPT_CANDIDATE

    task = _create()
    bad_candidate = copy.deepcopy(SCRIPT_CANDIDATE)
    bad_candidate["claims"][0]["quote"] = "材料里没有这句话"
    script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _generation_item(bad_candidate),
        ]
    )
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["progress"]["phase"] == "brief_ready"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="generation_after_response")
    assert crashed.returncode == 70, crashed.stderr

    resume_script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _campaign_tool(),
            _generation_item(bad_candidate),
            _generation_item(),
        ]
    )
    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=resume_script)
    assert resumed["progress"]["phase"] == "draft_ready"
    assert [item["tool"] for item in resumed["run"]["recovery"]["interrupted_calls"]] == ["generate_script"]

    after = _process_json("inspect", task["task_id"], data_dir=data_dir, script=resume_script)
    assert [attempt["status"] for attempt in after["attempts"]] == ["success", "interrupted", "success"]
    assert after["generator_messages_count"] == 6
    first_response = next(
        message
        for message in after["generator_messages"]
        if message["role"] == "assistant" and message["candidate"] == 1
    )
    assert "材料里没有这句话" in first_response["content"]
    assert [output["version"] for output in after["outputs"]] == [1]
    requests = [row for row in after["llm_requests"] if row["purpose"] == "script_generation"]
    assert len(requests) == 2


def test_pause_freezes_sim_clock_and_explicit_resume_continues_saved_progress(cli_env, model_script):
    task = _create()
    script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _campaign_tool(),
            _generation_item(),
        ]
    )
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["progress"]["phase"] == "brief_ready"
    assert first["progress"]["sim_elapsed_ms"] == 2000
    assert first["progress"]["used_rounds"] == 2
    assert first["resources"]["sim_spent_cents"] == 20

    paused = cli_json("pause", task["task_id"])
    assert paused["progress"]["paused"] is True
    assert paused["progress"]["sim_elapsed_ms"] == 2000
    assert paused["progress"]["used_rounds"] == 2
    assert paused["resources"]["sim_spent_cents"] == 20
    paused_inspect = cli_json("inspect", task["task_id"])
    paused_messages = len(paused_inspect["messages"])
    paused_requests = len(paused_inspect["llm_requests"])
    paused_attempts = len(paused_inspect["attempts"])
    plan_before = paused_inspect["todos"]

    still_paused = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert still_paused["run"]["stop_reason"] == "paused"
    assert still_paused["progress"]["paused"] is True
    assert still_paused["progress"]["sim_elapsed_ms"] == 2000
    assert still_paused["progress"]["used_rounds"] == 2
    assert still_paused["resources"]["sim_spent_cents"] == 20
    after_paused_run = cli_json("inspect", task["task_id"])
    assert len(after_paused_run["messages"]) == paused_messages
    assert len(after_paused_run["llm_requests"]) == paused_requests
    assert len(after_paused_run["attempts"]) == paused_attempts

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["progress"]["paused"] is False
    assert resumed["progress"]["phase"] == "draft_ready"
    assert resumed["progress"]["sim_elapsed_ms"] == 10000
    assert resumed["progress"]["used_rounds"] == 4
    assert resumed["resources"]["sim_spent_cents"] == 140
    assert resumed["resources"]["remaining_cents"] == 1000 - 140
    assert resumed["input"]["version"] == 1
    assert resumed["input"]["budget"] == "10.00"
    assert resumed["input"]["deadline"] == "2026-10-30T04:00:00+00:00"
    assert plan_before == [
        {"step": "campaign_draft", "title": "完整活动方案草稿", "status": "current"},
        {"step": "review", "title": "内容核验", "status": "pending"},
        {"step": "save", "title": "核实保存与导出", "status": "pending"},
    ]
    assert resumed["todos"] == [
        {"step": "review", "title": "内容核验", "status": "current"},
        {"step": "save", "title": "核实保存与导出", "status": "pending"},
    ]


def test_resume_after_round_cap_does_not_grant_new_rounds_or_charge(cli_env, model_script):
    task = _create()
    script = model_script(
        [{"type": "text", "content": f"第 {index} 轮没有动作"} for index in range(12)]
        + [_tool("search_materials", {"query": "自动卡点", "mode": "basic"})]
    )
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["status"] == "incomplete"
    assert first["progress"]["used_rounds"] == 12
    assert first["progress"]["phase"] == "stopped"
    assert first["progress"]["sim_elapsed_ms"] == 0
    assert first["resources"]["sim_spent_cents"] == 0
    assert "轮数上限" in first["reason"]
    before = cli_json("inspect", task["task_id"])
    assert len(before["llm_requests"]) == 12

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["status"] == "incomplete"
    assert resumed["run"]["stop_reason"] == "max_rounds"
    assert resumed["progress"]["used_rounds"] == 12
    assert resumed["progress"]["sim_elapsed_ms"] == 0
    assert resumed["resources"]["sim_spent_cents"] == 0
    assert "新额度" in resumed["reason"]
    after = cli_json("inspect", task["task_id"])
    assert len(after["llm_requests"]) == 12
    assert len(after["attempts"]) == 0
    assert len(after["messages"]) == len(before["messages"]) + 2
    assert after["recoveries"][-1]["summary"]["prior_status"] == "incomplete"
    assert after["recoveries"][-1]["summary"]["used_rounds"] == 12


def test_model_unavailable_preserves_progress_and_explicit_resume_retries(cli_env, model_script, monkeypatch):
    monkeypatch.delenv("CTA_MODEL_SCRIPT", raising=False)
    task = _create()
    first = cli_json("run", task["task_id"])
    assert first["status"] == "incomplete"
    assert first["run"]["stop_reason"] == "model_unavailable"
    assert "模型不可用" in first["reason"]
    assert first["progress"]["used_rounds"] == 0
    assert first["progress"]["sim_elapsed_ms"] == 0
    assert first["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    assert [message["role"] for message in inspected["messages"]] == ["system", "system", "user", "user"]
    assert inspected["messages"][-1]["content"].startswith("[状态快照]")
    assert inspected["llm_requests"] == []
    assert inspected["attempts"] == []

    script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
        ]
    )
    resumed = _process_json("resume", task["task_id"], data_dir=cli_env / "data", script=script)
    assert resumed["progress"]["phase"] == "brief_ready"
    assert resumed["progress"]["used_rounds"] == 2
    assert resumed["resources"]["sim_spent_cents"] == 20
    recovery = resumed["run"]["recovery"]
    assert recovery["prior_status"] == "incomplete"
    assert "模型不可用" in recovery["prior_reason"]
    after = cli_json("inspect", task["task_id"])
    assert len(after["llm_requests"]) == 2
    assert any("[恢复事实]" in (message["content"] or "") for message in after["messages"])


def test_resume_after_model_request_error_does_not_hide_retry(cli_env, model_script):
    task = _create()
    script = model_script(
        [
            {"type": "error", "message": "upstream 503"},
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _brief_tool(),
        ]
    )
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["status"] == "incomplete"
    assert first["progress"]["used_rounds"] == 1
    assert first["run"]["stop_reason"] == "model_error"
    before = cli_json("inspect", task["task_id"])
    assert len(before["llm_requests"]) == 1
    assert before["llm_requests"][0]["status"] == "error"
    assert len(before["messages"]) > 0

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["status"] == "in_progress"
    assert resumed["progress"]["phase"] == "brief_ready"
    assert resumed["progress"]["used_rounds"] == 3
    assert resumed["resources"]["sim_spent_cents"] == 20
    assert "503" in resumed["run"]["recovery"]["prior_reason"]
    after = cli_json("inspect", task["task_id"])
    assert len(after["llm_requests"]) == 3
    assert after["messages"][: len(before["messages"])] == before["messages"]


def test_resume_recovers_campaign_effect_written_before_tool_receipt(cli_env, model_script):
    task = _create()
    script = model_script(_brief_then_draft_script())
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["progress"]["phase"] == "brief_ready"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="after_campaign_effect")
    assert crashed.returncode == 70, crashed.stderr

    before = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    assert before["progress"]["phase"] == "draft_ready"
    assert [output["version"] for output in before["outputs"]] == [1]
    pending = _pending_call(before)
    assert pending is not None
    assert pending["function"]["name"] == "propose_campaign"
    assert len(before["attempts"]) == 2

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["run"]["stop_reason"] == "draft_ready"
    assert resumed["progress"]["phase"] == "draft_ready"
    assert resumed["resources"]["sim_spent_cents"] == 140
    assert [item["tool"] for item in resumed["run"]["recovery"]["recovered_calls"]] == ["propose_campaign"]

    after = _process_json("inspect", task["task_id"], data_dir=data_dir, script=script)
    assert [output["version"] for output in after["outputs"]] == [1]
    assert len(after["attempts"]) == 2
    result = json.loads(_tool_result(after, pending["id"])["content"])
    assert result["ok"] is True
    assert result["recovered"] is True
    assert result["output_version"] == 1
    assert CAMPAIGN["activity"]["theme"] in after["outputs"][0]["content_md"]
