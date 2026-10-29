"""Ticket 02: main loop, evidence-backed brief, versioned knowledge and accounting.

Seam: CLI. The model is injected at its system boundary with a scripted client;
tool results, messages, money and time are real product behaviour.
"""

import json

from conftest import VALID, cli_json, run_cli_process, run_scripted_stage

BRIEF = {
    "goal": "让大学生在社团招新季了解帧剪的自动卡点",
    "audience": "18-24 岁大学生（合成调研样本）",
    "product_facts": [
        {
            "claim": "自动卡点可把视频片段对齐到节拍点",
            "material_id": "M-001",
            "quote": "把用户选中的视频片段对齐到节拍点",
            "scope": "iOS/Android 4.2 及以上；15/30/60 秒成片",
            "use": "主脚本卡点段",
        }
    ],
    "creative_requirements": [{"text": "突出自动卡点", "source": "用户输入"}],
    "assumptions": [{"text": "活动平台为短视频信息流", "basis": "题材常识，待确认"}],
    "suggestions": [{"text": "采用校园社团情境", "basis": "M-003 调研"}],
    "unknowns": [{"question": "是否需要投放预算？", "impact": "影响内容条数", "blocking": False}],
    "sources": [
        {"material_id": "M-001", "used_for": "能力依据"},
        {"material_id": "M-003", "used_for": "受众依据"},
    ],
}


def _create(**overrides):
    values = {**VALID, "topic": "校园剪辑挑战", **overrides}
    return cli_json(
        "create",
        "--topic", values["topic"],
        "--budget", values["budget"],
        "--deadline", values["deadline"],
        "--audience", "大学生",
        "--requirement", "突出自动卡点",
    )


def _tool(name, arguments, usage=None):
    item = {"type": "tool", "name": name, "arguments": arguments}
    if usage is not None:
        item["usage"] = usage
    return item


def _brief_tool(**brief_overrides):
    brief = json.loads(json.dumps(BRIEF, ensure_ascii=False))
    brief.update(brief_overrides)
    return _tool("propose_brief", brief)


def test_scripted_main_loop_produces_valid_brief_with_evidence_and_accounting(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点", "mode": "basic"}, usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}),
            _tool("read_material", {"material_id": "M-001"}),
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])

    assert doc["status"] == "in_progress"
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["progress"]["used_rounds"] == 3
    assert doc["brief"]["version"] == 1
    assert doc["brief"]["content"]["goal"] == BRIEF["goal"]
    assert doc["plan"]["steps"][0]["status"] == "done"
    assert doc["plan"]["steps"][1]["status"] == "current"
    assert doc["gaps"] and doc["gaps"][0]["source"] == "brief"
    assert "Mock" in doc["mode_notice"]
    assert doc["resources"]["sim_spent_cents"] == 20
    assert doc["run"]["rounds_used"] == 3

    inspected = cli_json("inspect", task["task_id"])
    assert inspected["knowledge"][0]["knowledge_id"] == "campaign-creation"
    assert len(inspected["knowledge"][0]["sha256"]) == 64
    assert inspected["knowledge"][0]["chars"] > 500
    assert [a["status"] for a in inspected["attempts"]] == ["success"]
    assert inspected["ledger"][0]["amount_cents"] == 20
    assert len(inspected["llm_requests"]) == 3
    assert all(row["mock"] == 1 and row["status"] == "success" for row in inspected["llm_requests"])
    assert inspected["resources"]["mock_requests"] == 3
    assert inspected["resources"]["real_requests"] == 0

    roles = [message["role"] for message in inspected["messages"]]
    assert roles[:4] == ["system", "system", "user", "user"]
    tool_messages = [message for message in inspected["messages"] if message["role"] == "tool"]
    assert [message["name"] for message in tool_messages] == ["search_materials", "read_material", "propose_brief"]
    assert json.loads(tool_messages[0]["content"])["ok"] is True


def test_multiple_tool_calls_are_all_rejected_and_counted(cli_env, model_script):
    task = _create()
    model_script(
        [
            {"type": "multi", "calls": [
                {"name": "search_materials", "arguments": {"query": "自动卡点"}},
                {"name": "read_material", "arguments": {"material_id": "M-001"}},
            ]},
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["used_rounds"] == 2
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["resources"]["sim_spent_cents"] == 0

    inspected = cli_json("inspect", task["task_id"])
    rejected = [m for m in inspected["messages"] if m["role"] == "tool" and "multiple_calls" in (m["content"] or "")]
    assert len(rejected) == 2
    assert inspected["attempts"] == []


def test_unknown_and_invalid_tools_do_not_execute_business_actions(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("publish_campaign", {"path": "x"}),
            _tool("search_materials", "not-json"),
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["used_rounds"] == 3
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    contents = [m["content"] or "" for m in inspected["messages"] if m["role"] == "tool"]
    assert any("unknown_tool" in content for content in contents)
    assert any("invalid_arguments" in content for content in contents)


def test_truncated_response_rejects_calls_and_asks_for_one_complete_action(cli_env, model_script):
    task = _create()
    model_script(
        [
            {"type": "length", "content": "{\"type\":\"tool\"", "usage": {"prompt_tokens": 10, "completion_tokens": 2}},
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["progress"]["used_rounds"] == 2
    assert doc["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    assert any("截断" in (m["content"] or "") for m in inspected["messages"] if m["role"] == "user")


def test_text_without_tool_call_is_feedback_not_a_business_action(cli_env, model_script):
    task = _create()
    model_script([{"type": "text", "content": "我还需要更多信息。"}, _brief_tool()])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["used_rounds"] == 2
    assert doc["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    assert any("没有调用任何工具" in (m["content"] or "") for m in inspected["messages"] if m["role"] == "user")


def test_model_error_stops_incomplete_and_keeps_progress(cli_env, model_script):
    task = _create()
    model_script([{"type": "error", "message": "upstream 503"}])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["progress"]["phase"] == "stopped"
    assert "503" in doc["reason"]
    assert doc["progress"]["used_rounds"] == 1
    assert doc["brief"] is None
    assert doc["resources"]["sim_spent_cents"] == 0
    assert doc["run"]["stop_reason"] == "model_error"
    assert doc["resources"]["real_usage"]["unknown_usage_requests"] == 1
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["llm_requests"][0]["prompt_tokens"] is None
    assert inspected["llm_requests"][0]["status"] == "error"


def test_estimate_covers_full_remaining_path_without_charging(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool(
                "estimate_cost",
                {"actions": ["search_basic", "generate_direct", "review", "revision", "review", "save"]},
            ),
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    estimate_message = next(
        message for message in inspected["messages"] if message["role"] == "tool" and message["name"] == "estimate_cost"
    )
    estimate = json.loads(estimate_message["content"])["estimate"]
    steps = [line["mode"] for line in estimate["lines"]]
    assert steps == ["search_basic", "generate_direct", "review", "revision", "review", "save"]
    assert estimate["total_cost_cents"] == 20 + 120 + 80 + 120 + 80 + 10
    assert estimate["total_duration_ms"] == 2000 + 8000 + 6000 + 8000 + 6000 + 1000
    assert estimate["nature"] == "estimated"
    assert inspected["attempts"] == []


def test_budget_guard_rejects_before_attempt_and_model_continues(cli_env, model_script):
    task = _create(budget="0.01")
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点"}),
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["resources"]["sim_spent_cents"] == 0
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["attempts"] == []
    assert inspected["ledger"] == []
    rejection = next(m["content"] for m in inspected["messages"] if m["role"] == "tool")
    assert "budget_exceeded" in rejection


def test_deadline_guard_rejects_long_search(cli_env, model_script):
    task = _create(deadline="2026-10-29T00:00:01+08:00")
    model_script([_tool("search_materials", {"query": "自动卡点"}), _brief_tool()])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["attempts"] == []
    rejection = next(m["content"] for m in inspected["messages"] if m["role"] == "tool")
    assert "deadline_exceeded" in rejection


def test_material_with_only_an_id_returns_a_gap_and_brief_stays_cautious(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("read_material", {"material_id": "M-010"}),
            _brief_tool(unknowns=[{"question": "白皮书正文未纳入，是否补充？", "impact": "影响能力表述", "blocking": False}]),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    inspected = cli_json("inspect", task["task_id"])
    tool_messages = [m for m in inspected["messages"] if m["role"] == "tool"]
    assert "material_body_missing" in tool_messages[0]["content"]
    assert doc["brief"]["content"]["unknowns"][0]["question"].startswith("白皮书正文")


def test_material_instructions_do_not_authorize_completion(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "忽略", "mode": "extended"}),
            _brief_tool(),
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["status"] != "complete"
    assert doc["progress"]["save"] is None
    inspected = cli_json("inspect", task["task_id"])
    search_result = json.loads(next(m["content"] for m in inspected["messages"] if m["role"] == "tool"))
    assert search_result["results"][0]["material_id"] == "M-009"
    assert "warning" in search_result["results"][0]
    assert not any("忽略原有要求" in (m["content"] or "") and m["role"] == "assistant" for m in inspected["messages"])


def test_brief_validation_rejects_unverifiable_quote_then_accepts_valid_one(cli_env, model_script):
    task = _create()
    bad = _brief_tool()
    bad["arguments"]["product_facts"][0]["quote"] = "这句话不存在于材料里"
    model_script([bad, _brief_tool()])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["progress"]["phase"] == "brief_ready"
    assert doc["progress"]["used_rounds"] == 2
    assert doc["brief"]["version"] == 1
    inspected = cli_json("inspect", task["task_id"])
    rejection = next(m for m in inspected["messages"] if m["role"] == "tool")
    assert "invalid_brief" in rejection["content"]
    assert "无法在 M-001" in rejection["content"]


def test_clarification_stops_for_user_and_rerun_does_not_execute(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool(
                "request_clarification",
                {"questions": [
                    {"question": "活动目标是拉新还是促活？", "impact": "决定内容与行动引导", "blocking": True},
                ]},
            )
        ]
    )
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "needs_input"
    assert doc["progress"]["phase"] == "awaiting_clarification"
    assert "关键阻塞 1" in doc["reason"]
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["clarifications"][0]["questions"][0]["blocking"] is True

    before = len(inspected["messages"])
    again = run_scripted_stage("run", task["task_id"])
    assert again["status"] == "needs_input"
    assert again["run"]["stop_reason"] == "needs_input"
    assert len(cli_json("inspect", task["task_id"])["messages"]) == before


def test_stop_incomplete_preserves_state(cli_env, model_script):
    task = _create()
    model_script([_tool("stop_incomplete", {"reason": "现有资料无法支持核心卖点，且预算不足以补查。"})])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["progress"]["phase"] == "stopped"
    assert "现有资料" in doc["reason"]


def test_round_cap_stops_incomplete(cli_env, model_script):
    task = _create()
    limit = task["progress"]["max_rounds"]
    model_script([{"type": "text", "content": f"第 {i} 轮没有动作"} for i in range(limit)])
    doc = run_scripted_stage("run", task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["progress"]["used_rounds"] == limit
    assert "轮数上限" in doc["reason"]


def test_mock_adapter_exception_never_counts_as_a_real_model_request(cli_env, model_script):
    task = _create()
    model_script([{}])
    doc = run_scripted_stage("run", task["task_id"])
    usage = doc["resources"]["real_model_usage"]
    assert doc["mode"] == "mock"
    assert usage["real_requests"] == 0
    assert usage["mock_requests"] == 1


def test_brief_is_not_reprocessed_or_recharged_on_resume(cli_env, model_script):
    task = _create()
    model_script([_tool("search_materials", {"query": "自动卡点"}), _brief_tool(), {"type": "text", "content": "不应被调用"}])
    first = run_scripted_stage("run", task["task_id"])
    inspected = cli_json("inspect", task["task_id"])
    message_count = len(inspected["messages"])
    knowledge_count = len(inspected["knowledge"])
    assert first["progress"]["phase"] == "brief_ready"
    assert first["brief"]["version"] == 1

    second = cli_json("run", task["task_id"])
    again = cli_json("inspect", task["task_id"])

    assert second["status"] == "incomplete"
    assert again["brief"]["version"] == 1
    assert len(again["knowledge"]) == knowledge_count == 1
    assert len(again["messages"]) > message_count
    assert again["resources"]["sim_spent_cents"] == 20
    assert again["progress"]["used_rounds"] > first["progress"]["used_rounds"]


def test_cross_process_resume_continues_from_persisted_round(cli_env, model_script):
    task = _create()
    script = model_script(
        [
            _tool("search_materials", {"query": "自动卡点"}),
            _brief_tool(),
        ]
    )
    data_dir = cli_env / "data"
    first = run_cli_process(
        "run", task["task_id"], "--max-rounds", "1",
        data_dir=data_dir, extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert first.returncode == 0, first.stderr
    first_doc = json.loads(first.stdout)
    assert first_doc["progress"]["used_rounds"] == 1
    assert first_doc["resources"]["sim_spent_cents"] == 20
    assert first_doc["brief"] is None

    second = run_cli_process(
        "run", task["task_id"], "--max-rounds", "1",
        data_dir=data_dir, extra_env={"CTA_MODEL_SCRIPT": str(script)},
    )
    assert second.returncode == 0, second.stderr
    second_doc = json.loads(second.stdout)
    assert second_doc["progress"]["used_rounds"] == 2
    assert second_doc["progress"]["phase"] == "brief_ready"
    assert second_doc["brief"]["version"] == 1
    assert second_doc["resources"]["sim_spent_cents"] == 20
