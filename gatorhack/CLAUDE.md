# Traveler — notes for Claude Code

Prompt-to-fabrication CAD workflow. Python 3.10+ FastAPI backend, vanilla-JS front end, CadQuery for CAD,
Gemma 4 via OpenClaw (or Ollama) for the agents. No designer stage by design: Engineer → Machinist only.

## Commands
- Run: `./start.sh` (or `.venv/bin/python -m uvicorn app.main:app --port 8765 --reload`)
- Offline run: `CADFLOW_BACKEND=mock ./start.sh`
- E2E test (must pass before committing): `.venv/bin/python tests/test_e2e_mock.py`

## Architecture
- `app/workflow.py` is the single source of truth: the stage machine (review → plan → build → memo →
  machining → release), approval gates (`open_gate` / `answer_gate`), revision limit, parallel sub-engineers.
  Each stage handler ends by either opening a gate (status `awaiting_user`) or advancing `p["stage"]`.
- `app/agents/*` build prompts and parse replies; `app/prompts.py` holds the system prompts. Keep the
  OpenClaw `openclaw/agents/*/AGENTS.md` files in sync when prompts change.
- `app/llm.py` backends: `openclaw` (gateway `/v1/chat/completions`, `x-openclaw-agent-id`,
  `x-openclaw-session-key`), `ollama` (native `/api/chat`, `format: json`), `mock` (`app/mock.py`).
  `auto` probes in that order. Every call is traced to `workspace/projects/<id>/trace.jsonl`.
- `app/cad/sandbox.py` screens code (AST allow-list) and runs `app/cad/runner.py` in a subprocess.
  Never exec model code in the server process.
- `app/cad/validate.py` = the engineering tests. `app/manufacturing/costing.py` = deterministic cost
  model over `data/*.json`; the machinist LLM only interprets it and must not invent prices.
- State is JSON on disk (`workspace/projects/<id>/state.json`), events go to the UI over SSE (`/api/events`).

## Rules to preserve
- Agents must never pass a gate without a user response. New stages need a gate kind in
  `answer_gate`, a renderer in `web/app.js → gateCard`, and a row in the cadflow SKILL.md table.
- Front end: any page reachable in ≤3 clicks (rail → job → tab). Don't add nested navigation.
- Front end is dependency-free at runtime: libraries live in `web/vendor/`. No build step.
- Sentence-case UI copy; buttons say exactly what happens ("Approve plan and build").
