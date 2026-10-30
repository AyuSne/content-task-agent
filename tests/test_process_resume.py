"""Explicit cross-process resume after main-loop and business action interruption.

Seam: CLI in real separate processes against a real on-disk SQLite database.
Faults are injected only at system boundaries (model script, test-only env).
"""

import json
import shutil

from conftest import REPO_ROOT, cli_json, run_cli, run_cli_process, scripted_stage_rounds
from test_campaign_draft import (
    _brief_tool,
    _create,
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












def test_resume_after_round_cap_does_not_grant_new_rounds_or_charge(cli_env, model_script):
    task = _create()
    limit = task["progress"]["max_rounds"]
    script = model_script(
        [{"type": "text", "content": f"第 {index} 轮没有动作"} for index in range(limit)]
        + [_tool("search_materials", {"query": "自动卡点", "mode": "basic"})]
    )
    data_dir = cli_env / "data"
    first = _process_json("run", task["task_id"], data_dir=data_dir, script=script)
    assert first["status"] == "incomplete"
    assert first["progress"]["used_rounds"] == limit
    assert first["progress"]["phase"] == "stopped"
    assert first["progress"]["sim_elapsed_ms"] == 0
    assert first["resources"]["sim_spent_cents"] == 0
    assert "轮数上限" in first["reason"]
    before = cli_json("inspect", task["task_id"])
    assert len(before["llm_requests"]) == limit

    resumed = _process_json("resume", task["task_id"], data_dir=data_dir, script=script)
    assert resumed["status"] == "incomplete"
    assert resumed["run"]["stop_reason"] == "max_rounds"
    assert resumed["progress"]["used_rounds"] == limit
    assert resumed["progress"]["sim_elapsed_ms"] == 0
    assert resumed["resources"]["sim_spent_cents"] == 0
    assert "新额度" in resumed["reason"]
    after = cli_json("inspect", task["task_id"])
    assert len(after["llm_requests"]) == limit
    assert len(after["attempts"]) == 0
    assert len(after["messages"]) == len(before["messages"]) + 2
    assert after["recoveries"][-1]["summary"]["prior_status"] == "incomplete"
    assert after["recoveries"][-1]["summary"]["used_rounds"] == limit


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
    assert [message["role"] for message in inspected["messages"]] == ["system", "system", "system", "user", "user", "user"]
    assert any(message["content"].startswith("[适用费用能力概览]") for message in inspected["messages"])
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




REVIEW_PASS = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "五项质量要求均满足；意见仅为可选改进。",
    "findings": [],
    "evidence_gaps": [],
}










def _copy_runtime_inputs(tmp_path):
    material_path = tmp_path / "catalog.json"
    shutil.copy(REPO_ROOT / "data" / "materials" / "catalog.json", material_path)
    knowledge_dir = tmp_path / "knowledge"
    shutil.copytree(REPO_ROOT / "knowledge", knowledge_dir)
    shutil.copytree(REPO_ROOT / ".agents/skills", tmp_path / ".agents/skills")
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




def test_resume_recovers_exact_campaign_snapshot_without_new_attempt(cli_env, model_script):
    from test_campaign_delivery_new import _campaign, _script_ready
    task, prefix, script_ref = _script_ready(model_script)
    path = model_script(prefix + [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})])
    crashed = _run_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                           script=path, crash='after_tool_effect')
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json('inspect', task['task_id'])
    pending = _pending_call(before)
    assert pending['function']['name'] == 'submit_campaign'
    resumed = _run_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data', script=path)
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = json.loads(_tool_result(after, pending['id'])['content'])
    assert result['ok'] and result['recovered']
    campaign = next(o for o in after['outputs'] if o['kind'] == 'campaign')
    assert result['campaign_ref'] == {'id': campaign['output_id'], 'version': campaign['version']}
    assert campaign['content']['script_ref'] == script_ref
    assert after['outputs'] == before['outputs'] and after['attempts'] == before['attempts']
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_resume_keeps_unverifiable_save_unknown_without_replay(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json('review', task['task_id'])
    crashed = run_cli_process('save', task['task_id'], data_dir=cli_env / 'data',
                              extra_env={'CTA_FAULT_CRASH': 'save_after_record'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    assert before['save']['status'] == 'unknown' and not before['save']['verified']
    target_dir = cli_env / 'data' / 'mock' / 'exports' / task['task_id']
    target_dir.write_text('external file blocks the export directory', encoding='utf-8')
    result = cli_json('resume', task['task_id'], '--max-rounds', '0')
    assert result['status'] == 'incomplete' and result['save']['status'] == 'unknown'
    assert not result['save']['verified'] and result['run']['recovery']['save_unknown']
    after = cli_json('inspect', task['task_id'])
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    assert after['progress']['sim_elapsed_ms'] == before['progress']['sim_elapsed_ms']
    assert len(after['llm_requests']) == len(before['llm_requests'])
    code, out, _ = run_cli('save', task['task_id'])
    assert code == 4 and json.loads(out)['save_error']['code'] == 'save_unknown'
    assert target_dir.read_text(encoding='utf-8') == 'external file blocks the export directory'


def test_resume_reuses_completed_review_and_saves_same_campaign(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    reviewed = cli_json('review', task['task_id'])
    before = cli_json('inspect', task['task_id'])
    review_id = reviewed['review']['review_id']
    output = next(item for item in before['outputs'] if item['kind'] == 'campaign')
    ref = {'id': output['output_id'], 'version': output['version']}
    path = model_script.path if hasattr(model_script, 'path') else None
    # The fixture exposes the script path through the environment.
    import os
    from pathlib import Path
    path = Path(os.environ['CTA_MODEL_SCRIPT'])
    items = json.loads(path.read_text(encoding='utf-8'))
    model_script(items + [_tool('save_campaign', {'campaign_ref': ref})])
    result = cli_json('resume', task['task_id'], '--max-rounds', '1')
    assert result['status'] == 'complete' and result['save']['verified']
    assert result['review']['review_id'] == review_id
    assert result['run']['recovery']['reused']['review']['review_id'] == review_id
    after = cli_json('inspect', task['task_id'])
    assert len(after['reviews']) == len(before['reviews']) == 1
    assert len(after['attempts']) == len(before['attempts']) + 1
    assert after['messages'][:len(before['messages'])] == before['messages']
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents'] + 10
