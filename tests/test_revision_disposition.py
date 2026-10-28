"""Ticket 06: dispositions, evidence-preserving revision, re-review and review bounds."""

import json

from conftest import cli_json, run_cli
from test_campaign_draft import (
    _brief_then_draft_script,
    _campaign_tool,
    _create,
    _generation_item,
    _run_until_draft,
)

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


def _draft_with_review_failure(model_script):
    script = _brief_then_draft_script()
    script.append(_review_item(REVIEW_FAIL))
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["verdict"] == "fail"
    return task


def test_accepted_finding_drives_revision_and_re_review_then_save(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        _review_item(REVIEW_FAIL),
        _tool("record_disposition", {"finding_id": "F1", "decision": "accepted", "reason": "确实超出证据范围"}),
        _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
        _generation_item(),
        _campaign_tool(),
        _review_item(REVIEW_PASS_V2),
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    cli_json("review", task["task_id"])

    revised = cli_json("run", task["task_id"])
    assert revised["progress"]["phase"] == "draft_ready"
    assert revised["progress"]["current_output"]["version"] == 2
    assert revised["resources"]["sim_spent_cents"] == 140 + 80 + 120
    inspected = cli_json("inspect", task["task_id"])
    assert [output["version"] for output in inspected["outputs"]] == [1, 2]
    assert inspected["dispositions"][0]["decision"] == "accepted"

    re_reviewed = cli_json("review", task["task_id"])
    assert re_reviewed["review"]["verdict"] == "pass"
    assert re_reviewed["review"]["output_version"] == 2
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 2


def test_rejected_false_positive_allows_save_with_reason(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        _review_item(REVIEW_FAIL),
        _tool(
            "record_disposition",
            {"finding_id": "F1", "decision": "rejected", "reason": "M-001 原文明确支持，评审误报"},
        ),
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    cli_json("review", task["task_id"])

    run_cli("run", task["task_id"])
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["dispositions"][0]["decision"] == "rejected"
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 1


def test_accepted_without_revision_blocks_save(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        _review_item(REVIEW_FAIL),
        _tool("record_disposition", {"finding_id": "F1", "decision": "accepted", "reason": "需要返修"}),
        _tool("stop_incomplete", {"reason": "本轮不继续返修"}),
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    cli_json("review", task["task_id"])
    cli_json("run", task["task_id"])

    code, out, _err = run_cli("save", task["task_id"])
    document = json.loads(out)
    assert code == 4
    assert document["save_error"]["code"] == "unresolved_must_fix"


def test_disposition_for_unknown_finding_is_rejected(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        _review_item(REVIEW_FAIL),
        _tool("record_disposition", {"finding_id": "F999", "decision": "rejected", "reason": "不存在"}),
        _tool("stop_incomplete", {"reason": "结束"}),
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    cli_json("review", task["task_id"])
    cli_json("run", task["task_id"])
    inspected = cli_json("inspect", task["task_id"])
    rejection = next(
        json.loads(message["content"])
        for message in inspected["messages"]
        if message["role"] == "tool" and message["name"] == "record_disposition"
    )
    assert rejection["error"] == "finding_not_found"
    assert inspected["dispositions"] == []


def test_review_readback_limit_stops_at_two_and_incomplete(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        {"channel": "review", "type": "tool", "name": "read_material", "arguments": {"material_id": "M-001"}},
        {"channel": "review", "type": "tool", "name": "read_material", "arguments": {"material_id": "M-002"}},
        {"channel": "review", "type": "tool", "name": "read_material", "arguments": {"material_id": "M-004"}},
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["status"] == "incomplete"
    assert reviewed["review"]["readbacks_used"] == 2
    inspected = cli_json("inspect", task["task_id"])
    rejected = [
        json.loads(message["content"])
        for message in inspected["reviewer_messages"]
        if message["role"] == "tool"
        and message["name"] == "read_material"
        and "readback_limit" in (message["content"] or "")
    ]
    assert rejected


def test_review_multi_call_is_rejected_then_single_report_continues(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        {
            "channel": "review",
            "type": "multi",
            "calls": [
                {"name": "read_material", "arguments": {"material_id": "M-001"}},
                {"name": "read_material", "arguments": {"material_id": "M-002"}},
            ],
        },
        _review_item(REVIEW_PASS_V2),
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["verdict"] == "pass"
    assert reviewed["review"]["requests_used"] == 2
    assert reviewed["review"]["readbacks_used"] == 0
