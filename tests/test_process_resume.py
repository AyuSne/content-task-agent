"""Ticket 05: explicit cross-process resume after main-loop and generation interruption.

Seam: CLI in real separate processes against a real on-disk SQLite database.
Faults are injected only at system boundaries (model script, test-only env).
"""

import json
import shutil

from conftest import REPO_ROOT, cli_json, run_cli, run_cli_process, scripted_stage_rounds
from test_campaign_draft import (
    CAMPAIGN,
    _brief_then_draft_script,
    _brief_tool,
    _campaign_tool,
    _create,
    _generation_item,
    _run_until_draft,
    _tool,
)


def _run_process(*args, data_dir, script, crash=None):
    extra = {"CTA_MODEL_SCRIPT": str(script)}
    if crash is not None:
        extra["CTA_FAULT_CRASH"] = crash
    return run_cli_process(*args, data_dir=data_dir, extra_env=extra)


def _process_json(*args, data_dir, script, crash=None):
    if args[0] in ("run", "resume") and "--max-rounds" not in args:
        status = _process_json("status", args[1], data_dir=data_dir, script=script)
        cap = scripted_stage_rounds(script, status["progress"]["used_rounds"])
        args = (*args, "--max-rounds", str(cap))
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
    assert resumed["run"]["stop_reason"] == "injected_round_cap"
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


REVIEW_PASS = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "五项质量要求均满足；意见仅为可选改进。",
    "findings": [],
    "evidence_gaps": [],
}


def _review_item():
    return {"channel": "review", "type": "tool", "name": "report_review", "arguments": REVIEW_PASS}


def _reviewed_task(cli_env, model_script):
    task = _create()
    script = model_script(_brief_then_draft_script() + [_review_item()])
    _run_until_draft(task["task_id"])
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["progress"]["phase"] == "reviewed"
    assert reviewed["review"]["verdict"] == "pass"
    assert reviewed["resources"]["sim_spent_cents"] == 220
    return task, script


def test_resume_keeps_unknown_save_and_never_replays_it(cli_env, model_script):
    task, script = _reviewed_task(cli_env, model_script)
    data_dir = cli_env / "data"

    crashed = _run_process("save", task["task_id"], data_dir=data_dir, script=script, crash="save_after_record")
    assert crashed.returncode == 70, crashed.stderr

    before = cli_json("inspect", task["task_id"])
    assert before["save"]["status"] == "unknown"
    assert before["save"]["verified"] is False
    assert before["attempts"][-1]["kind"] == "save_campaign"
    assert before["attempts"][-1]["status"] == "running"
    assert before["resources"]["sim_spent_cents"] == 230
    assert before["progress"]["sim_elapsed_ms"] == 17000
    save_attempts = len([item for item in before["attempts"] if item["kind"] == "save_campaign"])
    requests_before = len(before["llm_requests"])

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["status"] == "incomplete"
    assert resumed["progress"]["phase"] == "save_unknown"
    assert "保存结果未知" in resumed["reason"]
    assert resumed["save"]["status"] == "unknown"
    assert resumed["save"]["verified"] is False
    assert resumed["resources"]["sim_spent_cents"] == 230
    assert resumed["progress"]["sim_elapsed_ms"] == 17000
    assert resumed["run"]["recovery"]["save_unknown"]["status"] == "unknown"

    after = cli_json("inspect", task["task_id"])
    assert len([item for item in after["attempts"] if item["kind"] == "save_campaign"]) == save_attempts
    assert after["attempts"][-1]["status"] == "interrupted"
    assert len(after["llm_requests"]) == requests_before

    code, out, _err = run_cli("save", task["task_id"])
    assert code == 4
    blocked = json.loads(out)
    assert blocked["save_error"]["code"] == "save_unknown"
    assert blocked["status"] == "incomplete"
    again = cli_json("inspect", task["task_id"])
    assert len([item for item in again["attempts"] if item["kind"] == "save_campaign"]) == save_attempts
    assert again["resources"]["sim_spent_cents"] == 230

    resumed_again = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed_again["progress"]["phase"] == "save_unknown"
    assert resumed_again["run"]["recovery"]["save_unknown"]["status"] == "unknown"
    final = cli_json("inspect", task["task_id"])
    assert len([item for item in final["attempts"] if item["kind"] == "save_campaign"]) == save_attempts


def test_resume_reuses_completed_review_and_continues_to_save(cli_env, model_script):
    task, script = _reviewed_task(cli_env, model_script)
    data_dir = cli_env / "data"
    before = cli_json("inspect", task["task_id"])
    review_id = before["reviews"][0]["review_id"]
    messages_before = len(before["messages"])
    requests_before = len(before["llm_requests"])
    attempts_before = len(before["attempts"])
    items = json.loads(script.read_text(encoding="utf-8"))
    script.write_text(json.dumps(items + [_tool("save_campaign", {})], ensure_ascii=False), encoding="utf-8")

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["progress"]["phase"] == "complete"
    assert resumed["status"] == "complete"
    assert resumed["review"]["review_id"] == review_id
    assert resumed["resources"]["sim_spent_cents"] == 230
    assert resumed["run"]["recovery"]["reused"]["review"]["review_id"] == review_id
    after = cli_json("inspect", task["task_id"])
    assert len(after["llm_requests"]) == requests_before + 1
    assert len(after["attempts"]) == attempts_before + 1
    assert len(after["reviews"]) == len(before["reviews"]) == 1
    assert len(after["messages"]) > messages_before
    assert after["messages"][:messages_before] == before["messages"]

    saved = _process_json("save", task["task_id"], data_dir=data_dir, script=script)
    assert saved["status"] == "complete"
    assert saved["save"]["verified"] is True
    assert saved["review"]["review_id"] == review_id
    assert saved["resources"]["sim_spent_cents"] == 230


def _copy_runtime_inputs(tmp_path):
    material_path = tmp_path / "catalog.json"
    shutil.copy(REPO_ROOT / "fixtures" / "materials" / "catalog.json", material_path)
    knowledge_dir = tmp_path / "knowledge"
    shutil.copytree(REPO_ROOT / "knowledge", knowledge_dir)
    payload = json.loads(material_path.read_text(encoding="utf-8"))
    for material in payload["materials"]:
        if material["id"] == "M-001":
            material["text"] += " 旧资料标记AAA"
    material_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = json.loads((knowledge_dir / "manifest.json").read_text(encoding="utf-8"))
    creation = next(item for item in manifest["docs"] if item["id"] == "campaign-creation")
    doc_path = knowledge_dir / creation["path"]
    doc_path.write_text(doc_path.read_text(encoding="utf-8") + "\n旧知识标记OLDK\n", encoding="utf-8")
    return material_path, knowledge_dir


def test_resume_uses_saved_material_and_knowledge_bodies_after_external_change(cli_env, model_script, monkeypatch):
    material_path, knowledge_dir = _copy_runtime_inputs(cli_env)
    monkeypatch.setenv("CTA_MATERIALS_PATH", str(material_path))
    monkeypatch.setenv("CTA_KNOWLEDGE_DIR", str(knowledge_dir))

    task = _create()
    script = model_script(_generation_script())
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["progress"]["phase"] == "brief_ready"

    crashed = _run_process("run", task["task_id"], data_dir=data_dir, script=script, crash="generation_after_start")
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json("inspect", task["task_id"])
    knowledge_before = before["knowledge"][0]
    materials_before = {item["material_id"]: item for item in before["material_snapshots"]}
    assert materials_before["M-001"]["version"] == "2026-10-29.v1"
    messages_before = before["messages"]

    payload = json.loads(material_path.read_text(encoding="utf-8"))
    for material in payload["materials"]:
        if material["id"] == "M-001":
            material["text"] = material["text"].replace("旧资料标记AAA", "新资料标记BBB")
    material_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = json.loads((knowledge_dir / "manifest.json").read_text(encoding="utf-8"))
    creation = next(item for item in manifest["docs"] if item["id"] == "campaign-creation")
    doc_path = knowledge_dir / creation["path"]
    doc_path.write_text(
        doc_path.read_text(encoding="utf-8").replace("旧知识标记OLDK", "新知识标记NEWK"),
        encoding="utf-8",
    )

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["progress"]["phase"] == "draft_ready"
    assert [item["tool"] for item in resumed["run"]["recovery"]["interrupted_calls"]] == ["generate_script"]

    after = cli_json("inspect", task["task_id"])
    assert after["messages"][: len(messages_before)] == messages_before
    assert after["knowledge"][0] == knowledge_before
    assert {item["material_id"]: item for item in after["material_snapshots"]}["M-001"] == materials_before["M-001"]
    generation_text = " ".join(
        message["content"] or "" for message in after["generator_messages"] if message["role"] != "assistant"
    )
    assert "旧知识标记OLDK" in generation_text
    assert "旧资料标记AAA" in generation_text
    assert "新知识标记NEWK" not in generation_text
    assert "新资料标记BBB" not in generation_text
    assert after["knowledge"][0]["chars"] == knowledge_before["chars"]
