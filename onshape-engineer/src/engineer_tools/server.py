"""MCP server exposing the Engineer engine.

Run:  engineer-tools [--runs-dir DIR] [--max-iterations 3] [--spawn-parts-over 6] ...
In OpenClaw the tools appear as engineer__<name>.
"""
from __future__ import annotations

import argparse
import copy
import functools
from pathlib import Path
from typing import Any, Literal, Optional

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

from .config import EngineerConfig
from .engine import Engine, EngineError
from .schemas import (ClassifiedFeedback, DesignBrief, DesignReview, HandoffExtras, IntegrationReport,
                      MachinistFeedback, ModelingPlan, PartReport, ReviewDecision, SketchInput, TechMemo,
                      TradeoffReport, WorkerSubmission)

LEAD_TOOLS = ["start_run", "run_status", "get_run_context", "list_runs", "submit_review", "record_review_decision",
              "submit_plan", "set_onshape_target", "next_orders", "build_status", "issue_rework",
              "verify_variables", "submit_integration", "submit_memo", "record_gate", "finalize_handoff",
              "submit_feedback", "submit_tradeoffs", "restart_from_designer"]
WORKER_TOOLS = ["get_work_order", "report_part", "submit_work_result"]
SHARED_TOOLS = ["lookup_material", "hand_calc", "convert"]


def _surface_errors(fn):
    """Turn EngineError into an MCP tool error whose text the model sees."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except EngineError as e:
            raise ToolError(str(e)) from e
    return wrapper


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $defs and drop titles: small local models handle flat schemas better."""
    defs = schema.get("$defs", {})

    def res(n: Any) -> Any:
        if isinstance(n, dict):
            if "$ref" in n:
                tgt = copy.deepcopy(defs[n["$ref"].split("/")[-1]])
                return res({**tgt, **{k: v for k, v in n.items() if k != "$ref"}})
            return {k: res(v) for k, v in n.items() if k not in ("title", "$defs")}
        if isinstance(n, list):
            return [res(x) for x in n]
        return n

    return res(schema)


def build_server(cfg: Optional[EngineerConfig] = None) -> Any:
    eng = Engine(cfg)
    mcp = _Server("engineer", instructions=(
        "Engineer-stage tools for an Onshape CAD pipeline (Designer -> Engineer -> Machinist). "
        "Every response includes `phase` and `next_step`. When unsure, call run_status."))

    # ---------------- shared ---------------- #
    @mcp.tool()
    @_surface_errors
    def lookup_material(query: str = "") -> dict:
        """Search the reference material database (density, E, yield, UTS, service temp, source).
        Empty query lists all ids. Never use material properties from anywhere else."""
        return eng.lookup_material(query)

    @mcp.tool()
    @_surface_errors
    def hand_calc(run_id: str, part: str, load_case: str,
                  case: Literal["axial", "cantilever_end_load", "simply_supported_center_load", "bolt_shear"],
                  material: str, force_n: float, length_mm: Optional[float] = None,
                  section: Optional[dict[str, float]] = None, n_bolts: Optional[int] = None,
                  bolt_minor_dia_mm: Optional[float] = None, kt: float = 1.0,
                  by: str = "lead") -> dict:
        """Stress/deflection hand calc, logged with a calc_id you must cite. Units N, mm, MPa.
        section: {"kind":"rect","b":..,"h":..} (h in bending direction) | {"kind":"circle","d":..} |
        {"kind":"tube","do":..,"di":..}. kt = stress concentration factor. by = 'lead' or your order_id."""
        return eng.hand_calc(run_id, part, load_case, case, material, force_n, length_mm, section, n_bolts,
                             bolt_minor_dia_mm, kt, by)

    @mcp.tool()
    @_surface_errors
    def convert(run_id: str, expressions: list[str]) -> dict:
        """Evaluate arithmetic over plan parameter names and return mm AND inches.
        e.g. ["plate_length/2", "hole_spacing - hole_dia", "plate_width/2 + 5"]. Trig takes degrees.
        Use the 'in' value for numeric Onshape MCP arguments (that server takes inches)."""
        return eng.convert(run_id, expressions)

    # ---------------- lead ---------------- #
    @mcp.tool()
    @_surface_errors
    def start_run(brief: DesignBrief, sketch: SketchInput) -> dict:
        """Start an Engineer run from the Designer's approved brief and sketch. Returns run_id."""
        return eng.start_run(brief, sketch)

    @mcp.tool()
    @_surface_errors
    def run_status(run_id: str) -> dict:
        """Current phase, iteration (n/max), order statuses, gates, and the exact next step."""
        return eng.run_status(run_id)

    @mcp.tool()
    @_surface_errors
    def get_run_context(run_id: str) -> dict:
        """Brief, sketch, review, decisions, plan and Onshape ids. Use after a long session to re-orient."""
        return eng.get_run_context(run_id)

    @mcp.tool()
    @_surface_errors
    def list_runs() -> list:
        """List engineer runs, newest first."""
        return eng.list_runs()

    @mcp.tool()
    @_surface_errors
    def submit_review(run_id: str, review: DesignReview) -> dict:
        """PHASE 1 output. Returns markdown_for_user to show at GATE 1."""
        return eng.submit_review(run_id, review)

    @mcp.tool()
    @_surface_errors
    def record_review_decision(run_id: str, decision: ReviewDecision, user_reply: str,
                               proceed: bool = True) -> dict:
        """GATE 1. Record the user's per-issue decisions and answers. user_reply = their verbatim answer
        from ask_user. proceed=false sends you back to revise the review (or aborts if they said abort)."""
        return eng.record_review_decision(run_id, decision, user_reply, proceed)

    @mcp.tool()
    @_surface_errors
    def submit_plan(run_id: str, plan: ModelingPlan) -> dict:
        """PHASE 2 output. Validates the plan, decides parallelization, creates work orders.
        Returns the Onshape variables to set (name + expression)."""
        return eng.submit_plan(run_id, plan)

    @mcp.tool()
    @_surface_errors
    def set_onshape_target(run_id: str, document_id: str, workspace_id: str,
                           variable_studio_id: Optional[str] = None,
                           assembly_element_id: Optional[str] = None) -> dict:
        """Record the Onshape document/workspace (and optionally the Assembly element id) for this run."""
        return eng.set_onshape_target(run_id, document_id, workspace_id, variable_studio_id, assembly_element_id)

    @mcp.tool()
    @_surface_errors
    def next_orders(run_id: str) -> dict:
        """Get the work orders that are ready now (dependencies met, within the worker cap), each with the
        exact task text for sessions_spawn. Marks them dispatched."""
        return eng.next_orders(run_id)

    @mcp.tool()
    @_surface_errors
    def build_status(run_id: str) -> dict:
        """Status of every work order: parts reported, failures, interface conflicts, failed stress checks."""
        return eng.build_status(run_id)

    @mcp.tool()
    @_surface_errors
    def issue_rework(run_id: str, order_id: str, instructions: str) -> dict:
        """Send a finished order back to a worker with specific instructions. Returns the spawn task."""
        return eng.issue_rework(run_id, order_id, instructions)

    @mcp.tool()
    @_surface_errors
    def verify_variables(run_id: str, part_studio_element_id: str, onshape_variables: str) -> dict:
        """Compare one Part Studio's variable table (raw get_variables text) to the plan.
        Required for every worker Part Studio before integration."""
        return eng.verify_variables(run_id, part_studio_element_id, onshape_variables)

    @mcp.tool()
    @_surface_errors
    def submit_integration(run_id: str, report: IntegrationReport) -> dict:
        """PHASE 3 output after the assembly is built and checked. Runs all verification checks.
        Fails with a table if anything doesn't pass."""
        return eng.submit_integration(run_id, report)

    @mcp.tool()
    @_surface_errors
    def submit_memo(run_id: str, memo: TechMemo) -> dict:
        """PHASE 4 output. Writes memo markdown; returns markdown_for_user for GATE 2."""
        return eng.submit_memo(run_id, memo)

    @mcp.tool()
    @_surface_errors
    def record_gate(run_id: str, gate: Literal["handoff", "feedback", "tradeoff", "design_change", "waiver"],
                    approved: bool, user_reply: str, feedback: Optional[str] = None,
                    check_name: Optional[str] = None) -> dict:
        """Record a user decision. user_reply MUST be the user's verbatim ask_user answer.
        handoff = GATE 2; feedback = approve engineering-only revisions; tradeoff = choice at the iteration
        limit; design_change = mid-build change to design intent (put the change in feedback);
        waiver = user accepts a failed check (check_name)."""
        return eng.record_gate(run_id, gate, approved, user_reply, feedback, check_name)

    @mcp.tool()
    @_surface_errors
    def finalize_handoff(run_id: str, extras: HandoffExtras) -> dict:
        """PHASE 5. Writes the Machinist handoff JSON (critical tolerances, open items, STEP export refs)."""
        return eng.finalize_handoff(run_id, extras)

    @mcp.tool()
    @_surface_errors
    def submit_feedback(run_id: str, feedback: MachinistFeedback, classification: ClassifiedFeedback) -> dict:
        """Machinist or user revision requests, each classified engineering_only or design_intent.
        Advances the iteration counter and routes: Engineer (needs user approval) or Designer.
        At the iteration limit it routes to trade-offs instead."""
        return eng.submit_feedback(run_id, feedback, classification)

    @mcp.tool()
    @_surface_errors
    def submit_tradeoffs(run_id: str, report: TradeoffReport) -> dict:
        """At the iteration limit: record the trade-off options. Returns markdown_for_user."""
        return eng.submit_tradeoffs(run_id, report)

    @mcp.tool()
    @_surface_errors
    def restart_from_designer(run_id: str, brief: Optional[DesignBrief] = None,
                              sketch: Optional[SketchInput] = None) -> dict:
        """After the Designer revises the design: same run and iteration counter, back to design review."""
        return eng.restart_from_designer(run_id, brief, sketch)

    # ---------------- worker ---------------- #
    @mcp.tool()
    @_surface_errors
    def get_work_order(run_id: str, order_id: str) -> dict:
        """WORKER: fetch your full work order (parts, parameters with inch values, datums, frozen interfaces,
        loads, acceptance criteria, Onshape ids, rework instructions)."""
        return eng.get_work_order(run_id, order_id)

    @mcp.tool()
    @_surface_errors
    def report_part(run_id: str, order_id: str, report: PartReport) -> dict:
        """WORKER: record one built part: Part Studio element id, part id, volume (m^3, from evVolume)
        and bounding box (m, from get_bounding_box). Computes mass from the material DB."""
        return eng.report_part(run_id, order_id, report)

    @mcp.tool()
    @_surface_errors
    def submit_work_result(run_id: str, order_id: str, submission: WorkerSubmission) -> dict:
        """WORKER: finish the order. List built/failed parts, interface conflicts, calc_ids, min wall."""
        return eng.submit_work_result(run_id, order_id, submission)

    _flatten_schemas(mcp)
    return mcp


def _flatten_schemas(mcp: Any) -> None:
    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", {}) if tm else {}
    for t in tools.values():
        if isinstance(getattr(t, "parameters", None), dict):
            t.parameters = _inline(t.parameters)


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="engineer-tools", description=__doc__)
    ap.add_argument("--runs-dir", type=Path)
    ap.add_argument("--materials-db", type=Path)
    ap.add_argument("--max-iterations", type=int)
    ap.add_argument("--spawn-parts-over", type=int, help="N")
    ap.add_argument("--spawn-subassemblies-over", type=int, help="M")
    ap.add_argument("--max-workers", type=int, help="K")
    ap.add_argument("--min-safety-factor", type=float)
    ap.add_argument("--min-wall-mm", type=float)
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    a = ap.parse_args(argv)
    kw = {k: v for k, v in {
        "runs_dir": a.runs_dir, "materials_db": a.materials_db, "max_iterations": a.max_iterations,
        "spawn_if_parts_over": a.spawn_parts_over, "spawn_if_subassemblies_over": a.spawn_subassemblies_over,
        "max_workers": a.max_workers, "min_safety_factor": a.min_safety_factor,
        "min_wall_thickness_mm": a.min_wall_mm}.items() if v is not None}
    build_server(EngineerConfig(**kw)).run(transport=a.transport)


if __name__ == "__main__":
    main()
