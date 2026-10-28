"""Explicit import of an operator-edited Markdown campaign.

An import never watches files or merges silently: it validates the base version,
required sections and references, then creates a new pending-review output.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.loop import _validate_campaign
from cta.materials import load_catalog
from cta.repository import append_message, create_output, latest_output, new_id
from cta.service import STATUS_IN_PROGRESS, TaskNotFound, TaskService

REQUIRED_SECTIONS = [
    "活动概述",
    "创作策略",
    "内容安排",
    "主视频脚本",
    "素材用途",
    "制作步骤",
    "证据关联",
    "必要待确认项",
]
STATUS_MAP = {"已有": "existing", "待获取": "to_acquire", "待制作": "to_create"}
META_RE = re.compile(r"<!--\s*cta:(\{.*?\})\s*-->")


class ImportDraftError(ValueError):
    def __init__(self, code: str, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


def _section(lines: list[str], title: str) -> list[str]:
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"## {title}" or stripped.startswith(f"## {title}（"):
            start = index + 1
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return lines[start:end]


def _bullet_map(lines: list[str], prefix: str = "- ") -> dict[str, str]:
    result = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        body = stripped[len(prefix):]
        if "：" in body:
            key, value = body.split("：", 1)
            result[key.strip()] = value.strip()
    return result


def parse_markdown(text: str) -> tuple[dict, dict]:
    lines = text.splitlines()
    metadata_match = META_RE.search(text)
    if metadata_match is None:
        raise ImportDraftError("missing_metadata", "文件缺少 cta 元数据，不能确定任务与基稿版本。")
    try:
        metadata = json.loads(metadata_match.group(1))
    except json.JSONDecodeError as exc:
        raise ImportDraftError("missing_metadata", f"元数据不是合法 JSON：{exc}") from exc
    for section in REQUIRED_SECTIONS:
        if not _section(lines, section):
            raise ImportDraftError("missing_section", f"缺少必要章节：## {section}")

    title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    activity_lines = _section(lines, "活动概述")
    activity_map = _bullet_map(activity_lines)
    content_items = []
    for line in _section(lines, "内容安排"):
        match = re.match(r"^\s*\d+\.\s*(.*?)｜(.*?)｜\s*(\d+)\s*秒\s*｜\s*(.*)$", line)
        if match:
            content_items.append(
                {
                    "title": match.group(1).strip(),
                    "purpose": match.group(2).strip(),
                    "duration_s": int(match.group(3)),
                    "platform": match.group(4).strip(),
                }
            )
    strategy_lines = _section(lines, "创作策略")
    strategy_map = _bullet_map(strategy_lines)
    alternatives = []
    for line in strategy_lines:
        match = re.match(r"^\s*-\s*未选\s*(.*?)：(.*)$", line)
        if match:
            alternatives.append({"name": match.group(1).strip(), "why_not": match.group(2).strip()})

    script_lines = _section(lines, "主视频脚本")
    duration_match = re.search(r"##\s*主视频脚本（(\d+)\s*秒）", text)
    duration = int(duration_match.group(1)) if duration_match else 30
    segments = []
    current = None
    for line in script_lines:
        header = re.match(r"^###\s*(\d+)\.\s*(.*)$", line)
        if header:
            if current is not None:
                segments.append(current)
            current = {
                "index": int(header.group(1)),
                "time_range": header.group(2).strip(),
                "visual": "",
                "audio": "",
                "subtitle": "",
                "material_ids": [],
                "evidence_ids": [],
            }
            continue
        if current is None:
            continue
        match = re.match(r"^-\s*(画面|音频|字幕|素材|证据)：(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "画面":
            current["visual"] = value
        elif key == "音频":
            current["audio"] = value
        elif key == "字幕":
            current["subtitle"] = value
        elif key == "素材":
            current["material_ids"] = [] if value == "无" else [item.strip() for item in value.split(",") if item.strip()]
        elif key == "证据":
            current["evidence_ids"] = [] if value == "无" else [item.strip() for item in value.split(",") if item.strip()]
    if current is not None:
        segments.append(current)

    materials = []
    for line in _section(lines, "素材用途"):
        match = re.match(r"^-\s*(.*?)（(已有|待获取|待制作)）：(.*?)；位置：(.*?)；权利/状态：(.*)$", line)
        if match:
            materials.append(
                {
                    "name": match.group(1).strip(),
                    "material_id": "",
                    "status": STATUS_MAP[match.group(2)],
                    "use": match.group(3).strip(),
                    "position": match.group(4).strip(),
                    "rights_note": match.group(5).strip(),
                }
            )
    steps = []
    for line in _section(lines, "制作步骤"):
        match = re.match(r"^\s*\d+\.\s*(.*?)｜(.*?)｜(.*?)｜(.*)$", line)
        if match:
            steps.append(
                {
                    "step": match.group(1).strip(),
                    "owner": match.group(2).strip(),
                    "timing": match.group(3).strip(),
                    "note": match.group(4).strip(),
                }
            )
    evidence = []
    for line in _section(lines, "证据关联"):
        match = re.match(
            r"^-\s*\[(.*?)\]\s*(.*?)；依据\s*(.*?)「(.*?)」；范围：(.*?)；用途：(.*)$", line
        )
        if match:
            evidence.append(
                {
                    "id": match.group(1).strip(),
                    "claim": match.group(2).strip(),
                    "material_id": match.group(3).strip(),
                    "quote": match.group(4),
                    "scope": match.group(5).strip(),
                    "use": match.group(6).strip(),
                }
            )
    questions = []
    for line in _section(lines, "必要待确认项"):
        match = re.match(r"^-\s*\[(关键阻塞|待确认)\]\s*(.*?)；影响：(.*)$", line)
        if match:
            questions.append(
                {
                    "question": match.group(2).strip(),
                    "impact": match.group(3).strip(),
                    "blocking": match.group(1) == "关键阻塞",
                }
            )
    campaign = {
        "strategy": {
            "name": strategy_map.get("选定", ""),
            "rationale": strategy_map.get("理由", ""),
            "alternatives": alternatives,
        },
        "activity": {
            "goal": activity_map.get("目标", ""),
            "audience": activity_map.get("受众", ""),
            "theme": title or activity_map.get("主题", ""),
            "format": activity_map.get("形式", ""),
            "content_items": content_items,
        },
        "script": {"duration_s": duration, "segments": segments},
        "materials": materials,
        "production_steps": steps,
        "evidence": evidence,
        "open_questions": questions,
    }
    return campaign, metadata


def import_draft(config: Config, task_id: str, file_path: str | Path) -> dict:
    path = Path(file_path)
    if not path.is_file():
        raise ImportDraftError("file_not_found", f"导入文件不存在：{path}")
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8")
    campaign, metadata = parse_markdown(text)
    with open_store(config.db_path) as conn, transaction(conn):
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise TaskNotFound(task_id)
        current_output = latest_output(conn, task["id"])
        if current_output is None:
            raise ImportDraftError("no_base_output", "任务还没有可编辑的完整方案草稿。")
        if metadata.get("task_id") != task["id"]:
            raise ImportDraftError("wrong_task", "导入文件属于其他任务。")
        if metadata.get("input_version") != task["input_version"]:
            raise ImportDraftError("stale_input", "导入文件基于旧任务输入；请重新导出最新稿。")
        if (
            metadata.get("output_id") != current_output["output_id"]
            or metadata.get("output_version") != current_output["version"]
        ):
            raise ImportDraftError("stale_base", "导入文件基于旧产物版本；不回写，请基于最新稿处理。")

        audience = campaign["activity"].get("audience", "")
        base_audience = current_output["content"].get("activity", {}).get("audience", "")
        if audience and base_audience and audience != base_audience:
            raise ImportDraftError(
                "semantic_change_requires_update",
                "导入稿改变了已确认受众；请先用 update 形成新任务版本，不能把正文变化静默升级为授权。",
            )

        catalog = load_catalog(config.project_root)
        for material in campaign.get("materials", []):
            if not material.get("material_id"):
                match = next(
                    (item for item in catalog.materials.values() if item.title == material.get("name")), None
                )
                if match is not None:
                    material["material_id"] = match.id
        normalized, errors = _validate_campaign(campaign, catalog)
        if errors:
            raise ImportDraftError("invalid_campaign", "导入稿未通过校验。", errors[:12])

        output = create_output(
            conn,
            task_id=task_id,
            kind="campaign",
            content=normalized,
            content_md=META_RE.sub("", text).strip() + "\n",
            input_version=int(task["input_version"]),
            attempt_id=None,
        )
        conn.execute("UPDATE outputs SET status = 'pending_review' WHERE id = ?", (output["output_id"],))
        conn.execute(
            "UPDATE tasks SET status = ?, phase = 'draft_ready', reason = ?, updated_at = ? WHERE id = ?",
            (STATUS_IN_PROGRESS, f"人工稿已导入为 v{output['version']}，等待内容核验。", utc_now_iso(), task_id),
        )
        conn.execute(
            "INSERT INTO imports (id, task_id, output_id, base_output_id, file_path, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("i_"),
                task_id,
                output["output_id"],
                current_output["output_id"],
                str(path),
                hashlib.sha256(raw_bytes).hexdigest(),
                utc_now_iso(),
            ),
        )
        append_message(
            conn,
            task_id,
            "user",
            f"[人工稿导入] 基于 v{current_output['version']} 形成 v{output['version']}；文件 {path}；等待核验。",
            meta={"kind": "human_import", "output_version": output["version"]},
        )
        import_info = {
            "output_id": output["output_id"],
            "output_version": output["version"],
            "base_output_id": current_output["output_id"],
            "file": str(path),
        }
    document = TaskService(config).status(task_id)
    document["import"] = import_info
    return document
