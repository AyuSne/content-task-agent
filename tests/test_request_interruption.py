"""An API attempt that never returns is durable and its usage remains unknown."""


import pytest
from conftest import cli_json, run_cli_process
from test_campaign_draft import _brief_then_draft_script, _create, _generation_item, _tool
from test_review_save import REVIEW_PASS, _review_item


@pytest.mark.parametrize("channel", ["main", "generation", "review"])
def test_process_death_during_model_request_preserves_attempt_before_resume(cli_env, model_script, channel):
    if channel == "main":
        items = [{"type": "crash"}, _tool("stop_incomplete", {"reason": "已明确保留中断请求，不继续业务。"})]
        purpose = "main_loop_round_1"
    elif channel == "generation":
        items = _brief_then_draft_script()
        items = items[:3] + [{"type": "crash", "channel": "generation"}, items[2], _generation_item(), items[-1]]
        purpose = "script_generation"
    else:
        items = [*_brief_then_draft_script(), _tool("review_campaign", {}), {"type": "crash", "channel": "review"},
                 _tool("review_campaign", {}), _review_item(REVIEW_PASS), _tool("save_campaign", {})]
        purpose = "review"
    script = model_script(items)
    task_id = _create()["task_id"]
    process = run_cli_process("run", task_id, data_dir=cli_env / "data", extra_env={"CTA_MODEL_SCRIPT": str(script)})
    assert process.returncode == 70, process.stderr
    before = cli_json("inspect", task_id)
    pending = [request for request in before["llm_requests"] if request["purpose"] == purpose]
    assert pending and pending[-1]["status"] == "running"
    request_id = pending[-1]["id"]
    used = before["progress"]["used_rounds"]
    resumed = run_cli_process("resume", task_id, "--max-rounds", "2", data_dir=cli_env / "data",
                              extra_env={"CTA_MODEL_SCRIPT": str(script)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json("inspect", task_id)
    recovered = next(request for request in after["llm_requests"] if request["id"] == request_id)
    assert recovered["status"] == "interrupted"
    assert recovered["prompt_tokens"] is None
    assert recovered["response_model"] is None
    assert "未知" in recovered["error"]
    assert after["progress"]["used_rounds"] >= used
    assert after["resources"]["real_model_usage"]["unknown_usage_requests"] >= 1
