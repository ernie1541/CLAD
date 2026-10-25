"""End-to-end run in mock mode: drives every approval gate through the HTTP API and checks the release.

    python tests/test_e2e_mock.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def wait_for(fn, timeout=240, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError


def main() -> int:
    ws = tempfile.mkdtemp(prefix="traveler-test-")
    env = {**os.environ, "CADFLOW_BACKEND": "mock", "CADFLOW_WORKSPACE": ws}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        c = httpx.Client(base_url=BASE, timeout=30)
        wait_for(lambda: _ok(c, "/api/health"), 30)
        h = c.get("/api/health").json()
        assert h["backend"] == "mock", h
        p = c.post("/api/projects", json={
            "prompt": "Wall bracket to hold a 2 kg router in a garage",
            "dimensions": "120 x 80 x 100 mm", "tolerance": "ISO 2768-m", "application": "garage wall mount",
            "budget": 150, "quantity": 2, "max_temp_c": 45, "load": "moderate", "environment": ["outdoor"],
        }).json()
        pid = p["id"]
        print("project", pid)

        def gate(kind):
            def f():
                pr = c.get(f"/api/projects/{pid}").json()
                if pr["status"] == "awaiting_user":
                    g = [g for g in pr["gates"] if g["status"] == "open"][0]
                    assert g["kind"] in (kind, "error"), (g["kind"], g.get("body"), pr.get("error"))
                    if g["kind"] == "error":
                        raise AssertionError(g["body"] + "\n" + g["data"].get("trace", ""))
                    return pr, g
                return None
            return wait_for(f)

        pr, g = gate("review_fixes")
        print("review:", [f["fix"] for f in g["data"]["fixes"]])
        r = c.post(f"/api/projects/{pid}/gates/{g['id']}", json={"action": "approve", "accepted": [f["id"] for f in g["data"]["fixes"]]})
        assert r.status_code == 200, r.text

        pr, g = gate("plan")
        print("plan subassemblies:", [s["id"] for s in g["data"]["plan"]["subassemblies"]], "spawn:", g["data"]["plan"]["spawn_sub_engineers"])
        # exercise one revision loop
        r = c.post(f"/api/projects/{pid}/gates/{g['id']}", json={"action": "revise", "comments": "Keep plates 6 mm."})
        assert r.status_code == 200, r.text
        pr, g = gate("plan")
        assert pr["iteration"] == 2
        c.post(f"/api/projects/{pid}/gates/{g['id']}", json={"action": "approve"})

        pr, g = gate("memo")
        parts = pr["build"]["parts"]
        print("parts:", {k: (v["status"], v["attempts"]) for k, v in parts.items()})
        assert parts["gusset"]["attempts"] == 2, "gusset should have failed once then passed"
        assert all(t["passed"] for t in pr["build"]["assembly"]["tests"]), pr["build"]["assembly"]["tests"]
        c.post(f"/api/projects/{pid}/gates/{g['id']}", json={"action": "approve"})

        pr, g = gate("production")
        strategies = pr["manufacturing"]["study"]["strategies"]
        for s in strategies:
            print(f"  {s['label']}: ${s['total_cost']} {s['lead_days']}d feasible={s['feasible']} " +
                  ", ".join(f"{a['part_id']}={a['process']}/{a['material']}" for a in s["assignments"]))
        r = c.post(f"/api/projects/{pid}/gates/{g['id']}", json={"action": "approve", "strategy": g["data"]["recommended"]})
        assert r.status_code == 200, r.text

        pr = wait_for(lambda: (lambda x: x if x["stage"] == "released" else None)(c.get(f"/api/projects/{pid}").json()))
        print("released files:", pr["release"]["files"])
        z = c.get(f"/api/files/{pid}/{pr['release']['zip']}")
        assert z.status_code == 200 and len(z.content) > 1000
        stl = c.get(f"/api/files/{pid}/{pr['build']['dir']}/placed/base_plate.stl")
        assert stl.status_code == 200
        # iteration limit enforced
        assert c.get("/api/approvals").json() == []
        print("OK — workspace", ws)
        return 0
    finally:
        srv.terminate()
        out = srv.communicate(timeout=10)[0].decode(errors="ignore")
        if "Traceback" in out:
            print(out[-3000:])


def _ok(c, path):
    try:
        return c.get(path).status_code == 200
    except httpx.HTTPError:
        return False


if __name__ == "__main__":
    sys.exit(main())
