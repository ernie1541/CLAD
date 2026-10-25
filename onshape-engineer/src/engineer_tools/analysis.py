"""Closed-form hand calcs. Units: N, mm, MPa (= N/mm^2). E comes from the reference DB in GPa."""
from __future__ import annotations

import math
from typing import Any, Optional

from .reference_db import ReferenceDB

CASES = ("axial", "cantilever_end_load", "simply_supported_center_load", "bolt_shear")


def section_props(section: dict[str, float]) -> tuple[float, float, float]:
    """Return (area mm^2, I mm^4, c mm) about the bending axis."""
    kind = section.get("kind")
    if kind == "rect":
        b, h = section["b"], section["h"]  # h is measured in the bending direction
        return b * h, b * h ** 3 / 12, h / 2
    if kind == "circle":
        d = section["d"]
        return math.pi * d ** 2 / 4, math.pi * d ** 4 / 64, d / 2
    if kind == "tube":
        do, di = section["do"], section["di"]
        return math.pi * (do ** 2 - di ** 2) / 4, math.pi * (do ** 4 - di ** 4) / 64, do / 2
    raise ValueError("section.kind must be 'rect' {b,h}, 'circle' {d} or 'tube' {do,di}")


def hand_calc(db: ReferenceDB, *, case: str, material: str, force_n: float,
              length_mm: Optional[float] = None, section: Optional[dict[str, float]] = None,
              n_bolts: Optional[int] = None, bolt_minor_dia_mm: Optional[float] = None,
              kt: float = 1.0, min_sf: float = 2.0) -> dict[str, Any]:
    m = db.get(material)
    if m is None:
        raise ValueError(f"Material '{material}' is not in the reference DB. Available: {db.keys()}")
    if case not in CASES:
        raise ValueError(f"case must be one of {CASES}")
    sy, E = float(m["yield"]), float(m["E"]) * 1000.0
    defl = None

    if case == "axial":
        if not section:
            raise ValueError("axial needs section")
        A, _, _ = section_props(section)
        sigma, method = force_n / A, "sigma = F/A"
    elif case in ("cantilever_end_load", "simply_supported_center_load"):
        if length_mm is None or not section:
            raise ValueError(f"{case} needs length_mm and section")
        _, I, c = section_props(section)
        if case == "cantilever_end_load":
            M, defl = force_n * length_mm, force_n * length_mm ** 3 / (3 * E * I)
            method = "cantilever, end load: M=FL, sigma=Mc/I, delta=FL^3/(3EI)"
        else:
            M, defl = force_n * length_mm / 4, force_n * length_mm ** 3 / (48 * E * I)
            method = "simply supported, center load: M=FL/4, sigma=Mc/I, delta=FL^3/(48EI)"
        sigma = M * c / I
    else:  # bolt_shear
        if not n_bolts or not bolt_minor_dia_mm:
            raise ValueError("bolt_shear needs n_bolts and bolt_minor_dia_mm")
        A = math.pi * bolt_minor_dia_mm ** 2 / 4
        sigma = math.sqrt(3) * force_n / (n_bolts * A)
        method = "bolt shear: tau=F/(nA), sigma_vm=sqrt(3)*tau (material = bolt material)"

    sigma *= kt
    sf = sy / sigma if sigma > 0 else float("inf")
    return {
        "method": method + (f", Kt={kt}" if kt != 1.0 else ""),
        "max_stress_mpa": round(sigma, 3),
        "allowable_mpa": sy,
        "safety_factor": round(sf, 3),
        "max_deflection_mm": None if defl is None else round(defl, 5),
        "passed": sf >= min_sf,
        "min_safety_factor_required": min_sf,
        "reference_ids": [material],
        "reference_source": m.get("source"),
    }
