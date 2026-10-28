"""The single main loop: one validated model action per round, then real feedback.

The loop never assumes the service returns one call. Unknown tools, invalid
arguments, multiple calls and truncated responses are rejected and returned to
the model; each round is counted and failures stay visible.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.knowledge import load_doc
from cta.materials import MaterialCatalog, load_catalog
from cta.model import ModelResponse, create_client
from cta.pricing import PriceTable, load_price_table
from cta.prompts import (
    GENERATION_PROMPT_VERSION,
    MAIN_SYSTEM_PROMPT,
    PROMPT_VERSION,
    SCRIPT_GENERATION_SYSTEM_PROMPT,
    TOOL_DEFINITIONS,
)
from cta.render import campaign_markdown
from cta.repository import (
    api_messages,
    append_message,
    append_snapshot,
    count_purpose_requests,
    create_output,
    find_pending_tool_calls,
    finish_attempt,
    latest_brief,
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
        _repair_interrupted_calls(conn, task)
        if task["status"] == STATUS_NEEDS_INPUT:
            run_info["stop_reason"] = "needs_input"
            return _document(config, task_id, run_info)
        if task["status"] == STATUS_COMPLETE:
            run_info["stop_reason"] = "already_complete"
            return _document(config, task_id, run_info)
        if task["paused"]:
            run_info["stop_reason"] = "paused"
            return _document(config, task_id, run_info)
        if task["phase"] == "draft_ready":
            run_info["stop_reason"] = "draft_ready"
            return _document(config, task_id, run_info)

        _ensure_context(conn, task, config)
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

            outcome = _handle_response(conn, task, response, price, catalog, config, client)
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
        if task["phase"] == "brief_ready":
            transition = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main' AND meta_json LIKE '%phase_transition%'",
                (task["id"],),
            ).fetchone()
            if int(transition["n"]) == 0:
                append_message(
                    conn,
                    task["id"],
                    "user",
                    "[阶段推进] 业务简报已保存。请比较创作方向，调用 generate_script 获得独立生成的脚本候选，"
                    "再整合活动安排、素材用途、制作步骤与证据，调用 propose_campaign 提交完整方案草稿。",
                    meta={"kind": "phase_transition", "from": "brief", "to": "campaign_draft"},
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


def _handle_response(conn, task, response: ModelResponse, price: PriceTable, catalog: MaterialCatalog, config: Config, client) -> dict:
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
    elif call.name == "propose_campaign":
        result, terminal = _propose_campaign(conn, task, parsed, catalog)
    elif call.name == "generate_script":
        result = _generate_script(conn, task, parsed, price, catalog, config, client)
        terminal = False
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
    stop_reason = {"propose_brief": "brief_ready", "propose_campaign": "draft_ready"}.get(call.name, call.name)
    return {"terminal": terminal, "stop_reason": stop_reason}


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
    if "search_mode" not in parsed or "generation_mode" not in parsed:
        return {"ok": False, "error": "invalid_arguments", "message": "search_mode 与 generation_mode 都是必填参数。"}
    search_mode = parsed.get("search_mode")
    generation_mode = parsed.get("generation_mode")
    include_revision = parsed.get("include_revision", False)
    if not isinstance(include_revision, bool):
        return {"ok": False, "error": "invalid_arguments", "message": "include_revision 必须是布尔值。"}
    if search_mode not in ("search_basic", "search_extended"):
        return {"ok": False, "error": "invalid_arguments", "message": "search_mode 无效。"}
    if generation_mode not in ("generate_direct", "generate_multi"):
        return {"ok": False, "error": "invalid_arguments", "message": "generation_mode 无效。"}
    estimate = price.estimate(search_mode, generation_mode, include_revision=include_revision)
    input_row = _input_row(conn, task)
    remaining = None if input_row["budget_cents"] is None else input_row["budget_cents"] - task_spent(conn, task["id"])
    sim_start = datetime.fromisoformat(task["sim_start"])
    projected = sim_start + timedelta(milliseconds=task["sim_elapsed_ms"] + estimate["total_duration_ms"])
    estimate["projected_sim_complete"] = projected.isoformat()
    estimate["deadline"] = input_row["deadline"]
    estimate["remaining_budget_cents"] = remaining
    estimate["budget_gap_cents"] = None if remaining is None else remaining - estimate["total_cost_cents"]
    estimate["affordable"] = remaining is None or estimate["total_cost_cents"] <= remaining
    if input_row["deadline"] is None:
        estimate["time_gap_ms"] = None
        estimate["within_deadline"] = None
    else:
        deadline = datetime.fromisoformat(input_row["deadline"])
        allowed_ms = (deadline - sim_start).total_seconds() * 1000
        estimate["time_gap_ms"] = int(allowed_ms - (task["sim_elapsed_ms"] + estimate["total_duration_ms"]))
        estimate["within_deadline"] = estimate["time_gap_ms"] >= 0
    return {"ok": True, "estimate": estimate}


def _guard_generation_path(conn, task, generation_mode: str, price: PriceTable) -> dict | None:
    spec = price.mode(generation_mode)
    review = price.mode("review")
    save = price.mode("save")
    cost = spec.cost_cents + review.cost_cents + save.cost_cents
    duration = spec.duration_ms + review.duration_ms + save.duration_ms
    input_row = _input_row(conn, task)
    spent = task_spent(conn, task["id"])
    remaining = None if input_row["budget_cents"] is None else input_row["budget_cents"] - spent
    estimate = {
        "generation": spec.as_dict(),
        "review": review.as_dict(),
        "save": save.as_dict(),
        "total_cost_cents": cost,
        "total_duration_ms": duration,
        "remaining_budget_cents": remaining,
        "notes": "生成前核对完整剩余路径；估算不是实际支出。",
    }
    if remaining is not None and cost > remaining:
        return {
            "ok": False,
            "error": "full_path_infeasible",
            "message": f"完整剩余路径需要 {cost} 分，但只剩 {remaining} 分；不能只做生成而无法核验保存。",
            "estimate": estimate,
        }
    if input_row["deadline"] is not None:
        deadline = datetime.fromisoformat(input_row["deadline"])
        sim_start = datetime.fromisoformat(task["sim_start"])
        allowed_ms = (deadline - sim_start).total_seconds() * 1000
        if task["sim_elapsed_ms"] + duration > allowed_ms:
            return {
                "ok": False,
                "error": "full_path_deadline_infeasible",
                "message": "完整剩余路径耗时超过模拟截止时间；不能只做生成而无法核验保存。",
                "estimate": estimate,
            }
    return None


def _generate_script(conn, task, parsed: dict, price: PriceTable, catalog: MaterialCatalog, config: Config, client) -> dict:
    strategy = parsed.get("strategy")
    generation_mode = parsed.get("generation_mode", "generate_direct")
    focus = parsed.get("focus", "")
    if strategy not in ("functional_demo", "user_scenario", "tutorial"):
        return {"ok": False, "error": "invalid_arguments", "message": "strategy 无效。"}
    if generation_mode not in ("generate_direct", "generate_multi"):
        return {"ok": False, "error": "invalid_arguments", "message": "generation_mode 无效。"}
    brief = latest_brief(conn, task["id"])
    if brief is None:
        return {"ok": False, "error": "brief_required", "message": "尚未保存业务简报，不能生成脚本。"}

    path_guard = _guard_generation_path(conn, task, generation_mode, price)
    if path_guard is not None:
        return path_guard
    spec = price.mode(generation_mode)
    guard = _guard(conn, task, spec.cost_cents, spec.duration_ms)
    if guard is not None:
        return guard

    attempt_id = start_attempt(
        conn,
        task=task,
        kind="generate_script",
        mode=generation_mode,
        cost_cents=spec.cost_cents,
        duration_ms=spec.duration_ms,
        pricing_version=price.version,
        meta={"strategy": strategy, "focus": focus},
    )
    conn.commit()

    knowledge_row = conn.execute(
        "SELECT content FROM knowledge_loads WHERE task_id = ? AND agent = 'main' AND knowledge_id = 'campaign-creation'",
        (task["id"],),
    ).fetchone()
    material_context = []
    referenced = {
        fact.get("material_id") for fact in brief["content"].get("product_facts", [])
    } | {source.get("material_id") for source in brief["content"].get("sources", [])}
    for material_id in sorted(item for item in referenced if isinstance(item, str)):
        material = catalog.get(material_id)
        if material is not None:
            material_context.append(material.as_dict())
    for material in catalog.materials.values():
        if material.kind == "asset" and material.id not in {item["material_id"] for item in material_context}:
            material_context.append(material.as_dict())

    request_payload = {
        "task": {
            "topic": _input_row(conn, task)["topic"],
            "audience": _input_row(conn, task)["audience"],
            "requirements": json.loads(_input_row(conn, task)["requirements_json"]),
        },
        "brief": brief["content"],
        "strategy": strategy,
        "focus": focus,
        "materials": material_context,
    }
    base_messages = [
        {"role": "system", "content": SCRIPT_GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "[脚本生成请求] "
            + json.dumps(
                {**request_payload, "working_knowledge": knowledge_row["content"] if knowledge_row else ""},
                ensure_ascii=False,
            ),
        },
    ]
    internal_requests = 1 if generation_mode == "generate_direct" else int(spec.max_internal_requests or 3)
    existing = count_purpose_requests(conn, task["id"], "script_generation")
    candidates = []
    errors = []
    for candidate_index in range(internal_requests):
        messages = list(base_messages)
        if internal_requests > 1:
            messages.append(
                {
                    "role": "user",
                    "content": f"请生成第 {candidate_index + 1} 个候选；可以调整开场或段落节奏，但必须遵守全部约束。只输出 JSON。",
                }
            )
        with transaction(conn):
            for message in messages:
                append_message(
                    conn,
                    task["id"],
                    message["role"],
                    message["content"],
                    agent="generator",
                    attempt_id=attempt_id,
                    meta={"kind": "generation_prompt", "candidate": candidate_index + 1},
                )
        started = datetime.now(UTC)
        try:
            response = client.complete(
                messages,
                [],
                cursor=existing + candidate_index,
                channel="generation",
                json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001 - boundary
            response = ModelResponse(error=config.redact(f"{type(exc).__name__}: {exc}")[:800])
        finished = datetime.now(UTC)
        with transaction(conn):
            record_llm_request(
                conn,
                task_id=task["id"],
                purpose="script_generation",
                response=response,
                started_at=started,
                finished_at=finished,
                mock=response.mock,
                attempt_id=attempt_id,
                meta={
                    "strategy": strategy,
                    "candidate": candidate_index + 1,
                    "context": "isolated_generation",
                    "prompt_version": GENERATION_PROMPT_VERSION,
                },
            )
            append_message(
                conn,
                task["id"],
                "assistant",
                response.content,
                agent="generator",
                attempt_id=attempt_id,
                meta={"kind": "generation_response", "candidate": candidate_index + 1},
            )
        if response.error:
            errors.append(response.error)
            continue
        if response.finish_reason == "length":
            errors.append("脚本生成响应被截断")
            continue
        try:
            candidate = json.loads(_strip_json_fences(response.content or ""))
        except json.JSONDecodeError:
            errors.append("脚本生成输出不是合法 JSON")
            continue
        normalized, candidate_errors = _validate_script(candidate, strategy, catalog)
        if candidate_errors:
            errors.extend(candidate_errors[:4])
            continue
        candidates.append(normalized)

    if not candidates:
        finish_attempt(
            conn,
            attempt_id,
            status="failed",
            error="; ".join(errors)[:800] or "no valid candidate",
            meta={"internal_requests": internal_requests, "candidates": 0},
        )
        conn.commit()
        return {
            "ok": False,
            "error": "generation_failed",
            "message": "脚本生成没有得到完整有效候选；已保留尝试与消耗。",
            "details": errors[:6],
            "attempt_id": attempt_id,
        }

    best = max(candidates, key=lambda item: len(item.get("segments", [])))
    finish_attempt(
        conn,
        attempt_id,
        status="success",
        meta={"internal_requests": internal_requests, "candidates": len(candidates)},
    )
    conn.commit()
    return {
        "ok": True,
        "strategy": strategy,
        "script": best,
        "attempt_id": attempt_id,
        "cost_cents": spec.cost_cents,
        "internal_requests": internal_requests,
        "candidate_count": len(candidates),
        "message": "脚本候选定稿完成；请把它整合进完整活动方案后调用 propose_campaign。",
    }


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _validate_script(candidate: dict, strategy: str, catalog: MaterialCatalog) -> tuple[dict, list[str]]:
    errors: list[str] = []
    if not isinstance(candidate, dict):
        return {}, ["脚本输出必须是 JSON 对象"]
    if candidate.get("strategy") != strategy:
        errors.append("脚本 strategy 必须与请求一致")
    duration = candidate.get("duration_s")
    if not isinstance(duration, int) or duration <= 0:
        errors.append("duration_s 必须是正整数")
    segments = candidate.get("segments")
    if not isinstance(segments, list) or len(segments) < 2:
        errors.append("segments 至少需要 2 段")
        segments = []
    normalized_segments = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segments[{index}] 必须是对象")
            continue
        for field in ("time_range", "visual", "audio", "subtitle"):
            if not isinstance(segment.get(field), str) or not segment.get(field, "").strip():
                errors.append(f"segments[{index}].{field} 不能为空")
        material_ids = segment.get("material_ids")
        if not isinstance(material_ids, list) or not all(isinstance(item, str) for item in material_ids):
            errors.append(f"segments[{index}].material_ids 必须是字符串数组")
            material_ids = []
        for material_id in material_ids:
            if catalog.get(material_id) is None:
                errors.append(f"segments[{index}].material_ids 含未知材料 {material_id}")
        evidence_ids = segment.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
            errors.append(f"segments[{index}].evidence_ids 必须是字符串数组")
            evidence_ids = []
        normalized_segments.append(
            {
                "index": segment.get("index", index + 1),
                "time_range": segment.get("time_range", ""),
                "visual": segment.get("visual", ""),
                "audio": segment.get("audio", ""),
                "subtitle": segment.get("subtitle", ""),
                "material_ids": material_ids,
                "evidence_ids": evidence_ids,
            }
        )
    claims = candidate.get("claims")
    if not isinstance(claims, list):
        errors.append("claims 必须是数组")
        claims = []
    normalized_claims = []
    claim_ids = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claims[{index}] 必须是对象")
            continue
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id.strip() or claim_id in claim_ids:
            errors.append(f"claims[{index}].id 必须唯一且非空")
            continue
        claim_ids.add(claim_id)
        material = catalog.get(claim.get("material_id")) if isinstance(claim.get("material_id"), str) else None
        if material is None:
            errors.append(f"claims[{index}].material_id 不在本任务材料中")
            continue
        quote = claim.get("quote")
        if not isinstance(quote, str) or " ".join(quote.split()) not in " ".join(material.text.split()):
            errors.append(f"claims[{index}].quote 无法在材料正文中找到")
            continue
        normalized_claims.append(
            {
                "id": claim_id,
                "claim": str(claim.get("claim", "")).strip(),
                "material_id": material.id,
                "quote": quote.strip(),
                "scope": str(claim.get("scope") or material.scope).strip(),
            }
        )
    for segment in normalized_segments:
        for evidence_id in segment["evidence_ids"]:
            if evidence_id not in claim_ids:
                errors.append(f"segment 证据 {evidence_id} 不在 claims 中")
    open_questions = candidate.get("open_questions", [])
    if not isinstance(open_questions, list):
        errors.append("open_questions 必须是数组")
        open_questions = []
    normalized = {
        "strategy": strategy,
        "duration_s": duration,
        "segments": normalized_segments,
        "claims": normalized_claims,
        "open_questions": open_questions,
    }
    return normalized, errors


def _propose_campaign(conn, task, parsed: dict, catalog: MaterialCatalog) -> tuple[dict, bool]:
    campaign, errors = _validate_campaign(parsed, catalog)
    if errors:
        return {"ok": False, "error": "invalid_campaign", "message": "完整方案未通过校验。", "details": errors[:12]}, False
    with transaction(conn):
        output = create_output(
            conn,
            task_id=task["id"],
            kind="campaign",
            content=campaign,
            content_md=campaign_markdown(campaign),
            input_version=int(task["input_version"]),
            attempt_id=None,
        )
        conn.execute(
            "UPDATE tasks SET status = ?, phase = 'draft_ready', reason = ?, updated_at = ? WHERE id = ?",
            (
                STATUS_IN_PROGRESS,
                f"完整方案草稿 v{output['version']} 已保存，等待内容核验。",
                utc_now_iso(),
                task["id"],
            ),
        )
    return {
        "ok": True,
        "output_id": output["output_id"],
        "output_version": output["version"],
        "message": "完整方案草稿已保存；尚未核验，不代表可交付。",
    }, True


def _validate_campaign(campaign: dict, catalog: MaterialCatalog) -> tuple[dict, list[str]]:
    errors: list[str] = []
    if not isinstance(campaign, dict):
        return {}, ["方案必须是 JSON 对象"]

    def required_text(container, key, label):
        value = container.get(key) if isinstance(container, dict) else None
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} 必须是非空字符串")
            return ""
        return value.strip()

    strategy = campaign.get("strategy") if isinstance(campaign.get("strategy"), dict) else {}
    if strategy.get("name") not in ("functional_demo", "user_scenario", "tutorial"):
        errors.append("strategy.name 必须是三种方向之一")
    required_text(strategy, "rationale", "strategy.rationale")
    alternatives = strategy.get("alternatives")
    if not isinstance(alternatives, list):
        errors.append("strategy.alternatives 必须是数组")
        alternatives = []

    activity = campaign.get("activity") if isinstance(campaign.get("activity"), dict) else {}
    for field in ("goal", "audience", "theme", "format"):
        required_text(activity, field, f"activity.{field}")
    content_items = activity.get("content_items")
    if not isinstance(content_items, list) or not content_items:
        errors.append("activity.content_items 至少需要 1 条")
        content_items = []
    normalized_items = []
    for index, item in enumerate(content_items):
        if not isinstance(item, dict):
            errors.append(f"activity.content_items[{index}] 必须是对象")
            continue
        normalized_items.append(
            {
                "title": required_text(item, "title", f"activity.content_items[{index}].title"),
                "purpose": required_text(item, "purpose", f"activity.content_items[{index}].purpose"),
                "duration_s": item.get("duration_s"),
                "platform": required_text(item, "platform", f"activity.content_items[{index}].platform"),
            }
        )

    evidence = campaign.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence 至少需要 1 条")
        evidence = []
    evidence_map = {}
    normalized_evidence = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"evidence[{index}] 必须是对象")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip() or item_id in evidence_map:
            errors.append(f"evidence[{index}].id 必须唯一且非空")
            continue
        material = catalog.get(item.get("material_id")) if isinstance(item.get("material_id"), str) else None
        if material is None:
            errors.append(f"evidence[{index}].material_id 不在本任务材料中")
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or " ".join(quote.split()) not in " ".join(material.text.split()):
            errors.append(f"evidence[{index}].quote 无法在材料正文中找到")
            continue
        normalized = {
            "id": item_id,
            "claim": required_text(item, "claim", f"evidence[{index}].claim"),
            "material_id": material.id,
            "quote": quote.strip(),
            "scope": str(item.get("scope") or material.scope).strip(),
            "use": required_text(item, "use", f"evidence[{index}].use"),
        }
        evidence_map[item_id] = normalized
        normalized_evidence.append(normalized)

    script = campaign.get("script") if isinstance(campaign.get("script"), dict) else {}
    duration = script.get("duration_s")
    if not isinstance(duration, int) or duration <= 0:
        errors.append("script.duration_s 必须是正整数")
    segments = script.get("segments")
    if not isinstance(segments, list) or len(segments) < 2:
        errors.append("script.segments 至少需要 2 段")
        segments = []
    normalized_segments = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"script.segments[{index}] 必须是对象")
            continue
        material_ids = segment.get("material_ids", [])
        if not isinstance(material_ids, list):
            errors.append(f"script.segments[{index}].material_ids 必须是数组")
            material_ids = []
        for material_id in material_ids:
            if catalog.get(material_id) is None:
                errors.append(f"script.segments[{index}] 含未知材料 {material_id}")
        evidence_ids = segment.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            errors.append(f"script.segments[{index}].evidence_ids 必须是数组")
            evidence_ids = []
        for evidence_id in evidence_ids:
            if evidence_id not in evidence_map:
                errors.append(f"script.segments[{index}] 引用未知证据 {evidence_id}")
        normalized_segments.append(
            {
                "index": segment.get("index", index + 1),
                "time_range": required_text(segment, "time_range", f"script.segments[{index}].time_range"),
                "visual": required_text(segment, "visual", f"script.segments[{index}].visual"),
                "audio": required_text(segment, "audio", f"script.segments[{index}].audio"),
                "subtitle": required_text(segment, "subtitle", f"script.segments[{index}].subtitle"),
                "material_ids": material_ids,
                "evidence_ids": evidence_ids,
            }
        )
    if normalized_segments and not any(segment["evidence_ids"] for segment in normalized_segments):
        errors.append("至少一个脚本段落需要引用证据")

    materials = campaign.get("materials")
    if not isinstance(materials, list) or not materials:
        errors.append("materials 至少需要 1 条")
        materials = []
    normalized_materials = []
    for index, item in enumerate(materials):
        if not isinstance(item, dict):
            errors.append(f"materials[{index}] 必须是对象")
            continue
        status = item.get("status")
        if status not in ("existing", "to_acquire", "to_create"):
            errors.append(f"materials[{index}].status 必须是 existing/to_acquire/to_create")
            continue
        material_id = item.get("material_id", "")
        if material_id and catalog.get(material_id) is None:
            errors.append(f"materials[{index}].material_id 不在本任务材料中")
            continue
        normalized_materials.append(
            {
                "name": required_text(item, "name", f"materials[{index}].name"),
                "material_id": material_id,
                "status": status,
                "use": required_text(item, "use", f"materials[{index}].use"),
                "position": required_text(item, "position", f"materials[{index}].position"),
                "rights_note": required_text(item, "rights_note", f"materials[{index}].rights_note"),
            }
        )

    steps = campaign.get("production_steps")
    if not isinstance(steps, list) or not steps:
        errors.append("production_steps 至少需要 1 条")
        steps = []
    normalized_steps = []
    for index, item in enumerate(steps):
        if not isinstance(item, dict):
            errors.append(f"production_steps[{index}] 必须是对象")
            continue
        normalized_steps.append(
            {
                "step": required_text(item, "step", f"production_steps[{index}].step"),
                "owner": required_text(item, "owner", f"production_steps[{index}].owner"),
                "timing": required_text(item, "timing", f"production_steps[{index}].timing"),
                "note": required_text(item, "note", f"production_steps[{index}].note"),
            }
        )

    open_questions = campaign.get("open_questions", [])
    if not isinstance(open_questions, list):
        errors.append("open_questions 必须是数组")
        open_questions = []
    normalized_questions = []
    for index, item in enumerate(open_questions):
        if not isinstance(item, dict):
            errors.append(f"open_questions[{index}] 必须是对象")
            continue
        blocking = item.get("blocking", False)
        if not isinstance(blocking, bool):
            errors.append(f"open_questions[{index}].blocking 必须是布尔值")
            continue
        normalized_questions.append(
            {
                "question": required_text(item, "question", f"open_questions[{index}].question"),
                "impact": required_text(item, "impact", f"open_questions[{index}].impact"),
                "blocking": blocking,
            }
        )

    normalized = {
        "strategy": {
            "name": strategy.get("name"),
            "rationale": strategy.get("rationale", ""),
            "alternatives": alternatives,
        },
        "activity": {
            "goal": activity.get("goal", ""),
            "audience": activity.get("audience", ""),
            "theme": activity.get("theme", ""),
            "format": activity.get("format", ""),
            "content_items": normalized_items,
        },
        "script": {"duration_s": duration, "segments": normalized_segments},
        "materials": normalized_materials,
        "production_steps": normalized_steps,
        "evidence": normalized_evidence,
        "open_questions": normalized_questions,
        "blocking_unknowns": sum(1 for item in normalized_questions if item["blocking"]),
    }
    return normalized, errors


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
        blocking = item.get("blocking", False)
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
        sources.append({"material_id": material_id, "used_for": str(item.get("used_for", "未说明")).strip()})
    normalized["sources"] = sources
    normalized["blocking_unknowns"] = sum(1 for item in unknowns if item["blocking"])
    return normalized, errors


def _request_clarification(conn, task, parsed: dict) -> tuple[dict, bool]:
    questions = parsed.get("questions")
    if not isinstance(questions, list) or not questions:
        return {"ok": False, "error": "invalid_arguments", "message": "questions 必须是非空数组。"}, False
    if len(questions) > 5:
        return {"ok": False, "error": "invalid_arguments", "message": "一次最多集中询问 5 个问题。"}, False
    normalized = []
    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            return {"ok": False, "error": "invalid_arguments", "message": f"questions[{index}] 必须是对象。"}, False
        question = item.get("question")
        impact = item.get("impact")
        blocking = item.get("blocking", False)
        if not isinstance(question, str) or not question.strip() or not isinstance(impact, str) or not impact.strip():
            return {"ok": False, "error": "invalid_arguments", "message": f"questions[{index}] 需要 question 与 impact。"}, False
        if not isinstance(blocking, bool):
            return {"ok": False, "error": "invalid_arguments", "message": f"questions[{index}].blocking 必须是布尔值。"}, False
        normalized.append({"question": question.strip(), "impact": impact.strip(), "blocking": blocking})
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
