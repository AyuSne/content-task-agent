"""Recover local action receipts through real new CLI processes, without replay."""

import copy
import json
import os
import subprocess
import sys
from datetime import datetime

import pytest
from conftest import cli_json, run_cli_process
from test_campaign_draft import _brief_then_draft_script, _create, _run_until_draft, _tool
from test_process_resume import _pending_call, _tool_result
from test_review_save import REVIEW_FAIL, _review_item

FIXED_UTC = "2026-10-29T01:02:03+00:00"


def _fixed_clock(monkeypatch):
    from cta import config

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(FIXED_UTC).astimezone(tz)

    monkeypatch.setattr(config, "datetime", Clock)


def _frozen_cli_process(*args, crash=None):
    """Pin only the external wall clock; run the real CLI and on-disk store."""
    script = f"""
from datetime import datetime
import cta.config
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat({FIXED_UTC!r}).astimezone(tz)
cta.config.datetime = Clock
from cta.cli import main
raise SystemExit(main())
"""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if crash is not None:
        env["CTA_FAULT_CRASH"] = crash
    return subprocess.run(
        [sys.executable, "-c", script, *args], env=env, capture_output=True,
        text=True, encoding="utf-8", check=False,
    )


@pytest.mark.parametrize("point", ["after_tool_request", "after_tool_effect"])
@pytest.mark.parametrize("tool", ["compare_strategies", "estimate_cost"])
def test_same_second_estimate_recovery_uses_only_this_request_result(cli_env, model_script, monkeypatch, point, tool):
    _fixed_clock(monkeypatch)
    arguments = {"search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 0}
    if tool == "estimate_cost":
        arguments = {"actions": ["generate_direct", "review", "save"]}
    model_script([
        _tool(tool, arguments),
        _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
        _tool(tool, arguments),
    ])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "2")
    initial = cli_json("inspect", task_id)
    first = next(json.loads(message["content"]) for message in initial["messages"]
                 if message["role"] == "tool" and message["name"] == tool)
    first_estimate = first["estimate"] if tool == "estimate_cost" else first
    assert first_estimate["incurred_cents"] == 0
    assert initial["resources"]["sim_spent_cents"] == 20

    crashed = _frozen_cli_process("run", task_id, "--max-rounds", "1", crash=point)
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json("inspect", task_id)
    pending = _pending_call(before)
    requested = next(message for message in before["messages"]
                     if message["role"] == "assistant" and any(call["id"] == pending["id"] for call in message["tool_calls"]))
    assert initial["estimates"][-1]["created_at"] == requested["created_at"]

    resumed = _frozen_cli_process("resume", task_id, "--max-rounds", "0")
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json("inspect", task_id)
    receipt = json.loads(_tool_result(after, pending["id"])["content"])
    if point == "after_tool_request":
        assert receipt["ok"] is False
        assert receipt["error"] == "local_action_interrupted"
    else:
        assert receipt["ok"] is True
        assert receipt["estimate_id"] != first["estimate_id"]
        assert receipt["estimate_id"] == before["estimates"][-1]["estimate_id"]
        estimate = receipt["estimate"] if tool == "estimate_cost" else receipt
        assert estimate["incurred_cents"] == 20
        assert estimate["remaining_budget_cents"] == 980
        assert set(receipt) == set(first) | {"recovered"}
        if tool == "compare_strategies":
            assert receipt["mode_table"] == first["mode_table"]
    for field in ("estimates", "ledger", "attempts"):
        assert after[field] == before[field]
    assert after["progress"]["used_rounds"] == before["progress"]["used_rounds"] == 3


@pytest.mark.parametrize("tool", ["record_disposition", "record_dispositions", "choose_strategy"])
def test_same_second_prior_action_cannot_complete_an_unexecuted_request(cli_env, model_script, monkeypatch, tool):
    _fixed_clock(monkeypatch)
    task_id, script = _prepare(tool, model_script)
    cli_json("run", task_id, "--max-rounds", "1")
    old = cli_json("inspect", task_id)
    calls = json.loads(script.read_text(encoding="utf-8"))
    model_script([*calls, calls[-1]])
    crashed = _frozen_cli_process("run", task_id, "--max-rounds", "1", crash="after_tool_request")
    assert crashed.returncode == 70, crashed.stderr
    pending = _pending_call(cli_json("inspect", task_id))
    resumed = _frozen_cli_process("resume", task_id, "--max-rounds", "0")
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json("inspect", task_id)
    receipt = json.loads(_tool_result(after, pending["id"])["content"])
    assert receipt["ok"] is False
    assert receipt["error"] == "local_action_interrupted"
    for field in ("dispositions", "estimates", "strategy_selections", "ledger", "attempts"):
        assert after[field] == old[field]


@pytest.mark.parametrize("tool", ["record_disposition", "record_dispositions", "compare_strategies", "choose_strategy", "estimate_cost"])
def test_process_death_between_local_writes_and_receipt_rolls_back_both(cli_env, model_script, tool):
    task_id, script = _prepare(tool, model_script)
    before = cli_json("inspect", task_id)
    crashed = run_cli_process(
        "run", task_id, "--max-rounds", "1", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": "before_local_tool_receipt"},
    )
    assert crashed.returncode == 70, crashed.stderr
    pending = _pending_call(cli_json("inspect", task_id))
    resumed = run_cli_process(
        "resume", task_id, "--max-rounds", "0", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json("inspect", task_id)
    receipt = json.loads(_tool_result(after, pending["id"])["content"])
    assert receipt["error"] == "local_action_interrupted"
    for field in ("dispositions", "estimates", "strategy_selections", "ledger", "attempts"):
        assert after[field] == before[field]


def test_executed_strategy_rejection_is_recovered_as_its_original_result(cli_env, model_script):
    task_id = _create(budget="0.50")["task_id"]
    script = model_script([_tool("choose_strategy", {
        "direction": "functional_demo", "rationale": "尝试功能演示完整路径。",
        "search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 0,
    })])
    crashed = run_cli_process(
        "run", task_id, "--max-rounds", "1", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": "after_tool_effect"},
    )
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json("inspect", task_id)
    pending = _pending_call(before)
    resumed = run_cli_process(
        "resume", task_id, "--max-rounds", "0", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["run"]["recovery"]["recovered_calls"][0]["tool"] == "choose_strategy"
    after = cli_json("inspect", task_id)
    receipt = json.loads(_tool_result(after, pending["id"])["content"])
    assert receipt["ok"] is False
    assert receipt["error"] == "strategy_infeasible"
    assert receipt["estimate_id"] == before["estimates"][-1]["estimate_id"]
    assert receipt["selection_recorded"] is False
    assert receipt["budget_feasible"] is False
    assert receipt["alternatives"] == []  # 50 cents cannot cover any complete path.
    for field in ("estimates", "strategy_selections", "ledger", "attempts"):
        assert after[field] == before[field]
    assert after["resources"]["sim_spent_cents"] == 0


def _prepare(tool, model_script):
    if tool in ("record_disposition", "record_dispositions"):
        report = copy.deepcopy(REVIEW_FAIL)
        report["findings"].append({**report["findings"][0], "finding_id": "F2", "severity": "optional"})
        decisions = [
            {"finding_id": "F1", "decision": "accepted", "reason": "按M-001收窄表述。"},
            {"finding_id": "F2", "decision": "rejected", "reason": "可选风格建议，原稿已满足要求。"},
        ]
        arguments = decisions[0] if tool == "record_disposition" else {"decisions": decisions}
        script = model_script([*_brief_then_draft_script(), _review_item(report), _tool(tool, arguments)])
        task_id = _create()["task_id"]
        _run_until_draft(task_id)
        cli_json("review", task_id)
    else:
        arguments = {"search_mode": "search_basic", "generation_mode": "generate_direct", "search_queries": 0}
        if tool == "estimate_cost":
            arguments = {"actions": ["generate_direct", "review", "save"]}
        if tool == "choose_strategy":
            arguments.update(direction="functional_demo", rationale="资料足够，采用功能演示。")
        script = model_script([_tool(tool, arguments)])
        task_id = _create()["task_id"]
    return task_id, script


@pytest.mark.parametrize("tool", ["record_disposition", "record_dispositions", "compare_strategies", "choose_strategy", "estimate_cost"])
@pytest.mark.parametrize("point", ["after_tool_effect", "after_tool_request"])
def test_local_action_receipt_is_verified_without_replaying_or_charging(cli_env, model_script, tool, point):
    task_id, script = _prepare(tool, model_script)
    crashed = run_cli_process(
        "run", task_id, "--max-rounds", "1", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": point},
    )
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json("inspect", task_id)
    pending = _pending_call(before)
    assert pending["function"]["name"] == tool

    resumed = run_cli_process(
        "resume", task_id, "--max-rounds", "0", data_dir=cli_env / "data",
        extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert resumed.returncode == 0, resumed.stderr
    document = json.loads(resumed.stdout)
    after = cli_json("inspect", task_id)
    receipt = json.loads(_tool_result(after, pending["id"])["content"])
    assert receipt["recovered"] is True
    if point == "after_tool_effect":
        assert receipt["ok"] is True
        assert [item["tool"] for item in document["run"]["recovery"]["recovered_calls"]] == [tool]
        if tool.startswith("record_disposition"):
            assert receipt["review_id"] == before["reviews"][-1]["review_id"]
            assert len(receipt["dispositions"]) == (1 if tool == "record_disposition" else 2)
        elif tool in ("compare_strategies", "estimate_cost"):
            assert receipt["estimate_id"] == before["estimates"][-1]["estimate_id"]
        else:
            assert receipt["selection_id"] == before["strategy_selections"][-1]["selection_id"]
    else:
        assert receipt["ok"] is False
        assert receipt["error"] == "local_action_interrupted"
        assert [item["tool"] for item in document["run"]["recovery"]["interrupted_calls"]] == [tool]
    for field in ("dispositions", "estimates", "strategy_selections", "ledger", "attempts"):
        assert after[field] == before[field]
    assert after["resources"]["sim_spent_cents"] == before["resources"]["sim_spent_cents"]
    assert after["progress"]["used_rounds"] == before["progress"]["used_rounds"]
    calls = [call["id"] for message in after["messages"] if message["role"] == "assistant" for call in message["tool_calls"]]
    results = [message["tool_call_id"] for message in after["messages"] if message["role"] == "tool"]
    assert calls == results
