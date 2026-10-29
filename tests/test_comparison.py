"""The experimental CLI runs actual model boundary calls; it never reuses labelled calibration drafts."""
import copy
import json

from conftest import cli_json
from test_campaign_draft import BRIEF, CAMPAIGN, SCRIPT_CANDIDATE
from test_review_save import REVIEW_FAIL, REVIEW_PASS


def _run(tmp_path, capsys, method, **case_changes):
    from cta.comparison import main

    case = {"topic": "社团卡点", "budget": "10", "deadline": "2026-10-30T12:00:00+08:00",
            "audience": "18-24岁大学生", "requirements": ["突出自动卡点"], **case_changes}
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / method
    code = main(["--mode", "mock", "--method", method, "--case", str(path), "--output", str(output)])
    report = json.loads(capsys.readouterr().out)
    return code, report, output


def test_oneshot_uses_model_output_and_records_independent_assessment(cli_env, model_script, capsys):
    candidate = copy.deepcopy(CAMPAIGN)
    candidate["activity"]["theme"] = "来自本次生成的唯一主题"
    model_script([
        {"channel": "comparison", "name": "propose_campaign", "arguments": candidate},
        {"channel": "review", "name": "report_review", "arguments": REVIEW_PASS},
    ])
    code, report, output = _run(cli_env, capsys, "oneshot")
    assert code == 0
    assert report["method"] == "oneshot"
    assert report["generation_requests"] == 1
    assert report["dynamic_recovery"] == "not_applicable"
    inspected = cli_json("inspect", report["task_id"])
    assert inspected["outputs"][-1]["content"]["activity"]["theme"] == "来自本次生成的唯一主题"
    assert len(inspected["llm_requests"]) == 2
    assert inspected["resources"]["sim_spent_cents"] == 200
    assert inspected["reviews"][-1]["verdict"] == "pass"
    assert report["execution_simulated_cost_cents"] == 120
    assert report["external_assessment"]["simulated_cost_cents"] == 80
    assert (output / "inspect.json").is_file()
    assert (output / "request-trace.json").is_file()


def test_fixed_flow_keeps_real_generation_but_stops_on_review_failure(cli_env, model_script, capsys):
    script = copy.deepcopy(SCRIPT_CANDIDATE)
    script["strategy"] = "functional_demo"
    candidate = copy.deepcopy(CAMPAIGN)
    candidate["strategy"]["name"] = "functional_demo"
    model_script([
        {"channel": "comparison", "name": "propose_brief", "arguments": BRIEF},
        {"channel": "generation", "type": "text", "content": json.dumps(script, ensure_ascii=False)},
        {"channel": "comparison", "name": "propose_campaign", "arguments": candidate},
        {"channel": "review", "name": "report_review", "arguments": REVIEW_FAIL},
    ])
    code, report, _ = _run(cli_env, capsys, "fixed")
    assert code == 0
    assert report["status"] == "incomplete"
    assert report["error"]["error"] == "unresolved_must_fix"
    inspected = cli_json("inspect", report["task_id"])
    assert len(inspected["outputs"]) == 1
    assert inspected["saves"] == []
    assert len(inspected["llm_requests"]) == 4
    assert inspected["resources"]["sim_spent_cents"] == 200
    assert report["dynamic_recovery"] == "fixed_stop"


def test_readback_comparison_only_changes_read_permission_and_hides_labels(cli_env, model_script, capsys):
    from cta.comparison import main

    case = {"version": "holdout-test", "pairs": [{
        "id": "source-needed", "requirements": ["事实应有支持"],
        "evidence": [{"material_id": "M-001"}], "material_ids": ["M-001"],
        "campaign": CAMPAIGN, "expected": {"label": "holdout-answer-must-not-be-shown"},
    }]}
    case_path = cli_env / "readback.json"
    case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    model_script([
        {"channel": "comparison_review", "name": "report_review", "arguments": REVIEW_PASS},
        {"channel": "comparison_review", "name": "read_material", "arguments": {"material_id": "M-001"}},
        {"channel": "comparison_review", "name": "report_review", "arguments": REVIEW_PASS},
    ])
    output = cli_env / "readbacks"
    assert main(["--method", "readback", "--mode", "mock", "--case", str(case_path), "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["results"]) == 2
    single, bounded = report["results"]
    assert single["requests"] == 1
    assert bounded["requests"] == 2
    assert bounded["readbacks"] == 1
    assert single["campaign_sha256"] == bounded["campaign_sha256"]
    trace = (output / "request-trace.json").read_text(encoding="utf-8")
    assert "holdout-answer-must-not-be-shown" not in trace
    assert "把用户选中的视频片段对齐到节拍点" in trace


def test_failed_baseline_without_candidate_still_retains_attempts_and_result(cli_env, model_script, capsys):
    model_script([])
    code, report, output = _run(cli_env, capsys, "oneshot", budget="1")
    assert code == 0
    assert report["status"] == "incomplete"
    assert report["export"]["error"] == "no_output"
    assert report["generation_requests"] == 0
    assert (output / "result.json").is_file()
