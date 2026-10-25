"""Runtime configuration.

Precedence: workspace/config.json (edited from the Dev page) > environment > defaults.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
WORKSPACE = Path(os.environ.get("CADFLOW_WORKSPACE", ROOT / "workspace"))
PROJECTS_DIR = WORKSPACE / "projects"
CONFIG_FILE = WORKSPACE / "config.json"

DEFAULTS: dict = {
    # auto -> OpenClaw gateway if reachable, else Ollama, else OpenAI-compatible API (if set), else mock
    "backend": "auto",
    "openclaw_url": "http://127.0.0.1:18789",
    "openclaw_token": "",
    "ollama_url": "http://127.0.0.1:11434",
    # Any OpenAI-compatible API: OpenRouter, Google AI Studio, LM Studio, vLLM, OpenAI, Groq…
    "openai_base_url": "",
    "openai_api_key": "",
    # Gemma 4 (released 2026-04-02). Big model leads, small models are the summoned sub-engineers.
    "engineer_model": "gemma4:26b",
    "sub_engineer_model": "gemma4:e4b",
    "machinist_model": "gemma4:26b",
    "openclaw_agents": {
        "engineer": "cad-lead-engineer",
        "sub_engineer": "cad-sub-engineer",
        "machinist": "cad-machinist",
    },
    "num_ctx": 16384,
    "temperature": 0.2,
    "llm_timeout_s": 600,
    # Workflow rules (from the planning notes)
    "max_iterations": 3,          # design revision loops (plan changes, memo revisions, machinist send-backs)
    "max_build_attempts": 3,      # auto-retries per part when CAD tests fail
    "max_parallel_sub_engineers": 4,
    "sub_engineer_threshold": 2,  # spawn sub-engineers when the plan has at least this many parts
    "cad_timeout_s": 120,
    # Integrations
    "slicer_path": "",            # PrusaSlicer CLI; auto-detected when blank
    "onshape_base_url": "https://cad.onshape.com",
    "onshape_access_key": "",
    "onshape_secret_key": "",
}

ENV_MAP = {
    "backend": "CADFLOW_BACKEND",
    "openclaw_url": "OPENCLAW_URL",
    "openclaw_token": "OPENCLAW_GATEWAY_TOKEN",
    "ollama_url": "OLLAMA_URL",
    "openai_base_url": "OPENAI_BASE_URL",
    "openai_api_key": "OPENAI_API_KEY",
    "engineer_model": "ENGINEER_MODEL",
    "sub_engineer_model": "SUB_ENGINEER_MODEL",
    "machinist_model": "MACHINIST_MODEL",
    "slicer_path": "SLICER_PATH",
    "onshape_access_key": "ONSHAPE_ACCESS_KEY",
    "onshape_secret_key": "ONSHAPE_SECRET_KEY",
}

SECRET_KEYS = {"openclaw_token", "openai_api_key", "onshape_access_key", "onshape_secret_key"}

_lock = threading.Lock()


def _coerce(default, value: str):
    if isinstance(default, bool):
        return value.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    for key, env in ENV_MAP.items():
        if os.environ.get(env):
            cfg[key] = _coerce(DEFAULTS[key], os.environ[env])
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            for k, v in saved.items():
                if k not in DEFAULTS:
                    continue
                if v == "" and os.environ.get(ENV_MAP.get(k, "")):
                    continue  # a blank Dev-page field never hides a value set in .env
                cfg[k] = v
        except json.JSONDecodeError:
            pass
    return cfg


def save(update: dict) -> dict:
    with _lock:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        current = {}
        if CONFIG_FILE.exists():
            try:
                current = json.loads(CONFIG_FILE.read_text())
            except json.JSONDecodeError:
                current = {}
        for k, v in update.items():
            if k not in DEFAULTS:
                continue
            if k in SECRET_KEYS and v == "••••":
                continue  # unchanged masked secret
            current[k] = v
        CONFIG_FILE.write_text(json.dumps(current, indent=2))
    return load()


def public(cfg: dict | None = None) -> dict:
    cfg = dict(cfg or load())
    for k in SECRET_KEYS:
        if cfg.get(k):
            cfg[k] = "••••"
    return cfg
