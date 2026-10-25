"""Unit conversion and safe arithmetic so the model never does mental math.

The Onshape MCP server takes numeric lengths in INCHES. Plans are in millimetres.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any

MM_PER_IN = 25.4

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)),
          "cos": lambda d: math.cos(math.radians(d)), "tan": lambda d: math.tan(math.radians(d)),
          "min": min, "max": max, "abs": abs, "round": round}
_CONSTS = {"pi": math.pi}


def safe_eval(expr: str, names: dict[str, float]) -> float:
    """Evaluate +-*/** arithmetic over plan parameter names. Trig functions take degrees."""
    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id in names:
                return float(names[n.id])
            if n.id in _CONSTS:
                return _CONSTS[n.id]
            raise NameError(f"Unknown parameter '{n.id}'. Known: {sorted(names)}")
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(f"Unsupported expression element: {ast.dump(n)[:80]}")

    return ev(ast.parse(expr.strip().lstrip("#"), mode="eval"))


def mm_to_in(v: float) -> float:
    return v / MM_PER_IN


def onshape_expression(value: float, unit: str) -> str:
    if unit == "mm":
        return f"{_fmt(value)} mm"
    if unit == "deg":
        return f"{_fmt(value)} deg"
    return _fmt(value)


def _fmt(v: float) -> str:
    return f"{v:.6g}"


_LEN = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": MM_PER_IN, "ft": 304.8, "meter": 1000.0,
        "millimeter": 1.0, "inch": MM_PER_IN}
_ANG = {"deg": 1.0, "degree": 1.0, "rad": 180.0 / math.pi, "radian": 180.0 / math.pi}


def parse_onshape_expression(expr: str) -> tuple[float | None, str | None]:
    """'6 mm' -> (6.0, 'mm'); '0.25 in' -> (6.35, 'mm'); '45 deg' -> (45.0,'deg'); '3' -> (3.0, None).
    Returns (None, None) for expressions that reference other variables."""
    m = re.fullmatch(r"\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\*?\s*([A-Za-z]*)\s*", expr or "")
    if not m:
        return None, None
    val, unit = float(m.group(1)), m.group(2).lower()
    if not unit:
        return val, None
    if unit in _LEN:
        return val * _LEN[unit], "mm"
    if unit in _ANG:
        return val * _ANG[unit], "deg"
    return None, None


def parse_variables(raw: Any) -> dict[str, str]:
    """Accept the Onshape MCP get_variables text ('- name = expr (desc)') or a JSON list/dict."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        if "variables" in raw:
            return parse_variables(raw["variables"])
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "variables" in item:
                out.update(parse_variables(item["variables"]))
            elif isinstance(item, dict) and "name" in item:
                out[item["name"]] = str(item.get("expression", item.get("value", "")))
        return out
    if isinstance(raw, str):
        for line in raw.splitlines():
            m = re.match(r"\s*-?\s*([A-Za-z_]\w*)\s*=\s*(.+?)(?:\s+\(.*\))?\s*$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out
