"""Resource contract exercised through the public CLI and scripted model seam."""
import json

from conftest import cli_json
from test_campaign_draft import _create, _tool


def test_existing_task_keeps_its_pricing_when_environment_default_changes(cli_env, model_script, monkeypatch):
    from test_brief_loop import BRIEF
    from test_direct_script import SCRIPT

    task = _create()
    monkeypatch.setenv("CTA_PRICING_VERSION", "test-direct-alternate")
    new_task = _create(topic="新版价目任务")
    assert new_task["pricing_overview"]["version"] == "test-direct-alternate"
    assert new_task["pricing_overview"]["modes"]["search_basic"]["cost_cents"] == 45
    assert cli_json("status", task["task_id"])["versions"]["pricing"] == "2026-10-30.v1"
    assert cli_json("status", task["task_id"])["pricing_overview"]["modes"]["search_basic"]["cost_cents"] == 20
    model_script([
        _tool("estimate_cost", {"actions": ["search_basic", "submit_script", "review", "save"]}),
        _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
        _tool("propose_brief", BRIEF),
        _tool("submit_script", {"brief_version": 1, "script": SCRIPT}),
        _tool("stop_incomplete", {"reason": "测试计量"}),
    ])
    result = cli_json("run", task["task_id"])
    assert result["resources"]["sim_spent_cents"] == 140
    inspected = cli_json("inspect", task["task_id"])
    overview_message = next(m for m in inspected["messages"] if "[适用费用能力概览]" in (m["content"] or ""))
    assert "2026-10-30.v1" in overview_message["content"]
    assert "failure_rule" in overview_message["content"]
    estimate = json.loads(next(m["content"] for m in inspected["messages"] if m["name"] == "estimate_cost"))
    assert estimate["estimate"]["total_cost_cents"] == 230
    assert estimate["estimate"]["version"] == "2026-10-30.v1"
    assert estimate["estimate"]["nature"] == "estimated"
    assert len(inspected["ledger"]) == 2
    assert inspected["ledger"][0]["amount_cents"] == 20
    assert inspected["ledger"][1]["amount_cents"] == 120
    model_script([_tool("propose_brief", BRIEF),
                  _tool("submit_script", {"brief_version": 1, "script": SCRIPT}),
                  _tool("stop_incomplete", {"reason": "测试计量"})])
    new_result = cli_json("run", new_task["task_id"])
    assert new_result["resources"]["sim_spent_cents"] == 250


def test_action_list_estimate_reports_missing_delivery_and_prices_selected_actions(cli_env, model_script):
    task = _create()
    model_script([
        _tool("estimate_cost", {"actions": ["search_basic", "revision"]}),
        _tool("estimate_cost", {"actions": ["search_basic", "revision", "review", "save"]}),
        _tool("stop_incomplete", {"reason": "测试估算"}),
    ])
    result = cli_json("run", task["task_id"])
    assert result["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    responses = [json.loads(m["content"]) for m in inspected["messages"] if m["name"] == "estimate_cost"]
    assert responses[0]["ok"] is False
    assert set(responses[0]["missing_actions"]) == {"review", "save"}
    assert responses[1]["estimate"]["total_cost_cents"] == 230
    assert responses[1]["estimate"]["total_duration_ms"] == 17000
    assert [line["mode"] for line in responses[1]["estimate"]["lines"]] == ["search_basic", "revision", "review", "save"]
    assert not inspected["ledger"]


def test_review_and_verified_save_use_task_price_after_default_changes(cli_env, model_script, monkeypatch):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    monkeypatch.setenv("CTA_PRICING_VERSION", "test-alternate")
    reviewed = cli_json("review", task["task_id"])
    assert reviewed["resources"]["sim_spent_cents"] == 220
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["resources"]["sim_spent_cents"] == 230
    attempts = cli_json("inspect", task["task_id"])["attempts"]
    assert [item["cost_cents"] for item in attempts[-2:]] == [80, 10]


def test_budget_and_simulated_deadline_reject_before_search_starts(cli_env, model_script):
    for task in (_create(topic="低预算", budget="0.10"),
                 _create(topic="截止不足", deadline="2026-10-29T00:00:01+08:00")):
        model_script([
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool("stop_incomplete", {"reason": "资源不足"}),
        ])
        result = cli_json("run", task["task_id"])
        inspected = cli_json("inspect", task["task_id"])
        assert result["resources"]["sim_spent_cents"] == 0
        assert not inspected["attempts"]
        assert not inspected["ledger"]
        feedback = json.loads(next(m["content"] for m in inspected["messages"] if m["name"] == "search_materials"))
        assert feedback["ok"] is False
