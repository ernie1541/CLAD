"""End-to-end test of the Engineer state machine, simulating the lead and worker agents."""
import json
from pathlib import Path

import pytest

from engineer_tools import Engine, EngineError, EngineerConfig
from engineer_tools.units import parse_onshape_expression, parse_variables, safe_eval

EX = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def eng(tmp_path):
    # Low thresholds so the 3-part example parallelizes.
    return Engine(EngineerConfig(runs_dir=tmp_path, spawn_if_parts_over=2, max_workers=4))


def load(name):
    return json.loads((EX / name).read_text())


REVIEW = {
    "issues": [
        {"id": 1, "category": "structural", "severity": "Critical", "issue": "Sharp inside corner at arm root",
         "why_it_matters": "Kt ~3 under bending", "proposed_fix": "Add 3 mm fillet", "changes_design_intent": False,
         "effect_on_design_intent": "none"},
        {"id": 2, "category": "manufacturability", "severity": "Minor", "issue": "Non-standard 6.2 mm hole",
         "why_it_matters": "Needs custom drill", "proposed_fix": "Use 6.6 mm (M6 clearance)",
         "changes_design_intent": False, "effect_on_design_intent": "none"},
    ],
    "assumptions": ["Wall fasteners are M6 class 8.8"],
    "questions": [{"id": "finish", "question": "Anodize?", "why_needed": "affects tolerances",
                   "default_if_unanswered": "clear anodize"}],
    "checked_summary": "Bending at arm root, bolt shear, hole sizes, envelope.",
}

DECISION = {"decisions": [{"issue_id": 1, "decision": "approve"},
                          {"issue_id": 2, "decision": "modify", "note": "Use 6.4 mm instead"}]}

VARS_OK = "\n".join([
    "Variables in Variable Studio:",
    "- plate_length = 120 mm", "- plate_width = 60 mm", "- plate_thickness = 6 mm",
    "- arm_length = 100 mm", "- arm_height = 30 mm", "- arm_thickness = 8 mm",
    "- hole_dia = 6.4 mm", "- hole_spacing = 80 mm", "- pin_dia = 8 mm", "- root_fillet = 3 mm",
])


def build_order(eng, run_id, order, *, conflict=False):
    """Simulate one worker run."""
    wo = eng.get_work_order(run_id, order["order_id"])["order"]
    assert all(p["value_in"] is not None for p in wo["parameters"] if p["unit"] == "mm")
    conv = eng.convert(run_id, ["plate_length/2", "hole_spacing - hole_dia"])["results"]
    assert conv[0]["mm"] == 60 and abs(conv[0]["in"] - 60 / 25.4) < 1e-6
    calc_ids = []
    for sub in wo["subassemblies"]:
        for p in sub["parts"]:
            r = eng.report_part(run_id, wo["order_id"], {
                "part": p["name"], "part_studio_element_id": f"ps-{wo['order_id']}", "part_id": "JHD",
                "volume_m3": 4.32e-5, "bbox_low_m": [0, 0, 0], "bbox_high_m": [0.12, 0.06, 0.006]})
            assert r["recorded"]["mass_g"] > 0
            if p["name"] == "arm":
                c = eng.hand_calc(run_id, "arm", "200 N tip load", "cantilever_end_load", "al_6061_t6", 200,
                                  length_mm=100, section={"kind": "rect", "b": 8, "h": 30}, kt=1.5,
                                  by=wo["order_id"])
                calc_ids.append(c["calc_id"])
    sub = {"built_parts": [p["name"] for s in wo["subassemblies"] for p in s["parts"]],
           "calc_ids": calc_ids, "min_wall_thickness_mm": 6, "notes": "ok"}
    if conflict:
        sub["conflicts"] = [{"interface_id": wo["interfaces"][0]["id"], "description": "hole spacing too tight",
                             "proposed_resolution": "widen to 85"}]
    return eng.submit_work_result(run_id, wo["order_id"], sub)


def run_build(eng, run_id, conflict_on=None):
    eng.set_onshape_target(run_id, "doc1", "ws1")
    seen = set()
    for _ in range(10):
        nxt = eng.next_orders(run_id)
        for t in nxt["spawn"]:
            assert "engineer__get_work_order" in t["task"]
            build_order(eng, run_id, t, conflict=(t["order_id"] == conflict_on and t["order_id"] not in seen))
            seen.add(t["order_id"])
        if nxt["all_done"] and not nxt["spawn"]:
            break
    return seen


def test_full_pipeline(eng, tmp_path):
    s = eng.start_run(load("brief_shelf_bracket.json"), load("sketch_shelf_bracket.json"))
    rid = s["run_id"]
    assert s["phase"] == "review" and s["iteration"] == "1/3"

    with pytest.raises(EngineError, match="Not allowed"):
        eng.submit_plan(rid, load("plan_shelf_bracket.json"))

    r = eng.submit_review(rid, REVIEW)
    assert r["phase"] == "awaiting_review_approval" and "| 1 |" in r["markdown_for_user"]

    with pytest.raises(EngineError, match="user_reply"):
        eng.record_review_decision(rid, DECISION, "")
    with pytest.raises(EngineError, match=r"No user decision for issues \[2\]"):
        eng.record_review_decision(rid, {"decisions": [{"issue_id": 1, "decision": "approve"}]}, "ok #1")
    d = eng.record_review_decision(rid, DECISION, "approve 1, for 2 use 6.4mm")
    assert d["phase"] == "plan" and d["answers"]["finish"].startswith("(default)")
    assert d["apply_fixes"][1]["fix"] == "Use 6.4 mm instead"

    bad = load("plan_shelf_bracket.json")
    bad["subassemblies"][0]["parts"][0]["material"] = "unobtainium"
    with pytest.raises(EngineError, match="unobtainium"):
        eng.submit_plan(rid, bad)

    p = eng.submit_plan(rid, load("plan_shelf_bracket.json"))
    assert p["parallel"] is True and len(p["orders"]) == 3
    assert {o["wave"] for o in p["orders"]} == {0, 1}
    assert {"name": "hole_dia", "expression": "6.4 mm"} in p["variables_to_set"]

    with pytest.raises(EngineError, match="set_onshape_target"):
        eng.next_orders(rid)
    eng.set_onshape_target(rid, "doc1", "ws1")

    first = eng.next_orders(rid)
    assert len(first["spawn"]) == 2  # wave 0 only; the arm waits for the wall plate
    arm_order = next(o["order_id"] for o in p["orders"] if o["wave"] == 1)
    assert arm_order not in {t["order_id"] for t in first["spawn"]}

    # worker misuse is rejected
    wo0 = first["spawn"][0]["order_id"]
    with pytest.raises(EngineError, match="not in order"):
        eng.report_part(rid, wo0, {"part": "arm", "part_studio_element_id": "x", "volume_m3": 1e-5})
    wo0_parts = [p["name"] for s in eng.get_work_order(rid, wo0)["order"]["subassemblies"] for p in s["parts"]]
    with pytest.raises(EngineError, match="report_part"):
        eng.submit_work_result(rid, wo0, {"built_parts": wo0_parts})
    with pytest.raises(EngineError, match="exactly your parts"):
        eng.submit_work_result(rid, wo0, {"built_parts": []})

    for t in first["spawn"]:
        build_order(eng, rid, t)
    second = eng.next_orders(rid)
    assert [t["order_id"] for t in second["spawn"]] == [arm_order]
    upstream = eng.get_work_order(rid, arm_order)["order"]["upstream_parts"]
    assert [u["part"] for u in upstream] == ["wall_plate"]
    build_order(eng, rid, second["spawn"][0], conflict=True)

    st = eng.build_status(rid)
    assert st["all_done"] and any("interface conflict" in x for x in st["problems"])
    rw = eng.issue_rework(rid, arm_order, "Keep hole_spacing = 80 mm (frozen). Move the arm instead.")
    assert "REWORK #1" in rw["spawn"][0]["task"]
    build_order(eng, rid, rw["spawn"][0])
    assert eng.build_status(rid)["problems"] == []

    integ = {"assembly_element_id": "asm1", "interference_raw": "No interference", "interference_resolved": True,
             "calc_ids": [], "min_wall_thickness_mm": 6}
    with pytest.raises(EngineError, match="parameters_match_plan"):
        eng.submit_integration(rid, integ)
    studios = sorted({t for t in eng.store.read(rid)["orders"]})
    bad_vars = eng.verify_variables(rid, f"ps-{studios[0]}", VARS_OK.replace("hole_dia = 6.4 mm", "hole_dia = 0.25 in"))
    assert not bad_vars["ok"] and "hole_dia" in bad_vars["mismatches"][0]
    with pytest.raises(EngineError, match="parameters_match_plan"):
        eng.submit_integration(rid, integ)
    for oid in studios:
        assert eng.verify_variables(rid, f"ps-{oid}", VARS_OK)["ok"]

    v = eng.submit_integration(rid, integ)
    assert v["phase"] == "memo" and "PASS" in v["verification_markdown"]

    memo = {"summary": "Two-part bracket plus pin.", "traceability": [
        {"requirement": "Hold 20 kg shelf load", "how_met": "arm SF 3.3", "verified": True},
        {"requirement": "Mount with two M6 screws", "how_met": "2x 6.4 mm holes, 80 mm spacing", "verified": True}],
        "materials_and_dims": "6061-T6, 6 mm plate", "verification_summary": "All checks pass",
        "machinist_notes": [{"part": "wall_plate", "note": "Hole spacing 80 ±0.05", "critical": True}]}
    with pytest.raises(EngineError, match="Traceability"):
        eng.submit_memo(rid, {**memo, "traceability": memo["traceability"][:1]})
    m = eng.submit_memo(rid, memo)
    assert m["phase"] == "awaiting_handoff_approval" and Path(m["memo_path"]).exists()

    with pytest.raises(EngineError, match="Not allowed"):
        eng.finalize_handoff(rid, {})
    eng.record_gate(rid, "handoff", True, "Looks good, send it")
    h = eng.finalize_handoff(rid, {"critical_tolerances": [
        {"part": "wall_plate", "feature": "hole spacing", "nominal_mm": 80, "tolerance": "±0.05"}],
        "exports": [{"part": "wall_plate", "path_or_url": "/tmp/wall_plate.step"}]})
    pkg = json.loads(Path(h["handoff_path"]).read_text())
    assert pkg["iteration"] == 1 and len(pkg["parts"]) == 3
    assert any("No STEP export recorded for arm" in o for o in pkg["open_items"])
    assert pkg["verification"]["stress_checks"][0]["safety_factor"] > 2

    # ---- iteration 2: engineering-only machinist feedback ----
    fb = {"source": "machinist", "requests": ["Loosen hole tolerance to ±0.1"]}
    cl = {"items": [{"request": fb["requests"][0], "restated": "hole spacing ±0.1", "classification": "engineering_only",
                     "rationale": "tolerance only", "proposed_change": "spacing tol ±0.1"}]}
    f = eng.submit_feedback(rid, fb, cl)
    assert f["route"] == "engineer" and f["iteration"] == "2/3" and f["phase"] == "awaiting_feedback_approval"
    eng.record_gate(rid, "feedback", True, "yes do it")
    eng.submit_plan(rid, load("plan_shelf_bracket.json"))
    assert eng.run_status(rid)["iteration"] == "2/3"




def test_design_intent_routes_to_designer_and_limit(eng):
    rid = eng.start_run(load("brief_shelf_bracket.json"), load("sketch_shelf_bracket.json"))["run_id"]
    # fast-forward a run to DONE
    eng.submit_review(rid, {"issues": [], "checked_summary": "all fine"})
    eng.record_review_decision(rid, {}, "go")
    eng.submit_plan(rid, load("plan_shelf_bracket.json"))
    run_build(eng, rid)
    for oid in eng.store.read(rid)["orders"]:
        assert eng.verify_variables(rid, f"ps-{oid}", VARS_OK)["ok"]
    eng.submit_integration(rid, {"interference_resolved": True, "min_wall_thickness_mm": 6})
    eng.submit_memo(rid, {"summary": "s", "traceability": [{"requirement": "a", "how_met": "b", "verified": True},
                                                           {"requirement": "c", "how_met": "d", "verified": True}],
                          "materials_and_dims": "x", "verification_summary": "y"})
    eng.record_gate(rid, "handoff", False, "No - make it 50 mm narrower so it fits my shelf")
    intent = {"items": [{"request": "narrower", "restated": "reduce width 50 mm", "classification": "design_intent",
                         "rationale": "changes envelope", "proposed_change": "width 10 mm"}]}
    r = eng.submit_feedback(rid, {"source": "user", "requests": ["narrower"]}, intent)
    assert r["route"] == "designer" and r["phase"] == "routed_to_designer" and r["iteration"] == "2/3"
    r = eng.restart_from_designer(rid)
    assert r["phase"] == "review" and r["iteration"] == "2/3"


def test_iteration_limit_tradeoffs(eng):
    rid = eng.start_run(load("brief_shelf_bracket.json"), load("sketch_shelf_bracket.json"))["run_id"]
    st = eng.store.read(rid)
    st["iteration"], st["phase"] = 3, "done"
    eng.store.write(rid, st)
    cl = {"items": [{"request": "r", "restated": "r", "classification": "engineering_only", "rationale": "x",
                     "proposed_change": "y"}]}
    r = eng.submit_feedback(rid, {"requests": ["r"]}, cl)
    assert r["phase"] == "iteration_limit_reached" and r["iteration"] == "3/3"
    with pytest.raises(EngineError, match="at least 2"):
        eng.submit_tradeoffs(rid, {"conflict": "c", "options": [{"label": "a", "description": "d", "pros": [], "cons": []}]})
    t = eng.submit_tradeoffs(rid, {"conflict": "Cost vs stiffness", "options": [
        {"label": "Keep 7075", "description": "stiff", "pros": ["SF 4"], "cons": ["$$"]},
        {"label": "6061 + thicker arm", "description": "cheaper", "pros": ["$"], "cons": ["+40 g"]}],
        "recommendation": "Option 2"})
    assert "This is your call" in t["markdown_for_user"]
    r = eng.record_gate(rid, "tradeoff", True, "Option 2")
    assert r["phase"] == "plan" and r["iteration"] == "4/4"


def test_hand_calc_and_units(eng):
    rid = eng.start_run(load("brief_shelf_bracket.json"), load("sketch_shelf_bracket.json"))["run_id"]
    c = eng.hand_calc(rid, "plate", "tip", "cantilever_end_load", "al_6061_t6", 200, length_mm=100,
                      section={"kind": "rect", "b": 40, "h": 6})
    assert c["max_stress_mpa"] == pytest.approx(83.333, abs=1e-3)
    assert c["safety_factor"] == pytest.approx(3.312, abs=1e-3)
    assert c["max_deflection_mm"] == pytest.approx(200 * 100 ** 3 / (3 * 68900 * 720), rel=1e-4)
    with pytest.raises(EngineError, match="not in the reference DB"):
        eng.hand_calc(rid, "p", "t", "axial", "mithril", 1, section={"kind": "circle", "d": 5})
    assert safe_eval("a/2 + sqrt(b)", {"a": 10, "b": 16}) == 9
    with pytest.raises(ValueError):
        safe_eval("__import__('os')", {})
    assert parse_onshape_expression("0.25 in") == (pytest.approx(6.35), "mm")
    assert parse_onshape_expression("#a + 2 mm") == (None, None)
    assert parse_variables("- a = 6 mm (thick)\n- b = 45 deg") == {"a": "6 mm", "b": "45 deg"}
    assert eng.lookup_material("6061")["matches"][0]["id"] == "al_6061_t6"
