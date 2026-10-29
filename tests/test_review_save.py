"""Ticket 04: independent review, dispositions boundary, verified save and export."""

import copy
import json
from pathlib import Path

from conftest import VALID, cli_json, run_cli
from test_campaign_draft import (
    CAMPAIGN,
    _brief_then_draft_script,
    _campaign_tool,
    _create,
    _run_until_draft,
)

REVIEW_PASS = {
    "verdict": "pass",
    "completeness": "full",
    "summary": "五项质量要求均满足；意见仅为可选改进。",
    "findings": [],
    "evidence_gaps": [],
}

REVIEW_FAIL = {
    "verdict": "fail",
    "completeness": "full",
    "summary": "有关键事实无支持，必须修改。",
    "findings": [
        {
            "finding_id": "F1",
            "severity": "must_fix",
            "location": "主脚本第 2 段字幕",
            "requirement": "事实支持",
            "evidence": "M-001 不包含该承诺",
            "issue": "字幕写入了材料未支持的能力。",
            "suggestion": "删除或改为 M-001 支持的表达。",
            "reviewed_version": "1",
        }
    ],
    "evidence_gaps": [],
}


def _review_item(report, **extra):
    return {"channel": "review", "type": "tool", "name": "report_review", "arguments": report, **extra}


def _draft_ready(model_script, review_item):
    script = _brief_then_draft_script()
    script.append(review_item)
    model_script(script)
    task = _create()
    draft = _run_until_draft(task["task_id"])
    assert draft["progress"]["phase"] == "draft_ready"
    return task


def _save(task_id, monkeypatch=None):
    code, out, _err = run_cli("save", task_id)
    return code, json.loads(out)


def test_review_pass_then_verified_save_and_exports(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    reviewed = cli_json("review", task["task_id"])

    assert reviewed["status"] == "in_progress"
    assert reviewed["progress"]["phase"] == "reviewed"
    assert reviewed["review"]["verdict"] == "pass"
    assert reviewed["review"]["completeness"] == "full"
    assert reviewed["review"]["requests_used"] == 1
    assert reviewed["review"]["readbacks_used"] == 0
    assert reviewed["resources"]["sim_spent_cents"] == 140 + 80
    assert reviewed["plan"] == {"version": 0, "steps": []}

    inspected = cli_json("inspect", task["task_id"])
    assert inspected["reviewer_knowledge"][0]["knowledge_id"] == "content-review"
    assert len(inspected["reviewer_knowledge"][0]["sha256"]) == 64
    review_request = next(row for row in inspected["llm_requests"] if row["purpose"] == "review")
    assert '"context": "isolated_review"' in review_request["meta_json"]
    assert review_request["mock"] == 1
    assert not any("内容核验工作知识" in (message["content"] or "") for message in inspected["messages"])

    code, saved = _save(task["task_id"])
    assert code == 0
    assert saved["status"] == "complete"
    assert saved["progress"]["phase"] == "complete"
    assert saved["save"]["verified"] is True
    assert saved["save"]["status"] == "verified"
    assert len(saved["save"]["files"]) >= 5
    assert saved["resources"]["sim_spent_cents"] == 140 + 80 + 10

    for item in saved["save"]["files"]:
        path = Path(item["path"])
        assert path.is_file()
        assert path.stat().st_size == item["bytes"]

    report = next(
        item for item in saved["save"]["files"] if item["path"].endswith("execution-report.md")
    )
    report_text = Path(report["path"]).read_text(encoding="utf-8")
    assert "# 执行报告" in report_text
    assert "## 内容核验" in report_text
    assert "保存记录" in report_text
    campaign_md = next(item for item in saved["save"]["files"] if item["path"].endswith("campaign-v1.md"))
    assert Path(campaign_md["path"]).read_text(encoding="utf-8").startswith("# 把招新现场剪成你的节奏")
    trace = next(item for item in saved["save"]["files"] if item["path"].endswith("trace.jsonl"))
    events = [
        json.loads(line)
        for line in Path(trace["path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["event"] == "review" for event in events)
    assert any(event["event"] == "save" and event["verified"] for event in events)

    exported = cli_json("export", task["task_id"])
    assert exported["verified"] is True
    assert [item["sha256"] for item in exported["files"]] == [
        item["sha256"] for item in saved["save"]["files"]
    ]

    duplicate = cli_json("save", task["task_id"])
    assert duplicate["duplicate"] is True
    assert duplicate["resources"]["sim_spent_cents"] == 230
    attempts = cli_json("inspect", task["task_id"])["attempts"]
    assert len([attempt for attempt in attempts if attempt["kind"] == "save_campaign"]) == 1

    duplicate_task = cli_json(
        "create", "--topic", VALID["topic"], "--budget", VALID["budget"],
        "--deadline", VALID["deadline"], "--audience", "大学生", "--requirement", "突出自动卡点",
    )
    assert duplicate_task["task_id"] == task["task_id"]
    assert duplicate_task["status"] == "complete"


def test_save_without_review_is_blocked(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    code, document = _save(task["task_id"])
    assert code == 4
    assert document["save_error"]["code"] == "not_reviewed"
    assert document["save"] is None
    assert document["status"] == "in_progress"
    assert document["resources"]["sim_spent_cents"] == 140


def test_save_blocked_by_must_fix_finding(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_FAIL))
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["verdict"] == "fail"
    assert reviewed["review"]["findings"][0]["severity"] == "must_fix"

    code, document = _save(task["task_id"])
    assert code == 4
    assert document["save_error"]["code"] == "unresolved_must_fix"
    assert document["save"] is None
    assert document["status"] == "in_progress"


def test_explicit_save_failure_keeps_cost_and_incomplete_state(cli_env, model_script, monkeypatch):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    monkeypatch.setenv("CTA_FAIL_SAVE", "1")
    code, document = _save(task["task_id"])
    assert code == 4
    assert document["status"] == "incomplete"
    assert document["progress"]["phase"] == "save_failed"
    assert document["save"]["status"] == "failed"
    assert document["save"]["verified"] is False
    assert document["save_error"]["code"] == "save_failed"
    assert document["resources"]["sim_spent_cents"] == 230
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["attempts"][-1]["kind"] == "save_campaign"
    assert inspected["attempts"][-1]["status"] == "failed"


def test_save_blocked_by_blocking_campaign_question(cli_env, model_script):
    campaign = copy.deepcopy(CAMPAIGN)
    campaign["open_questions"] = [
        {"question": "产品录屏由谁提供？", "impact": "核心演示无法制作", "blocking": True}
    ]
    script = _brief_then_draft_script(campaign_item=_campaign_tool(campaign))
    script.append(_review_item(REVIEW_PASS))
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    cli_json("review", task["task_id"])
    code, document = _save(task["task_id"])
    assert code == 4
    assert document["save_error"]["code"] == "blocking_open_questions"
    assert document["save"] is None


def test_review_error_is_incomplete_and_blocks_save(cli_env, model_script):
    task = _draft_ready(model_script, {"channel": "review", "type": "error", "message": "review upstream 503"})
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["progress"]["phase"] == "review_incomplete"
    assert reviewed["review"]["status"] == "incomplete"
    assert reviewed["review"]["verdict"] is None

    code, document = _save(task["task_id"])
    assert code == 4
    assert document["save_error"]["code"] == "review_incomplete"


def test_incomplete_verdict_is_not_recorded_as_complete_and_can_retry(cli_env, model_script):
    task = _draft_ready(
        model_script,
        _review_item({
            "verdict": "incomplete",
            "completeness": "partial",
            "summary": "材料不足，无法完成核验。",
            "findings": [],
            "evidence_gaps": ["缺少 M-010 正文"],
        }),
    )
    first = cli_json("review", task["task_id"])
    assert first["progress"]["phase"] == "review_incomplete"
    assert first["review"]["status"] == "incomplete"
    code, document = _save(task["task_id"])
    assert code == 4
    assert document["save_error"]["code"] == "review_incomplete"


def test_non_object_review_arguments_are_rejected_then_continue(cli_env, model_script):
    script = _brief_then_draft_script()
    script += [
        {"channel": "review", "type": "tool", "name": "read_material", "arguments": "[]"},
        _review_item(REVIEW_PASS),
    ]
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["status"] == "complete"
    assert reviewed["review"]["verdict"] == "pass"
    assert reviewed["review"]["requests_used"] == 2
    inspected = cli_json("inspect", task["task_id"])
    rejection = next(
        message for message in inspected["reviewer_messages"]
        if message["role"] == "tool" and "invalid_arguments" in (message["content"] or "")
    )
    assert "参数必须是 JSON 对象" in rejection["content"]


def test_duplicate_review_reuses_existing_record(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    first = cli_json("review", task["task_id"])
    second = cli_json("review", task["task_id"])
    assert second["duplicate"] is True
    assert second["review"]["review_id"] == first["review"]["review_id"]
    assert second["resources"]["sim_spent_cents"] == first["resources"]["sim_spent_cents"] == 220
