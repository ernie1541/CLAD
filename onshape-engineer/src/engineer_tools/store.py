"""File-backed run state. One directory per run; safe across processes (fcntl lock)."""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows
    fcntl = None

_thread_lock = threading.RLock()


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- paths ----------------------------------------------------------- #
    def run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or ".." in run_id:
            raise ValueError(f"Invalid run_id '{run_id}'")
        return self.root / run_id

    def new_run_id(self, title: str) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:24] or "run"
        return f"{time.strftime('%Y%m%d')}-{slug}-{uuid.uuid4().hex[:6]}"

    # ---- locking --------------------------------------------------------- #
    @contextlib.contextmanager
    def lock(self, run_id: str) -> Iterator[None]:
        d = self.run_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        with _thread_lock:
            if fcntl is None:
                yield
                return
            with open(d / ".lock", "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    # ---- json io --------------------------------------------------------- #
    def read(self, run_id: str, name: str = "state.json") -> dict[str, Any]:
        p = self.run_dir(run_id) / name
        if not p.exists():
            raise FileNotFoundError(f"No {name} for run '{run_id}'")
        return json.loads(p.read_text())

    def exists(self, run_id: str, name: str = "state.json") -> bool:
        return (self.run_dir(run_id) / name).exists()

    def write(self, run_id: str, data: Any, name: str = "state.json") -> Path:
        p = self.run_dir(run_id) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        os.replace(tmp, p)
        return p

    def write_text(self, run_id: str, name: str, text: str) -> Path:
        p = self.run_dir(run_id) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def log(self, run_id: str, kind: str, **data: Any) -> None:
        p = self.run_dir(run_id) / "events.jsonl"
        with p.open("a") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, **data}, default=str) + "\n")

    def list_runs(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            s = d / "state.json"
            if s.exists():
                st = json.loads(s.read_text())
                out.append({"run_id": st["run_id"], "title": st["title"], "phase": st["phase"],
                            "iteration": st["iteration"]})
        return out
