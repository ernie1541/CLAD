---
name: cadflow
description: Run CAD fabrication jobs through the local Traveler workflow (lead engineer, sub-engineers, machinist) — create jobs, relay approval gates to the user, answer them with the user's decision, test CadQuery code, and get quotes.
---

# cadflow — drive Traveler from chat

Traveler runs at `${TRAVELER_URL:-http://127.0.0.1:8765}`. All calls are plain `curl` with JSON.
**Never answer an approval gate without the user's explicit decision in this conversation.**

## Create a job
```bash
curl -s -X POST "${TRAVELER_URL:-http://127.0.0.1:8765}/api/projects" -H 'Content-Type: application/json' -d '{
  "prompt": "Wall bracket for a 2 kg router, four M5 holes",
  "dimensions": "120 x 80 x 100 mm",
  "tolerance": "ISO 2768-m",
  "application": "garage wall mount",
  "budget": 150, "quantity": 2,
  "max_temp_c": 45, "load": "moderate",
  "environment": ["outdoor"],
  "processes_available": []
}'
```
`load` is `light|moderate|heavy`; `environment` items are `outdoor|water|chemical|food`;
`processes_available` items are `fdm|sla|sls|cnc_mill|laser_cut|injection_molding` (empty = any).
The reply contains `id`. The user can watch it at `${TRAVELER_URL}/#/job/<id>/traveler`.

## Check a job
```bash
curl -s "${TRAVELER_URL:-http://127.0.0.1:8765}/api/projects/<id>" | python3 -c "import sys,json; p=json.load(sys.stdin); print(p['stage'], p['status']); [print(json.dumps(g, indent=1)[:3000]) for g in p['gates'] if g['status']=='open']"
```
`status` is `running` (wait ~20 s and check again), `awaiting_user` (relay the open gate), or `done`.

## List everything waiting on the user
```bash
curl -s "${TRAVELER_URL:-http://127.0.0.1:8765}/api/approvals"
```

## Answer a gate (only with the user's decision)
`POST /api/projects/<id>/gates/<gate_id>` with one of:

| Gate `kind` | Approve body | Notes |
|---|---|---|
| `clarify`, `sub_question` | `{"action":"approve","answers":{"0":"...","1":"..."}}` | keys are question indexes |
| `review_fixes` | `{"action":"approve","accepted":["F1","F3"],"comments":"..."}` | list the fix ids the user accepts |
| `plan` | `{"action":"approve"}` | starts the sub-engineers |
| `build_failure` | `{"action":"approve","comments":"guidance for the sub-engineers"}` | retries failed parts |
| `memo` | `{"action":"approve"}` | hands off to the machinist |
| `production` | `{"action":"approve","strategy":"lowest_cost"}` | ids come from `manufacturing.study.strategies` |
| `error` | `{"action":"approve"}` | retries the failed operation |

Any gate also accepts `{"action":"revise","comments":"what to change"}` (max 3 revisions per job) or
`{"action":"abort"}`.

```bash
curl -s -X POST "${TRAVELER_URL:-http://127.0.0.1:8765}/api/projects/<id>/gates/<gate_id>" -H 'Content-Type: application/json' -d '{"action":"approve"}'
```

## Download the release
When `stage` is `released`, send the user `${TRAVELER_URL}/api/files/<id>/<release.zip>`.

## Test CadQuery code (sub-engineers)
```bash
curl -s -X POST "${TRAVELER_URL:-http://127.0.0.1:8765}/api/cad/run" -H 'Content-Type: application/json' \
  -d '{"code":"import cadquery as cq\nresult = cq.Workplane(\"XY\").box(40,20,5)","envelope_mm":[40,20,5],"tolerance_mm":0.3}'
```
Returns `ok`, the list of tests, metrics and any traceback.

## Quick quote (machinist)
```bash
curl -s -X POST "${TRAVELER_URL:-http://127.0.0.1:8765}/api/estimate" -H 'Content-Type: application/json' \
  -d '{"bbox_mm":[100,60,20],"volume_mm3":60000,"area_mm2":20000,"quantity":10,"tolerance":"ISO 2768-m"}'
```
