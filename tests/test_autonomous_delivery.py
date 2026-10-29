"""A content task's CLI run owns generation, review, revision and verified save."""

import copy
import json
from pathlib import Path

from conftest import cli_json, run_cli_process
from test_campaign_draft import CAMPAIGN, _brief_then_draft_script, _campaign_tool, _create, _generation_item, _tool
from test_review_save import REVIEW_PASS, _review_item
from test_revision_disposition import REVIEW_FAIL


def test_one_run_delivers_reviewed_campaign_without_operator_review_or_save(cli_env, model_script):
    task = _create()
    model_script([
        *_brief_then_draft_script(),
        _tool("review_campaign", {}),
        _review_item(REVIEW_PASS),
        _tool("save_campaign", {}),
    ])

    doc = cli_json("run", task["task_id"])

    assert doc["status"] == "complete"
    assert doc["save"]["verified"] is True
    assert doc["resources"]["sim_spent_cents"] == 230
    assert doc["progress"]["used_rounds"] == 6
    facts = cli_json("inspect", task["task_id"])
    assert [attempt["kind"] for attempt in facts["attempts"]] == [
        "search_materials", "generate_script", "review_campaign", "save_campaign",
    ]
    calls = []
    results = []
    for message in facts["messages"]:
        if message["role"] == "assistant":
            calls.extend(item["id"] for item in message["tool_calls"] or [])
        if message["role"] == "tool":
            results.append(message["tool_call_id"])
    assert calls == results
    trace = next(item for item in doc["save"]["files"] if item["path"].endswith("trace.jsonl"))
    exported = Path(trace["path"]).read_text(encoding="utf-8")
    assert "save_campaign" in exported
    events = [json.loads(line) for line in exported.splitlines()]
    assert any(event["event"] == "message" and event.get("name") == "save_campaign"
               and event.get("role") == "tool" for event in events)


def test_main_agent_revises_failed_review_and_rechecks_before_save(cli_env, model_script):
    task = _create()
    campaign = copy.deepcopy(CAMPAIGN)
    campaign["script"]["segments"][1]["subtitle"] = "自动卡点把选中片段对齐到节拍点"
    model_script([
        *_brief_then_draft_script(),
        _tool("review_campaign", {}), _review_item(REVIEW_FAIL),
        _tool("record_disposition", {"finding_id": "F1", "decision": "accepted", "reason": "按M-001收窄能力表述。"}),
        _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct", "focus": "修正F1，保护其它正确内容。"}),
        _generation_item(), _campaign_tool(campaign),
        _tool("review_campaign", {}), _review_item(REVIEW_PASS),
        _tool("save_campaign", {}),
    ])

    doc = cli_json("run", task["task_id"])

    assert doc["status"] == "complete"
    assert doc["save"]["output_version"] == 2
    assert doc["review"]["output_version"] == 2
    assert doc["resources"]["sim_spent_cents"] == 430
    facts = cli_json("inspect", task["task_id"])
    assert len(facts["outputs"]) == len(facts["reviews"]) == 2
    requests = [message for message in facts["generator_messages"] if message["role"] == "user"]
    revision = json.loads(requests[-1]["content"].split("[脚本生成请求] ", 1)[1])["revision"]
    assert revision["current_output_version"] == 1
    assert revision["review"]["findings"][0]["finding_id"] == "F1"
    assert revision["dispositions"][0]["decision"] == "accepted"
    assert revision["current_campaign"]["materials"] == CAMPAIGN["materials"]


def test_imported_campaign_resume_gives_main_agent_full_edit_and_new_review(cli_env, model_script):
    task = _create()
    model_script([
        *_brief_then_draft_script(),
        _tool("review_campaign", {}), _review_item(REVIEW_PASS), _tool("save_campaign", {}),
        _tool("review_campaign", {}), _review_item(REVIEW_PASS), _tool("save_campaign", {}),
    ])
    first = cli_json("run", task["task_id"])
    assert first["status"] == "complete"
    saved_md = next(item for item in first["save"]["files"] if item["path"].endswith("campaign-v1.md"))
    edited_path = cli_env / "edited.md"
    edited = Path(saved_md["path"]).read_text(encoding="utf-8").replace("把招新现场剪成你的节奏", "把社团招新剪成你的节奏")
    edited_path.write_text(edited, encoding="utf-8")
    imported = cli_json("import-draft", task["task_id"], "--file", str(edited_path))
    assert imported["progress"]["current_output"]["status"] == "pending_review"

    resumed = cli_json("resume", task["task_id"])

    assert resumed["status"] == "complete"
    assert resumed["save"]["output_version"] == resumed["review"]["output_version"] == 2
    assert resumed["resources"]["sim_spent_cents"] == 320
    facts = cli_json("inspect", task["task_id"])
    context = next(message for message in facts["messages"] if "[待核验人工稿]" in (message["content"] or ""))
    assert "把社团招新剪成你的节奏" in context["content"]
    assert "主脚本" in context["content"]
    assert edited_path.read_text(encoding="utf-8") == edited


def test_save_after_effect_crash_recovers_main_tool_receipt_without_new_attempt(cli_env, model_script):
    task = _create()
    script = model_script([
        *_brief_then_draft_script(),
        _tool("review_campaign", {}), _review_item(REVIEW_PASS), _tool("save_campaign", {}),
    ])
    crashed = run_cli_process(
        "run", task["task_id"], data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": "after_save_effect"},
    )
    assert crashed.returncode == 70
    before = cli_json("inspect", task["task_id"])

    process = run_cli_process(
        "resume", task["task_id"], data_dir=cli_env / "data", extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert process.returncode == 0, process.stderr
    resumed = json.loads(process.stdout)

    assert resumed["status"] == "complete"
    assert resumed["progress"]["used_rounds"] == 6
    assert resumed["resources"]["sim_spent_cents"] == 230
    after = cli_json("inspect", task["task_id"])
    assert len(after["attempts"]) == len(before["attempts"]) == 4
    assert after["ledger"] == before["ledger"]
    receipt = [message for message in after["messages"] if message.get("name") == "save_campaign" and message["role"] == "tool"]
    assert len(receipt) == 1
    assert json.loads(receipt[0]["content"])["recovered"] is True


def test_failed_save_feedback_and_explicit_retry_keep_one_business_record(cli_env, model_script, monkeypatch):
    task = _create()
    script = model_script([
        *_brief_then_draft_script(),
        _tool("review_campaign", {}), _review_item(REVIEW_PASS), _tool("save_campaign", {}),
        _tool("stop_incomplete", {"reason": "保存明确失败，保留成果，待文件环境修复后显式续跑。"}),
        _tool("save_campaign", {}),
    ])
    monkeypatch.setenv("CTA_FAIL_SAVE", "1")
    failed = cli_json("run", task["task_id"])
    assert failed["status"] == "incomplete"
    assert failed["save"]["status"] == "failed"
    save_id = failed["save"]["save_id"]
    assert failed["resources"]["sim_spent_cents"] == 230
    monkeypatch.delenv("CTA_FAIL_SAVE")

    process = run_cli_process(
        "resume", task["task_id"], data_dir=cli_env / "data", extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert process.returncode == 0, process.stderr
    resumed = json.loads(process.stdout)

    assert resumed["status"] == "complete"
    assert resumed["save"]["save_id"] == save_id
    assert resumed["resources"]["sim_spent_cents"] == 240
    facts = cli_json("inspect", task["task_id"])
    assert len(facts["saves"]) == 1
    attempts = [attempt for attempt in facts["attempts"] if attempt["kind"] == "save_campaign"]
    assert [attempt["status"] for attempt in attempts] == ["failed", "success"]
