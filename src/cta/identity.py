"""Input normalization, validation and request identity.

Only explicit, normalized field equality identifies a duplicate request. No
semantic similarity is used. Invalid fields participate by their stable error
code, so equivalent invalid forms (for example ``0`` and ``0.0``) still map to
one task.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext

from cta.config import parse_instant_with_notes, to_utc_iso


def collapse(value: str) -> str:
    return " ".join(value.split())


@dataclass(frozen=True)
class RawInput:
    topic: str | None = None
    budget: str | None = None
    deadline: str | None = None
    audience: str | None = None
    requirements: tuple[str, ...] = ()

    @classmethod
    def from_args(cls, *, topic, budget, deadline, audience=None, requirements: Iterable[str] = ()) -> RawInput:
        return cls(
            topic=topic,
            budget=budget,
            deadline=deadline,
            audience=audience,
            requirements=tuple(requirements or ()),
        )

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "budget": self.budget,
            "deadline": self.deadline,
            "audience": self.audience,
            "requirements": list(self.requirements),
        }


@dataclass(frozen=True)
class ValidationError:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class Intake:
    raw: RawInput
    topic: str | None
    budget_cents: int | None
    deadline_utc: datetime | None
    audience: str | None
    requirements: list[str]
    errors: list[ValidationError] = field(default_factory=list)
    interpretations: list[str] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def valid(self) -> bool:
        return not self.errors

    def failure_summary(self) -> str:
        labels = {"topic": "主题", "budget": "预算", "deadline": "方案截止时间"}
        fields = []
        for error in self.errors:
            label = labels.get(error.field, error.field)
            if label not in fields:
                fields.append(label)
        return "输入不完整或无效，需要补充：" + "、".join(fields)

    def _invalid_key(self, field_name: str, raw: str | None) -> str:
        for error in self.errors:
            if error.field == field_name:
                if error.code in ("not_a_number", "invalid_format"):
                    return f"invalid:{error.code}:{collapse(raw or '')}"
                return f"invalid:{error.code}"
        return "invalid:unknown"

    def canonical(self, mode: str) -> dict:
        return {
            "mode": mode,
            "topic": f"value:{self.topic}" if self.topic is not None else self._invalid_key("topic", self.raw.topic),
            "budget": (
                f"value:{self.budget_cents}"
                if self.budget_cents is not None
                else self._invalid_key("budget", self.raw.budget)
            ),
            "deadline": (
                f"value:{to_utc_iso(self.deadline_utc)}"
                if self.deadline_utc is not None
                else self._invalid_key("deadline", self.raw.deadline)
            ),
            "audience": self.audience,
            "requirements": self.requirements,
        }


def _parse_topic(raw: str | None, errors: list[ValidationError]) -> str | None:
    text = collapse(raw or "")
    if not text:
        errors.append(ValidationError("topic", "required", "主题（topic）为必填项，且不能为空白。"))
        return None
    return text


def _parse_budget(raw: str | None, errors: list[ValidationError]) -> int | None:
    text = (raw or "").strip()
    if not text:
        errors.append(ValidationError("budget", "required", "任务预算（budget）为必填项，单位为元。"))
        return None
    try:
        with localcontext() as ctx:
            ctx.prec = 28
            amount = Decimal(text)
    except (InvalidOperation, ValueError):
        errors.append(ValidationError("budget", "not_a_number", f"任务预算必须是数字，收到：{text}"))
        return None
    if not amount.is_finite():
        errors.append(ValidationError("budget", "not_a_number", f"任务预算必须是有限数字，收到：{text}"))
        return None
    if amount <= 0:
        errors.append(ValidationError("budget", "not_positive", "任务预算必须大于 0。"))
        return None
    try:
        with localcontext() as ctx:
            ctx.prec = max(28, amount.adjusted() + 4)
            quantized = amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        errors.append(ValidationError("budget", "out_of_range", "任务预算超出可处理范围。"))
        return None
    if quantized != amount:
        errors.append(ValidationError("budget", "too_precise", "任务预算最多保留两位小数。"))
        return None
    cents = int(quantized * 100)
    if cents > 2**63 - 1:
        errors.append(ValidationError("budget", "out_of_range", "任务预算超出可存储范围。"))
        return None
    return cents


def _parse_deadline(
    raw: str | None, sim_start: datetime, errors: list[ValidationError], notes: list[str]
) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        errors.append(ValidationError("deadline", "required", "方案截止时间（deadline）为必填项，使用 ISO-8601 时间。"))
        return None
    try:
        parsed, parsed_notes = parse_instant_with_notes(text)
    except (ValueError, TypeError):
        errors.append(ValidationError("deadline", "invalid_format", f"方案截止时间格式无效：{text}"))
        return None
    notes.extend(parsed_notes)
    if parsed <= sim_start:
        errors.append(
            ValidationError(
                "deadline",
                "not_after_start",
                f"方案截止时间必须晚于任务模拟起点（{sim_start.isoformat()}）。",
            )
        )
        return None
    return parsed


def normalize(raw: RawInput, sim_start: datetime, mode: str) -> Intake:
    errors: list[ValidationError] = []
    notes: list[str] = []
    topic = _parse_topic(raw.topic, errors)
    budget_cents = _parse_budget(raw.budget, errors)
    deadline = _parse_deadline(raw.deadline, sim_start, errors, notes)
    audience = collapse(raw.audience or "") or None
    requirements = [collapse(item) for item in raw.requirements if collapse(item)]

    partial = Intake(
        raw=raw,
        topic=topic,
        budget_cents=budget_cents,
        deadline_utc=deadline,
        audience=audience,
        requirements=requirements,
        errors=errors,
        interpretations=notes,
    )
    payload = json.dumps(partial.canonical(mode), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return replace(partial, fingerprint=fingerprint)


def dedupe_key(mode: str, fingerprint: str) -> str:
    return f"{mode}:{fingerprint}"
