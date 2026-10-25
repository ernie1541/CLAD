"""Tolerance handling backed by data/tolerances.json (ISO 2768-1 general tolerances)."""
from __future__ import annotations

import json
import re
from functools import lru_cache

from ..config import DATA_DIR


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads((DATA_DIR / "tolerances.json").read_text())


def iso2768(cls: str, nominal_mm: float) -> float | None:
    cls = cls.lower()
    for row in table()["iso2768_linear"]["ranges"]:
        if row["over"] < nominal_mm <= row["up_to"]:
            v = row.get(cls)
            return float(v) if v is not None else None
    return None


def required_tolerance_mm(spec: str | float | None, largest_dim_mm: float = 100.0) -> float:
    """Convert a user tolerance spec to a ± value in mm. Defaults to ISO 2768-m."""
    if spec is None or spec == "":
        return iso2768("m", largest_dim_mm) or 0.3
    if isinstance(spec, (int, float)):
        return abs(float(spec))
    s = str(spec).strip().lower()
    m = re.search(r"2768[\s-]*([fmcv])\b", s) or re.fullmatch(r"([fmcv])", s)
    if m:
        return iso2768(m.group(1), largest_dim_mm) or 0.3
    for word, cls in (("fine", "f"), ("medium", "m"), ("coarse", "c"), ("very coarse", "v")):
        if s == word:
            return iso2768(cls, largest_dim_mm) or 0.3
    num = re.search(r"(\d*\.?\d+)", s)
    if num:
        v = float(num.group(1))
        if "in" in s or '"' in s or "thou" in s:
            v = v * 25.4 if "thou" not in s else v * 0.0254
        return v
    return 0.3
