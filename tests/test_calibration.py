"""Ticket 11: six-pair reviewer calibration entry and metrics."""

import json

from conftest import run_cli


def _finding():
    return {
        "finding_id": "F1",
        "severity": "must_fix",
        "location": "主脚本第 1 段",
        "requirement": "事实支持",
        "evidence": "材料不支持该承诺",
        "issue": "字幕超出材料支持范围。",
        "suggestion": "改为材料支持的表达。",
        "reviewed_version": "1",
    }


def _report(verdict: str, findings=None):
    return {
        "verdict": verdict,
        "completeness": "full",
        "summary": "校准输出",
        "findings": findings or [],
        "evidence_gaps": [],
    }


def _script_items(all_good=False):
    items = []
    for _ in range(6):
        items.append(
            {
                "channel": "review",
                "type": "tool",
                "name": "report_review",
                "arguments": _report("pass" if all_good else "fail", [] if all_good else [_finding()]),
            }
        )
        items.append(
            {
                "channel": "review",
                "type": "tool",
                "name": "report_review",
                "arguments": _report("pass"),
            }
        )
    return items


def test_calibration_reports_hits_and_no_good_false_positives(cli_env, model_script, tmp_path):
    model_script(_script_items())
    output = tmp_path / "calibration.json"
    code, out, err = run_cli("calibrate", "--output", str(output))
    assert code == 0, err
    document = json.loads(out)
    assert document["pairs_count"] == 6
    assert document["metrics"]["problem_samples"] == 6
    assert document["metrics"]["bad_detected"] == 6
    assert document["metrics"]["missed"] == []
    assert document["metrics"]["false_positives"] == []
    assert document["metrics"]["incomplete_reviews"] == 0
    assert document["usage"]["requests"] == 12
    assert output.is_file()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["metrics"] == document["metrics"]
    assert all("label_source" not in json.dumps(result["findings"]) for result in saved["results"])


def test_calibration_marks_misses_but_not_false_positives(cli_env, model_script, tmp_path):
    model_script(_script_items(all_good=True))
    code, out, err = run_cli("calibrate", "--output", str(tmp_path / "misses.json"))
    assert code == 0, err
    document = json.loads(out)
    assert document["metrics"]["bad_detected"] == 0
    assert len(document["metrics"]["missed"]) == 6
    assert document["metrics"]["false_positives"] == []


def test_calibration_does_not_leak_labels_into_evaluator_payload(cli_env, model_script, tmp_path):
    model_script(_script_items())
    captured = []

    class RecordingClient:
        def complete(self, messages, tools, *, cursor, channel="main", json_mode=False):
            captured.append(json.dumps(messages, ensure_ascii=False))
            from cta.model import ModelResponse

            return ModelResponse(
                content=None,
                tool_calls=(),
                finish_reason="stop",
                model="record",
                mock=True,
            )

    from cta.calibration import run_calibration
    from cta.config import load_config

    config = load_config(mode="mock", data_dir=cli_env / "data")
    run_calibration(config, output_path=tmp_path / "labels.json", client=RecordingClient())
    assert captured
    combined = " ".join(captured)
    assert "P1-unsupported-capability" not in combined
    assert '"variant"' not in combined
    assert "label_source" not in combined