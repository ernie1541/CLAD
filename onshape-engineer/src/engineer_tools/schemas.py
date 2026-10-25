"""Typed contracts for the Engineer stage (Designer -> Engineer -> Machinist).

Every structure an agent submits is validated against one of these models before the
run is allowed to move to the next phase. Units are millimetres unless a field says otherwise.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# =========================================================================== #
# Inputs from the Designer
# =========================================================================== #
class Load(BaseModel):
    description: str = Field(..., description="e.g. '200 N downward at the free end'")
    magnitude: float
    unit: str = Field(..., description="N, N*m, MPa, ...")
    location: Optional[str] = None
    kind: Literal["static", "cyclic", "impact", "thermal", "pressure"] = "static"


class DesignBrief(BaseModel):
    title: str
    function: str = Field(..., description="What the part/assembly must do")
    loads: list[Load] = Field(default_factory=list)
    environment: str = "indoor, room temperature"
    size_limits: Optional[str] = Field(None, description="Envelope, e.g. 'fits in 100x50x30 mm'")
    quantity: int = 1
    budget: Optional[str] = None
    preferred_materials: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list, description="Explicit user requirements")
    notes: Optional[str] = None


class SketchInput(BaseModel):
    description: str = Field(..., description="Text description of the approved sketch with dimensions")
    image_paths: list[str] = Field(default_factory=list)
    onshape_url: Optional[str] = Field(None, description="Designer's Onshape sketch/document, if any")


# =========================================================================== #
# Phase 1 - Design review
# =========================================================================== #
class Severity(str, Enum):
    critical = "Critical"
    major = "Major"
    minor = "Minor"


class IssueCategory(str, Enum):
    structural = "structural"
    fit_assembly = "fit_assembly"
    manufacturability = "manufacturability"
    material = "material"
    missing_info = "missing_info"


class Issue(BaseModel):
    id: int
    category: IssueCategory
    severity: Severity
    issue: str
    why_it_matters: str
    proposed_fix: str
    changes_design_intent: bool
    effect_on_design_intent: str


class ClarifyingQuestion(BaseModel):
    id: str = Field(..., description="snake_case")
    question: str
    why_needed: str
    default_if_unanswered: Optional[str] = None


class DesignReview(BaseModel):
    issues: list[Issue] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    checked_summary: str = Field(..., description="What you checked. Required even when there are no issues.")


class IssueDecision(BaseModel):
    issue_id: int
    decision: Literal["approve", "reject", "modify"]
    note: Optional[str] = Field(None, description="Required when decision is 'modify'")


class ReviewDecision(BaseModel):
    decisions: list[IssueDecision] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict, description="question id -> user's answer")
    extra_instructions: Optional[str] = None


# =========================================================================== #
# Phase 2 - Modeling plan
# =========================================================================== #
class Parameter(BaseModel):
    name: str = Field(..., description="snake_case; becomes the Onshape variable #name")
    value: float
    unit: Literal["mm", "deg", "count"] = "mm"
    description: str = ""


class PartSpec(BaseModel):
    name: str = Field(..., description="Unique snake_case part id")
    description: str
    material: str = Field(..., description="Material id from the reference database")
    quantity: int = 1
    key_features: list[str] = Field(default_factory=list)


class Subassembly(BaseModel):
    name: str
    description: str
    parts: list[PartSpec]
    depends_on: list[str] = Field(default_factory=list, description="Names of subassemblies that must be built first")


class InterfaceContract(BaseModel):
    """Frozen before dispatch. Workers may not change any of this."""
    id: str
    part_a: str
    part_b: str
    mating_features: str
    parameters: list[str] = Field(default_factory=list, description="Plan parameter names this interface fixes")
    fit: Optional[str] = Field(None, description="ISO fit, e.g. 'H7/g6'")
    fasteners: Optional[str] = Field(None, description="e.g. '4x M5x0.8 SHCS'")


class Datum(BaseModel):
    name: str
    definition: str = Field(..., description="e.g. 'Origin at bottom-left-rear corner of base_plate; +Z up'")


class ModelingPlan(BaseModel):
    default_tolerance: str = "±0.1 mm"
    parameters: list[Parameter]
    datums: list[Datum] = Field(default_factory=list)
    subassemblies: list[Subassembly]
    interfaces: list[InterfaceContract] = Field(default_factory=list)
    modeling_order: list[str] = Field(default_factory=list, description="Part names in build order")

    def all_parts(self) -> list[PartSpec]:
        return [p for s in self.subassemblies for p in s.parts]

    def params(self) -> dict[str, Parameter]:
        return {p.name: p for p in self.parameters}


# =========================================================================== #
# Lead <-> Worker contract
# =========================================================================== #
class OnshapeTarget(BaseModel):
    document_id: str
    workspace_id: str


class OnshapeParameter(BaseModel):
    """A plan parameter pre-converted for the Onshape MCP (which takes inches)."""
    name: str
    value: float
    unit: str
    value_in: Optional[float] = Field(None, description="Inches, for numeric MCP arguments (mm params only)")
    onshape_expression: str = Field(..., description="Pass this to set_variable, e.g. '6 mm'")
    frozen: bool = Field(False, description="Fixed by an interface contract")


class UpstreamPart(BaseModel):
    part: str
    part_studio_element_id: str
    part_id: Optional[str] = None


class WorkOrder(BaseModel):
    order_id: str
    run_id: str
    iteration: int
    wave: int
    subassemblies: list[Subassembly]
    parameters: list[OnshapeParameter]
    datums: list[Datum]
    interfaces: list[InterfaceContract]
    loads: list[Load]
    acceptance_criteria: list[str]
    context: str
    onshape: Optional[OnshapeTarget] = None
    upstream_parts: list[UpstreamPart] = Field(default_factory=list)
    rework: list[str] = Field(default_factory=list, description="Rework instructions from the lead, newest last")

    def part_names(self) -> list[str]:
        return [p.name for s in self.subassemblies for p in s.parts]


class PartReport(BaseModel):
    """What a worker reports after building one part in Onshape."""
    part: str
    part_studio_element_id: str
    part_id: Optional[str] = None
    volume_m3: float = Field(..., description="From eval_featurescript evVolume (SI, cubic metres)")
    bbox_low_m: Optional[list[float]] = Field(None, description="[x,y,z] metres, from get_bounding_box")
    bbox_high_m: Optional[list[float]] = None
    notes: str = ""


class PartRecord(BaseModel):
    part: str
    order_id: str
    material: str
    part_studio_element_id: str
    part_id: Optional[str] = None
    volume_mm3: float
    mass_g: Optional[float] = None
    bbox_mm: Optional[list[float]] = None
    bbox_low_mm: Optional[list[float]] = None
    parameter_check: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class InterfaceConflict(BaseModel):
    interface_id: str
    description: str
    proposed_resolution: Optional[str] = None


class WorkerSubmission(BaseModel):
    built_parts: list[str]
    failed_parts: list[str] = Field(default_factory=list)
    conflicts: list[InterfaceConflict] = Field(default_factory=list)
    calc_ids: list[str] = Field(default_factory=list, description="hand_calc ids for stress checks you ran")
    min_wall_thickness_mm: Optional[float] = None
    notes: str = ""


# =========================================================================== #
# Phase 3 - Integration / verification
# =========================================================================== #
class StressCheck(BaseModel):
    calc_id: str
    part: str
    load_case: str
    method: str
    max_stress_mpa: float
    allowable_mpa: float
    safety_factor: float
    max_deflection_mm: Optional[float] = None
    passed: bool
    reference_ids: list[str]


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str


class IntegrationReport(BaseModel):
    """What the lead submits after assembling and checking everything."""
    assembly_element_id: Optional[str] = None
    interference_raw: Optional[str] = Field(None, description="Raw text output of check_assembly_interference")
    interference_resolved: bool = Field(..., description="True if no real interference remains")
    calc_ids: list[str] = Field(default_factory=list, description="hand_calc ids for assembly-level checks")
    min_wall_thickness_mm: Optional[float] = None
    min_feature_size_mm: Optional[float] = None
    notes: str = ""


class VerificationReport(BaseModel):
    checks: list[CheckResult]
    stress_checks: list[StressCheck]
    total_mass_g: Optional[float] = None

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks) and all(s.passed for s in self.stress_checks)


# =========================================================================== #
# Phase 4 - Technical memo
# =========================================================================== #
class TraceRow(BaseModel):
    requirement: str
    how_met: str
    verified: bool


class DesignChange(BaseModel):
    description: str
    reason: str
    issue_ids: list[int] = Field(default_factory=list)


class MachinistNote(BaseModel):
    part: str
    note: str
    critical: bool = False


class TechMemo(BaseModel):
    summary: str = Field(..., description="3-5 sentences")
    traceability: list[TraceRow]
    design_changes: list[DesignChange] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    materials_and_dims: str
    verification_summary: str
    risks_open_items: list[str] = Field(default_factory=list)
    machinist_notes: list[MachinistNote] = Field(default_factory=list)


# =========================================================================== #
# Phase 5 - Handoff (the Machinist's input contract)
# =========================================================================== #
class CriticalTolerance(BaseModel):
    part: str
    feature: str
    nominal_mm: float
    tolerance: str = Field(..., description="e.g. '+0.018/-0.000' or 'H7'")
    gdt: Optional[str] = Field(None, description="e.g. 'position dia 0.05 |A|B|'")


class ExportRef(BaseModel):
    part: str
    format: str = "STEP"
    path_or_url: str = Field(..., description="File path or Onshape translation URL from export_part_studio")


class HandoffExtras(BaseModel):
    critical_tolerances: list[CriticalTolerance] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    exports: list[ExportRef] = Field(default_factory=list)


class HandoffPart(BaseModel):
    name: str
    material: str
    quantity: int
    part_studio_element_id: str
    part_id: Optional[str] = None
    mass_g: Optional[float] = None
    bbox_mm: Optional[list[float]] = None
    exports: list[ExportRef] = Field(default_factory=list)


class HandoffPackage(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    title: str
    iteration: int
    units: str = "mm"
    onshape: Optional[OnshapeTarget] = None
    assembly_element_id: Optional[str] = None
    parts: list[HandoffPart]
    materials: list[str]
    critical_tolerances: list[CriticalTolerance]
    quantities: dict[str, int]
    open_items: list[str]
    memo_path: str
    plan_path: str
    verification: VerificationReport


# =========================================================================== #
# Iterations
# =========================================================================== #
class MachinistFeedback(BaseModel):
    source: Literal["machinist", "user"] = "machinist"
    requests: list[str]
    cost_estimate: Optional[str] = None
    notes: Optional[str] = None


class ClassifiedRequest(BaseModel):
    request: str
    restated: str
    classification: Literal["engineering_only", "design_intent"]
    rationale: str
    proposed_change: str


class ClassifiedFeedback(BaseModel):
    items: list[ClassifiedRequest]


class TradeoffOption(BaseModel):
    label: str
    description: str
    pros: list[str]
    cons: list[str]


class TradeoffReport(BaseModel):
    conflict: str
    options: list[TradeoffOption]
    recommendation: Optional[str] = None
