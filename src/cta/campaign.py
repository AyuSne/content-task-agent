"""Accept a complete campaign by binding it to a saved script snapshot."""

import json

from cta.config import utc_now_iso
from cta.repository import create_output, input_version_applies


def submit_campaign(conn, task, args, catalog):
    errors = []
    ref = args.get("script_ref")
    script_row = None
    if (
        isinstance(ref, dict)
        and set(ref) == {"id", "version"}
        and isinstance(ref["id"], str)
        and isinstance(ref["version"], int)
        and not isinstance(ref["version"], bool)
    ):
        script_row = conn.execute(
            "SELECT * FROM outputs WHERE task_id = ? AND kind = 'script' AND id = ? AND version = ?",
            (task["id"], ref["id"], ref["version"]),
        ).fetchone()
    if script_row is None or not input_version_applies(conn, task, script_row["input_version"]):
        errors.append("script_ref 必须引用本任务适用于当前输入的脚本版本")
    campaign = args.get("campaign")
    if set(args) != {"script_ref", "campaign"} or not isinstance(campaign, dict):
        return {"ok": False, "error": "invalid_campaign", "details": ["必须提供 script_ref 和 campaign"]}
    allowed = {"strategy", "activity", "materials", "production_steps", "evidence", "open_questions"}
    if set(campaign) != allowed:
        errors.append("campaign 必须包含策略、活动、素材、制作步骤、证据和待确认项，不能重复提交脚本")

    def fields(obj, keys, path):
        if not isinstance(obj, dict):
            errors.append(f"{path} 必须是对象")
            return
        for key in keys:
            if not isinstance(obj.get(key), str) or not obj[key].strip():
                errors.append(f"{path}.{key} 必须是非空字符串")

    strategy = campaign.get("strategy")
    fields(strategy, ("name", "rationale"), "strategy")
    if isinstance(strategy, dict):
        alternatives = strategy.get("alternatives", [])
        if not isinstance(alternatives, list):
            errors.append("strategy.alternatives 必须是数组")
        else:
            for i, item in enumerate(alternatives):
                fields(item, ("name", "why_not"), f"strategy.alternatives[{i}]")
    activity = campaign.get("activity")
    fields(activity, ("goal", "audience", "theme", "format"), "activity")
    items = activity.get("content_items") if isinstance(activity, dict) else None
    if not isinstance(items, list) or not items:
        errors.append("activity.content_items 至少需要一项")
    else:
        for i, item in enumerate(items):
            fields(item, ("title", "purpose", "platform"), f"activity.content_items[{i}]")
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("duration_s"), int)
                or isinstance(item.get("duration_s"), bool)
                or item["duration_s"] <= 0
            ):
                errors.append(f"activity.content_items[{i}].duration_s 必须为正整数")
    materials = campaign.get("materials")
    if not isinstance(materials, list) or not materials:
        errors.append("materials 至少需要一项")
    else:
        for i, item in enumerate(materials):
            fields(item, ("name", "use", "position", "rights_note"), f"materials[{i}]")
            if not isinstance(item, dict):
                continue
            if item.get("status") not in ("existing", "to_acquire", "to_create"):
                errors.append(f"materials[{i}].status 无效")
            mid = item.get("material_id", "")
            if not isinstance(mid, str) or (mid and catalog.get(mid) is None):
                errors.append(f"materials[{i}].material_id 不属于本任务")
    steps = campaign.get("production_steps")
    if not isinstance(steps, list) or not steps:
        errors.append("production_steps 至少需要一项")
    else:
        for i, item in enumerate(steps):
            fields(item, ("step", "owner", "timing", "note"), f"production_steps[{i}]")
    evidence = campaign.get("evidence")
    if not isinstance(evidence, list):
        errors.append("evidence 必须是数组")
    else:
        ids = set()
        for i, item in enumerate(evidence):
            fields(item, ("id", "claim", "material_id", "material_version", "quote", "scope", "use"), f"evidence[{i}]")
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            if isinstance(identifier, str):
                if identifier in ids:
                    errors.append(f"evidence[{i}].id 重复")
                ids.add(identifier)
            error = catalog.evidence_error(item.get("material_id"), item.get("material_version"),
                                           item.get("quote"), normalize_whitespace=True)
            if error == "material":
                errors.append(f"evidence[{i}] 材料或版本不属于本任务")
            elif error == "quote":
                errors.append(f"evidence[{i}].quote 无法在材料正文中找到")
    questions = campaign.get("open_questions")
    if not isinstance(questions, list):
        errors.append("open_questions 必须是数组")
    else:
        for i, item in enumerate(questions):
            fields(item, ("question", "impact"), f"open_questions[{i}]")
            if not isinstance(item, dict) or not isinstance(item.get("blocking"), bool):
                errors.append(f"open_questions[{i}].blocking 必须是布尔值")
    if errors:
        return {"ok": False, "error": "invalid_campaign", "details": errors, "message": "方案未接收，请修正上述问题。"}
    script = json.loads(script_row["content_json"])["script"]
    snapshot = {
        **campaign,
        "script_ref": ref,
        "script": script,
        "blocking_unknowns": sum(q["blocking"] for q in questions),
    }
    output = create_output(
        conn,
        task_id=task["id"],
        kind="campaign",
        content=snapshot,
        content_md=None,
        input_version=task["input_version"],
        attempt_id=None,
    )
    conn.execute(
        "UPDATE tasks SET status = 'in_progress', phase = 'draft_ready', reason = ?, updated_at = ? WHERE id = ?",
        ("完整方案已接收，等待独立核验。", utc_now_iso(), task["id"]),
    )
    return {
        "ok": True,
        "campaign_ref": {"id": output["output_id"], "version": output["version"]},
        "script_ref": ref,
        "message": "方案快照已保存，须核验后方可交付。",
    }


def campaign_brief(conn, task_id, output):
    """Read the brief actually referenced by the campaign's script snapshot."""
    ref = output["content"]["script_ref"]
    row = conn.execute(
        "SELECT content_json FROM outputs WHERE task_id = ? AND id = ? AND version = ? AND kind = 'script'",
        (task_id, ref["id"], ref["version"]),
    ).fetchone()
    brief_version = json.loads(row["content_json"])["brief_version"]
    brief = conn.execute(
        "SELECT version, content_json FROM briefs WHERE task_id = ? AND version = ?", (task_id, brief_version)
    ).fetchone()
    return {"version": brief["version"], "content": json.loads(brief["content_json"])}
