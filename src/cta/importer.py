"""Explicit import of an operator-edited Markdown campaign.

An import never watches files or merges silently: it validates the base version,
required sections and references, then creates a new pending-review output.
"""

from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from pathlib import Path

from cta.artifacts import _script_errors
from cta.campaign import submit_campaign
from cta.config import Config, utc_now_iso
from cta.db import open_store, transaction
from cta.materials import load_task_catalog
from cta.render import DRAFT_NOTICE, VERIFIED_NOTICE, campaign_markdown
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
    """Parse the editable rendering without inventing omitted identifiers or evidence."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    metadata_matches = list(META_RE.finditer(text))
    if not metadata_matches:
        raise ImportDraftError("missing_metadata", "文件缺少 cta 元数据，不能确定任务与基稿版本。")
    if len(metadata_matches) != 1 or text[metadata_matches[0].end():].strip():
        raise ImportDraftError("invalid_campaign", "cta 元数据必须唯一且位于文件末尾。")
    metadata_match = metadata_matches[0]
    try:
        metadata = json.loads(metadata_match.group(1))
    except json.JSONDecodeError as exc:
        raise ImportDraftError("missing_metadata", f"元数据不是合法 JSON：{exc}") from exc
    headings = [line.strip() for line in lines if line.startswith("## ")]
    for section in REQUIRED_SECTIONS:
        matches = [heading for heading in headings if heading == f"## {section}" or heading.startswith(f"## {section}（")]
        if not matches:
            raise ImportDraftError("missing_section", f"缺少必要章节：## {section}")
        if len(matches) != 1:
            raise ImportDraftError("invalid_campaign", f"章节重复：## {section}")
    if len(headings) != len(REQUIRED_SECTIONS):
        raise ImportDraftError("invalid_campaign", "导入稿包含无法识别的章节。")

    def entries(section):
        return [line.strip() for line in _section(lines, section)
                if line.strip() and not META_RE.fullmatch(line.strip())]

    def reject(section, line):
        raise ImportDraftError("invalid_campaign", f"{section} 中无法解析：{line}")

    def evidence_items(rows, section):
        result = []
        for line in rows:
            if line == "- 无":
                if len(rows) != 1:
                    reject(section, line)
                continue
            match = re.fullmatch(r"- \[(.*?)\] (.*?)；依据 (.*?)（版本 (.*?)）「(.*?)」；范围：(.*?)；用途：(.*)", line)
            if not match:
                reject(section, line)
            result.append(dict(zip(("id", "claim", "material_id", "material_version", "quote", "scope", "use"), match.groups())))
        return result

    activity_rows = entries("活动概述")
    activity_map = _bullet_map(activity_rows)
    if len(activity_rows) != 4 or set(activity_map) != {"目标", "受众", "形式", "主题"}:
        reject("活动概述", "请保留目标、受众、形式和主题")
    preamble = lines[:next(i for i, line in enumerate(lines) if line.strip() == "## 活动概述" or line.strip().startswith("## 活动概述（"))]
    titles = [line[2:].strip() for line in preamble if line.startswith("# ")]
    if len(titles) != 1 or any(line.strip() and not line.startswith("# ") and line.strip() not in (DRAFT_NOTICE, VERIFIED_NOTICE) for line in preamble):
        reject("活动概述", "标题前含无法归入字段的正文")
    title = titles[0]
    if title != activity_map["主题"]:
        reject("活动概述", "标题与主题不一致，请同步修改")
    strategy_rows = entries("创作策略")
    strategy_map = _bullet_map(strategy_rows)
    alternatives = []
    for line in strategy_rows:
        if line.startswith(("- 选定：", "- 理由：")):
            continue
        match = re.fullmatch(r"- 未选 (.*?)：(.*)", line)
        if not match:
            reject("创作策略", line)
        alternatives.append({"name": match.group(1), "why_not": match.group(2)})
    if sum(line.startswith("- 选定：") for line in strategy_rows) != 1 or sum(line.startswith("- 理由：") for line in strategy_rows) != 1:
        reject("创作策略", "缺少方向或理由")
    content_items = []
    for line in entries("内容安排"):
        match = re.fullmatch(r"\d+\. (.*?)｜(.*?)｜(\d+) 秒｜(.*)", line)
        if not match:
            reject("内容安排", line)
        content_items.append(dict(zip(("title", "purpose", "duration_s", "platform"),
                                      (match[1], match[2], int(match[3]), match[4]))))

    script_rows = entries("主视频脚本")
    duration_match = re.search(r"^## 主视频脚本（(\d+) 秒）$", text, re.MULTILINE)
    if not duration_match or len(script_rows) < 3 or not script_rows[0].startswith("脚本标题：") or not script_rows[1].startswith("脚本方向："):
        reject("主视频脚本", "缺少时长、标题或方向")
    segments, current, evidence_rows = [], None, []
    in_evidence = False
    for line in script_rows[2:]:
        if line == "### 脚本证据":
            if current is not None:
                segments.append(current)
                current = None
            in_evidence = True
            continue
        if in_evidence:
            evidence_rows.append(line)
            continue
        header = re.fullmatch(r"### (.*?)\. (\d+) 秒", line)
        if header:
            if current is not None:
                segments.append(current)
            current = {"segment_id": header[1], "duration_seconds": int(header[2])}
            continue
        match = re.fullmatch(r"- (画面|音频|字幕|证据)：(.*)", line)
        if not match or current is None:
            reject("主视频脚本", line)
        key = {"画面": "visual", "音频": "voiceover", "字幕": "on_screen_text", "证据": "evidence_ids"}[match[1]]
        if key in current:
            reject("主视频脚本", line)
        current[key] = ([] if match[2] == "无" else [item.strip() for item in match[2].split(",")]) if key == "evidence_ids" else match[2]
    if not in_evidence or not evidence_rows:
        reject("主视频脚本", "缺少脚本证据章节")
    script = {"title": script_rows[0].split("：", 1)[1], "direction": script_rows[1].split("：", 1)[1],
              "duration_seconds": int(duration_match[1]), "segments": segments,
              "evidence": evidence_items(evidence_rows, "脚本证据")}
    materials = []
    for line in entries("素材用途"):
        match = re.fullmatch(r"- (.*?)（(已有|待获取|待制作)）：(.*?)；位置：(.*?)；权利/状态：(.*?)；素材ID：(.*)", line)
        if not match:
            reject("素材用途", line)
        materials.append({"name": match[1], "status": STATUS_MAP[match[2]], "use": match[3],
                          "position": match[4], "rights_note": match[5], "material_id": match[6]})
    steps = []
    for line in entries("制作步骤"):
        match = re.fullmatch(r"\d+\. (.*?)｜(.*?)｜(.*?)｜(.*)", line)
        if not match:
            reject("制作步骤", line)
        steps.append(dict(zip(("step", "owner", "timing", "note"), match.groups())))
    questions = []
    question_rows = entries("必要待确认项")
    for line in question_rows:
        if line == "- 无":
            if len(question_rows) != 1:
                reject("必要待确认项", line)
            continue
        match = re.fullmatch(r"- \[(关键阻塞|待确认)\] (.*?)；影响：(.*)", line)
        if not match:
            reject("必要待确认项", line)
        questions.append({"question": match[2], "impact": match[3], "blocking": match[1] == "关键阻塞"})
    campaign = {"strategy": {"name": strategy_map["选定"], "rationale": strategy_map["理由"], "alternatives": alternatives},
                "activity": {"goal": activity_map["目标"], "audience": activity_map["受众"],
                             "theme": activity_map["主题"], "format": activity_map["形式"], "content_items": content_items},
                "script": script, "materials": materials, "production_steps": steps,
                "evidence": evidence_items(entries("证据关联"), "证据关联"), "open_questions": questions}
    def decode(value):
        if isinstance(value, str):
            return unescape(value)
        if isinstance(value, list):
            return [decode(item) for item in value]
        if isinstance(value, dict):
            return {key: decode(item) for key, item in value.items()}
        return value

    return decode(campaign), metadata


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

        base = current_output["content"]
        expected_fields = {key: base[key] for key in ("strategy", "activity", "script", "materials", "production_steps", "evidence", "open_questions")}
        try:
            rendered_base, _ = parse_markdown(campaign_markdown(base) + "\n<!-- cta:{} -->\n")
        except ImportDraftError as exc:
            raise ImportDraftError("invalid_campaign", "基稿包含无法无损表达的字段，无法安全导入。", [exc.message]) from exc
        if rendered_base != expected_fields:
            raise ImportDraftError("invalid_campaign", "基稿包含无法无损表达的字段，无法安全导入。")
        catalog = load_task_catalog(conn, task, config.project_root)
        errors = _script_errors(campaign["script"], catalog)
        if errors:
            raise ImportDraftError("invalid_campaign", "人工脚本未通过校验。", errors)
        old_ref = base["script_ref"]
        ref = old_ref
        if campaign["script"] != base["script"]:
            old_script = conn.execute("SELECT content_json FROM outputs WHERE task_id = ? AND id = ? AND version = ? AND kind = 'script'",
                                      (task_id, old_ref["id"], old_ref["version"])).fetchone()
            previous = json.loads(old_script["content_json"])
            script_output = create_output(conn, task_id=task_id, kind="script",
                                          content={"script": campaign["script"], "brief_version": previous["brief_version"],
                                                   "revises_script_ref": old_ref, "source": "human_import"},
                                          content_md=None, input_version=task["input_version"], attempt_id=None)
            ref = {"id": script_output["output_id"], "version": script_output["version"]}
        fields = {key: value for key, value in campaign.items() if key != "script"}
        accepted = submit_campaign(conn, task, {"script_ref": ref, "campaign": fields}, catalog)
        if not accepted["ok"]:
            raise ImportDraftError("invalid_campaign", "导入稿未通过校验。", accepted.get("details", []))
        output = {"output_id": accepted["campaign_ref"]["id"], "version": accepted["campaign_ref"]["version"]}
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
            "[人工稿导入] " + json.dumps({"base_output_id": current_output["output_id"], "output_id": output["output_id"], "output_version": output["version"], "input_version": task["input_version"], "campaign": latest_output(conn, task_id)["content"], "file": str(path), "instruction": "这是当前完整人工稿，须重新核验；旧评审及处置不继承。"}, ensure_ascii=False),
            meta={"kind": "human_import", "output_version": output["version"], "imported_output_id": output["output_id"]},
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
