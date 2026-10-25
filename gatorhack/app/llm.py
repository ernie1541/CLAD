"""LLM backends for the agents.

* openclaw – each role is an OpenClaw agent (cad-lead-engineer / cad-sub-engineer / cad-machinist)
             reached through the gateway's OpenAI-compatible /v1/chat/completions endpoint.
* ollama   – Gemma 4 called directly through Ollama's native /api/chat.
* openai   – any OpenAI-compatible /chat/completions API (OpenRouter, Google AI Studio, LM Studio, vLLM…).
* mock     – deterministic offline responses so the whole workflow can be demoed/tested with no model.

`auto` tries openclaw → ollama → openai (when a base URL is set) → mock.
"""
from __future__ import annotations

import json
import re
import time

import httpx

from . import config, mock, store

ROLE_MODEL_KEY = {
    "engineer": "engineer_model",
    "sub_engineer": "sub_engineer_model",
    "machinist": "machinist_model",
}

_resolved: dict = {"backend": None, "at": 0.0, "detail": ""}


class LLMError(RuntimeError):
    pass


async def probe(cfg: dict) -> dict:
    """Check which backends answer. Returns {'openclaw': (ok, detail), 'ollama': (ok, detail)}."""
    result = {}
    async with httpx.AsyncClient(timeout=2.5) as client:
        try:
            headers = {"Authorization": f"Bearer {cfg['openclaw_token']}"} if cfg.get("openclaw_token") else {}
            r = await client.get(cfg["openclaw_url"].rstrip("/") + "/v1/models", headers=headers)
            ok = r.status_code == 200
            result["openclaw"] = {"ok": ok, "detail": f"HTTP {r.status_code}" + ("" if ok else " (enable gateway.http.endpoints.chatCompletions and set the token)")}
        except Exception as e:  # noqa: BLE001
            result["openclaw"] = {"ok": False, "detail": f"unreachable ({type(e).__name__})"}
        try:
            r = await client.get(cfg["ollama_url"].rstrip("/") + "/api/tags")
            models = [m.get("name") for m in r.json().get("models", [])] if r.status_code == 200 else []
            wanted = {cfg["engineer_model"], cfg["sub_engineer_model"], cfg["machinist_model"]}
            missing = sorted(m for m in wanted if m not in models and f"{m}:latest" not in models)
            result["ollama"] = {
                "ok": r.status_code == 200,
                "models": models,
                "missing": missing,
                "detail": "ok" if not missing else "missing models: " + ", ".join(missing) + " (run: ollama pull <model>)",
            }
        except Exception as e:  # noqa: BLE001
            result["ollama"] = {"ok": False, "detail": f"unreachable ({type(e).__name__})", "models": [], "missing": []}
        if cfg.get("openai_base_url"):
            try:
                headers = {"Authorization": f"Bearer {cfg['openai_api_key']}"} if cfg.get("openai_api_key") else {}
                r = await client.get(cfg["openai_base_url"].rstrip("/") + "/models", headers=headers)
                ok = r.status_code == 200
                result["openai"] = {"ok": ok, "detail": "ok" if ok else f"HTTP {r.status_code} (check the base URL and API key)"}
            except Exception as e:  # noqa: BLE001
                result["openai"] = {"ok": False, "detail": f"unreachable ({type(e).__name__})"}
        else:
            result["openai"] = {"ok": False, "detail": "not configured"}
    return result


async def resolve_backend(cfg: dict, force: bool = False) -> str:
    choice = cfg.get("backend", "auto")
    if choice != "auto":
        return choice
    if not force and _resolved["backend"] and time.time() - _resolved["at"] < 30:
        return _resolved["backend"]
    p = await probe(cfg)
    if p["openclaw"]["ok"]:
        backend = "openclaw"
    elif p["ollama"]["ok"]:
        backend = "ollama"
    elif p["openai"]["ok"]:
        backend = "openai"
    else:
        backend = "mock"
    _resolved.update(backend=backend, at=time.time(), detail=json.dumps(p))
    return backend


async def chat(
    role: str,
    task: str,
    messages: list[dict],
    *,
    project_id: str | None = None,
    session: str = "",
    json_mode: bool = True,
    ctx: dict | None = None,
) -> str:
    """Send messages for an agent role. `task` names the step (used for tracing and mock dispatch)."""
    cfg = config.load()
    backend = await resolve_backend(cfg)
    model = cfg[ROLE_MODEL_KEY[role]]
    started = time.time()
    error = None
    text = ""
    try:
        if backend == "mock":
            text = mock.respond(role, task, messages, ctx or {})
        elif backend == "openclaw":
            text = await _openclaw(cfg, role, messages, project_id, session)
        elif backend == "ollama":
            text = await _ollama(cfg, model, messages, json_mode)
        elif backend == "openai":
            text = await _openai(cfg, model, messages, json_mode)
        else:
            raise LLMError(f"unknown backend {backend}")
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        raise LLMError(f"{backend} call failed for {role}/{task}: {error}") from e
    finally:
        if project_id:
            store.append_jsonl(project_id, "trace.jsonl", {
                "t": store.now(),
                "role": role,
                "task": task,
                "backend": backend,
                "model": cfg["openclaw_agents"].get(role) if backend == "openclaw" else (model if backend in ("ollama", "openai") else "mock"),
                "session": session,
                "latency_s": round(time.time() - started, 2),
                "messages": messages,
                "response": text,
                "error": error,
            })
    return text


async def _openclaw(cfg: dict, role: str, messages: list[dict], project_id: str | None, session: str) -> str:
    agent = cfg["openclaw_agents"][role]
    headers = {"Content-Type": "application/json", "x-openclaw-agent-id": agent}
    if cfg.get("openclaw_token"):
        headers["Authorization"] = f"Bearer {cfg['openclaw_token']}"
    if project_id:
        headers["x-openclaw-session-key"] = f"cadflow:{project_id}:{role}:{session or 'main'}"
    body = {"model": f"openclaw:{agent}", "messages": messages, "stream": False}
    async with httpx.AsyncClient(timeout=cfg["llm_timeout_s"]) as client:
        r = await client.post(cfg["openclaw_url"].rstrip("/") + "/v1/chat/completions", headers=headers, json=body)
        if r.status_code != 200:
            raise LLMError(f"OpenClaw HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
    return data["choices"][0]["message"]["content"] or ""


async def _ollama(cfg: dict, model: str, messages: list[dict], json_mode: bool) -> str:
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
    }
    if json_mode:
        body["format"] = "json"
    async with httpx.AsyncClient(timeout=cfg["llm_timeout_s"]) as client:
        r = await client.post(cfg["ollama_url"].rstrip("/") + "/api/chat", json=body)
        if r.status_code != 200:
            raise LLMError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
    return data.get("message", {}).get("content", "") or ""


async def _openai(cfg: dict, model: str, messages: list[dict], json_mode: bool) -> str:
    base = (cfg.get("openai_base_url") or "").rstrip("/")
    if not base:
        raise LLMError("OpenAI-compatible backend selected but no base URL is set (Dev page)")
    headers = {"Content-Type": "application/json"}
    if cfg.get("openai_api_key"):
        headers["Authorization"] = f"Bearer {cfg['openai_api_key']}"
    body = {"model": model, "messages": messages, "temperature": cfg["temperature"]}
    async with httpx.AsyncClient(timeout=cfg["llm_timeout_s"]) as client:
        r = None
        if json_mode:
            r = await client.post(base + "/chat/completions", headers=headers, json={**body, "response_format": {"type": "json_object"}})
        if r is None or r.status_code in (400, 422):  # some providers reject response_format
            r = await client.post(base + "/chat/completions", headers=headers, json=body)
        if r.status_code != 200:
            raise LLMError(f"API HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
    return data["choices"][0]["message"].get("content") or ""


# ---------------- parsing helpers ----------------

def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ``` fences, prose, trailing commas)."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth, in_str, esc = 0, False, False
    end = -1
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    chunk = t[start:end + 1] if end > 0 else t[start:]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", chunk)
        return json.loads(repaired)


async def chat_json(role: str, task: str, messages: list[dict], **kw) -> dict:
    """chat() + JSON parsing with up to two repair round-trips."""
    msgs = list(messages)
    last_err = None
    for _ in range(3):
        text = await chat(role, task, msgs, json_mode=True, **kw)
        try:
            return extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            msgs = msgs + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": f"That reply was not valid JSON ({e}). Reply again with ONLY the JSON object, no prose, no code fences."},
            ]
    raise LLMError(f"{role}/{task}: model never returned valid JSON ({last_err})")


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if blocks:
        return max(blocks, key=len).strip()
    if "import cadquery" in text:
        return text[text.find("import cadquery"):].strip()
    return text.strip()


def extract_tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S | re.I)
    return m.group(1).strip() if m else ""
