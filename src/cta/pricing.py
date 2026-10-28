"""Versioned synthetic pricing table and full-path estimates."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from cta.config import find_project_root

DEFAULT_PRICING_VERSION = "2026-10-29.v1"


@dataclass(frozen=True)
class ModeSpec:
    key: str
    label: str
    cost_cents: int
    duration_ms: int
    max_internal_requests: int | None = None
    max_requests: int | None = None
    max_readbacks: int | None = None
    candidates: int | None = None
    failure_rule: str = ""

    def as_dict(self) -> dict:
        return {
            "mode": self.key,
            "label": self.label,
            "cost_cents": self.cost_cents,
            "duration_ms": self.duration_ms,
            "max_internal_requests": self.max_internal_requests,
            "max_requests": self.max_requests,
            "max_readbacks": self.max_readbacks,
            "candidates": self.candidates,
            "failure_rule": self.failure_rule,
        }


class PriceTable:
    def __init__(self, payload: dict, path: Path):
        self.version = payload["version"]
        self.currency = payload.get("currency", "CNY_cent")
        self.path = path
        self.modes = {
            key: ModeSpec(
                key=key,
                label=value["label"],
                cost_cents=int(value["cost_cents"]),
                duration_ms=int(value["duration_ms"]),
                max_internal_requests=value.get("max_internal_requests"),
                max_requests=value.get("max_requests"),
                max_readbacks=value.get("max_readbacks"),
                candidates=value.get("candidates"),
                failure_rule=value.get("failure_rule", ""),
            )
            for key, value in payload["modes"].items()
        }
        self.full_path = payload["full_path"]

    def mode(self, key: str) -> ModeSpec:
        try:
            return self.modes[key]
        except KeyError as exc:
            raise ValueError(f"unknown pricing mode: {key}") from exc

    def remaining_after_generation(self, generation_mode: str) -> dict:
        """Cost/time of the still-required generation, review and save steps."""
        specs = [
            ("generation", self.mode(generation_mode)),
            ("review", self.mode(self.full_path.get("review", "review"))),
            ("save", self.mode(self.full_path.get("save", "save"))),
        ]
        return {
            "lines": [{"step": step, **spec.as_dict()} for step, spec in specs],
            "total_cost_cents": sum(spec.cost_cents for _, spec in specs),
            "total_duration_ms": sum(spec.duration_ms for _, spec in specs),
        }

    def estimate(self, search_mode: str, generation_mode: str, *, include_revision: bool = False) -> dict:
        names = ["search", "generation", "review", "save"]
        selected = {
            "search": search_mode,
            "generation": generation_mode,
            "review": self.full_path.get("review", "review"),
            "save": self.full_path.get("save", "save"),
        }
        if include_revision:
            names.insert(2, "revision")
            selected["revision"] = "revision"
        lines = []
        for name in names:
            spec = self.mode(selected[name])
            lines.append({"step": name, **spec.as_dict()})
        return {
            "version": self.version,
            "currency": self.currency,
            "pricing_source": str(self.path.name),
            "lines": lines,
            "total_cost_cents": sum(line["cost_cents"] for line in lines),
            "total_duration_ms": sum(line["duration_ms"] for line in lines),
            "notes": "估算与预留不是实际支出；执行前仍须按当前预算与模拟截止检查。",
        }


def load_price_table(project_root: Path | None = None) -> PriceTable:
    root = project_root or find_project_root()
    version = os.environ.get("CTA_PRICING_VERSION") or DEFAULT_PRICING_VERSION
    path = root / "fixtures" / "pricing" / f"{version}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["version"] != version:
        raise ValueError(f"pricing version mismatch: {path}")
    return PriceTable(payload, path)
