"""Ticket 09: explicit input updates, versioned validity and re-validation."""

import json

from conftest import cli_json, run_cli
from test_campaign_draft import _brief_tool, _create
from test_review_save import REVIEW_PASS, _draft_ready, _review_item


def _update(task_id, *args):
    code, out, _err = run_cli("update", task_id, *args)
    return code, json.loads(out)


def test_resource_update_creates_new_input_version_and_preserves_work(cli_env):
    task = _create()
    code, document = _update(task["task_id"], "--budget", "20")
    assert code == 0
    assert document["change"]["kind"] == "resource"
    assert document["input"]["version"] == 2
    assert document["input"]["budget_cents"] == 2000
    assert document["progress"]["used_rounds"] == 0
    assert document["status"] == "in_progress"
    inspected = cli_json("inspect", task["task_id"])
    assert [item["version"] for item in inspected["input_versions"]] == [1, 2]
    assert inspected["input_versions"][0]["budget_cents"] == 1000


def test_invalid_update_is_rejected_and_old_version_remains(cli_env):
    task = _create()
    code, document = _update(task["task_id"], "--budget", "0")
    assert code == 4
    assert document["update_error"]["code"] == "invalid_input"
    status = cli_json("status", task["task_id"])
    assert status["input"]["version"] == 1
    assert status["input"]["budget_cents"] == 1000


def test_semantic_update_marks_old_content_and_review_stale(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    code, updated = _update(task["task_id"], "--audience", "研究生")
    assert code == 0
    assert updated["change"]["kind"] == "semantic"
    assert updated["progress"]["phase"] == "input_updated"
    assert updated["input"]["version"] == 2
    code, document, _err = run_cli("save", task["task_id"])
    assert code == 4
    assert json.loads(document)["save_error"]["code"] == "input_changed"


def test_semantic_update_can_rebuild_brief_v2(cli_env, model_script):
    task = _create()
    code, _updated = _update(task["task_id"], "--topic", "研究生社团招新")
    assert code == 0
    model_script([_brief_tool()])
    document = cli_json("run", task["task_id"])
    assert document["progress"]["phase"] == "brief_ready"
    assert document["brief"]["version"] == 1
    assert document["input"]["version"] == 2
    inspected = cli_json("inspect", task["task_id"])
    assert len(inspected["input_versions"]) == 2


def test_knowledge_adoption_is_explicit_and_marks_results_stale(cli_env):
    task = _create()
    code, document, _err = run_cli(
        "adopt-knowledge", task["task_id"], "--knowledge-id", "campaign-creation", "--version", "2026-10-30.v1"
    )
    assert code == 0
    body = json.loads(document)
    assert body["progress"]["phase"] == "input_updated"
    inspected = cli_json("inspect", task["task_id"])
    knowledge = next(item for item in inspected["knowledge"] if item["knowledge_id"] == "campaign-creation")
    assert knowledge["version"] == "2026-10-30.v1"
    assert any("知识采用" in (message["content"] or "") for message in inspected["messages"])


def test_material_version_adoption_marks_facts_and_reviews_stale(cli_env):
    task = _create()
    code, document, _err = run_cli("adopt-material", task["task_id"], "--version", "2026-10-30.v1")
    assert code == 0
    body = json.loads(document)
    assert body["progress"]["phase"] == "input_updated"
    assert body["versions"]["materials"] == "2026-10-30.v1"


def test_resource_update_after_completion_does_not_reset_consumption(cli_env, model_script):
    task = _draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json("review", task["task_id"])
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    spent = saved["resources"]["sim_spent_cents"]
    code, updated = _update(task["task_id"], "--budget", "30")
    assert code == 0
    assert updated["status"] == "complete"
    assert updated["input"]["version"] == 2
    assert updated["resources"]["sim_spent_cents"] == spent
    assert updated["progress"]["used_rounds"] == saved["progress"]["used_rounds"]
