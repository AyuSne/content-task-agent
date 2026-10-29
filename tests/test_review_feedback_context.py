"""Re-review retains findings and dispositions through the public task CLI."""

import copy
import json

from conftest import cli_json
from test_campaign_draft import (
    _brief_then_draft_script,
    _campaign_tool,
    _create,
    _generation_item,
    _run_until_draft,
    _tool,
)
from test_revision_disposition import REVIEW_FAIL, REVIEW_PASS_V2, _review_item


def test_review_receives_existing_evidence_for_brief_and_campaign(cli_env, model_script):
    model_script(_brief_then_draft_script() + [_review_item(REVIEW_PASS_V2)])
    task_id = _create()["task_id"]
    _run_until_draft(task_id)
    cli_json("review", task_id)
    inspected = cli_json("inspect", task_id)
    payload = next(
        json.loads(message["content"].removeprefix("[待核验方案] "))
        for message in inspected["reviewer_messages"]
        if message["role"] == "user" and message["content"].startswith("[待核验方案] ")
    )
    evidence = {item["material_id"]: item for item in payload["evidence"]}
    assert "复杂变速或弱节拍音乐需要人工调整" in evidence["M-001"]["text"]
    assert "仅代表该合成样本" in evidence["M-003"]["scope"]
    assert "商用授权尚未购买" in evidence["M-006"]["scope"] or "商用授权尚未购买" in evidence["M-006"]["text"]
    assert payload["delivery_scope"]["deliverable"] == "campaign_plan"


def test_re_review_receives_original_findings_dispositions_and_applicable_versions(cli_env, model_script):
    failed = copy.deepcopy(REVIEW_FAIL)
    failed["findings"].append({
        **failed["findings"][0], "finding_id": "F2", "severity": "needs_evidence",
        "issue": "音乐授权尚未核对。",
    })
    script = _brief_then_draft_script() + [
        _review_item(failed),
        _tool("record_disposition", {"finding_id": "F1", "decision": "accepted", "reason": "按事实材料返修"}),
        _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
        _generation_item(),
        _campaign_tool(),
        _review_item(REVIEW_PASS_V2),
    ]
    model_script(script)
    task_id = _create()["task_id"]
    _run_until_draft(task_id)
    original = cli_json("review", task_id)["review"]
    cli_json("run", task_id)
    revised = cli_json("review", task_id)["review"]
    assert revised["output_version"] == 2

    inspected = cli_json("inspect", task_id)
    payloads = [
        json.loads(message["content"].removeprefix("[待核验方案] "))
        for message in inspected["reviewer_messages"]
        if message["role"] == "user" and message["content"].startswith("[待核验方案] ")
    ]
    assert payloads, "The actual isolated review inputs must remain inspectable."
    history = payloads[-1]["review_history"]
    assert history[0]["review_id"] == original["review_id"]
    assert history[0]["output_version"] == 1
    assert history[0]["input_version"] == 1
    assert history[0]["findings"][0]["disposition"]["decision"] == "accepted"
    assert history[0]["findings"][0]["disposition"]["reason"] == "按事实材料返修"
    unresolved = payloads[-1]["unresolved_findings"]
    assert {finding["finding_id"] for finding in unresolved} == {"F1", "F2"}
    assert all(finding["reviewed_version"] == "1" for finding in unresolved)
    assert payloads[-1]["reviewed_version"] == 2
    assert payloads[-1]["review_scope"]["current_input_version"] == 1
    assert "重新核对" in payloads[-1]["review_scope"]["history_applicability"]
