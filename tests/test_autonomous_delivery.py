"""A content task's CLI run owns generation, review, revision and verified save."""

import copy
import json
from pathlib import Path

from conftest import cli_json, run_cli_process
from test_campaign_draft import _create, _tool
from test_campaign_delivery_new import _campaign, _new_draft_ready, _result
from test_brief_loop import BRIEF
from test_direct_script import SCRIPT
from test_review_save import REVIEW_PASS, _review_item
from test_revision_disposition import REVIEW_FAIL, REVIEW_PASS_V2
from cta.model import ModelResponse, ToolCall


class ResponsiveModel:
    """Respond at the model seam using actual receipts, without guessing IDs."""

    def __init__(self, review_reports):
        self.review_reports = review_reports

    def complete(self, messages, tools, *, cursor, channel="main", json_mode=False):
        if channel == "review":
            report = self.review_reports[min(cursor, len(self.review_reports) - 1)]
            return ModelResponse(tool_calls=(ToolCall(f"review_{cursor}", "report_review",
                                 json.dumps(report, ensure_ascii=False)),), finish_reason="tool_calls",
                                 model="responsive-mock", mock=True)
        receipts = {}
        for message in messages:
            if message["role"] == "tool":
                result = json.loads(message["content"])
                if result.get("script_ref"):
                    receipts.setdefault("scripts", []).append(result["script_ref"])
                if result.get("campaign_ref"):
                    receipts.setdefault("campaigns", []).append(result["campaign_ref"])
                if result.get("review"):
                    receipts.setdefault("reviews", []).append(result["review"])
        actions = [
            ("search_materials", {"query": "自动卡点", "mode": "basic"}),
            ("propose_brief", BRIEF),
            ("submit_script", {"brief_version": 1, "script": SCRIPT}),
        ]
        if cursor < len(actions):
            name, arguments = actions[cursor]
        elif cursor == 3:
            name, arguments = "submit_campaign", {"script_ref": receipts["scripts"][-1], "campaign": _campaign()}
        elif cursor == 4:
            name, arguments = "review_campaign", {"campaign_ref": receipts["campaigns"][-1]}
        elif len(self.review_reports) == 1:
            name, arguments = "save_campaign", {"campaign_ref": receipts["campaigns"][-1]}
        elif cursor == 5:
            name, arguments = "record_dispositions", {"review_id": receipts["reviews"][-1]["review_id"],
                "campaign_ref": receipts["campaigns"][-1], "decisions": [
                    {"finding_id": "F1", "decision": "accepted", "reason": "按 M-001 收窄能力表述。"}]}
        elif cursor == 6:
            revised = copy.deepcopy(SCRIPT)
            revised["segments"][0]["voiceover"] = "选择片段，再对齐节拍。"
            name, arguments = "submit_script", {"brief_version": 1, "script": revised,
                                                 "revises_script_ref": receipts["scripts"][-1]}
        elif cursor == 7:
            name, arguments = "submit_campaign", {"script_ref": receipts["scripts"][-1], "campaign": _campaign()}
        elif cursor == 8:
            name, arguments = "review_campaign", {"campaign_ref": receipts["campaigns"][-1]}
        else:
            name, arguments = "save_campaign", {"campaign_ref": receipts["campaigns"][-1]}
        return ModelResponse(tool_calls=(ToolCall(f"adaptive_{cursor}", name,
                             json.dumps(arguments, ensure_ascii=False)),), finish_reason="tool_calls",
                             model="responsive-mock", mock=True)


def _responsive(monkeypatch, reports):
    client = ResponsiveModel(reports)
    monkeypatch.setattr("cta.loop.create_client", lambda config: client)
    monkeypatch.setattr("cta.review.create_client", lambda config: client)


def test_passed_review_exposes_current_save_option_without_requiring_optional_rewrite(cli_env, monkeypatch):
    report = {**REVIEW_PASS, "findings": [{**REVIEW_FAIL["findings"][0], "severity": "optional"}]}
    _responsive(monkeypatch, [report])
    task_id = _create()["task_id"]
    assert cli_json("run", task_id)["status"] == "complete"
    facts = cli_json("inspect", task_id)
    result = _result(facts, "review_campaign")[0]
    options = result["delivery_options"]
    assert options["quality_allows_save"] is True
    assert options["optional_findings"] == 1
    assert options["remaining_main_rounds"] == facts["progress"]["max_rounds"] - 5
    assert len([o for o in facts["outputs"] if o["kind"] == "campaign"]) == 1


def test_one_run_delivers_reviewed_campaign_without_operator_review_or_save(cli_env, monkeypatch):
    task = _create()
    _responsive(monkeypatch, [REVIEW_PASS])
    doc = cli_json("run", task["task_id"])
    assert doc["status"] == "complete"
    assert doc["save"]["verified"] is True
    assert doc["resources"]["sim_spent_cents"] == 230
    assert doc["progress"]["used_rounds"] == 6
    markdown = next(item for item in doc["save"]["files"] if item["path"].endswith("campaign-v1.md"))
    delivered = Path(markdown["path"]).read_text(encoding="utf-8")
    assert "本版本已完成内容核验与保存核实" in delivered
    assert "尚未完成内容核验与保存核实" not in delivered
    facts = cli_json("inspect", task["task_id"])
    assert [attempt["kind"] for attempt in facts["attempts"]] == [
        "search_materials", "submit_script", "review_campaign", "save_campaign"]
    calls = [item["id"] for m in facts["messages"] if m["role"] == "assistant" for item in m["tool_calls"] or []]
    results = [m["tool_call_id"] for m in facts["messages"] if m["role"] == "tool"]
    assert calls == results
    assert not any(row["purpose"] == "script_generation" for row in facts["llm_requests"])
    trace = next(item for item in doc["save"]["files"] if item["path"].endswith("trace.jsonl"))
    events = [json.loads(line) for line in Path(trace["path"]).read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "message" and event.get("name") == "save_campaign"
               and event.get("role") == "tool" for event in events)


def test_main_agent_revises_failed_review_and_rechecks_before_save(cli_env, monkeypatch):
    task = _create()
    _responsive(monkeypatch, [REVIEW_FAIL, REVIEW_PASS_V2])
    doc = cli_json("run", task["task_id"])
    assert doc["status"] == "complete"
    assert doc["save"]["output_version"] == doc["review"]["output_version"] == 2
    assert doc["resources"]["sim_spent_cents"] == 430
    facts = cli_json("inspect", task["task_id"])
    campaigns = [o for o in facts["outputs"] if o["kind"] == "campaign"]
    assert len(campaigns) == len(facts["reviews"]) == 2
    assert facts["dispositions"][0]["decision"] == "accepted"
    assert facts["dispositions"][0]["review_id"] == facts["reviews"][0]["review_id"]
    assert facts["scripts"][1]["content"]["revises_script_ref"] == {
        "id": facts["scripts"][0]["output_id"], "version": 1}
    assert facts["scripts"][0]["content"]["script"] == SCRIPT
    assert campaigns[0]["content"]["materials"] == campaigns[1]["content"]["materials"] == _campaign()["materials"]
    assert all(row["purpose"] != "script_generation" for row in facts["llm_requests"])


def _draft_script(model_script, reviews):
    task = _new_draft_ready(model_script, reviews)
    view = cli_json("inspect", task["task_id"])
    script = view["scripts"][0]
    script_ref = {"id": script["output_id"], "version": script["version"]}
    campaign_ref = _result(view, "submit_campaign")[0]["campaign_ref"]
    prefix = [_tool("search_materials", {"query": "自动卡点"}),
              _tool("propose_brief", BRIEF),
              _tool("submit_script", {"brief_version": 1, "script": SCRIPT}),
              _tool("submit_campaign", {"script_ref": script_ref, "campaign": _campaign()})]
    return task, prefix, campaign_ref


def test_imported_campaign_resume_gives_main_agent_full_edit_and_new_review(cli_env, model_script):
    task, prefix, ref = _draft_script(model_script, [_review_item(REVIEW_PASS), _review_item(REVIEW_PASS_V2)])
    prefix += [_tool("review_campaign", {"campaign_ref": ref}), _tool("save_campaign", {"campaign_ref": ref})]
    model_script(prefix + [_review_item(REVIEW_PASS), _review_item(REVIEW_PASS_V2)])
    first = cli_json("resume", task["task_id"], "--max-rounds", "2")
    assert first["status"] == "complete"
    saved_md = next(item for item in first["save"]["files"] if item["path"].endswith("campaign-v1.md"))
    edited_path = cli_env / "edited.md"
    edited = Path(saved_md["path"]).read_text(encoding="utf-8").replace("把招新现场剪成你的节奏", "把社团招新剪成你的节奏")
    edited_path.write_text(edited, encoding="utf-8")
    imported = cli_json("import-draft", task["task_id"], "--file", str(edited_path))
    assert imported["progress"]["current_output"]["status"] == "pending_review"
    output = imported["progress"]["current_output"]
    new_ref = {"id": output["output_id"], "version": output["version"]}
    model_script(prefix + [_tool("review_campaign", {"campaign_ref": new_ref}),
                           _tool("save_campaign", {"campaign_ref": new_ref}),
                           _review_item(REVIEW_PASS), _review_item(REVIEW_PASS_V2)])
    resumed = cli_json("resume", task["task_id"])
    assert resumed["status"] == "complete"
    assert resumed["save"]["output_version"] == resumed["review"]["output_version"] == 2
    assert resumed["resources"]["sim_spent_cents"] == 320
    facts = cli_json("inspect", task["task_id"])
    context = next(message for message in facts["messages"] if "[人工稿导入]" in (message["content"] or ""))
    assert "把社团招新剪成你的节奏" in context["content"]
    payload = json.loads(context["content"].split("] ", 1)[1])
    assert payload["campaign"]["script"] == SCRIPT
    assert edited_path.read_text(encoding="utf-8") == edited


def test_save_after_effect_crash_recovers_main_tool_receipt_without_new_attempt(cli_env, model_script):
    task, prefix, ref = _draft_script(model_script, _review_item(REVIEW_PASS))
    script = model_script(prefix + [_tool("review_campaign", {"campaign_ref": ref}),
                                    _tool("save_campaign", {"campaign_ref": ref}), _review_item(REVIEW_PASS)])
    crashed = run_cli_process("resume", task["task_id"], data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": "after_save_effect"})
    assert crashed.returncode == 70
    before = cli_json("inspect", task["task_id"])
    process = run_cli_process("resume", task["task_id"], data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script)})
    assert process.returncode == 0, process.stderr
    resumed = json.loads(process.stdout)
    assert resumed["status"] == "complete"
    assert resumed["progress"]["used_rounds"] == 6
    assert resumed["resources"]["sim_spent_cents"] == 230
    after = cli_json("inspect", task["task_id"])
    assert len(after["attempts"]) == len(before["attempts"]) == 4
    assert after["ledger"] == before["ledger"]
    receipt = [m for m in after["messages"] if m.get("name") == "save_campaign" and m["role"] == "tool"]
    assert len(receipt) == 1 and json.loads(receipt[0]["content"])["recovered"] is True


def test_failed_save_feedback_and_explicit_retry_keep_one_business_record(cli_env, model_script, monkeypatch):
    task, prefix, ref = _draft_script(model_script, _review_item(REVIEW_PASS))
    script = model_script(prefix + [_tool("review_campaign", {"campaign_ref": ref}),
        _tool("save_campaign", {"campaign_ref": ref}),
        _tool("stop_incomplete", {"reason": "保存失败，待文件环境修复后显式续跑。"}),
        _tool("save_campaign", {"campaign_ref": ref}), _review_item(REVIEW_PASS)])
    monkeypatch.setenv("CTA_FAIL_SAVE", "1")
    failed = cli_json("resume", task["task_id"])
    assert failed["status"] == "incomplete" and failed["save"]["status"] == "failed"
    save_id = failed["save"]["save_id"]
    assert failed["resources"]["sim_spent_cents"] == 230
    monkeypatch.delenv("CTA_FAIL_SAVE")
    process = run_cli_process("resume", task["task_id"], data_dir=cli_env / "data",
                              extra_env={"CTA_MODEL_SCRIPT": str(script)})
    assert process.returncode == 0, process.stderr
    resumed = json.loads(process.stdout)
    assert resumed["status"] == "complete" and resumed["save"]["save_id"] == save_id
    assert resumed["resources"]["sim_spent_cents"] == 240
    facts = cli_json("inspect", task["task_id"])
    assert len(facts["saves"]) == 1
    attempts = [attempt for attempt in facts["attempts"] if attempt["kind"] == "save_campaign"]
    assert [attempt["status"] for attempt in attempts] == ["failed", "success"]
