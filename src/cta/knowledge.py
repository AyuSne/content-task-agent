"""Versioned product working knowledge: load, hash and retain the actual text."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cta.config import find_project_root


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
            "path": str(self.path.relative_to(self.path.parents[2])),
        }


def _manifest(project_root: Path) -> dict:
    return json.loads((project_root / "knowledge" / "manifest.json").read_text(encoding="utf-8"))


def load_doc(knowledge_id: str, project_root: Path | None = None) -> KnowledgeDoc:
    root = project_root or find_project_root()
    docs = {item["id"]: item for item in _manifest(root)["docs"]}
    if knowledge_id not in docs:
        raise ValueError(f"unknown knowledge: {knowledge_id}")
    entry = docs[knowledge_id]
    path = root / "knowledge" / entry["path"]
    content = path.read_text(encoding="utf-8")
    return KnowledgeDoc(
        knowledge_id=knowledge_id,
        title=entry["title"],
        version=entry["version"],
        path=path,
        content=content,
    )
