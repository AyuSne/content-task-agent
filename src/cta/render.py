"""Human-readable rendering from authoritative campaign JSON."""

from __future__ import annotations


def campaign_markdown(campaign: dict) -> str:
    strategy = campaign.get("strategy", {})
    activity = campaign.get("activity", {})
    script = campaign.get("script", {})
    lines = [
        f"# {activity.get('theme', '内容活动方案')}",
        "",
        "> 草稿：尚未完成内容核验与保存核实，不能视为最终交付。",
        "",
        "## 活动概述",
        "",
        f"- 目标：{activity.get('goal', '')}",
        f"- 受众：{activity.get('audience', '')}",
        f"- 形式：{activity.get('format', '')}",
        f"- 主题：{activity.get('theme', '')}",
        "",
        "## 创作策略",
        "",
        f"- 选定：{strategy.get('name', '')}",
        f"- 理由：{strategy.get('rationale', '')}",
    ]
    for alternative in strategy.get("alternatives", []):
        lines.append(f"- 未选 {alternative.get('name', '')}：{alternative.get('why_not', '')}")
    lines += ["", "## 内容安排", ""]
    for index, item in enumerate(activity.get("content_items", []), start=1):
        lines.append(
            f"{index}. {item.get('title', '')}｜{item.get('purpose', '')}｜"
            f"{item.get('duration_s', '')} 秒｜{item.get('platform', '')}"
        )
    lines += ["", f"## 主视频脚本（{script.get('duration_s', '')} 秒）", ""]
    for segment in script.get("segments", []):
        lines += [
            f"### {segment.get('index', '')}. {segment.get('time_range', '')}",
            "",
            f"- 画面：{segment.get('visual', '')}",
            f"- 音频：{segment.get('audio', '')}",
            f"- 字幕：{segment.get('subtitle', '')}",
            f"- 素材：{', '.join(segment.get('material_ids', [])) or '无'}",
            f"- 证据：{', '.join(segment.get('evidence_ids', [])) or '无'}",
            "",
        ]
    lines += ["## 素材用途", ""]
    for material in campaign.get("materials", []):
        lines.append(
            f"- {material.get('name', '')}（{material.get('status', '')}）：{material.get('use', '')}；"
            f"位置：{material.get('position', '')}；权利/状态：{material.get('rights_note', '')}"
        )
    lines += ["", "## 制作步骤", ""]
    for index, step in enumerate(campaign.get("production_steps", []), start=1):
        lines.append(
            f"{index}. {step.get('step', '')}｜{step.get('owner', '')}｜{step.get('timing', '')}｜{step.get('note', '')}"
        )
    lines += ["", "## 证据关联", ""]
    for evidence in campaign.get("evidence", []):
        lines.append(
            f"- [{evidence.get('id', '')}] {evidence.get('claim', '')}；依据 {evidence.get('material_id', '')}"
            f"「{evidence.get('quote', '')}」；范围：{evidence.get('scope', '')}；用途：{evidence.get('use', '')}"
        )
    lines += ["", "## 必要待确认项", ""]
    if campaign.get("open_questions"):
        for question in campaign["open_questions"]:
            blocking = "关键阻塞" if question.get("blocking") else "待确认"
            lines.append(f"- [{blocking}] {question.get('question', '')}；影响：{question.get('impact', '')}")
    else:
        lines.append("- 无")
    return "\n".join(lines).rstrip() + "\n"
