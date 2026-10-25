"""HTTP API + static front end.  Run:  uvicorn app.main:app --port 8765"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, llm, store, workflow
from .cad import sandbox, validate
from .config import DATA_DIR, WEB_DIR, WORKSPACE
from .integrations import onshape
from .manufacturing import costing, slicer


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.set_loop(asyncio.get_running_loop())
    (WORKSPACE / "uploads").mkdir(parents=True, exist_ok=True)
    workflow.resume_all()
    yield


app = FastAPI(title="Traveler — CAD fabrication workflow", lifespan=lifespan)


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


# ---------------------------------------------------------------- system

@app.get("/api/health")
async def health():
    cfg = config.load()
    backend = await llm.resolve_backend(cfg, force=True)
    probe = await llm.probe(cfg)
    return {
        "backend": backend,
        "backend_setting": cfg["backend"],
        "probe": probe,
        "models": {"engineer": cfg["engineer_model"], "sub_engineer": cfg["sub_engineer_model"], "machinist": cfg["machinist_model"]},
        "openclaw_agents": cfg["openclaw_agents"],
        "slicer": slicer.find_slicer(cfg.get("slicer_path", "")),
        "onshape": onshape.configured(cfg),
        "rules": {k: cfg[k] for k in ("max_iterations", "max_build_attempts", "max_parallel_sub_engineers", "sub_engineer_threshold")},
        "ops": workflow.OPS,
    }


@app.get("/api/config")
def get_config():
    return config.public()


@app.put("/api/config")
async def put_config(body: dict):
    cfg = config.save(body)
    await llm.resolve_backend(cfg, force=True)
    return config.public(cfg)


@app.post("/api/dev/test/{what}")
async def dev_test(what: str):
    cfg = config.load()
    if what == "backend":
        backend = await llm.resolve_backend(cfg, force=True)
        t = time.time()
        try:
            text = await llm.chat("engineer", "ping", [{"role": "user", "content": 'Reply with the JSON {"ok": true}.'}], json_mode=True)
            if backend == "mock":
                text = '{"ok": true, "mock": true}'
            return {"ok": True, "backend": backend, "latency_s": round(time.time() - t, 2), "reply": text[:300]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "backend": backend, "detail": str(e)}
    if what == "cad":
        code = "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 20, 5).faces('>Z').workplane().hole(6)"
        res = await sandbox.run_part_code(code, None, None, cfg["cad_timeout_s"])
        return {"ok": res.get("ok", False), "detail": res.get("metrics") or res.get("error")}
    if what == "slicer":
        exe = slicer.find_slicer(cfg.get("slicer_path", ""))
        return {"ok": bool(exe), "detail": exe or "PrusaSlicer not found. Install it or set its path; FDM times will be estimated."}
    if what == "onshape":
        try:
            return await onshape.test(cfg)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)}
    raise HTTPException(404, "unknown test")


# ---------------------------------------------------------------- data

@app.get("/api/data/{name}")
def data(name: str):
    if name not in ("materials", "processes", "tolerances"):
        raise HTTPException(404)
    return json.loads((DATA_DIR / f"{name}.json").read_text())


class EstimateIn(BaseModel):
    bbox_mm: list[float]
    volume_mm3: float
    area_mm2: float
    quantity: int = 1
    tolerance: str = "ISO 2768-m"
    max_temp_c: float | None = None
    load: str = "light"
    environment: list[str] = []
    processes_available: list[str] = []


@app.post("/api/estimate")
def estimate(body: EstimateIn):
    req = workflow.normalize_requirements({**body.model_dump(), "dimensions": "x".join(str(v) for v in body.bbox_mm)})
    part = {"id": "part", "name": "Part", "quantity": 1, "metrics": body.model_dump()}
    return costing.plan_production([part], req)


class CadIn(BaseModel):
    code: str
    envelope_mm: list[float] | None = None
    tolerance_mm: float = 0.3


@app.post("/api/cad/run")
async def cad_run(body: CadIn):
    """Sandbox endpoint used by the Dev page and by OpenClaw agents (cadflow skill)."""
    cfg = config.load()
    res = await sandbox.run_part_code(body.code, None, None, cfg["cad_timeout_s"])
    tests = validate.part_tests(res, {"envelope_mm": body.envelope_mm}, body.tolerance_mm)
    return {"ok": validate.passed(tests), "tests": tests, "metrics": res.get("metrics"), "error": res.get("error"), "traceback": res.get("traceback")}


# ---------------------------------------------------------------- projects

@app.get("/api/projects")
def projects():
    out = []
    for p in store.list_projects():
        out.append({k: p.get(k) for k in ("id", "title", "created", "updated", "stage", "status", "revision", "iteration")}
                   | {"open_gates": len(workflow.open_gates(p)), "prompt": p["prompt"][:160],
                      "total_cost": (p.get("release") or {}).get("total_cost")})
    return out


@app.post("/api/projects")
async def create(body: dict):
    try:
        return workflow.create_project(body)
    except ValueError as e:
        _err(e)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "reference").name
    if not name.lower().endswith((".step", ".stp", ".stl")):
        raise HTTPException(400, "Upload a STEP or STL file.")
    dest = WORKSPACE / "uploads" / f"{int(time.time())}_{name}"
    dest.write_bytes(await file.read())
    res = await sandbox.run_job({"mode": "measure", "path": str(dest)}, 60)
    if not res.get("ok"):
        raise HTTPException(400, f"Could not read that file: {res.get('error')}")
    return {"name": name, "path": str(dest), "bbox_mm": res["metrics"]["bbox_mm"], "metrics": res["metrics"]}


@app.get("/api/projects/{pid}")
def project(pid: str):
    p = store.load(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


@app.delete("/api/projects/{pid}")
def delete(pid: str):
    import shutil

    d = store.project_dir(pid)
    if d.exists():
        shutil.rmtree(d)
    return {"ok": True}


@app.post("/api/projects/{pid}/gates/{gid}")
def answer(pid: str, gid: str, body: dict):
    try:
        return workflow.answer_gate(pid, gid, body)
    except KeyError as e:
        _err(e, 404)
    except ValueError as e:
        _err(e)


@app.post("/api/projects/{pid}/resume")
def resume(pid: str):
    p = store.load(pid)
    if not p:
        raise HTTPException(404)
    if p["status"] == "running":
        workflow.kick(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/log")
def log(pid: str):
    return store.read_jsonl(pid, "log.jsonl", 1000)


@app.get("/api/projects/{pid}/trace")
def trace(pid: str):
    return store.read_jsonl(pid, "trace.jsonl", 300)


@app.get("/api/approvals")
def approvals():
    out = []
    for p in store.list_projects():
        for g in workflow.open_gates(p):
            out.append({"project": p["id"], "project_title": p["title"], **{k: g[k] for k in ("id", "kind", "op", "agent", "title", "created")}})
    out.sort(key=lambda g: g["created"])
    return out


@app.get("/api/files/{pid}/{path:path}")
def files(pid: str, path: str):
    base = store.project_dir(pid).resolve()
    f = (base / path).resolve()
    if not str(f).startswith(str(base)) or not f.is_file():
        raise HTTPException(404)
    media = {"stl": "model/stl", "step": "application/step", "md": "text/markdown", "csv": "text/csv",
             "py": "text/x-python", "gcode": "text/plain", "json": "application/json", "zip": "application/zip"}.get(f.suffix[1:].lower())
    return FileResponse(f, media_type=media, filename=f.name)


@app.post("/api/projects/{pid}/onshape")
async def to_onshape(pid: str):
    p = store.load(pid)
    if not p or not (p.get("build") or {}).get("assembly"):
        raise HTTPException(400, "No assembly yet.")
    cfg = config.load()
    try:
        step = store.project_dir(pid) / p["build"]["dir"] / "assembly.step"
        res = await onshape.upload_step(cfg, f"{p['title']} rev {p['revision']}", step)
    except Exception as e:  # noqa: BLE001
        _err(e)
    p["onshape"] = res
    store.save(p)
    store.log(pid, "system", f"Sent rev {p['revision']} to Onshape: {res['url']}")
    return res


# ---------------------------------------------------------------- live events

@app.get("/api/events")
async def events(request: Request, project: str | None = None):
    q = store.subscribe(project)

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            store.unsubscribe(project, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- front end

@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
