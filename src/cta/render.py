"""Human-readable rendering from authoritative campaign JSON."""

from __future__ import annotations

from html import escape

DRAFT_NOTICE = "> 草稿：尚未完成内容核验与保存核实，不能视为最终交付。"
VERIFIED_NOTICE = "> 本版本已完成内容核验与保存核实；后续制作素材仍按下文状态处理。"

def _escape_fields(value):
    if isinstance(value, str):
        text = escape(value, quote=False)
        for char in "｜；：（）「」,\r\n":
            text = text.replace(char, f"&#{ord(char)};")
        return text
    if isinstance(value, list):
        return [_escape_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _escape_fields(item) for key, item in value.items()}
    return value


def _escape_evidence_id(value):
    if value == "无":
        return "&#26080;"
    return "".join(f"&#{ord(char)};" if char in "[]" or char.isspace() else char for char in value)


def campaign_markdown(campaign: dict) -> str:
    campaign = _escape_fields(campaign)
    strategy = campaign.get("strategy", {})
    activity = campaign.get("activity", {})
    script = campaign.get("script", {})
    lines = [
        f"# {activity.get('theme', '内容活动方案')}",
        "",
        DRAFT_NOTICE,
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
        if not isinstance(alternative, dict):
            continue
        lines.append(f"- 未选 {alternative.get('name', '')}：{alternative.get('why_not', '')}")
    lines += ["", "## 内容安排", ""]
    for index, item in enumerate(activity.get("content_items", []), start=1):
        lines.append(
            f"{index}. {item.get('title', '')}｜{item.get('purpose', '')}｜"
            f"{item.get('duration_s', '')} 秒｜{item.get('platform', '')}"
        )
    lines += ["", f"## 主视频脚本（{script.get('duration_seconds', '')} 秒）", ""]
    lines += [f"脚本标题：{script.get('title', '')}", f"脚本方向：{script.get('direction', '')}", ""]
    for segment in script.get("segments", []):
        lines += [
            f"### {segment.get('segment_id', '')}. {str(segment.get('duration_seconds', '')) + ' 秒'}",
            "",
            f"- 画面：{segment.get('visual', '')}",
            f"- 音频：{segment.get('voiceover', '')}",
            f"- 字幕：{segment.get('on_screen_text', '')}",
            f"- 证据：{', '.join(_escape_evidence_id(item) for item in segment.get('evidence_ids', [])) or '无'}",
            "",
        ]
    lines += ["### 脚本证据", ""]
    for evidence in script.get("evidence", []):
        lines.append(
            f"- [{_escape_evidence_id(evidence.get('id', ''))}] {evidence.get('claim', '').replace(']', '&#93;')}；依据 {evidence.get('material_id', '')}"
            f"（版本 {evidence.get('material_version', '')}）「{evidence.get('quote', '')}」；范围：{evidence.get('scope', '')}；用途：{evidence.get('use', '')}"
        )
    if not script.get("evidence"):
        lines.append("- 无")
    lines += ["", "## 素材用途", ""]
    status_labels = {"existing": "已有", "to_acquire": "待获取", "to_create": "待制作"}
    for material in campaign.get("materials", []):
        lines.append(
            f"- {material.get('name', '')}（{status_labels.get(material.get('status'), material.get('status'))}）："
            f"{material.get('use', '')}；位置：{material.get('position', '')}；权利/状态：{material.get('rights_note', '')}；素材ID：{material.get('material_id', '')}"
        )
    lines += ["", "## 制作步骤", ""]
    for index, step in enumerate(campaign.get("production_steps", []), start=1):
        lines.append(
            f"{index}. {step.get('step', '')}｜{step.get('owner', '')}｜{step.get('timing', '')}｜{step.get('note', '')}"
        )
    lines += ["", "## 证据关联", ""]
    for evidence in campaign.get("evidence", []):
        lines.append(
            f"- [{_escape_evidence_id(evidence.get('id', ''))}] {evidence.get('claim', '').replace(']', '&#93;')}；依据 {evidence.get('material_id', '')}"
            f"（版本 {evidence.get('material_version', '')}）「{evidence.get('quote', '')}」；范围：{evidence.get('scope', '')}；用途：{evidence.get('use', '')}"
        )
    if not campaign.get("evidence"):
        lines.append("- 无")
    lines += ["", "## 必要待确认项", ""]
    if campaign.get("open_questions"):
        for question in campaign["open_questions"]:
            blocking = "关键阻塞" if question.get("blocking") else "待确认"
            lines.append(f"- [{blocking}] {question.get('question', '')}；影响：{question.get('impact', '')}")
    else:
        lines.append("- 无")
    return "\n".join(lines).rstrip() + "\n"
