"""Ticket 10: explicit Markdown draft import, base conflict and re-review."""

import json
from pathlib import Path

from conftest import cli_json, run_cli
from test_campaign_draft import _brief_then_draft_script, _create, _run_until_draft
from test_review_save import REVIEW_PASS, _review_item


def _draft_with_review_script(model_script):
    script = _brief_then_draft_script()
    script.append(_review_item(REVIEW_PASS))
    model_script(script)
    task = _create()
    _run_until_draft(task["task_id"])
    return task


def _exported_markdown(task_id):
    exported = cli_json("export", task_id)
    path = next(Path(item["path"]) for item in exported["files"] if item["path"].endswith("campaign-v1.md"))
    return path, path.read_text(encoding="utf-8")


def _import(task_id, path):
    code, out, _err = run_cli("import-draft", task_id, "--file", str(path))
    return code, json.loads(out)


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
    outputs = inspected["outputs"]
    assert [output["version"] for output in outputs] == [1, 2]
    assert outputs[-1]["status"] == "pending_review"
    assert inspected["imports"][0]["base_output_id"] == outputs[0]["output_id"]
    assert "把社团招新剪成你的节奏" in outputs[-1]["content_md"]

    reviewed = cli_json("review", task["task_id"])
    assert reviewed["review"]["output_version"] == 2
    saved = cli_json("save", task["task_id"])
    assert saved["status"] == "complete"
    assert saved["save"]["output_version"] == 2


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
    assert cli_json("inspect", task["task_id"])["outputs"][-1]["version"] == 2


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
    assert cli_json("inspect", task["task_id"])["outputs"][-1]["version"] == 1


def test_missing_metadata_is_rejected(cli_env, model_script):
    task = _draft_with_review_script(model_script)
    path, text = _exported_markdown(task["task_id"])
    path.write_text(text.split("<!-- cta:")[0], encoding="utf-8")
    code, document = _import(task["task_id"], path)
    assert code == 4
    assert document["error"] == "missing_metadata"