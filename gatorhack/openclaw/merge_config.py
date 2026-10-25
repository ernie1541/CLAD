#!/usr/bin/env python3
"""Merge Traveler's settings into ~/.openclaw/openclaw.json and copy the gateway token into Traveler's .env.

Safe to run more than once. Always writes a timestamped backup first.
  python3 openclaw/merge_config.py [--config PATH] [--engineer gemma4:26b] [--sub gemma4:e4b]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_json5(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        import json5  # type: ignore
        return json5.loads(text)
    except ImportError:
        pass
    # minimal JSON5 → JSON: strip comments, quote bare keys, drop trailing commas, single → double quotes
    out, i, n, in_str, q = [], 0, len(text), False, ""
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == q:
                in_str = False
            i += 1; continue
        if c in "\"'":
            in_str, q = True, c
            out.append(c); i += 1; continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(c); i += 1
    s = "".join(out)
    s = re.sub(r"'((?:[^'\\]|\\.)*)'", lambda m: json.dumps(m.group(1)), s)
    s = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)", r'\1"\2"\3', s)
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


def deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        elif k not in dst:
            dst[k] = v
        elif not isinstance(v, (dict, list)):
            dst[k] = v
    return dst


def merge_by_id(existing: list, new: list) -> list:
    ids = {e.get("id"): i for i, e in enumerate(existing) if isinstance(e, dict)}
    for item in new:
        if item["id"] in ids:
            existing[ids[item["id"]]].update(item)
        else:
            existing.append(item)
    return existing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("OPENCLAW_CONFIG", str(Path.home() / ".openclaw" / "openclaw.json")))
    ap.add_argument("--engineer", default="gemma4:26b")
    ap.add_argument("--sub", default="gemma4:e4b")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    a = ap.parse_args()

    path = Path(a.config).expanduser()
    cfg: dict = {}
    if path.exists():
        try:
            cfg = load_json5(path.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"✗ Couldn't parse {path} ({e}).\n  Merge openclaw/openclaw.fragment.json5 into it by hand instead.")
            return 1
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy(path, backup)
        print(f"✓ Backed up your config → {backup}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"• No config at {path} yet; creating one.")

    # 1. gateway: enable the OpenAI-compatible endpoint, keep (or create) token auth
    gw = cfg.setdefault("gateway", {})
    deep_merge(gw, {"http": {"endpoints": {"chatCompletions": {"enabled": True}}}})
    auth = gw.setdefault("auth", {})
    token = auth.get("token") if auth.get("mode", "token") == "token" else None
    if not token:
        token = secrets.token_urlsafe(32)
        auth.update({"mode": "token", "token": token})
        print("✓ Created a gateway token")
    else:
        print("✓ Kept your existing gateway token")

    # 2. Ollama provider with the Gemma 4 models (keeps any models you already listed)
    prov = cfg.setdefault("models", {}).setdefault("providers", {}).setdefault("ollama", {})
    prov.setdefault("baseUrl", a.ollama)
    prov.setdefault("api", "ollama")
    prov.setdefault("apiKey", "ollama-local")
    prov["models"] = merge_by_id(prov.get("models", []) or [], [
        {"id": a.engineer, "contextWindow": 32768},
        {"id": a.sub, "contextWindow": 32768},
    ])

    # 3. agents: the three Traveler bots (+ keep your default agent as default)
    agents = cfg.setdefault("agents", {})
    deep_merge(agents, {"defaults": {"subagents": {"model": f"ollama/{a.sub}", "maxConcurrent": 4}}})
    lst = agents.get("list")
    if not lst:
        # Without a list OpenClaw uses an implicit "main" agent; keep it first and default so your chats don't move.
        lst = [{"id": "main", "default": True}]
        print("• Added your existing default agent as 'main' so it stays the default")
    oc = "~/.openclaw"
    agents["list"] = merge_by_id(lst, [
        {"id": "cad-lead-engineer", "workspace": f"{oc}/workspace-cad-lead-engineer", "model": {"primary": f"ollama/{a.engineer}"},
         "subagents": {"allowAgents": ["cad-sub-engineer"]}},
        {"id": "cad-sub-engineer", "workspace": f"{oc}/workspace-cad-sub-engineer", "model": {"primary": f"ollama/{a.sub}"}},
        {"id": "cad-machinist", "workspace": f"{oc}/workspace-cad-machinist", "model": {"primary": f"ollama/{a.engineer}"}},
    ])

    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"✓ Wrote {path}")

    # 4. hand the token to Traveler
    env = ROOT / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [l for l in lines if not l.startswith("OPENCLAW_GATEWAY_TOKEN=")]
    lines.append(f"OPENCLAW_GATEWAY_TOKEN={token}")
    env.write_text("\n".join(lines) + "\n")
    print(f"✓ Saved the token to {env}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
