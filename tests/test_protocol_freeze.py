"""Actual tools sent to the model stay fixed when the installed code changes."""

import json
import shutil

from conftest import REPO_ROOT, run_cli_process


def test_resume_uses_original_tool_order_after_external_code_update(cli_env, tmp_path):
    runtime = tmp_path / "runtime"
    for name in ("src", "fixtures", "knowledge"):
        shutil.copytree(REPO_ROOT / name, runtime / name)
    shutil.copy2(REPO_ROOT / "pyproject.toml", runtime / "pyproject.toml")
    from cta.prompts import TOOL_DEFINITIONS

    names = [tool["function"]["name"] for tool in TOOL_DEFINITIONS]
    script = tmp_path / "responses.json"
    script.write_text(json.dumps([
        {"name": "search_materials", "arguments": {"query": "自动卡点"}},
        {"name": "stop_incomplete", "arguments": {"reason": "protocol remains valid"}, "expected_tool_names": names},
    ]), encoding="utf-8")
    env = {"PYTHONPATH": str(runtime / "src"), "CTA_MODEL_SCRIPT": str(script)}
    data = tmp_path / "data"
    created = run_cli_process("create", "--topic", "协议冻结", "--budget", "10", "--deadline", "2026-10-30",
                              data_dir=data, extra_env=env)
    task_id = json.loads(created.stdout)["task_id"]
    first = run_cli_process("run", task_id, "--max-rounds", "1", data_dir=data, extra_env=env)
    assert first.returncode == 0, first.stderr
    prompts = runtime / "src/cta/prompts.py"
    with prompts.open("a", encoding="utf-8") as stream:
        stream.write("\nTOOL_DEFINITIONS.reverse()\n")
    resumed = run_cli_process("resume", task_id, data_dir=data, extra_env=env)
    assert resumed.returncode == 0, resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["reason"] == "protocol remains valid"
    inspected = run_cli_process("inspect", task_id, data_dir=data, extra_env=env)
    requests = json.loads(inspected.stdout)["llm_requests"]
    hashes = {json.loads(request["meta_json"])["tool_schema_sha256"] for request in requests}
    assert len(hashes) == 1
