"""Strategy advice is usable beyond the original example and stays at the task's adopted version."""

import json

import pytest
from conftest import cli_json


@pytest.mark.parametrize("topic,audience", [
    ("英语口播AI字幕功能演示", "成年英语口播创作者"),
    ("周末旅行剪辑教程", "25-40岁成年旅行爱好者"),
])
def test_default_strategy_advice_does_not_prescribe_unrelated_example_facts(cli_env, topic, audience):
    task = cli_json("create", "--new", "--topic", topic, "--audience", audience,
                    "--budget", "10", "--deadline", "2026-10-30")
    comparison = cli_json("strategies", task["task_id"])
    advice = json.dumps(comparison["directions"], ensure_ascii=False)
    assert "校园" not in advice
    assert "M-001" not in advice
    assert "M-005" not in advice
    assert {d["direction"] for d in comparison["directions"]} == {"functional_demo", "user_scenario", "tutorial"}
    assert all(d["material_needs"] and d["evidence_queries"] for d in comparison["directions"])


def test_existing_task_uses_its_strategy_version_after_default_changes(cli_env, model_script, monkeypatch):
    monkeypatch.setenv("CTA_STRATEGY_VERSION", "2026-10-29.v2")
    task = cli_json("create", "--topic", "英语口播字幕", "--budget", "10", "--deadline", "2026-10-30")
    task_id = task["task_id"]
    original = cli_json("strategies", task_id)
    monkeypatch.setenv("CTA_STRATEGY_VERSION", "2026-10-29.v1")
    status = cli_json("status", task_id)
    comparison = cli_json("strategies", task_id)
    assert status["versions"]["strategy"] == "2026-10-29.v2"
    assert status["strategy"]["directions"] == [
        {key: d[key] for key in ("direction", "label", "expression_goal", "material_needs", "evidence_queries",
                                "required_queries", "risk")}
        for d in original["directions"]
    ]
    assert comparison["strategy_version"] == original["strategy_version"]
    assert comparison["directions"] == original["directions"]
    model_script([{"name": "compare_strategies", "arguments": {
        "search_mode": "search_basic", "generation_mode": "generate_direct",
    }}])
    cli_json("run", task_id, "--max-rounds", "1")
    facts = cli_json("inspect", task_id)
    returned = next(json.loads(m["content"]) for m in facts["messages"]
                    if m["role"] == "tool" and m["name"] == "compare_strategies")
    assert returned["strategy_version"] == "2026-10-29.v2"
    assert returned["directions"] == original["directions"]
