"""Ticket 03: complete campaign draft in the main loop.

Seam: CLI. Script generation is a real isolated model call (injected at the
model boundary in offline tests); the main loop integrates the full campaign.
"""

import copy
import json

from conftest import VALID, cli_json

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

SCRIPT_CANDIDATE = {
    "strategy": "user_scenario",
    "duration_s": 30,
    "segments": [
        {
            "index": 1,
            "time_range": "0-8s",
            "visual": "社团招新现场，人声与鼓点进入",
            "audio": "现场环境声，鼓点渐强",
            "subtitle": "招新季，先把现场拍下来",
            "material_ids": ["M-005"],
            "evidence_ids": [],
        },
        {
            "index": 2,
            "time_range": "8-22s",
            "visual": "产品操作录屏：选中片段对齐节拍，逐段微调",
            "audio": "节奏清晰的流行乐",
            "subtitle": "自动卡点把片段对齐节拍，还能逐段微调",
            "material_ids": ["M-001"],
            "evidence_ids": ["E1"],
        },
        {
            "index": 3,
            "time_range": "22-30s",
            "visual": "成片播放，社团成员看向镜头",
            "audio": "音乐收束",
            "subtitle": "把招新现场剪成你的节奏",
            "material_ids": ["M-005"],
            "evidence_ids": [],
        },
    ],
    "claims": [
        {
            "id": "E1",
            "claim": "自动卡点把视频片段对齐到节拍点",
            "material_id": "M-001",
            "quote": "把用户选中的视频片段对齐到节拍点",
            "scope": "iOS/Android 4.2 及以上；15/30/60 秒成片",
        }
    ],
    "open_questions": [],
}

CAMPAIGN = {
    "strategy": {
        "name": "user_scenario",
        "rationale": "受众更愿意保留手动调整，用社团招新情境承接自动卡点演示",
        "alternatives": [
            {"name": "tutorial", "why_not": "教程信息密度高，30 秒内难以兼顾情境与操作"},
            {"name": "functional_demo", "why_not": "纯功能演示缺少校园情境，吸引力较弱"},
        ],
    },
    "activity": {
        "goal": "让 18-24 岁大学生在社团招新季了解并尝试自动卡点",
        "audience": "18-24 岁大学生，重点是社团招新负责人",
        "theme": "把招新现场剪成你的节奏",
        "format": "1 条 30 秒主视频 + 2 条 15 秒切条",
        "content_items": [
            {"title": "主视频", "purpose": "情境 + 操作演示", "duration_s": 30, "platform": "短视频信息流"},
            {"title": "切条 A", "purpose": "卡点前后对比", "duration_s": 15, "platform": "短视频信息流"},
        ],
    },
    "script": {
        "duration_s": 30,
        "segments": copy.deepcopy(SCRIPT_CANDIDATE["segments"]),
    },
    "materials": [
        {
            "name": "校园场景视频素材包",
            "material_id": "M-005",
            "status": "existing",
            "use": "社团招新情境画面",
            "position": "第 1、3 段",
            "rights_note": "演员授权已取得，限本题活动使用",
        },
        {
            "name": "产品操作录屏",
            "material_id": "",
            "status": "to_create",
            "use": "展示自动卡点操作与逐段微调",
            "position": "第 2 段",
            "rights_note": "待录制，使用正式版本 4.2",
        },
        {
            "name": "背景音乐",
            "material_id": "M-006",
            "status": "to_acquire",
            "use": "卡点演示音乐",
            "position": "第 2 段",
            "rights_note": "商用授权待购买，未取得前不可发布",
        },
    ],
    "production_steps": [
        {"step": "录制自动卡点操作", "owner": "内容制作", "timing": "T-3 天", "note": "使用 4.2 正式版"},
        {"step": "获取音乐授权", "owner": "运营", "timing": "T-2 天", "note": "未取得则替换音乐"},
    ],
    "evidence": [
        {
            "id": "E1",
            "claim": "自动卡点把视频片段对齐到节拍点，可逐段微调",
            "material_id": "M-001",
            "quote": "把用户选中的视频片段对齐到节拍点",
            "scope": "iOS/Android 4.2 及以上；15/30/60 秒成片",
            "use": "主脚本第 2 段字幕与操作演示",
        }
    ],
    "open_questions": [
        {"question": "投放平台清单未确认", "impact": "影响切条画幅", "blocking": False},
    ],
}


def _create(**overrides):
    values = {**VALID, "topic": "校园剪辑挑战", **overrides}
    return cli_json(
        "create", "--topic", values["topic"], "--budget", values["budget"],
        "--deadline", values["deadline"], "--audience", "大学生", "--requirement", "突出自动卡点",
    )


def _tool(name, arguments, **extra):
    return {"type": "tool", "name": name, "arguments": arguments, **extra}


def _brief_tool():
    return _tool("propose_brief", BRIEF)


def _generation_item(candidate=None, **extra):
    item = {
        "channel": "generation",
        "type": "text",
        "content": json.dumps(candidate or SCRIPT_CANDIDATE, ensure_ascii=False),
        "usage": {"prompt_tokens": 800, "completion_tokens": 400, "total_tokens": 1200},
    }
    item.update(extra)
    return item


def _campaign_tool(campaign=None):
    return _tool("propose_campaign", copy.deepcopy(campaign or CAMPAIGN))


def _brief_then_draft_script(generation_item=None, campaign_item=None):
    return [
        _tool("search_materials", {"query": "自动卡点", "mode": "basic"}),
        _brief_tool(),
        _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
        generation_item if generation_item is not None else _generation_item(),
        campaign_item if campaign_item is not None else _campaign_tool(),
    ]


def _run_until_draft(task_id):
    first = cli_json("run", task_id)
    assert first["progress"]["phase"] == "brief_ready"
    return cli_json("run", task_id)


def test_complete_draft_uses_isolated_generation_and_full_campaign(cli_env, model_script):
    task = _create()
    model_script(_brief_then_draft_script())
    doc = _run_until_draft(task["task_id"])

    assert doc["status"] == "in_progress"
    assert doc["progress"]["phase"] == "draft_ready"
    assert doc["progress"]["current_output"]["version"] == 1
    assert doc["progress"]["current_output"]["status"] == "draft"
    assert doc["progress"]["current_output"]["strategy"] == "user_scenario"
    assert doc["plan"]["steps"][1]["status"] == "done"
    assert doc["plan"]["steps"][2]["status"] == "current"
    assert doc["resources"]["sim_spent_cents"] == 20 + 120
    assert doc["artifacts"]["campaign_versions"] == 1

    inspected = cli_json("inspect", task["task_id"])
    assert len(inspected["outputs"]) == 1
    output = inspected["outputs"][0]
    assert output["content"]["activity"]["theme"] == CAMPAIGN["activity"]["theme"]
    assert output["content"]["materials"][1]["status"] == "to_create"
    assert output["content"]["materials"][2]["status"] == "to_acquire"
    assert output["content_md"].startswith("# 把招新现场剪成你的节奏")
    assert inspected["generator_messages_count"] == 3
    purposes = [row["purpose"] for row in inspected["llm_requests"]]
    assert purposes.count("script_generation") == 1
    assert all(row["mock"] == 1 for row in inspected["llm_requests"])
    assert [attempt["kind"] for attempt in inspected["attempts"]] == ["search_materials", "generate_script"]
    assert all(attempt["status"] == "success" for attempt in inspected["attempts"])

    main_messages = " ".join(message["content"] or "" for message in inspected["messages"])
    assert "[脚本生成请求]" not in main_messages
    generator_knowledge = next(
        row for row in inspected["llm_requests"] if row["purpose"] == "script_generation"
    )
    assert '"context": "isolated_generation"' in generator_knowledge["meta_json"]
    assert "user_scenario" in generator_knowledge["meta_json"]


def test_generation_failure_leaves_no_draft_and_keeps_cost(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _generation_item(type="error", message="generation upstream 500"),
            _tool("stop_incomplete", {"reason": "脚本生成连续失败，无法形成完整草稿。"}),
        ]
    )
    doc = _run_until_draft(task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["progress"]["phase"] == "stopped"
    assert doc["resources"]["sim_spent_cents"] == 140
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["outputs"] == []
    assert inspected["attempts"][-1]["kind"] == "generate_script"
    assert inspected["attempts"][-1]["status"] == "failed"


def test_full_path_guard_rejects_generation_that_cannot_be_reviewed(cli_env, model_script):
    task = _create(budget="1.00")
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _tool("stop_incomplete", {"reason": "预算不足以完成生成后的核验与保存。"}),
        ]
    )
    doc = _run_until_draft(task["task_id"])
    assert doc["status"] == "incomplete"
    assert doc["resources"]["sim_spent_cents"] == 20
    inspected = cli_json("inspect", task["task_id"])
    rejection = next(
        message for message in inspected["messages"]
        if message["role"] == "tool" and message["name"] == "generate_script"
    )
    assert "full_path_infeasible" in rejection["content"]
    assert len(inspected["attempts"]) == 1


def test_invalid_campaign_is_rejected_then_valid_one_is_saved(cli_env, model_script):
    task = _create()
    bad = copy.deepcopy(CAMPAIGN)
    bad["evidence"][0]["quote"] = "不存在的原文"
    script = _brief_then_draft_script(campaign_item=_campaign_tool(bad))
    script.append(_campaign_tool())
    model_script(script)
    doc = _run_until_draft(task["task_id"])
    assert doc["progress"]["phase"] == "draft_ready"
    inspected = cli_json("inspect", task["task_id"])
    rejection = next(
        message for message in inspected["messages"]
        if message["role"] == "tool" and message["name"] == "propose_campaign"
    )
    assert "invalid_campaign" in rejection["content"]
    assert "无法在材料正文中找到" in rejection["content"]
    assert [output["version"] for output in inspected["outputs"]] == [1]


def test_script_claim_without_supported_quote_fails_generation(cli_env, model_script):
    task = _create()
    bad_candidate = copy.deepcopy(SCRIPT_CANDIDATE)
    bad_candidate["claims"][0]["quote"] = "材料里没有这句话"
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_direct"}),
            _generation_item(bad_candidate),
            _tool("stop_incomplete", {"reason": "脚本事实无支持，无法继续。"}),
        ]
    )
    doc = _run_until_draft(task["task_id"])
    assert doc["status"] == "incomplete"
    inspected = cli_json("inspect", task["task_id"])
    assert inspected["outputs"] == []
    assert inspected["attempts"][-1]["status"] == "failed"


def test_rerun_after_draft_does_not_duplicate_or_recharge(cli_env, model_script):
    task = _create()
    model_script(_brief_then_draft_script())
    first_draft = _run_until_draft(task["task_id"])
    inspected = cli_json("inspect", task["task_id"])
    messages = len(inspected["messages"])
    generator_messages = inspected["generator_messages_count"]

    again = cli_json("run", task["task_id"])
    second = cli_json("inspect", task["task_id"])
    assert again["run"]["stop_reason"] == "draft_ready"
    assert again["progress"]["current_output"]["version"] == first_draft["progress"]["current_output"]["version"] == 1
    assert again["resources"]["sim_spent_cents"] == first_draft["resources"]["sim_spent_cents"] == 140
    assert len(second["messages"]) == messages
    assert second["generator_messages_count"] == generator_messages
    assert len(second["outputs"]) == 1


def test_multi_candidate_generation_uses_bounded_internal_requests(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "自动卡点"}),
            _brief_tool(),
            _tool("generate_script", {"strategy": "user_scenario", "generation_mode": "generate_multi"}),
            _generation_item(),
            _generation_item(),
            _generation_item(),
            _campaign_tool(),
        ]
    )
    doc = _run_until_draft(task["task_id"])
    assert doc["progress"]["phase"] == "draft_ready"
    assert doc["resources"]["sim_spent_cents"] == 20 + 300
    inspected = cli_json("inspect", task["task_id"])
    generation_requests = [row for row in inspected["llm_requests"] if row["purpose"] == "script_generation"]
    assert len(generation_requests) == 3
    assert inspected["attempts"][-1]["meta_json"].count("internal_requests") == 1
    assert '"internal_requests": 3' in inspected["attempts"][-1]["meta_json"]


def test_malformed_alternatives_are_rejected_without_crashing(cli_env, model_script):
    task = _create()
    campaign = copy.deepcopy(CAMPAIGN)
    campaign["strategy"]["alternatives"] = ["tutorial"]
    script = _brief_then_draft_script(campaign_item=_campaign_tool(campaign))
    script.append(_campaign_tool())
    model_script(script)
    doc = _run_until_draft(task["task_id"])
    assert doc["progress"]["phase"] == "draft_ready"
    inspected = cli_json("inspect", task["task_id"])
    rejection = next(
        message for message in inspected["messages"]
        if message["role"] == "tool" and message["name"] == "propose_campaign"
    )
    assert "invalid_campaign" in rejection["content"]
    assert "alternatives[0]" in rejection["content"]
