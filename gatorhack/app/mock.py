"""Deterministic stand-in for Gemma so the workflow runs end-to-end with no model (demo + tests).

It always designs a gusseted L-bracket sized from the requested dimensions, and the gusset's first
attempt deliberately fails so the test → retry loop is visible.
"""
from __future__ import annotations

import json
import re


def _dims(project: dict) -> tuple[float, float, float]:
    from .cad.validate import parse_dims

    d = project["requirements"].get("dimensions_mm") or []
    if not d:
        for qa in project.get("answers", []):
            d = parse_dims(qa["a"]) or []
            if d:
                break
    if len(d) != 3:
        return 120.0, 80.0, 100.0
    return float(d[0]), float(d[1]), float(d[2])


def respond(role: str, task: str, messages: list[dict], ctx: dict) -> str:
    p = ctx.get("project", {})
    if role == "engineer" and task == "review":
        return json.dumps(_review(p))
    if role == "engineer" and task == "plan":
        return json.dumps(_plan(p, ctx.get("feedback")))
    if task == "write_part":
        return _code(p, ctx["part"], ctx.get("attempt", 1))
    if role == "engineer" and task == "memo":
        return _memo(ctx["facts"])
    if role == "machinist":
        return json.dumps(_machinist(p, ctx["study"]))
    return "{}"


def _review(p: dict) -> dict:
    r = p["requirements"]
    wc = r["work_conditions"]
    drawbacks, fixes = [], []
    drawbacks.append({"issue": "Stress concentration at the bracket root", "severity": "high",
                      "detail": "A plain L-bracket under load peaks in bending stress at the inside corner."})
    fixes.append({"id": "F1", "fix": "Add a center gusset between the upright and the base", "addresses": "Stress concentration"})
    if r["tolerance_mm"] < 0.3:
        drawbacks.append({"issue": f"±{r['tolerance_mm']:g} mm is tighter than FDM printing holds", "severity": "medium",
                          "detail": "Hole positions may need machining or a resin/CNC process."})
        fixes.append({"id": "F2", "fix": "Apply the tight tolerance only to the mounting-hole pattern; ISO 2768-m elsewhere", "addresses": "Tolerance"})
    if "outdoor" in (wc.get("environment") or []):
        drawbacks.append({"issue": "UV exposure", "severity": "medium", "detail": "PLA/ABS degrade outdoors."})
        fixes.append({"id": f"F{len(fixes) + 1}", "fix": "Restrict materials to UV-stable options (ASA, aluminum, stainless)", "addresses": "UV exposure"})
    if wc.get("max_temp_c") and wc["max_temp_c"] > 60:
        drawbacks.append({"issue": f"{wc['max_temp_c']:g} °C service temperature", "severity": "medium", "detail": "Rules out PLA, PETG, TPU and standard resins."})
    fixes.append({"id": f"F{len(fixes) + 1}", "fix": "Use 6 mm plate thickness and Ø5.5 mm clearance holes for M5 fasteners", "addresses": "Fastening"})
    questions = []
    if not r.get("dimensions_mm") and not p.get("answers"):
        questions.append("What overall size should the bracket be (width × depth × height in mm)?")
    return {
        "summary": f"Build a gusseted mounting bracket for: {p['prompt']}. Overall {' × '.join(f'{v:g}' for v in _dims(p))} mm.",
        "drawbacks": drawbacks,
        "proposed_fixes": fixes,
        "assumptions": ["M5 fasteners", "Z up, base on the ground plane", "6 mm wall/plate thickness"],
        "questions_for_user": questions,
    }


def _plan(p: dict, feedback) -> dict:
    W, D, H = _dims(p)
    t = 6.0
    L = round(min(D - t - 10, H - t - 10) * 0.6, 1)
    gusset = any("gusset" in f.get("fix", "").lower() for f in p.get("accepted_fixes", [])) or not p.get("review")
    upright_parts = [{
        "id": "upright_plate", "name": "Upright plate",
        "description": f"Rectangular plate {W:g} wide (X), {t:g} thick (Y), {H - t:g} tall (Z) with two Ø5.5 holes through Y at mid-height, 15 mm in from each side.",
        "local_frame": "center of bottom face", "envelope_mm": [W, t, H - t], "features": ["2x Ø5.5 through holes"],
        "interfaces": ["bottom face sits on base plate top face"], "position_mm": [0, t / 2, 0], "rotation_deg": [0, 0, 0],
        "quantity": 1, "solids": 1, "material_hint": "aluminum 6061",
    }]
    if gusset:
        upright_parts.append({
            "id": "gusset", "name": "Center gusset",
            "description": f"Right-triangle web, legs {L:g} along +Y and +Z, {t:g} thick in X, centered on X=0.",
            "local_frame": "right-angle corner at origin", "envelope_mm": [t, L, L], "features": ["triangular web"],
            "interfaces": ["back face against upright plate", "bottom face on base plate"], "position_mm": [0, t, 0],
            "rotation_deg": [0, 0, 0], "quantity": 1, "solids": 1, "material_hint": "aluminum 6061",
        })
    note = (" Revised per feedback: " + "; ".join(feedback)) if feedback else ""
    return {
        "assembly_name": "mount_bracket",
        "approach": ("Three plate-style parts so each can be cut or printed flat: a drilled base, an upright, and a gusset "
                     "that carries the bending load at the root. The base and the upright group are separate sub-assemblies "
                     "so two sub-engineers can model them in parallel." + note),
        "overall_envelope_mm": [W, D, H],
        "subassemblies": [
            {"id": "base", "name": "Base", "purpose": "Bolts to the mounting surface", "position_mm": [0, 0, 0], "rotation_deg": [0, 0, 0],
             "parts": [{
                 "id": "base_plate", "name": "Base plate",
                 "description": f"Plate {W:g} × {D:g} × {t:g} mm, four Ø5.5 through holes 10 mm in from each edge, 3 mm vertical corner fillets.",
                 "local_frame": "center of bottom face", "envelope_mm": [W, D, t], "features": ["4x Ø5.5 through holes", "R3 corners"],
                 "interfaces": ["upright sits on top face along the -Y edge"], "position_mm": [0, 0, 0], "rotation_deg": [0, 0, 0],
                 "quantity": 1, "solids": 1, "material_hint": "aluminum 6061"}]},
            {"id": "upright", "name": "Upright", "purpose": "Carries the mounted device", "position_mm": [0, -D / 2, t], "rotation_deg": [0, 0, 0],
             "parts": upright_parts},
        ],
        "tests": ["Gusset faces touch both plates without overlap", "Overall envelope matches request"],
        "risks": ["Fastener torque on thin plastic plates", "Weld or adhesive joint not modeled"],
        "questions_for_user": [],
    }


def _code(p: dict, part: dict, attempt: int) -> str:
    ex, ey, ez = part["envelope_mm"]
    pid = part["id"]
    if pid == "base_plate":
        body = (f"L, W, T = {ex:g}, {ey:g}, {ez:g}\n"
                "result = (cq.Workplane('XY').box(L, W, T, centered=(True, True, False))\n"
                "          .faces('>Z').workplane()\n"
                "          .rect(L - 20, W - 20, forConstruction=True).vertices().hole(5.5)\n"
                "          .edges('|Z').fillet(3))")
    elif pid == "upright_plate":
        body = (f"W, T, H = {ex:g}, {ey:g}, {ez:g}\n"
                "result = (cq.Workplane('XY').box(W, T, H, centered=(True, True, False))\n"
                "          .faces('>Y').workplane(centerOption='CenterOfBoundBox')\n"
                "          .pushPoints([(-(W / 2 - 15), 0), (W / 2 - 15, 0)]).hole(5.5))")
    elif pid == "gusset":
        if attempt == 1:  # deliberate failure to exercise the test/retry loop
            body = (f"L = {ey:g}\n"
                    "result = cq.Workplane('YZ').polyline([(0, 0), (L, 0), (0, L)]).close().extrude(thickness / 2, both=True)")
        else:
            body = (f"t, L = {ex:g}, {ey:g}\n"
                    "result = cq.Workplane('YZ').polyline([(0, 0), (L, 0), (0, L)]).close().extrude(t / 2, both=True)")
    else:
        body = f"result = cq.Workplane('XY').box({ex:g}, {ey:g}, {ez:g}, centered=(True, True, False))"
    notes = "Fixed the undefined thickness variable." if (pid == "gusset" and attempt > 1) else f"Modeled {part['name']} from its envelope and features."
    return f"<notes>{notes}</notes>\n<question>none</question>\n```python\nimport cadquery as cq\n{body}\n```"


def _memo(f: dict) -> str:
    r = f["requirements"]
    rows = []
    for s in f["subassemblies"]:
        for pt in s["parts"]:
            bb = pt.get("bbox_mm") or []
            rows.append(f"| {pt['name']} | {s['name']} | {' × '.join(f'{v:g}' for v in bb)} | {(pt.get('volume_mm3') or 0) / 1000:.1f} cm³ |")
    fixes = "\n".join(f"- {x['fix']}" for x in f["accepted_fixes"]) or "- None accepted"
    failing = [t for t in f["assembly_tests"] if not t["passed"]]
    return "\n".join([
        f"## Technical memo — rev {f['revision']}", "",
        "### Purpose", f"Mounting bracket for {r.get('application') or 'general use'}, quantity {r.get('quantity', 1)}, "
        f"tolerance ±{r.get('tolerance_mm', 0.3):g} mm.", "",
        "### Design decisions", f.get("plan_approach", ""), "", "Accepted review fixes:", fixes, "",
        "### Assembly breakdown", "| Part | Sub-assembly | Size (mm) | Volume |", "|---|---|---|---|", *rows, "",
        "### Verification",
        f"Every part passed automated geometry tests; {f['part_test_failures_resolved']} failed attempt(s) were caught and corrected by the sub-engineers. "
        + ("Assembly tests all passed." if not failing else "Assembly tests FAILED: " + "; ".join(t["name"] for t in failing)), "",
        "### Open risks", *[f"- {x}" for x in f.get("risks", [])], "",
        "### Recommendation", "Ready for the machinist to select process and material." if not failing else "Revise before hand-off.",
    ])


def _machinist(p: dict, study: dict) -> dict:
    feasible = [s for s in study["strategies"] if s["feasible"]]
    pick = min((s for s in feasible if s["within_budget"]), key=lambda s: s["total_cost"], default=None) or (feasible[0] if feasible else study["strategies"][0])
    send_back = not feasible
    procs = sorted({a["process_name"] for a in pick["assignments"]})
    return {
        "recommended_strategy": pick["id"],
        "rationale": (f"{pick['label']} makes the order for ${pick['total_cost']:,.2f} in about {pick['lead_days']} days using "
                      f"{', '.join(procs) or 'no process'}. " + ("It fits the budget." if pick["within_budget"] else "It is over budget.")),
        "process_notes": [f"{a['part_id']}: {a['process_name']} in {a['material_name']} — {'; '.join(a['notes']) or 'standard setup'}" for a in pick["assignments"]],
        "dfm_changes": ["Keep all plates the same thickness so they nest on one sheet",
                        "Use 1.5 mm inside radii where the gusset meets the plates if milled from solid"],
        "send_back_to_engineer": send_back,
        "send_back_reason": "No process can make every part under the current constraints." if send_back else "",
        "questions_for_user": [],
    }
