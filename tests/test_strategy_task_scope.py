"""Open strategies remain specific to each task through the public CLI."""

import copy

import pytest
from conftest import cli_json
from test_brief_loop import BRIEF
from test_campaign_delivery_new import _campaign, _result
from test_campaign_draft import _tool
from test_direct_script import SCRIPT


@pytest.mark.parametrize("topic,audience,direction", [
    ("英语口播字幕功能演示", "成年英语口播创作者", "口播演示与字幕教程结合"),
    ("周末旅行剪辑教程", "25-40岁成年旅行爱好者", "旅行过程叙事与教程结合"),
])
def test_open_strategy_uses_current_task_content_without_forced_example(cli_env, model_script, topic, audience, direction):
    task = cli_json("create", "--new", "--topic", topic, "--audience", audience,
                    "--budget", "10", "--deadline", "2026-10-30")
    brief = copy.deepcopy(BRIEF)
    brief["goal"] = topic
    brief["audience"] = audience
    brief["suggestions"] = []
    brief["sources"] = [brief["sources"][0]]
    script = copy.deepcopy(SCRIPT)
    script["title"] = topic
    script["direction"] = direction
    prefix = [_tool("propose_brief", brief), _tool("submit_script", {"brief_version": 1, "script": script})]
    model_script(prefix)
    cli_json("run", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    item = view["scripts"][0]
    ref = {"id": item["output_id"], "version": item["version"]}
    campaign = _campaign()
    campaign["strategy"] = {"name": direction, "rationale": "以当前受众和目标选择表达", "alternatives": []}
    campaign["activity"].update(goal=topic, audience=audience, theme=topic)
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": campaign})])
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    view = cli_json("inspect", task["task_id"])
    assert _result(view, "submit_campaign")[0]["ok"] is True
    output = next(o for o in view["outputs"] if o["kind"] == "campaign")
    assert output["content"]["activity"]["goal"] == topic
    assert output["content"]["activity"]["audience"] == audience
    assert output["content"]["strategy"]["name"] == direction
    assert "校园" not in output["content"]["strategy"]["name"]
    assert output["content"]["script"]["direction"] == direction
    assert not view["strategy_selections"]
    assert all(m["name"] not in {"compare_strategies", "choose_strategy", "generate_script"}
               for m in view["messages"] if m["role"] == "tool")
