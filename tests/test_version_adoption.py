"""Explicit version adoption uses the chosen bodies and preserves prior inputs."""

import json
import shutil
from pathlib import Path

from conftest import cli_json, run_cli, run_cli_process
from test_campaign_draft import _create, _tool
from test_human_import import _draft_with_review_script, _exported_markdown, _import
from test_review_save import REVIEW_PASS, _draft_ready, _review_item


def test_adopted_material_bodies_are_used_after_new_process_resume(cli_env, model_script):
    model_script([
        _tool("read_material", {"material_id": "M-006"}),
        _tool("read_material", {"material_id": "M-006"}),
    ])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "1")
    before = cli_json("inspect", task_id)
    cli_json("adopt-material", task_id, "--version", "2026-10-30.v1")
    process = run_cli_process("resume", task_id, "--max-rounds", "1", data_dir=cli_env / "data")
    assert process.returncode == 0, process.stderr
    after = cli_json("inspect", task_id)
    reads = [json.loads(message["content"]) for message in after["messages"]
             if message["role"] == "tool" and message["name"] == "read_material"]
    assert reads[0]["version"] == "2026-10-29.v1"
    assert reads[-1]["version"] == "2026-10-30.v1"
    assert "已取得商用授权" in reads[-1]["material"]["text"]
    assert after["messages"][:len(before["messages"])] == before["messages"]
    adoption = next(item for item in after["material_history"] if item["to_version"] == "2026-10-30.v1")
    assert "待获取授权" in next(item for item in adoption["previous"]["materials"]
                           if item["material_id"] == "M-006")["text"]
    assert "已取得商用授权" in next(item for item in adoption["current"]["materials"]
                              if item["material_id"] == "M-006")["text"]


def test_materials_are_frozen_at_creation_before_the_first_run(cli_env, model_script, monkeypatch):
    original = json.loads((Path(__file__).resolve().parents[1] / "fixtures/materials/catalog.json").read_text("utf-8"))
    path = cli_env / "external-catalog.json"
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CTA_MATERIALS_PATH", str(path))
    task_id = _create()["task_id"]
    original["version"] = "unadopted-version"
    original["materials"][5]["text"] = "UNADOPTED BODY"
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    model_script([_tool("read_material", {"material_id": "M-006"})])
    cli_json("run", task_id, "--max-rounds", "1")
    inspected = cli_json("inspect", task_id)
    read = next(json.loads(message["content"]) for message in inspected["messages"]
                if message["role"] == "tool" and message["name"] == "read_material")
    assert read["version"] == "2026-10-29.v1"
    assert "待获取授权" in read["material"]["text"]


def test_adopted_knowledge_reaches_main_and_preserves_previous_body(cli_env, model_script, monkeypatch):
    knowledge = cli_env / "knowledge"
    shutil.copytree(Path(__file__).resolve().parents[1] / "knowledge", knowledge)
    monkeypatch.setenv("CTA_KNOWLEDGE_DIR", str(knowledge))
    model_script([_tool("estimate_path", {}), _tool("estimate_path", {})])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "1")
    before = cli_json("inspect", task_id)
    cli_json("adopt-knowledge", task_id, "--knowledge-id", "campaign-creation", "--version", "2026-10-30.v1")
    expected = (knowledge / "campaign-creation/2026-10-30.v1.md").read_text("utf-8")
    (knowledge / "campaign-creation/2026-10-30.v1.md").write_text("UNADOPTED FILE CHANGE", encoding="utf-8")
    process = run_cli_process("resume", task_id, "--max-rounds", "1", data_dir=cli_env / "data")
    assert process.returncode == 0, process.stderr
    after = cli_json("inspect", task_id)
    assert after["messages"][:len(before["messages"])] == before["messages"]
    adoption_message = next(message["content"] for message in after["messages"] if "[知识采用]" in (message["content"] or ""))
    assert expected in adoption_message
    assert "UNADOPTED FILE CHANGE" not in json.dumps(after, ensure_ascii=False)
    history = after["knowledge_history"][-1]
    assert history["current"]["content"] == expected
    assert history["previous"]["version"] == before["knowledge"][0]["version"]
    assert "活动方案创作工作知识" in history["previous"]["content"]


def test_import_validates_against_frozen_task_materials(cli_env, model_script, monkeypatch):
    task_id = _draft_with_review_script(model_script)["task_id"]
    path, text = _exported_markdown(task_id)
    changed_catalog = json.loads((Path(__file__).resolve().parents[1] / "fixtures/materials/catalog.json").read_text("utf-8"))
    changed_catalog["version"] = "unadopted-version"
    changed_catalog["materials"][0]["text"] = "UNADOPTED BODY"
    external = cli_env / "external-catalog.json"
    external.write_text(json.dumps(changed_catalog, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CTA_MATERIALS_PATH", str(external))
    path.write_text(text.replace("把招新现场剪成你的节奏", "把社团招新剪成你的节奏"), encoding="utf-8")
    code, imported = _import(task_id, path)
    assert code == 0, imported
    assert imported["import"]["output_version"] == 2
    assert cli_json("status", task_id)["versions"]["materials"] == "2026-10-29.v1"


def test_resource_update_records_reuse_without_rewriting_origin_and_appends_new_constraints(cli_env, model_script):
    task_id = _draft_ready(model_script, _review_item(REVIEW_PASS))["task_id"]
    reviewed = cli_json("review", task_id)
    before = cli_json("inspect", task_id)
    updated = cli_json("update", task_id, "--budget", "20", "--deadline", "2026-10-31T12:00:00+08:00")
    assert updated["content_reuse"]["source_input_version"] == 1
    assert updated["content_reuse"]["input_version"] == 2
    assert updated["content_reuse"]["output_id"] == before["outputs"][0]["output_id"]
    assert updated["content_reuse"]["review_id"] == reviewed["review"]["review_id"]
    assert updated["resource_reassessment"]["total_cost_cents"] == 10
    after = cli_json("inspect", task_id)
    assert after["outputs"][0]["input_version"] == 1
    assert after["messages"][:len(before["messages"])] == before["messages"]
    message = next(json.loads(item["content"].removeprefix("[输入更新] "))
                   for item in reversed(after["messages"]) if (item["content"] or "").startswith("[输入更新] "))
    assert message["budget_cents"] == 2000
    assert message["deadline"] == "2026-10-31T04:00:00+00:00"
    assert message["reuse"]["output_id"] == before["outputs"][0]["output_id"]
    again = cli_json("update", task_id, "--budget", "21")
    assert again["content_reuse"]["source_input_version"] == 1
    assert again["content_reuse"]["input_version"] == 3


def test_resource_update_save_reuses_semantic_review_and_original_output(cli_env, model_script):
    task_id = _draft_ready(model_script, _review_item(REVIEW_PASS))["task_id"]
    reviewed = cli_json("review", task_id)
    cli_json("update", task_id, "--budget", "20")
    code, out, _err = run_cli("save", task_id)
    saved = json.loads(out)
    assert code == 0, saved
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 1
    inspected = cli_json("inspect", task_id)
    assert len(inspected["outputs"]) == 1
    assert inspected["outputs"][0]["input_version"] == 1
    assert inspected["reviews"][0]["review_id"] == reviewed["review"]["review_id"]
    assert len(inspected["reviews"]) == 1


def test_resource_update_does_not_revalidate_a_semantically_stale_output(cli_env, model_script):
    task_id = _draft_ready(model_script, _review_item(REVIEW_PASS))["task_id"]
    cli_json("review", task_id)
    cli_json("update", task_id, "--audience", "研究生")
    updated = cli_json("update", task_id, "--budget", "20")
    assert updated["content_reuse"] is None
    assert updated["progress"]["phase"] == "input_updated"
    code, out, _err = run_cli("save", task_id)
    assert code == 4
    assert json.loads(out)["save_error"]["code"] == "input_changed"


def test_resource_update_checks_remaining_save_cost_after_review(cli_env, model_script):
    task_id = _draft_ready(model_script, _review_item(REVIEW_PASS))["task_id"]
    reviewed = cli_json("review", task_id)
    updated = cli_json("update", task_id, "--budget", "2.25")
    assert updated["status"] == "incomplete"
    assert updated["resource_reassessment"]["projected_total_cents"] == 230
    assert updated["resource_reassessment"]["feasible"] is False
    assert updated["resources"]["sim_spent_cents"] == reviewed["resources"]["sim_spent_cents"] == 220


def test_resource_reassessment_respects_evidence_based_rejection(cli_env, model_script):
    from test_campaign_draft import _brief_then_draft_script, _tool
    from test_revision_disposition import REVIEW_FAIL

    task = _draft_ready(model_script, _review_item(REVIEW_FAIL))
    cli_json("review", task["task_id"])
    model_script(_brief_then_draft_script() + [_tool("record_disposition", {
        "finding_id": "F1", "decision": "rejected", "reason": "M-001正文确实支持当前有限范围表述。",
    })])
    cli_json("run", task["task_id"], "--max-rounds", "1")
    updated = cli_json("update", task["task_id"], "--budget", "2.30")
    assert updated["resource_reassessment"]["projected_total_cents"] == 230
    assert updated["resource_reassessment"]["feasible"] is True
