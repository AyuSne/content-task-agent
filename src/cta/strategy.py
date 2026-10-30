"""Versioned creative-direction table and full remaining-path comparison.

The table is synthetic configuration fixed by version. It describes the
expression goal, material needs and risk of each direction; costs and durations
always come from the same versioned price table the execution guards use.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cta.config import find_project_root
from cta.pricing import PriceTable

DEFAULT_STRATEGY_VERSION = "2026-10-29.v2"
DIRECTIONS = ("functional_demo", "user_scenario", "tutorial")
SEARCH_MODES = ("search_basic", "search_extended")
GENERATION_MODES = ("generate_direct", "generate_multi")
MAX_SEARCH_QUERIES = 6


@dataclass(frozen=True)
class DirectionSpec:
    key: str
    label: str
    expression_goal: str
    material_needs: tuple[str, ...]
    evidence_queries: tuple[str, ...]
    risk: str

    @property
    def required_queries(self) -> int:
        return len(self.evidence_queries)

    def as_dict(self) -> dict:
        return {
            "direction": self.key,
            "label": self.label,
            "expression_goal": self.expression_goal,
            "material_needs": list(self.material_needs),
            "evidence_queries": list(self.evidence_queries),
            "required_queries": self.required_queries,
            "risk": self.risk,
        }


class StrategyTable:
    def __init__(self, payload: dict, path: Path):
        self.version = payload["version"]
        self.description = payload.get("description", "")
        self.path = path
        self.directions = {}
        for key, value in payload["directions"].items():
            if key not in DIRECTIONS:
                raise ValueError(f"unknown strategy direction: {key}")
            needs = tuple(value.get("material_needs") or ())
            queries = tuple(value.get("evidence_queries") or ())
            if not needs or not queries:
                raise ValueError(f"strategy direction {key} needs material_needs and evidence_queries")
            self.directions[key] = DirectionSpec(
                key=key,
                label=value["label"],
                expression_goal=value["expression_goal"],
                material_needs=needs,
                evidence_queries=queries,
                risk=value["risk"],
            )
        missing = [key for key in DIRECTIONS if key not in self.directions]
        if missing:
            raise ValueError(f"strategy table missing directions: {missing}")

    def direction(self, key: str) -> DirectionSpec:
        try:
            return self.directions[key]
        except KeyError as exc:
            raise ValueError(f"unknown strategy direction: {key}") from exc


@dataclass(frozen=True)
class ResourceState:
    incurred_cents: int
    budget_cents: int | None
    sim_start: datetime
    sim_elapsed_ms: int
    deadline: datetime | None

    @property
    def sim_now(self) -> datetime:
        return self.sim_start + timedelta(milliseconds=self.sim_elapsed_ms)

    @property
    def remaining_budget_cents(self) -> int | None:
        if self.budget_cents is None:
            return None
        return self.budget_cents - self.incurred_cents


def _assess(
    price: PriceTable,
    direction: DirectionSpec,
    search_mode: str,
    generation_mode: str,
    search_queries: int,
    include_revision: bool,
    state: ResourceState,
) -> dict:
    path = price.estimate(
        search_mode,
        generation_mode,
        search_queries=search_queries,
        include_revision=include_revision,
    )
    projected = state.sim_now + timedelta(milliseconds=path["total_duration_ms"])
    remaining = state.remaining_budget_cents
    budget_feasible = remaining is None or path["total_cost_cents"] <= remaining
    deadline_feasible = state.deadline is None or projected <= state.deadline
    return {
        "direction": direction.key,
        "search_mode": search_mode,
        "generation_mode": generation_mode,
        "search_queries": search_queries,
        "include_revision": include_revision,
        "path": path,
        "total_cost_cents": path["total_cost_cents"],
        "total_duration_ms": path["total_duration_ms"],
        "projected_sim_complete": projected.isoformat(),
        "budget_gap_cents": None if remaining is None else remaining - path["total_cost_cents"],
        "time_gap_ms": None if state.deadline is None else int((state.deadline - projected).total_seconds() * 1000),
        "budget_feasible": budget_feasible,
        "deadline_feasible": deadline_feasible,
        "feasible": budget_feasible and deadline_feasible,
    }


def _compact_option(option: dict, reference_cost: int) -> dict:
    return {
        "search_mode": option["search_mode"],
        "generation_mode": option["generation_mode"],
        "search_queries": option["search_queries"],
        "total_cost_cents": option["total_cost_cents"],
        "total_duration_ms": option["total_duration_ms"],
        "cost_delta_cents": option["total_cost_cents"] - reference_cost,
        "feasible": option["feasible"],
        "budget_feasible": option["budget_feasible"],
        "deadline_feasible": option["deadline_feasible"],
    }


def compare_strategies(
    table: StrategyTable,
    price: PriceTable,
    state: ResourceState,
    *,
    directions: list[str] | None = None,
    search_mode: str = "search_basic",
    generation_mode: str = "generate_direct",
    search_queries: int | None = None,
    include_revision: bool = False,
) -> dict:
    if search_mode not in SEARCH_MODES:
        raise ValueError(f"unknown search mode: {search_mode}")
    if generation_mode not in GENERATION_MODES:
        raise ValueError(f"unknown generation mode: {generation_mode}")
    if search_queries is not None and not (0 <= int(search_queries) <= MAX_SEARCH_QUERIES):
        raise ValueError(f"search_queries must be between 0 and {MAX_SEARCH_QUERIES}")
    selected = list(directions) if directions else list(DIRECTIONS)
    if len(set(selected)) != len(selected):
        raise ValueError("directions must not contain duplicates")
    for key in selected:
        table.direction(key)

    results = []
    cheapest = None
    for key in selected:
        spec = table.direction(key)
        queries = spec.required_queries if search_queries is None else int(search_queries)
        chosen = _assess(price, spec, search_mode, generation_mode, queries, include_revision, state)
        options = []
        for candidate_search in SEARCH_MODES:
            for candidate_generation in GENERATION_MODES:
                option = _assess(
                    price, spec, candidate_search, candidate_generation, queries, include_revision, state
                )
                options.append(option)
                if option["feasible"]:
                    candidate = {
                        "direction": key,
                        "search_mode": candidate_search,
                        "generation_mode": candidate_generation,
                        "search_queries": queries,
                        "total_cost_cents": option["total_cost_cents"],
                        "total_duration_ms": option["total_duration_ms"],
                    }
                    if cheapest is None or (
                        candidate["total_cost_cents"],
                        candidate["total_duration_ms"],
                        key,
                        candidate_search,
                        candidate_generation,
                    ) < (
                        cheapest["total_cost_cents"],
                        cheapest["total_duration_ms"],
                        cheapest["direction"],
                        cheapest["search_mode"],
                        cheapest["generation_mode"],
                    ):
                        cheapest = candidate
        alternatives = [
            _compact_option(option, chosen["total_cost_cents"])
            for option in options
            if option["feasible"]
            and (option["search_mode"], option["generation_mode"])
            != (chosen["search_mode"], chosen["generation_mode"])
        ]
        alternatives.sort(key=lambda item: (item["total_cost_cents"], item["total_duration_ms"]))
        item = {
            **spec.as_dict(),
            "search_queries": queries,
            "path": chosen["path"],
            "projected_sim_complete": chosen["projected_sim_complete"],
            "budget_gap_cents": chosen["budget_gap_cents"],
            "time_gap_ms": chosen["time_gap_ms"],
            "budget_feasible": chosen["budget_feasible"],
            "deadline_feasible": chosen["deadline_feasible"],
            "feasible": chosen["feasible"],
            "infeasibility": None
            if chosen["feasible"]
            else {
                "budget": None
                if chosen["budget_feasible"]
                else f"完整路径需要 {chosen['total_cost_cents']} 分，当前只剩 {state.remaining_budget_cents} 分",
                "deadline": None
                if chosen["deadline_feasible"]
                else (
                    f"完整路径预计 {chosen['projected_sim_complete']} 完成，晚于模拟截止 "
                    f"{state.deadline.isoformat() if state.deadline else '未设置'}"
                ),
            },
            "alternatives": alternatives,
        }
        results.append(item)

    mode_table = {
        "version": price.version,
        "currency": price.currency,
        "modes": {key: spec.as_dict() for key, spec in price.modes.items()},
    }
    return {
        "ok": True,
        "strategy_version": table.version,
        "strategy_source": table.path.name,
        "pricing_version": price.version,
        "pricing_source": price.path.name,
        "incurred_cents": state.incurred_cents,
        "budget_cents": state.budget_cents,
        "remaining_budget_cents": state.remaining_budget_cents,
        "sim_now": state.sim_now.isoformat(),
        "deadline": state.deadline.isoformat() if state.deadline else None,
        "search_mode": search_mode,
        "generation_mode": generation_mode,
        "search_queries": search_queries,
        "include_revision": include_revision,
        "directions": results,
        "cheapest_feasible": cheapest,
        "mode_table": mode_table,
        "notes": (
            "全路径估算包含已知必要检索、生成、核验、保存；include_revision 时另含返修与复核。"
            "估算与预留不是实际支出；预留不保证后续动作成功，执行前仍按当前预算与模拟截止检查。"
        ),
    }


def load_strategy_table(project_root: Path | None = None, *, version: str | None = None) -> StrategyTable:
    root = project_root or find_project_root()
    version = version or os.environ.get("CTA_STRATEGY_VERSION") or DEFAULT_STRATEGY_VERSION
    path = root / "data" / "strategy" / f"{version}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["version"] != version:
        raise ValueError(f"strategy version mismatch: {path}")
    return StrategyTable(payload, path)
