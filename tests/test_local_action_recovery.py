"""Recover local action receipts through real new CLI processes, without replay."""

import copy
import json

import pytest
from conftest import cli_json, run_cli_process
from test_campaign_draft import _brief_then_draft_script, _create, _run_until_draft, _tool
from test_process_resume import _pending_call, _tool_result
from test_review_save import REVIEW_FAIL, _review_item


def _prepare(tool, model_script):
    if tool in ("record_disposition", "record_dispositions"):
        report = copy.deepcopy(REVIEW_FAIL)
        report["findings"].append({**report["findings"][0], "finding_id": "F2", "severity": "optional"})
        decisions = [
            {"finding_id": "F1", "decision": "accepted", "reason": "按M-001收窄表述。"},
            {"finding_id": "F2", "decision": "rejected", "reason": "可选风格建议，原稿已满足要求。"},
        ]
        arguments = decisions[0] if tool == "record_disposition" else {"decisions": decisions}
        script = model_script([*_brief_then_draft_script(), _review_item(report), _tool(tool, arguments)])
        task_id = _create()["task_id"]
        _run_until_draft(task_id)
        cli_json("review", task_id)
    else:
        arguments = {"search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 0}
        if tool == "choose_strategy":
            arguments.update(direction="functional_demo", rationale="资料足够，采用功能演示。")
        script = model_script([_tool(tool, arguments)])
        task_id = _create()["task_id"]
    return task_id, script


@pytest.mark.parametrize("tool", ["record_disposition", "record_dispositions", "compare_strategies", "choose_strategy"])
@pytest.mark.parametrize("point", ["after_tool_effect", "after_tool_request"])
def test_local_action_receipt_is_verified_without_replaying_or_charging(cli_env, model_script, tool, point):
    task_id, script = _prepare(tool, model_script)
    crashed = run_cli_process(
        "run", task_id, "--max-rounds", "1", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": point},
    )
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json("inspect", task_id)
    pending = _pending_call(before)
    assert pending["function"]["name"] == tool

    resumed = run_cli_process(
        "resume", task_id, "--max-rounds", "0", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert resumed.returncode == 0, resumed.stderr
    document = json.loads(resumed.stdout)
    after = cli_json("inspect", task_id)
    receipt = json.loads(_tool_result(after, pending["id"])["content"])
    assert receipt["recovered"] is True
    if point == "after_tool_effect":
        assert receipt["ok"] is True
        assert [item["tool"] for item in document["run"]["recovery"]["recovered_calls"]] == [tool]
        if tool.startswith("record_disposition"):
            assert receipt["review_id"] == before["reviews"][-1]["review_id"]
            assert len(receipt["dispositions"]) == (1 if tool == "record_disposition" else 2)
        elif tool == "compare_strategies":
            assert receipt["estimate_id"] == before["estimates"][-1]["estimate_id"]
        else:
            assert receipt["selection_id"] == before["strategy_selections"][-1]["selection_id"]
    else:
        assert receipt["ok"] is False
        assert receipt["error"] == "local_action_interrupted"
        assert [item["tool"] for item in document["run"]["recovery"]["interrupted_calls"]] == [tool]
    for field in ("dispositions", "estimates", "strategy_selections", "ledger", "attempts"):
        assert after[field] == before[field]
    assert after["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]
    assert after["progress"]["used_rounds"] == before["progress"]["used_rounds"]
    calls = [call["id"] for message in after["messages"] if message["role"] == "assistant" for call in message["tool_calls"]]
    results = [message["tool_call_id"] for message in after["messages"] if message["role"] == "tool"]
    assert calls == results
