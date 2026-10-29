"""Task plans are model-authored, durable, and do not grant delivery eligibility."""
import json

from conftest import VALID, cli_json, run_cli_process


def test_plan_roundtrip_and_invalid_references_across_processes(cli_env, model_script):
    task = cli_json("create", "--topic", VALID["topic"], "--budget", VALID["budget"], "--deadline", VALID["deadline"])
    task_id = task["task_id"]
    initial = [{"id": "research", "title": "核对字幕材料", "status": "current", "depends_on": [], "references": []},
               {"id": "write", "title": "编写字幕方案", "status": "pending", "depends_on": ["research"], "references": []}]
    revised = [{"id": "research", "title": "核对字幕材料", "status": "done", "depends_on": [],
                "references": [{"kind": "tool_result", "id": "missing"}]}, initial[1]]
    script = model_script([
        {"type": "tool", "name": "update_plan", "arguments": {"steps": initial}},
        {"type": "tool", "name": "update_plan", "arguments": {"steps": revised}},
    ])
    data_dir = cli_env / "data"
    def run_once():
        result = run_cli_process("run", task_id, "--max-rounds", "1", data_dir=data_dir,
                                 extra_env={"CTA_MODEL_SCRIPT": str(script)})
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    first = run_once()
    assert first["plan"]["steps"] == initial
    assert first["plan"]["version"] == 1
    second = run_once()
    assert second["plan"]["steps"] == initial
    assert second["plan"]["version"] == 1
    assert second["progress"]["used_rounds"] == 2
    assert second["resources"]["sim_spent_cents"] == 0
    assert second["status"] != "complete"
    inspected = cli_json("inspect", task_id)
    results = [json.loads(m["content"]) for m in inspected["messages"] if m["name"] == "update_plan"]
    assert results[0]["ok"] is True
    assert results[1]["error"] == "invalid_plan"
    assert "missing" in str(results[1])


def test_plan_updates_link_real_feedback_and_report_it(cli_env, model_script):
    task = cli_json("create", "--topic", VALID["topic"], "--budget", VALID["budget"], "--deadline", VALID["deadline"])
    task_id = task["task_id"]
    first_plan = [{"id": "lookup", "title": "查找资料", "status": "current", "depends_on": [], "references": []}]
    next_plan = [{"id": "lookup", "title": "查找资料", "status": "done", "depends_on": [],
                  "references": [{"kind": "tool_result", "id": "search-1"}]}]
    model_script([
        {"type": "tool", "name": "update_plan", "arguments": {"steps": first_plan}},
        {"type": "tool", "name": "search_materials", "id": "search-1", "arguments": {"query": "字幕"}},
        {"type": "tool", "name": "update_plan", "arguments": {"steps": next_plan}},
    ])
    doc = cli_json("run", task_id, "--max-rounds", "3")
    assert doc["plan"]["version"] == 2
    assert doc["plan"]["steps"] == next_plan
    assert doc["progress"]["used_rounds"] == 3
    assert doc["status"] != "complete"
    inspected = cli_json("inspect", task_id)
    assert [plan["version"] for plan in inspected["plan_history"]] == [1, 2]
    exported = cli_json("export", task_id)
    report = next(item for item in exported["files"] if "report" in item["path"])
    from pathlib import Path
    text = Path(report["path"]).read_text(encoding="utf-8")
    assert "查找资料" in text
    assert "tool_result:search-1" in text


def test_plan_recovery_keeps_committed_result_without_replay(cli_env, model_script):
    task = cli_json("create", "--topic", VALID["topic"], "--budget", VALID["budget"], "--deadline", VALID["deadline"])
    task_id = task["task_id"]
    steps = [{"id": "write", "title": "编写", "status": "current", "depends_on": [], "references": []}]
    script = model_script([{"type": "tool", "name": "update_plan", "arguments": {"steps": steps}}])
    data_dir = cli_env / "data"
    crashed = run_cli_process("run", task_id, "--max-rounds", "1", data_dir=data_dir,
                              extra_env={"CTA_MODEL_SCRIPT": str(script), "CTA_FAULT_CRASH": "after_tool_effect"})
    assert crashed.returncode == 70
    resumed = run_cli_process("resume", task_id, "--max-rounds", "0", data_dir=data_dir,
                              extra_env={"CTA_MODEL_SCRIPT": str(script)})
    assert resumed.returncode == 0, resumed.stderr
    doc = json.loads(resumed.stdout)
    assert doc["plan"]["version"] == 1
    assert doc["plan"]["steps"] == steps
    assert doc["progress"]["used_rounds"] == 1
    assert doc["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task_id)
    receipt = next(json.loads(m["content"]) for m in inspected["messages"] if m["name"] == "update_plan")
    assert receipt["ok"] is True and receipt["recovered"] is True
    assert len(inspected["plan_history"]) == 1


def test_plan_call_in_multi_action_round_is_rejected(cli_env, model_script):
    task = cli_json("create", "--topic", VALID["topic"], "--budget", VALID["budget"], "--deadline", VALID["deadline"])
    steps = [{"id": "write", "title": "编写", "status": "done", "depends_on": [], "references": []}]
    model_script([{"type": "multi", "calls": [
        {"name": "update_plan", "arguments": {"steps": steps}},
        {"name": "read_material", "arguments": {"material_id": "M-001"}},
    ]}])
    doc = cli_json("run", task["task_id"], "--max-rounds", "1")
    assert doc["plan"]["version"] == 0
    assert doc["status"] != "complete"
    assert doc["progress"]["used_rounds"] == 1
    inspected = cli_json("inspect", task["task_id"])
    receipts = [json.loads(m["content"]) for m in inspected["messages"] if m["role"] == "tool"]
    assert [item["error"] for item in receipts] == ["multiple_calls", "multiple_calls"]


def test_invalid_plan_dependency_is_rejected_without_replacing_prior_version(cli_env, model_script):
    task = cli_json("create", "--topic", VALID["topic"], "--budget", VALID["budget"], "--deadline", VALID["deadline"])
    steps = [
        {"id": "a", "title": "核对资料", "status": "current", "depends_on": [], "references": []},
        {"id": "b", "title": "创作", "status": "pending", "depends_on": ["a"], "references": []},
    ]
    cyclic = [dict(steps[0], depends_on=["b"]), steps[1]]
    model_script([
        {"type": "tool", "name": "update_plan", "arguments": {"steps": steps}},
        {"type": "tool", "name": "update_plan", "arguments": {"steps": cyclic}},
        {"type": "tool", "name": "update_plan", "arguments": {"steps": steps}},
    ])
    doc = cli_json("run", task["task_id"], "--max-rounds", "3")
    assert doc["plan"]["version"] == 1
    assert doc["plan"]["steps"] == steps
    inspected = cli_json("inspect", task["task_id"])
    receipts = [json.loads(m["content"]) for m in inspected["messages"] if m["name"] == "update_plan"]
    assert receipts[1]["error"] == "invalid_plan"
    assert "循环" in receipts[1]["message"]
    assert receipts[2]["changed"] is False
    assert len(inspected["plan_history"]) == 1
