"""Screen model-written CadQuery code, then run it in a child process with a timeout."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from ..config import ROOT

ALLOWED_IMPORTS = {"cadquery", "math"}
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "input",
    "breakpoint", "getattr", "setattr", "delattr", "memoryview", "exit", "quit",
}


class UnsafeCode(ValueError):
    pass


def screen(code: str) -> None:
    """Reject code that could touch the machine. Raises UnsafeCode / SyntaxError."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise UnsafeCode(f"import of '{a.name}' is not allowed (only cadquery and math)")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                raise UnsafeCode(f"import from '{node.module}' is not allowed (only cadquery and math)")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise UnsafeCode(f"use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeCode(f"dunder attribute access '{node.attr}' is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise UnsafeCode("global/nonlocal statements are not allowed")


async def run_job(job: dict, timeout_s: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="cadjob-") as tmp:
        job_path = Path(tmp) / "job.json"
        res_path = Path(tmp) / "result.json"
        job_path.write_text(json.dumps(job))
        env = {k: v for k, v in os.environ.items() if not k.endswith(("TOKEN", "KEY", "SECRET"))}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.cad.runner", str(job_path), str(res_path),
            cwd=str(ROOT), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": f"Timeout: CAD script ran longer than {timeout_s}s (simplify the geometry)"}
        if not res_path.exists():
            return {"ok": False, "error": "CAD process crashed", "traceback": stderr.decode(errors="ignore")[-1500:]}
        return json.loads(res_path.read_text())


async def run_part_code(code: str, step: Path | None, stl: Path | None, timeout_s: int) -> dict:
    try:
        screen(code)
    except SyntaxError as e:
        return {"ok": False, "stage": "screen", "error": f"SyntaxError: {e.msg} (line {e.lineno})"}
    except UnsafeCode as e:
        return {"ok": False, "stage": "screen", "error": f"UnsafeCode: {e}"}
    res = await run_job({"mode": "part", "code": code, "step": str(step) if step else None, "stl": str(stl) if stl else None}, timeout_s)
    res.setdefault("stage", "execute")
    return res
