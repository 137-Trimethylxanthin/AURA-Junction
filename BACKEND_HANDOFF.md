# What a backend must implement to work with the AURA frontend

The source of truth is `frontend/src/api/contract.ts` and `frontend/src/api/contract.md`. `backend/aura/contract.py` mirrors it and is a working reference implementation. This file is a short checklist; where it differs from the contract files, the contract files win.

Every response carries `contract_version: "1.1"`. Absent optional fields are omitted, never sent as `null` (one exception below).

## Endpoints the frontend calls

| Endpoint | Returns |
|---|---|
| `GET /health` | `{ok, contract_version, checks{…}}`; 503 when not ok |
| `GET /drawings` | Library summaries. Never lists uploads. |
| `GET /drawings/{id}` | The drawing JSON; 409 with code `processing` until ready |
| `GET /drawings/{id}/status` | queued, processing, ready or failed |
| `GET /drawings/{id}/{file}` | `plan.svg`, `thumb.png`, `original.png` only. Uploads: `private, no-store`. |
| `POST /ask` | `{drawing_id, question, scope?, overrides?, lang_hint?}` → AskResponse |
| `POST /chat` | Server-sent events, see below |
| `GET /conversations/{id}` | Stored turns, for restoring after a reload |
| `GET /answers/{id}/evidence.csv` | Evidence rows; cells that look like formulas are escaped |
| `POST /rewrite` | `{drawing_id, text}` → `{blocks, not_answerable, detected_lang, calls, cost_usd, tokens, model}` |
| `POST /uploads` | Multipart field `file` → 202 `{drawing_id, delete_token, status: "queued"}` |
| `DELETE /drawings/{id}` | Header `x-delete-token` → 204 |

## Chat stream events, in order

`turn{conversation_id, turn_id}` → `understood{detected_lang, canonical_en, route}` → zero or more `tool_call{node}` and `tool_result{node_id, evidence, summary}` → optional `check{check}` → `answer{response}` or `error{error, code, calls?, cost_usd?}` → `done{}`.

A turn that runs past its deadline sends `error` with code `timeout`, then `done`.

## Errors

Body: `{contract_version, error, code}`. Codes: `bad_request`, `unknown_drawing`, `not_found` (404), `processing` (409), `too_large`, `unsupported_type`, `rate_limited` and `budget_exhausted` (429), `presenter_required`, `timeout` (504), `internal`. A 504 carries `calls` and `cost_usd` for requests already sent.

Only network failures and 5xx make the frontend fall back to bundled local data, and only for the bundled drawing. Every 4xx is shown as an error.

## Rules the frontend relies on

- **Coordinates:** every box and polygon point is a percentage (0–100) of the served drawing image, origin top-left. Non-square plans keep separate x and y scales.
- **Evidence ids** (`win-01`, `door-01`, `basin-01`, `room-<id>` …) are stable across calls and rebuilds, in reading order. An object drawn twice carries `duplicate_of` on the later copy.
- **Unknown areas:** `area_m2` is `number | null`. `null` means no printed area and no outline; it comes with `included: false` and is never summed. It is the only `null` allowed. Never send 0 for unknown.
- **Ids:** the server issues conversation ids; an unknown one sent by the client gets a new id in `turn`. `answer_id` is fresh on every answer, including cache hits. Upload ids and delete tokens are unguessable and uploads are never listed anywhere.
- **Answers:** kinds `count`, `area`, `quantity` (m, m2, m3), `text`, `abstain` (`not_on_drawing`, `not_supported_yet`, `unclear_question`, `low_confidence`). Scopes `floor`, `model`, `unit`, `room`, `label`, `region`.
- **Method honesty:** `method: "cad_geometry"` only for answers computed from CAD data. Model-read answers use `vision` and an `estimate{confidence, probabilities?, measured?}` block.
- **Cost honesty:** `calls` and `cost_usd` count every model request that was sent for the answer, including failed ones. `check.calls`, `check.cost_usd`, `check.tokens` and `check.cached` do the same for the check.
- **Numbers:** values come from code or tool results, never from model prose. `explanation` may only contain numbers a tool produced.
- **Dataset match (`gold`)** only when the question's scope equals a dataset question's.

## How to check a backend against the frontend

1. `frontend/src/api/conformance.fixtures.json` holds question → expected answer cases for the bundled drawing `swiss-46639`. `backend/tests/test_contract.py` shows how the reference backend runs them.
2. Point the frontend at the backend with `VITE_API_URL` and run `npm run e2e:api` in `frontend/`.
