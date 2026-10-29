"""Standard product skills exposed through the task CLI and model seam."""
import json

from conftest import cli_json
from test_campaign_draft import _create, _tool


def test_preloaded_skill_is_discoverable_and_readable_by_model(cli_env, model_script):
    model_script([_tool("read_skill", {"name": "campaign-creation"})])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "1")
    view = cli_json("inspect", task_id)
    directory = next(m["content"] for m in view["messages"] if m["content"] and "[Skill目录]" in m["content"])
    assert "campaign-creation" in directory and "content-review" in directory
    assert "已预装" in directory
    result = next(json.loads(m["content"]) for m in view["messages"] if m["name"] == "read_skill")
    assert result["ok"] is True
    assert result["resource"] == "SKILL.md"
    assert result["content"].startswith("---\nname: campaign-creation\n")
    assert "sha256" in result and "version" in result
    assert "/.agents/skills/campaign-creation/SKILL.md" in result["source"].replace("\\", "/")


def test_reference_reads_are_confined_and_reuse_task_version_across_processes(cli_env, model_script, monkeypatch):
    import shutil
    from pathlib import Path

    from conftest import run_cli_process

    knowledge = cli_env / "knowledge"
    shutil.copytree(Path(__file__).resolve().parents[1] / "knowledge", knowledge)
    shutil.copytree(Path(__file__).resolve().parents[1] / ".agents/skills", cli_env / ".agents/skills")
    monkeypatch.setenv("CTA_KNOWLEDGE_DIR", str(knowledge))
    resource = "references/evidence-examples.md"
    original = (cli_env / ".agents/skills/campaign-creation" / resource).read_text(encoding="utf-8")
    model_script([
        _tool("read_skill", {"name": "campaign-creation", "resource": resource}),
        _tool("read_skill", {"name": "campaign-creation", "resource": "../../manifest.json"}),
        _tool("read_skill", {"name": "campaign-creation", "resource": resource}),
    ])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "2")
    (cli_env / ".agents/skills/campaign-creation" / resource).write_text("CHANGED WITHOUT ADOPTION", encoding="utf-8")
    process = run_cli_process("resume", task_id, "--max-rounds", "1", data_dir=cli_env / "data")
    assert process.returncode == 0, process.stderr
    view = cli_json("inspect", task_id)
    results = [json.loads(m["content"]) for m in view["messages"] if m["name"] == "read_skill"]
    assert results[0]["content"] == results[2]["content"] == original
    assert results[1]["ok"] is False and results[1]["error"] == "unknown_resource"
    assert results[0]["version"] == results[2]["version"]
    assert results[0]["sha256"] == results[2]["sha256"]
    assert view["resources"]["sim_spent_cents"] == 0


def test_explicit_adoption_records_and_uses_new_reference_snapshot(cli_env, model_script, monkeypatch):
    import shutil
    from pathlib import Path

    knowledge = cli_env / "knowledge"
    shutil.copytree(Path(__file__).resolve().parents[1] / "knowledge", knowledge)
    shutil.copytree(Path(__file__).resolve().parents[1] / ".agents/skills", cli_env / ".agents/skills")
    monkeypatch.setenv("CTA_KNOWLEDGE_DIR", str(knowledge))
    resource = "references/evidence-examples.md"
    path = cli_env / ".agents/skills/campaign-creation" / resource
    original = path.read_text(encoding="utf-8")
    model_script([_tool("read_skill", {"name": "campaign-creation", "resource": resource}),
                  _tool("read_skill", {"name": "campaign-creation", "resource": resource})])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "1")
    manifest_path = knowledge / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["docs"][0]["version"] = "updated-v1"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    path.write_text("updated reference", encoding="utf-8")
    cli_json("adopt-knowledge", task_id, "--knowledge-id", "campaign-creation", "--version", "updated-v1")
    path.write_text("unadopted edit", encoding="utf-8")
    cli_json("resume", task_id, "--max-rounds", "1")
    view = cli_json("inspect", task_id)
    results = [json.loads(m["content"]) for m in view["messages"] if m["name"] == "read_skill"]
    assert results[0]["content"] == original
    assert results[1]["content"] == "updated reference"
    assert results[1]["version"] == "updated-v1"
    history = view["knowledge_history"][-1]
    assert history["previous_bundle"]["references"][resource] == original
    assert history["current_bundle"]["references"][resource] == "updated reference"


def test_reviewer_preloads_task_adopted_body_even_if_source_changes(cli_env, model_script, monkeypatch):
    import shutil
    from pathlib import Path

    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item

    knowledge = cli_env / "knowledge"
    shutil.copytree(Path(__file__).resolve().parents[1] / "knowledge", knowledge)
    shutil.copytree(Path(__file__).resolve().parents[1] / ".agents/skills", cli_env / ".agents/skills")
    monkeypatch.setenv("CTA_KNOWLEDGE_DIR", str(knowledge))
    reviewer_path = cli_env / ".agents/skills/content-review/SKILL.md"
    original = reviewer_path.read_text(encoding="utf-8")
    task_id = _new_draft_ready(model_script, _review_item(REVIEW_PASS))["task_id"]
    reviewer_path.write_text("UNADOPTED REVIEW BODY", encoding="utf-8")
    cli_json("review", task_id)
    view = cli_json("inspect", task_id)
    reviewer_systems = [m["content"] for m in view["reviewer_messages"] if m["role"] == "system"]
    assert any(original in m and "[Skill目录]" in m and "content-review" in m for m in reviewer_systems)
    assert not any("UNADOPTED REVIEW BODY" in m for m in reviewer_systems)


def test_catalog_reports_actual_preloads_and_model_reads(cli_env, model_script):
    model_script([_tool("read_skill", {"name": "content-review"}),
                  _tool("read_skill", {"name": "missing"})])
    task_id = _create()["task_id"]
    cli_json("run", task_id, "--max-rounds", "2")
    view = cli_json("inspect", task_id)
    catalog = {item["name"]: item for item in view["skill_catalog"]}
    assert catalog["campaign-creation"]["preloaded_by"] == ["main"]
    assert catalog["content-review"]["read_by_main"] == ["SKILL.md"]
    rejected = [json.loads(m["content"]) for m in view["messages"] if m["name"] == "read_skill"][-1]
    assert rejected["error"] == "unknown_skill"
