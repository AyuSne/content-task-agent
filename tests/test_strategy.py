"""Ticket 07: feedback-driven creative strategy, full-path resources and stops.

Seam: the public CLI. The model is injected at its system boundary; the
comparison, reservations, accounting and stop facts are real product behaviour.
"""

import json
from pathlib import Path

from conftest import cli_json, run_scripted_stage
from test_campaign_draft import BRIEF, CAMPAIGN, _campaign_tool, _create, _generation_item


def _tool(name, arguments, **extra):
    return {"type": "tool", "name": name, "arguments": arguments, **extra}


def test_compare_strategies_returns_versioned_full_paths_without_charge(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "compare_strategies",
                {"search_mode": "search_basic", "generation_mode": "generate_direct"},
            ),
            _tool("stop_incomplete", {"reason": "仅比较策略，暂不生成。"}),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["resources"]["sim_spent_cents"] == 20

    inspected = cli_json("inspect", task["task_id"])
    result = json.loads(
        next(
            message["content"]
            for message in inspected["messages"]
            if message["name"] == "compare_strategies"
        )
    )
    assert result["ok"] is True
    assert result["strategy_version"] == "2026-10-29.v1"
    assert result["pricing_version"] == "2026-10-29.v1"
    assert result["incurred_cents"] == 20
    assert result["remaining_budget_cents"] == 980

    directions = {item["direction"]: item for item in result["directions"]}
    assert set(directions) == {"functional_demo", "user_scenario", "tutorial"}
    assert directions["functional_demo"]["expression_goal"]
    assert directions["functional_demo"]["material_needs"]
    assert directions["functional_demo"]["risk"]
    assert directions["functional_demo"]["required_queries"] == 2
    assert directions["user_scenario"]["required_queries"] == 3
    assert directions["functional_demo"]["path"]["total_cost_cents"] == 20 + 20 + 120 + 80 + 10
    assert directions["user_scenario"]["path"]["total_cost_cents"] == 20 + 20 + 20 + 120 + 80 + 10
    steps = [line["step"] for line in directions["functional_demo"]["path"]["lines"]]
    assert steps == ["search", "search", "generation", "review", "save"]
    assert directions["functional_demo"]["feasible"] is True
    assert directions["functional_demo"]["budget_feasible"] is True
    assert directions["functional_demo"]["deadline_feasible"] is True
    assert directions["functional_demo"]["alternatives"]

    modes = result["mode_table"]["modes"]
    assert modes["generate_multi"]["max_internal_requests"] == 3
    assert modes["generate_multi"]["candidates"] == 3
    assert modes["search_extended"]["max_internal_requests"] == 3
    assert modes["review"]["max_requests"] == 3
    assert modes["review"]["max_readbacks"] == 2
    assert all(modes[key]["failure_rule"] for key in modes)
    assert result["cheapest_feasible"]["total_cost_cents"] == 250

    assert inspected["estimates"][0]["kind"] == "strategy_comparison"
    assert inspected["estimates"][0]["feasible"] is True
    assert inspected["estimates"][0]["pricing_version"] == "2026-10-29.v1"
    assert inspected["ledger"][0]["amount_cents"] == 20
    assert len(inspected["ledger"]) == 1


def test_strategies_cli_view_is_read_only_and_complete(cli_env):
    task = _create()
    before = cli_json("status", task["task_id"])
    view = cli_json("strategies", task["task_id"])
    after = cli_json("status", task["task_id"])

    assert view["strategy_version"] == "2026-10-29.v1"
    assert view["pricing_version"] == "2026-10-29.v1"
    assert view["incurred_cents"] == 0
    assert len(view["directions"]) == 3
    assert view["mode_table"]["modes"]["generate_direct"]["max_internal_requests"] == 1

    assert before["progress"]["used_rounds"] == after["progress"]["used_rounds"] == 0
    assert before["resources"]["sim_spent_cents"] == after["resources"]["sim_spent_cents"] == 0
    assert before["versions"]["strategy"] == "2026-10-29.v1"


def test_choose_strategy_rejects_infeasible_path_then_records_feasible_alternative(cli_env, model_script):
    task = _create(budget="2.50")
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_extended",
                    "generation_mode": "generate_multi",
                    "search_queries": 1,
                    "rationale": "情境方向资料更多，先考虑高配路径。",
                },
            ),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 1,
                    "rationale": "高配路径超出剩余预算，改用可行替代。",
                },
            ),
            _tool("stop_incomplete", {"reason": "仅验证策略选择与预留，不生成。"}),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["resources"]["sim_spent_cents"] == 20

    inspected = cli_json("inspect", task["task_id"])
    choices = [
        json.loads(message["content"])
        for message in inspected["messages"]
        if message["name"] == "choose_strategy"
    ]
    assert choices[0]["ok"] is False
    assert choices[0]["error"] == "strategy_infeasible"
    assert choices[0]["budget_feasible"] is False
    assert choices[0]["deadline_feasible"] is True
    assert choices[0]["alternatives"][0]["search_mode"] == "search_basic"
    assert choices[0]["alternatives"][0]["generation_mode"] == "generate_direct"
    assert choices[0]["alternatives"][0]["total_cost_cents"] == 230

    assert choices[1]["ok"] is True
    assert choices[1]["selection_id"].startswith("st_")
    assert choices[1]["total_cost_cents"] == 230
    assert len(inspected["strategy_selections"]) == 1
    selection = inspected["strategy_selections"][0]
    assert selection["direction"] == "user_scenario"
    assert selection["generation_mode"] == "generate_direct"
    assert selection["total_cost_cents"] == 230
    assert selection["nature"] == "reserved"

    assert len(inspected["ledger"]) == 1
    assert inspected["ledger"][0]["amount_cents"] == 20
    assert inspected["resources"]["simulated_tool_fee"]["incurred_cents"] == 20
    assert inspected["resources"]["simulated_tool_fee"]["reserved"]["total_cost_cents"] == 230
    assert inspected["resources"]["simulated_tool_fee"]["estimated"]["total_cost_cents"] == 230


def test_choose_improvement_requires_expected_benefit_and_records_reservation(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_multi",
                    "search_queries": 0,
                    "improvement": True,
                    "rationale": "多候选择优可以比较开场。",
                },
            ),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_multi",
                    "search_queries": 0,
                    "improvement": True,
                    "expected_benefit": "比较三个开场候选，降低单一候选的风险",
                    "rationale": "多候选的额外模拟费用可由更高确定性抵消。",
                },
            ),
            _tool("stop_incomplete", {"reason": "仅验证改进选择的记录。"}),
        ]
    )
    run_scripted_stage("run", task["task_id"])
    inspected = cli_json("inspect", task["task_id"])
    choices = [
        json.loads(message["content"])
        for message in inspected["messages"]
        if message["name"] == "choose_strategy"
    ]
    assert choices[0]["ok"] is False
    assert "expected_benefit" in choices[0]["message"]
    assert choices[1]["ok"] is True
    selection = inspected["strategy_selections"][0]
    assert selection["improvement"] is True
    assert selection["expected_benefit"] == "比较三个开场候选，降低单一候选的风险"
    assert selection["total_cost_cents"] == 300 + 80 + 10
    assert inspected["resources"]["simulated_tool_fee"]["reserved"]["improvement"] is True
    assert inspected["resources"]["sim_spent_cents"] == 20


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


def test_resources_separate_incurred_estimated_reserved_and_unknown_vs_not_applicable(cli_env, model_script):
    task = _create()
    fresh = cli_json("status", task["task_id"])
    fees = fresh["resources"]["simulated_tool_fee"]
    assert fees["category"] == "simulated_tool_fee"
    assert fees["unit"] == "CNY_cent"
    assert fees["source"] == "pricing:2026-10-29.v1"
    assert fees["incurred_cents"] == 0
    assert fees["estimated"] is None
    assert fees["reserved"] is None
    assert fresh["resources"]["real_model_usage"]["amount"]["status"] == "unknown"
    assert fresh["resources"]["activity_costs"]["status"] == "not_applicable"
    assert "real_model_usage.amount" in fresh["resources"]["unknown"]
    assert "activity_costs" in fresh["resources"]["not_applicable"]

    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "choose_strategy",
                {
                    "direction": "functional_demo",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 0,
                    "rationale": "预算足够覆盖直接生成与核验保存。",
                },
            ),
            _tool("stop_incomplete", {"reason": "仅核对资源口径。"}),
        ]
    )
    run_scripted_stage("run", task["task_id"])
    doc = cli_json("status", task["task_id"])
    fees = doc["resources"]["simulated_tool_fee"]
    assert fees["incurred_cents"] == 20
    assert fees["estimated"]["total_cost_cents"] == 210
    assert fees["estimated"]["nature"] == "estimated"
    assert fees["reserved"]["total_cost_cents"] == 210
    assert fees["reserved"]["nature"] == "reserved"
    assert doc["resources"]["sim_spent_cents"] == 20
    assert len(cli_json("inspect", task["task_id"])["ledger"]) == 1


def test_improvement_after_workable_baseline_keeps_both_successful_attempts(cli_env, model_script):
    from test_review_save import REVIEW_PASS, _review_item

    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool("propose_brief", BRIEF),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 0,
                    "rationale": "直接生成已能形成可交付脚本。",
                },
            ),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _generation_item(),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_multi",
                    "search_queries": 0,
                    "improvement": True,
                    "expected_benefit": "比较三个开场候选，降低单一候选的表达风险",
                    "rationale": "基础方案已可行；额外投入有明确改善目标，且完整路径仍可行。",
                },
            ),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_multi"}),
            _generation_item(),
            _generation_item(),
            _generation_item(),
            _campaign_tool(CAMPAIGN),
            _review_item(REVIEW_PASS),
        ]
    )
    first = run_scripted_stage("run", task["task_id"])
    assert first["progress"]["phase"] == "brief_ready"
    draft = run_scripted_stage("run", task["task_id"])
    assert draft["progress"]["phase"] == "draft_ready"
    assert draft["resources"]["sim_spent_cents"] == 20 + 120 + 300

    reviewed = cli_json("review", task["task_id"])
    saved = cli_json("save", task["task_id"])
    assert reviewed["review"]["verdict"] == "pass"
    assert saved["status"] == "complete"
    assert saved["resources"]["sim_spent_cents"] == 20 + 120 + 300 + 80 + 10

    inspected = cli_json("inspect", task["task_id"])
    selections = inspected["strategy_selections"]
    assert len(selections) == 2
    assert selections[0]["improvement"] is False
    assert selections[1]["improvement"] is True
    assert selections[1]["expected_benefit"] == "比较三个开场候选，降低单一候选的表达风险"
    assert selections[1]["total_cost_cents"] == 390

    attempts = [item for item in inspected["attempts"] if item["kind"] == "generate_script"]
    assert [item["mode"] for item in attempts] == ["generate_direct", "generate_multi"]
    assert all(item["status"] == "success" for item in attempts)
    generation_requests = [row for row in inspected["llm_requests"] if row["purpose"] == "script_generation"]
    assert len(generation_requests) == 4

    resources = saved["resources"]
    assert resources["simulated_tool_fee"]["incurred_cents"] == 530
    assert resources["simulated_tool_fee"]["reserved"]["total_cost_cents"] == 390
    assert resources["activity_costs"]["status"] == "unknown"
    assert "activity_costs" in resources["unknown"]
    assert "activity_costs" not in resources["not_applicable"]
    assert resources["real_model_usage"]["amount"]["status"] == "unknown"


def test_budget_feedback_chooses_feasible_alternative_and_completes(cli_env, model_script):
    from test_review_save import REVIEW_PASS, _review_item

    task = _create(budget="2.50")
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "compare_strategies",
                {
                    "search_mode": "search_extended",
                    "generation_mode": "generate_multi",
                    "search_queries": 1,
                    "directions": ["user_scenario", "tutorial"],
                },
            ),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_extended",
                    "generation_mode": "generate_multi",
                    "search_queries": 1,
                    "rationale": "先考虑高配路径。",
                },
            ),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 1,
                    "rationale": "高配超出剩余预算；基本检索加直接生成仍能覆盖完整交付路径。",
                },
            ),
            _tool("propose_brief", BRIEF),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _generation_item(),
            _campaign_tool(),
            _review_item(REVIEW_PASS),
        ]
    )
    first = run_scripted_stage("run", task["task_id"])
    assert first["progress"]["phase"] == "brief_ready"
    draft = run_scripted_stage("run", task["task_id"])
    assert draft["progress"]["phase"] == "draft_ready"
    cli_json("review", task["task_id"])
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["resources"]["sim_spent_cents"] == 20 + 120 + 80 + 10

    inspected = cli_json("inspect", task["task_id"])
    comparison = json.loads(
        next(message["content"] for message in inspected["messages"] if message["name"] == "compare_strategies")
    )
    assert comparison["ok"] is True
    assert all(item["feasible"] is False for item in comparison["directions"])
    assert comparison["cheapest_feasible"]["search_mode"] == "search_basic"
    assert comparison["cheapest_feasible"]["generation_mode"] == "generate_direct"
    choices = [
        json.loads(message["content"])
        for message in inspected["messages"]
        if message["name"] == "choose_strategy"
    ]
    assert choices[0]["ok"] is False
    assert choices[0]["alternatives"][0]["total_cost_cents"] == 230
    assert choices[1]["ok"] is True

    assert [attempt["kind"] for attempt in inspected["attempts"]] == [
        "search_materials",
        "generate_script",
        "review_campaign",
        "save_campaign",
    ]
    assert [attempt["mode"] for attempt in inspected["attempts"]] == [
        "search_basic",
        "generate_direct",
        "review",
        "save",
    ]
    assert [row["amount_cents"] for row in inspected["ledger"]] == [20, 120, 80, 10]
    selection = inspected["strategy_selections"][0]
    assert selection["search_mode"] == "search_basic"
    assert selection["generation_mode"] == "generate_direct"
    assert selection["total_cost_cents"] == 230

    report_path = next(
        item["path"] for item in saved["save"]["files"] if item["path"].endswith("execution-report.md")
    )
    report = Path(report_path).read_text(encoding="utf-8")
    assert "## 创作策略与反馈" in report
    assert "## 成本口径与账本" in report
    assert "预留不是实际支出" in report
    assert "金额未知" in report
    trace_path = next(item["path"] for item in saved["save"]["files"] if item["path"].endswith("trace.jsonl"))
    trace_events = [
        json.loads(line)
        for line in Path(trace_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["event"] == "estimate" for event in trace_events)
    assert any(
        event["event"] == "strategy_selection" and event["generation_mode"] == "generate_direct"
        for event in trace_events
    )


def test_no_feasible_path_stops_with_gap_and_records_no_selection(cli_env, model_script):
    task = _create(budget="0.50")
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "compare_strategies",
                {
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 0,
                },
            ),
            _tool(
                "choose_strategy",
                {
                    "direction": "functional_demo",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 0,
                    "rationale": "尝试最低成本路径。",
                },
            ),
            _tool(
                "stop_incomplete",
                {
                    "reason": "剩余预算不足以完成生成、核验与保存。",
                    "gaps": [{"gap": "预算缺口无法覆盖完整交付路径", "impact": "无法形成可交付方案"}],
                },
            ),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["resources"]["sim_spent_cents"] == 20
    assert any(gap["source"] == "stop" for gap in doc["gaps"])

    inspected = cli_json("inspect", task["task_id"])
    comparison = json.loads(
        next(message["content"] for message in inspected["messages"] if message["name"] == "compare_strategies")
    )
    assert comparison["cheapest_feasible"] is None
    assert all(item["budget_feasible"] is False for item in comparison["directions"])
    assert all(item["deadline_feasible"] is True for item in comparison["directions"])
    choice = json.loads(
        next(message["content"] for message in inspected["messages"] if message["name"] == "choose_strategy")
    )
    assert choice["ok"] is False
    assert choice["error"] == "strategy_infeasible"
    assert choice["alternatives"] == []
    assert choice["infeasibility"]["budget"]
    assert choice["infeasibility"]["deadline"] is None
    assert inspected["strategy_selections"] == []
    assert inspected["outputs"] == []


def test_budget_and_deadline_feasibility_are_reported_separately(cli_env, model_script):
    short_deadline = _create(deadline="2026-10-29T00:00:12+08:00")
    small_budget = _create(budget="0.50")
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool(
                "compare_strategies",
                {"search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 1},
            ),
            _tool(
                "stop_incomplete",
                {"reason": "按当前资源缺口停止。", "gaps": [{"gap": "资源不足", "impact": "无法完成交付"}]},
            ),
        ]
    )
    for task in (short_deadline, small_budget):
        doc = run_scripted_stage("run", task["task_id"])
        assert doc["status"] == "incomplete"

    deadline_doc = cli_json("inspect", short_deadline["task_id"])
    deadline_compare = json.loads(
        next(message["content"] for message in deadline_doc["messages"] if message["name"] == "compare_strategies")
    )
    item = deadline_compare["directions"][0]
    assert item["budget_feasible"] is True
    assert item["deadline_feasible"] is False
    assert item["infeasibility"]["budget"] is None
    assert item["infeasibility"]["deadline"]
    assert item["time_gap_ms"] < 0

    budget_doc = cli_json("inspect", small_budget["task_id"])
    budget_compare = json.loads(
        next(message["content"] for message in budget_doc["messages"] if message["name"] == "compare_strategies")
    )
    item = budget_compare["directions"][0]
    assert item["budget_feasible"] is False
    assert item["deadline_feasible"] is True
    assert item["infeasibility"]["budget"]
    assert item["infeasibility"]["deadline"] is None


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


def test_generation_failure_reestimate_and_alternative_then_complete(cli_env, model_script):
    from test_review_save import REVIEW_PASS, _review_item

    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool("propose_brief", BRIEF),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_multi",
                    "search_queries": 1,
                    "rationale": "先尝试多候选择优。",
                },
            ),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_multi"}),
            _generation_item(type="error", message="candidate upstream 500"),
            _generation_item(type="error", message="candidate upstream 500"),
            _generation_item(type="error", message="candidate upstream 500"),
            _tool(
                "estimate_cost",
                {"search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 1},
            ),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_direct",
                    "search_queries": 1,
                    "rationale": "多候选全部失败，重新估算后改用直接生成。",
                },
            ),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _generation_item(),
            _campaign_tool(),
            _review_item(REVIEW_PASS),
        ]
    )
    first = run_scripted_stage("run", task["task_id"])
    assert first["progress"]["phase"] == "brief_ready"
    draft = run_scripted_stage("run", task["task_id"])
    assert draft["progress"]["phase"] == "draft_ready"
    assert draft["resources"]["sim_spent_cents"] == 20 + 300 + 120

    inspected = cli_json("inspect", task["task_id"])
    attempts = [item for item in inspected["attempts"] if item["kind"] == "generate_script"]
    assert [item["mode"] for item in attempts] == ["generate_multi", "generate_direct"]
    assert [item["status"] for item in attempts] == ["failed", "success"]
    assert attempts[0]["cost_cents"] == 300
    estimate = json.loads(
        next(message["content"] for message in inspected["messages"] if message["name"] == "estimate_cost")
    )["estimate"]
    assert estimate["incurred_cents"] == 320
    assert estimate["remaining_budget_cents"] == 680
    assert estimate["affordable"] is True
    assert estimate["within_deadline"] is True
    assert inspected["strategy_selections"][-1]["generation_mode"] == "generate_direct"
    assert len([row for row in inspected["llm_requests"] if row["purpose"] == "script_generation"]) == 4

    cli_json("review", task["task_id"])
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["resources"]["sim_spent_cents"] == 20 + 300 + 120 + 80 + 10


def test_partial_generation_success_keeps_valid_candidate_and_cost(cli_env, model_script):
    from test_review_save import REVIEW_PASS, _review_item

    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
            _tool("propose_brief", BRIEF),
            _tool(
                "choose_strategy",
                {
                    "direction": "user_scenario",
                    "search_mode": "search_basic",
                    "generation_mode": "generate_multi",
                    "search_queries": 0,
                    "rationale": "多候选可比较开场。",
                },
            ),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_multi"}),
            _generation_item(),
            _generation_item(type="error", message="candidate upstream 500"),
            _generation_item(type="length", content="{\"strategy\":\"user_scenario\""),
            _campaign_tool(),
            _review_item(REVIEW_PASS),
        ]
    )
    first = run_scripted_stage("run", task["task_id"])
    assert first["progress"]["phase"] == "brief_ready"
    draft = run_scripted_stage("run", task["task_id"])
    assert draft["progress"]["phase"] == "draft_ready"

    inspected = cli_json("inspect", task["task_id"])
    result = json.loads(
        next(message["content"] for message in inspected["messages"] if message["name"] == "generate_script")
    )
    assert result["ok"] is True
    assert result["partial"] is True
    assert result["candidate_count"] == 1
    assert result["expected_candidates"] == 3
    assert result["internal_requests"] == 3
    assert result["candidate_errors"]
    attempt = next(item for item in inspected["attempts"] if item["kind"] == "generate_script")
    assert attempt["status"] == "success"
    assert '"partial": true' in attempt["meta_json"]
    assert len([row for row in inspected["llm_requests"] if row["purpose"] == "script_generation"]) == 3

    cli_json("review", task["task_id"])
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["resources"]["sim_spent_cents"] == 20 + 300 + 80 + 10


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
