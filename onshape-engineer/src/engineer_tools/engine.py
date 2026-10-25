"""The Engineer state machine. Every MCP tool is a thin wrapper around a method here.

The engine enforces the parts the models must not be trusted with:
phase order, approval gates, the iteration limit, frozen interface contracts,
unit conversion, stress-check provenance and the handoff schema.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from . import planner, render
from .analysis import hand_calc as _hand_calc
from .config import EngineerConfig
from .reference_db import ReferenceDB
from .schemas import (CheckResult, ClassifiedFeedback, DesignBrief, DesignReview, HandoffExtras,
                      HandoffPackage, HandoffPart, IntegrationReport, MachinistFeedback, ModelingPlan,
                      OnshapeTarget, PartRecord, PartReport, ReviewDecision, SketchInput, StressCheck,
                      TechMemo, TradeoffReport, UpstreamPart, VerificationReport, WorkerSubmission,
                      WorkOrder)
from .store import RunStore
from .units import mm_to_in, parse_onshape_expression, parse_variables, safe_eval


class EngineError(Exception):
    """Raised for anything the agent must fix. The message tells it how."""


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #
REVIEW = "review"
GATE_REVIEW = "awaiting_review_approval"
PLAN = "plan"
BUILD = "build"
MEMO = "memo"
GATE_HANDOFF = "awaiting_handoff_approval"
HANDOFF = "handoff"
DONE = "done"
GATE_FEEDBACK = "awaiting_feedback_approval"
ROUTED = "routed_to_designer"
TRADEOFF = "iteration_limit_reached"
GATE_TRADEOFF = "awaiting_tradeoff_decision"
STOPPED = "stopped"
ABORTED = "aborted"

NEXT_STEP = {
    REVIEW: "Analyze the brief and sketch, then call submit_review.",
    GATE_REVIEW: "GATE 1: show the user the review markdown and ask_user for a decision on every issue plus "
                 "answers to the questions. Then call record_review_decision with their verbatim reply.",
    PLAN: "Write the modeling plan and call submit_plan.",
    BUILD: "Create the Onshape document and record it (set_onshape_target), then loop: "
           "next_orders -> sessions_spawn each task with agentId='engineer-worker' -> sessions_yield -> "
           "build_status. Rework with issue_rework. When every order is done: get_variables + verify_variables "
           "for every Part Studio, build the assembly, check interference, hand_calc, then submit_integration.",
    MEMO: "Write the technical memo and call submit_memo.",
    GATE_HANDOFF: "GATE 2: show the user the memo and ask_user to approve the handoff to the Machinist. "
                  "Then record_gate(gate='handoff'). If they want changes, call submit_feedback with source='user'.",
    HANDOFF: "Export STEP for each part (export_part_studio) and call finalize_handoff.",
    DONE: "Handoff delivered. If the Machinist sends feedback, call submit_feedback.",
    GATE_FEEDBACK: "Show the user the classified feedback and ask_user to approve the engineering-only changes. "
                   "Then record_gate(gate='feedback').",
    ROUTED: "Design-intent changes go back to the Designer. When the Designer re-approves a sketch, call "
            "restart_from_designer with the updated brief/sketch.",
    TRADEOFF: "Iteration limit reached. Lay out the trade-offs and call submit_tradeoffs. Do NOT decide for the user.",
    GATE_TRADEOFF: "Show the trade-offs and ask_user to choose. Then record_gate(gate='tradeoff').",
    STOPPED: "Run stopped by the user's decision. Nothing further to do.",
    ABORTED: "Run aborted.",
}


def _parse(model: type[BaseModel], data: Any, what: str) -> Any:
    if isinstance(data, model):
        return data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise EngineError(f"{what}: not valid JSON ({e})") from e
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise EngineError(f"{what} failed validation. Fix these fields and resubmit:\n{e}") from e


class Engine:
    def __init__(self, config: Optional[EngineerConfig] = None):
        self.cfg = config or EngineerConfig()
        self.db = ReferenceDB(self.cfg.materials_db)
        self.store = RunStore(self.cfg.runs_dir)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _load(self, run_id: str) -> dict[str, Any]:
        try:
            return self.store.read(run_id)
        except FileNotFoundError as e:
            raise EngineError(f"Unknown run_id '{run_id}'. Use list_runs.") from e

    def _save(self, st: dict[str, Any]) -> None:
        st["updated"] = time.time()
        self.store.write(st["run_id"], st)

    def _require(self, st: dict[str, Any], *phases: str) -> None:
        if st["phase"] not in phases:
            raise EngineError(f"Not allowed in phase '{st['phase']}' (needs {list(phases)}). "
                              f"Next step: {NEXT_STEP[st['phase']]}")

    def _max_iter(self, st: dict[str, Any]) -> int:
        return self.cfg.max_iterations + st.get("extra_iterations", 0)

    def _status(self, st: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"run_id": st["run_id"], "phase": st["phase"],
                "iteration": f"{st['iteration']}/{self._max_iter(st)}",
                "next_step": NEXT_STEP[st["phase"]], **extra}

    def _plan(self, st: dict[str, Any]) -> ModelingPlan:
        if not st.get("plan"):
            raise EngineError("No plan yet.")
        return ModelingPlan.model_validate(st["plan"])

    def _order_file(self, order_id: str) -> str:
        return f"orders/{order_id}.json"

    def _read_order(self, run_id: str, order_id: str) -> dict[str, Any]:
        try:
            return self.store.read(run_id, self._order_file(order_id))
        except FileNotFoundError as e:
            raise EngineError(f"Unknown order '{order_id}' in run '{run_id}'") from e

    def _gate(self, st: dict[str, Any], gate: str, approved: bool, user_reply: str, **data: Any) -> None:
        if not user_reply or not user_reply.strip():
            raise EngineError("user_reply must be the user's actual answer (verbatim from ask_user). "
                              "Never approve on the user's behalf.")
        rec = {"gate": gate, "approved": approved, "user_reply": user_reply, "iteration": st["iteration"],
               "t": time.time(), **data}
        st.setdefault("gates", []).append(rec)
        self.store.log(st["run_id"], "gate", **rec)

    # ------------------------------------------------------------------ #
    # shared tools
    # ------------------------------------------------------------------ #
    def list_runs(self) -> list[dict[str, Any]]:
        return self.store.list_runs()

    def lookup_material(self, query: str = "") -> dict[str, Any]:
        if not query.strip():
            return {"materials": [{"id": k, "name": v["name"]} for k, v in sorted(self.db.materials.items())],
                    "note": self.db.meta.get("note")}
        hits = self.db.search(query)
        return {"matches": hits, "note": self.db.meta.get("note")} if hits else {
            "matches": [], "available_ids": self.db.keys(),
            "note": "Not in the reference DB. Do not invent properties: flag it as an open item."}

    def convert(self, run_id: str, expressions: list[str]) -> dict[str, Any]:
        st = self._load(run_id)
        plan = self._plan(st)
        names = {p.name: p.value for p in plan.parameters}
        out = []
        for e in expressions:
            try:
                v = safe_eval(e, names)
                out.append({"expr": e, "mm": round(v, 6), "in": round(mm_to_in(v), 6)})
            except Exception as ex:  # noqa: BLE001
                out.append({"expr": e, "error": str(ex)})
        return {"results": out, "note": "Use 'in' for numeric Onshape MCP arguments (it takes inches)."}

    def hand_calc(self, run_id: str, part: str, load_case: str, case: str, material: str, force_n: float,
                  length_mm: Optional[float] = None, section: Optional[dict[str, float]] = None,
                  n_bolts: Optional[int] = None, bolt_minor_dia_mm: Optional[float] = None,
                  kt: float = 1.0, by: str = "lead") -> dict[str, Any]:
        with self.store.lock(run_id):
            st = self._load(run_id)
            try:
                res = _hand_calc(self.db, case=case, material=material, force_n=force_n, length_mm=length_mm,
                                 section=section, n_bolts=n_bolts, bolt_minor_dia_mm=bolt_minor_dia_mm,
                                 kt=kt, min_sf=self.cfg.min_safety_factor)
            except (ValueError, KeyError) as e:
                raise EngineError(str(e)) from e
            cid = f"calc-{len(st.setdefault('calcs', {})) + 1:03d}"
            st["calcs"][cid] = {"calc_id": cid, "part": part, "load_case": load_case, "iteration": st["iteration"],
                                "by": by, "inputs": {"case": case, "material": material, "force_n": force_n,
                                                     "length_mm": length_mm, "section": section,
                                                     "n_bolts": n_bolts, "bolt_minor_dia_mm": bolt_minor_dia_mm,
                                                     "kt": kt}, "result": res}
            self._save(st)
        return {"calc_id": cid, **res, "note": "Cite this calc_id in your submission."}

    # ------------------------------------------------------------------ #
    # Phase 1: review
    # ------------------------------------------------------------------ #
    def start_run(self, brief: Any, sketch: Any) -> dict[str, Any]:
        b = _parse(DesignBrief, brief, "brief")
        s = _parse(SketchInput, sketch, "sketch")
        run_id = self.store.new_run_id(b.title)
        st = {"run_id": run_id, "title": b.title, "created": time.time(), "iteration": 1, "phase": REVIEW,
              "brief": b.model_dump(), "sketch": s.model_dump(), "gates": [], "calcs": {}, "orders": {},
              "history": []}
        with self.store.lock(run_id):
            self._save(st)
            self.store.log(run_id, "start", title=b.title)
        return self._status(st, materials_available=self.db.keys(),
                            config={"min_safety_factor": self.cfg.min_safety_factor,
                                    "max_iterations": self.cfg.max_iterations,
                                    "spawn_if_parts_over": self.cfg.spawn_if_parts_over,
                                    "spawn_if_subassemblies_over": self.cfg.spawn_if_subassemblies_over,
                                    "max_workers": self.cfg.max_workers})

    def run_status(self, run_id: str) -> dict[str, Any]:
        st = self._load(run_id)
        orders = {oid: {k: o[k] for k in ("status", "wave", "rework_count")} for oid, o in st["orders"].items()}
        return self._status(st, title=st["title"], orders=orders,
                            gates=[{k: g[k] for k in ("gate", "approved", "iteration")} for g in st["gates"]])

    def get_run_context(self, run_id: str) -> dict[str, Any]:
        """Everything the lead needs to re-orient after a long session or compaction."""
        st = self._load(run_id)
        return {k: st.get(k) for k in ("run_id", "phase", "iteration", "brief", "sketch", "review",
                                       "review_decision", "plan", "onshape", "pending_changes",
                                       "design_changes")} | {"next_step": NEXT_STEP[st["phase"]]}

    def submit_review(self, run_id: str, review: Any) -> dict[str, Any]:
        r = _parse(DesignReview, review, "review")
        ids = [i.id for i in r.issues]
        if len(set(ids)) != len(ids):
            raise EngineError("Issue ids must be unique")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, REVIEW)
            st["review"] = r.model_dump(mode="json")
            st["phase"] = GATE_REVIEW
            self._save(st)
        md = render.review_md(r, st["iteration"], self._max_iter(st))
        self.store.write_text(run_id, f"review_i{st['iteration']}.md", md)
        return self._status(st, markdown_for_user=md)

    def record_review_decision(self, run_id: str, decision: Any, user_reply: str,
                               proceed: bool = True) -> dict[str, Any]:
        d = _parse(ReviewDecision, decision, "decision")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, GATE_REVIEW)
            review = DesignReview.model_validate(st["review"])
            if not proceed:
                self._gate(st, "review", False, user_reply, decision=d.model_dump())
                st["phase"] = ABORTED if "abort" in user_reply.lower() else REVIEW
                st.setdefault("review_feedback", []).append(user_reply)
                self._save(st)
                return self._status(st, note="Revise the review using the user's reply, then submit_review again."
                                    if st["phase"] == REVIEW else "Run aborted by user.")
            decided = {x.issue_id for x in d.decisions}
            missing = [i.id for i in review.issues if i.id not in decided]
            if missing:
                raise EngineError(f"No user decision for issues {missing}. Ask the user about them.")
            for x in d.decisions:
                if x.decision == "modify" and not x.note:
                    raise EngineError(f"Issue {x.issue_id}: 'modify' needs the user's note.")
            answers = dict(d.answers)
            unanswered = []
            for q in review.questions:
                if q.id not in answers:
                    if q.default_if_unanswered:
                        answers[q.id] = f"(default) {q.default_if_unanswered}"
                    else:
                        unanswered.append(q.id)
            if unanswered:
                raise EngineError(f"Questions {unanswered} have no answer and no default. Ask the user.")
            d.answers = answers
            self._gate(st, "review", True, user_reply, decision=d.model_dump())
            st["review_decision"] = d.model_dump()
            st["phase"] = PLAN
            self._save(st)
        by_id = {i.id: i for i in review.issues}
        apply = [{"issue_id": x.issue_id, "fix": by_id[x.issue_id].proposed_fix if x.decision == "approve" else x.note}
                 for x in d.decisions if x.decision != "reject"]
        rejected = [x.issue_id for x in d.decisions if x.decision == "reject"]
        return self._status(st, apply_fixes=apply, rejected_issues=rejected, answers=answers,
                            rule="Rejected fixes must NOT appear in the plan.")

    # ------------------------------------------------------------------ #
    # Phase 2: plan + dispatch
    # ------------------------------------------------------------------ #
    def _context(self, st: dict[str, Any]) -> str:
        b = DesignBrief.model_validate(st["brief"])
        L = [f"Product: {b.title}. Function: {b.function}.", f"Environment: {b.environment}.",
             f"Size limits: {b.size_limits or 'none given'}. Quantity: {b.quantity}."]
        if b.requirements:
            L.append("Requirements: " + "; ".join(b.requirements))
        rd = st.get("review_decision")
        if rd and st.get("review"):
            issues = {i["id"]: i for i in st["review"]["issues"]}
            for x in rd["decisions"]:
                fix = issues[x["issue_id"]]["proposed_fix"] if x["decision"] == "approve" else x.get("note")
                if x["decision"] != "reject":
                    L.append(f"Approved fix #{x['issue_id']}: {fix}")
            for k, v in rd.get("answers", {}).items():
                L.append(f"Answer {k}: {v}")
        for c in st.get("design_changes", []):
            L.append(f"Approved design change: {c}")
        for c in st.get("pending_changes", []):
            L.append(f"Revision for this iteration: {c}")
        return "\n".join(L)

    def submit_plan(self, run_id: str, plan: Any) -> dict[str, Any]:
        p = _parse(ModelingPlan, plan, "plan")
        errs = planner.validate_plan(p, self.db)
        if errs:
            raise EngineError("Plan rejected:\n- " + "\n- ".join(errs))
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, PLAN)
            brief = DesignBrief.model_validate(st["brief"])
            decision = planner.decide_parallel(p, self.cfg)
            orders = planner.build_orders(run_id, st["iteration"], p, brief, self._context(st), self.cfg, decision)
            if st.get("plan"):
                st["history"].append({"iteration": st["iteration"] - 1, "plan": st["plan"]})
            st["plan"] = p.model_dump(mode="json")
            st["parallel"] = {"parallel": decision.parallel, "reason": decision.reason}
            st["orders"] = {o.order_id: {"status": "pending", "wave": o.wave, "rework_count": 0,
                                         "subassemblies": [s.name for s in o.subassemblies]} for o in orders}
            st.pop("variable_checks", None)
            st.pop("integration", None)
            st.pop("verification", None)
            for o in orders:
                self.store.write(run_id, {"order": o.model_dump(mode="json"), "parts": {}, "submission": None},
                                 self._order_file(o.order_id))
            st["phase"] = BUILD
            self._save(st)
            self.store.write(run_id, st["plan"], f"plan_i{st['iteration']}.json")
        return self._status(st, parallel=decision.parallel, reason=decision.reason,
                            orders=[{"order_id": o.order_id, "wave": o.wave, "parts": o.part_names()}
                                    for o in orders],
                            variables_to_set=[{"name": x.name, "expression": x.onshape_expression}
                                              for x in orders[0].parameters])

    def set_onshape_target(self, run_id: str, document_id: str, workspace_id: str,
                           variable_studio_id: Optional[str] = None,
                           assembly_element_id: Optional[str] = None) -> dict[str, Any]:
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, PLAN, BUILD)
            on = st.get("onshape") or {}
            on.update({"document_id": document_id, "workspace_id": workspace_id})
            if variable_studio_id:
                on["variable_studio_id"] = variable_studio_id
            if assembly_element_id:
                on["assembly_element_id"] = assembly_element_id
            st["onshape"] = on
            self._save(st)
        return self._status(st, onshape=on, reminder="Now call next_orders and spawn the workers.")

    def _ready(self, st: dict[str, Any]) -> list[str]:
        orders = st["orders"]
        out = []
        for oid, o in sorted(orders.items(), key=lambda kv: (kv[1]["wave"], kv[0])):
            if o["status"] != "pending":
                continue
            if all(x["status"] == "done" for x in orders.values() if x["wave"] < o["wave"]):
                out.append(oid)
        return out

    def next_orders(self, run_id: str) -> dict[str, Any]:
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, BUILD)
            if not st.get("onshape", {}).get("document_id"):
                raise EngineError("Call set_onshape_target first (create the Onshape document and Variable Studio).")
            running = sum(1 for o in st["orders"].values() if o["status"] == "dispatched")
            slots = max(0, self.cfg.max_workers - running)
            ready = self._ready(st)[:slots]
            target = OnshapeTarget(document_id=st["onshape"]["document_id"],
                                   workspace_id=st["onshape"]["workspace_id"])
            built = self._built_parts(st)
            tasks = []
            for oid in ready:
                data = self._read_order(run_id, oid)
                order = WorkOrder.model_validate(data["order"])
                order.onshape = target
                deps = {d for s in order.subassemblies for d in s.depends_on}
                plan = self._plan(st)
                dep_parts = {p.name for s in plan.subassemblies if s.name in deps for p in s.parts}
                order.upstream_parts = [UpstreamPart(part=r.part, part_studio_element_id=r.part_studio_element_id,
                                                     part_id=r.part_id) for r in built.values()
                                        if r.part in dep_parts]
                data["order"] = order.model_dump(mode="json")
                self.store.write(run_id, data, self._order_file(oid))
                st["orders"][oid]["status"] = "dispatched"
                st["orders"][oid]["dispatched_at"] = time.time()
                tasks.append({"order_id": oid, "label": f"Build {', '.join(order.part_names())}"[:80],
                              "task": planner.spawn_task(order)})
            self._save(st)
        all_done = all(o["status"] == "done" for o in st["orders"].values())
        waiting = [k for k, v in st["orders"].items() if v["status"] == "dispatched"]
        hint = ("Spawn each task now with sessions_spawn(agentId='engineer-worker', task=..., label=...), "
                "then sessions_yield.") if tasks else (
            "All orders done: assemble and verify." if all_done else
            f"Nothing new is ready. Waiting on {waiting}: sessions_yield, then build_status.")
        return self._status(st, spawn=tasks, waiting_on=waiting, all_done=all_done, hint=hint)

    def _built_parts(self, st: dict[str, Any]) -> dict[str, PartRecord]:
        out: dict[str, PartRecord] = {}
        for oid in st["orders"]:
            data = self.store.read(st["run_id"], self._order_file(oid))
            for name, rec in data["parts"].items():
                out[name] = PartRecord.model_validate(rec)
        return out

    # ------------------------------------------------------------------ #
    # Worker tools
    # ------------------------------------------------------------------ #
    def get_work_order(self, run_id: str, order_id: str) -> dict[str, Any]:
        st = self._load(run_id)
        data = self._read_order(run_id, order_id)
        if st["orders"].get(order_id, {}).get("status") not in ("dispatched",):
            raise EngineError(f"Order {order_id} is not dispatched (status "
                              f"{st['orders'].get(order_id, {}).get('status')}). Stop and report this.")
        return {"order": data["order"], "already_reported_parts": list(data["parts"]),
                "previous_submission": data.get("submission")}

    def report_part(self, run_id: str, order_id: str, report: Any) -> dict[str, Any]:
        r = _parse(PartReport, report, "report")
        with self.store.lock(run_id):
            st = self._load(run_id)
            if st["orders"].get(order_id, {}).get("status") != "dispatched":
                raise EngineError(f"Order {order_id} is not open for reports (finished or not dispatched).")
            data = self._read_order(run_id, order_id)
            order = WorkOrder.model_validate(data["order"])
            specs ={p.name: p for s in order.subassemblies for p in s.parts}
            if r.part not in specs:
                raise EngineError(f"Part '{r.part}' is not in order {order_id}. Your parts: {list(specs)}")
            if r.volume_m3 <= 0:
                raise EngineError("volume_m3 must be > 0. Run eval_featurescript with evVolume and pass the value.")
            vol_mm3 = r.volume_m3 * 1e9
            dens = (self.db.get(specs[r.part].material) or {}).get("density")
            bbox = low = None
            warnings = []
            if r.bbox_low_m and r.bbox_high_m:
                low = [round(v * 1000, 4) for v in r.bbox_low_m]
                bbox = [round((h - l) * 1000, 4) for l, h in zip(r.bbox_low_m, r.bbox_high_m)]
                if any(x <= 0 for x in bbox):
                    raise EngineError("Bounding box has a non-positive size. bbox_high_m must exceed bbox_low_m.")
            else:
                warnings.append("No bounding box reported (get_bounding_box). Envelope can't be checked.")
            if vol_mm3 < 1:
                warnings.append(f"Volume {vol_mm3:.4f} mm^3 is tiny. Did you pass cubic inches or mm^3 instead of m^3?")
            rec = PartRecord(part=r.part, order_id=order_id, material=specs[r.part].material,
                             part_studio_element_id=r.part_studio_element_id, part_id=r.part_id,
                             volume_mm3=round(vol_mm3, 3),
                             mass_g=round(vol_mm3 / 1000 * dens, 3) if dens else None,
                             bbox_mm=bbox, bbox_low_mm=low, notes=r.notes)
            data["parts"][r.part] = rec.model_dump()
            self.store.write(run_id, data, self._order_file(order_id))
            self.store.log(run_id, "part", order_id=order_id, part=r.part, volume_mm3=rec.volume_mm3)
        remaining = [p for p in specs if p not in data["parts"]]
        return {"recorded": rec.model_dump(), "warnings": warnings, "parts_remaining": remaining}

    def submit_work_result(self, run_id: str, order_id: str, submission: Any) -> dict[str, Any]:
        s = _parse(WorkerSubmission, submission, "submission")
        with self.store.lock(run_id):
            st = self._load(run_id)
            data = self._read_order(run_id, order_id)
            order = WorkOrder.model_validate(data["order"])
            parts = set(order.part_names())
            listed = set(s.built_parts) | set(s.failed_parts)
            if listed != parts:
                raise EngineError(f"built_parts + failed_parts must list exactly your parts {sorted(parts)}. "
                                  f"Missing: {sorted(parts - listed)}; unknown: {sorted(listed - parts)}")
            unreported = [p for p in s.built_parts if p not in data["parts"]]
            if unreported:
                raise EngineError(f"Call report_part for {unreported} first, or move them to failed_parts.")
            bad_calcs = [c for c in s.calc_ids if c not in st.get("calcs", {})]
            if bad_calcs:
                raise EngineError(f"Unknown calc ids {bad_calcs}. Only cite ids returned by hand_calc.")
            ids = {i.id for i in order.interfaces}
            for c in s.conflicts:
                if c.interface_id not in ids:
                    raise EngineError(f"Conflict cites unknown interface '{c.interface_id}'. Yours: {sorted(ids)}")
            data["submission"] = s.model_dump()
            self.store.write(run_id, data, self._order_file(order_id))
            st["orders"][order_id]["status"] = "done"
            st["orders"][order_id]["finished_at"] = time.time()
            self._save(st)
            self.store.log(run_id, "submission", order_id=order_id, failed=s.failed_parts,
                           conflicts=len(s.conflicts))
        return {"accepted": True, "order_id": order_id,
                "reply_to_lead": "Done. Reply to the lead with a short summary: parts built, failures, conflicts."}

    # ------------------------------------------------------------------ #
    # Lead: build monitoring and rework
    # ------------------------------------------------------------------ #
    def build_status(self, run_id: str) -> dict[str, Any]:
        st = self._load(run_id)
        rows, problems = [], []
        for oid, meta in sorted(st["orders"].items()):
            data = self.store.read(run_id, self._order_file(oid))
            sub = data.get("submission") or {}
            rows.append({"order_id": oid, "status": meta["status"], "wave": meta["wave"],
                         "rework_count": meta["rework_count"],
                         "parts": {k: {"mass_g": v["mass_g"], "bbox_mm": v["bbox_mm"]} for k, v in data["parts"].items()},
                         "failed_parts": sub.get("failed_parts", []), "conflicts": sub.get("conflicts", []),
                         "calc_ids": sub.get("calc_ids", []), "min_wall_thickness_mm": sub.get("min_wall_thickness_mm"),
                         "notes": sub.get("notes", "")})
            if meta["status"] == "done":
                for f in sub.get("failed_parts", []):
                    problems.append(f"{oid}: part '{f}' failed. Rework or escalate.")
                for c in sub.get("conflicts", []):
                    problems.append(f"{oid}: interface conflict {c['interface_id']}: {c['description']}")
                for cid in sub.get("calc_ids", []):
                    if not st["calcs"][cid]["result"]["passed"]:
                        problems.append(f"{oid}: {cid} fails SF ({st['calcs'][cid]['result']['safety_factor']})")
        all_done = bool(rows) and all(r["status"] == "done" for r in rows)
        return self._status(st, orders=rows, problems=problems, all_done=all_done,
                            hint=("Fix problems with issue_rework (or ask_user if it changes design intent)."
                                  if problems else "All clear so far.") if all_done else
                            "Some orders still running: sessions_yield, or next_orders if a wave finished.")

    def issue_rework(self, run_id: str, order_id: str, instructions: str) -> dict[str, Any]:
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, BUILD)
            meta = st["orders"].get(order_id)
            if meta is None:
                raise EngineError(f"Unknown order {order_id}")
            if meta["status"] == "dispatched":
                raise EngineError(f"{order_id} is still running. Wait for it (sessions_yield).")
            if meta["rework_count"] >= self.cfg.max_rework_per_order:
                raise EngineError(f"{order_id} hit the rework limit ({self.cfg.max_rework_per_order}). "
                                  "Escalate to the user with ask_user.")
            data = self._read_order(run_id, order_id)
            order = WorkOrder.model_validate(data["order"])
            order.rework.append(instructions)
            data["order"] = order.model_dump(mode="json")
            prev = data.get("submission")
            data["submission"] = None
            data.setdefault("previous_submissions", []).append(prev)
            self.store.write(run_id, data, self._order_file(order_id))
            meta["rework_count"] += 1
            meta["status"] = "dispatched"
            st.pop("integration", None)
            self._save(st)
        return self._status(st, spawn=[{"order_id": order_id, "label": f"Rework {order_id}"[:80],
                                        "task": planner.spawn_task(order)}],
                            hint="sessions_spawn this task with agentId='engineer-worker', then sessions_yield.")

    def verify_variables(self, run_id: str, part_studio_element_id: str, onshape_variables: Any) -> dict[str, Any]:
        parsed = parse_variables(onshape_variables)
        if not parsed:
            raise EngineError("Could not parse any variables. Pass the raw text output of get_variables for "
                              "that Part Studio.")
        with self.store.lock(run_id):
            st = self._load(run_id)
            plan = self._plan(st)
            mismatches, missing, unparsed = [], [], []
            for p in plan.parameters:
                if p.name not in parsed:
                    missing.append(p.name)
                    continue
                val, unit = parse_onshape_expression(parsed[p.name])
                if val is None:
                    unparsed.append(f"{p.name} = {parsed[p.name]}")
                    continue
                want_unit = {"mm": "mm", "deg": "deg", "count": None}[p.unit]
                if unit != want_unit or abs(val - p.value) > max(1e-6, 1e-6 * abs(p.value)):
                    mismatches.append(f"{p.name}: Onshape has '{parsed[p.name]}', plan says {p.value} {p.unit}")
            extra = sorted(set(parsed) - {p.name for p in plan.parameters})
            ok = not (mismatches or missing)
            rec = {"ok": ok, "iteration": st["iteration"], "mismatches": mismatches, "missing": missing,
                   "unparsed": unparsed, "extra": extra, "t": time.time()}
            st.setdefault("variable_checks", {})[part_studio_element_id] = rec
            self._save(st)
        return {"part_studio_element_id": part_studio_element_id, **rec,
                "hint": "A worker changed or skipped plan variables: issue_rework that order (contracts are "
                        "frozen), or set_variable back to the plan value and re-check." if not ok
                else "This Part Studio matches the plan."}

    # ------------------------------------------------------------------ #
    # Phase 3: integration + verification
    # ------------------------------------------------------------------ #
    def _stress(self, st: dict[str, Any], calc_ids: list[str]) -> list[StressCheck]:
        out = []
        for cid in dict.fromkeys(calc_ids):
            c = st["calcs"].get(cid)
            if c is None:
                raise EngineError(f"Unknown calc id {cid}")
            r = c["result"]
            out.append(StressCheck(calc_id=cid, part=c["part"], load_case=c["load_case"], method=r["method"],
                                   max_stress_mpa=r["max_stress_mpa"], allowable_mpa=r["allowable_mpa"],
                                   safety_factor=r["safety_factor"], max_deflection_mm=r["max_deflection_mm"],
                                   passed=r["passed"], reference_ids=r["reference_ids"]))
        return out

    def submit_integration(self, run_id: str, report: Any) -> dict[str, Any]:
        rep = _parse(IntegrationReport, report, "report")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, BUILD)
            not_done = [k for k, v in st["orders"].items() if v["status"] != "done"]
            if not_done:
                raise EngineError(f"Orders not finished: {not_done}")
            plan = self._plan(st)
            brief = DesignBrief.model_validate(st["brief"])
            built = self._built_parts(st)
            subs = [self.store.read(run_id, self._order_file(o)).get("submission") or {} for o in st["orders"]]
            waivers = {g["check_name"]: g["user_reply"] for g in st["gates"]
                       if g["gate"] == "waiver" and g["approved"] and g["iteration"] == st["iteration"]}

            checks: list[CheckResult] = []

            def add(name: str, passed: bool, detail: str) -> None:
                if not passed and name in waivers:
                    checks.append(CheckResult(name=name, passed=True, detail=f"WAIVED by user ('{waivers[name]}'): {detail}"))
                else:
                    checks.append(CheckResult(name=name, passed=passed, detail=detail))

            missing = [p.name for p in plan.all_parts() if p.name not in built]
            failed = sorted({f for s in subs for f in s.get("failed_parts", [])})
            add("all_parts_built", not missing and not failed,
                "all parts built" if not (missing or failed) else f"missing {missing}, failed {failed}")
            conflicts = [c for s in subs for c in s.get("conflicts", [])]
            add("interface_contracts", not conflicts,
                "no open conflicts" if not conflicts else "; ".join(f"{c['interface_id']}: {c['description']}" for c in conflicts))
            vcs = st.get("variable_checks", {})
            studios = sorted({r.part_studio_element_id for r in built.values()})
            unchecked = [e for e in studios if e not in vcs]
            bad = {e: vcs[e]["mismatches"] + [f"missing {m}" for m in vcs[e]["missing"]]
                   for e in studios if e in vcs and not vcs[e]["ok"]}
            add("parameters_match_plan", not unchecked and not bad,
                f"{len(studios)} Part Studio(s) match the plan" if not unchecked and not bad else
                f"run verify_variables for {unchecked}" if unchecked else f"mismatches {bad}")
            add("interference", rep.interference_resolved,
                "no interference (note: the Onshape MCP check is bounding-box based)" if rep.interference_resolved
                else "interference remains")
            walls = [w for w in [rep.min_wall_thickness_mm] + [s.get("min_wall_thickness_mm") for s in subs] if w is not None]
            add("min_wall_thickness", bool(walls) and min(walls) >= self.cfg.min_wall_thickness_mm,
                f"min {min(walls)} mm (limit {self.cfg.min_wall_thickness_mm} mm)" if walls else "not reported")
            calc_ids = rep.calc_ids + [c for s in subs for c in s.get("calc_ids", [])]
            stress = self._stress(st, calc_ids)
            add("stress_coverage", bool(stress) or not brief.loads,
                f"{len(stress)} hand calcs for {len(brief.loads)} load(s)" if stress or not brief.loads
                else "brief has loads but no hand_calc was cited")
            total = sum((r.mass_g or 0) * next((p.quantity for p in plan.all_parts() if p.name == r.part), 1)
                        for r in built.values())
            v = VerificationReport(checks=checks, stress_checks=stress, total_mass_g=round(total, 2))
            st["verification"] = v.model_dump()
            if not v.all_passed:
                self._save(st)
                fails = [c.name for c in v.checks if not c.passed] + [s.calc_id for s in v.stress_checks if not s.passed]
                raise EngineError("Verification failed: " + ", ".join(fails) + ".\n\n" + render.verification_md(v) +
                                  "\n\nFix with issue_rework / set_variable / new hand_calc. If a fix would change "
                                  "design intent, ask_user, then record_gate(gate='design_change'). Only the user can "
                                  "waive a check: record_gate(gate='waiver', check_name=...).")
            st["integration"] = rep.model_dump()
            if rep.assembly_element_id:
                st.setdefault("onshape", {})["assembly_element_id"] = rep.assembly_element_id
            st["phase"] = MEMO
            self._save(st)
        return self._status(st, verification_markdown=render.verification_md(v))

    # ------------------------------------------------------------------ #
    # Phase 4-5: memo, gate, handoff
    # ------------------------------------------------------------------ #
    def submit_memo(self, run_id: str, memo: Any) -> dict[str, Any]:
        m = _parse(TechMemo, memo, "memo")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, MEMO)
            brief = DesignBrief.model_validate(st["brief"])
            listed = " ".join(r.requirement.lower() for r in m.traceability)
            missing = [r for r in brief.requirements if r.lower()[:25] not in listed]
            if len(m.traceability) < len(brief.requirements):
                raise EngineError(f"Traceability needs a row for every brief requirement ({len(brief.requirements)}); "
                                  f"got {len(m.traceability)}. Possibly missing: {missing}")
            v = VerificationReport.model_validate(st["verification"])
            md = render.memo_md(m, st["title"], st["iteration"], v)
            path = self.store.write_text(run_id, f"memo_i{st['iteration']}.md", md)
            st["memo"] = m.model_dump()
            st["memo_path"] = str(path)
            st["phase"] = GATE_HANDOFF
            self._save(st)
        return self._status(st, memo_path=str(path), markdown_for_user=md)

    def record_gate(self, run_id: str, gate: str, approved: bool, user_reply: str,
                    feedback: Optional[str] = None, check_name: Optional[str] = None) -> dict[str, Any]:
        with self.store.lock(run_id):
            st = self._load(run_id)
            if gate == "handoff":
                self._require(st, GATE_HANDOFF)
                self._gate(st, gate, approved, user_reply, feedback=feedback)
                st["phase"] = HANDOFF if approved else GATE_HANDOFF
                note = None if approved else ("Call submit_feedback(source='user') with the user's requested "
                                              "changes. That starts the next iteration.")
            elif gate == "feedback":
                self._require(st, GATE_FEEDBACK)
                self._gate(st, gate, approved, user_reply, feedback=feedback)
                if approved:
                    st["phase"] = PLAN
                    note = "Revise the plan with the pending changes and call submit_plan."
                else:
                    st["pending_changes"] = []
                    st["iteration"] -= 1  # nothing will be rebuilt; give the iteration back
                    st["phase"] = st.get("phase_before_feedback", DONE)
                    note = "Changes declined. Run returns to its previous state."
            elif gate == "tradeoff":
                self._require(st, GATE_TRADEOFF)
                self._gate(st, gate, approved, user_reply, feedback=feedback)
                if approved:
                    st["extra_iterations"] = st.get("extra_iterations", 0) + 1
                    st["iteration"] += 1
                    st["pending_changes"] = [f"User's trade-off decision: {user_reply}"]
                    st["phase"] = PLAN
                    note = "User authorized one more iteration. Re-plan with their chosen option."
                else:
                    st["phase"] = STOPPED
                    note = "User chose to stop."
            elif gate == "design_change":
                self._require(st, PLAN, BUILD)
                self._gate(st, gate, approved, user_reply, feedback=feedback)
                if approved:
                    st.setdefault("design_changes", []).append(feedback or user_reply)
                note = ("Approved: apply it (update the plan only via a new iteration if parameters change; "
                        "otherwise issue_rework)." if approved else "Declined: keep the current design intent.")
            elif gate == "waiver":
                self._require(st, BUILD)
                if not check_name:
                    raise EngineError("waiver needs check_name")
                self._gate(st, gate, approved, user_reply, check_name=check_name, feedback=feedback)
                note = f"Check '{check_name}' {'waived' if approved else 'NOT waived'} for this iteration."
            else:
                raise EngineError("gate must be one of: handoff, feedback, tradeoff, design_change, waiver "
                                  "(the review gate uses record_review_decision)")
            self._save(st)
        return self._status(st, note=note)

    def finalize_handoff(self, run_id: str, extras: Any) -> dict[str, Any]:
        x = _parse(HandoffExtras, extras, "extras")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, HANDOFF)
            plan = self._plan(st)
            built = self._built_parts(st)
            names = {p.name for p in plan.all_parts()}
            for t in x.critical_tolerances:
                if t.part not in names:
                    raise EngineError(f"Critical tolerance cites unknown part '{t.part}'")
            for e in x.exports:
                if e.part not in names:
                    raise EngineError(f"Export cites unknown part '{e.part}'")
            open_items = list(x.open_items)
            parts = []
            for p in plan.all_parts():
                r = built[p.name]
                ex = [e for e in x.exports if e.part == p.name]
                if not ex:
                    open_items.append(f"No STEP export recorded for {p.name}")
                parts.append(HandoffPart(name=p.name, material=p.material, quantity=p.quantity,
                                         part_studio_element_id=r.part_studio_element_id, part_id=r.part_id,
                                         mass_g=r.mass_g, bbox_mm=r.bbox_mm, exports=ex))
            on = st.get("onshape") or {}
            plan_path = self.store.write(run_id, st["plan"], f"plan_i{st['iteration']}.json")
            pkg = HandoffPackage(
                run_id=run_id, title=st["title"], iteration=st["iteration"],
                onshape=OnshapeTarget(document_id=on["document_id"], workspace_id=on["workspace_id"]) if on else None,
                assembly_element_id=on.get("assembly_element_id"), parts=parts,
                materials=sorted({p.material for p in plan.all_parts()}),
                critical_tolerances=x.critical_tolerances,
                quantities={p.name: p.quantity * DesignBrief.model_validate(st["brief"]).quantity
                            for p in plan.all_parts()},
                open_items=open_items, memo_path=st["memo_path"], plan_path=str(plan_path),
                verification=VerificationReport.model_validate(st["verification"]))
            path = self.store.write(run_id, pkg.model_dump(mode="json"), f"handoff_i{st['iteration']}.json")
            st["handoff_path"] = str(path)
            st["phase"] = DONE
            self._save(st)
        return self._status(st, handoff_path=str(path), handoff=pkg.model_dump(mode="json"))

    # ------------------------------------------------------------------ #
    # Iterations
    # ------------------------------------------------------------------ #
    def submit_feedback(self, run_id: str, feedback: Any, classification: Any) -> dict[str, Any]:
        fb = _parse(MachinistFeedback, feedback, "feedback")
        cl = _parse(ClassifiedFeedback, classification, "classification")
        if len(cl.items) < len(fb.requests):
            raise EngineError("Classify every request (one item per request).")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, DONE, GATE_HANDOFF)
            st.setdefault("feedback_history", []).append({"iteration": st["iteration"], "feedback": fb.model_dump(),
                                                          "classification": cl.model_dump()})
            if st["iteration"] >= self._max_iter(st):
                st["phase"] = TRADEOFF
                st["outstanding_feedback"] = cl.model_dump()
                self._save(st)
                return self._status(st, note="Iteration limit reached. Do not make changes. Present trade-offs.")
            st["phase_before_feedback"] = st["phase"]
            st["iteration"] += 1
            intent = [i for i in cl.items if i.classification == "design_intent"]
            eng = [i for i in cl.items if i.classification == "engineering_only"]
            if intent:
                st["phase"] = ROUTED
                st["designer_requests"] = [i.model_dump() for i in cl.items]
                self._save(st)
                return self._status(st, route="designer", designer_requests=[i.model_dump() for i in cl.items],
                                    note="At least one request changes design intent, so the whole set goes back to "
                                         "the Designer (engineering-only items included as notes).")
            st["pending_changes"] = [f"{i.restated} -> {i.proposed_change}" for i in eng]
            st["phase"] = GATE_FEEDBACK
            self._save(st)
        return self._status(st, route="engineer", changes_for_approval=st["pending_changes"])

    def submit_tradeoffs(self, run_id: str, report: Any) -> dict[str, Any]:
        t = _parse(TradeoffReport, report, "report")
        if len(t.options) < 2:
            raise EngineError("Give at least 2 options.")
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, TRADEOFF)
            st["tradeoffs"] = t.model_dump()
            st["phase"] = GATE_TRADEOFF
            self._save(st)
        return self._status(st, markdown_for_user=render.tradeoffs_md(t))

    def restart_from_designer(self, run_id: str, brief: Any = None, sketch: Any = None) -> dict[str, Any]:
        with self.store.lock(run_id):
            st = self._load(run_id)
            self._require(st, ROUTED)
            if brief is not None:
                st["brief"] = _parse(DesignBrief, brief, "brief").model_dump()
            if sketch is not None:
                st["sketch"] = _parse(SketchInput, sketch, "sketch").model_dump()
            st["history"].append({"iteration": st["iteration"] - 1, "review": st.get("review"),
                                  "review_decision": st.get("review_decision")})
            for k in ("review", "review_decision", "pending_changes"):
                st.pop(k, None)
            st["phase"] = REVIEW
            self._save(st)
        return self._status(st, note="Same run, same iteration counter. Start a fresh design review.")
