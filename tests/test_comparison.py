"""Current independent readback comparison remains available."""

import json

from test_campaign_draft import CAMPAIGN
from test_review_save import REVIEW_PASS


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


def test_readback_arms_cannot_use_extra_requests_to_correct_a_protocol_failure(cli_env, model_script, capsys):
    from cta.comparison import main

    case_path = cli_env / "protocol-pair.json"
    case_path.write_text(json.dumps({"version": "protocol-pair", "pairs": [{
        "id": "sufficient", "requirements": ["事实支持"], "evidence": [{"material_id": "M-001"}],
        "campaign": CAMPAIGN, "material_ids": ["M-001"],
    }]}), encoding="utf-8")
    invalid = {"channel": "comparison_review", "name": "report_review", "arguments": {}}
    model_script([
        invalid, invalid,
        {"channel": "comparison_review", "name": "report_review", "arguments": REVIEW_PASS},
    ])
    assert main(["--method", "readback", "--mode", "mock", "--case", str(case_path),
                 "--output", str(cli_env / "protocol-output")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [arm["report_status"] for arm in result["results"]] == ["incomplete", "incomplete"]
    assert [arm["requests"] for arm in result["results"]] == [1, 1]
    assert [arm["readbacks"] for arm in result["results"]] == [0, 0]
