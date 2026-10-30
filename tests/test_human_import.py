"""Ticket 10: explicit Markdown draft import, base conflict and re-review."""

import copy
import json
from pathlib import Path

from conftest import cli_json, run_cli
from test_brief_loop import BRIEF
from test_campaign_delivery_new import _campaign, _new_draft_ready, _result
from test_campaign_draft import _create, _tool
from test_direct_script import SCRIPT
from test_review_save import REVIEW_PASS, _review_item


def _draft_with_review_script(model_script):
    return _new_draft_ready(model_script, [_review_item(REVIEW_PASS), _review_item(REVIEW_PASS)])


def _exported_markdown(task_id):
    exported = cli_json("export", task_id)
    path = next(Path(item["path"]) for item in exported["files"] if item["path"].endswith("campaign-v1.md"))
    return path, path.read_text(encoding="utf-8")


def _import(task_id, path):
    code, out, _err = run_cli("import-draft", task_id, "--file", str(path))
    return code, json.loads(out)


def test_evidence_identifiers_survive_unedited_export_and_import(cli_env, model_script):
    task = _create()
    script = copy.deepcopy(SCRIPT)
    identifiers = ["x] y", "无", " edge ", "x,y", "&#93;", "x\x0by", "x\u0085y"]
    script["evidence"] = [{**script["evidence"][0], "id": identifier} for identifier in identifiers]
    script["segments"][0]["evidence_ids"] = identifiers
    script["segments"][0]["duration_seconds"] = 10
    script["segments"].append({**script["segments"][0], "segment_id": "s2", "duration_seconds": 5,
                               "evidence_ids": ["无"]})
    prefix = [_tool("propose_brief", BRIEF), _tool("submit_script", {"brief_version": 1, "script": script})]
    model_script(prefix)
    cli_json("run", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    ref = _result(view, "submit_script")[-1]["script_ref"]
    campaign = _campaign()
    campaign["evidence"][0]["id"] = "x] y"
    campaign["evidence"][0]["claim"] += "；演示] 说明"
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": campaign})])
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    path, _ = _exported_markdown(task["task_id"])
    code, imported = _import(task["task_id"], path)
    assert code == 0, imported
    view = cli_json("inspect", task["task_id"])
    outputs = [output for output in view["outputs"] if output["kind"] == "campaign"]
    assert outputs[-1]["content"]["script"] == script
    assert outputs[-1]["content"]["evidence"] == campaign["evidence"]
    assert outputs[-1]["content"]["script_ref"] == ref


def test_legacy_ambiguous_evidence_id_never_silently_changes(cli_env, model_script):
    task = _create()
    prefix = [_tool("propose_brief", BRIEF), _tool("submit_script", {"brief_version": 1, "script": SCRIPT})]
    model_script(prefix)
    cli_json("run", task["task_id"], "--max-rounds", "2")
    ref = _result(cli_json("inspect", task["task_id"]), "submit_script")[-1]["script_ref"]
    campaign = _campaign()
    campaign["evidence"][0]["id"] = "x] y"
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": campaign})])
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.replace("x&#93;&#32;y", "x] y"), encoding="utf-8")
    code, imported = _import(task["task_id"], path)
    view = cli_json("inspect", task["task_id"])
    outputs = [output for output in view["outputs"] if output["kind"] == "campaign"]
    assert code != 0 and imported["error"] == "invalid_campaign"
    assert len(outputs) == 1


def test_legacy_no_evidence_label_never_drops_a_real_reference(cli_env, model_script):
    task = _create()
    script = copy.deepcopy(SCRIPT)
    script["evidence"][0]["id"] = "无"
    script["segments"][0]["evidence_ids"] = ["无"]
    prefix = [_tool("propose_brief", BRIEF), _tool("submit_script", {"brief_version": 1, "script": script})]
    model_script(prefix)
    cli_json("run", task["task_id"], "--max-rounds", "2")
    ref = _result(cli_json("inspect", task["task_id"]), "submit_script")[-1]["script_ref"]
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": _campaign()})])
    cli_json("resume", task["task_id"], "--max-rounds", "1")
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.replace("- 证据：&#26080;", "- 证据：无"), encoding="utf-8")
    code, imported = _import(task["task_id"], path)
    outputs = [o for o in cli_json("inspect", task["task_id"])["outputs"] if o["kind"] == "campaign"]
    assert code != 0 and imported["error"] == "invalid_campaign" and len(outputs) == 1


def test_unescaped_edited_evidence_id_is_not_silently_truncated(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.replace("- [E1] ", "- [x] y] "), encoding="utf-8")
    code, result = _import(task["task_id"], path)
    view = cli_json("inspect", task["task_id"])
    outputs = [item for item in view["outputs"] if item["kind"] == "campaign"]
    assert code != 0 and result["error"] == "invalid_campaign" and len(outputs) == 1
    path.write_text(text.replace("- [E1] ", "- [x&#93; y] "), encoding="utf-8")
    code, result = _import(task["task_id"], path)
    assert code == 0, result
    outputs = [item for item in cli_json("inspect", task["task_id"])["outputs"] if item["kind"] == "campaign"]
    assert outputs[-1]["content"]["evidence"][0]["id"] == "x] y"


def test_edited_markdown_forms_pending_review_version_and_can_complete(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    edited = text.replace("把招新现场剪成你的节奏", "把社团招新剪成你的节奏")
    path.write_text(edited, encoding="utf-8")

    code, imported = _import(task["task_id"], path)
    assert code == 0
    assert imported["import"]["output_version"] == 2
    assert imported["progress"]["phase"] == "draft_ready"
    assert imported["progress"]["current_output"]["version"] == 2
    inspected = cli_json("inspect", task["task_id"])
    outputs = [output for output in inspected["outputs"] if output["kind"] == "campaign"]
    assert [output["version"] for output in outputs] == [1, 2]
    assert outputs[-1]["status"] == "pending_review"
    assert inspected["imports"][0]["base_output_id"] == outputs[0]["output_id"]
    assert outputs[-1]["content"]["activity"]["theme"] == "把社团招新剪成你的节奏"

    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["output_version"] == 2
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 2


def test_crlf_operator_input_is_preserved_and_export_has_stable_line_endings(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    edited = text.replace("把招新现场剪成你的节奏", "人工稿换行核对").replace("\r\n", "\n")
    source = edited.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(source)
    code, _ = _import(task["task_id"], path)
    assert code == 0
    assert path.read_bytes() == source
    cli_json("review", task["task_id"])
    saved = cli_json("save", task["task_id"])
    exported = next(Path(item["path"]) for item in saved["save"]["files"] if item["path"].endswith("campaign-v2.md"))
    assert b"\r" not in exported.read_bytes()
    first = cli_json("reconcile", task["task_id"])["save"]
    assert cli_json("reconcile", task["task_id"])["save"] == first


def _verified_task(model_script):
    task = _draft_with_review_script(model_script)
    cli_json("review", task["task_id"])
    cli_json("save", task["task_id"])
    return task


def test_latest_resource_reused_export_can_be_imported_with_source_provenance(cli_env, model_script):
    task = _verified_task(model_script)
    cli_json("update", task["task_id"], "--budget", "20")
    _path, text = _exported_markdown(task["task_id"])
    edited = cli_env / "resource-edit.md"
    edited.write_text(text.replace("把招新现场剪成你的节奏", "新资源下的人工稿"), encoding="utf-8")
    code, imported = _import(task["task_id"], edited)
    assert code == 0, imported
    assert imported["input"]["version"] == 2
    facts = cli_json("inspect", task["task_id"])
    campaigns = [o for o in facts["outputs"] if o["kind"] == "campaign"]
    assert campaigns[0]["input_version"] == 1
    assert campaigns[-1]["input_version"] == 2


def test_pending_human_output_exports_current_version_instead_of_old_saved_files(cli_env, model_script):
    task = _verified_task(model_script)
    _path, text = _exported_markdown(task["task_id"])
    edited = cli_env / "pending-edit.md"
    edited.write_text(text.replace("把招新现场剪成你的节奏", "尚待核验的当前人工稿"), encoding="utf-8")
    assert _import(task["task_id"], edited)[0] == 0
    exported = cli_json("export", task["task_id"])
    assert exported["output_version"] == 2
    assert exported["save_id"] is None
    assert exported["delivery_complete"] is False
    current = next(Path(item["path"]) for item in exported["files"] if item["path"].endswith("campaign-v2.md"))
    body = current.read_text(encoding="utf-8")
    assert "尚待核验的当前人工稿" in body
    assert "尚未完成内容核验" in body


def test_resource_update_of_pending_human_draft_reserves_only_review_and_save(cli_env, model_script):
    task = _verified_task(model_script)
    _path, text = _exported_markdown(task["task_id"])
    edited = cli_env / "review-pending.md"
    edited.write_text(text.replace("把招新现场剪成你的节奏", "人工稿待核验资源估算"), encoding="utf-8")
    assert _import(task["task_id"], edited)[0] == 0
    updated = cli_json("update", task["task_id"], "--budget", "3.20")
    assert updated["resource_reassessment"]["projected_total_cents"] == 320
    assert updated["resource_reassessment"]["feasible"] is True


def test_semantic_update_export_does_not_relabel_old_content_as_current_input(cli_env, model_script):
    task = _verified_task(model_script)
    cli_json("update", task["task_id"], "--audience", "研究生")
    _path, text = _exported_markdown(task["task_id"])
    edited = cli_env / "old-input.md"
    edited.write_text(text.replace("把招新现场剪成你的节奏", "旧条件仍需复核"), encoding="utf-8")
    code, rejected = _import(task["task_id"], edited)
    assert code == 4
    assert rejected["error"] == "stale_input"
    after = cli_json("inspect", task["task_id"])
    assert after["progress"]["phase"] == "input_updated"
    assert len([o for o in after["outputs"] if o["kind"] == "campaign"]) == 1


def test_missing_required_section_is_rejected_and_keeps_base(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    lines = text.splitlines()
    start = lines.index("## 素材用途")
    end = next(index for index in range(start + 1, len(lines)) if lines[index].startswith("## "))
    path.write_text("\n".join(lines[:start] + lines[end:]) + "\n", encoding="utf-8")
    before = cli_json("inspect", task["task_id"])

    code, document = _import(task["task_id"], path)
    assert code == 4
    assert document["error"] == "missing_section"
    after = cli_json("inspect", task["task_id"])
    assert len(after["outputs"]) == len(before["outputs"])
    assert path.is_file()


def test_stale_base_import_is_rejected_without_silent_overwrite(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.replace("把招新现场剪成你的节奏", "第一次人工修改"), encoding="utf-8")
    code, first = _import(task["task_id"], path)
    assert code == 0
    assert first["import"]["output_version"] == 2
    # Re-import the original v1 file after v2 exists.
    code, document = _import(task["task_id"], path)
    # The file still carries v1 metadata.
    assert code == 4
    assert document["error"] == "stale_base"
    assert [o for o in cli_json("inspect", task["task_id"])["outputs"] if o["kind"] == "campaign"][-1]["version"] == 2


def test_unimported_external_edit_does_not_change_authority(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.replace("把招新现场剪成你的节奏", "未导入的修改"), encoding="utf-8")
    before = cli_json("status", task["task_id"])
    after = cli_json("status", task["task_id"])
    assert before["progress"]["current_output"]["version"] == after["progress"]["current_output"]["version"] == 1
    assert "未导入的修改" not in json.dumps(after, ensure_ascii=False)


def test_changing_confirmed_audience_in_body_requires_task_update(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    edited = text.replace("- 受众：18-24 岁大学生，重点是社团招新负责人", "- 受众：高中生")
    path.write_text(edited, encoding="utf-8")
    code, document = _import(task["task_id"], path)
    assert code == 4
    assert document["error"] == "semantic_change_requires_update"
    assert [o for o in cli_json("inspect", task["task_id"])["outputs"] if o["kind"] == "campaign"][-1]["version"] == 1


def test_missing_metadata_is_rejected(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.split("<!-- cta:")[0], encoding="utf-8")
    code, document = _import(task["task_id"], path)
    assert code == 4
    assert document["error"] == "missing_metadata"
