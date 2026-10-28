"""The single main loop: one validated model action per round, then real feedback.

The loop never assumes the service returns one call. Unknown tools, invalid
arguments, multiple calls and truncated responses are rejected and returned to
the model; each round is counted and failures stay visible.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.knowledge import load_doc
from cta.materials import MaterialCatalog, load_catalog
from cta.model import ModelResponse, create_client
from cta.pricing import PriceTable, load_price_table
from cta.prompts import MAIN_SYSTEM_PROMPT, PROMPT_VERSION, TOOL_DEFINITIONS
from cta.repository import (
    api_messages,
    append_message,
    append_snapshot,
    find_pending_tool_calls,
    finish_attempt,
    record_llm_request,
    snapshot_state,
    start_attempt,
    task_spent,
)
from cta.service import (
    MAX_ROUNDS,
    STATUS_COMPLETE,
    STATUS_IN_PROGRESS,
    STATUS_INCOMPLETE,
    STATUS_NEEDS_INPUT,
    TaskNotFound,
    TaskService,
)

TOOL_NAMES = {item["function"]["name"] for item in TOOL_DEFINITIONS}
CHARGED_MODES = {"search_basic", "search_extended"}


def _task(conn, task_id: str):
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise TaskNotFound(task_id)
    return task


def _input_row(conn, task):
    return conn.execute(
        "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?",
        (task["id"], task["input_version"]),
    ).fetchone()


def run_task(config: Config, task_id: str, *, max_rounds: int | None = None) -> dict:
    price = load_price_table(config.project_root)
    catalog = load_catalog(config.project_root)
    run_info = {"rounds_used": 0, "stop_reason": None, "requests": 0}

    with open_store(config.db_path) as conn:
        task = _task(conn, task_id)
        if task["status"] == STATUS_NEEDS_INPUT:
            run_info["stop_reason"] = "needs_input"
            return _document(config, task_id, run_info)
        if task["status"] == STATUS_COMPLETE:
            run_info["stop_reason"] = "already_complete"
            return _document(config, task_id, run_info)
        if task["paused"]:
            run_info["stop_reason"] = "paused"
            return _document(config, task_id, run_info)
        if task["phase"] == "brief_ready":
            run_info["stop_reason"] = "brief_ready"
            return _document(config, task_id, run_info)

        _ensure_context(conn, task, config)
        _repair_interrupted_calls(conn, task)
        _snapshot(conn, task)

        try:
            client = create_client(config)
        except Exception as exc:  # noqa: BLE001 - model boundary
            _set_incomplete(conn, task, f"模型不可用：{config.redact(str(exc))}")
            run_info["stop_reason"] = "model_unavailable"
            return _document(config, task_id, run_info)

        while True:
            task = _task(conn, task_id)
            if task["status"] in (STATUS_COMPLETE, STATUS_INCOMPLETE):
                run_info["stop_reason"] = task["status"]
                break
            if task["used_rounds"] >= MAX_ROUNDS:
                _set_incomplete(conn, task, f"达到主循环轮数上限（{MAX_ROUNDS} 轮），保留现有成果。")
                run_info["stop_reason"] = "max_rounds"
                break
            if max_rounds is not None and run_info["rounds_used"] >= max_rounds:
                run_info["stop_reason"] = "injected_round_cap"
                break
            if _past_deadline(conn, task):
                _set_incomplete(conn, task, "模拟方案截止时间已到，保留现有成果。")
                run_info["stop_reason"] = "deadline"
                break

            round_index = int(task["used_rounds"])
            with transaction(conn):
                conn.execute(
                    "UPDATE tasks SET used_rounds = used_rounds + 1, last_run_at = ?, updated_at = ? WHERE id = ?",
                    (utc_now_iso(), utc_now_iso(), task_id),
                )
            run_info["rounds_used"] += 1
            run_info["requests"] += 1

            messages = api_messages(conn, task_id)
            started = datetime.now(UTC)
            try:
                response = client.complete(messages, TOOL_DEFINITIONS, cursor=round_index)
            except Exception as exc:  # noqa: BLE001 - boundary
                response = ModelResponse(error=config.redact(f"{type(exc).__name__}: {exc}")[:800], model=config.model)
            finished = datetime.now(UTC)

            with transaction(conn):
                record_llm_request(
                    conn,
                    task_id=task_id,
                    purpose=f"main_loop_round_{round_index + 1}",
                    response=response,
                    started_at=started,
                    finished_at=finished,
                    mock=response.mock,
                    meta={"prompt_version": PROMPT_VERSION, "round": round_index + 1},
                )

            if response.error:
                with transaction(conn):
                    append_message(conn, task_id, "user", f"[运行时反馈] 模型请求失败：{response.error}", meta={"kind": "runtime_feedback"})
                    _set_incomplete(conn, task, f"模型请求失败：{response.error}")
                run_info["stop_reason"] = "model_error"
                break

            outcome = _handle_response(conn, task, response, price, catalog, config)
            if outcome["terminal"]:
                run_info["stop_reason"] = outcome["stop_reason"]
                break

        task = _task(conn, task_id)
        if task["status"] == STATUS_IN_PROGRESS and run_info["stop_reason"] is None:
            run_info["stop_reason"] = "stopped"

    return _document(config, task_id, run_info)


def _document(config: Config, task_id: str, run_info: dict) -> dict:
    document = TaskService(config).status(task_id)
    document["run"] = run_info
    return document


def _ensure_context(conn, task, config: Config) -> None:
    with transaction(conn):
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main'", (task["id"],)
        ).fetchone()
        if int(existing["n"]) == 0:
            append_message(
                conn,
                task["id"],
                "system",
                MAIN_SYSTEM_PROMPT,
                meta={"kind": "system", "prompt_version": PROMPT_VERSION},
            )
            doc = load_doc("campaign-creation", config.project_root)
            conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_loads (task_id, agent, knowledge_id, version, sha256, content, loaded_at)
                VALUES (?, 'main', ?, ?, ?, ?, ?)
                """,
                (task["id"], doc.knowledge_id, doc.version, doc.sha256, doc.content, utc_now_iso()),
            )
            stored = conn.execute(
                "SELECT * FROM knowledge_loads WHERE task_id = ? AND agent = 'main' AND knowledge_id = ?",
                (task["id"], doc.knowledge_id),
            ).fetchone()
            append_message(
                conn,
                task["id"],
                "system",
                f"[工作知识] {stored['knowledge_id']} {stored['version']} sha256:{stored['sha256']}\n\n{stored['content']}",
                meta={"kind": "knowledge", "knowledge_id": stored["knowledge_id"], "version": stored["version"]},
            )
            input_row = _input_row(conn, task)
            task_input = {
                "task_id": task["id"],
                "mode": task["mode"],
                "input_version": input_row["version"],
                "topic": input_row["topic"],
                "budget_cents": input_row["budget_cents"],
                "deadline": input_row["deadline"],
                "audience": input_row["audience"],
                "requirements": json.loads(input_row["requirements_json"]),
                "sim_start": task["sim_start"],
            }
            append_message(
                conn,
                task["id"],
                "user",
                "[任务输入] " + json.dumps(task_input, ensure_ascii=False),
                meta={"kind": "task_input", "input_version": input_row["version"]},
            )


def _repair_interrupted_calls(conn, task) -> None:
    pending = find_pending_tool_calls(conn, task["id"])
    if not pending:
        return
    with transaction(conn):
        for call in pending:
            append_message(
                conn,
                task["id"],
                "tool",
                json.dumps(
                    {"ok": False, "error": "interrupted", "message": "上一次进程在该调用返回结果前中断。"},
                    ensure_ascii=False,
                ),
                tool_call_id=call["id"],
                name=call["function"]["name"],
                meta={"kind": "recovery"},
            )


def _snapshot(conn, task) -> None:
    with transaction(conn):
        append_snapshot(conn, task["id"], snapshot_state(conn, task["id"]))


def _set_incomplete(conn, task, reason: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE tasks SET status = ?, phase = 'stopped', reason = ?, updated_at = ? WHERE id = ?",
            (STATUS_INCOMPLETE, reason, utc_now_iso(), task["id"]),
        )


def _past_deadline(conn, task) -> bool:
    input_row = _input_row(conn, task)
    if input_row["deadline"] is None:
        return False
    deadline = datetime.fromisoformat(input_row["deadline"])
    sim_start = datetime.fromisoformat(task["sim_start"])
    elapsed = (deadline - sim_start).total_seconds() * 1000
    return task["sim_elapsed_ms"] >= elapsed


def _handle_response(conn, task, response: ModelResponse, price: PriceTable, catalog: MaterialCatalog, config: Config) -> dict:
    calls = list(response.tool_calls)
    if response.finish_reason == "length":
        with transaction(conn):
            append_message(
                conn,
                task["id"],
                "assistant",
                response.content,
                tool_calls=[call.as_openai() for call in calls] or None,
                meta={"kind": "truncated"},
            )
            for call in calls:
                append_message(
                    conn,
                    task["id"],
                    "tool",
                    json.dumps({"ok": False, "error": "truncated_response", "message": "响应被截断，本轮调用全部拒绝。"}, ensure_ascii=False),
                    tool_call_id=call.id,
                    name=call.name,
                    meta={"kind": "rejection"},
                )
            if not calls:
                append_message(conn, task["id"], "user", "[运行时反馈] 上一条响应被截断，没有形成可执行动作；请重新给出一个完整动作。", meta={"kind": "runtime_feedback"})
        return {"terminal": False}

    if not calls:
        with transaction(conn):
            append_message(
                conn,
                task["id"],
                "assistant",
                response.content,
                meta={"kind": "no_action"},
            )
            append_message(
                conn,
                task["id"],
                "user",
                "[运行时反馈] 本轮没有调用任何工具，未产生业务动作。请依据当前阶段选择一个工具调用。",
                meta={"kind": "runtime_feedback"},
            )
        return {"terminal": False}

    with transaction(conn):
        append_message(
            conn,
            task["id"],
            "assistant",
            response.content,
            tool_calls=[call.as_openai() for call in calls],
            meta={"kind": "tool_request"},
        )
        if len(calls) > 1:
            for call in calls:
                append_message(
                    conn,
                    task["id"],
                    "tool",
                    json.dumps(
                        {"ok": False, "error": "multiple_calls", "message": "每轮只允许一个工具调用；本轮全部拒绝，未执行任何业务动作。"},
                        ensure_ascii=False,
                    ),
                    tool_call_id=call.id,
                    name=call.name,
                    meta={"kind": "rejection"},
                )
            return {"terminal": False}

    call = calls[0]
    parsed, error = _parse_call(call)
    if error is not None:
        with transaction(conn):
            append_message(
                conn,
                task["id"],
                "tool",
                json.dumps({"ok": False, **error}, ensure_ascii=False),
                tool_call_id=call.id,
                name=call.name,
                meta={"kind": "rejection"},
            )
        return {"terminal": False}

    if call.name == "propose_brief":
        result, terminal = _propose_brief(conn, task, parsed, catalog)
    elif call.name == "request_clarification":
        result, terminal = _request_clarification(conn, task, parsed)
    elif call.name == "stop_incomplete":
        result, terminal = _stop_incomplete(conn, task, parsed)
    else:
        result = _execute_business_tool(conn, task, call.name, parsed, price, catalog, config)
        terminal = False

    with transaction(conn):
        append_message(
            conn,
            task["id"],
            "tool",
            json.dumps(result, ensure_ascii=False),
            tool_call_id=call.id,
            name=call.name,
            attempt_id=result.get("attempt_id"),
            meta={"kind": "tool_result"},
        )
        if terminal:
            append_snapshot(conn, task["id"], snapshot_state(conn, task["id"]))
    return {"terminal": terminal, "stop_reason": "brief_ready" if call.name == "propose_brief" and terminal else call.name}


def _parse_call(call) -> tuple[dict, dict | None]:
    if call.name not in TOOL_NAMES:
        return {}, {"error": "unknown_tool", "message": f"未知工具：{call.name}；本轮未执行任何业务动作。"}
    try:
        parsed = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {}, {"error": "invalid_arguments", "message": "工具参数不是合法 JSON；本轮未执行任何业务动作。"}
    if not isinstance(parsed, dict):
        return {}, {"error": "invalid_arguments", "message": "工具参数必须是 JSON 对象。"}
    return parsed, None


def _execute_business_tool(conn, task, name: str, parsed: dict, price: PriceTable, catalog: MaterialCatalog, config: Config) -> dict:
    if name == "search_materials":
        return _search(conn, task, parsed, price, catalog)
    if name == "read_material":
        material_id = parsed.get("material_id")
        if not isinstance(material_id, str) or not material_id:
            return {"ok": False, "error": "invalid_arguments", "message": "material_id 必须是非空字符串。"}
        return catalog.read(material_id)
    if name == "estimate_cost":
        return _estimate(conn, task, parsed, price)
    return {"ok": False, "error": "unknown_tool", "message": f"未知工具：{name}"}


def _search(conn, task, parsed: dict, price: PriceTable, catalog: MaterialCatalog) -> dict:
    query = parsed.get("query")
    mode = parsed.get("mode", "basic")
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "invalid_arguments", "message": "query 必须是非空字符串。"}
    if mode not in ("basic", "extended"):
        return {"ok": False, "error": "invalid_arguments", "message": "mode 只能是 basic 或 extended。"}
    mode_key = "search_basic" if mode == "basic" else "search_extended"
    spec = price.mode(mode_key)
    guard = _guard(conn, task, spec.cost_cents, spec.duration_ms)
    if guard is not None:
        return guard
    attempt_id = start_attempt(
        conn,
        task=task,
        kind="search_materials",
        mode=mode_key,
        cost_cents=spec.cost_cents,
        duration_ms=spec.duration_ms,
        pricing_version=price.version,
        meta={"query": query, "mode": mode},
    )
    conn.commit()
    try:
        result = catalog.search(query, mode)
    except Exception as exc:  # noqa: BLE001 - business tool boundary
        finish_attempt(conn, attempt_id, status="failed", error=str(exc)[:500])
        conn.commit()
        return {"ok": False, "error": "tool_failed", "message": f"检索执行失败：{exc}", "attempt_id": attempt_id}
    finish_attempt(conn, attempt_id, status="success")
    conn.commit()
    return {**result, "attempt_id": attempt_id, "cost_cents": spec.cost_cents, "duration_ms": spec.duration_ms}


def _estimate(conn, task, parsed: dict, price: PriceTable) -> dict:
    search_mode = parsed.get("search_mode", "search_basic")
    generation_mode = parsed.get("generation_mode", "generate_direct")
    include_revision = bool(parsed.get("include_revision", False))
    if search_mode not in ("search_basic", "search_extended"):
        return {"ok": False, "error": "invalid_arguments", "message": "search_mode 无效。"}
    if generation_mode not in ("generate_direct", "generate_multi"):
        return {"ok": False, "error": "invalid_arguments", "message": "generation_mode 无效。"}
    estimate = price.estimate(search_mode, generation_mode, include_revision=include_revision)
    input_row = _input_row(conn, task)
    remaining = None if input_row["budget_cents"] is None else input_row["budget_cents"] - task_spent(conn, task["id"])
    estimate["remaining_budget_cents"] = remaining
    estimate["affordable"] = remaining is None or estimate["total_cost_cents"] <= remaining
    return {"ok": True, "estimate": estimate}


def _guard(conn, task, cost_cents: int, duration_ms: int) -> dict | None:
    input_row = _input_row(conn, task)
    spent = task_spent(conn, task["id"])
    budget = input_row["budget_cents"]
    if budget is not None and spent + cost_cents > budget:
        return {
            "ok": False,
            "error": "budget_exceeded",
            "message": f"预算不足：已发生 {spent} 分，本次需要 {cost_cents} 分，预算 {budget} 分。请选择更省的路径或停止。",
            "spent_cents": spent,
            "requested_cost_cents": cost_cents,
            "budget_cents": budget,
        }
    if input_row["deadline"] is not None:
        deadline = datetime.fromisoformat(input_row["deadline"])
        sim_start = datetime.fromisoformat(task["sim_start"])
        allowed_ms = (deadline - sim_start).total_seconds() * 1000
        if task["sim_elapsed_ms"] + duration_ms > allowed_ms:
            return {
                "ok": False,
                "error": "deadline_exceeded",
                "message": f"模拟时限不足：当前已用 {task['sim_elapsed_ms']} ms，本次需要 {duration_ms} ms，截止前只剩 {int(allowed_ms - task['sim_elapsed_ms'])} ms。",
                "sim_elapsed_ms": task["sim_elapsed_ms"],
                "requested_duration_ms": duration_ms,
            }
    return None


def _propose_brief(conn, task, parsed: dict, catalog: MaterialCatalog) -> tuple[dict, bool]:
    brief, errors = _validate_brief(parsed, catalog)
    if errors:
        return {"ok": False, "error": "invalid_brief", "message": "简报未通过校验。", "details": errors}, False
    version = int(task["brief_version"]) + 1
    with transaction(conn):
        conn.execute(
            "INSERT INTO briefs (task_id, version, content_json, created_at) VALUES (?, ?, ?, ?)",
            (task["id"], version, json.dumps(brief, ensure_ascii=False), utc_now_iso()),
        )
        conn.execute(
            "UPDATE tasks SET brief_version = ?, status = ?, phase = 'brief_ready', reason = ?, updated_at = ? WHERE id = ?",
            (
                version,
                STATUS_IN_PROGRESS,
                "业务简报已形成，等待进入方案生成。",
                utc_now_iso(),
                task["id"],
            ),
        )
    return {"ok": True, "brief_version": version, "message": "业务简报已通过校验并保存；本阶段结束。"}, True


def _validate_brief(brief: dict, catalog: MaterialCatalog) -> tuple[dict, list[str]]:
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
        facts.append(
            {
                "claim": claim.strip(),
                "material_id": material_id,
                "quote": quote.strip(),
                "scope": (fact.get("scope") or material.scope).strip(),
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
            if key == "creative_requirements":
                items.append({"text": text.strip(), "source": str(basis or "用户输入").strip()})
            else:
                items.append({"text": text.strip(), "basis": str(basis or "未说明").strip()})
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
        unknowns.append({"question": question.strip(), "impact": impact.strip(), "blocking": bool(item.get("blocking", False))})
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
        sources.append({"material_id": material_id, "used_for": str(item.get("used_for", "未说明")).strip()})
    normalized["sources"] = sources
    normalized["blocking_unknowns"] = sum(1 for item in unknowns if item["blocking"])
    return normalized, errors


def _request_clarification(conn, task, parsed: dict) -> tuple[dict, bool]:
    questions = parsed.get("questions")
    if not isinstance(questions, list) or not questions:
        return {"ok": False, "error": "invalid_arguments", "message": "questions 必须是非空数组。"}, False
    normalized = []
    for index, item in enumerate(questions[:5]):
        if not isinstance(item, dict):
            return {"ok": False, "error": "invalid_arguments", "message": f"questions[{index}] 必须是对象。"}, False
        question = item.get("question")
        impact = item.get("impact")
        if not isinstance(question, str) or not question.strip() or not isinstance(impact, str) or not impact.strip():
            return {"ok": False, "error": "invalid_arguments", "message": f"questions[{index}] 需要 question 与 impact。"}, False
        normalized.append({"question": question.strip(), "impact": impact.strip(), "blocking": bool(item.get("blocking", False))})
    with transaction(conn):
        conn.execute(
            "INSERT INTO clarifications (task_id, questions_json, created_at) VALUES (?, ?, ?)",
            (task["id"], json.dumps(normalized, ensure_ascii=False), utc_now_iso()),
        )
        conn.execute(
            "UPDATE tasks SET status = ?, phase = 'awaiting_clarification', reason = ?, updated_at = ? WHERE id = ?",
            (
                STATUS_NEEDS_INPUT,
                f"需要用户澄清 {len(normalized)} 个问题（其中关键阻塞 {sum(1 for q in normalized if q['blocking'])} 个）。",
                utc_now_iso(),
                task["id"],
            ),
        )
    return {"ok": True, "questions": normalized, "message": "问题已保存，任务等待用户补充。"}, True


def _stop_incomplete(conn, task, parsed: dict) -> tuple[dict, bool]:
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return {"ok": False, "error": "invalid_arguments", "message": "reason 必须是非空字符串。"}, False
    with transaction(conn):
        conn.execute(
            "UPDATE tasks SET status = ?, phase = 'stopped', reason = ?, updated_at = ? WHERE id = ?",
            (STATUS_INCOMPLETE, reason.strip(), utc_now_iso(), task["id"]),
        )
    return {"ok": True, "message": "任务已停止并保留现有成果。"}, True
