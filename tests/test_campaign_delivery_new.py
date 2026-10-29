"""Campaign submission and delivery through CLI and scripted model seam."""

import copy
import json
from pathlib import Path

from conftest import cli_json
from test_brief_loop import BRIEF
from test_campaign_draft import CAMPAIGN, _create, _tool
from test_direct_script import SCRIPT
from test_review_save import REVIEW_PASS, _review_item


def _script_ready(model_script):
    task = _create()
    prefix = [_tool("propose_brief", BRIEF), _tool("submit_script", {"brief_version": 1, "script": SCRIPT})]
    model_script(prefix)
    cli_json("run", task["task_id"], "--max-rounds", "2")
    script = cli_json("inspect", task["task_id"])["scripts"][0]
    return task, prefix, {"id": script["output_id"], "version": script["version"]}


def _campaign():
    campaign = copy.deepcopy(CAMPAIGN)
    del campaign["script"]
    campaign["evidence"][0]["material_version"] = "2026-10-29.v1"
    campaign["strategy"] = {"name": "校园情境与教程结合", "rationale": "演示操作并联系校园", "alternatives": []}
    return campaign


def _result(view, name):
    return [json.loads(m["content"]) for m in view["messages"] if m["role"] == "tool" and m["name"] == name]


def test_campaign_references_script_snapshot_and_rejects_unknown_reference(cli_env, model_script):
    task, prefix, ref = _script_ready(model_script)
    campaign = _campaign()
    model_script(
        prefix
        + [
            _tool("submit_campaign", {"script_ref": {"id": "unknown", "version": 1}, "campaign": campaign}),
            _tool("submit_campaign", {"script_ref": ref, "campaign": campaign}),
        ]
    )
    cli_json("resume", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    bad, good = _result(view, "submit_campaign")
    assert bad["ok"] is False and bad["error"] == "invalid_campaign"
    assert good["ok"] is True and good["campaign_ref"]["version"] == 1
    campaigns = [output for output in view["outputs"] if output["kind"] == "campaign"]
    assert len(campaigns) == 1
    snapshot = campaigns[0]["content"]
    assert snapshot["script_ref"] == ref and snapshot["script"] == SCRIPT
    assert snapshot["strategy"]["name"] == "校园情境与教程结合"
    assert snapshot["materials"][1]["status"] == "to_create"
    assert view["resources"]["sim_spent_cents"] == 120


def test_review_and_save_explicit_version_then_verify_all_delivery_files(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool("submit_campaign", {"script_ref": script_ref, "campaign": _campaign()})]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    view = cli_json("inspect", task["task_id"])
    ref = _result(view, "submit_campaign")[0]["campaign_ref"]
    prefix += [
        _tool("review_campaign", {"campaign_ref": {"id": "other", "version": 1}}),
        _tool("review_campaign", {"campaign_ref": ref}),
        _review_item(REVIEW_PASS),
    ]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    reviews = _result(view, "review_campaign")
    assert reviews[0]["ok"] is False and reviews[0]["error"] == "invalid_campaign_ref"
    assert reviews[1]["ok"] is True and reviews[1]["review"]["output_id"] == ref["id"]
    review_context = next(m["content"] for m in view["reviewer_messages"] if "[待核验方案]" in (m["content"] or ""))
    assert SCRIPT["title"] in review_context and SCRIPT["segments"][0]["visual"] in review_context
    assert "requirements" in review_context and "M-001" in review_context
    model_script(prefix + [_tool("save_campaign", {"campaign_ref": ref})])
    result = cli_json("resume", task["task_id"], "--max-rounds", "1")
    assert result["status"] == "complete"
    view = cli_json("inspect", task["task_id"])
    saved = _result(view, "save_campaign")[0]
    assert saved["ok"] is True and saved["save"]["verified"] is True
    assert saved["save"]["output_id"] == ref["id"] and saved["save"]["output_version"] == ref["version"]
    assert view["resources"]["sim_spent_cents"] == 210
    assert len(saved["save"]["files"]) >= 5
    campaign_file = next(x for x in saved["save"]["files"] if x["path"].endswith("campaign-v1.md"))
    text = Path(campaign_file["path"]).read_text(encoding="utf-8")
    assert SCRIPT["segments"][0]["voiceover"] in text
    assert "待制作" in text and "待获取" in text


def test_later_script_revision_leaves_previous_campaign_snapshot_unchanged(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool("submit_campaign", {"script_ref": script_ref, "campaign": _campaign()})]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    revised = copy.deepcopy(SCRIPT)
    revised["segments"][0]["voiceover"] = "先挑选片段，再对齐节拍。"
    model_script(
        prefix + [_tool("submit_script", {"brief_version": 1, "script": revised, "revises_script_ref": script_ref})]
    )
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    view = cli_json("inspect", task["task_id"])
    campaign = next(x for x in view["outputs"] if x["kind"] == "campaign")
    assert campaign["content"]["script_ref"] == script_ref
    assert campaign["content"]["script"] == SCRIPT
    assert view["scripts"][1]["content"]["script"] == revised
    assert view["resources"]["sim_spent_cents"] == 240


def _new_draft_ready(model_script, review_item):
    task = _create()
    prefix = [
        _tool("search_materials", {"query": "自动卡点"}),
        _tool("propose_brief", BRIEF),
        _tool("submit_script", {"brief_version": 1, "script": SCRIPT}),
    ]
    model_script(prefix)
    cli_json("run", task["task_id"], "--max-rounds", "3")
    script = cli_json("inspect", task["task_id"])["scripts"][0]
    ref = {"id": script["output_id"], "version": script["version"]}
    review_items = review_item if isinstance(review_item, list) else [review_item]
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": _campaign()}), *review_items])
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    assert cli_json("status", task["task_id"])["progress"]["phase"] == "draft_ready"
    return task


def test_save_is_blocked_until_complete_review_and_wrong_reference_never_charges(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool("submit_campaign", {"script_ref": script_ref, "campaign": _campaign()})]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    ref = _result(cli_json("inspect", task["task_id"]), "submit_campaign")[0]["campaign_ref"]
    prefix += [
        _tool("save_campaign", {"campaign_ref": {"id": "other", "version": 1}}),
        _tool("save_campaign", {"campaign_ref": ref}),
        _tool("review_campaign", {"campaign_ref": ref}),
        _review_item(
            {
                "verdict": "incomplete",
                "completeness": "partial",
                "summary": "缺资料",
                "findings": [],
                "evidence_gaps": ["缺资料"],
            }
        ),
        _tool("save_campaign", {"campaign_ref": ref}),
    ]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "4")
    view = cli_json("inspect", task["task_id"])
    saves = _result(view, "save_campaign")
    assert [x["error"] for x in saves] == ["invalid_campaign_ref", "not_reviewed", "review_incomplete"]
    review = _result(view, "review_campaign")[0]
    assert review["ok"] is False and review["error"] == "review_incomplete"
    assert view["resources"]["sim_spent_cents"] == 200
    assert all(x["kind"] != "save_campaign" for x in view["attempts"])
    assert view["status"] != "complete"


def test_save_failure_is_reported_and_never_becomes_complete(cli_env, model_script, monkeypatch):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool("submit_campaign", {"script_ref": script_ref, "campaign": _campaign()})]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    ref = _result(cli_json("inspect", task["task_id"]), "submit_campaign")[0]["campaign_ref"]
    prefix += [_tool("review_campaign", {"campaign_ref": ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    monkeypatch.setenv("CTA_FAIL_SAVE", "1")
    model_script(prefix + [_tool("save_campaign", {"campaign_ref": ref})])
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    view = cli_json("inspect", task["task_id"])
    saved = _result(view, "save_campaign")[0]
    assert saved["ok"] is False and saved["error"] == "save_failed"
    assert saved["save"]["status"] == "failed" and saved["save"]["verified"] is False
    assert view["status"] != "complete" and view["resources"]["sim_spent_cents"] == 210


def test_interrupted_save_is_unknown_until_reconciled_without_recharge(cli_env, model_script):
    from conftest import run_cli_process

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    crashed = run_cli_process(
        "save", task["task_id"], data_dir=cli_env / "data", extra_env={"CTA_FAULT_CRASH": "save_after_record"}
    )
    assert crashed.returncode == 70
    before = cli_json("status", task["task_id"])
    assert before["save"]["status"] == "unknown" and before["save"]["verified"] is False
    assert before["status"] != "complete"
    reconciled = cli_json("reconcile", task["task_id"])
    assert reconciled["save"]["verified"] is True and reconciled["status"] == "complete"
    view = cli_json("inspect", task["task_id"])
    assert len([a for a in view["attempts"] if a["kind"] == "save_campaign"]) == 1
    assert view["resources"]["sim_spent_cents"] == 230


def test_previous_campaign_review_is_rejected_after_new_version(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    campaign = _campaign()
    prefix += [_tool("submit_campaign", {"script_ref": script_ref, "campaign": campaign})]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    old_ref = _result(cli_json("inspect", task["task_id"]), "submit_campaign")[0]["campaign_ref"]
    campaign["activity"]["theme"] = "新的活动主题"
    prefix += [
        _tool("submit_campaign", {"script_ref": script_ref, "campaign": campaign}),
        _tool("review_campaign", {"campaign_ref": old_ref}),
    ]
    model_script(prefix)
    cli_json("resume", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    assert _result(view, "review_campaign")[0]["error"] == "invalid_campaign_ref"
    assert len([o for o in view["outputs"] if o["kind"] == "campaign"]) == 2
    assert not view["reviewer_messages"] and view["resources"]["sim_spent_cents"] == 120


def test_review_reads_only_task_materials_with_bounded_requests(cli_env, model_script):
    read = lambda mid: {"channel": "review", "name": "read_material", "arguments": {"material_id": mid}}
    task = _new_draft_ready(model_script, [read("M-001"), read("M-003"), _review_item(REVIEW_PASS)])
    result = cli_json("review", task["task_id"])
    assert result["review"]["status"] == "complete"
    assert result["review"]["requests_used"] == 3 and result["review"]["readbacks_used"] == 2
    messages = cli_json("inspect", task["task_id"])["reviewer_messages"]
    reads = [json.loads(m["content"]) for m in messages if m["role"] == "tool" and m["name"] == "read_material"]
    assert len(reads) == 2 and all(x["ok"] for x in reads)
