"""Export the authoritative SQLite state as Markdown, JSON and JSONL Trace."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cta.config import Config
from cta.render import campaign_markdown
from cta.repository import (
    latest_brief,
    latest_estimate,
    latest_output,
    latest_review,
    latest_save,
    latest_strategy_selection,
    list_stops,
    task_llm_usage,
    task_spent,
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def execution_report_markdown(
    conn,
    task,
    output: dict,
    review: dict | None,
    save: dict | None,
    attempt_status_overrides: dict[str, str] | None = None,
) -> str:
    from datetime import datetime, timedelta

    input_row = conn.execute(
        "SELECT * FROM task_inputs WHERE task_id = ? AND version = ?", (task["id"], task["input_version"])
    ).fetchone()
    usage = task_llm_usage(conn, task["id"])
    spent = task_spent(conn, task["id"])
    attempts = conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY rowid", (task["id"],)).fetchall()
    ledger = conn.execute("SELECT * FROM ledger WHERE task_id = ? ORDER BY id", (task["id"],)).fetchall()
    brief = latest_brief(conn, task["id"])
    selection = latest_strategy_selection(conn, task["id"])
    estimate = latest_estimate(conn, task["id"])
    stops = list_stops(conn, task["id"])
    activity_entries = [
        item
        for item in (output or {}).get("content", {}).get("materials", [])
        if item.get("status") in ("to_acquire", "to_create")
    ]
    sim_complete = datetime.fromisoformat(task["sim_start"]) + timedelta(milliseconds=task["sim_elapsed_ms"])
    try:
        wall_seconds = (
            datetime.fromisoformat(task["updated_at"]) - datetime.fromisoformat(task["created_at"])
        ).total_seconds()
        wall_text = f"{wall_seconds:.0f} s"
    except ValueError:
        wall_text = "未知"
    plan = [
        ("业务简报", "done" if int(task["brief_version"]) > 0 else "pending"),
        ("完整方案草稿", "done" if output is not None else "pending"),
        ("内容核验", "done" if review is not None and review["status"] == "complete" else "pending"),
        ("核实保存", "done" if save is not None and save["verified"] else "pending"),
    ]
    lines = [
        f"# 执行报告：{input_row['topic']}",
        "",
        f"- 任务：`{task['id']}`（{task['mode']} 模式）",
        f"- 状态：`{task['status']}`；阶段：`{task['phase']}`",
        f"- 原因：{task['reason']}",
        f"- 任务版本：{task['input_version']}；产物版本：{None if output is None else output['version']}",
        f"- 模拟时钟：起点 {task['sim_start']}；已用 {task['sim_elapsed_ms']} ms；模拟完成时刻 {sim_complete.isoformat()}；已用轮数 {task['used_rounds']}/12",
        f"- 真实墙钟：从创建到最近更新 {wall_text}；API 采得累计耗时 {usage['elapsed_ms']} ms",
        (
            f"- 模拟费用：预算 {input_row['budget_cents']} 分；已发生 {spent} 分；剩余 "
            f"{None if input_row['budget_cents'] is None else input_row['budget_cents'] - spent} 分"
        ),
        (
            f"- 真实模型请求：{usage['requests']} 次（真实 {usage['real_requests']}，Mock {usage['mock_requests']}）；"
            f"输入 {usage['prompt_tokens']} / 输出 {usage['completion_tokens']} tokens；缓存命中 {usage['cache_hit_tokens']} / "
            f"未命中 {usage['cache_miss_tokens']}；未知用量请求 {usage['unknown_usage_requests']}；金额：未知（无可靠单价来源）"
        ),
        (
            "- 活动费用："
            + (
                "未知（方案含待获取/待制作素材，但输入与材料未给出金额）"
                if activity_entries
                else "不涉及（未涉及活动制作或投放预算/实际支出）"
            )
        ),
        f"- 报告生成时间（真实）：{(save or {}).get('verified_at') or task['updated_at']}",
        "",
        "## 任务计划与步骤",
        "",
    ]
    for title, status in plan:
        lines.append(f"- {title}：{status}")
    for step in (output or {}).get("content", {}).get("activity", {}).get("content_items", []):
        lines.append(f"- 内容安排：{step.get('title')}（{step.get('duration_s')} 秒，{step.get('platform')}）")
    for attempt in attempts:
        status = (attempt_status_overrides or {}).get(attempt["id"], attempt["status"])
        lines.append(
            f"- 执行尝试 `{attempt['id']}`：{attempt['kind']} / {attempt['mode']} / {status}；"
            f"费用 {attempt['cost_cents']} 分；耗时 {attempt['duration_ms']} ms；错误 {attempt['error'] or '无'}"
        )
    lines += ["", "## 创作策略与反馈", ""]
    if selection is None:
        lines.append("- 尚未记录策略选择。")
    else:
        lines += [
            (
                f"- 选定方向：{selection['direction']}；检索模式：{selection['search_mode']}；"
                f"生成模式：{selection['generation_mode']}；已知剩余检索：{selection['search_queries']} 次；"
                f"含返修复核：{'是' if selection['include_revision'] else '否'}"
            ),
            f"- 选择理由：{selection['rationale']}",
            (
                f"- 改进投入：{'是' if selection['improvement'] else '否'}；"
                f"预期收益：{selection['expected_benefit'] or '不适用'}"
            ),
            (
                f"- 完整路径估算：{selection['total_cost_cents']} 分 / {selection['total_duration_ms']} ms；"
                f"策略表 {selection['strategy_version']}；费用表 {selection['pricing_version']}；预留不是实际支出"
            ),
        ]
    if estimate is not None:
        lines.append(
            f"- 最近估算：{estimate['kind']}；{estimate['total_cost_cents']} 分；"
            f"可行：{'是' if estimate['feasible'] else '否'}；已发生 {estimate['incurred_cents']} 分；"
            f"剩余预算 {estimate['remaining_cents']} 分"
        )
    for stop in stops:
        for gap in stop["gaps"]:
            lines.append(
                f"- 停止缺口（{stop['kind']}）：{gap.get('gap')}；影响：{gap.get('impact') or '未说明'}"
            )
        if not stop["gaps"]:
            lines.append(f"- 停止（{stop['kind']}）：{stop['reason']}")
    lines += ["", "## 成本口径与账本", ""]
    lines.append(
        f"- 模拟工具费用：单位 CNY_cent；来源 pricing:{task['pricing_version']}；"
        f"已发生 {spent} 分；估算/预留单列，不计入已发生"
    )
    lines.append(
        f"- 真实模型用量：单位 request/token；来源 API response usage；"
        f"金额未知（不把 token 用量换算为金额）；真实请求 {usage['real_requests']} 次"
    )
    lines.append(
        "- 活动费用："
        + (
            "未知（方案素材涉及待获取/待制作项，未提供金额）"
            if activity_entries
            else "不涉及"
        )
    )
    for row in ledger:
        lines.append(
            f"- {row['created_at']}｜{row['category']}｜{row['amount_cents']} {row['unit']}｜"
            f"{row['nature']}｜{row['source']}｜{row['note'] or ''}"
        )
    lines += ["", "## 工具结果", ""]
    tool_rows = conn.execute(
        "SELECT name, content, created_at FROM messages WHERE task_id = ? AND agent = 'main' AND role = 'tool' ORDER BY seq",
        (task["id"],),
    ).fetchall()
    for row in tool_rows:
        lines.append(f"- {row['created_at']}｜{row['name']}｜{(row['content'] or '')[:300]}")
    lines += ["", "## 简要决策依据", ""]
    decision_rows = conn.execute(
        "SELECT content, created_at FROM messages WHERE task_id = ? AND agent = 'main' AND role = 'assistant' ORDER BY seq",
        (task["id"],),
    ).fetchall()
    for row in decision_rows:
        text = (row["content"] or "").strip()
        if text:
            lines.append(f"- {row['created_at']}｜{text[:240]}")
    lines += ["", "## 内容核验", ""]
    if review is None:
        lines.append("- 尚未核验。")
    else:
        lines.append(
            f"- 被审版本 v{review['output_version']}；结论 {review['verdict']}；完整性 {review['completeness']}；"
            f"请求 {review['requests_used']} 次；回读 {review['readbacks_used']} 次"
        )
        lines.append(f"- 摘要：{review['summary']}")
        for finding in review["findings"]:
            lines.append(
                f"- [{finding['severity']}] {finding['finding_id']} @ {finding['location']}：{finding['issue']}"
                f"；建议：{finding['suggestion']}；依据：{finding['evidence']}"
            )
    lines += ["", "## 保存与导出", ""]
    if save is None:
        lines.append("- 尚未核实保存。")
    else:
        lines.append(f"- 保存记录 `{save['save_id']}`：{save['status']}；已核实 {save['verified']}；版本 v{save['output_version']}")
        lines.append("- 交付文件及逐文件哈希见 saves 表与导出目录。")
    lines += ["", "## 缺口与限制", ""]
    gaps = (brief or {}).get("content", {}).get("unknowns", [])
    if not gaps:
        lines.append("- 简报未记录未知项。")
    for gap in gaps:
        lines.append(f"- [{'关键阻塞' if gap.get('blocking') else '待确认'}] {gap.get('question')}；影响：{gap.get('impact')}")
    for stop in stops:
        for gap in stop["gaps"]:
            lines.append(
                f"- [停止缺口/{stop['kind']}] {gap.get('gap')}；影响：{gap.get('impact') or '未说明'}"
            )
    lines += [
        "",
        "- 模拟按时不证明真实墙钟按时；制作可用不代表已制作成片。",
        "- 本报告不包含密钥、模型内部思维过程或未核实的成功声明。",
        "",
    ]
    return "\n".join(lines)


def trace_events(conn, task) -> list[dict]:
    events = [{"event": "task", "task_id": task["id"], "mode": task["mode"], "status": task["status"], "phase": task["phase"], "at": task["created_at"]}]
    for row in conn.execute("SELECT * FROM messages WHERE task_id = ? ORDER BY agent, seq", (task["id"],)):
        content = row["content"] or ""
        if row["role"] == "system":
            content = f"[system content retained, {len(content)} chars]"
        events.append(
            {
                "event": "message",
                "task_id": task["id"],
                "agent": row["agent"],
                "seq": row["seq"],
                "role": row["role"],
                "name": row["name"],
                "tool_call_id": row["tool_call_id"],
                "tool_calls": json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else [],
                "content": content[:4000],
                "at": row["created_at"],
            }
        )
    for row in conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        events.append({"event": "attempt", "task_id": task["id"], **dict(row)})
    for row in conn.execute("SELECT * FROM ledger WHERE task_id = ? ORDER BY id", (task["id"],)):
        events.append({"event": "ledger", "task_id": task["id"], **dict(row)})
    for row in conn.execute("SELECT * FROM llm_requests WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        item = dict(row)
        item.pop("meta_json", None)
        events.append({"event": "llm_request", "task_id": task["id"], **item})
    for row in conn.execute("SELECT * FROM reviews WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        item = dict(row)
        item["findings"] = json.loads(item.pop("findings_json"))
        item.pop("evidence_gaps_json", None)
        events.append({"event": "review", "task_id": task["id"], **item})
    for row in conn.execute("SELECT * FROM saves WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        events.append(
            {
                "event": "save",
                "task_id": task["id"],
                "save_id": row["id"],
                "output_id": row["output_id"],
                "output_version": row["output_version"],
                "status": row["status"],
                "verified": bool(row["verified"]),
                "verified_at": row["verified_at"],
                "created_at": row["created_at"],
            }
        )
    for row in conn.execute("SELECT * FROM estimates WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        events.append(
            {
                "event": "estimate",
                "task_id": task["id"],
                "estimate_id": row["id"],
                "kind": row["kind"],
                "direction": row["direction"],
                "search_mode": row["search_mode"],
                "generation_mode": row["generation_mode"],
                "search_queries": row["search_queries"],
                "include_revision": bool(row["include_revision"]),
                "strategy_version": row["strategy_version"],
                "pricing_version": row["pricing_version"],
                "total_cost_cents": row["total_cost_cents"],
                "total_duration_ms": row["total_duration_ms"],
                "incurred_cents": row["incurred_cents"],
                "remaining_cents": row["remaining_cents"],
                "feasible": bool(row["feasible"]),
                "created_at": row["created_at"],
            }
        )
    for row in conn.execute("SELECT * FROM strategy_selections WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        events.append(
            {
                "event": "strategy_selection",
                "task_id": task["id"],
                "selection_id": row["id"],
                "direction": row["direction"],
                "search_mode": row["search_mode"],
                "generation_mode": row["generation_mode"],
                "search_queries": row["search_queries"],
                "include_revision": bool(row["include_revision"]),
                "improvement": bool(row["improvement"]),
                "expected_benefit": row["expected_benefit"],
                "rationale": row["rationale"],
                "strategy_version": row["strategy_version"],
                "pricing_version": row["pricing_version"],
                "path": json.loads(row["path_json"]),
                "total_cost_cents": row["total_cost_cents"],
                "total_duration_ms": row["total_duration_ms"],
                "incurred_cents": row["incurred_cents"],
                "remaining_cents": row["remaining_cents"],
                "created_at": row["created_at"],
            }
        )
    for row in conn.execute("SELECT * FROM stops WHERE task_id = ? ORDER BY rowid", (task["id"],)):
        events.append(
            {
                "event": "stop",
                "task_id": task["id"],
                "stop_id": row["id"],
                "kind": row["kind"],
                "reason": row["reason"],
                "gaps": json.loads(row["gaps_json"]),
                "created_at": row["created_at"],
            }
        )
    return events


def build_files(
    conn,
    config: Config,
    task,
    output: dict,
    review: dict | None,
    save: dict | None,
    *,
    include_trace: bool = True,
    report_task=None,
    attempt_status_overrides: dict[str, str] | None = None,
) -> list[dict]:
    task_dir = config.exports_dir / task["id"]
    task_dir.mkdir(parents=True, exist_ok=True)
    campaign = output["content"]
    version = output["version"]
    files = [
        (task_dir / f"campaign-v{version}.md", output["content_md"] or campaign_markdown(campaign)),
        (task_dir / f"campaign-v{version}.json", json.dumps(campaign, ensure_ascii=False, indent=2) + "\n"),
        (
            task_dir / "execution-report.md",
            execution_report_markdown(
                conn,
                report_task or task,
                output,
                review,
                save,
                attempt_status_overrides=attempt_status_overrides,
            ),
        ),
    ]
    if include_trace:
        files.append((task_dir / "trace.jsonl", trace_text(conn, task)))
    brief = latest_brief(conn, task["id"])
    if brief is not None:
        files.append(
            (task_dir / f"brief-v{brief['version']}.json", json.dumps(brief["content"], ensure_ascii=False, indent=2) + "\n")
        )
    records = []
    for path, content in files:
        _write_atomic(path, content)
        records.append({"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size})
    return records


def trace_text(conn, task) -> str:
    return "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in trace_events(conn, task))


def build_trace_file(conn, config: Config, task) -> dict:
    path = config.exports_dir / task["id"] / "trace.jsonl"
    _write_atomic(path, trace_text(conn, task))
    return record_file(path)


def record_file(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def verify_records(records: list[dict]) -> tuple[bool, list[dict]]:
    checked = []
    ok = True
    for item in records:
        path = Path(item["path"])
        exists = path.is_file()
        digest = sha256_file(path) if exists else None
        good = exists and digest == item["sha256"]
        ok = ok and good
        checked.append({**item, "exists": exists, "current_sha256": digest, "ok": good})
    return ok, checked


def export_task(config: Config, task_id: str) -> dict:
    from cta.db import open_store
    from cta.service import TaskNotFound

    with open_store(config.db_path) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise TaskNotFound(task_id)
        output = latest_output(conn, task_id)
        if output is None:
            raise ValueError("任务还没有可导出的完整方案草稿。")
        review = latest_review(conn, task_id)
        save = latest_save(conn, task_id)
        records = build_files(conn, config, task, output, review, save)
        ok, checked = verify_records(records)
        return {
            "task_id": task_id,
            "output_version": output["version"],
            "save_id": None if save is None else save["save_id"],
            "verified": ok,
            "files": checked,
        }
