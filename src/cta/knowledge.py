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
    path = knowledge_root / knowledge_id / f"{selected_version}.md"
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
