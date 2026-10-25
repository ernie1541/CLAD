"""Executed in a child process: `python -m app.cad.runner job.json result.json`.

Modes
  part      – exec CadQuery code, export STEP + STL, return mass properties
  assembly  – place part STEPs (nested sub-assemblies), export assembly STEP, placed STLs, interference matrix
  measure   – bounding box / volume of an uploaded STEP or STL reference
Everything heavy (OpenCascade) stays out of the web server process.
"""
from __future__ import annotations

import json
import math
import sys
import traceback


def _shape_from_result(result):
    import cadquery as cq

    if isinstance(result, cq.Assembly):
        return result.toCompound()
    if isinstance(result, cq.Workplane):
        vals = [v for v in result.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise ValueError("`result` Workplane contains no solids (did you forget to extrude?)")
        return vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    if isinstance(result, cq.Shape):
        return result
    raise TypeError(f"`result` must be a cadquery Workplane/Shape, got {type(result).__name__}")


def _metrics(shape) -> dict:
    bb = shape.BoundingBox()
    solids = shape.Solids()
    vol = sum(s.Volume() for s in solids) if solids else 0.0
    try:
        com = shape.Center()
        com = [round(com.x, 3), round(com.y, 3), round(com.z, 3)]
    except Exception:  # noqa: BLE001
        com = None
    return {
        "valid": bool(shape.isValid()),
        "solids": len(solids),
        "volume_mm3": round(vol, 3),
        "area_mm2": round(shape.Area(), 3),
        "bbox_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
        "bbox_min": [round(bb.xmin, 3), round(bb.ymin, 3), round(bb.zmin, 3)],
        "bbox_max": [round(bb.xmax, 3), round(bb.ymax, 3), round(bb.zmax, 3)],
        "center_of_mass": com,
        "faces": len(shape.Faces()),
    }


def _export(shape, step_path: str | None, stl_path: str | None) -> None:
    import cadquery as cq

    if step_path:
        cq.exporters.export(shape, step_path, exportType="STEP")
    if stl_path:
        cq.exporters.export(shape, stl_path, exportType="STL", tolerance=0.05, angularTolerance=0.15)


def run_part(job: dict) -> dict:
    import cadquery as cq

    ns = {"cq": cq, "cadquery": cq, "math": math, "__name__": "__cadpart__"}
    exec(compile(job["code"], "<part>", "exec"), ns)  # noqa: S102 - code was AST-screened by the parent
    if "result" not in ns:
        raise NameError("the script must assign the final solid to a variable named `result`")
    shape = _shape_from_result(ns["result"])
    m = _metrics(shape)
    _export(shape, job.get("step"), job.get("stl"))
    return {"ok": True, "metrics": m}


def _location(pos, rot):
    import cadquery as cq

    loc = cq.Location(cq.Vector(*pos))
    rx, ry, rz = rot
    # apply rotations about the part origin X, then Y, then Z
    r = cq.Location()
    if rx:
        r = cq.Location(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), rx) * r
    if ry:
        r = cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 1, 0), ry) * r
    if rz:
        r = cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), rz) * r
    return loc * r


def run_assembly(job: dict) -> dict:
    import cadquery as cq

    colors = [(0.45, 0.55, 0.68), (0.85, 0.66, 0.2), (0.5, 0.62, 0.5), (0.7, 0.45, 0.4), (0.6, 0.6, 0.65), (0.55, 0.45, 0.65)]
    top = cq.Assembly(name=job.get("name", "assembly"))
    placed = []  # (part_id, shape_in_world)
    ci = 0
    for sub in job["subassemblies"]:
        sub_loc = _location(sub.get("position_mm", [0, 0, 0]), sub.get("rotation_deg", [0, 0, 0]))
        sub_assy = cq.Assembly(name=sub["id"])
        for part in sub["parts"]:
            shape = cq.importers.importStep(part["step"]).val()
            loc = _location(part.get("position_mm", [0, 0, 0]), part.get("rotation_deg", [0, 0, 0]))
            r, g, b = colors[ci % len(colors)]
            ci += 1
            sub_assy.add(shape, name=part["id"], loc=loc, color=cq.Color(r, g, b))
            world = shape.moved(sub_loc * loc)
            placed.append((part["id"], world))
            if part.get("placed_stl"):
                cq.exporters.export(world, part["placed_stl"], exportType="STL", tolerance=0.05, angularTolerance=0.15)
        if job.get("sub_steps", {}).get(sub["id"]):
            _save_assy(sub_assy, job["sub_steps"][sub["id"]])
        top.add(sub_assy, name=sub["id"], loc=sub_loc)
    _save_assy(top, job["step"])

    # Interference: pairwise common volume (bbox prefilter)
    interferences = []
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            a_id, a = placed[i]
            b_id, b = placed[j]
            ba, bb = a.BoundingBox(), b.BoundingBox()
            if (ba.xmax < bb.xmin or bb.xmax < ba.xmin or ba.ymax < bb.ymin or bb.ymax < ba.ymin
                    or ba.zmax < bb.zmin or bb.zmax < ba.zmin):
                continue
            try:
                common = a.intersect(b)
                v = sum(s.Volume() for s in common.Solids())
            except Exception:  # noqa: BLE001
                v = 0.0
            if v > 0.01:
                interferences.append({"a": a_id, "b": b_id, "volume_mm3": round(v, 3)})

    whole = cq.Compound.makeCompound([s for _, s in placed])
    return {"ok": True, "metrics": _metrics(whole), "interferences": interferences}


def _save_assy(assy, path: str) -> None:
    if hasattr(assy, "export"):
        try:
            assy.export(path)
            return
        except Exception:  # noqa: BLE001
            pass
    assy.save(path)


def run_measure(job: dict) -> dict:
    import cadquery as cq

    path = job["path"]
    if path.lower().endswith((".step", ".stp")):
        shape = cq.importers.importStep(path).val()
        return {"ok": True, "metrics": _metrics(shape)}
    # STL: compute bbox directly (ascii or binary)
    import struct

    data = open(path, "rb").read()
    pts = []
    if data[:5].lower() == b"solid" and b"facet" in data[:1000]:
        for line in data.decode(errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("vertex"):
                pts.append(tuple(float(v) for v in line.split()[1:4]))
    else:
        n = struct.unpack("<I", data[80:84])[0]
        for i in range(n):
            off = 84 + i * 50 + 12
            for k in range(3):
                pts.append(struct.unpack("<3f", data[off + 12 * k: off + 12 * k + 12]))
    if not pts:
        raise ValueError("could not read STL vertices")
    xs, ys, zs = zip(*pts)
    return {"ok": True, "metrics": {"bbox_mm": [round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3), round(max(zs) - min(zs), 3)], "triangles": len(pts) // 3}}


def main() -> None:
    job_path, result_path = sys.argv[1], sys.argv[2]
    job = json.load(open(job_path))
    try:
        mode = job.get("mode", "part")
        out = {"part": run_part, "assembly": run_assembly, "measure": run_measure}[mode](job)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc().splitlines()
        # keep the traceback short and focused on the user code
        relevant = [l for l in tb if "<part>" in l or not l.startswith("  File")]
        out = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": "\n".join(relevant[-14:])}
    with open(result_path, "w") as fh:
        json.dump(out, fh)


if __name__ == "__main__":
    main()
