# Traveler

Prompt → tested CAD → cheapest way to make it. One agentic workflow, three roles, built on **Gemma 4**
(released April 2, 2026) running through **OpenClaw** agents, with the user signing off at every gate.

```
You ──prompt, dimensions, tolerance, application, budget, work conditions──▶ Op 10 Intake
                                                                               │
Lead engineer (gemma4:26b)   Op 20 review: drawbacks + fixes ───────────▶ you approve fixes
                             Op 30 plan: split into sub-assemblies ─────▶ you approve plan
                                   │  summons N sub-engineers (gemma4:e4b), in parallel
Sub-engineers                Op 40 CadQuery per part → sandbox → tests → auto-retry (max 3)
                                   │  questions bubble up to you
Lead engineer                      assembles, interference check
                             Op 50 technical memo ──────────────────────▶ you sign hand-off
Machinist (gemma4:26b)       Op 60 every process × material, slicing, cost ▶ you pick strategy
                                   └── or sends it back to the engineer (max 3 revisions)
                             Op 70 release: STEP, STL, G-code, BOM, plan, memo, SHA-256 manifest
```

There is no designer stage. The engineer works directly from your request.

## Run it

```bash
./start.sh
```
That creates a Python env, installs CadQuery/FastAPI once, and opens http://127.0.0.1:8765.
With no model running it uses **offline demo agents** so the whole route still works (good for testing the UI).

### Use real Gemma 4

**Option A: OpenClaw (recommended)**
```bash
./openclaw/setup_openclaw.sh        # installs the 3 agent workspaces + cadflow skill, offers to pull Gemma
# (also merges the settings into ~/.openclaw/openclaw.json with a backup, and saves the token to .env)
openclaw gateway restart
./start.sh
```
Traveler calls `cad-lead-engineer`, `cad-sub-engineer` and `cad-machinist` through the gateway's
`/v1/chat/completions` endpoint (one session per job and sub-assembly). You can also chat with the lead
engineer on any OpenClaw channel; it runs jobs through Traveler with the `cadflow` skill and relays every approval.

**Option B: Ollama directly**
```bash
ollama pull gemma4:26b && ollama pull gemma4:e4b
./start.sh                          # auto-detects Ollama when no gateway is up
```

**Option C: any OpenAI-compatible API** (OpenRouter, Google AI Studio, LM Studio, vLLM, OpenAI, Groq…)
On **Dev**, set Backend to `openai`, fill in the base URL and API key, and type the provider's model names
into the three model fields. Or in `.env`:
```bash
CADFLOW_BACKEND=openai
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-...
ENGINEER_MODEL=<provider's model id>
SUB_ENGINEER_MODEL=<provider's model id>
MACHINIST_MODEL=<provider's model id>
```

Other local Ollama models work the same way: `ollama pull <model>` and put its name in the model fields.

The sidebar shows which backend is live. Change models, limits and keys on **Dev**.

### Optional
- **PrusaSlicer** installed → FDM parts are really sliced (print time, grams, G-code in the release).
- **Onshape API keys** on Dev → "Send to Onshape" pushes the assembly STEP into a new Onshape document.

## The front end
Every page is at most 3 clicks away: the left rail (1 click) reaches New job, Jobs, Approvals, Materials,
Processes, Agents and Dev; a job (2) opens its tabs Traveler, Model, Memo, Production, Files, Log (3).
Approvals collects every gate across jobs, so any decision is 2 clicks.

## Rules the workflow enforces
- No agent passes a checkpoint without your approval. Agents stop and ask when an answer would change the design.
- 3 revisions per job (plan changes, memo revisions and machinist send-backs share the count).
- 3 build attempts per part. Each failure's tests and traceback go back to the sub-engineer.
- Sub-engineers are summoned when the plan has 2+ parts, up to 4 in parallel.
- Model code is AST-screened (only `cadquery` and `math`) and runs in a separate process with a timeout.

## Data the machinist uses
`data/materials.json` (28 materials), `data/processes.json` (FDM, SLA, SLS, CNC, laser, injection molding),
`data/tolerances.json` (ISO 2768-1, ISO 286 fits, DFM rules). Edit them to match your shop.

## Layout
```
app/            FastAPI server, workflow state machine, agents, CAD sandbox, costing, slicer, Onshape
data/           materials / processes / tolerances datasets
web/            front end (vanilla JS, three.js viewer, vendored libs, works offline)
openclaw/       the three OpenClaw bots, cadflow skill, config fragment, setup script
tests/          end-to-end test (mock agents, real CadQuery)
workspace/      jobs, revisions and released files (created at runtime)
```

## Test
```bash
.venv/bin/python tests/test_e2e_mock.py
```
