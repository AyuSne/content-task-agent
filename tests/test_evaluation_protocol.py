"""The three public reviewer entries share one bounded protocol contract."""

import json

import pytest
from conftest import cli_json
from test_campaign_draft import CAMPAIGN, _brief_then_draft_script, _create, _run_until_draft
from test_review_save import REVIEW_PASS


def _item(name, arguments, channel="review"):
    return {"channel": channel, "name": name, "arguments": arguments}


def _entry(entry, cli_env, model_script, capsys, invalid):
    """Observe only public CLI results and persisted/exported messages."""
    report = _item("report_review", REVIEW_PASS)
    if entry == "production":
        model_script([*_brief_then_draft_script(), invalid, report])
        task = _create()
        _run_until_draft(task["task_id"])
        result = cli_json("review", task["task_id"])
        messages = cli_json("inspect", task["task_id"])["reviewer_messages"]
        return messages, result["review"]["readbacks_used"], result["review"]["requests_used"]
    if entry == "calibration":
        pairs = {"version": "protocol-test", "pairs": [{
            "id": "hidden-correct-label", "issue": "test", "requirements": ["事实支持"],
            "evidence": [{"material_id": "M-001"}], "bad": CAMPAIGN, "good": CAMPAIGN,
        }]}
        path = cli_env / "pairs.json"
        path.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
        model_script([invalid, report, report])
        result = cli_json("calibrate", "--pairs", str(path))
        first = result["results"][0]
        return first["messages"], first["readbacks"], first["requests"]
    from cta.comparison import main

    case = {"version": "protocol-test", "pairs": [{
        "id": "hidden-correct-label", "requirements": ["事实支持"],
        "evidence": [{"material_id": "M-001"}], "campaign": CAMPAIGN,
        "material_ids": ["M-001"], "expected": {"label": "never-load-this-answer"},
    }]}
    path = cli_env / "readback.json"
    path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    report = {**report, "channel": "comparison_review"}
    model_script([report, {**invalid, "channel": "comparison_review"}, report])
    output = cli_env / "comparison"
    assert main(["--method", "readback", "--mode", "mock", "--case", str(path), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)["results"][1]
    trace = json.loads((output / "request-trace.json").read_text(encoding="utf-8"))
    messages = next(item["final_messages"] for item in trace if item["arm"] == "bounded" and "final_messages" in item)
    return messages, result["readbacks"], result["requests"]


@pytest.mark.parametrize("entry", ["production", "calibration", "comparison"])
@pytest.mark.parametrize("invalid, expected_errors", [
    (_item("read_material", {"material_id": []}), ["invalid_arguments"]),
    (_item("read_material", {"material_id": "M-001", "extra": True}), ["invalid_arguments"]),
    (_item("save_campaign", {}), ["unknown_tool"]),
    ({"channel": "review", "type": "multi", "calls": [
        {"name": "read_material", "arguments": {"material_id": "M-001"}},
        {"name": "report_review", "arguments": REVIEW_PASS},
    ]}, ["review_protocol", "review_protocol"]),
])
def test_rejected_actions_have_consistent_feedback_without_reads(
    entry, invalid, expected_errors, cli_env, model_script, capsys,
):
    messages, reads, requests = _entry(
        entry, cli_env, model_script, capsys, invalid,
    )
    results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    assert [result["error"] for result in results if not result["ok"]] == expected_errors
    assert reads == 0
    assert requests == (1 if entry == "comparison" else 2)
    assert "never-load-this-answer" not in json.dumps(messages)


def test_external_exception_is_recorded_but_internal_callback_failure_propagates():
    from cta.evaluation import evaluate
    from cta.model import ModelResponse, ToolCall

    class FailingModel:
        def complete(self, *args, **kwargs):
            raise OSError("provider unavailable")

    recorded = []
    result = evaluate([], FailingModel(), lambda mid: {}, output_version=1,
                      on_request=lambda *record: recorded.append(record))
    assert result.requests == len(recorded) == 1
    assert result.report is None
    assert result.request_records[0]["usage"] is None
    assert result.usage["unknown_usage_requests"] == 1
    assert result.request_records[0]["started_at"] <= result.request_records[0]["finished_at"]
    assert "provider unavailable" in result.errors[0]

    class ReaderModel:
        def complete(self, *args, **kwargs):
            return ModelResponse(tool_calls=(ToolCall("r1", "read_material", '{"material_id":"M-001"}'),))

    def broken_reader(material_id):
        raise RuntimeError("broken internal contract")

    with pytest.raises(RuntimeError, match="broken internal contract"):
        evaluate([], ReaderModel(), broken_reader, output_version=1)


def test_truncated_multi_calls_are_all_paired_and_do_not_form_a_pass():
    from cta.evaluation import evaluate, review_report_status
    from cta.model import ModelResponse, ToolCall

    class TruncatedModel:
        def complete(self, *args, **kwargs):
            return ModelResponse(finish_reason="length", tool_calls=(
                ToolCall("read1", "read_material", '{"material_id":"M-001"}'),
                ToolCall("report1", "report_review", json.dumps(REVIEW_PASS)),
            ))

    def forbidden_read(material_id):
        pytest.fail("Rejected calls must not read anything")

    result = evaluate([], TruncatedModel(), forbidden_read, output_version=1, max_requests=1)
    assert result.requests == 1
    assert result.readbacks == 0
    assert review_report_status(result.report) == "incomplete"
    assert [message["tool_call_id"] for message in result.messages if message["role"] == "tool"] == ["read1", "report1"]


def test_read_only_extra_requests_allow_two_original_reads_then_one_report():
    from cta.evaluation import evaluate, review_report_status
    from cta.model import ModelResponse, ToolCall

    class ReadThenReport:
        def complete(self, messages, tools, *, cursor, channel="review"):
            if cursor < 2:
                call = ToolCall(f"read{cursor}", "read_material", json.dumps({"material_id": f"M-00{cursor + 1}"}))
            else:
                call = ToolCall("report", "report_review", json.dumps(REVIEW_PASS))
            return ModelResponse(tool_calls=(call,))

    result = evaluate([], ReadThenReport(), lambda mid: {"ok": True, "material_id": mid},
                      output_version=1, protocol_corrections=False)
    assert result.requests == 3
    assert result.readbacks == 2
    assert review_report_status(result.report) == "complete"
