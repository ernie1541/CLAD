"""Reference database. Agents may only use material properties that come from here."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any, Optional


class ReferenceDB:
    def __init__(self, path: Optional[Path] = None):
        text = (Path(path).read_text() if path
                else resources.files("engineer_tools.data").joinpath("materials.json").read_text())
        raw = json.loads(text)
        self.meta = raw.pop("_meta", {})
        self.materials: dict[str, dict[str, Any]] = raw

    def keys(self) -> list[str]:
        return sorted(self.materials)

    def get(self, key: str) -> Optional[dict[str, Any]]:
        return self.materials.get(key)

    def search(self, query: str) -> list[dict[str, Any]]:
        terms = query.lower().replace("-", " ").replace("_", " ").split()
        out = []
        for k, v in self.materials.items():
            hay = f"{k} {v.get('name', '')}".lower().replace("-", " ").replace("_", " ")
            if all(t in hay for t in terms):
                out.append({"id": k, **v})
        return out
