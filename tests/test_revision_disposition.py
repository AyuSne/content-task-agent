"""Ticket 06: dispositions, evidence-preserving revision, re-review and review bounds."""

import json

from conftest import cli_json, run_cli

REVIEW_FAIL = {
    "verdict": "fail",
    "completeness": "full",
    "summary": "主脚本有一处事实无支持，必须修改。",
    "findings": [
        {
            "finding_id": "F1",
            "severity": "must_fix",
            "location": "主脚本第 2 段",
            "requirement": "事实支持",
            "evidence": "M-001 不支持该表述",
            "issue": "字幕超出了材料支持范围。",
            "suggestion": "改为 M-001 支持的表述。",
            "reviewed_version": "1",
        }
    ],
    "evidence_gaps": [],
}

REVIEW_PASS_V2 = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "返修后事实与要求一致。",
    "findings": [],
    "evidence_gaps": [],
}


def _review_item(report, **extra):
    return {"channel": "review", "type": "tool", "name": "report_review", "arguments": report, **extra}


def _tool(name, arguments, **extra):
    return {"type": "tool", "name": name, "arguments": arguments, **extra}


def test_accepted_finding_drives_revision_and_re_review_then_save(cli_env, model_script):
    from copy import deepcopy

    from test_campaign_delivery_new import _campaign
    from test_direct_script import SCRIPT
    from test_review_dispositions_new import _reviewed

    task_id, prefix, script_ref, campaign_ref, review_id = _reviewed(model_script)
    decision = {"review_id": review_id, "campaign_ref": campaign_ref, "decisions": [
        {"finding_id": "F1", "decision": "accepted", "reason": "确实超出证据范围"}]}
    revised = deepcopy(SCRIPT)
    revised["segments"][0]["voiceover"] = "先选择片段，再对齐节拍。"
    prefix += [_tool("record_dispositions", decision), _tool("submit_script", {
        "brief_version": 1, "script": revised, "revises_script_ref": script_ref})]
    model_script(prefix)
    cli_json("resume", task_id, "--max-rounds", "2")
    inspected = cli_json("inspect", task_id)
    new_script = inspected["scripts"][-1]
    new_ref = {"id": new_script["output_id"], "version": new_script["version"]}
    prefix += [_tool("submit_campaign", {"script_ref": new_ref, "campaign": _campaign()})]
    model_script(prefix + [_review_item(REVIEW_FAIL), _review_item(REVIEW_PASS_V2)])
    cli_json("resume", task_id, "--max-rounds", "1")
    inspected = cli_json("inspect", task_id)
    campaigns = [output for output in inspected["outputs"] if output["kind"] == "campaign"]
    assert [output["version"] for output in campaigns] == [1, 2]
    assert inspected["dispositions"][0]["decision"] == "accepted"
    assert inspected["scripts"][0]["content"]["script"] == SCRIPT
    assert inspected["scripts"][1]["content"]["script"] == revised
    assert inspected["resources"]["sim_spent_cents"] == 120 + 80 + 120
    re_reviewed = cli_json("review", task_id)
    assert re_reviewed["review"]["verdict"] == "pass"
    assert re_reviewed["review"]["output_version"] == 2
    saved = cli_json("save", task_id)
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 2


def test_rejected_false_positive_allows_save_with_reason(cli_env, model_script):
    from test_review_dispositions_new import _reviewed
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    evidence = {"kind": "material", "material_id": "M-001", "material_version": "2026-10-29.v1",
                "quote": "把用户选中的视频片段对齐到节拍点", "scope": "iOS/Android 4.2 及以上"}
    model_script(prefix + [_tool("record_dispositions", {"review_id": review_id, "campaign_ref": campaign_ref,
        "decisions": [{"finding_id": "F1", "decision": "rejected", "reason": "M-001 原文明确支持，评审误报", "evidence": [evidence]}]})])
    cli_json("resume", task_id, "--max-rounds", "1")
    inspected = cli_json("inspect", task_id)
    assert inspected["dispositions"][0]["decision"] == "rejected"
    assert inspected["dispositions"][0]["evidence"] == [evidence]
    saved = cli_json("save", task_id)
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 1


def test_accepted_without_revision_blocks_save(cli_env, model_script):
    from test_review_dispositions_new import _reviewed
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    model_script(prefix + [_tool("record_dispositions", {"review_id": review_id, "campaign_ref": campaign_ref,
        "decisions": [{"finding_id": "F1", "decision": "accepted", "reason": "需要返修"}]})])
    cli_json("resume", task_id, "--max-rounds", "1")
    code, out, _err = run_cli("save", task_id)
    document = json.loads(out)
    assert code == 4
    assert document["save_error"]["code"] == "unresolved_must_fix"


def test_disposition_for_unknown_finding_is_rejected(cli_env, model_script):
    from test_campaign_delivery_new import _result
    from test_review_dispositions_new import _reviewed
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    model_script(prefix + [_tool("record_dispositions", {"review_id": review_id, "campaign_ref": campaign_ref,
        "decisions": [{"finding_id": "F999", "decision": "accepted", "reason": "不存在"}]})])
    cli_json("resume", task_id, "--max-rounds", "1")
    inspected = cli_json("inspect", task_id)
    rejection = _result(inspected, "record_dispositions")[0]
    assert rejection["error"] == "finding_not_found"
    assert inspected["dispositions"] == []


def test_review_readback_limit_stops_at_two_and_incomplete(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    reads = [{"channel": "review", "type": "tool", "name": "read_material", "arguments": {"material_id": mid}}
             for mid in ("M-001", "M-002", "M-004")]
    task = _new_draft_ready(model_script, reads)
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["status"] == "incomplete"
    assert reviewed["review"]["readbacks_used"] == 2
    inspected = cli_json("inspect", task["task_id"])
    rejected = [json.loads(message["content"]) for message in inspected["reviewer_messages"]
                if message["role"] == "tool" and message["name"] == "read_material"
                and "readback_limit" in (message["content"] or "")]
    assert rejected


def test_review_multi_call_is_rejected_then_single_report_continues(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    multi = {"channel": "review", "type": "multi", "calls": [
        {"name": "read_material", "arguments": {"material_id": "M-001"}},
        {"name": "read_material", "arguments": {"material_id": "M-002"}}]}
    task = _new_draft_ready(model_script, [multi, _review_item(REVIEW_PASS_V2)])
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["verdict"] == "pass"
    assert reviewed["review"]["requests_used"] == 2
    assert reviewed["review"]["readbacks_used"] == 0
