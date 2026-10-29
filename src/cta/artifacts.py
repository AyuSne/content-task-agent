"""Receive and version complete scripts under the task's resource contract."""

from __future__ import annotations

from cta.db import transaction
from cta.faults import crash
from cta.repository import create_output, finish_attempt, start_attempt


def _script_errors(script, catalog):
    if not isinstance(script, dict):
        return ["script 必须是对象"]
    errors = []
    if set(script) - {"title", "direction", "duration_seconds", "segments", "evidence"}:
        errors.append("script 包含未知字段")
    for field in ("title", "direction"):
        if not isinstance(script.get(field), str) or not script[field].strip():
            errors.append(f"script.{field} 必须是非空字符串")
    duration = script.get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        errors.append("script.duration_seconds 必须是正整数")
    evidence = script.get("evidence")
    ids = set()
    if not isinstance(evidence, list):
        errors.append("script.evidence 必须是数组")
        evidence = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"script.evidence[{index}] 必须是对象")
            continue
        if set(item) - {"id", "claim", "material_id", "material_version", "quote", "scope", "use"}:
            errors.append(f"script.evidence[{index}] 包含未知字段")
        for field in ("id", "claim", "material_id", "material_version", "quote", "scope", "use"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"script.evidence[{index}].{field} 必须非空")
        identifier = item.get("id")
        if isinstance(identifier, str):
            if identifier in ids:
                errors.append(f"script.evidence[{index}].id 重复")
            ids.add(identifier)
        material = catalog.get(item.get("material_id")) if isinstance(item.get("material_id"), str) else None
        if material is None or item.get("material_version") != catalog.version:
            errors.append(f"script.evidence[{index}] 材料或版本不属于本任务")
        elif not isinstance(item.get("quote"), str) or not item["quote"].strip() or " ".join(item["quote"].split()) not in " ".join(material.text.split()):
            errors.append(f"script.evidence[{index}].quote 无法在材料正文中找到")
    segments = script.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("script.segments 必须是非空数组")
        segments = []
    segment_ids = set()
    total = 0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"script.segments[{index}] 必须是对象")
            continue
        if set(segment) - {"segment_id", "duration_seconds", "visual", "voiceover", "on_screen_text", "evidence_ids"}:
            errors.append(f"script.segments[{index}] 包含未知字段")
        for field in ("segment_id", "visual"):
            if not isinstance(segment.get(field), str) or not segment[field].strip():
                errors.append(f"script.segments[{index}].{field} 必须非空")
        for field in ("voiceover", "on_screen_text"):
            if not isinstance(segment.get(field), str):
                errors.append(f"script.segments[{index}].{field} 必须是字符串")
        if not any(isinstance(segment.get(field), str) and segment[field].strip() for field in ("voiceover", "on_screen_text")):
            errors.append(f"script.segments[{index}] 至少需要旁白或字幕")
        segment_id = segment.get("segment_id")
        if isinstance(segment_id, str):
            if segment_id in segment_ids:
                errors.append(f"script.segments[{index}].segment_id 重复")
            segment_ids.add(segment_id)
        seconds = segment.get("duration_seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            errors.append(f"script.segments[{index}].duration_seconds 必须是正整数")
        else:
            total += seconds
        refs = segment.get("evidence_ids")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in ids for ref in refs):
            errors.append(f"script.segments[{index}].evidence_ids 必须引用脚本内证据")
    if isinstance(duration, int) and not isinstance(duration, bool) and total != duration:
        errors.append("脚本段落时长之和必须等于总时长")
    return errors


def submit_script(conn, task, arguments, price, catalog, *, tool_call_id, guard):
    errors = []
    if set(arguments) - {"brief_version", "script", "revises_script_ref"}:
        errors.append("包含未知参数")
    version = arguments.get("brief_version")
    brief = None
    if isinstance(version, int) and not isinstance(version, bool):
        brief = conn.execute("SELECT * FROM briefs WHERE task_id = ? AND version = ?", (task["id"], version)).fetchone()
    if brief is None or brief["input_version"] != task["input_version"]:
        errors.append("brief_version 必须引用当前输入版本的本任务简报")
    previous = arguments.get("revises_script_ref")
    if previous is not None:
        if (not isinstance(previous, dict) or set(previous) != {"id", "version"}
                or not isinstance(previous.get("id"), str) or not previous["id"]
                or not isinstance(previous.get("version"), int) or isinstance(previous["version"], bool)):
            errors.append("revises_script_ref 必须包含 id 和 version")
        else:
            row = conn.execute("SELECT * FROM outputs WHERE task_id = ? AND kind = 'script' AND id = ? AND version = ?",
                               (task["id"], previous["id"], previous["version"])).fetchone()
            if row is None or row["input_version"] != task["input_version"]:
                errors.append("revises_script_ref 必须引用当前输入的本任务脚本")
    errors.extend(_script_errors(arguments.get("script"), catalog))
    if errors:
        return {"ok": False, "error": "invalid_script", "details": errors, "started": False, "charged": False}
    mode = "revision" if previous is not None else "submit_script"
    spec = price.mode(mode)
    refusal = guard(conn, task, spec.cost_cents, spec.duration_ms)
    if refusal is not None:
        return {**refusal, "started": False, "charged": False}
    with transaction(conn):
        attempt_id = start_attempt(conn, task=task, kind="submit_script", mode=mode,
                                   cost_cents=spec.cost_cents, duration_ms=spec.duration_ms,
                                   pricing_version=price.version, meta={"tool_call_id": tool_call_id})
    crash("script_after_start")
    with transaction(conn):
        output = create_output(conn, task_id=task["id"], kind="script",
                               content={"script": arguments["script"], "brief_version": version,
                                        "revises_script_ref": previous}, content_md=None,
                               input_version=task["input_version"], attempt_id=attempt_id)
        finish_attempt(conn, attempt_id, status="success")
    return {"ok": True, "script_ref": {"id": output["output_id"], "version": output["version"]},
            "brief_version": version, "attempt_id": attempt_id, "started": True,
            "consumption": {"pricing_version": price.version, "cost_cents": spec.cost_cents,
                            "duration_ms": spec.duration_ms},
            "message": "完整脚本已接收；尚未核验或保存完整方案。"}
