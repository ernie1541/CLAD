"""The single agentic workflow.

Route (shop-traveler operations):
  Op 10 Intake            requirements captured from the front end (no designer stage)
  Op 20 Engineering review  lead engineer lists drawbacks + fixes          → GATE: user accepts fixes
  Op 30 Model plan          lead engineer splits into sub-assemblies       → GATE: user approves plan / spawning
  Op 40 Build & test        sub-engineers model parts in parallel, every part is tested, lead assembles
  Op 50 Technical memo      lead engineer writes memo                      → GATE: user signs hand-off
  Op 60 Production plan     machinist costs every process × material       → GATE: user picks strategy or sends back
  Op 70 Release             stable, hashed files (STEP/STL/G-code/BOM/plan) packaged as a revision

Any agent can stop the run with questions (clarify gates). Revisions (plan changes, memo revisions,
machinist send-backs) share one counter capped by config.max_iterations (default 3).
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import shutil
import traceback
import zipfile
from pathlib import Path

from . import config, store
from .agents import engineer, machinist, sub_engineer
from .cad import sandbox, validate
from .manufacturing import costing, slicer, tolerance

OPS = [
    {"op": 10, "stage": "intake", "name": "Intake"},
    {"op": 20, "stage": "review", "name": "Engineering review"},
    {"op": 30, "stage": "plan", "name": "Model plan"},
    {"op": 40, "stage": "build", "name": "Build & test"},
    {"op": 50, "stage": "memo", "name": "Technical memo"},
    {"op": 60, "stage": "machining", "name": "Production plan"},
    {"op": 70, "stage": "release", "name": "Release"},
]

_tasks: dict[str, asyncio.Task] = {}


# ------------------------------------------------------------------ creation

def normalize_requirements(data: dict) -> dict:
    dims = validate.parse_dims(data.get("dimensions", "")) or []
    largest = max(dims) if dims else 100.0
    env = data.get("environment") or []
    if isinstance(env, str):
        env = [e.strip() for e in env.split(",") if e.strip()]
    procs = data.get("processes_available") or []
    if isinstance(procs, str):
        procs = [x.strip() for x in procs.split(",") if x.strip()]

    def num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    load = data.get("load") or "light"
    return {
        "dimensions": data.get("dimensions", ""),
        "dimensions_mm": dims,
        "tolerance": data.get("tolerance", "") or "ISO 2768-m",
        "tolerance_mm": tolerance.required_tolerance_mm(data.get("tolerance"), largest),
        "application": data.get("application", ""),
        "budget": num(data.get("budget")),
        "quantity": int(num(data.get("quantity")) or 1),
        "work_conditions": {
            "max_temp_c": num(data.get("max_temp_c")),
            "load": load,
            "environment": env,
            "notes": data.get("work_notes", ""),
        },
        "processes_available": procs,
        "reference": data.get("reference"),
    }


def short_title(text: str, limit: int = 64) -> str:
    first = text.strip().split("\n")[0].split(". ")[0].rstrip(".")
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def create_project(data: dict) -> dict:
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Describe the part or assembly you need.")
    p = {
        "id": store.new_id(),
        "title": short_title(data.get("title") or prompt),
        "created": store.now(),
        "prompt": prompt,
        "requirements": normalize_requirements(data),
        "stage": "review",
        "status": "running",
        "iteration": 1,
        "revision": None,
        "revisions": [],
        "gates": [],
        "answers": [],
        "accepted_fixes": [],
        "rejected_fixes": [],
        "fix_comments": "",
        "feedback": [],
        "review": None,
        "plan": None,
        "build": None,
        "memo": None,
        "manufacturing": None,
        "release": None,
        "clarify_rounds": {},
        "error": None,
    }
    store.save(p)
    store.log(p["id"], "system", "Op 10 intake recorded. Handing to the lead engineer.")
    kick(p["id"])
    return p


# ------------------------------------------------------------------ runner

def kick(pid: str) -> None:
    """Start (or continue) the runner for a project. Safe to call from any thread."""
    def _start():
        t = _tasks.get(pid)
        if t and not t.done():
            # a runner is mid-flight; re-kick once it finishes so new state is picked up
            t.add_done_callback(lambda _t: kick(pid))
            return
        _tasks[pid] = asyncio.get_running_loop().create_task(_run(pid))

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is store._loop:
        _start()
    elif store._loop is not None:
        store._loop.call_soon_threadsafe(_start)
    else:
        raise RuntimeError("event loop not initialised")


async def _run(pid: str) -> None:
    while True:
        p = store.load(pid)
        if not p or p["status"] != "running":
            return
        stage = p["stage"]
        try:
            handler = {"review": do_review, "plan": do_plan, "build": do_build, "memo": do_memo,
                       "machining": do_machining, "release": do_release}.get(stage)
            if handler is None:
                return
            await handler(p)
        except Exception as e:  # noqa: BLE001
            p = store.load(pid) or p
            p["status"] = "awaiting_user"
            p["error"] = f"{type(e).__name__}: {e}"
            open_gate(p, "error", agent="system", title=f"Op {op_of(stage)} stopped on an error",
                      body=f"`{p['error']}`\n\nCheck the model backend on the Dev page, then retry this operation.",
                      data={"stage": stage, "trace": traceback.format_exc()[-2000:]})
            store.log(pid, "system", p["error"], level="error")
            store.save(p)
            return


def resume_all() -> None:
    for p in store.list_projects():
        if p.get("status") == "running":
            kick(p["id"])


def op_of(stage: str) -> int:
    return next((o["op"] for o in OPS if o["stage"] == stage), 0)


# ------------------------------------------------------------------ gates

def open_gate(p: dict, kind: str, *, agent: str, title: str, body: str = "", data: dict | None = None) -> dict:
    cfg = config.load()
    gate = {
        "id": f"g{len(p['gates']) + 1}",
        "kind": kind,
        "stage": p["stage"],
        "op": op_of(p["stage"]),
        "agent": agent,
        "title": title,
        "body": body,
        "data": data or {},
        "status": "open",
        "created": store.now(),
        "can_revise": p["iteration"] < cfg["max_iterations"],
        "iteration": p["iteration"],
        "max_iterations": cfg["max_iterations"],
    }
    p["gates"].append(gate)
    p["status"] = "awaiting_user"
    store.log(p["id"], agent, f"Waiting for your approval: {title}", level="gate", gate=gate["id"])
    store.publish(p["id"], {"type": "gate", "gate": gate["id"], "title": title})
    return gate


def open_gates(p: dict) -> list[dict]:
    return [g for g in p["gates"] if g["status"] == "open"]


def answer_gate(pid: str, gid: str, resp: dict) -> dict:
    p = store.load(pid)
    if not p:
        raise KeyError("project not found")
    gate = next((g for g in p["gates"] if g["id"] == gid), None)
    if not gate or gate["status"] != "open":
        raise ValueError("This approval is already closed.")
    action = resp.get("action", "approve")
    cfg = config.load()

    if action == "abort":
        _close(gate, resp)
        p["stage"], p["status"] = "aborted", "done"
        store.log(pid, "user", "Project stopped by user.")
        store.save(p)
        return p

    if action == "revise":
        if p["iteration"] >= cfg["max_iterations"]:
            raise ValueError(f"Revision limit reached ({cfg['max_iterations']}). Approve or stop the project.")
        text = (resp.get("comments") or "").strip()
        if not text:
            raise ValueError("Say what should change.")
        p["iteration"] += 1
        p["feedback"] = [text] + ([resp["extra"]] if resp.get("extra") else [])
        _close(gate, resp)
        p["stage"], p["status"] = "plan", "running"
        store.log(pid, "user", f"Revision {p['iteration']}/{cfg['max_iterations']} requested: {text}")
        store.save(p)
        kick(pid)
        return p

    kind = gate["kind"]
    if kind == "clarify":
        answers = resp.get("answers") or {}
        for i, q in enumerate(gate["data"].get("questions", [])):
            a = str(answers.get(str(i), "")).strip()
            p["answers"].append({"q": q, "a": a or "(no answer — use your best default and state it)", "stage": gate["stage"]})
        p["clarify_rounds"][gate["stage"]] = p["clarify_rounds"].get(gate["stage"], 0) + 1
    elif kind == "review_fixes":
        accepted = set(resp.get("accepted") or [])
        fixes = p["review"]["proposed_fixes"]
        p["accepted_fixes"] = [f for f in fixes if f["id"] in accepted]
        p["rejected_fixes"] = [f for f in fixes if f["id"] not in accepted]
        p["fix_comments"] = (resp.get("comments") or "").strip()
        p["stage"] = "plan"
    elif kind == "plan":
        p["stage"] = "build"
        p["build"] = None  # fresh build, new revision
        p["feedback"] = []
    elif kind == "sub_question":
        answers = resp.get("answers") or {}
        for i, item in enumerate(gate["data"].get("questions", [])):
            st = p["build"]["parts"][item["part_id"]]
            st["guidance"] = (st.get("guidance", "") + " " + str(answers.get(str(i), ""))).strip()
            st["status"] = "pending"
            st["question"] = ""
    elif kind == "build_failure":
        guidance = (resp.get("comments") or "").strip()
        for pid_ in gate["data"].get("failed", []):
            st = p["build"]["parts"][pid_]
            st["status"], st["attempts"], st["history"] = "pending", 0, st["history"][-1:]
            if guidance:
                st["guidance"] = guidance
    elif kind == "memo":
        p["stage"] = "machining"
    elif kind == "production":
        sid = resp.get("strategy") or p["manufacturing"]["review"]["recommended_strategy"]
        if sid not in {s["id"] for s in p["manufacturing"]["study"]["strategies"]}:
            raise ValueError("Pick one of the listed strategies.")
        strat = next(s for s in p["manufacturing"]["study"]["strategies"] if s["id"] == sid)
        if not strat["feasible"]:
            raise ValueError("That strategy leaves parts unproducible. Pick another or send the design back.")
        p["manufacturing"]["selected"] = sid
        p["stage"] = "release"
    elif kind == "error":
        p["error"] = None
    _close(gate, resp)
    p["status"] = "running"
    store.log(pid, "user", f"Approved: {gate['title']}")
    store.save(p)
    kick(pid)
    return p


def _close(gate: dict, resp: dict) -> None:
    gate["status"] = "answered"
    gate["response"] = resp
    gate["answered_at"] = store.now()


def _clarify(p: dict, agent: str, questions: list[str], why: str) -> bool:
    """Open a clarify gate if the agent asked questions and this stage hasn't exhausted its 2 rounds."""
    if questions and p["clarify_rounds"].get(p["stage"], 0) < 2:
        open_gate(p, "clarify", agent=agent, title=why, body="Answer what you can. Blank answers let the agent use a stated default.",
                  data={"questions": questions[:6]})
        store.save(p)
        return True
    return False


# ------------------------------------------------------------------ Op 20

async def do_review(p: dict) -> None:
    store.log(p["id"], "engineer", "Reviewing the request for drawbacks, risks and missing information.")
    review = await engineer.review(p)
    p["review"] = review
    if _clarify(p, "engineer", review["questions_for_user"], "The lead engineer needs a few answers before reviewing"):
        return
    open_gate(p, "review_fixes", agent="engineer", title="Accept the engineer's proposed fixes",
              body=review.get("summary", ""), data={"drawbacks": review["drawbacks"], "fixes": review["proposed_fixes"],
                                                    "assumptions": review["assumptions"]})
    store.save(p)


# ------------------------------------------------------------------ Op 30

async def do_plan(p: dict) -> None:
    cfg = config.load()
    fb = p.get("feedback") or None
    store.log(p["id"], "engineer", "Planning the model and splitting it into sub-assemblies." + (" (revision)" if fb else ""))
    plan = await engineer.plan(p, fb)
    p["plan"] = plan
    if _clarify(p, "engineer", plan["questions_for_user"], "The lead engineer needs answers to finish the plan"):
        return
    n_parts = sum(len(s["parts"]) for s in plan["subassemblies"])
    spawn = n_parts >= cfg["sub_engineer_threshold"]
    plan["spawn_sub_engineers"] = spawn
    plan["sub_engineer_count"] = len(plan["subassemblies"]) if spawn else 0
    body = plan["approach"]
    if spawn:
        body += (f"\n\nOn approval the lead engineer will summon **{len(plan['subassemblies'])} sub-engineer(s)** "
                 f"({cfg['sub_engineer_model']}) to build the sub-assemblies in parallel.")
    else:
        body += f"\n\nOn approval the lead engineer ({cfg['engineer_model']}) builds this single part itself."
    open_gate(p, "plan", agent="engineer", title="Approve the model plan", body=body, data={"plan": plan})
    store.save(p)


# ------------------------------------------------------------------ Op 40

def _rev_letter(n: int) -> str:
    return chr(ord("A") + n) if n < 26 else f"R{n}"


def _new_build(p: dict) -> dict:
    rev = _rev_letter(len(p["revisions"]))
    p["revisions"].append(rev)
    p["revision"] = rev
    d = store.project_dir(p["id"]) / f"rev-{rev}"
    for sub in ("parts", "placed", "code", "sub"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    parts = {}
    for s in p["plan"]["subassemblies"]:
        for pt in s["parts"]:
            parts[pt["id"]] = {"status": "pending", "attempts": 0, "history": [], "sub": s["id"], "guidance": "", "question": "",
                               "tests": [], "metrics": None, "files": {}, "code": "", "notes": ""}
    return {"revision": rev, "dir": f"rev-{rev}", "subassemblies": {}, "parts": parts, "assembly": None}


async def do_build(p: dict) -> None:
    cfg = config.load()
    plan = p["plan"]
    if not p.get("build"):
        p["build"] = _new_build(p)
        store.save(p)
    b = p["build"]
    rev_dir = store.project_dir(p["id"]) / b["dir"]
    tol = p["requirements"]["tolerance_mm"]
    spawn = plan.get("spawn_sub_engineers", True)
    sem = asyncio.Semaphore(cfg["max_parallel_sub_engineers"])
    store.log(p["id"], "engineer", (f"Summoning {len(plan['subassemblies'])} sub-engineer(s) for rev {b['revision']}."
                                    if spawn else f"Building rev {b['revision']} directly."))

    async def run_sub(idx: int, sub: dict) -> None:
        role = "sub_engineer" if spawn else "engineer"
        agent_name = f"Sub-engineer {idx}" if spawn else "Lead engineer"
        model = cfg["sub_engineer_model"] if spawn else cfg["engineer_model"]
        b["subassemblies"][sub["id"]] = {"agent": agent_name, "model": model, "status": "running"}
        async with sem:
            siblings = []
            for part in sub["parts"]:
                st = b["parts"][part["id"]]
                if st["status"] == "passed":
                    siblings.append({"id": part["id"], "name": part["name"], "bbox_mm": st["metrics"]["bbox_mm"]})
                    continue
                st["status"] = "building"
                store.save(p)
                while st["attempts"] < cfg["max_build_attempts"]:
                    store.log(p["id"], agent_name, f"Modeling {part['name']} (attempt {st['attempts'] + 1}/{cfg['max_build_attempts']}).")
                    res = await sub_engineer.write_part(p, sub, part, siblings, st["history"], role=role, guidance=st.get("guidance", ""))
                    if res["question"]:
                        st["status"], st["question"] = "question", res["question"]
                        store.log(p["id"], agent_name, f"Needs your input on {part['name']}: {res['question']}", level="gate")
                        break
                    st["attempts"] += 1
                    st["code"], st["notes"] = res["code"], res["notes"]
                    step = rev_dir / "parts" / f"{part['id']}.step"
                    stl = rev_dir / "parts" / f"{part['id']}.stl"
                    (rev_dir / "code" / f"{part['id']}.py").write_text(res["code"])
                    run = await sandbox.run_part_code(res["code"], step, stl, cfg["cad_timeout_s"])
                    tests = validate.part_tests(run, part, tol)
                    st["tests"] = tests
                    if validate.passed(tests):
                        st["status"] = "passed"
                        st["metrics"] = run["metrics"]
                        st["files"] = {"step": f"{b['dir']}/parts/{part['id']}.step", "stl": f"{b['dir']}/parts/{part['id']}.stl",
                                       "code": f"{b['dir']}/code/{part['id']}.py"}
                        store.log(p["id"], agent_name, f"{part['name']} passed {sum(t['passed'] for t in tests)}/{len(tests)} tests.", level="pass")
                        break
                    failures = [f"{t['name']}: {t['detail']}" for t in tests if not t["passed"] and t["severity"] == "fail"]
                    if run.get("traceback"):
                        failures.append("Traceback:\n" + run["traceback"])
                    st["history"].append({"code": res["code"], "failures": failures})
                    st["history"] = st["history"][-3:]
                    store.log(p["id"], agent_name, f"{part['name']} failed tests: {failures[0][:160]}", level="fail")
                    store.save(p)
                if st["status"] == "building":
                    st["status"] = "failed"
                store.save(p)
                if st["status"] != "passed":
                    b["subassemblies"][sub["id"]]["status"] = "blocked"
                    return
                siblings.append({"id": part["id"], "name": part["name"], "bbox_mm": st["metrics"]["bbox_mm"]})
            b["subassemblies"][sub["id"]]["status"] = "done"
            store.save(p)

    await asyncio.gather(*(run_sub(i + 1, s) for i, s in enumerate(plan["subassemblies"])))

    questions = [{"part_id": k, "part": _part(plan, k)["name"], "question": v["question"], "agent": b["subassemblies"][v["sub"]]["agent"]}
                 for k, v in b["parts"].items() if v["status"] == "question"]
    if questions:
        open_gate(p, "sub_question", agent="sub-engineer", title="A sub-engineer needs your input",
                  data={"questions": questions})
        store.save(p)
        return
    failed = [k for k, v in b["parts"].items() if v["status"] == "failed"]
    if failed:
        open_gate(p, "build_failure", agent="engineer",
                  title=f"{len(failed)} part(s) failed testing after {cfg['max_build_attempts']} attempts",
                  body="Give the sub-engineers guidance and retry, request a plan revision, or stop.",
                  data={"failed": failed, "details": {k: {"name": _part(plan, k)["name"], "tests": b["parts"][k]["tests"],
                                                          "last_error": (b["parts"][k]["history"][-1]["failures"] if b["parts"][k]["history"] else [])}
                                                      for k in failed}})
        store.save(p)
        return
    pending = [k for k, v in b["parts"].items() if v["status"] != "passed"]
    if pending:
        return  # will be re-run (shouldn't normally happen)

    # ---- lead engineer assembles + tests the assembly
    store.log(p["id"], "engineer", "All parts passed. Assembling and running interference checks.")
    job = {"mode": "assembly", "name": plan["assembly_name"], "step": str(rev_dir / "assembly.step"),
           "sub_steps": {s["id"]: str(rev_dir / "sub" / f"{s['id']}.step") for s in plan["subassemblies"]},
           "subassemblies": [{**s, "parts": [{**pt, "step": str(rev_dir / "parts" / f"{pt['id']}.step"),
                                               "placed_stl": str(rev_dir / "placed" / f"{pt['id']}.stl")} for pt in s["parts"]]}
                             for s in plan["subassemblies"]]}
    run = await sandbox.run_job(job, cfg["cad_timeout_s"] * 2)
    tests = validate.assembly_tests(run, p["requirements"], tol)
    b["assembly"] = {"tests": tests, "metrics": run.get("metrics"), "interferences": run.get("interferences", []),
                     "files": {"step": f"{b['dir']}/assembly.step"} if run.get("ok") else {}}
    if not run.get("ok"):
        open_gate(p, "build_failure", agent="engineer", title="The assembly could not be built",
                  body=f"`{run.get('error')}`", data={"failed": [], "details": {}})
        store.save(p)
        return
    store.log(p["id"], "engineer", f"Assembly rev {b['revision']}: " + ", ".join(f"{t['name']} {'✓' if t['passed'] else '✗'}" for t in tests),
              level="pass" if validate.passed(tests) else "fail")
    p["stage"] = "memo"
    store.save(p)


def _part(plan: dict, pid: str) -> dict:
    for s in plan["subassemblies"]:
        for pt in s["parts"]:
            if pt["id"] == pid:
                return pt
    return {"id": pid, "name": pid}


# ------------------------------------------------------------------ Op 50

def verification_appendix(p: dict) -> str:
    b = p["build"]
    lines = ["", "---", "", f"### Verification record (rev {b['revision']}, generated by the test harness)", "",
             "| Part | Sub-assembly | Built by | Size (mm) | Volume (cm³) | Attempts | Tests |", "|---|---|---|---|---|---|---|"]
    for s in p["plan"]["subassemblies"]:
        for pt in s["parts"]:
            st = b["parts"][pt["id"]]
            m = st["metrics"] or {}
            ok = sum(t["passed"] for t in st["tests"])
            lines.append(f"| {pt['name']} | {s['name']} | {b['subassemblies'].get(s['id'], {}).get('agent', '')} | "
                         f"{' × '.join(f'{v:g}' for v in m.get('bbox_mm', []))} | {m.get('volume_mm3', 0) / 1000:.2f} | {st['attempts']} | {ok}/{len(st['tests'])} |")
    lines += ["", "| Assembly test | Result | Detail |", "|---|---|---|"]
    for t in b["assembly"]["tests"]:
        lines.append(f"| {t['name']} | {'pass' if t['passed'] else 'FAIL'} | {t['detail']} |")
    return "\n".join(lines)


async def do_memo(p: dict) -> None:
    store.log(p["id"], "engineer", "Writing the technical memo.")
    text = await engineer.memo(p)
    text = text.strip() + "\n" + verification_appendix(p)
    p["memo"] = text
    (store.project_dir(p["id"]) / p["build"]["dir"] / "technical_memo.md").write_text(text)
    failing = [t for t in p["build"]["assembly"]["tests"] if not t["passed"]]
    body = "Sign this memo to hand the assembly to the machinist."
    if failing:
        body = "**Assembly tests failed:** " + "; ".join(f"{t['name']} — {t['detail']}" for t in failing) + \
               "\n\nRequest a revision to fix this, or approve anyway to hand off as-is."
    open_gate(p, "memo", agent="engineer", title=f"Sign the technical memo (rev {p['revision']})", body=body,
              data={"failing_tests": failing})
    store.save(p)


# ------------------------------------------------------------------ Op 60

def _parts_for_costing(p: dict) -> list[dict]:
    out = []
    for s in p["plan"]["subassemblies"]:
        for pt in s["parts"]:
            st = p["build"]["parts"][pt["id"]]
            out.append({"id": pt["id"], "name": pt["name"], "quantity": pt["quantity"], "metrics": st["metrics"],
                        "material_hint": pt.get("material_hint", "")})
    return out


async def do_machining(p: dict) -> None:
    cfg = config.load()
    rev_dir = store.project_dir(p["id"]) / p["build"]["dir"]
    parts = _parts_for_costing(p)
    slice_data = {}
    exe = slicer.find_slicer(cfg.get("slicer_path", ""))
    if exe:
        (rev_dir / "gcode").mkdir(exist_ok=True)
        for pt in parts:
            res = await slicer.slice_stl(rev_dir / "parts" / f"{pt['id']}.stl", rev_dir / "gcode" / f"{pt['id']}.gcode", exe)
            if res.get("ok"):
                slice_data[pt["id"]] = res
                store.log(p["id"], "machinist", f"Sliced {pt['name']}: {res.get('print_hours', 0):.2f} h, {res.get('filament_g', 0):.0f} g.")
            else:
                store.log(p["id"], "machinist", f"Slicer failed on {pt['name']}; using the volume estimate.", level="warn")
    else:
        store.log(p["id"], "machinist", "PrusaSlicer not found; FDM time is estimated from volume.")
    store.log(p["id"], "machinist", "Costing every process and material against your tolerance, budget and work conditions.")
    study = costing.plan_production(parts, p["requirements"], slice_data)
    review = await machinist.review(p, study)
    p["manufacturing"] = {"study": study, "review": review, "sliced": {k: {kk: v.get(kk) for kk in ("print_hours", "filament_g")} for k, v in slice_data.items()},
                          "slicer": bool(exe), "selected": None}
    if _clarify(p, "machinist", review["questions_for_user"], "The machinist needs a few answers before quoting"):
        return
    rec = next(s for s in study["strategies"] if s["id"] == review["recommended_strategy"])
    body = review["rationale"]
    if review["send_back_to_engineer"]:
        body += f"\n\n**The machinist recommends sending this back to the engineer:** {review['send_back_reason']}"
    open_gate(p, "production", agent="machinist", title="Choose the production strategy",
              body=body, data={"recommended": rec["id"], "send_back": review["send_back_to_engineer"],
                               "send_back_reason": review["send_back_reason"], "dfm": review["dfm_changes"]})
    store.save(p)


# ------------------------------------------------------------------ Op 70

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def do_release(p: dict) -> None:
    b = p["build"]
    proj = store.project_dir(p["id"])
    rev_dir = proj / b["dir"]
    rel = rev_dir / "release"
    if rel.exists():
        shutil.rmtree(rel)
    (rel / "parts").mkdir(parents=True)
    mf = p["manufacturing"]
    strat = next(s for s in mf["study"]["strategies"] if s["id"] == mf["selected"])
    by_part = {a["part_id"]: a for a in strat["assignments"]}

    shutil.copy(rev_dir / "assembly.step", rel / f"{p['plan']['assembly_name']}_rev{b['revision']}.step")
    for pid, st in b["parts"].items():
        for ext in ("step", "stl"):
            shutil.copy(rev_dir / "parts" / f"{pid}.{ext}", rel / "parts" / f"{pid}_rev{b['revision']}.{ext}")
        a = by_part.get(pid)
        g = rev_dir / "gcode" / f"{pid}.gcode"
        if a and a["process"] == "fdm" and g.exists():
            (rel / "gcode").mkdir(exist_ok=True)
            shutil.copy(g, rel / "gcode" / f"{pid}_rev{b['revision']}.gcode")
    (rel / "technical_memo.md").write_text(p["memo"] or "")

    # BOM
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["part_id", "name", "sub_assembly", "qty_total", "process", "material", "size_mm", "mass_g", "unit_cost_usd", "line_cost_usd", "machine_hours_each", "lead_days"])
    for s in p["plan"]["subassemblies"]:
        for pt in s["parts"]:
            a = by_part[pt["id"]]
            m = b["parts"][pt["id"]]["metrics"]
            w.writerow([pt["id"], pt["name"], s["name"], a["quantity"], a["process_name"], a["material_name"],
                        "x".join(f"{v:g}" for v in m["bbox_mm"]), a["mass_g"], a["unit_cost"], a["line_cost"], a["machine_hours"], a["lead_days"]])
    (rel / "bom.csv").write_text(buf.getvalue())

    # Manufacturing plan
    rv = mf["review"]
    lines = [f"# Production plan — {p['title']} (rev {b['revision']})", "",
             f"**Strategy:** {strat['label']}  ", f"**Order quantity:** {mf['study']['order_quantity']}  ",
             f"**Total cost:** ${strat['total_cost']:,.2f}  ", f"**Lead time:** {strat['lead_days']} days  ", "",
             "## Machinist rationale", "", rv.get("rationale", ""), "", "## Parts", "",
             "| Part | Process | Material | Qty | Unit $ | Line $ | Notes |", "|---|---|---|---|---|---|---|"]
    for a in strat["assignments"]:
        lines.append(f"| {a['part_id']} | {a['process_name']} | {a['material_name']} | {a['quantity']} | {a['unit_cost']:.2f} | {a['line_cost']:.2f} | {'; '.join(a['notes'])} |")
    if rv.get("process_notes"):
        lines += ["", "## Shop notes", ""] + [f"- {n}" for n in rv["process_notes"]]
    if rv.get("dfm_changes"):
        lines += ["", "## DFM suggestions for the next revision", ""] + [f"- {n}" for n in rv["dfm_changes"]]
    lines += ["", "## Assumptions", ""] + [f"- {a}" for a in mf["study"]["assumptions"]]
    (rel / "manufacturing_plan.md").write_text("\n".join(lines))

    manifest = {"project": p["id"], "title": p["title"], "revision": b["revision"], "released": store.now(),
                "strategy": strat["id"], "files": {}}
    for f in sorted(rel.rglob("*")):
        if f.is_file():
            manifest["files"][str(f.relative_to(rel))] = {"sha256": _sha(f), "bytes": f.stat().st_size}
    (rel / "manifest.json").write_text(json.dumps(manifest, indent=2))

    zname = f"{p['plan']['assembly_name']}_rev{b['revision']}.zip"
    with zipfile.ZipFile(rev_dir / zname, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(rel.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(rel))
    p["release"] = {"revision": b["revision"], "zip": f"{b['dir']}/{zname}", "dir": f"{b['dir']}/release",
                    "files": list(manifest["files"].keys()), "manifest": manifest, "strategy": strat["label"],
                    "total_cost": strat["total_cost"], "lead_days": strat["lead_days"]}
    p["stage"], p["status"] = "released", "done"
    store.log(p["id"], "machinist", f"Released rev {b['revision']}: {len(manifest['files'])} files, ${strat['total_cost']:,.2f}, {strat['lead_days']} days.", level="pass")
    store.save(p)
