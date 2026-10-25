"""Machinist (Gemma): reviews the deterministic cost study, recommends a strategy, flags DFM / send-backs."""
from __future__ import annotations

from .. import llm, prompts
from .common import compact, requirements_block


def _study_digest(study: dict) -> dict:
    return {
        "order_quantity": study["order_quantity"],
        "strategies": [
            {"id": s["id"], "total_cost": s["total_cost"], "lead_days": s["lead_days"], "feasible": s["feasible"],
             "within_budget": s["within_budget"], "unproducible_parts": s["unproducible_parts"],
             "assignments": [{"part": a["part_id"], "process": a["process_name"], "material": a["material_name"],
                              "unit_cost": a["unit_cost"], "qty": a["quantity"], "hours": a["machine_hours"]} for a in s["assignments"]]}
            for s in study["strategies"]
        ],
        "blocked_processes_per_part": {pid: o["blocked_reasons"] for pid, o in study["options"].items()},
    }


async def review(p: dict, study: dict) -> dict:
    parts = [{"id": pid, "bbox_mm": v.get("metrics", {}).get("bbox_mm"), "volume_mm3": v.get("metrics", {}).get("volume_mm3")}
             for pid, v in p["build"]["parts"].items()]
    msgs = [
        {"role": "system", "content": prompts.MACHINIST_SYSTEM},
        {"role": "user", "content": (
            "TASK: choose the production strategy.\n\n" + requirements_block(p) +
            "\n\nEngineer memo (excerpt):\n" + (p.get("memo") or "")[:2500] +
            "\n\nParts:\n" + compact(parts) +
            "\n\nCost study:\n" + compact(_study_digest(study)) +
            "\n\nIf no strategy is feasible or within budget, or a cheap design change would save a lot, set "
            "send_back_to_engineer=true and explain. Answer with JSON:\n" + prompts.MACHINIST_SCHEMA
        )},
    ]
    out = await llm.chat_json("machinist", "review", msgs, project_id=p["id"], session="machinist", ctx={"project": p, "study": study})
    ids = {s["id"] for s in study["strategies"]}
    if out.get("recommended_strategy") not in ids:
        feasible = [s for s in study["strategies"] if s["feasible"]]
        out["recommended_strategy"] = (feasible or study["strategies"])[0]["id"]
    for k in ("process_notes", "dfm_changes", "questions_for_user"):
        out[k] = [str(x) for x in out.get(k, []) or [] if str(x).strip()]
    out["send_back_to_engineer"] = bool(out.get("send_back_to_engineer"))
    out.setdefault("rationale", "")
    out.setdefault("send_back_reason", "")
    return out
