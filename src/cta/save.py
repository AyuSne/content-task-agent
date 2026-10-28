"""Verify review prerequisites and save a specific campaign version.

Completion requires a verified save record whose files match the authoritative
output; a draft or a request is never enough.
"""

from __future__ import annotations

import json
import os

from cta.config import Config, utc_now_iso
from cta.db import open_store
from cta.export import build_files, build_trace_file, verify_records
from cta.pricing import load_price_table
from cta.repository import (
    create_save,
    finish_attempt,
    latest_brief,
    latest_output,
    latest_review,
    latest_save,
    start_attempt,
    task_spent,
)


class SaveBlocked(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _blocking_open_questions(conn, task_id: str, output: dict) -> int:
    brief = latest_brief(conn, task_id)
    brief_blocking = 0 if brief is None else sum(
        1 for item in brief["content"].get("unknowns", []) if item.get("blocking")
    )
    campaign_blocking = sum(
        1 for item in output["content"].get("open_questions", []) if item.get("blocking")
    )
    return brief_blocking + campaign_blocking


def _preconditions(conn, task, output: dict) -> dict | None:
    review = latest_review(conn, task["id"])
    if review is None:
        return {"code": "not_reviewed", "message": "当前产物尚未经过独立内容核验。"}
    if review["status"] != "complete":
        return {"code": "review_incomplete", "message": "内容核验未完成，不能保存。"}
    if review["output_id"] != output["output_id"]:
        return {"code": "review_stale", "message": "最近一次核验不适用于当前产物版本。"}
    must_fix = [item for item in review["findings"] if item["severity"] == "must_fix"]
    if must_fix:
        return {"code": "unresolved_must_fix", "message": f"仍有 {len(must_fix)} 条必须处理意见未处置。"}
    if review["verdict"] != "pass" or review["completeness"] != "full":
        return {"code": "review_not_passed", "message": "核验未通过或完整性不足，不能保存。"}
    if review["evidence_gaps"]:
        return {"code": "review_evidence_gaps", "message": "核验仍有材料缺口，不能保存。"}
    blocking = _blocking_open_questions(conn, task["id"], output)
    if blocking:
        return {"code": "blocking_open_questions", "message": f"仍有 {blocking} 个关键待确认项未处理。"}
    return None


def _guard(conn, task, cost_cents: int, duration_ms: int) -> dict | None:
    input_row = conn.execute(
        "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?", (task["id"], task["input_version"])
    ).fetchone()
    spent = task_spent(conn, task["id"])
    budget = input_row["budget_cents"]
    if budget is not None and spent + cost_cents > budget:
        return {"code": "budget_exceeded", "message": f"预算不足：已发生 {spent} 分，保存需要 {cost_cents} 分。"}
    if input_row["deadline"] is not None:
        from datetime import datetime

        deadline = datetime.fromisoformat(input_row["deadline"])
        sim_start = datetime.fromisoformat(task["sim_start"])
        allowed_ms = (deadline - sim_start).total_seconds() * 1000
        if task["sim_elapsed_ms"] + duration_ms > allowed_ms:
            return {"code": "deadline_exceeded", "message": "模拟截止时间不足以完成保存。"}
    return None


def save_task(config: Config, task_id: str) -> dict:
    from cta.service import TaskNotFound, TaskService

    price = load_price_table(config.project_root)
    spec = price.mode("save")
    with open_store(config.db_path) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise TaskNotFound(task_id)
        output = latest_output(conn, task_id)
        if output is None:
            raise SaveBlocked("no_output", "任务还没有可保存的完整方案草稿。")
        existing = latest_save(conn, task_id)
        if existing is not None and existing["output_id"] == output["output_id"] and existing["verified"]:
            document = TaskService(config).status(task_id)
            document["duplicate"] = True
            document["save"] = existing
            return document

        blocked = _preconditions(conn, task, output)
        if blocked is not None:
            document = TaskService(config).status(task_id)
            document["save"] = None
            document["save_error"] = blocked
            document["duplicate"] = False
            return document
        guard = _guard(conn, task, spec.cost_cents, spec.duration_ms)
        if guard is not None:
            document = TaskService(config).status(task_id)
            document["save"] = None
            document["save_error"] = guard
            document["duplicate"] = False
            return document

        attempt_id = start_attempt(
            conn,
            task=task,
            kind="save_campaign",
            mode="save",
            cost_cents=spec.cost_cents,
            duration_ms=spec.duration_ms,
            pricing_version=price.version,
            meta={"output_id": output["output_id"], "output_version": output["version"]},
        )
        conn.commit()

        review = latest_review(conn, task_id)
        verified_at = utc_now_iso()
        save = create_save(
            conn,
            task_id=task_id,
            output_id=output["output_id"],
            output_version=output["version"],
            attempt_id=attempt_id,
            status="verified",
            files=[],
            verified=False,
        )
        conn.commit()
        try:
            if os.environ.get("CTA_FAIL_SAVE"):
                raise OSError("injected save failure")
            files = build_files(
                conn,
                config,
                task,
                output,
                review,
                {**save, "verified": True, "status": "verified", "verified_at": verified_at},
                include_trace=False,
            )
            ok, checked = verify_records(files)
            if not ok:
                raise OSError("导出文件校验失败")
            conn.execute(
                "UPDATE saves SET files_json = ?, verified = 1, verified_at = ? WHERE id = ?",
                (json.dumps(checked, ensure_ascii=False), verified_at, save["save_id"]),
            )
            conn.commit()
            trace_file = build_trace_file(conn, config, task)
            checked = checked + [trace_file]
            conn.execute(
                "UPDATE saves SET files_json = ? WHERE id = ?",
                (json.dumps(checked, ensure_ascii=False), save["save_id"]),
            )
            conn.execute(
                "UPDATE tasks SET status = 'complete', phase = 'complete', reason = ?, updated_at = ?, paused = 0 WHERE id = ?",
                (f"已核实保存产物 v{output['version']} 及交付文件。", utc_now_iso(), task_id),
            )
            finish_attempt(
                conn,
                attempt_id,
                status="success",
                meta={"output_id": output["output_id"], "save_id": save["save_id"], "files": len(checked)},
            )
            conn.commit()
            document = TaskService(config).status(task_id)
            document["duplicate"] = False
            document["save"] = latest_save(conn, task_id)
            return document
        except Exception as exc:  # noqa: BLE001 - save failure is recorded, not hidden
            conn.execute(
                "UPDATE saves SET status = 'failed', verified = 0, error = ? WHERE id = ?",
                (str(exc)[:800], save["save_id"]),
            )
            conn.execute(
                "UPDATE tasks SET status = 'incomplete', phase = 'save_failed', reason = ?, updated_at = ? WHERE id = ?",
                (f"保存失败：{str(exc)[:400]}", utc_now_iso(), task_id),
            )
            finish_attempt(conn, attempt_id, status="failed", error=str(exc)[:500])
            conn.commit()
            document = TaskService(config).status(task_id)
            document["duplicate"] = False
            document["save"] = latest_save(conn, task_id)
            document["save_error"] = {"code": "save_failed", "message": str(exc)[:800]}
            return document
