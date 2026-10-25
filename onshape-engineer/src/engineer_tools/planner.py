"""Deterministic planning: plan validation, parallelization decision, work-order generation."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import EngineerConfig
from .reference_db import ReferenceDB
from .schemas import (DesignBrief, ModelingPlan, OnshapeParameter, Subassembly, WorkOrder)
from .units import mm_to_in, onshape_expression

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_plan(plan: ModelingPlan, db: ReferenceDB) -> list[str]:
    errs: list[str] = []
    pnames = [p.name for p in plan.parameters]
    for n in pnames:
        if not _NAME.match(n):
            errs.append(f"Parameter '{n}' must be snake_case (a-z, 0-9, _), starting with a letter")
    dup = {n for n in pnames if pnames.count(n) > 1}
    if dup:
        errs.append(f"Duplicate parameter names: {sorted(dup)}")
    for p in plan.parameters:
        if p.unit == "mm" and p.value <= 0:
            errs.append(f"Parameter '{p.name}' is a length and must be > 0 (got {p.value})")

    if not plan.subassemblies:
        errs.append("Plan has no subassemblies")
    snames = [s.name for s in plan.subassemblies]
    if len(set(snames)) != len(snames):
        errs.append("Subassembly names must be unique")
    parts = plan.all_parts()
    part_names = [p.name for p in parts]
    dup = {n for n in part_names if part_names.count(n) > 1}
    if dup:
        errs.append(f"Duplicate part names: {sorted(dup)}")
    for p in parts:
        if not _NAME.match(p.name):
            errs.append(f"Part '{p.name}' must be snake_case")
        if db.get(p.material) is None:
            errs.append(f"Part '{p.name}': material '{p.material}' not in reference DB. Use one of {db.keys()} "
                        "or flag the material as an open item and pick the closest DB entry.")
    for s in plan.subassemblies:
        if not s.parts:
            errs.append(f"Subassembly '{s.name}' has no parts")
        for d in s.depends_on:
            if d not in snames:
                errs.append(f"Subassembly '{s.name}' depends on unknown '{d}'")
            if d == s.name:
                errs.append(f"Subassembly '{s.name}' depends on itself")
    if not errs and _has_cycle(plan.subassemblies):
        errs.append("Subassembly dependencies contain a cycle")

    pset, partset = set(pnames), set(part_names)
    ids = [i.id for i in plan.interfaces]
    if len(set(ids)) != len(ids):
        errs.append("Interface ids must be unique")
    for i in plan.interfaces:
        for side in (i.part_a, i.part_b):
            if side not in partset:
                errs.append(f"Interface '{i.id}' references unknown part '{side}'")
        for pn in i.parameters:
            if pn not in pset:
                errs.append(f"Interface '{i.id}' references unknown parameter '{pn}'")
        if not i.parameters:
            errs.append(f"Interface '{i.id}' must list the plan parameters it freezes (e.g. hole spacing, bore dia)")
    for n in plan.modeling_order:
        if n not in partset:
            errs.append(f"modeling_order lists unknown part '{n}'")
    return errs


def _has_cycle(subs: list[Subassembly]) -> bool:
    graph = {s.name: list(s.depends_on) for s in subs}
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(d) for d in graph.get(n, [])):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in graph)


def levels(subs: list[Subassembly]) -> dict[str, int]:
    """Dependency depth of each subassembly (0 = no dependencies)."""
    by = {s.name: s for s in subs}
    memo: dict[str, int] = {}

    def lvl(n: str) -> int:
        if n not in memo:
            deps = by[n].depends_on
            memo[n] = 0 if not deps else 1 + max(lvl(d) for d in deps)
        return memo[n]

    return {s.name: lvl(s.name) for s in subs}


@dataclass
class ParallelDecision:
    parallel: bool
    reason: str
    n_parts: int
    n_independent: int


def decide_parallel(plan: ModelingPlan, cfg: EngineerConfig) -> ParallelDecision:
    n_parts = len(plan.all_parts())
    n_ind = sum(1 for s in plan.subassemblies if not s.depends_on)
    big = n_parts > cfg.spawn_if_parts_over or n_ind > cfg.spawn_if_subassemblies_over
    if big and len(plan.subassemblies) > 1:
        return ParallelDecision(True, f"{n_parts} parts (> {cfg.spawn_if_parts_over}) or {n_ind} independent "
                                f"subassemblies (> {cfg.spawn_if_subassemblies_over}): one worker per subassembly, "
                                f"max {cfg.max_workers} at a time", n_parts, n_ind)
    if big:
        return ParallelDecision(False, "Large, but only one subassembly: one worker. Split it into subassemblies "
                                "to parallelize.", n_parts, n_ind)
    return ParallelDecision(False, f"{n_parts} parts / {n_ind} independent subassemblies is under the thresholds: "
                            "one worker builds everything", n_parts, n_ind)


def build_orders(run_id: str, iteration: int, plan: ModelingPlan, brief: DesignBrief, context: str,
                 cfg: EngineerConfig, decision: ParallelDecision) -> list[WorkOrder]:
    groups: list[tuple[int, list[Subassembly]]]
    if decision.parallel:
        lv = levels(plan.subassemblies)
        groups = [(lv[s.name], [s]) for s in plan.subassemblies]
    else:
        groups = [(0, list(plan.subassemblies))]

    orders = []
    for idx, (wave, subs) in enumerate(sorted(groups, key=lambda g: g[0]), start=1):
        part_names = {p.name for s in subs for p in s.parts}
        ifaces = [i for i in plan.interfaces if i.part_a in part_names or i.part_b in part_names]
        frozen = {pn for i in ifaces for pn in i.parameters}
        params = [OnshapeParameter(
            name=p.name, value=p.value, unit=p.unit,
            value_in=round(mm_to_in(p.value), 6) if p.unit == "mm" else None,
            onshape_expression=onshape_expression(p.value, p.unit),
            frozen=p.name in frozen) for p in plan.parameters]
        oid = f"wo-i{iteration}-{idx:02d}-{subs[0].name if len(subs) == 1 else 'all'}"[:60]
        orders.append(WorkOrder(
            order_id=oid, run_id=run_id, iteration=iteration, wave=wave, subassemblies=subs,
            parameters=params, datums=plan.datums, interfaces=ifaces, loads=brief.loads,
            acceptance_criteria=[
                f"Create ONE Part Studio named '{oid}' and build every part of this order in it.",
                "Driving dimensions reference plan variables (#name) where the MCP tool has a variable* "
                "argument; any other number comes from the `convert` tool. No made-up numbers.",
                "Every part is placed in the GLOBAL frame defined by the datums.",
                "Frozen parameters (interfaces) are used exactly. Never work around a contract: report a conflict.",
                "Call report_part for every part (volume from FeatureScript evVolume, bounding box).",
                f"For loads your parts carry, run hand_calc and get SF >= {cfg.min_safety_factor}.",
                f"Minimum wall thickness >= {cfg.min_wall_thickness_mm} mm.",
            ],
            context=context))
    return orders


def spawn_task(order: WorkOrder) -> str:
    """Short task text for sessions_spawn; the worker pulls the full order with get_work_order."""
    names = ", ".join(order.part_names())
    rework = f" This is REWORK #{len(order.rework)}: read the `rework` field first." if order.rework else ""
    return (f"Execute work order {order.order_id} (run {order.run_id}). Parts: {names}.{rework} "
            f"Start by calling engineer__get_work_order with order_id='{order.order_id}'. "
            "Follow your AGENTS.md procedure. Finish with engineer__submit_work_result, then reply with a "
            "one-paragraph summary.")
