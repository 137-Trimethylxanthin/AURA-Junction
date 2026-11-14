# Chat contract (frontend ↔ backend)

This is the interface the chat frontend relies on. It is meant to stay stable: later backend work (indexing,
fast path, other models) changes how answers are produced, not this shape. Additive changes only (new optional
fields, new event types) — a frontend should ignore fields and event types it does not know.

## Auth
Every request except `GET /` (the reference page) and `GET /health` needs the token configured as
`JUNCTION_API_TOKEN`:
- header `Authorization: Bearer <token>`, or
- query `?token=<token>` (for `<img src>` and `EventSource`, which cannot set headers).

Wrong or missing token → `401 {"detail": "missing or wrong token"}`.

## 1. Upload or delete a file
`POST /files` — multipart form, field `file`. Accepted: `.ifc .dxf .pdf .png .jpg .jpeg .svg`, up to
`JUNCTION_MAX_UPLOAD_MB` (default 500).

```json
200 {"file_id": "e092494125b25785", "name": "site897_floor.dxf", "ext": "dxf", "size": 208457}
```
Errors: `422` (extension / empty file), `413` (too large).

`GET /files/{file_id}/raw` — the original bytes (always served as a download; build a blob URL for previews).

`DELETE /files/{file_id}` — removes the stored/registered file entry (`200 {"deleted": "<file_id>"}`, `404` if unknown).

## 2. Ask a question
`POST /runs` — starts a background run, returns at once.
```json
request  {"file_ids": ["e092494125b25785"], "questions": ["What is the area of apartment B?"]}
response 202 {"run_id": "7f0d2efea9313906", "status": "queued"}
```
- `questions`: 1–100; all are answered in the same run (one answer per question, same order).
- `file_ids`: 1–10; the agent sees all of them.
- Runs take seconds (demo) to minutes (real model). Each run is independent: there is no conversation memory
  yet. To give follow-up context, the frontend can fold earlier Q&A into the question text for now.

`POST /ask` — same body; blocks until the run ends and returns the run (below). With `"stream": true` it
returns the event stream directly instead.

`POST /runs/{run_id}/cancel` — cancels an active or queued run (`200 {"id": "<run_id>", "status": "cancelled" | run.status}`, `404` if unknown).

## 3. Follow the run live
`GET /runs/{run_id}/events` — Server-Sent Events. Each event:
```
id: 3
event: tool_finished
data: {"seq": 3, "t": 7.91, "type": "tool_finished", ...}
```
`seq` is 1, 2, 3 … per run; `t` is seconds since the run started. A late subscriber first receives the backlog,
then live events; the stream ends after `run_finished`. Resume with `?after=<seq>` or the `Last-Event-ID` header.

| type | fields | meaning |
|---|---|---|
| `run_started` | `run_id, model, files[], questions[]` | the agent starts |
| `tool_started` | `call_id, tool` + `command` (shell) or `path` (view_image) or `arguments` (other) | a tool call began |
| `tool_finished` | `call_id, tool, exit_code, timed_out, ended, seconds` + `output` (shell, head+tail clipped) or `path, view, width, height` (view_image) | it ended; pair with `tool_started` by `call_id` — calls can overlap |
| `message` | `text` | the model wrote text between tool calls |
| `reasoning` | `text` | a reasoning summary, when the model provides one |
| `answers` | `answers[]` (see below) | final answers |
| `run_finished` | `status` (`done` \| `error`), `seconds, usage, error` | always the last event |

`view` in a view_image event is a URL path (`/runs/{id}/views/{n}.png`) to the image exactly as the model saw it.

## 4. Get the run
`GET /runs/{run_id}`
```json
{"id": "...", "status": "queued|running|done|error", "model": "gemini-4-carbon",
 "files": [{"file_id": "...", "name": "drawing.dxf", "original_name": "site897_floor.dxf"}],
 "questions": ["..."], "answers": [ ... ], "usage": {"requests": 16, "input_tokens": 355492, "output_tokens": 6772,
 "total_tokens": 366377}, "error": null, "created": 1794650000.0, "seconds": 263.3, "events": [ ... ]}
```

## Answer object
```json
{"n": 1,
 "answer": 38.45,                 // number (counts, quantities), string (open questions) or null (file cannot answer)
 "unit": "m2",                    // "count", "m2", "m", "m3", ... or null
 "method": "Summed the four room outlines inside Wohnung B ...",
 "check": "Cross-checked with ...", // or "none"
 "confidence": 0.95,              // 0..1
 "evidence_total": 4,             // how many items the answer rests on (evidence may be truncated)
 "evidence": [ ... ]}
```
Evidence items have a `kind` and only the keys that apply (nulls are dropped):

| kind | keys | how to show it |
|---|---|---|
| `image` | `bbox_px [x0,y0,x1,y1]`, `image` (file name in the run workspace, e.g. `drawing.png`), `label`, `text` | draw the box on that image: `GET /runs/{id}/artifacts/{image}` — pixel coordinates of the full image |
| `cad` | `handle`, `layer`, `bbox [xmin,ymin,xmax,ymax]` (drawing units), `label` | CAD entity reference; highlight if you render the DXF |
| `ifc` | `guid`, `ifc_class`, `name` | IFC element reference (GlobalId) |
| `text` | `text`, `label`, sometimes `handle`/`bbox` | a label or text the answer was read from |
| any | `note`, `file`, `group`, `group_label`, `lead_to_px`, `lead_to_cad`, `lead_to_label`, `target_file` | free-form detail, multi-plan source/target file names, categorical color group id & legend label, and directional destination vector |

Uploaded files appear in the run workspace as `drawing.<ext>` (`drawing_2.<ext>` …); evidence refers to those names.

## Other endpoints
- `GET /runs/{id}/artifacts` — files the agent wrote (renders, crops); `GET /runs/{id}/artifacts/{name}` — one file
  (images inline, everything else as download).
- `GET /health` — `{"status": "ok"}` without token; with token also the model, sandbox and run counters.

## Demo mode (no API key needed)
Start the backend with `JUNCTION_MODEL=demo`: every run goes through the real API and event stream with a scripted
model (shell call → image view → one answer per question with an `image` evidence box at `[100,100,400,300]`),
finishing in about a second. Use it to build and test the frontend.
