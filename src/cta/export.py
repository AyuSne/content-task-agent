"""Export the authoritative SQLite state as Markdown, JSON and JSONL Trace."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cta.config import Config
from cta.render import DRAFT_NOTICE, VERIFIED_NOTICE, campaign_markdown
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
    temp.write_text(content, encoding="utf-8", newline="")
    temp.replace(path)


def execution_report_markdown(
    conn,
    task,
    output: dict | None,
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
    from cta.campaign import campaign_brief

    brief = campaign_brief(conn, task["id"], output) if output is not None else latest_brief(conn, task["id"])
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
    from cta.repository import plan_view

    plan = plan_view(conn, task["id"])["steps"]
    lines = [
        f"# 执行报告：{input_row['topic']}",
        "",
        f"- 任务：`{task['id']}`（{task['mode']} 模式）",
        f"- 状态：`{task['status']}`；阶段：`{task['phase']}`",
        f"- 原因：{task['reason']}",
        f"- 任务版本：{task['input_version']}；产物版本：{None if output is None else output['version']}",
        f"- 模拟时钟：起点 {task['sim_start']}；已用 {task['sim_elapsed_ms']} ms；模拟完成时刻 {sim_complete.isoformat()}；已用轮数 {task['used_rounds']}/{task['max_rounds']}",
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
    status_labels = {"done": "已完成", "current": "进行中", "pending": "待开始"}
    if not plan:
        lines.append("- 尚未建立任务计划；阶段与执行事实见本报告其他部分。")
    for step in plan:
        dependencies = ", ".join(step["depends_on"]) or "无"
        refs = ", ".join(f"{ref['kind']}:{ref['id']}" for ref in step["references"]) or "无"
        lines.append(f"- {step['id']} / {step['title']}：{status_labels.get(step['status'], step['status'])}；依赖：{dependencies}；关联：{refs}")
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
        strategy = (output or {}).get("content", {}).get("strategy")
        if strategy:
            lines.append(f"- 方案方向：{strategy['name']}；理由：{strategy['rationale']}")
        else:
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
        "SELECT content, tool_calls_json, created_at FROM messages WHERE task_id = ? AND agent = 'main' AND role = 'assistant' ORDER BY seq",
        (task["id"],),
    ).fetchall()
    for row in decision_rows:
        text = (row["content"] or "").strip()
        if text:
            lines.append(f"- {row['created_at']}｜{text[:240]}")
        elif row["tool_calls_json"]:
            calls = [item.get("function", {}).get("name", "?") for item in json.loads(row["tool_calls_json"])]
            lines.append(f"- {row['created_at']}｜调用工具：{', '.join(calls)}")
    lines += ["", "## 内容核验", ""]
    reviews = conn.execute("SELECT * FROM reviews WHERE task_id = ? ORDER BY rowid", (task["id"],)).fetchall()
    if not reviews:
        lines.append("- 尚未核验。")
    for row in reviews:
        lines.append(
            f"- 评审 `{row['id']}`；被审版本 v{row['output_version']}；结论 {row['verdict']}；"
            f"完整性 {row['completeness']}；请求 {row['requests_used']} 次；回读 {row['readbacks_used']} 次"
        )
        lines.append(f"- 摘要：{row['summary']}")
        for finding in json.loads(row["findings_json"]):
            lines.append(
                f"- [{finding['severity']}] {finding['finding_id']} @ {finding['location']}：{finding['issue']}"
                f"；建议：{finding['suggestion']}；依据：{finding['evidence']}"
            )
    lines += ["", "## 评审处置", ""]
    rows = conn.execute("""SELECT dispositions.*, reviews.output_id, reviews.output_version FROM dispositions
                           JOIN reviews ON reviews.id = dispositions.review_id
                           WHERE reviews.task_id = ? ORDER BY dispositions.id""", (task["id"],)).fetchall()
    if not rows:
        lines.append("- 尚无处置记录。")
    for row in rows:
        evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else []
        lines.append(f"- 评审 {row['review_id']}，方案 {row['output_id']} v{row['output_version']}，意见 {row['finding_id']}：{row['decision']}；理由：{row['reason']}；依据：{json.dumps(evidence, ensure_ascii=False)}")
    lines.append("- 定位依据仅确认原文存在，语义是否支持处置仍须判断。")
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


def campaign_export_markdown(conn, output: dict, task, *, verified: bool = False) -> str:
    from cta.service import output_reuse_basis

    body = output["content_md"] or campaign_markdown(output["content"])
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    notice = VERIFIED_NOTICE if verified else DRAFT_NOTICE
    body = body.replace(DRAFT_NOTICE, notice).replace(VERIFIED_NOTICE, notice)
    metadata = {
        "task_id": task["id"],
        "output_id": output["output_id"],
        "output_version": output["version"],
        "input_version": task["input_version"] if output_reuse_basis(conn, task, output) else output["input_version"],
        "source_input_version": output["input_version"],
    }
    return body.rstrip() + "\n\n<!-- cta:" + json.dumps(metadata, ensure_ascii=False) + " -->\n"


def build_files(
    conn,
    config: Config,
    task,
    output: dict,
    review: dict | None,
    save: dict | None,
    *,
    include_report: bool = True,
    include_trace: bool = True,
    report_task=None,
    attempt_status_overrides: dict[str, str] | None = None,
) -> list[dict]:
    task_dir = config.exports_dir / task["id"]
    task_dir.mkdir(parents=True, exist_ok=True)
    campaign = output["content"]
    version = output["version"]
    files = [
        (task_dir / f"campaign-v{version}.md", campaign_export_markdown(
            conn, output, task, verified=bool(save and save["verified"] and save["output_id"] == output["output_id"]))),
        (task_dir / f"campaign-v{version}.json", json.dumps(campaign, ensure_ascii=False, indent=2) + "\n"),
    ]
    if include_report:
        files.append(
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
            )
        )
    if include_trace:
        files.append((task_dir / "trace.jsonl", trace_text(conn, task)))
    from cta.campaign import campaign_brief

    brief = campaign_brief(conn, task["id"], output)
    if brief is not None:
        files.append(
            (task_dir / f"brief-v{brief['version']}.json", json.dumps(brief["content"], ensure_ascii=False, indent=2) + "\n")
        )
    records = []
    for path, content in files:
        _write_atomic(path, content)
        records.append({"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size})
    return records


def build_report_file(conn, config: Config, task, output: dict | None, review: dict | None, save: dict | None) -> dict:
    path = config.exports_dir / task["id"] / "execution-report.md"
    _write_atomic(path, execution_report_markdown(conn, task, output, review, save))
    return record_file(path)


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


def export_task(config: Config, task_id: str, *, repair: bool = False) -> dict:
    from cta.db import open_store
    from cta.service import TaskNotFound

    with open_store(config.db_path) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise TaskNotFound(task_id)
        output = latest_output(conn, task_id)
        if output is None:
            records = [build_report_file(conn, config, task, None, None, None), build_trace_file(conn, config, task)]
            brief = latest_brief(conn, task_id)
            if brief is not None:
                path = config.exports_dir / task_id / f"brief-v{brief['version']}.json"
                _write_atomic(path, json.dumps(brief["content"], ensure_ascii=False, indent=2) + "\n")
                records.append(record_file(path))
            ok, checked = verify_records(records)
            return {"task_id": task_id, "output_version": None, "save_id": None,
                    "verified": ok, "delivery_complete": False, "files": checked, "repaired": False}
        review = latest_review(conn, task_id)
        save = latest_save(conn, task_id)
        if review is not None and review["output_id"] != output["output_id"]:
            review = None
        if save is not None and save["output_id"] != output["output_id"]:
            save = None
        if save is not None:
            rebuilt = False
            try:
                ok, checked = verify_records(save["files"]) if save["files"] else (False, [])
            except OSError:
                ok, checked = False, []
            if repair or (save["verified"] and ok):
                from cta.save import reconcile_task

                reconciliation = reconcile_task(config, task_id)
                rebuilt = reconciliation.get("reconcile", {}).get("reconciled", False)
                task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                save = latest_save(conn, task_id)
                ok, checked = verify_records(save["files"]) if save["verified"] else (False, [])
            elif save["verified"]:
                from cta.save import invalidate_export_verification

                invalidate_export_verification(conn, task_id, save["save_id"], "交付文件与保存记录不一致；需要显式修复或导入人工稿。")
                task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                save = latest_save(conn, task_id)
            if not save["verified"]:
                records = [build_report_file(conn, config, task, output, review, save), build_trace_file(conn, config, task)]
                _, process_files = verify_records(records)
                return {"task_id": task_id, "output_version": output["version"], "save_id": save["save_id"],
                        "verified": False, "delivery_complete": False, "files": process_files,
                        "existing_save_file_checks": checked, "save_status": save["status"], "repaired": False,
                        "message": "保留现有活动稿。保存未核实或已失败；请显式导入编辑，或核实/修复后决定业务重试。"}
            return {
                "task_id": task_id,
                "output_version": output["version"],
                "save_id": save["save_id"],
                "verified": ok,
                "delivery_complete": bool(ok and task["status"] == "complete"),
                "files": checked,
                "repaired": rebuilt,
            }
        target = config.exports_dir / task_id / f"campaign-v{output['version']}.md"
        if target.is_file() and not repair:
            actual = target.read_text(encoding="utf-8").rsplit("\n<!-- cta:", 1)[0].rstrip()
            expected = campaign_export_markdown(conn, output, task).rsplit("\n<!-- cta:", 1)[0].rstrip()
            if actual != expected:
                records = [build_report_file(conn, config, task, output, review, None), build_trace_file(conn, config, task)]
                _, checked = verify_records(records)
                return {"task_id": task_id, "output_version": output["version"], "save_id": None,
                        "verified": False, "delivery_complete": False, "files": checked, "repaired": False,
                        "message": "当前活动稿含外部编辑，已保留。请显式import-draft或export --repair。"}
        records = build_files(conn, config, task, output, review, save)
        ok, checked = verify_records(records)
        return {
            "task_id": task_id,
            "output_version": output["version"],
            "save_id": None if save is None else save["save_id"],
            "verified": ok,
            "delivery_complete": False,
            "files": checked,
            "repaired": False,
        }
