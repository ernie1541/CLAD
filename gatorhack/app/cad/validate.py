"""Engineering tests every part and assembly must pass before anything moves forward."""
from __future__ import annotations


def _test(name: str, passed: bool, detail: str, severity: str = "fail") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "severity": "pass" if passed else severity}


def part_tests(run: dict, part: dict, tol_mm: float) -> list[dict]:
    """`run` is the sandbox result; `part` is the plan entry (envelope_mm etc.)."""
    tests: list[dict] = []
    if not run.get("ok"):
        stage = run.get("stage", "execute")
        tests.append(_test("Code screening" if stage == "screen" else "Script executes", False, run.get("error", "failed")))
        return tests
    tests.append(_test("Script executes", True, "CadQuery script ran and produced geometry"))
    m = run["metrics"]
    tests.append(_test("Valid B-rep", m["valid"], "OpenCascade BRepCheck " + ("passed" if m["valid"] else "reported an invalid shape")))
    expect_solids = int(part.get("solids", 1) or 1)
    tests.append(_test(
        "Solid count", m["solids"] == expect_solids,
        f"{m['solids']} solid(s), expected {expect_solids}" + ("" if m["solids"] == expect_solids else " — disconnected bodies or a missing union"),
        "fail" if m["solids"] == 0 else "warn",
    ))
    tests.append(_test("Positive volume", m["volume_mm3"] > 0, f"{m['volume_mm3']:.1f} mm³"))

    env = part.get("envelope_mm")
    if env and len(env) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in env):
        allow = max(tol_mm, 0.5)
        bb = m["bbox_mm"]
        s_bb, s_env = sorted(bb), sorted(env)
        over = [round(b - e, 2) for b, e in zip(s_bb, s_env) if b > e + allow]
        tests.append(_test(
            "Fits envelope", not over,
            f"bbox {fmt3(bb)} vs envelope {fmt3(env)} (±{allow} mm)" + (f"; oversize by {over} mm" if over else ""),
        ))
        tiny = s_bb[2] < 0.5 * s_env[2]
        tests.append(_test(
            "Fills envelope", not tiny,
            "largest dimension is " + ("well under" if tiny else "consistent with") + " the planned envelope",
            "warn",
        ))
        axis_ok = all(b <= e + allow for b, e in zip(bb, env))
        tests.append(_test("Axis orientation", axis_ok, "X/Y/Z extents match the plan" if axis_ok else "part is rotated relative to the plan's X/Y/Z envelope", "warn"))
    return tests


def assembly_tests(run: dict, requirements: dict, tol_mm: float) -> list[dict]:
    tests: list[dict] = []
    if not run.get("ok"):
        return [_test("Assembly builds", False, run.get("error", "failed"))]
    tests.append(_test("Assembly builds", True, "all parts placed and exported as one STEP assembly"))
    inter = run.get("interferences", [])
    tests.append(_test(
        "No interference", not inter,
        "no overlapping solids" if not inter else "; ".join(f"{i['a']} ∩ {i['b']} = {i['volume_mm3']} mm³" for i in inter),
    ))
    target = parse_dims(requirements.get("dimensions", ""))
    if target:
        bb = run["metrics"]["bbox_mm"]
        allow = max(tol_mm, 0.5) * 2
        over = [round(b - e, 2) for b, e in zip(sorted(bb), sorted(target)) if b > e + allow]
        tests.append(_test("Overall size", not over, f"assembly {fmt3(bb)} vs requested {fmt3(target)}" + (f"; over by {over}" if over else "")))
    return tests


def passed(tests: list[dict]) -> bool:
    return all(t["passed"] or t["severity"] == "warn" for t in tests)


def fmt3(v) -> str:
    return " × ".join(f"{x:g}" for x in v) + " mm"


def parse_dims(text: str) -> list[float] | None:
    """'120x80x40 mm', '120 x 80 x 40', '4.7"x3"x1.5"' (inches converted)."""
    import re

    if not text:
        return None
    t = str(text).lower().replace("×", "x").replace("*", "x")
    nums = re.findall(r"(\d+(?:\.\d+)?)", t)
    if len(nums) < 3:
        return None
    vals = [float(n) for n in nums[:3]]
    if '"' in t or "in" in re.sub(r"[^a-z]", " ", t).split():
        vals = [v * 25.4 for v in vals]
    elif "cm" in t:
        vals = [v * 10 for v in vals]
    return vals
