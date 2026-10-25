"""Project persistence (plain JSON on disk) + in-process event bus for live UI updates."""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from pathlib import Path

from .config import PROJECTS_DIR

_locks: dict[str, threading.Lock] = {}
_subscribers: dict[str, list[asyncio.Queue]] = {}
_global_subscribers: list[asyncio.Queue] = []
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def now() -> float:
    return round(time.time(), 3)


def new_id() -> str:
    return time.strftime("%y%m%d") + "-" + secrets.token_hex(3)


def project_dir(pid: str) -> Path:
    safe = "".join(c for c in pid if c.isalnum() or c in "-_")
    return PROJECTS_DIR / safe


def _lock(pid: str) -> threading.Lock:
    return _locks.setdefault(pid, threading.Lock())


def save(project: dict) -> None:
    d = project_dir(project["id"])
    d.mkdir(parents=True, exist_ok=True)
    project["updated"] = now()
    with _lock(project["id"]):
        tmp = d / "state.json.tmp"
        tmp.write_text(json.dumps(project, indent=2, default=str))
        tmp.replace(d / "state.json")
    publish(project["id"], {"type": "state", "stage": project.get("stage"), "status": project.get("status")})


def load(pid: str) -> dict | None:
    f = project_dir(pid) / "state.json"
    if not f.exists():
        return None
    with _lock(pid):
        return json.loads(f.read_text())


def list_projects() -> list[dict]:
    out = []
    if not PROJECTS_DIR.exists():
        return out
    for d in PROJECTS_DIR.iterdir():
        f = d / "state.json"
        if f.exists():
            try:
                out.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda p: p.get("created", 0), reverse=True)
    return out


def append_jsonl(pid: str, name: str, record: dict) -> None:
    d = project_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, "a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def read_jsonl(pid: str, name: str, limit: int = 500) -> list[dict]:
    f = project_dir(pid) / name
    if not f.exists():
        return []
    lines = f.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def log(pid: str, agent: str, message: str, level: str = "info", **extra) -> None:
    rec = {"t": now(), "agent": agent, "level": level, "message": message, **extra}
    append_jsonl(pid, "log.jsonl", rec)
    publish(pid, {"type": "log", **rec})


# ---------------- event bus ----------------

def subscribe(pid: str | None) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    if pid is None:
        _global_subscribers.append(q)
    else:
        _subscribers.setdefault(pid, []).append(q)
    return q


def unsubscribe(pid: str | None, q: asyncio.Queue) -> None:
    try:
        if pid is None:
            _global_subscribers.remove(q)
        else:
            _subscribers.get(pid, []).remove(q)
    except ValueError:
        pass


def publish(pid: str, event: dict) -> None:
    event = {"project": pid, **event}
    targets = list(_subscribers.get(pid, [])) + list(_global_subscribers)
    if not targets or _loop is None:
        return

    def _put():
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _put()
    else:
        _loop.call_soon_threadsafe(_put)
