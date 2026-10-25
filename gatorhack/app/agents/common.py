from __future__ import annotations

import json
import re


def requirements_block(p: dict) -> str:
    r = p["requirements"]
    wc = r.get("work_conditions", {})
    lines = [
        f"Request: {p['prompt']}",
        f"Dimensions: {r.get('dimensions') or 'not specified'}",
        f"Tolerance: {r.get('tolerance') or 'ISO 2768-m (default)'} → ±{r.get('tolerance_mm', 0.3):g} mm",
        f"Application: {r.get('application') or 'not specified'}",
        f"Budget: {('$' + str(r['budget'])) if r.get('budget') else 'not specified'}",
        f"Quantity: {r.get('quantity', 1)}",
        f"Work conditions: max temp {wc.get('max_temp_c') or 'room'} °C; load {wc.get('load', 'light')}; "
        f"environment {', '.join(wc.get('environment') or []) or 'indoor/dry'}; notes: {wc.get('notes') or '—'}",
        f"Processes available to the user: {', '.join(r.get('processes_available') or []) or 'any'}",
    ]
    if r.get("reference"):
        ref = r["reference"]
        lines.append(f"Reference CAD uploaded: {ref.get('name')} with bounding box {ref.get('bbox_mm')} mm")
    if p.get("answers"):
        lines.append("User answers so far:")
        for qa in p["answers"]:
            lines.append(f"  Q: {qa['q']}\n  A: {qa['a']}")
    return "\n".join(lines)


def slug(s: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")
    return s[:40] or fallback


def vec3(v, default=(0.0, 0.0, 0.0)) -> list[float]:
    try:
        out = [float(x) for x in list(v)[:3]]
        if len(out) == 3:
            return out
    except (TypeError, ValueError):
        pass
    return list(default)


def compact(obj) -> str:
    return json.dumps(obj, indent=1, default=str)
