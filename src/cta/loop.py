"""The single main loop: one validated model action per round, then real feedback.

The loop never assumes the service returns one call. Unknown tools, invalid
arguments, multiple calls and truncated responses are rejected and returned to
the model; each round is counted and failures stay visible.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.faults import crash
from cta.knowledge import freeze_bundles, read_skill, task_bundles
from cta.materials import MaterialCatalog, load_catalog, load_task_catalog
from cta.model import ModelResponse, create_client
from cta.pricing import PriceTable, load_price_table
from cta.prompts import (
    MAIN_SYSTEM_PROMPT,
    PROMPT_VERSION,
    TOOL_DEFINITIONS,
)
from cta.protocol import HANDLER_VERSION, incompatibility
from cta.recovery import (
    append_recovery_fact,
    recover_task,
    recovery_has_events,
)
from cta.repository import (
    LOCAL_TOOL_NAMES,
    api_messages,
    append_message,
    append_snapshot,
    count_attempts,
    finish_attempt,
    latest_brief,
    latest_output,
    latest_review,
    record_estimate,
    record_llm_request,
    record_local_tool_result,
    record_stop,
    snapshot_state,
    start_attempt,
    start_llm_request,
    task_spent,
)
from cta.review import ReviewError, review_task
from cta.save import SaveBlocked, reconcile_task, save_preconditions, save_task
from cta.service import (
    STATUS_COMPLETE,
    STATUS_IN_PROGRESS,
    STATUS_INCOMPLETE,
    STATUS_NEEDS_INPUT,
    TaskNotFound,
    TaskService,
    output_reuse_basis,
)
from cta.task_plan import update_plan


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


def run_task(config: Config, task_id: str, *, max_rounds: int | None = None, explicit_resume: bool = False) -> dict:
    catalog = load_catalog(config.project_root)
    run_info = {
        "rounds_used": 0,
        "stop_reason": None,
        "requests": 0,
        "explicit_resume": explicit_resume,
        "recovery": None,
    }

    with open_store(config.db_path) as conn:
        task = _task(conn, task_id)
        if task["protocol_version"] != "direct-script-v1":
            run_info["stop_reason"] = "legacy_protocol"
            document = _document(config, task_id, run_info)
            document["reason"] = ("旧任务使用旧业务协议，请使用固定代码基线 "
                                  "836aa827d835e54a8e9385c18e7a3ff05ed3e600 和原采用资料恢复。")
            return document
        incompatible = incompatibility(conn, task_id)
        if incompatible is not None:
            run_info["stop_reason"] = incompatible["error"]
            document = _document(config, task_id, run_info)
            document.update(incompatible)
            return document
        price = load_price_table(config.project_root, version=task["pricing_version"])
        if task["status"] == STATUS_NEEDS_INPUT and not _has_messages(conn, task_id):
            run_info["stop_reason"] = "needs_input"
            return _document(config, task_id, run_info)

        # Verify unfinished or result-unknown actions before anything else.
        report = recover_task(conn, task, config, explicit=explicit_resume)
        run_info["recovery"] = report
        task = _task(conn, task_id)
        _ensure_base_context(conn, task, config)
        protocol = _main_protocol(conn, task)
        task = _task(conn, task_id)

        unpaused = False
        if task["paused"] and not explicit_resume:
            if recovery_has_events(report):
                append_recovery_fact(conn, task, report)
                _snapshot(conn, task)
            run_info["stop_reason"] = "paused"
            return _document(config, task_id, run_info)
        if explicit_resume and task["paused"]:
            with transaction(conn):
                conn.execute(
                    "UPDATE tasks SET paused = 0, updated_at = ? WHERE id = ?",
                    (utc_now_iso(), task_id),
                )
            unpaused = True
            task = _task(conn, task_id)

        _ensure_phase_context(conn, task, config)
        task = _task(conn, task_id)

        if explicit_resume or recovery_has_events(report) or unpaused:
            run_info["recovery_summary"] = append_recovery_fact(conn, task, report, unpaused=unpaused)
        _snapshot(conn, task)
        task = _task(conn, task_id)

        if report["save_unknown"] is not None:
            run_info["stop_reason"] = "save_unknown"
            return _document(config, task_id, run_info)
        if task["status"] == STATUS_NEEDS_INPUT:
            run_info["stop_reason"] = "needs_input"
            return _document(config, task_id, run_info)

        if explicit_resume:
            if task["status"] == STATUS_INCOMPLETE:
                if task["used_rounds"] >= task["max_rounds"]:
                    _set_incomplete(conn, task, f"达到主循环轮数上限（{task['max_rounds']} 轮）；显式续跑不获得新额度。")
                    run_info["stop_reason"] = "max_rounds"
                    return _document(config, task_id, run_info)
                if _past_deadline(conn, task):
                    _set_incomplete(conn, task, "模拟方案截止时间已到，保留现有成果；续跑不获得新的模拟时限。")
                    run_info["stop_reason"] = "deadline"
                    return _document(config, task_id, run_info)
                resume_phase = _resume_phase(conn, task_id)
                with transaction(conn):
                    conn.execute(
                        "UPDATE tasks SET status = ?, phase = ?, reason = ?, updated_at = ? WHERE id = ?",
                        (
                            STATUS_IN_PROGRESS,
                            resume_phase,
                            "显式续跑：从已保存进度继续；原输入、预算、模拟截止与已用轮数保持不变。",
                            utc_now_iso(),
                            task_id,
                        ),
                    )
                task = _task(conn, task_id)
                _ensure_phase_context(conn, task, config)
                _snapshot(conn, task)
                task = _task(conn, task_id)
        else:
            if task["status"] == STATUS_INCOMPLETE:
                run_info["stop_reason"] = "incomplete"
                return _document(config, task_id, run_info)

        if task["status"] == STATUS_COMPLETE:
            reconcile_task(config, task_id, refresh_exports=True)
            run_info["stop_reason"] = "already_complete"
            return _document(config, task_id, run_info)
        if task["used_rounds"] >= task["max_rounds"]:
            _set_incomplete(conn, task, f"达到主循环轮数上限（{task['max_rounds']} 轮），保留现有成果。")
            run_info["stop_reason"] = "max_rounds"
            return _document(config, task_id, run_info)
        if _past_deadline(conn, task):
            _set_incomplete(conn, task, "模拟方案截止时间已到，保留现有成果。")
            run_info["stop_reason"] = "deadline"
            return _document(config, task_id, run_info)

        catalog = load_task_catalog(conn, task, config.project_root)

        try:
            client = create_client(config)
        except Exception as exc:  # noqa: BLE001 - model boundary
            _set_incomplete(
                conn,
                task,
                f"模型不可用：{config.redact(str(exc))}",
                kind="model_unavailable",
                gaps=[{"gap": "主模型不可用", "impact": "当前无法继续，保留已完成成果；配置修复后可显式续跑"}],
            )
            run_info["stop_reason"] = "model_unavailable"
            return _document(config, task_id, run_info)

        while True:
            task = _task(conn, task_id)
            if task["status"] in (STATUS_COMPLETE, STATUS_INCOMPLETE):
                run_info["stop_reason"] = task["status"]
                break
            if task["used_rounds"] >= task["max_rounds"]:
                _set_incomplete(
                    conn,
                    task,
                    f"达到主循环轮数上限（{task['max_rounds']} 轮），保留现有成果。",
                    kind="max_rounds",
                    gaps=[{"gap": f"达到主循环轮数上限（{task['max_rounds']} 轮）", "impact": "未完成步骤不再执行；需显式续跑或调整条件"}],
                )
                run_info["stop_reason"] = "max_rounds"
                break
            if max_rounds is not None and run_info["rounds_used"] >= max_rounds:
                run_info["stop_reason"] = "injected_round_cap"
                break
            if _past_deadline(conn, task):
                _set_incomplete(
                    conn,
                    task,
                    "模拟方案截止时间已到，保留现有成果。",
                    kind="deadline",
                    gaps=[{"gap": "模拟方案截止时间已到", "impact": "剩余核验/保存或返修无法在截止前完成"}],
                )
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
            request_id = start_llm_request(
                conn, task_id=task_id, purpose=f"main_loop_round_{round_index + 1}", started_at=started,
                request_config=config.model_config_public(), mock=config.mode == "mock",
                meta={"prompt_version": protocol["prompt_version"], "round": round_index + 1,
                      "tool_schema_sha256": protocol["sha256"],
                      "request": config.redact_data({
                          "model": "scripted" if config.mode == "mock" else config.model,
                          "messages": messages, "tools": protocol["tools"],
                          "tool_choice": "auto", "stream": False, "thinking": {"type": "disabled"},
                          "mock": config.mode == "mock",
                      })},
            )
            try:
                response = client.complete(messages, protocol["tools"], cursor=round_index)
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
                    request_id=request_id,
                    meta={"prompt_version": protocol["prompt_version"], "round": round_index + 1,
                          "tool_schema_sha256": protocol["sha256"]},
                )

            if response.error:
                with transaction(conn):
                    append_message(conn, task_id, "user", f"[运行时反馈] 模型请求失败：{response.error}", meta={"kind": "runtime_feedback"})
                _set_incomplete(
                    conn,
                    task,
                    f"模型请求失败：{response.error}",
                    kind="model_error",
                    gaps=[{"gap": "模型请求失败", "impact": "本轮无有效动作；保留已有成果与用量，需显式续跑"}],
                )
                run_info["stop_reason"] = "model_error"
                break

            outcome = _handle_response(conn, task, response, price, catalog, config, client,
                                       tools=protocol["tools"])
            task = _task(conn, task_id)
            _ensure_phase_context(conn, task, config)
            _snapshot(conn, task)
            if outcome["terminal"]:
                if task["status"] == STATUS_COMPLETE:
                    # The save's main-tool receipt is now durable; include it
                    # and the final snapshot in the same verified delivery.
                    reconcile_task(config, task_id, refresh_exports=True)
                run_info["stop_reason"] = outcome["stop_reason"]
                break

        task = _task(conn, task_id)
        if task["status"] == STATUS_IN_PROGRESS and run_info["stop_reason"] is None:
            run_info["stop_reason"] = "stopped"

    return _document(config, task_id, run_info)


def resume_task(config: Config, task_id: str, *, max_rounds: int | None = None) -> dict:
    """Explicit cross-process resume: verify unfinished actions, then continue."""
    return run_task(config, task_id, max_rounds=max_rounds, explicit_resume=True)


def _has_messages(conn, task_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main'", (task_id,)
    ).fetchone()
    return int(row["n"]) > 0


def _resume_phase(conn, task_id: str) -> str:
    output = latest_output(conn, task_id)
    if output is not None:
        review = latest_review(conn, task_id)
        return "reviewed" if (
            review is not None and review["status"] == "complete" and review["output_id"] == output["output_id"]
        ) else "draft_ready"
    brief = latest_brief(conn, task_id)
    return "brief_ready" if brief is not None else "created"


def _document(config: Config, task_id: str, run_info: dict) -> dict:
    document = TaskService(config).status(task_id)
    document["run"] = run_info
    return document


def _main_protocol(conn, task) -> dict:
    prompt_version = "unknown: legacy prompt version not recorded"
    for row in conn.execute(
        "SELECT meta_json FROM messages WHERE task_id = ? AND agent = 'main' ORDER BY seq", (task["id"],)
    ):
        meta = json.loads(row["meta_json"])
        if "tool_protocol" in meta:
            return meta["tool_protocol"]
        if meta.get("kind") == "system":
            prompt_version = meta["prompt_version"]
    protocol = _protocol_definition(prompt_version)
    with transaction(conn):
        append_message(conn, task["id"], "user",
                       "[工具定义采用] 本次起冻结所用工具定义与顺序；此前历史未记录定义，不能追认为已冻结。",
                       meta={"kind": "tool_schema_adoption", "tool_protocol": protocol})
    return protocol


def _protocol_definition(prompt_version: str = PROMPT_VERSION) -> dict:
    return {
        "prompt_version": prompt_version,
        "handler_version": HANDLER_VERSION,
        "tools": TOOL_DEFINITIONS,
        "sha256": hashlib.sha256(json.dumps(TOOL_DEFINITIONS, ensure_ascii=False).encode()).hexdigest(),
    }


def _ensure_base_context(conn, task, config: Config) -> None:
    """Append the stable system/knowledge/input prefix only for a fresh task.

    A resumed task already has its actual messages, so external knowledge files
    are never reloaded or substituted.
    """
    with transaction(conn):
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main' AND role = 'system'", (task["id"],)
        ).fetchone()
        if int(existing["n"]) > 0:
            return
        append_message(
            conn,
            task["id"],
            "system",
            MAIN_SYSTEM_PROMPT,
            meta={"kind": "system", "prompt_version": PROMPT_VERSION,
                  "tool_protocol": _protocol_definition()},
        )
        freeze_bundles(conn, task["id"], config.project_root)
        bundles = task_bundles(conn, task["id"])
        doc = next(item for item in bundles if item["name"] == "campaign-creation")
        directory = [{"name": item["name"], "description": item["description"],
                      "version": item["version"], "loaded": item["name"] == "campaign-creation"}
                     for item in bundles]
        append_message(conn, task["id"], "system",
                       "[Skill目录] 已预装的 Skill 标识 loaded=true；其他 Skill 可按需读取。\n"
                       + json.dumps(directory, ensure_ascii=False), meta={"kind": "skill_directory"})
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_loads (task_id, agent, knowledge_id, version, sha256, content, loaded_at)
            VALUES (?, 'main', ?, ?, ?, ?, ?)
            """,
            (task["id"], doc["knowledge_id"], doc["version"], doc["sha256"], doc["content"], utc_now_iso()),
        )
        stored = conn.execute(
            "SELECT * FROM knowledge_loads WHERE task_id = ? AND agent = 'main' AND knowledge_id = ?",
            (task["id"], doc["knowledge_id"]),
        ).fetchone()
        append_message(
            conn,
            task["id"],
            "system",
            f"[工作知识] {stored['knowledge_id']} {stored['version']} sha256:{stored['sha256']}\n\n{stored['content']}",
            meta={"kind": "knowledge", "knowledge_id": stored["knowledge_id"], "version": stored["version"]},
        )
        price = load_price_table(config.project_root, version=task["pricing_version"])
        append_message(conn, task["id"], "user", "[适用费用能力概览] " + json.dumps(price.overview(), ensure_ascii=False),
                       meta={"kind": "pricing_overview", "version": price.version})
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
            "raw_input": json.loads(input_row["raw_json"]),
            "sim_start": task["sim_start"],
            "max_rounds": task["max_rounds"],
        }
        append_message(
            conn,
            task["id"],
            "user",
            "[任务输入] " + json.dumps(task_input, ensure_ascii=False),
            meta={"kind": "task_input", "input_version": input_row["version"]},
        )


def _delivery_options(conn, task) -> dict:
    task = _task(conn, task["id"])
    output, review = latest_output(conn, task["id"]), latest_review(conn, task["id"])
    blocked = {"code": "no_output"} if output is None else save_preconditions(conn, task, output)
    return {
        "quality_allows_save": blocked is None,
        "quality_blocker": blocked,
        "optional_findings": sum(f["severity"] == "optional" for f in review["findings"]) if review else 0,
        "remaining_main_rounds": task["max_rounds"] - task["used_rounds"],
        "next": (
            "当前稿已满足内容保存前提。比较保存当前合格稿与有实质收益的改进；optional 无需全部采纳，"
            "可依据当前已满足的要求、收益和回归风险保留原稿。任何改进先核对完整返修/复审/保存路径及剩余主轮数；"
            "没有明确值得投入的改进时调用 save_campaign，保存仍核对预算、模拟截止和全部文件。"
            if blocked is None else
            "处理当前关键缺口，集中记录每条意见的依据；必要返修后复审，资料不足或完整路径不可行时明确停止。"
        ),
    }


def _ensure_phase_context(conn, task, config: Config) -> None:
    if task["phase"] not in ("brief_ready", "draft_ready", "reviewed", "review_incomplete", "input_updated"):
        return
    with transaction(conn):
        if task["phase"] == "brief_ready":
            transition = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main' AND meta_json LIKE '%phase_transition%'",
                (task["id"],),
            ).fetchone()
            script_exists = conn.execute(
                "SELECT 1 FROM outputs WHERE task_id = ? AND kind = 'script' AND input_version = ? LIMIT 1",
                (task["id"], task["input_version"]),
            ).fetchone()
            if int(transition["n"]) == 0 and script_exists is None:
                append_message(
                    conn,
                    task["id"],
                    "user",
                    "[阶段推进] 业务简报已保存。请依据当前材料完成脚本，调用 submit_script 提交完整稿并引用该简报版本。",
                    meta={"kind": "phase_transition", "from": "brief", "to": "campaign_draft"},
                )
        if task["phase"] == "draft_ready":
            output = latest_output(conn, task["id"])
            if output is not None and output["status"] == "pending_review":
                imported = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main' AND meta_json LIKE ?",
                    (task["id"], f'%"imported_output_id": "{output["output_id"]}"%'),
                ).fetchone()
                if not imported["n"]:
                    append_message(
                        conn, task["id"], "user",
                        "[待核验人工稿] " + json.dumps({
                            "output_id": output["output_id"],
                            "output_version": output["version"],
                            "input_version": output["input_version"],
                            "campaign": output["content"],
                            "markdown": output["content_md"],
                            "instruction": "这是当前采用的完整人工稿。检查修改与整体关系，调用 review_campaign；旧稿通过结论不继承。",
                        }, ensure_ascii=False),
                        meta={"kind": "imported_campaign", "imported_output_id": output["output_id"]},
                    )
        if task["phase"] in ("reviewed", "review_incomplete"):
            review = latest_review(conn, task["id"])
            pattern = "%review_feedback%"
            if review is not None:
                pattern = f'%"review_id": "{review["review_id"]}"%'
            feedback = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main' AND meta_json LIKE ?",
                (task["id"], pattern),
            ).fetchone()
            if int(feedback["n"]) == 0:
                dispositions = []
                if review is not None:
                    dispositions = [
                        {"finding_id": row["finding_id"], "decision": row["decision"], "reason": row["reason"],
                         "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else []}
                        for row in conn.execute(
                            "SELECT * FROM dispositions WHERE review_id = ? ORDER BY id", (review["review_id"],)
                        )
                    ]
                append_message(
                    conn,
                    task["id"],
                    "user",
                    "[核验结果] "
                    + json.dumps(
                        {
                            "review": review,
                            "dispositions": dispositions,
                            "delivery_options": _delivery_options(conn, task),
                        },
                        ensure_ascii=False,
                    ),
                    meta={"kind": "review_feedback", "review_id": None if review is None else review["review_id"]},
                )
        if task["phase"] == "input_updated":
            existing_update = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE task_id = ? AND agent = 'main' "
                "AND json_extract(meta_json, '$.kind') = 'input_update' "
                "AND json_extract(meta_json, '$.input_version') = ?",
                (task["id"], task["input_version"]),
            ).fetchone()
            if int(existing_update["n"]) == 0:
                input_row = _input_row(conn, task)
                append_message(
                    conn,
                    task["id"],
                    "user",
                    "[输入更新] "
                    + json.dumps(
                        {
                            "input_version": input_row["version"],
                            "topic": input_row["topic"],
                            "audience": input_row["audience"],
                            "budget_cents": input_row["budget_cents"],
                            "deadline": input_row["deadline"],
                            "requirements": json.loads(input_row["requirements_json"]),
                            "raw_input": json.loads(input_row["raw_json"]),
                            "instruction": "按新输入重新形成简报，并复核受影响的内容与评审；旧简报/旧稿不自动通过。",
                        },
                        ensure_ascii=False,
                    ),
                    meta={"kind": "input_update", "input_version": input_row["version"]},
                )


def _snapshot(conn, task) -> None:
    with transaction(conn):
        append_snapshot(conn, task["id"], snapshot_state(conn, task["id"]))


def _set_incomplete(conn, task, reason: str, *, kind: str = "stopped", gaps: list[dict] | None = None) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE tasks SET status = ?, phase = 'stopped', reason = ?, updated_at = ? WHERE id = ?",
            (STATUS_INCOMPLETE, reason, utc_now_iso(), task["id"]),
        )
        record_stop(conn, task_id=task["id"], kind=kind, reason=reason, gaps=gaps)


def _past_deadline(conn, task) -> bool:
    input_row = _input_row(conn, task)
    if input_row["deadline"] is None:
        return False
    deadline = datetime.fromisoformat(input_row["deadline"])
    sim_start = datetime.fromisoformat(task["sim_start"])
    elapsed = (deadline - sim_start).total_seconds() * 1000
    return task["sim_elapsed_ms"] >= elapsed


def _handle_response(
    conn,
    task,
    response: ModelResponse,
    price: PriceTable,
    catalog: MaterialCatalog,
    config: Config,
    client,
    *,
    tools: list[dict] | None = None,
) -> dict:
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
        request_seq = append_message(
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

    crash("after_tool_request")
    call = calls[0]
    parsed, error = _parse_call(call, tools if tools is not None else TOOL_DEFINITIONS)
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

    if call.name in LOCAL_TOOL_NAMES:
        # These helpers perform only local writes. Their effects and exact
        # return value must commit together before a missing receipt is possible.
        with transaction(conn):
            if call.name == "propose_brief":
                from cta.artifacts import propose_brief
                result, terminal = propose_brief(conn, task, parsed, catalog)
            elif call.name == "request_clarification":
                result, terminal = _request_clarification(conn, task, parsed)
            elif call.name == "stop_incomplete":
                result, terminal = _stop_incomplete(conn, task, parsed)
            elif call.name == "record_dispositions":
                from cta.dispositions import record_dispositions
                result = record_dispositions(conn, task, parsed, catalog)
                result["delivery_options"] = _delivery_options(conn, task)
            else:
                result = _execute_business_tool(conn, task, call.name, parsed, price, catalog, config)
            crash("before_local_tool_receipt")
            record_local_tool_result(conn, task["id"], request_seq, call.id, call.name, result)
        if call.name not in {"propose_brief", "request_clarification", "stop_incomplete"}:
            terminal = False
    elif call.name == "submit_script":
        from cta.artifacts import submit_script
        result = submit_script(conn, task, parsed, price, catalog, tool_call_id=call.id, request_seq=request_seq, guard=_guard)
        terminal = False
    elif call.name == "review_campaign":
        target_error = _campaign_target_error(conn, task, parsed)
        try:
            if target_error:
                raise ReviewError(target_error, "invalid_campaign_ref")
            document = review_task(config, task["id"], tool_call_id=call.id, request_seq=request_seq)
            review = document["review"]
            result = {"ok": review["status"] == "complete", "review": review, "resources": document["resources"],
                      "delivery_options": _delivery_options(conn, task)}
            if review["status"] != "complete":
                result.update(error="review_incomplete", message="核验未完成，不能作为通过依据。")
        except ReviewError as exc:
            result = {"ok": False, "error": exc.code, "message": str(exc)}
        terminal = False
    elif call.name == "save_campaign":
        target_error = _campaign_target_error(conn, task, parsed)
        try:
            if target_error:
                raise SaveBlocked("invalid_campaign_ref", target_error)
            document = save_task(config, task["id"], tool_call_id=call.id, request_seq=request_seq)
            result = {
                "ok": document["status"] == STATUS_COMPLETE,
                "save": document.get("save"),
                "resources": document["resources"],
                **({"error": document["save_error"]["code"], "message": document["save_error"]["message"]}
                   if document.get("save_error") else {}),
            }
            terminal = result["ok"] or result.get("error") == "save_unknown"
            if document["progress"]["phase"] == "save_failed":
                with transaction(conn):
                    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (STATUS_IN_PROGRESS, task["id"]))
        except SaveBlocked as exc:
            result = {"ok": False, "error": exc.code, "message": exc.message}
            terminal = False
    else:
        result = _execute_business_tool(
            conn, task, call.name, parsed, price, catalog, config, tool_call_id=call.id, request_seq=request_seq
        )
        terminal = False

    if call.name in {"review_campaign", "save_campaign"}:
        crash("after_business_result_before_receipt")
        with transaction(conn):
            record_local_tool_result(conn, task["id"], request_seq, call.id, call.name, result)

    crash("after_tool_effect")
    if call.name == "review_campaign":
        crash("after_review_effect")
    if call.name == "save_campaign":
        crash("after_save_effect")

    with transaction(conn):
        append_message(
            conn,
            task["id"],
            "tool",
            json.dumps(result, ensure_ascii=False),
            tool_call_id=call.id,
            name=call.name,
            attempt_id=result.get("attempt_id"),
            meta={
                "kind": "tool_result",
                **({"review_id": result["review"]["review_id"]}
                   if call.name == "review_campaign" and result.get("review") else {}),
            },
        )
        if terminal:
            append_snapshot(conn, task["id"], snapshot_state(conn, task["id"]))
    stop_reason = {"propose_brief": "brief_ready", "propose_campaign": "draft_ready"}.get(call.name, call.name)
    return {"terminal": terminal, "stop_reason": stop_reason}


def _parse_call(call, tools: list[dict]) -> tuple[dict, dict | None]:
    if call.name not in {item["function"]["name"] for item in tools}:
        return {}, {"error": "unknown_tool", "message": f"未知工具：{call.name}；本轮未执行任何业务动作。"}
    try:
        parsed = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return {}, {"error": "invalid_arguments", "message": "工具参数不是合法 JSON；本轮未执行任何业务动作。"}
    if not isinstance(parsed, dict):
        return {}, {"error": "invalid_arguments", "message": "工具参数必须是 JSON 对象。"}
    return parsed, None


def _campaign_target_error(conn, task, parsed):
    ref = parsed.get("campaign_ref")
    output = latest_output(conn, task["id"])
    if (set(parsed) != {"campaign_ref"} or not isinstance(ref, dict) or set(ref) != {"id", "version"}
            or not isinstance(ref.get("id"), str) or not isinstance(ref.get("version"), int)
            or isinstance(ref.get("version"), bool) or output is None
            or ref["id"] != output["output_id"] or ref["version"] != output["version"]):
        return "campaign_ref 必须明确引用本任务当前方案 ID 和版本。"
    if output["input_version"] != task["input_version"] and output_reuse_basis(conn, task, output) is None:
        return "方案不适用于当前输入版本。"
    return None


def _execute_business_tool(
    conn,
    task,
    name: str,
    parsed: dict,
    price: PriceTable,
    catalog: MaterialCatalog,
    config: Config,
    *,
    tool_call_id: str | None = None,
    request_seq: int | None = None,
) -> dict:
    if name == "submit_campaign":
        from cta.campaign import submit_campaign
        return submit_campaign(conn, task, parsed, catalog)
    if name == "read_skill":
        return read_skill(conn, task["id"], parsed)
    if name == "update_plan":
        return update_plan(conn, task["id"], parsed)
    if name == "search_materials":
        return _search(conn, task, parsed, price, catalog, tool_call_id=tool_call_id, request_seq=request_seq)
    if name == "read_material":
        material_id = parsed.get("material_id")
        if not isinstance(material_id, str) or not material_id:
            return {"ok": False, "error": "invalid_arguments", "message": "material_id 必须是非空字符串。"}
        return catalog.read(material_id)
    if name == "estimate_cost":
        return _estimate(conn, task, parsed, price)
    return {"ok": False, "error": "unknown_tool", "message": f"未知工具：{name}"}


def _injected_search_failures() -> int:
    """Test-only environment injection: fail the first N search attempts of a task."""
    raw = os.environ.get("CTA_FAIL_SEARCH")
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _search(
    conn,
    task,
    parsed: dict,
    price: PriceTable,
    catalog: MaterialCatalog,
    *,
    tool_call_id: str | None = None,
    request_seq: int | None = None,
) -> dict:
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
        meta={"query": query, "mode": mode, "tool_call_id": tool_call_id, "request_seq": request_seq},
    )
    conn.commit()
    crash("search_after_start")
    fail_first = _injected_search_failures()
    if fail_first and count_attempts(conn, task["id"], "search_materials", exclude_id=attempt_id) < fail_first:
        result = {"ok": False, "error": "tool_failed", "injected": True,
                  "message": "检索执行失败：injected search failure (CTA_FAIL_SEARCH)；本次尝试与消耗已保留。"}
    else:
        try:
            result = catalog.search(query, mode)
        except Exception as exc:  # noqa: BLE001 - business tool boundary
            result = {"ok": False, "error": "tool_failed", "message": f"检索执行失败：{exc}"}
    result.update(attempt_id=attempt_id, cost_cents=spec.cost_cents, duration_ms=spec.duration_ms)
    error = None if result["ok"] else (result["message"] if "error" in result else result["gap"])
    finish_attempt(conn, attempt_id, status="success" if result["ok"] else "failed",
                   error=error, meta={"result": result})
    conn.commit()
    return result



def _estimate(conn, task, parsed: dict, price: PriceTable) -> dict:
    actions = parsed.get("actions")
    if not isinstance(actions, list) or not actions or not all(isinstance(action, str) for action in actions):
        return {"ok": False, "error": "invalid_arguments", "message": "actions 必须是非空动作名称列表。"}
    unknown = [action for action in actions if action not in price.modes]
    if unknown:
        return {"ok": False, "error": "invalid_arguments", "message": f"未知费用动作：{unknown}"}
    output = latest_output(conn, task["id"])
    review = latest_review(conn, task["id"])
    reviewed = output is not None and review is not None and review["output_id"] == output["output_id"] and review["status"] == "complete"
    required = ["save"]
    if not reviewed or "revision" in actions:
        required.insert(0, "review")
    missing = [action for action in required if action not in actions]
    if missing:
        return {"ok": False, "error": "missing_delivery_actions", "missing_actions": missing,
                "message": "请将尚需的核验和保存加入动作清单；调整清单不会锁定实际执行顺序。"}
    estimate = price.estimate_actions(actions)
    input_row = _input_row(conn, task)
    spent = task_spent(conn, task["id"])
    remaining = None if input_row["budget_cents"] is None else input_row["budget_cents"] - spent
    sim_start = datetime.fromisoformat(task["sim_start"])
    projected = sim_start + timedelta(milliseconds=task["sim_elapsed_ms"] + estimate["total_duration_ms"])
    estimate["projected_sim_complete"] = projected.isoformat()
    estimate["deadline"] = input_row["deadline"]
    estimate["incurred_cents"] = spent
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
    feasible = bool(estimate["affordable"] and estimate["within_deadline"] is not False)
    recorded = record_estimate(
        conn,
        task_id=task["id"],
        kind="cost_estimate",
        direction=None,
        search_mode=None,
        generation_mode=None,
        search_queries=None,
        include_revision="revision" in actions,
        strategy_version=task["strategy_version"],
        pricing_version=price.version,
        total_cost_cents=estimate["total_cost_cents"],
        total_duration_ms=estimate["total_duration_ms"],
        incurred_cents=spent,
        remaining_cents=remaining,
        feasible=feasible,
        detail={"estimate": estimate},
    )
    estimate["estimate_id"] = recorded["estimate_id"]
    estimate["nature"] = "estimated"
    return {"ok": True, "estimate": estimate, "estimate_id": recorded["estimate_id"]}


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
    if not isinstance(alternatives, list) or not alternatives:
        errors.append("strategy.alternatives 至少需要 1 条已比较但未选择的方案")
        alternatives = []
    normalized_alternatives = []
    for index, alternative in enumerate(alternatives):
        if not isinstance(alternative, dict):
            errors.append(f"strategy.alternatives[{index}] 必须是对象")
            continue
        name = required_text(alternative, "name", f"strategy.alternatives[{index}].name")
        why_not = required_text(alternative, "why_not", f"strategy.alternatives[{index}].why_not")
        if name and why_not:
            normalized_alternatives.append({"name": name, "why_not": why_not})

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
        if not isinstance(item.get("duration_s"), int) or isinstance(item.get("duration_s"), bool) or item["duration_s"] <= 0:
            errors.append(f"activity.content_items[{index}].duration_s 必须是正整数")

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
        scope = item.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            errors.append(f"evidence[{index}].scope 不能为空")
            continue
        normalized = {
            "id": item_id,
            "claim": required_text(item, "claim", f"evidence[{index}].claim"),
            "material_id": material.id,
            "quote": quote.strip(),
            "scope": scope.strip(),
            "use": required_text(item, "use", f"evidence[{index}].use"),
        }
        evidence_map[item_id] = normalized
        normalized_evidence.append(normalized)

    script = campaign.get("script") if isinstance(campaign.get("script"), dict) else {}
    duration = script.get("duration_s")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
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
            "alternatives": normalized_alternatives,
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
    raw_gaps = parsed.get("gaps", [])
    if not isinstance(raw_gaps, list):
        return {"ok": False, "error": "invalid_arguments", "message": "gaps 必须是数组。"}, False
    if len(raw_gaps) > 6:
        return {"ok": False, "error": "invalid_arguments", "message": "gaps 最多 6 条。"}, False
    gaps = []
    for index, item in enumerate(raw_gaps):
        if not isinstance(item, dict):
            return {"ok": False, "error": "invalid_arguments", "message": f"gaps[{index}] 必须是对象。"}, False
        gap = item.get("gap")
        impact = item.get("impact", "")
        if not isinstance(gap, str) or not gap.strip():
            return {"ok": False, "error": "invalid_arguments", "message": f"gaps[{index}].gap 不能为空。"}, False
        if not isinstance(impact, str):
            return {"ok": False, "error": "invalid_arguments", "message": f"gaps[{index}].impact 必须是字符串。"}, False
        gaps.append({"gap": gap.strip(), "impact": impact.strip()})
    conn.execute(
        "UPDATE tasks SET status = ?, phase = 'stopped', reason = ?, updated_at = ? WHERE id = ?",
        (STATUS_INCOMPLETE, reason.strip(), utc_now_iso(), task["id"]),
    )
    record_stop(conn, task_id=task["id"], kind="model_stop", reason=reason.strip(), gaps=gaps)
    return {
        "ok": True,
        "gaps": gaps,
        "message": "任务已停止并保留现有成果与消耗。"
        + ("" if gaps else " 未提供具体缺口；请补充无法解决的缺口与影响。"),
    }, True
