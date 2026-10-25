"""Sub-engineer (small Gemma): writes CadQuery for one part of its sub-assembly; retries on test failures."""
from __future__ import annotations

from .. import llm, prompts
from .common import compact


async def write_part(p: dict, sub: dict, part: dict, siblings: list[dict], history: list[dict], role: str = "sub_engineer",
                     guidance: str = "") -> dict:
    """Returns {'code', 'notes', 'question'}. `history` = previous attempts [{code, failures}]."""
    tol = p["requirements"].get("tolerance_mm", 0.3)
    brief = {
        "sub_assembly": {"id": sub["id"], "name": sub["name"], "purpose": sub.get("purpose", "")},
        "part": {k: part[k] for k in ("id", "name", "description", "local_frame", "envelope_mm", "features", "interfaces", "solids")},
        "tolerance_mm": tol,
        "already_built_in_this_subassembly": siblings,
    }
    msgs = [
        {"role": "system", "content": prompts.SUB_ENGINEER_SYSTEM},
        {"role": "user", "content": (
            "Reference CadQuery examples (style guide, do not copy blindly):\n```python\n" + prompts.CADQUERY_EXAMPLES + "```\n\n"
            "Model this part:\n" + compact(brief) +
            (f"\n\nGuidance from the user: {guidance}" if guidance else "")
        )},
    ]
    for h in history:
        msgs.append({"role": "assistant", "content": f"<notes>previous attempt</notes>\n<question>none</question>\n```python\n{h['code']}\n```"})
        msgs.append({"role": "user", "content": (
            "That script FAILED the engineering tests:\n- " + "\n- ".join(h["failures"]) +
            "\nFix every failure and return the complete corrected script in the same reply format."
        )})
    text = await llm.chat(role, "write_part", msgs, project_id=p["id"], session=f"sub:{sub['id']}", json_mode=False,
                          ctx={"project": p, "part": part, "sub": sub, "attempt": len(history) + 1})
    q = llm.extract_tag(text, "question")
    if q.lower().strip(" .") in ("none", "no", "n/a", ""):
        q = ""
    return {"code": llm.extract_code(text), "notes": llm.extract_tag(text, "notes"), "question": q}
