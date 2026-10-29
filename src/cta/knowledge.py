"""Versioned product working knowledge: load, hash and retain the actual text."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cta.config import find_project_root


def _knowledge_root(project_root: Path) -> Path:
    """Test-only override; normal runs use the project's versioned knowledge."""
    override = os.environ.get("CTA_KNOWLEDGE_DIR")
    return Path(override) if override else project_root / "knowledge"


@dataclass(frozen=True)
class KnowledgeDoc:
    knowledge_id: str
    title: str
    version: str
    path: Path
    content: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return {
            "knowledge_id": self.knowledge_id,
            "title": self.title,
            "version": self.version,
            "sha256": self.sha256,
            "path": str(self.path),
        }


def _manifest(project_root: Path) -> dict:
    return json.loads((_knowledge_root(project_root) / "manifest.json").read_text(encoding="utf-8"))


def load_doc(knowledge_id: str, project_root: Path | None = None, version: str | None = None) -> KnowledgeDoc:
    root = project_root or find_project_root()
    knowledge_root = _knowledge_root(root)
    docs = {item["id"]: item for item in _manifest(root)["docs"]}
    if knowledge_id not in docs:
        raise ValueError(f"unknown knowledge: {knowledge_id}")
    entry = docs[knowledge_id]
    selected_version = version or entry["version"]
    if not isinstance(selected_version, str) or not selected_version or any(c in selected_version for c in "/\\"):
        raise ValueError(f"unknown knowledge version: {knowledge_id} {selected_version}")
    path = knowledge_root / (entry["path"] if selected_version == entry["version"] else f"{knowledge_id}/{selected_version}.md")
    if not path.is_file():
        if version is not None:
            raise ValueError(f"unknown knowledge version: {knowledge_id} {version}")
        path = knowledge_root / entry["path"]
    content = path.read_text(encoding="utf-8")
    return KnowledgeDoc(
        knowledge_id=knowledge_id,
        title=entry["title"],
        version=selected_version,
        path=path,
        content=content,
    )


def snapshot_bundle(project_root: Path, knowledge_id: str, version: str | None = None) -> dict:
    """Capture the entry and its allowed references together as one task version."""
    doc = load_doc(knowledge_id, project_root, version)
    entry = next(item for item in _manifest(project_root)["docs"] if item["id"] == knowledge_id)
    references = {}
    refs_dir = doc.path.parent / "references"
    if (doc.path.name == "SKILL.md" and refs_dir.is_dir()
            and refs_dir.resolve().is_relative_to(doc.path.parent.resolve())):
        root = refs_dir.resolve()
        for path in refs_dir.rglob("*"):
            if path.is_file() and path.resolve().is_relative_to(root):
                references[path.relative_to(doc.path.parent).as_posix()] = path.read_text(encoding="utf-8")
    return {**doc.as_dict(), "name": knowledge_id, "description": entry["description"],
            "content": doc.content, "references": references}


def freeze_bundles(conn, task_id: str, project_root: Path) -> None:
    for entry in _manifest(project_root)["docs"]:
        exists = conn.execute("SELECT 1 FROM skill_bundles WHERE task_id = ? AND name = ?",
                              (task_id, entry["id"])).fetchone()
        if not exists:
            store_bundle(conn, task_id, snapshot_bundle(project_root, entry["id"]))


def store_bundle(conn, task_id: str, bundle: dict) -> None:
    conn.execute("""INSERT INTO skill_bundles (task_id, name, bundle_json) VALUES (?, ?, ?)
                 ON CONFLICT(task_id, name) DO UPDATE SET bundle_json = excluded.bundle_json""",
                 (task_id, bundle["name"], json.dumps(bundle, ensure_ascii=False)))


def task_bundles(conn, task_id: str) -> list[dict]:
    return [json.loads(row["bundle_json"]) for row in conn.execute(
        "SELECT bundle_json FROM skill_bundles WHERE task_id = ? ORDER BY rowid", (task_id,))]


def read_skill(conn, task_id: str, arguments: dict) -> dict:
    name = arguments.get("name")
    resource = arguments.get("resource", "SKILL.md")
    if (set(arguments) - {"name", "resource"} or not isinstance(name, str) or not name
            or not isinstance(resource, str) or not resource):
        return {"ok": False, "error": "invalid_arguments", "message": "提供 Skill 名称和可选相对引用路径。"}
    bundle = next((item for item in task_bundles(conn, task_id) if item["name"] == name), None)
    if bundle is None:
        return {"ok": False, "error": "unknown_skill", "message": "该 Skill 不在任务目录中。"}
    content = bundle["content"] if resource == "SKILL.md" else bundle["references"].get(resource)
    if content is None:
        return {"ok": False, "error": "unknown_resource", "message": "只能读取该 Skill 已采用的正文或引用资源。"}
    return {"ok": True, "name": name, "version": bundle["version"], "resource": resource,
            "source": str(Path(bundle["path"]).parent / resource),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content}
