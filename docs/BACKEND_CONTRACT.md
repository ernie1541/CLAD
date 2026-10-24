# Python backend contract

The frontend is intentionally transport-driven. It currently uses `src/lib/mockTransport.js`.
Set `VITE_USE_MOCK=false` to switch to `src/lib/apiClient.js`, which expects a Python API.

## 1. Create session

`POST /api/sessions`

```json
{
  "prompt": "Design an 80 mm sensor bracket..."
}
```

Response:

```json
{
  "session_id": "uuid"
}
```

## 2. Stream workflow events

`GET /api/sessions/{session_id}/events`

Use Server-Sent Events (`text/event-stream`). Each `data:` payload is one JSON event.

Supported events:

```json
{"type":"session.created","session_id":"uuid"}
{"type":"role.started","role":"designer","iteration":1}
{"type":"assistant.thinking","role":"designer","text":"Mapping constraints..."}
{"type":"assistant.message","role":"designer","text":"..."}
{"type":"question","role":"designer","id":"q1","prompt":"overall width (mm):","options":["..."]}
{"type":"role.completed","role":"designer"}
{"type":"engineer.split","reason":"...","parts":["module A","module B","module C"]}
{"type":"session.complete","summary":{"title":"Ready","status":"READY","parts":3,"manufacturing":"CNC"}}
{"type":"error","message":"..."}
```

## 3. Send user response

`POST /api/sessions/{session_id}/messages`

```json
{
  "text": "120 x 80 x 30 mm; hole spacing 64 mm exact"
}
```

Response can be `204 No Content` because the next work arrives through SSE.

## Suggested database tables

Keep persistence boring and backend-owned:

- `design_sessions(id, created_at, status, original_prompt, active_role)`
- `design_messages(id, session_id, role, kind, text, created_at)`
- `design_requirements(id, session_id, key, value_json, constraint_type, source_message_id)`
- `design_events(id, session_id, sequence, event_type, payload_json, created_at)`
- `design_artifacts(id, session_id, onshape_document_id, url, format, metadata_json)`

SQLite is enough for the hackathon. PostgreSQL can replace it later without changing the frontend API.
