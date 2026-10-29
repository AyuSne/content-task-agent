"""Open creative directions and resource feedback through the public CLI."""

import json

from conftest import cli_json, run_scripted_stage
from test_campaign_draft import BRIEF, _create, _tool


def _responses(view, name):
    return [json.loads(m["content"]) for m in view["messages"] if m["role"] == "tool" and m["name"] == name]


def test_new_toolset_excludes_fixed_comparison_selection_and_generation(cli_env, model_script):
    task = _create()
    names = ["read_skill", "update_plan", "review_campaign", "save_campaign", "search_materials",
             "read_material", "estimate_cost", "propose_brief", "submit_script", "submit_campaign",
             "request_clarification", "record_dispositions", "stop_incomplete"]
    model_script([_tool("stop_incomplete", {"reason": "检查模型工具集合"}, expected_tool_names=names)])
    result = cli_json("run", task["task_id"])
    assert result["status"] == "incomplete"
    facts = cli_json("inspect", task["task_id"])
    assert facts["resources"]["sim_spent_cents"] == 0
    assert not facts["ledger"] and not facts["outputs"]
    assert _responses(facts, "stop_incomplete")[0]["ok"] is True


def test_action_estimates_compare_feasible_paths_without_spending_or_locking_order(cli_env, model_script):
    task = _create(budget="2.50")
    expensive = ["search_extended", "submit_script", "revision", "review", "save"]
    feasible = ["search_basic", "submit_script", "review", "save"]
    model_script([_tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
                  _tool("estimate_cost", {"actions": expensive}),
                  _tool("estimate_cost", {"actions": feasible}),
                  _tool("propose_brief", BRIEF),
                  _tool("stop_incomplete", {"reason": "成本比较结束，保留简报。"})])
    result = cli_json("run", task["task_id"])
    facts = cli_json("inspect", task["task_id"])
    high, low = [x["estimate"] for x in _responses(facts, "estimate_cost")]
    assert high["version"] == low["version"] == facts["versions"]["pricing"] == "2026-10-30.v2"
    assert high["total_cost_cents"] == 390 and high["affordable"] is False
    assert low["total_cost_cents"] == 230 and low["affordable"] is True
    assert high["within_deadline"] is True and low["within_deadline"] is True
    assert high["incurred_cents"] == low["incurred_cents"] == 20
    assert [x["mode"] for x in low["lines"]] == feasible
    assert low["nature"] == high["nature"] == "estimated"
    assert result["resources"]["sim_spent_cents"] == 20
    assert [row["amount_cents"] for row in facts["ledger"]] == [20]
    assert facts["briefs"][0]["version"] == 1


def test_budget_and_deadline_estimates_are_separate_and_infeasible_task_stops(cli_env, model_script):
    short = _create(topic="截止不足", deadline="2026-10-29T00:00:12+08:00")
    small = _create(topic="预算不足", budget="0.50")
    model_script([_tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
                  _tool("estimate_cost", {"actions": ["search_basic", "submit_script", "review", "save"]}),
                  _tool("stop_incomplete", {"reason": "资源不足以完成交付", "gaps": [
                      {"gap": "资源不足", "impact": "无法完成核验和保存"}]})])
    views = []
    for task in (short, small):
        result = cli_json("run", task["task_id"])
        assert result["status"] == "incomplete" and result["resources"]["sim_spent_cents"] == 20
        assert any(gap["source"] == "stop" for gap in result["gaps"])
        view = cli_json("inspect", task["task_id"])
        assert not view["outputs"] and [row["amount_cents"] for row in view["ledger"]] == [20]
        views.append(_responses(view, "estimate_cost")[0]["estimate"])
    assert views[0]["affordable"] is True and views[0]["within_deadline"] is False
    assert views[0]["time_gap_ms"] < 0 and views[0]["budget_gap_cents"] >= 0
    assert views[1]["affordable"] is False and views[1]["within_deadline"] is True
    assert views[1]["budget_gap_cents"] < 0 and views[1]["time_gap_ms"] >= 0


def test_estimates_remain_separate_from_actual_cost_and_unknown_model_and_activity_costs(cli_env, model_script):
    task = _create()
    fresh = cli_json("status", task["task_id"])
    fees = fresh["resources"]["simulated_tool_fee"]
    assert fees["category"] == "simulated_tool_fee" and fees["unit"] == "CNY_cent"
    assert fees["source"] == "pricing:2026-10-30.v2" and fees["incurred_cents"] == 0
    assert fees["estimated"] is None and fees["reserved"] is None
    assert fresh["resources"]["real_model_usage"]["amount"]["status"] == "unknown"
    assert fresh["resources"]["activity_costs"]["status"] == "not_applicable"
    model_script([_tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
                  _tool("estimate_cost", {"actions": ["submit_script", "review", "save"]}),
                  _tool("stop_incomplete", {"reason": "资源口径检查"})])
    doc = cli_json("run", task["task_id"])
    fees = doc["resources"]["simulated_tool_fee"]
    assert fees["incurred_cents"] == 20 and fees["estimated"]["total_cost_cents"] == 210
    assert fees["estimated"]["nature"] == "estimated" and fees["reserved"] is None
    assert doc["resources"]["sim_spent_cents"] == 20
    assert len(cli_json("inspect", task["task_id"])["ledger"]) == 1


def test_model_uses_cost_feedback_to_choose_feasible_path_and_deliver(cli_env, monkeypatch):
    from pathlib import Path

    from test_autonomous_delivery import ResponsiveModel
    from test_review_save import REVIEW_PASS

    from cta.model import ModelResponse, ToolCall

    class CostAwareModel(ResponsiveModel):
        def complete(self, messages, tools, *, cursor, channel="main", json_mode=False):
            if channel == "main" and cursor in (1, 2):
                if cursor == 2:
                    prior = next(json.loads(m["content"]) for m in reversed(messages)
                                 if m["role"] == "tool" and "estimate" in m["content"])
                    assert prior["estimate"]["affordable"] is False
                actions = (["search_extended", "submit_script", "revision", "review", "save"]
                           if cursor == 1 else ["submit_script", "review", "save"])
                return ModelResponse(tool_calls=(ToolCall(f"estimate_{cursor}", "estimate_cost",
                    json.dumps({"actions": actions})),), finish_reason="tool_calls", model="cost-mock", mock=True)
            if channel == "main" and cursor == 3:
                prior = next(json.loads(m["content"]) for m in reversed(messages)
                             if m["role"] == "tool" and "estimate" in m["content"])
                assert prior["estimate"]["affordable"] is True
            shifted = cursor - 2 if channel == "main" and cursor > 2 else cursor
            return super().complete(messages, tools, cursor=shifted, channel=channel, json_mode=json_mode)

    client = CostAwareModel([REVIEW_PASS])
    monkeypatch.setattr("cta.loop.create_client", lambda config: client)
    monkeypatch.setattr("cta.review.create_client", lambda config: client)
    task = _create(budget="2.50")
    saved = cli_json("run", task["task_id"])
    assert saved["status"] == "complete" and saved["save"]["verified"] is True
    assert saved["resources"]["sim_spent_cents"] == 230
    facts = cli_json("inspect", task["task_id"])
    high, low = [item["estimate"] for item in _responses(facts, "estimate_cost")]
    assert high["affordable"] is False and high["total_cost_cents"] == 390
    assert low["affordable"] is True and low["total_cost_cents"] == 210
    assert [row["amount_cents"] for row in facts["ledger"]] == [20, 120, 80, 10]
    assert [a["kind"] for a in facts["attempts"]] == ["search_materials", "submit_script", "review_campaign", "save_campaign"]
    assert not facts["strategy_selections"]
    report_path = next(item["path"] for item in saved["save"]["files"] if item["path"].endswith("execution-report.md"))
    report = Path(report_path).read_text(encoding="utf-8")
    assert "成本口径与账本" in report and "金额未知" in report
    trace_path = next(item["path"] for item in saved["save"]["files"] if item["path"].endswith("trace.jsonl"))
    events = [json.loads(line) for line in Path(trace_path).read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "estimate" for event in events) == 2


def test_stop_incomplete_records_concrete_gaps_and_preserves_success(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "stop_incomplete",
                {
                    "reason": "剩余预算无法覆盖核验与保存，且没有可行替代。",
                    "gaps": [
                        {"gap": "预算无法覆盖核验与保存", "impact": "不能形成可交付方案"},
                        {"gap": "现有资料不足以支撑关键能力主张", "impact": "需要补查或由用户决定"},
                    ],
                },
            ),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert "预算无法覆盖" in doc["reason"]
    stop_gaps = [gap for gap in doc["gaps"] if gap["source"] == "stop"]
    assert len(stop_gaps) == 2
    assert stop_gaps[0]["question"] == "预算无法覆盖核验与保存"
    assert doc["resources"]["sim_spent_cents"] == 20

    inspected = cli_json("inspect", task["task_id"])
    assert inspected["stops"][0]["kind"] == "model_stop"
    assert len(inspected["stops"][0]["gaps"]) == 2

def test_search_failure_is_preserved_and_model_rechecks(cli_env, model_script, monkeypatch):
    monkeypatch.setenv("CTA_FAIL_SEARCH", "1")
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool("search_materials", {"query": "自动卡点 能力 范围", "mode": "basic"}),
            _tool("propose_brief", BRIEF),
            _tool("stop_incomplete", {"reason": "已完成补查与简报，按计划停止。", "gaps": [{"gap": "未生成方案", "impact": "本样例仅验证检索失败处理"}]}),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["brief"]["version"] == 1
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["resources"]["sim_spent_cents"] == 40

    inspected = cli_json("inspect", task["task_id"])
    attempts = [item for item in inspected["attempts"] if item["kind"] == "search_materials"]
    assert [item["status"] for item in attempts] == ["failed", "success"]
    assert all(item["cost_cents"] == 20 for item in attempts)
    results = [
        json.loads(message["content"])
        for message in inspected["messages"]
        if message["name"] == "search_materials"
    ]
    assert results[0]["ok"] is False
    assert results[0]["error"] == "tool_failed"
    assert results[0]["injected"] is True
    assert results[1]["ok"] is True
    assert len(inspected["ledger"]) == 2
    assert [row["amount_cents"] for row in inspected["ledger"]] == [20, 20]

def test_round_cap_records_limit_gap_and_keeps_spend(cli_env, model_script):
    task = _create()
    limit = task["progress"]["max_rounds"]
    model_script([{"type": "text", "content": f"第 {index} 轮没有动作"} for index in range(limit)])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["progress"]["used_rounds"] == limit
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["stops"][0]["kind"] == "max_rounds"
    assert inspected["stops"][0]["gaps"][0]["gap"].startswith("达到主循环轮数上限")
    assert doc["gaps"][0]["source"] == "stop"
