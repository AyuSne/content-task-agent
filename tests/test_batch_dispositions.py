"""The main CLI handles one complete review's independent decisions atomically."""

import copy
import json

import pytest
from conftest import cli_json
from test_campaign_delivery_new import _campaign, _result
from test_campaign_draft import _tool
from test_review_save import REVIEW_FAIL


def _prepare(model_script, decisions):
    from test_review_dispositions_new import _reviewed

    review = copy.deepcopy(REVIEW_FAIL)
    review["findings"].append({
        **review["findings"][0], "finding_id": "F2", "severity": "optional",
        "issue": "可选表达建议。", "suggestion": "资源允许时考虑开场变化。",
    })
    task_id, prefix, script_ref, campaign_ref, review_id = _reviewed(model_script, review)
    target = {"review_id": review_id, "campaign_ref": campaign_ref}
    model_script(prefix + [_tool("record_dispositions", {**target, "decisions": decisions})])
    return task_id, cli_json("status", task_id), prefix, script_ref, campaign_ref, review_id


def _decisions():
    return [
        {"finding_id": "F1", "decision": "accepted", "reason": "M-001不支持该能力，须收窄字幕。"},
        {"finding_id": "F2", "decision": "rejected", "reason": "该建议为可选风格，当前开场已符合原要求，避免无益改动。",
         "evidence": [{"kind": "requirement", "input_version": 1, "field_path": "requirements[0]", "quote": "突出自动卡点"}]},
    ]


def test_main_records_two_independent_review_decisions_in_one_action(cli_env, model_script):
    task_id, before, _, _, _, review_id = _prepare(model_script, _decisions())
    after = cli_json("run", task_id, "--max-rounds", "1")
    inspected = cli_json("inspect", task_id)
    assert {item["finding_id"]: item["decision"] for item in inspected["dispositions"]} == {
        "F1": "accepted", "F2": "rejected",
    }
    result = json.loads(next(message["content"] for message in inspected["messages"]
                             if message.get("name") == "record_dispositions" and message["role"] == "tool"))
    assert result["ok"] is True
    assert result["review_id"] == review_id
    assert [item["reason"] for item in result["dispositions"]] == [item["reason"] for item in _decisions()]
    assert after["progress"]["used_rounds"] == before["progress"]["used_rounds"] + 1
    assert after["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]


@pytest.mark.parametrize("bad_item", [
    {"finding_id": "F999", "decision": "accepted", "reason": "不存在的意见"},
    {"finding_id": "F1", "decision": "rejected", "reason": "重复ID"},
    {"finding_id": "F2", "decision": "invalid", "reason": "非法处置"},
    {"finding_id": "F2", "decision": "accepted", "reason": " "},
])
def test_one_invalid_decision_rejects_whole_batch_without_writes_or_simulated_cost(cli_env, model_script, bad_item):
    task_id, before, _, _, _, _ = _prepare(model_script, [_decisions()[0], bad_item])
    after = cli_json("run", task_id, "--max-rounds", "1")
    inspected = cli_json("inspect", task_id)
    assert inspected["dispositions"] == []
    result = json.loads(next(message["content"] for message in inspected["messages"]
                             if message.get("name") == "record_dispositions" and message["role"] == "tool"))
    assert result["ok"] is False
    assert result["error"] in ("invalid_arguments", "finding_not_found", "invalid_dispositions")
    assert after["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]


def test_batch_requires_a_complete_review_of_the_current_output(cli_env, model_script):
    task_id, before, prefix, script_ref, campaign_ref, review_id = _prepare(model_script, _decisions())
    model_script(prefix + [
        _tool("submit_campaign", {"script_ref": script_ref, "campaign": _campaign()}),
        _tool("record_dispositions", {"review_id": review_id, "campaign_ref": campaign_ref, "decisions": _decisions()}),
    ])
    cli_json("resume", task_id, "--max-rounds", "2")
    inspected = cli_json("inspect", task_id)
    assert inspected["dispositions"] == []
    result = _result(inspected, "record_dispositions")[0]
    assert result["error"] == "invalid_campaign_ref"
    assert inspected["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]
