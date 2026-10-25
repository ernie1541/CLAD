"""Machinist cost engine: every part × process × material, filtered by work conditions, rolled into strategies."""
from __future__ import annotations

import json
import math
from functools import lru_cache

from ..config import DATA_DIR

ENV_FLAGS = {"outdoor": "outdoor_uv", "water": "water", "chemical": "chemical", "food": "food_safe"}


@lru_cache(maxsize=1)
def materials() -> dict:
    return json.loads((DATA_DIR / "materials.json").read_text())


@lru_cache(maxsize=1)
def processes() -> dict:
    return json.loads((DATA_DIR / "processes.json").read_text())


def reload() -> None:
    materials.cache_clear()
    processes.cache_clear()


def required_strength(load) -> float:
    levels = materials()["load_levels_mpa"]
    if isinstance(load, (int, float)):
        return float(load)
    return float(levels.get(str(load or "light").lower(), levels["light"]))


def _fits(bbox, env) -> bool:
    return all(b <= e for b, e in zip(sorted(bbox), sorted(env)))


def estimate(part: dict, proc: dict, mat: dict, req: dict, total_qty: int, slice_info: dict | None = None) -> dict:
    m = part["metrics"]
    vol_cm3 = m["volume_mm3"] / 1000.0
    area_cm2 = m["area_mm2"] / 100.0
    bx, by, bz = m["bbox_mm"]
    rho = mat["density_g_cm3"]
    price_g = mat["cost_per_kg"] / 1000.0
    reasons: list[str] = []
    notes: list[str] = []

    # ---- feasibility -------------------------------------------------
    if proc["id"] not in mat["processes"]:
        reasons.append(f"{mat['name']} is not run on {proc['name']}")
    if not _fits(m["bbox_mm"], proc["envelope_mm"]):
        reasons.append(f"part {bx:g}×{by:g}×{bz:g} mm exceeds {proc['name']} envelope")
    tol = req["tolerance_mm"]
    if proc["tolerance_mm"] > tol + 1e-9:
        reasons.append(f"process holds ±{proc['tolerance_mm']} mm, requirement is ±{tol:g} mm")
    wc = req.get("work_conditions", {})
    t_need = wc.get("max_temp_c")
    if t_need not in (None, "") and float(t_need) > mat["max_temp_c"]:
        reasons.append(f"{mat['name']} rated to {mat['max_temp_c']} °C, needs {t_need} °C")
    s_need = required_strength(wc.get("load"))
    if mat["tensile_mpa"] < s_need:
        reasons.append(f"{mat['name']} tensile {mat['tensile_mpa']} MPa below {s_need:g} MPa for '{wc.get('load', 'light')}' load")
    for env in wc.get("environment", []) or []:
        flag = ENV_FLAGS.get(env)
        if flag and not mat.get(flag):
            reasons.append(f"{mat['name']} not suited for {env} exposure")
    if proc["id"] == "laser_cut":
        t = min(m["bbox_mm"])
        second = sorted(m["bbox_mm"])[1]
        if t > proc["max_thickness_mm"] or t > 0.25 * second:
            reasons.append("not a flat plate (laser cutting needs a uniform-thickness 2D profile)")
    allowed = [p for p in (req.get("processes_available") or []) if p and p != "any"]
    if allowed and proc["id"] not in allowed:
        reasons.append(f"{proc['name']} is not available to you")

    # ---- cost --------------------------------------------------------
    hours = 0.0
    material_cost = 0.0
    tooling = 0.0
    pid = proc["id"]
    if pid == "fdm":
        eff = min(vol_cm3, vol_cm3 * proc["solid_fraction"] + area_cm2 * 0.12)
        if slice_info and slice_info.get("print_hours"):
            hours = slice_info["print_hours"]
            grams = slice_info.get("filament_g") or eff * rho
            notes.append("print time from slicer")
        else:
            hours = eff / proc["deposition_cm3_per_hr"] + 0.15
            grams = eff * rho
        material_cost = grams * price_g * proc["material_waste_factor"]
    elif pid == "sla":
        hours = bz / proc["z_speed_mm_per_hr"] + 0.5
        material_cost = vol_cm3 * rho * price_g * proc["material_waste_factor"]
    elif pid == "sls":
        hours = vol_cm3 / proc["build_cm3_per_hr"] + 0.5
        material_cost = vol_cm3 * rho * price_g * proc["material_waste_factor"]
    elif pid == "cnc_mill":
        mg = proc["stock_margin_mm"] * 2
        stock = (bx + mg) * (by + mg) * (bz + mg) / 1000.0
        removed = max(stock - vol_cm3, 0.0)
        mrr = proc["mrr_cm3_per_min"].get(mat["class"], proc["mrr_cm3_per_min"]["plastic"])
        hours = removed / mrr / 60.0 + area_cm2 / proc["finish_cm2_per_min"] / 60.0 + 0.25
        material_cost = stock * rho * price_g
        notes.append(f"stock {bx + mg:.0f}×{by + mg:.0f}×{bz + mg:.0f} mm, removes {removed:.0f} cm³")
    elif pid == "laser_cut":
        t = max(min(m["bbox_mm"]), 0.5)
        dims = sorted(m["bbox_mm"])
        cut_len = max(0.0, (m["area_mm2"] - 2 * m["volume_mm3"] / t)) / t
        speed = proc["cut_speed_mm_per_min"].get(mat["class"], proc["cut_speed_mm_per_min"]["plastic"])
        speed = speed * min(1.0, 3.0 / t)  # thicker stock cuts slower
        hours = cut_len / speed / 60.0 + 0.05
        sheet_cm3 = dims[1] * dims[2] * t / 1000.0
        material_cost = sheet_cm3 * rho * price_g * proc["material_waste_factor"]
        notes.append(f"{t:g} mm sheet, ≈{cut_len / 1000:.2f} m of cut")
    elif pid == "injection_molding":
        tooling = proc["tooling_base"] + proc["tooling_per_cm3_bbox"] * (bx * by * bz / 1000.0)
        hours = proc["cycle_s"] / 3600.0
        material_cost = vol_cm3 * rho * price_g * proc["material_waste_factor"]
        notes.append(f"mold ≈ ${tooling:,.0f}, amortized over {total_qty} parts")

    machine_cost = hours * proc["machine_rate_per_hr"]
    setup = proc["setup_cost"] / max(total_qty, 1)
    tooling_unit = tooling / max(total_qty, 1)
    unit = material_cost + machine_cost + proc["labor_per_part"] + setup + tooling_unit

    t_margin = (mat["max_temp_c"] - float(t_need or 25)) / 100.0
    performance = (min(mat["tensile_mpa"] / max(s_need, 1), 10) + max(min(t_margin, 3), -3)
                   + min(tol / proc["tolerance_mm"], 5) * 0.5)
    hint = (part.get("material_hint") or "").lower()
    matches_hint = bool(hint) and (mat["name"].lower() in hint or mat["id"] in hint or hint in mat["name"].lower())

    return {
        "part_id": part["id"],
        "process": pid,
        "process_name": proc["name"],
        "material": mat["id"],
        "material_name": mat["name"],
        "feasible": not reasons,
        "reasons": reasons,
        "unit_cost": round(unit, 2),
        "breakdown": {
            "material": round(material_cost, 2),
            "machine": round(machine_cost, 2),
            "labor": round(proc["labor_per_part"], 2),
            "setup": round(setup, 2),
            "tooling": round(tooling_unit, 2),
        },
        "machine_hours": round(hours, 3),
        "total_qty": total_qty,
        "lead_days": proc["lead_days"],
        "tolerance_mm": proc["tolerance_mm"],
        "mass_g": round(vol_cm3 * rho, 1),
        "performance": round(performance, 2),
        "matches_hint": matches_hint,
        "notes": notes,
    }


def plan_production(parts: list[dict], req: dict, slice_data: dict | None = None) -> dict:
    """parts: [{id, name, quantity, metrics, material_hint}], req: normalized requirements."""
    order_qty = max(int(req.get("quantity") or 1), 1)
    procs = processes()["processes"]
    mats = materials()["materials"]
    per_part = {}
    for part in parts:
        total_qty = order_qty * max(int(part.get("quantity") or 1), 1)
        opts = []
        for proc in procs:
            for mat in mats:
                if proc["id"] not in mat["processes"]:
                    continue
                opts.append(estimate(part, proc, mat, req, total_qty, (slice_data or {}).get(part["id"]) if proc["id"] == "fdm" else None))
        feasible = sorted([o for o in opts if o["feasible"]], key=lambda o: o["unit_cost"])
        blocked = [o for o in opts if not o["feasible"]]
        # summarize why each process was blocked (first reason per process)
        why = {}
        for o in blocked:
            why.setdefault(o["process_name"], o["reasons"][0])
        per_part[part["id"]] = {"total_qty": total_qty, "feasible": feasible, "blocked_reasons": why}

    def build(name: str, label: str, key) -> dict:
        assign, total, missing, hours_by_proc = [], 0.0, [], {}
        for part in parts:
            pp = per_part[part["id"]]
            if not pp["feasible"]:
                missing.append(part["id"])
                continue
            best = min(pp["feasible"], key=key)
            line = best["unit_cost"] * pp["total_qty"]
            total += line
            hours_by_proc[best["process"]] = hours_by_proc.get(best["process"], 0) + best["machine_hours"] * pp["total_qty"]
            assign.append({**best, "quantity": pp["total_qty"], "line_cost": round(line, 2)})
        lead = 0
        for a in assign:
            lead = max(lead, a["lead_days"] + math.ceil(hours_by_proc[a["process"]] / 8.0))
        budget = req.get("budget")
        return {
            "id": name,
            "label": label,
            "assignments": assign,
            "total_cost": round(total, 2),
            "lead_days": lead,
            "feasible": not missing,
            "unproducible_parts": missing,
            "within_budget": (budget in (None, "", 0)) or total <= float(budget),
        }

    strategies = [
        build("lowest_cost", "Lowest cost", lambda o: o["unit_cost"]),
        build("fastest", "Fastest turnaround", lambda o: (o["lead_days"] + math.ceil(o["machine_hours"] * o["total_qty"] / 8.0), o["unit_cost"])),
        build("best_performance", "Best performance", lambda o: (-o["performance"], o["unit_cost"])),
    ]
    # Only show a strategy if it actually beats "lowest cost" on what it claims to optimize
    low = strategies[0]
    sig = lambda st: tuple((a["part_id"], a["process"], a["material"]) for a in st["assignments"])
    kept = [low]
    fast, perf = strategies[1], strategies[2]
    if fast["lead_days"] < low["lead_days"] and sig(fast) != sig(low):
        kept.append(fast)
    if sig(perf) not in {sig(k) for k in kept}:
        kept.append(perf)
    strategies = kept
    return {
        "order_quantity": order_qty,
        "strategies": strategies,
        "options": {pid: {"total_qty": v["total_qty"], "top": v["feasible"][:8], "blocked_reasons": v["blocked_reasons"]} for pid, v in per_part.items()},
        "assumptions": [
            "Costs use data/processes.json and data/materials.json (typical 2026 small-shop rates, USD).",
            f"Setup and tooling are amortized over the order quantity ({order_qty}) × parts per assembly.",
            "Lead time = process queue days + machine hours at one 8-hour shift per day.",
            f"Tolerance requirement ±{req['tolerance_mm']:g} mm; strength need from load '{req.get('work_conditions', {}).get('load', 'light')}'.",
        ],
    }
