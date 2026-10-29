"""Ticket 03: complete campaign draft in the main loop.

Seam: CLI. Script generation is a real isolated model call (injected at the
model boundary in offline tests); the main loop integrates the full campaign.
"""

import copy
import json

from conftest import VALID, cli_json, run_scripted_stage

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
    first = run_scripted_stage("run", task_id)
    assert first["progress"]["phase"] == "brief_ready"
    return run_scripted_stage("run", task_id)


def test_complete_draft_uses_main_context_and_full_campaign(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    doc = cli_json("status", task["task_id"])
    assert doc["status"] == "in_progress"
    assert doc["progress"]["phase"] == "draft_ready"
    assert doc["progress"]["current_output"]["version"] == 1
    assert doc["progress"]["current_output"]["status"] == "draft"
    assert doc["plan"] == {"version": 0, "steps": []}
    assert doc["resources"]["sim_spent_cents"] == 140
    inspected = cli_json("inspect", task["task_id"])
    output = next(x for x in inspected["outputs"] if x["kind"] == "campaign")
    assert output["content"]["activity"]["theme"] == CAMPAIGN["activity"]["theme"]
    assert output["content"]["materials"][1]["status"] == "to_create"
    assert output["content"]["materials"][2]["status"] == "to_acquire"
    from pathlib import Path
    exported = cli_json("export", task["task_id"])
    markdown = next(item["path"] for item in exported["files"] if item["path"].endswith("campaign-v1.md"))
    assert Path(markdown).read_text(encoding="utf-8").startswith("# 把招新现场剪成你的节奏")
    assert inspected["generator_messages_count"] == 0
    assert all(row["purpose"] != "script_generation" and row["mock"] == 1 for row in inspected["llm_requests"])
    assert [a["kind"] for a in inspected["attempts"]] == ["search_materials", "submit_script"]
    assert all(a["status"] == "success" for a in inspected["attempts"])


def test_interrupted_script_attempt_keeps_charge_and_no_draft(cli_env, model_script):
    from conftest import run_cli_process
    from test_direct_script import SCRIPT
    task = _create()
    responses = [_tool("search_materials", {"query": "自动卡点"}), _brief_tool(),
                 _tool("submit_script", {"brief_version": 1, "script": SCRIPT})]
    path = model_script(responses)
    crashed = run_cli_process("run", task["task_id"], data_dir=cli_env / "data",
                              extra_env={"CTA_MODEL_SCRIPT": str(path), "CTA_FAULT_CRASH": "script_after_start"})
    assert crashed.returncode == 70
    resumed = run_cli_process("resume", task["task_id"], "--max-rounds", "0", data_dir=cli_env / "data",
                              extra_env={"CTA_MODEL_SCRIPT": str(path)})
    assert resumed.returncode == 0, resumed.stderr
    view = cli_json("inspect", task["task_id"])
    assert not view["outputs"] and view["resources"]["sim_spent_cents"] == 140
    assert view["attempts"][-1]["kind"] == "submit_script" and view["attempts"][-1]["status"] == "interrupted"


def test_budget_guard_rejects_script_before_start(cli_env, model_script):
    from test_direct_script import SCRIPT
    task = _create(budget="1.00")
    model_script([_tool("search_materials", {"query": "自动卡点"}), _brief_tool(),
                  _tool("submit_script", {"brief_version": 1, "script": SCRIPT}),
                  _tool("stop_incomplete", {"reason": "预算不足"})])
    doc = cli_json("run", task["task_id"], "--max-rounds", "4")
    assert doc["status"] == "incomplete" and doc["resources"]["sim_spent_cents"] == 20
    view = cli_json("inspect", task["task_id"])
    rejection = next(json.loads(m["content"]) for m in view["messages"] if m["role"] == "tool" and m["name"] == "submit_script")
    assert rejection["error"] == "budget_exceeded" and rejection["started"] is False
    assert len(view["attempts"]) == 1 and not view["outputs"]


def test_invalid_campaign_is_rejected_then_valid_one_is_saved(cli_env, model_script):
    from test_campaign_delivery_new import _campaign, _result, _script_ready
    task, prefix, ref = _script_ready(model_script)
    bad = _campaign(); bad["evidence"][0]["quote"] = "不存在的原文"
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": bad}),
                           _tool("submit_campaign", {"script_ref": ref, "campaign": _campaign()})])
    cli_json("resume", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    results = _result(view, "submit_campaign")
    assert results[0]["error"] == "invalid_campaign" and "无法在材料正文中找到" in str(results[0])
    assert results[1]["ok"] and len([x for x in view["outputs"] if x["kind"] == "campaign"]) == 1


def test_script_claim_without_supported_quote_is_rejected_without_charge(cli_env, model_script):
    from test_direct_script import SCRIPT
    task = _create()
    bad = copy.deepcopy(SCRIPT); bad["evidence"][0]["quote"] = "材料里没有这句话"
    model_script([_tool("search_materials", {"query": "自动卡点"}), _brief_tool(),
                  _tool("submit_script", {"brief_version": 1, "script": bad}),
                  _tool("stop_incomplete", {"reason": "脚本事实无支持"})])
    doc = cli_json("run", task["task_id"], "--max-rounds", "4")
    view = cli_json("inspect", task["task_id"])
    assert doc["status"] == "incomplete" and not view["outputs"]
    assert len(view["attempts"]) == 1 and view["resources"]["sim_spent_cents"] == 20
    rejection = next(json.loads(m["content"]) for m in view["messages"] if m["name"] == "submit_script")
    assert rejection["error"] == "invalid_script" and rejection["started"] is False


def test_run_after_draft_completes_without_regenerating_or_recharging_script(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    before = cli_json("inspect", task["task_id"])
    cli_json("review", task["task_id"])
    result = cli_json("save", task["task_id"])
    after = cli_json("inspect", task["task_id"])
    assert result["status"] == "complete" and result["save"]["output_version"] == 1
    assert before["resources"]["sim_spent_cents"] == 140 and result["resources"]["sim_spent_cents"] == 230
    assert len(after["scripts"]) == len(before["scripts"]) == 1 and after["generator_messages_count"] == 0


def test_open_direction_needs_no_fixed_candidates_or_independent_generation(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    view = cli_json("inspect", task["task_id"])
    campaign = next(x for x in view["outputs"] if x["kind"] == "campaign")
    assert campaign["content"]["strategy"]["name"] == "校园情境与教程结合"
    assert len(view["scripts"]) == 1 and view["generator_messages_count"] == 0
    assert len([a for a in view["attempts"] if a["kind"] == "submit_script"]) == 1


def test_malformed_alternatives_are_rejected_without_crashing(cli_env, model_script):
    from test_campaign_delivery_new import _campaign, _result, _script_ready
    task, prefix, ref = _script_ready(model_script)
    campaign = _campaign(); campaign["strategy"]["alternatives"] = ["tutorial"]
    model_script(prefix + [_tool("submit_campaign", {"script_ref": ref, "campaign": campaign}),
                           _tool("submit_campaign", {"script_ref": ref, "campaign": _campaign()})])
    cli_json("resume", task["task_id"], "--max-rounds", "2")
    view = cli_json("inspect", task["task_id"])
    rejected, accepted = _result(view, "submit_campaign")
    assert rejected["error"] == "invalid_campaign" and "alternatives[0]" in str(rejected)
    assert accepted["ok"] and len([x for x in view["outputs"] if x["kind"] == "campaign"]) == 1
