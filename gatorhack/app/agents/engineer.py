"""Lead engineer (big Gemma): review → plan (with sub-assembly split) → memo."""
from __future__ import annotations

from .. import llm, prompts
from .common import compact, requirements_block, slug, vec3


def _sys() -> dict:
    return {"role": "system", "content": prompts.ENGINEER_SYSTEM}


async def review(p: dict) -> dict:
    msgs = [_sys(), {"role": "user", "content": (
        "TASK: REVIEW the request below before any modeling.\n\n"
        f"{requirements_block(p)}\n\n"
        "List real drawbacks only (no filler), each with a concrete proposed fix the user can accept or reject. "
        "Ask questions only if the answer would change the design.\n"
        f"Answer with JSON:\n{prompts.REVIEW_SCHEMA}"
    )}]
    out = await llm.chat_json("engineer", "review", msgs, project_id=p["id"], session="lead", ctx={"project": p})
    out.setdefault("summary", "")
    out["drawbacks"] = [d for d in out.get("drawbacks", []) if isinstance(d, dict)]
    fixes = []
    for i, f in enumerate(out.get("proposed_fixes", []) or [], 1):
        if isinstance(f, str):
            f = {"fix": f}
        if isinstance(f, dict) and f.get("fix"):
            f["id"] = f.get("id") or f"F{i}"
            fixes.append(f)
    out["proposed_fixes"] = fixes
    out["assumptions"] = [str(a) for a in out.get("assumptions", []) or []]
    out["questions_for_user"] = [str(q) for q in out.get("questions_for_user", []) or [] if str(q).strip()]
    return out


async def plan(p: dict, feedback: list[str] | None = None) -> dict:
    accepted = p.get("accepted_fixes", [])
    parts = [requirements_block(p), ""]
    parts.append("Review summary: " + p.get("review", {}).get("summary", ""))
    parts.append("Fixes the user ACCEPTED (apply them): " + (compact(accepted) if accepted else "none"))
    rejected = p.get("rejected_fixes", [])
    if rejected:
        parts.append("Fixes the user REJECTED (do not apply): " + compact(rejected))
    if p.get("fix_comments"):
        parts.append("User comments on the review: " + p["fix_comments"])
    if p.get("plan") and feedback:
        parts.append("\nPREVIOUS PLAN:\n" + compact(p["plan"]))
    if feedback:
        parts.append("\nREVISION REQUESTED — address every point:\n- " + "\n- ".join(feedback))
    msgs = [_sys(), {"role": "user", "content": (
        "TASK: PLAN the CAD model.\n\n" + "\n".join(parts) + "\n\n"
        "Split the product into sub-assemblies. Each sub-assembly will be handed to its own sub-engineer, so give "
        "every part a complete, unambiguous geometric description in its local frame, exact envelope, and placement. "
        "Keep the part count as small as the function allows.\n"
        f"Answer with JSON:\n{prompts.PLAN_SCHEMA}"
    )}]
    out = await llm.chat_json("engineer", "plan", msgs, project_id=p["id"], session="lead", ctx={"project": p, "feedback": feedback})
    return normalize_plan(out)


def normalize_plan(plan: dict) -> dict:
    subs = plan.get("subassemblies")
    if not subs and plan.get("parts"):
        subs = [{"id": "main", "name": "Main assembly", "parts": plan["parts"]}]
    seen: set[str] = set()
    norm_subs = []
    for si, sub in enumerate(subs or [], 1):
        sid = slug(sub.get("id") or sub.get("name"), f"sub{si}")
        while sid in seen:
            sid += "_b"
        seen.add(sid)
        parts = []
        for pi, part in enumerate(sub.get("parts", []) or [], 1):
            if not isinstance(part, dict):
                continue
            pid = slug(part.get("id") or part.get("name"), f"part{si}_{pi}")
            while pid in seen:
                pid += "_b"
            seen.add(pid)
            env = vec3(part.get("envelope_mm"), (50, 50, 10))
            parts.append({
                "id": pid,
                "name": part.get("name") or pid.replace("_", " ").title(),
                "description": part.get("description", ""),
                "local_frame": part.get("local_frame", "center of bottom face"),
                "envelope_mm": [abs(x) for x in env],
                "features": [str(f) for f in part.get("features", []) or []],
                "interfaces": [str(f) for f in part.get("interfaces", []) or []],
                "position_mm": vec3(part.get("position_mm")),
                "rotation_deg": vec3(part.get("rotation_deg")),
                "quantity": max(int(part.get("quantity") or 1), 1),
                "solids": max(int(part.get("solids") or 1), 1),
                "material_hint": part.get("material_hint", ""),
            })
        if parts:
            norm_subs.append({
                "id": sid,
                "name": sub.get("name") or sid.replace("_", " ").title(),
                "purpose": sub.get("purpose", ""),
                "position_mm": vec3(sub.get("position_mm")),
                "rotation_deg": vec3(sub.get("rotation_deg")),
                "parts": parts,
            })
    if not norm_subs:
        raise ValueError("plan contained no parts")
    return {
        "assembly_name": slug(plan.get("assembly_name"), "assembly"),
        "approach": plan.get("approach", ""),
        "overall_envelope_mm": vec3(plan.get("overall_envelope_mm"), (0, 0, 0)),
        "subassemblies": norm_subs,
        "tests": [str(t) for t in plan.get("tests", []) or []],
        "risks": [str(r) for r in plan.get("risks", []) or []],
        "questions_for_user": [str(q) for q in plan.get("questions_for_user", []) or [] if str(q).strip()],
    }


async def memo(p: dict) -> str:
    build = p["build"]
    facts = {
        "requirements": p["requirements"],
        "accepted_fixes": p.get("accepted_fixes", []),
        "plan_approach": p["plan"]["approach"],
        "subassemblies": [
            {"id": s["id"], "name": s["name"], "built_by": build["subassemblies"].get(s["id"], {}).get("agent"),
             "parts": [{"id": pt["id"], "name": pt["name"], **{k: build["parts"].get(pt["id"], {}).get("metrics", {}).get(k) for k in ("bbox_mm", "volume_mm3")},
                        "attempts": build["parts"].get(pt["id"], {}).get("attempts")} for pt in s["parts"]]}
            for s in p["plan"]["subassemblies"]
        ],
        "assembly_tests": build.get("assembly", {}).get("tests", []),
        "part_test_failures_resolved": sum(max(0, (v.get("attempts") or 1) - 1) for v in build["parts"].values()),
        "risks": p["plan"].get("risks", []),
        "revision": p["revision"],
        "iteration": p["iteration"],
    }
    msgs = [_sys(), {"role": "user", "content": (
        "TASK: write the TECHNICAL MEMO for the completed assembly. The user signs this before the machinist "
        "takes over. Markdown, about 300-500 words, sections: Purpose, Design decisions (incl. accepted fixes), "
        "Assembly breakdown (table: part, sub-assembly, size, volume), Verification (what was tested and the results), "
        "Open risks, Recommendation for hand-off. Use only the facts below.\n\n" + compact(facts) +
        "\n\nReply with the Markdown memo only (no JSON)."
    )}]
    return await llm.chat("engineer", "memo", msgs, project_id=p["id"], session="lead", json_mode=False, ctx={"project": p, "facts": facts})
