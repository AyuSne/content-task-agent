"""Receive and version complete scripts under the task's resource contract."""

from __future__ import annotations

import json

from cta.config import utc_now_iso
from cta.db import transaction
from cta.faults import crash
from cta.materials import MaterialCatalog
from cta.repository import create_output, finish_attempt, input_version_applies, start_attempt


def _store_brief(conn, task, brief):
    """Store a validated brief in the caller's transaction."""
    version = int(task["brief_version"]) + 1
    conn.execute(
        "INSERT INTO briefs (task_id, version, input_version, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (task["id"], version, task["input_version"], json.dumps(brief, ensure_ascii=False), utc_now_iso()),
    )
    conn.execute(
        "UPDATE tasks SET brief_version = ?, status = ?, phase = 'brief_ready', reason = ?, updated_at = ? WHERE id = ?",
        (version, "in_progress", "业务简报已形成，等待进入方案生成。", utc_now_iso(), task["id"]),
    )
    return version


def propose_brief(conn, task, arguments, catalog):
    brief, errors = validate_brief(arguments, catalog)
    if errors:
        return {"ok": False, "error": "invalid_brief", "message": "简报未通过校验。", "details": errors}, False
    version = _store_brief(conn, task, brief)
    return {"ok": True, "brief_version": version, "message": "业务简报已通过校验并保存；继续组织完整方案。"}, False


def validate_brief(brief: dict, catalog: MaterialCatalog) -> tuple[dict, list[str]]:
    errors: list[str] = []

    def text_field(name: str) -> str:
        value = brief.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{name} 必须是非空字符串")
            return ""
        return value.strip()

    goal = text_field("goal")
    audience = text_field("audience")
    normalized = {"goal": goal, "audience": audience}

    for key in ("product_facts", "creative_requirements", "assumptions", "suggestions", "unknowns", "sources"):
        if not isinstance(brief.get(key), list):
            errors.append(f"{key} 必须是数组")

    facts = []
    for index, fact in enumerate(brief.get("product_facts", []) if isinstance(brief.get("product_facts"), list) else []):
        if not isinstance(fact, dict):
            errors.append(f"product_facts[{index}] 必须是对象")
            continue
        material_id = fact.get("material_id")
        quote = fact.get("quote")
        claim = fact.get("claim")
        use = fact.get("use")
        material = catalog.get(material_id) if isinstance(material_id, str) else None
        if material is None:
            errors.append(f"product_facts[{index}].material_id 不在本任务材料中：{material_id}")
            continue
        if not isinstance(quote, str) or not quote.strip():
            errors.append(f"product_facts[{index}].quote 不能为空")
            continue
        if " ".join(quote.split()) not in " ".join(material.text.split()):
            errors.append(f"product_facts[{index}].quote 无法在 {material_id} 正文中找到")
            continue
        if not isinstance(claim, str) or not claim.strip():
            errors.append(f"product_facts[{index}].claim 不能为空")
            continue
        if not isinstance(use, str) or not use.strip():
            errors.append(f"product_facts[{index}].use 不能为空")
            continue
        scope = fact.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            errors.append(f"product_facts[{index}].scope 不能为空")
            continue
        facts.append(
            {
                "claim": claim.strip(),
                "material_id": material_id,
                "quote": quote.strip(),
                "scope": scope.strip(),
                "use": use.strip(),
            }
        )
    normalized["product_facts"] = facts

    for key in ("creative_requirements", "assumptions", "suggestions"):
        items = []
        for index, item in enumerate(brief.get(key, []) if isinstance(brief.get(key), list) else []):
            if not isinstance(item, dict):
                errors.append(f"{key}[{index}] 必须是对象")
                continue
            text = item.get("text")
            basis = item.get("basis") if key != "creative_requirements" else item.get("source")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{key}[{index}].text 不能为空")
                continue
            basis_field = "source" if key == "creative_requirements" else "basis"
            if not isinstance(basis, str) or not basis.strip():
                errors.append(f"{key}[{index}].{basis_field} 不能为空")
                continue
            items.append({"text": text.strip(), basis_field: basis.strip()})
        normalized[key] = items

    unknowns = []
    for index, item in enumerate(brief.get("unknowns", []) if isinstance(brief.get("unknowns"), list) else []):
        if not isinstance(item, dict):
            errors.append(f"unknowns[{index}] 必须是对象")
            continue
        question = item.get("question")
        impact = item.get("impact")
        if not isinstance(question, str) or not question.strip():
            errors.append(f"unknowns[{index}].question 不能为空")
            continue
        if not isinstance(impact, str) or not impact.strip():
            errors.append(f"unknowns[{index}].impact 不能为空")
            continue
        blocking = item.get("blocking")
        if not isinstance(blocking, bool):
            errors.append(f"unknowns[{index}].blocking 必须是布尔值")
            continue
        unknowns.append({"question": question.strip(), "impact": impact.strip(), "blocking": blocking})
    normalized["unknowns"] = unknowns

    sources = []
    for index, item in enumerate(brief.get("sources", []) if isinstance(brief.get("sources"), list) else []):
        if not isinstance(item, dict):
            errors.append(f"sources[{index}] 必须是对象")
            continue
        material_id = item.get("material_id")
        if not isinstance(material_id, str) or catalog.get(material_id) is None:
            errors.append(f"sources[{index}].material_id 不在本任务材料中")
            continue
        used_for = item.get("used_for")
        if not isinstance(used_for, str) or not used_for.strip():
            errors.append(f"sources[{index}].used_for 不能为空")
            continue
        sources.append({"material_id": material_id, "used_for": used_for.strip()})
    normalized["sources"] = sources
    normalized["blocking_unknowns"] = sum(1 for item in unknowns if item["blocking"])
    return normalized, errors


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


def submit_script(conn, task, arguments, price, catalog, *, tool_call_id, request_seq, guard):
    errors = []
    if set(arguments) - {"brief", "brief_version", "script", "revises_script_ref"}:
        errors.append("包含未知参数")
    has_brief = "brief" in arguments
    has_version = "brief_version" in arguments
    if has_brief == has_version:
        errors.append("必须且只能提供 brief 或 brief_version")
    version = arguments.get("brief_version")
    new_brief = None
    if has_brief:
        raw_brief = arguments["brief"]
        if not isinstance(raw_brief, dict):
            errors.append("brief 必须是对象")
        else:
            new_brief, brief_errors = validate_brief(raw_brief, catalog)
            errors.extend(f"brief.{error}" for error in brief_errors)
        if arguments.get("revises_script_ref") is not None or conn.execute(
            "SELECT 1 FROM outputs WHERE task_id = ? AND kind = 'script' LIMIT 1", (task["id"],)
        ).fetchone():
            errors.append("联合简报只允许在首次脚本提交时使用；后续脚本引用已有简报")
    if has_version:
        brief = None
        if isinstance(version, int) and not isinstance(version, bool):
            brief = conn.execute("SELECT * FROM briefs WHERE task_id = ? AND version = ?", (task["id"], version)).fetchone()
        if brief is None or not input_version_applies(conn, task, brief["input_version"]):
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
            if row is None or not input_version_applies(conn, task, row["input_version"]):
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
                                   pricing_version=price.version, meta={"tool_call_id": tool_call_id, "request_seq": request_seq})
    crash("script_after_start")
    with transaction(conn):
        if new_brief is not None:
            version = _store_brief(conn, task, new_brief)
            crash("joint_after_brief")
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
