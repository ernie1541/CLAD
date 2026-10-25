# onshape-engineer

The **Engineer** stage of a Designer → Engineer → Machinist CAD pipeline. It runs on **Gemma in OpenClaw**
and builds in **Onshape through MCP**.

It is split into a **Lead Engineer** that thinks and **Worker Engineers** that build:

```
                 ┌──────────────── user (ask_user gates) ────────────────┐
                 │                                                       │
Designer ──brief+sketch──▶  engineer-lead  (gemma4:31b, main session)   ──handoff.json──▶ Machinist
                            review → plan → dispatch → integrate → memo
                                 │ sessions_spawn (≤K at a time, by dependency wave)
                 ┌───────────────┼───────────────┐
           engineer-worker  engineer-worker  engineer-worker   (gemma4:26b, sub-agents)
           1 work order each: own Part Studio, frozen contracts, report_part, submit_work_result
                 └──────── onshape MCP ──────────┘
```

Everything a small local model shouldn't be trusted with lives in code, in the **`engineer-tools` MCP server**:

| Enforced in code (`engineer-tools`) | Left to the model (`AGENTS.md`) |
|---|---|
| Phase order. Can't plan before Gate 1 or hand off before Gate 2 | Finding design drawbacks |
| Gates need the user's verbatim `ask_user` reply | Writing the plan, parameters, contracts |
| Max 3 iterations, then trade-offs only, and the user decides | Classifying feedback (engineering vs intent) |
| Plan validation: names, DB materials, contracts reference real parts/params, no dependency cycles | Driving the Onshape tools |
| **Parallel or not** (N parts / M independent subassemblies), dependency waves, K cap | Choosing critical sections for hand calcs |
| Work orders with every parameter pre-converted to inches + Onshape expressions | Memo prose |
| Workers can only report their own parts; built + failed must cover the order | |
| Onshape variables in every Part Studio checked against the frozen plan | |
| Stress checks only count if they cite a logged `hand_calc` id (no invented numbers) | |
| Mass from material DB density × Onshape volume | |
| Verification gate before the memo. Only the user can waive a check | |
| Machinist handoff JSON schema | |

## Install

```bash
# 1. Gemma via Ollama
ollama pull gemma4:31b && ollama pull gemma4:26b

# 2. This package (MCP server: `engineer-tools`)
pip install -e .            # Python ≥ 3.10; works with mcp 1.x and 2.x

# 3. Onshape MCP server (hedless/onshape-mcp)
git clone https://github.com/hedless/onshape-mcp && pip install -e ./onshape-mcp
export ONSHAPE_ACCESS_KEY=...  ONSHAPE_SECRET_KEY=...   # Onshape Developer Portal → API keys

# 4. OpenClaw config + agent prompts
#    Merge openclaw/openclaw.engineer.json5 into ~/.openclaw/openclaw.json, then:
cp openclaw/workspaces/engineer-lead/AGENTS.md   ~/.openclaw/workspace-engineer-lead/AGENTS.md
cp openclaw/workspaces/engineer-worker/AGENTS.md ~/.openclaw/workspace-engineer-worker/AGENTS.md
openclaw mcp doctor engineer --probe && openclaw mcp doctor onshape --probe
```

Check each agent's effective tool list with `/tools` in a session. Workers should **not** see
`onshape__create_assembly` or `engineer__submit_plan`, and the lead should **not** see `onshape__create_extrude`.

## Run

Open a session with `engineer-lead` and give it the Designer's output, e.g. `examples/brief_shelf_bracket.json`
plus `examples/sketch_shelf_bracket.json`. The lead calls `engineer__start_run` and follows `next_step` from
every tool result. You'll be asked to approve at:

1. **Gate 1:** the design review (approve / reject / modify each issue, answer questions)
2. **Design changes or waivers** mid-build, only if something would change intent or a check fails
3. **Gate 2:** the technical memo, before the handoff to the Machinist
4. **Revisions:** engineering-only Machinist feedback; at the iteration limit, the trade-off decision

Run state lives in `~/.openclaw/engineer-runs/<run_id>/`: `state.json`, `orders/*.json`, `events.jsonl` (audit
trail of every gate), `review_i*.md`, `plan_i*.json`, `memo_i*.md`, `handoff_i*.json`. A gateway restart
loses nothing. `engineer__run_status` / `get_run_context` re-orient the lead.

## Tuning

`engineer-tools` flags (set in the `mcp.servers.engineer.args` block):

| Flag | Default | Meaning |
|---|---|---|
| `--spawn-parts-over` | 6 | **N**: parallelize when unique parts > N |
| `--spawn-subassemblies-over` | 2 | **M**: …or independent subassemblies > M |
| `--max-workers` | 4 | **K**: max workers at once (keep `subagents.maxConcurrent` and `maxChildrenPerAgent` equal) |
| `--max-iterations` | 3 | Pipeline iteration limit |
| `--min-safety-factor` | 2.0 | On yield |
| `--min-wall-mm` | 1.0 | Minimum wall thickness check |
| `--materials-db` | bundled | JSON in the format of `src/engineer_tools/data/materials.json` |
| `--runs-dir` | `~/.openclaw/engineer-runs` | |

Under the thresholds, one worker builds everything. Over them, there is one worker per subassembly, and
dependent subassemblies wait for their wave. The plan's `depends_on` drives the waves, so put dependencies
only where geometry genuinely depends on another part.

## Handoff contract (Machinist input)

`handoff_i<N>.json` follows `HandoffPackage` in `src/engineer_tools/schemas.py`:
`parts[]` (material, quantity, Onshape element/part ids, mass, bbox, STEP export refs), `materials[]`,
`critical_tolerances[]` (part, feature, nominal, tolerance, GD&T), `quantities`, `open_items[]`, the memo and
plan paths, and the full `verification` report. Build the Machinist agent against this schema. Machinist
feedback comes back in through `engineer__submit_feedback` (`MachinistFeedback`).

## Things to verify on your first real run

These depend on Onshape behaviour I could not test without your account:

- **Units:** hedless/onshape-mcp takes **inches** for every numeric argument. Work orders carry `value_in`
  and `convert` returns inches, but watch the first parts' bounding boxes. `report_part` warns on tiny volumes.
- **Part Studio variables:** workers `set_variable` on their own Part Studio and reference `#name` through the
  `variable*` arguments (the MCP's documented workflow). The lead then runs `verify_variables` per Part Studio.
- **The measurement FeatureScript** in the worker `AGENTS.md` (evVolume + evBox3d per body). If your Onshape
  version rejects it, use the fallback path described there.
- **Interference** in hedless/onshape-mcp is bounding-box based: no overlap is trustworthy, but a reported
  overlap may be a false positive.
- **Gemma tool-calling stamina:** the low-level sketch/feature tools need face and edge ids. Expect some rework
  loops. If workers struggle, raise `num_ctx`, move workers to `gemma4:31b`, or narrow the Onshape catalog with
  `toolFilter`.
- **Material DB:** the bundled values are typical handbook values. Verify them or swap in your own DB.

## Development

```bash
pip install -e ".[dev]" && pytest
```

`tests/test_pipeline.py` drives a full run through the engine, simulating the lead and workers: the gates,
parallel waves, an interface conflict and rework, variable mismatches, verification, the memo, the handoff,
an engineering-only revision, routing to the Designer, and the iteration limit. `tests/test_mcp_stdio.py`
talks to the real server process over stdio, the way OpenClaw does.
