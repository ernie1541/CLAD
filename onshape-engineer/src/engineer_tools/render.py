"""Markdown renderers for what the lead shows the user at each gate."""
from __future__ import annotations

from .schemas import DesignReview, TechMemo, TradeoffReport, VerificationReport

_SEV = {"Critical": 0, "Major": 1, "Minor": 2}


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def review_md(r: DesignReview, iteration: int, max_iter: int) -> str:
    L = [f"### Design review (iteration {iteration}/{max_iter})", ""]
    if r.issues:
        L += ["| # | Issue | Severity | Why it matters | Proposed fix | Changes intent? |",
              "|---|---|---|---|---|---|"]
        for i in sorted(r.issues, key=lambda x: (_SEV[x.severity.value], x.id)):
            L.append(f"| {i.id} | {_cell(i.issue)} | {i.severity.value} | {_cell(i.why_it_matters)} | "
                     f"{_cell(i.proposed_fix)} | {'Yes: ' + _cell(i.effect_on_design_intent) if i.changes_design_intent else 'No'} |")
    else:
        L.append("No issues found.")
    L += ["", f"**Checked:** {r.checked_summary}"]
    if r.assumptions:
        L += ["", "**Assumptions**"] + [f"- {a}" for a in r.assumptions]
    if r.questions:
        L += ["", "**Questions**"]
        L += [f"- `{q.id}`: {q.question} _(why: {q.why_needed}"
              + (f"; default if unanswered: {q.default_if_unanswered}" if q.default_if_unanswered else "")
              + ")_" for q in r.questions]
    L += ["", "Reply per issue: approve / reject / modify (with a note), and answer the questions."]
    return "\n".join(L)


def verification_md(v: VerificationReport) -> str:
    L = ["| Check | Result | Detail |", "|---|---|---|"]
    L += [f"| {c.name} | {'PASS' if c.passed else 'FAIL'} | {_cell(c.detail)} |" for c in v.checks]
    if v.stress_checks:
        L += ["", "| Part | Load case | Method | Stress (MPa) | Allowable | SF | Deflection (mm) | Result |",
              "|---|---|---|---|---|---|---|---|"]
        L += [f"| {s.part} | {_cell(s.load_case)} | {_cell(s.method)} | {s.max_stress_mpa} | {s.allowable_mpa} | "
              f"{s.safety_factor} | {s.max_deflection_mm if s.max_deflection_mm is not None else '-'} | "
              f"{'PASS' if s.passed else 'FAIL'} |" for s in v.stress_checks]
    if v.total_mass_g is not None:
        L += ["", f"Total mass: {v.total_mass_g:.1f} g"]
    return "\n".join(L)


def memo_md(m: TechMemo, title: str, iteration: int, v: VerificationReport | None) -> str:
    L = [f"# Technical Memo: {title}", f"_Iteration {iteration}_", "", "## 1. Summary", m.summary, "",
         "## 2. Requirements traceability", "| Requirement | How met | Verified |", "|---|---|---|"]
    L += [f"| {_cell(r.requirement)} | {_cell(r.how_met)} | {'Y' if r.verified else 'N'} |" for r in m.traceability]
    L += ["", "## 3. Design changes from the sketch"]
    L += [f"- {c.description}: {c.reason}" + (f" (issues {', '.join(map(str, c.issue_ids))})" if c.issue_ids else "")
          for c in m.design_changes] or ["- None"]
    L += ["", "## 4. Assumptions"] + ([f"- {a}" for a in m.assumptions] or ["- None"])
    L += ["", "## 5. Materials & key dimensions/tolerances", m.materials_and_dims]
    L += ["", "## 6. Verification results", m.verification_summary]
    if v is not None:
        L += ["", verification_md(v)]
    L += ["", "## 7. Known risks / open items"] + ([f"- {r}" for r in m.risks_open_items] or ["- None"])
    L += ["", "## 8. Notes for the Machinist"]
    L += [f"- **{n.part}**{' (CRITICAL)' if n.critical else ''}: {n.note}" for n in m.machinist_notes] or ["- None"]
    return "\n".join(L) + "\n"


def tradeoffs_md(t: TradeoffReport) -> str:
    L = [f"### Iteration limit reached\n\n**Conflict:** {t.conflict}", ""]
    for i, o in enumerate(t.options, 1):
        L += [f"**Option {i}: {o.label}**: {o.description}",
              "- Pros: " + "; ".join(o.pros), "- Cons: " + "; ".join(o.cons), ""]
    if t.recommendation:
        L.append(f"**Engineer's recommendation:** {t.recommendation}")
    L.append("\nThis is your call. Pick an option (choosing one authorizes one more iteration) or stop here.")
    return "\n".join(L)
