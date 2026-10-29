"""Cross-process recovery: verify unfinished actions before appending results.

Recovery never starts a new business attempt and never re-charges a started
attempt. It looks at the authoritative records (attempts, messages, briefs,
outputs, reviews, saves) and either appends an evidence-backed result for the
missing tool receipt or marks the call/attempt interrupted or unknown while
keeping the assistant/tool pairing legal.
"""

from __future__ import annotations

import json

from cta.config import Config, utc_now_iso
from cta.db import transaction
from cta.materials import MaterialCatalog, load_task_catalog
from cta.repository import (
    append_message,
    find_pending_tool_calls,
    finish_attempt,
    latest_brief,
    latest_output,
    latest_review,
    latest_save,
    list_strategy_selections,
    pending_todos,
    task_llm_usage,
    task_spent,
)

ATTEMPT_KINDS = {"search_materials": "search_materials", "generate_script": "generate_script"}


def recover_task(conn, task, config: Config, *, explicit: bool) -> dict:
    report = {
        "explicit_resume": explicit,
        "prior_status": task["status"],
        "prior_phase": task["phase"],
        "prior_reason": config.redact(task["reason"]),
        "pending_calls": 0,
        "recovered_calls": [],
        "interrupted_calls": [],
        "interrupted_attempts": [],
        "interrupted_requests": [],
        "save_unknown": None,
        "unpaused": False,
        "reused": {},
        "todos": [],
    }
    catalog = load_task_catalog(conn, task, config.project_root)
    pending = find_pending_tool_calls(conn, task["id"])
    report["pending_calls"] = len(pending)
    for call in pending:
        outcome = _recover_call(conn, task, call, catalog, config)
        with transaction(conn):
            if outcome["attempt_status"] is not None:
                finish_attempt(
                    conn,
                    outcome["attempt_id"],
                    status=outcome["attempt_status"],
                    error=outcome.get("attempt_error"),
                    meta={"recovered": outcome["category"] == "recovered"},
                )
            append_message(
                conn,
                task["id"],
                "tool",
                json.dumps(outcome["result"], ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["function"]["name"],
                attempt_id=outcome["attempt_id"],
                meta={"kind": "recovery_result", "category": outcome["category"]},
            )
        entry = {
            "tool": call["function"]["name"],
            "tool_call_id": call["id"],
            "attempt_id": outcome["attempt_id"],
            "outcome": outcome["category"],
        }
        if outcome["category"] == "recovered":
            report["recovered_calls"].append(entry)
        else:
            report["interrupted_calls"].append(entry)

    # Any still-running attempt that was not tied to a recovered call has no
    # verifiable result left behind; retain its cost/time as interrupted.
    running = conn.execute(
        "SELECT * FROM attempts WHERE task_id = ? AND status = 'running' ORDER BY rowid",
        (task["id"],),
    ).fetchall()
    for attempt in running:
        with transaction(conn):
            finish_attempt(
                conn,
                attempt["id"],
                status="interrupted",
                error="进程中断且未留下可核实的结果",
                meta={"recovered": True},
            )
        report["interrupted_attempts"].append(
            {"attempt_id": attempt["id"], "kind": attempt["kind"], "cost_cents": attempt["cost_cents"]}
        )

    report["save_unknown"] = _detect_unknown_save(conn, task, config)
    pending_requests = conn.execute(
        "SELECT id, purpose FROM llm_requests WHERE task_id = ? AND status = 'running'", (task["id"],)
    ).fetchall()
    with transaction(conn):
        for request in pending_requests:
            conn.execute(
                "UPDATE llm_requests SET status = 'interrupted', error = ? WHERE id = ?",
                ("请求已启动，进程中断前未收到完整响应；真实用量未知。", request["id"]),
            )
            report["interrupted_requests"].append(dict(request))
    report["reused"] = _reused_state(conn, task)
    report["todos"] = _todos(conn, task)
    return report


def recovery_has_events(report: dict) -> bool:
    return bool(
        report["pending_calls"]
        or report["interrupted_attempts"]
        or report["save_unknown"]
        or report["interrupted_requests"]
    )


def append_recovery_fact(conn, task, report: dict, *, unpaused: bool = False) -> dict:
    output = latest_output(conn, task["id"])
    review = latest_review(conn, task["id"])
    save = latest_save(conn, task["id"])
    usage = task_llm_usage(conn, task["id"])
    summary = {
        **report,
        "unpaused": unpaused or report.get("unpaused", False),
        "task_id": task["id"],
        "mode": task["mode"],
        "input_version": task["input_version"],
        "brief_version": int(task["brief_version"]),
        "output_version": None if output is None else output["version"],
        "review_id": None if review is None else review["review_id"],
        "save_status": None if save is None else save["status"],
        "save_verified": None if save is None else save["verified"],
        "sim_elapsed_ms": task["sim_elapsed_ms"],
        "sim_start": task["sim_start"],
        "used_rounds": task["used_rounds"],
        "max_rounds": task["max_rounds"],
        "spent_cents": task_spent(conn, task["id"]),
        "requests": usage["requests"],
        "reused": report["reused"],
        "todos": report["todos"],
        "instruction": (
            "这是新进程追加的恢复事实。只继续未完成的有界步骤；已恢复的有效检索、草稿和评审直接复用，"
            "不要重新执行或重新计费；原预算、模拟截止和已用轮数不变，续跑不获得新额度。"
        ),
    }
    with transaction(conn):
        append_message(
            conn,
            task["id"],
            "user",
            "[恢复事实] " + json.dumps(summary, ensure_ascii=False),
            meta={"kind": "recovery_fact", "summary": summary},
        )
    return summary


def _arguments(call) -> dict | None:
    try:
        parsed = json.loads(call.get("function", {}).get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _attempt_for(conn, task_id: str, call: dict, kind: str):
    rows = conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY rowid", (task_id,)).fetchall()
    for row in reversed(rows):
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        if meta.get("tool_call_id") == call["id"]:
            return row
    # Databases written before tool_call_id was persisted can still be recovered
    # when exactly one matching attempt was left running.
    for row in reversed(rows):
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        if row["kind"] == kind and row["status"] == "running" and not meta.get("tool_call_id"):
            return row
    return None


def _recover_call(conn, task, call: dict, catalog: MaterialCatalog, config: Config) -> dict:
    name = call.get("function", {}).get("name", "")
    parsed = _arguments(call)
    if parsed is None:
        return _outcome({"ok": False, "error": "invalid_arguments", "message": "工具参数不是合法 JSON；未产生业务动作。"}, category="interrupted")

    if name == "search_materials":
        return _recover_search(conn, task, call, parsed, catalog)
    if name == "read_material":
        material_id = parsed.get("material_id")
        if not isinstance(material_id, str) or not material_id:
            return _outcome({"ok": False, "error": "invalid_arguments", "message": "material_id 必须是非空字符串。"}, category="interrupted")
        result = {**catalog.read(material_id), "recovered": True}
        return _outcome(result, category="recovered" if result.get("ok") else "interrupted")
    if name == "estimate_cost":
        return _recover_estimate(conn, task, parsed)
    if name in ("record_disposition", "record_dispositions", "compare_strategies", "choose_strategy"):
        return _recover_local_action(conn, task, call, parsed)
    if name == "propose_brief":
        return _recover_brief(conn, task, call)
    if name == "propose_campaign":
        return _recover_campaign(conn, task, call)
    if name == "request_clarification":
        return _recover_clarification(conn, task, call)
    if name == "stop_incomplete":
        return _recover_stop(conn, task, call)
    if name == "generate_script":
        return _recover_generation(conn, task, call, parsed, catalog)
    if name == "review_campaign":
        output = latest_output(conn, task["id"])
        review = latest_review(conn, task["id"])
        if output is not None and review is not None and review["output_id"] == output["output_id"]:
            return _outcome({"ok": True, "review": review, "recovered": True}, category="recovered")
        return _outcome({
            "ok": False, "error": "review_interrupted", "recovered": True,
            "message": "上次核验没有完整权威结果；保留已发生消耗，仅可重新执行该有界步骤。",
        }, category="interrupted")
    if name == "save_campaign":
        from cta.save import reconcile_task

        save = latest_save(conn, task["id"])
        if save is not None and save["status"] == "failed":
            return _outcome({
                "ok": False, "error": "save_failed", "save": save, "recovered": True,
                "message": "保存已明确失败；保留原尝试与消耗，重试须由主 Agent 决定。",
            }, category="recovered")
        if save is not None:
            try:
                document = reconcile_task(config, task["id"])
            except OSError:
                document = {"save": save}
            saved = document.get("save")
            if saved is not None and saved["verified"]:
                return _outcome({"ok": True, "save": saved, "recovered": True}, category="recovered")
            return _outcome({
                "ok": False, "error": "save_unknown", "save": saved, "recovered": True,
                "message": "保存结果未核实，保持未知；不能盲目重放。",
            }, category="interrupted")
        return _outcome({
            "ok": False, "error": "save_interrupted", "recovered": True, "no_charge": True,
            "message": "保存开始前中断，没有保存记录；由主 Agent 决定下一步。",
        }, category="interrupted")
    return _outcome(
        {"ok": False, "error": "unknown_tool", "message": f"未知工具：{name}；本轮未执行任何业务动作。", "recovered": True},
        category="interrupted",
    )


def _outcome(result: dict, *, category: str, attempt_id=None, attempt_status=None, attempt_error=None) -> dict:
    return {
        "result": result,
        "category": category,
        "attempt_id": attempt_id,
        "attempt_status": attempt_status,
        "attempt_error": attempt_error,
    }


def _recover_local_action(conn, task, call: dict, parsed: dict) -> dict:
    name = call["function"]["name"]
    result = None
    if name in ("record_disposition", "record_dispositions"):
        decisions = [parsed] if name == "record_disposition" else parsed.get("decisions")
        review, output = latest_review(conn, task["id"]), latest_output(conn, task["id"])
        if review is not None and output is not None and review["output_id"] == output["output_id"] \
                and isinstance(decisions, list) and decisions:
            stored = {row["finding_id"]: dict(row) for row in conn.execute(
                "SELECT * FROM dispositions WHERE review_id = ? AND created_at >= ?",
                (review["review_id"], call["requested_at"]),
            )}
            if all(isinstance(item, dict) and isinstance(item.get("finding_id"), str)
                   and item["finding_id"] in stored and item.get("decision") == stored[item["finding_id"]]["decision"]
                   and isinstance(item.get("reason"), str)
                   and item["reason"].strip() == stored[item["finding_id"]]["reason"] for item in decisions):
                result = {"ok": True, "review_id": review["review_id"], "dispositions": decisions}
    elif name == "compare_strategies":
        rows = conn.execute(
            "SELECT * FROM estimates WHERE task_id = ? AND kind = 'strategy_comparison' AND created_at >= ? ORDER BY rowid DESC",
            (task["id"], call["requested_at"]),
        )
        for row in rows:
            if (row["search_mode"] == parsed.get("search_mode") and row["generation_mode"] == parsed.get("generation_mode")
                    and row["search_queries"] == parsed.get("search_queries")
                    and bool(row["include_revision"]) == parsed.get("include_revision", False)):
                detail = json.loads(row["detail_json"])
                if parsed.get("directions") is None or set(parsed["directions"]) == {d["direction"] for d in detail["directions"]}:
                    result = {**detail, "ok": True, "estimate_id": row["id"], "nature": "estimated"}
                    break
    else:
        for selection in reversed(list_strategy_selections(conn, task["id"])):
            if (selection["created_at"] >= call["requested_at"] and selection["direction"] == parsed.get("direction")
                    and selection["search_mode"] == parsed.get("search_mode")
                    and selection["generation_mode"] == parsed.get("generation_mode")
                    and (parsed.get("search_queries") is None or selection["search_queries"] == parsed["search_queries"])
                    and selection["include_revision"] == parsed.get("include_revision", False)
                    and selection["improvement"] == parsed.get("improvement", False)
                    and isinstance(parsed.get("rationale"), str) and selection["rationale"] == parsed["rationale"].strip()):
                result = {"ok": True, **selection, "selection_recorded": True}
                break
    if result is not None:
        return _outcome({**result, "recovered": True}, category="recovered")
    return _outcome({
        "ok": False, "error": "local_action_interrupted", "recovered": True,
        "message": "未找到与本请求完整匹配的权威结果；保留已有局部事实，不重放动作，由主 Agent 决定下一步。",
    }, category="interrupted")


def _recover_search(conn, task, call: dict, parsed: dict, catalog: MaterialCatalog) -> dict:
    attempt = _attempt_for(conn, task["id"], call, "search_materials")
    if attempt is None:
        return _outcome(
            {
                "ok": False,
                "error": "interrupted",
                "message": "上一次进程在检索开始前中断，未产生费用；请重新调用检索。",
                "recovered": True,
                "no_charge": True,
            },
            category="interrupted",
        )
    if attempt["status"] == "failed":
        return _outcome(
            {
                "ok": False,
                "error": "tool_failed",
                "message": f"检索执行失败：{attempt['error'] or '未知错误'}",
                "attempt_id": attempt["id"],
                "recovered": True,
            },
            category="interrupted",
            attempt_id=attempt["id"],
        )
    query = parsed.get("query")
    mode = parsed.get("mode", "basic")
    result = catalog.search(query, mode) if isinstance(query, str) and query.strip() else {
        "ok": False,
        "error": "invalid_arguments",
        "message": "query 必须是非空字符串。",
    }
    result = {
        **result,
        "attempt_id": attempt["id"],
        "cost_cents": attempt["cost_cents"],
        "duration_ms": attempt["duration_ms"],
        "recovered": True,
        "recovered_from": "attempt",
    }
    status = "success" if attempt["status"] == "running" else None
    return _outcome(result, category="recovered", attempt_id=attempt["id"], attempt_status=status)


def _recover_estimate(conn, task, parsed: dict) -> dict:
    from cta.loop import _estimate
    from cta.pricing import load_price_table

    try:
        price = load_price_table()
        result = _estimate(conn, task, parsed, price)
    except Exception as exc:  # noqa: BLE001 - recovery reports rather than crashes
        return _outcome(
            {"ok": False, "error": "interrupted", "message": f"估算请求中断且无法重建：{exc}", "recovered": True},
            category="interrupted",
        )
    return _outcome({**result, "recovered": True}, category="recovered" if result.get("ok") else "interrupted")


def _recover_brief(conn, task, call: dict) -> dict:
    brief = latest_brief(conn, task["id"])
    if brief is not None and brief["created_at"] >= call.get("requested_at", ""):
        return _outcome(
            {
                "ok": True,
                "brief_version": brief["version"],
                "message": "恢复核实：简报已写入权威记录；本调用无需重放。",
                "recovered": True,
            },
            category="recovered",
        )
    return _outcome(
        {
            "ok": False,
            "error": "interrupted",
            "message": "上一次进程在简报写入前中断，未形成简报；请重新提交简报。",
            "recovered": True,
            "no_charge": True,
        },
        category="interrupted",
    )


def _recover_campaign(conn, task, call: dict) -> dict:
    output = latest_output(conn, task["id"])
    if output is not None and output["created_at"] >= call.get("requested_at", ""):
        return _outcome(
            {
                "ok": True,
                "output_id": output["output_id"],
                "output_version": output["version"],
                "message": "恢复核实：完整方案草稿版本已写入权威记录；本调用无需重放。",
                "recovered": True,
            },
            category="recovered",
        )
    return _outcome(
        {
            "ok": False,
            "error": "interrupted",
            "message": "上一次进程在方案写入前中断，未形成新草稿；请重新提交完整方案。",
            "recovered": True,
            "no_charge": True,
        },
        category="interrupted",
    )


def _recover_clarification(conn, task, call: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM clarifications WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task["id"],)
    ).fetchone()
    if row is not None and row["created_at"] >= call.get("requested_at", ""):
        return _outcome(
            {"ok": True, "questions": json.loads(row["questions_json"]), "message": "恢复核实：澄清问题已保存。", "recovered": True},
            category="recovered",
        )
    return _outcome(
        {"ok": False, "error": "interrupted", "message": "上一次进程在澄清问题保存前中断；请重新提出。", "recovered": True},
        category="interrupted",
    )


def _recover_stop(conn, task, call: dict) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    if row["status"] == "incomplete" and row["phase"] == "stopped" and row["updated_at"] >= call.get("requested_at", ""):
        return _outcome(
            {"ok": True, "message": "恢复核实：任务停止状态已写入。", "recovered": True},
            category="recovered",
        )
    return _outcome(
        {"ok": False, "error": "interrupted", "message": "上一次进程在停止动作写入前中断；请重新决定是否停止。", "recovered": True},
        category="interrupted",
    )


def _recover_generation(conn, task, call: dict, parsed: dict, catalog: MaterialCatalog) -> dict:
    from cta.loop import _strip_json_fences, _validate_script

    attempt = _attempt_for(conn, task["id"], call, "generate_script")
    if attempt is None:
        return _outcome(
            {
                "ok": False,
                "error": "generation_interrupted",
                "message": "上一次进程在生成开始前中断，未产生费用；可重新执行该有界步骤。",
                "retry": True,
                "recovered": True,
                "no_charge": True,
            },
            category="interrupted",
        )
    if attempt["status"] == "failed":
        return _outcome(
            {
                "ok": False,
                "error": "generation_failed",
                "message": f"上一次生成执行失败：{attempt['error'] or '未知错误'}",
                "attempt_id": attempt["id"],
                "retry": True,
                "recovered": True,
            },
            category="interrupted",
            attempt_id=attempt["id"],
        )

    meta = json.loads(attempt["meta_json"] or "{}")
    strategy = meta.get("strategy") or parsed.get("strategy")
    rows = conn.execute(
        "SELECT * FROM messages WHERE task_id = ? AND agent = 'generator' AND attempt_id = ? ORDER BY seq",
        (task["id"], attempt["id"]),
    ).fetchall()
    candidates = []
    errors = []
    responses = 0
    for row in rows:
        try:
            message_meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            message_meta = {}
        if row["role"] != "assistant" or message_meta.get("kind") != "generation_response":
            continue
        responses += 1
        try:
            candidate = json.loads(_strip_json_fences(row["content"] or ""))
        except json.JSONDecodeError:
            errors.append(f"候选 {message_meta.get('candidate', responses)} 输出不是合法 JSON")
            continue
        normalized, candidate_errors = _validate_script(candidate, strategy, catalog)
        if candidate_errors:
            errors.extend(candidate_errors[:3])
            continue
        candidates.append(normalized)

    if candidates:
        best = max(candidates, key=lambda item: len(item.get("segments", [])))
        result = {
            "ok": True,
            "strategy": strategy,
            "script": best,
            "attempt_id": attempt["id"],
            "cost_cents": attempt["cost_cents"],
            "internal_requests": meta.get("internal_requests") or responses,
            "candidate_count": len(candidates),
            "recovered": True,
            "message": "恢复核实：已保存的生成候选完整有效，直接复用；未重新调用生成模型。",
        }
        status = "success" if attempt["status"] == "running" else None
        return _outcome(result, category="recovered", attempt_id=attempt["id"], attempt_status=status)

    result = {
        "ok": False,
        "error": "generation_interrupted",
        "message": "上一次生成未得到完整有效结果；保留原尝试与消耗，只重新执行生成这一有界步骤。",
        "details": errors[:6] or ["没有可核实的完整候选"],
        "attempt_id": attempt["id"],
        "traceable_outputs": responses,
        "retry": True,
        "recovered": True,
    }
    status = "interrupted" if attempt["status"] in ("running", "success") else None
    return _outcome(
        result,
        category="interrupted",
        attempt_id=attempt["id"],
        attempt_status=status,
        attempt_error="恢复时未能从已保存记录核实完整有效候选",
    )


def _detect_unknown_save(conn, task, config: Config) -> dict | None:
    output = latest_output(conn, task["id"])
    save = latest_save(conn, task["id"])
    if output is None or save is None:
        return None
    if save["output_id"] != output["output_id"] or save["verified"] or save["status"] == "failed":
        return None
    from cta.save import reconcile_task

    try:
        document = reconcile_task(config, task["id"])
        if document.get("save", {}).get("verified"):
            return None
    except OSError:
        # Expected external file access failures preserve the unknown record.
        pass
    if save["status"] != "unknown":
        with transaction(conn):
            conn.execute(
                "UPDATE saves SET status = 'unknown', error = ? WHERE id = ?",
                ("恢复时无法核实保存结果；保持未知，未盲目重放。", save["save_id"]),
            )
    with transaction(conn):
        conn.execute(
            "UPDATE tasks SET status = 'incomplete', phase = 'save_unknown', reason = ?, updated_at = ? WHERE id = ?",
            (
                f"保存结果未知：产物 v{save['output_version']} 的保存回执缺失且文件无法核实；保持未知并安全停止。",
                utc_now_iso(),
                task["id"],
            ),
        )
    return latest_save(conn, task["id"])


def _reused_state(conn, task) -> dict:
    output = latest_output(conn, task["id"])
    review = latest_review(conn, task["id"])
    save = latest_save(conn, task["id"])
    knowledge = [
        {"knowledge_id": row["knowledge_id"], "version": row["version"], "sha256": row["sha256"]}
        for row in conn.execute(
            "SELECT * FROM knowledge_loads WHERE task_id = ? ORDER BY id", (task["id"],)
        )
    ]
    material_count = conn.execute(
        "SELECT COUNT(*) AS n FROM material_snapshots WHERE task_id = ?", (task["id"],)
    ).fetchone()["n"]
    search_rows = conn.execute(
        "SELECT content FROM messages WHERE task_id = ? AND role = 'tool' AND name = 'search_materials'",
        (task["id"],),
    ).fetchall()
    searches = 0
    for row in search_rows:
        try:
            if json.loads(row["content"] or "{}").get("ok") is True:
                searches += 1
        except json.JSONDecodeError:
            continue
    attempts = conn.execute(
        "SELECT COUNT(*) AS n FROM attempts WHERE task_id = ?", (task["id"],)
    ).fetchone()["n"]
    return {
        "input_version": task["input_version"],
        "brief_version": int(task["brief_version"]),
        "output_version": None if output is None else output["version"],
        "review": None
        if review is None
        else {"review_id": review["review_id"], "status": review["status"], "verdict": review["verdict"]},
        "save": None if save is None else {"save_id": save["save_id"], "status": save["status"], "verified": save["verified"]},
        "knowledge": knowledge,
        "materials": {"version": task["materials_version"], "count": int(material_count)},
        "search_results": int(searches),
        "attempts": int(attempts),
        "spent_cents": task_spent(conn, task["id"]),
    }


def _todos(conn, task) -> list[dict]:
    output = latest_output(conn, task["id"])
    review = latest_review(conn, task["id"])
    save = latest_save(conn, task["id"])
    return pending_todos(task, output, review, save)
