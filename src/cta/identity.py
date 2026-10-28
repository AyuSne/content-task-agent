"""Input normalization, validation and request identity.

Only explicit, normalized field equality identifies a duplicate request. No
semantic similarity is used.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext

from cta.config import parse_instant, to_utc_iso


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
    budget_display: str | None
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

    def canonical(self, mode: str) -> dict:
        # Valid fields are identified by normalized value only; invalid or
        # missing fields keep the normalized raw text so distinct incomplete
        # requests are not silently merged.
        return {
            "mode": mode,
            "topic": self.topic,
            "budget_cents": self.budget_cents,
            "budget_raw": None if self.budget_cents is not None else collapse(self.raw.budget or ""),
            "deadline_utc": None if self.deadline_utc is None else to_utc_iso(self.deadline_utc),
            "deadline_raw": None if self.deadline_utc is not None else collapse(self.raw.deadline or ""),
            "audience": self.audience,
            "requirements": self.requirements,
        }


def _parse_topic(raw: str | None, errors: list[ValidationError]) -> str | None:
    text = collapse(raw or "")
    if not text:
        errors.append(ValidationError("topic", "required", "主题（topic）为必填项，且不能为空白。"))
        return None
    return text


def _parse_budget(raw: str | None, errors: list[ValidationError]) -> tuple[int | None, str | None]:
    text = (raw or "").strip()
    if not text:
        errors.append(ValidationError("budget", "required", "任务预算（budget）为必填项，单位为元。"))
        return None, None
    try:
        with localcontext() as ctx:
            ctx.prec = 28
            amount = Decimal(text)
    except (InvalidOperation, ValueError):
        errors.append(ValidationError("budget", "not_a_number", f"任务预算必须是数字，收到：{text}"))
        return None, None
    if not amount.is_finite():
        errors.append(ValidationError("budget", "not_a_number", f"任务预算必须是有限数字，收到：{text}"))
        return None, None
    if amount <= 0:
        errors.append(ValidationError("budget", "not_positive", "任务预算必须大于 0。"))
        return None, None
    quantized = amount.quantize(Decimal("0.01"))
    if quantized != amount:
        errors.append(ValidationError("budget", "too_precise", "任务预算最多保留两位小数。"))
        return None, None
    cents = int(quantized * 100)
    return cents, f"{cents / 100:.2f}"


def _parse_deadline(raw: str | None, sim_start: datetime, errors: list[ValidationError], notes: list[str]) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        errors.append(ValidationError("deadline", "required", "方案截止时间（deadline）为必填项，使用 ISO-8601 时间。"))
        return None
    try:
        parsed = parse_instant(text)
    except (ValueError, TypeError):
        errors.append(ValidationError("deadline", "invalid_format", f"方案截止时间格式无效：{text}"))
        return None
    if len(text) == 10:
        notes.append("截止时间只给到日期，按北京时间当日 23:59:59 解释。")
    elif "T" in text and not (text.endswith(("Z", "z")) or "+" in text[10:] or "-" in text[10:]):
        notes.append("截止时间未带时区，按北京时间解释。")
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
    budget_cents, budget_display = _parse_budget(raw.budget, errors)
    deadline = _parse_deadline(raw.deadline, sim_start, errors, notes)
    audience_text = collapse(raw.audience or "")
    audience = audience_text or None
    requirements = [collapse(item) for item in raw.requirements if collapse(item)]

    partial = Intake(
        raw=raw,
        topic=topic,
        budget_cents=budget_cents,
        budget_display=budget_display,
        deadline_utc=deadline,
        audience=audience,
        requirements=requirements,
        errors=errors,
        interpretations=notes,
    )
    canonical = partial.canonical(mode)
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return Intake(
        raw=raw,
        topic=topic,
        budget_cents=budget_cents,
        budget_display=budget_display,
        deadline_utc=deadline,
        audience=audience,
        requirements=requirements,
        errors=errors,
        interpretations=notes,
        fingerprint=fingerprint,
    )


def dedupe_key(mode: str, fingerprint: str) -> str:
    return f"{mode}:{fingerprint}"
