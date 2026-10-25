"""PrusaSlicer CLI integration (real print time + filament), with graceful fallback when not installed."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

MAC_PATHS = [
    "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
    "/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
]


def find_slicer(configured: str = "") -> str | None:
    if configured and Path(configured).exists():
        return configured
    for name in ("prusa-slicer", "PrusaSlicer", "prusaslicer"):
        p = shutil.which(name)
        if p:
            return p
    for p in MAC_PATHS:
        if os.path.exists(p):
            return p
    return None


def _parse_time(s: str) -> float:
    total = 0.0
    for val, unit in re.findall(r"(\d+)\s*([dhms])", s):
        total += int(val) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total / 3600.0


def parse_gcode_stats(text: str) -> dict:
    out = {}
    m = re.search(r";\s*estimated printing time \(normal mode\)\s*=\s*(.+)", text)
    if m:
        out["print_hours"] = round(_parse_time(m.group(1)), 3)
    m = re.search(r";\s*filament used \[g\]\s*=\s*([\d.]+)", text)
    if m:
        out["filament_g"] = float(m.group(1))
    m = re.search(r";\s*filament used \[mm\]\s*=\s*([\d.]+)", text)
    if m:
        out["filament_mm"] = float(m.group(1))
    return out


async def slice_stl(stl: Path, gcode: Path, slicer: str, layer_height: float = 0.2, infill: int = 20, timeout_s: int = 300) -> dict:
    cmd = [slicer, "--export-gcode", "--layer-height", str(layer_height), "--fill-density", f"{infill}%",
           "--output", str(gcode), str(stl)]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": "slicer timeout"}
    if proc.returncode != 0 or not gcode.exists():
        return {"ok": False, "error": (err or out).decode(errors="ignore")[-400:]}
    head = gcode.read_text(errors="ignore")
    stats = parse_gcode_stats(head[-20000:] + head[:5000])
    return {"ok": True, "gcode": str(gcode), **stats}
