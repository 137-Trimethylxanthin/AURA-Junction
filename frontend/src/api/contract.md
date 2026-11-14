# AURA API contract, version 1.1

AURA stands for Automated Understanding of Rooms & Areas.

This file is for the backend team. The source of truth is `contract.ts`, which sits next to this file. If the two ever disagree, `contract.ts` wins; please tell the frontend team when that happens.

**Versioning.** Every body carries `contract_version`. The current version is `"1.1"` (see §1.1); `"1.0"` bodies stay valid.
- The frontend rejects any body whose **major** version differs from its own. It shows an error card and does not fall back.
- A minor bump, such as `1.1`, may only add optional fields.

## Endpoints

| Method and path | Response | Notes |
|---|---|---|
| `GET /health` | `{ "ok": true, "contract_version": "1.0" }` | Called once when the app mounts, with a **10 s** timeout to allow for a cold start. A failure or timeout makes the session use local answers. |
| `GET /drawings/:id` | `Drawing` | For `swiss-46639` the frontend always uses its bundled copy. Other ids are rendered from this response. |
| `POST /ask` | `AskResponse`, or `ErrorBody` with a 4xx status | JSON body `AskRequest`. The frontend aborts after **15 s**. |

**CORS.** Allow the Cloudflare Pages origin (`https://<project>.pages.dev`) and its preview subdomains, for `GET`, `POST` and the `content-type` header.

### Timeouts and fallback

**Timeouts.**
- Aim to answer `/ask` well under 10 s.
- After **15 s** the frontend aborts the request and ignores any response that arrives later.
- If you can't answer in time, return an abstain rather than keep the client waiting.

**What each failure does on the bundled drawing (`swiss-46639`).**

| What happens | Effect in the frontend |
|---|---|
| Network error, a **5xx** status, or a timeout | Switches the whole session to its local answers, and says so with a source badge and a toast. Sessions never mix sources. |
| A **4xx** status (`ErrorBody`, or FastAPI's default `{ "detail": [...] }` from a 422) | Shows an error card. It never falls back. |
| A body that fails validation | Treated as an error and never falls back. |

For any other drawing id, every failure shows an error card.

## Obligations on the backend

1. **`Pct`** is a percentage (0–100) of the width or height of `Drawing.images.rebuilt` as you serve it. The origin is top-left and y points down.
   - For `swiss-46639` that image is the 1221×1220 px render of the DXF extents plus a 0.6 m margin on every side, with y flipped.
   - `Drawing.frame` holds the numbers needed to reproduce it: extents x [-20.166657, 9.146366] and y [-16.868711, 12.430481]. That frame is 30.513023 × 30.499193 m.
2. **The canonical drawing file** `frontend/src/data/drawing.swiss-46639.json` is shared. Serve it unchanged, or regenerate it byte-equivalent (`node frontend/scripts/build-drawing.mjs`). Besides the `Drawing` fields it carries two extra sections, which clients ignore:
   - `evidence`: the boxes for each subject
   - `gold`: the dataset answers
3. **Evidence ids** are `win-01…`, `door-01…`, `basin-01…`, `wc-01…` and `room-<room_id>`.
   - They are stable across calls.
   - They are numbered in reading order: top to bottom by the box's top edge, then left to right.
   - Two pairs of door boxes have identical coordinates: the same door is drawn twice at the same spot in the CAD file, and the dataset counts both. Both boxes carry the `note` "Drawn twice at the same spot in the CAD file; the dataset counts both."
4. **`room_id`** is the only key used for rooms: in polygons, scopes, overrides and evidence. For `swiss-46639` it equals the room number, e.g. `"3.04"`.
5. **Honesty rules.**
   - Never return a number you did not derive.
   - If a model did not run, `check.status` is `"sample"`, with no `agreed`, `total` or `per_box`, and trace `confidence` is `"sample"` or absent.
   - Report `calls` and `cost_usd` truthfully.
6. **`gold`** is set only when the answered scope equals a dataset question's scope and there are no `overrides`. It is never set for a `region` scope.
   - `tolerance` is `"exact"` for counts and `"2pct"` for areas.
   - `match` is computed with that tolerance.
   - `dataset_qid` is `"questions.jsonl:<line>"`: the FR line for French questions, the EN line otherwise.
7. **Omit optional fields; never send `null`.** With FastAPI use `response_model_exclude_none=True`. The frontend strips `null`s defensively, but the contract is "absent".
8. **Trace `evidence_ids`** must refer to evidence in the same response (`evidence.boxes[].id` or `room-<room_id>` of `evidence.rooms`). The UI can only light what the response carries.

## Types

### Shared

| Type | Meaning |
|---|---|
| `Lang` | `"en"`, `"fr"`, `"fi"` or `"de"` |
| `Pct` | A number from 0 to 100 (see obligation 1) |
| `RoomId` | An opaque string |
| `Subject` | `"window"`, `"door"`, `"washbasin"`, `"wc"`, `"bathtub"`, `"shower"`, `"room"` or `"apartment"` |

### Drawing (`GET /drawings/:id`)

| Field | Meaning |
|---|---|
| `contract_version` | `"1.0"` |
| `id` | Drawing id, e.g. `"swiss-46639"` |
| `title` | e.g. `"Plan 3e étage"` |
| `subtitle?` | *Optional, an addition to the plan.* e.g. `"Site 11420 · 1:200"` |
| `frame.dxf_min`, `frame.dxf_max` | DXF extents in DXF units |
| `frame.margin_m` | Margin around the extents in the render (0.6) |
| `frame.m_per_unit` | Metres per DXF unit (1) |
| `frame.y_flip` | Always `true` |
| `frame.px` | Render size `[1221, 1220]` |
| `m_per_pct_x` | Metres per 1 % of image width (0.30513). *An addition to the plan.* |
| `m_per_pct_y` | Metres per 1 % of image height (0.30499) |
| `images.rebuilt` | URL of the image the `Pct` values refer to |
| `images.original?` | URL of the original scan, for the compare view |
| `images.original_alignment?` | `"exact"` or `"approximate"`. `swiss-46639` is `"approximate"`: the crop is fitted to its bounding box. |
| `rooms[]` | `RoomPoly`, below |
| `units[]` | `{ id, label }`, e.g. `{ "id": "A", "label": "Apartment A" }` |
| `credit` | `{ text, license, url? }`. Must stay on screen (CC BY 4.0). |

**`RoomPoly`**

| Field | Meaning |
|---|---|
| `room_id` | The room key |
| `num` | The number as printed |
| `name` | The label as printed (French on this drawing, e.g. `"Chambre à coucher"`) |
| `unit?` | Apartment letter |
| `area_m2` | Printed net area |
| `outdoor` | `true` for a terrace or balcony |
| `points` | The polygon as `[Pct, Pct][]`. It is closed implicitly and has no repeated points. |
| `label_at` | Where the label sits |
| `rect?` | `{ x, y, w, h, w_m, h_m }`, present only for rooms that can be resized in a what-if (14 rooms on `swiss-46639`) |

### The shared drawing file (extra sections)

`drawing.swiss-46639.json` is a `Drawing` plus:

| Field | Meaning |
|---|---|
| `evidence.window`, `evidence.door`, `evidence.washbasin` | `Box[]` with final ids, in reading order |
| `gold[]` | `{ dataset_qid, dataset_qid_fr, question_en, answer, scope, value, tolerance }`. `dataset_qid` is the EN line and `dataset_qid_fr` the FR line of the same question. `answer` is `{ kind: "count", subject }` or `{ kind: "area" }`. `question_en` is **our paraphrase**, not the dataset text (the dataset is not redistributed). |

### AskRequest (`POST /ask` body)

| Field | Meaning |
|---|---|
| `drawing_id` | Which drawing |
| `question` | The user's text, in any of the 4 languages |
| `scope?` | A scope set explicitly by the UI, such as a marked region. If present, it wins over any scope in the text. |
| `overrides?` | `[{ room_id, w_m, h_m }]`: what-if sizes. A room's area becomes `round(w_m × h_m, 2)` and its evidence row gets `edited: true`. |
| `lang_hint?` | The language of the previous answer, for questions too short to detect |

**`Scope`** is one of:

| Value | Meaning |
|---|---|
| `{ kind: "floor" }` | The whole drawing |
| `{ kind: "unit", unit: "A" }` | One apartment |
| `{ kind: "room", room_id: "3.04" }` | One room |
| `{ kind: "label", label: "Chambre à coucher" }` | All rooms with that printed label |
| `{ kind: "region", rect: {x,y,w,h}, label: "Area 1" }` | A box marked by the user. A box counts if its **centre** is inside the rect; a room counts if its `label_at` is inside it. |

### AskResponse

| Field | Meaning |
|---|---|
| `contract_version` | `"1.0"` |
| `answer` | One of the three kinds below |
| `evidence.boxes[]` | `Box`: `{ id, x, y, w, h, subject, room_id?, unit?, note? }`. For a count of fixtures these are exactly the boxes counted. |
| `evidence.rooms[]` | `{ room_id, included, area_m2, edited? }`. For areas, list **every** room in the scope and mark outdoor ones `included: false`, so the user can add a terrace back in. For label counts and apartment counts, list the rooms as lit polygons. |
| `detected_lang` | The language of the question. The answer card copy follows it. |
| `canonical_en` | The question as understood, in plain English, e.g. `"How many windows are drawn in apartment A"`. Shown under "Understood as". |
| `scope` | The scope actually answered |
| `method` | `"cad_geometry"`, `"ocr"`, `"detector"`, `"vision"` or `"none"` (abstain) |
| `check?` | `{ method: "decisions_api", status: "sample" or "live", agreed?, total?, per_box? }`. The numbers are only allowed when `status` is `"live"`. |
| `gold?` | `{ dataset_qid, value, tolerance, match }` (obligation 6) |
| `trace[]` | `TraceNode`: `{ id, parent?, title, tag, body, kind, evidence_ids?, confidence? }`. `kind` is `"exact"`, `"model"`, `"warn"` or `"info"`. Nodes form a tree via `parent`. Hovering a node lights its `evidence_ids`. A numeric `confidence` is a probability from 0 to 1 (0.93, not 93); the same scale applies to `check.per_box` values. |
| `calls` | Model calls made for this answer |
| `cost_usd` | What this answer cost |
| `latency_ms` | Server time spent |
| `source` | `"api"` from the backend, `"local"` from the in-browser adapter. *An addition to the plan.* |

**The three kinds of `answer`:**

| Kind | Fields |
|---|---|
| `count` | `{ kind: "count", value, subject }` |
| `area` | `{ kind: "area", value, unit: "m2" }`. `value` is the sum of the included rooms, rounded to 2 decimals. |
| `abstain` | `{ kind: "abstain", reason, where_to_find? }` |

**Abstain reasons.** The UI never shows a number for an abstain.

| `reason` | When to use it |
|---|---|
| `not_on_drawing` | The drawing cannot answer this kind of question, such as a fire rating, a height or a material. Put a short hint in `where_to_find`, written in the question's language. |
| `not_supported_yet` | The drawing has the information but we don't extract it yet (e.g. WC, bathtub or shower on a drawing whose file has no such layer). The bundled swiss-46639 now answers all three. |
| `unclear_question` | Nothing in the question can be matched, or it names a room or apartment that doesn't exist. |

**How the UI derives the number it displays.** It recomputes the value from the evidence and the user's toggles, so the evidence must add up to `answer.value`.

| Answer | Displayed value |
|---|---|
| Count of window, door, washbasin and the other fixtures | Boxes the user has not rejected |
| Count of rooms | Rooms with `included: true` |
| Count of apartments | Distinct `unit` values among the included rooms |
| Area | Sum of `area_m2` over the included rooms, rounded to 2 decimals |

### ErrorBody (with a 4xx status)

```json
{ "contract_version": "1.0", "error": "Unknown drawing 'swiss-1'", "code": "unknown_drawing" }
```

`code` is `"bad_request"`, `"unknown_drawing"` or `"internal"`. Use a 5xx status for real server faults: on the bundled drawing, a 5xx makes the frontend fall back to local answers.

## Examples

These were produced by the local adapter (`local.ts`), apart from the error example. Your responses should have the same shape, with `source: "api"`.

### Count, with a region scope

Request:

```json
{ "drawing_id": "swiss-46639", "question": "Combien de lavabos ici ?",
  "scope": { "kind": "region", "rect": { "x": 47.4, "y": 39.2, "w": 13.6, "h": 15.2 }, "label": "Area 1" } }
```

Response:

```json
{
  "contract_version": "1.0",
  "answer": { "kind": "count", "value": 3, "subject": "washbasin" },
  "evidence": {
    "boxes": [
      { "id": "basin-01", "x": 48.57, "y": 41.8, "w": 3.93, "h": 1.97, "subject": "washbasin", "room_id": "3.06", "unit": "A" },
      { "id": "basin-02", "x": 53.94, "y": 44.74, "w": 5.57, "h": 1.97, "subject": "washbasin", "room_id": "3.09", "unit": "A" },
      { "id": "basin-03", "x": 55.64, "y": 50.67, "w": 2.62, "h": 2.3, "subject": "washbasin", "room_id": "3.12", "unit": "A" }
    ],
    "rooms": []
  },
  "detected_lang": "fr",
  "canonical_en": "How many washbasins are drawn inside Area 1",
  "scope": { "kind": "region", "rect": { "x": 47.4, "y": 39.2, "w": 13.6, "h": 15.2 }, "label": "Area 1" },
  "method": "cad_geometry",
  "check": { "method": "decisions_api", "status": "sample" },
  "trace": [
    { "id": "q", "title": "Question", "tag": "French · detected", "body": "“Combien de lavabos ici ?”", "kind": "info" },
    { "id": "intent", "parent": "q", "title": "Intent", "tag": "local router", "body": "count · washbasins · Area 1", "kind": "info" },
    { "id": "source", "parent": "q", "title": "Pick the best source", "tag": "router",
      "body": "The CAD geometry bundled with this drawing answers counts and areas exactly, so no image model is needed.", "kind": "exact" },
    { "id": "read", "parent": "source", "title": "Read the layer", "tag": "DXF · washbasin layer",
      "body": "5 washbasins on the DXF washbasin layer.", "kind": "exact",
      "evidence_ids": ["basin-01", "basin-02", "basin-03"] },
    { "id": "scope", "parent": "source", "title": "Apply the scope", "tag": "geometry",
      "body": "Kept the ones whose centre is inside Area 1 → 3.", "kind": "exact", "evidence_ids": ["basin-01", "basin-02", "basin-03"] },
    { "id": "check", "parent": "q", "title": "Cross-check on the image", "tag": "Decisions API · sample",
      "body": "Sample only: the image cross-check runs once the Decisions API is wired. No model has checked this answer.",
      "kind": "model", "confidence": "sample", "evidence_ids": ["basin-01", "basin-02", "basin-03"] },
    { "id": "answer", "parent": "q", "title": "Answer", "tag": "count",
      "body": "3 washbasins. Each one is drawn on the plan for you to check.", "kind": "exact",
      "evidence_ids": ["basin-01", "basin-02", "basin-03"] }
  ],
  "calls": 0,
  "cost_usd": 0,
  "latency_ms": 3,
  "source": "local"
}
```

There is no `gold` because the scope is a region. "How many washbasins are drawn on this floor plan?" would return 5 boxes with `"gold": { "dataset_qid": "questions.jsonl:103429", "value": 5, "tolerance": "exact", "match": true }`.

### Area, with gold

Request:

```json
{ "drawing_id": "swiss-46639", "question": "What is the net floor area of apartment B?" }
```

Response:

```json
{
  "contract_version": "1.0",
  "answer": { "kind": "area", "value": 90.55, "unit": "m2" },
  "evidence": {
    "boxes": [],
    "rooms": [
      { "room_id": "3.14", "included": false, "area_m2": 25.53 },
      { "room_id": "3.15", "included": true, "area_m2": 8.86 },
      { "room_id": "3.16", "included": true, "area_m2": 13.55 },
      { "room_id": "3.17", "included": true, "area_m2": 31.11 },
      { "room_id": "3.18", "included": true, "area_m2": 4.59 },
      { "room_id": "3.19", "included": true, "area_m2": 16.33 },
      { "room_id": "3.20", "included": true, "area_m2": 5.6 },
      { "room_id": "3.21", "included": true, "area_m2": 10.51 },
      { "room_id": "3.22", "included": false, "area_m2": 56.97 }
    ]
  },
  "detected_lang": "en",
  "canonical_en": "What is the net floor area of apartment B",
  "scope": { "kind": "unit", "unit": "B" },
  "method": "cad_geometry",
  "check": { "method": "decisions_api", "status": "sample" },
  "gold": { "dataset_qid": "questions.jsonl:103439", "value": 90.54, "tolerance": "2pct", "match": true },
  "trace": [
    { "id": "q", "title": "Question", "tag": "English · detected", "body": "“What is the net floor area of apartment B?”", "kind": "info" },
    { "id": "intent", "parent": "q", "title": "Intent", "tag": "local router", "body": "area · net · apartment B · without outdoor", "kind": "info" },
    { "id": "source", "parent": "q", "title": "Pick the best source", "tag": "router",
      "body": "The CAD geometry bundled with this drawing answers counts and areas exactly, so no image model is needed.", "kind": "exact" },
    { "id": "collect", "parent": "source", "title": "Collect rooms", "tag": "room labels · printed areas",
      "body": "9 numbered rooms in apartment B; 2 outdoor left out.", "kind": "exact",
      "evidence_ids": ["room-3.14", "room-3.15", "room-3.16", "room-3.17", "room-3.18", "room-3.19", "room-3.20", "room-3.21", "room-3.22"] },
    { "id": "check", "parent": "q", "title": "Cross-check on the image", "tag": "Decisions API · sample",
      "body": "Sample only: the image cross-check runs once the Decisions API is wired. No model has checked this answer.",
      "kind": "model", "confidence": "sample",
      "evidence_ids": ["room-3.15", "room-3.16", "room-3.17", "room-3.18", "room-3.19", "room-3.20", "room-3.21"] },
    { "id": "answer", "parent": "q", "title": "Answer", "tag": "sum",
      "body": "Sum of 7 net room areas = 90.55 m², shown room by room.", "kind": "exact",
      "evidence_ids": ["room-3.15", "room-3.16", "room-3.17", "room-3.18", "room-3.19", "room-3.20", "room-3.21"] }
  ],
  "calls": 0,
  "cost_usd": 0,
  "latency_ms": 3,
  "source": "local"
}
```

**What-if.** Add `"overrides": [{ "room_id": "3.04", "w_m": 4.135, "h_m": 3.6 }]` to "What is the net floor area of apartment A?".
- Room 3.04 becomes `{ "room_id": "3.04", "included": true, "area_m2": 14.89, "edited": true }`.
- The value becomes 137.19.
- `gold` is omitted.

### Abstain

Request:

```json
{ "drawing_id": "swiss-46639", "question": "What is the fire rating of the entrance door?" }
```

Response:

```json
{
  "contract_version": "1.0",
  "answer": { "kind": "abstain", "reason": "not_on_drawing", "where_to_find": "A door schedule or the IFC model would have it." },
  "evidence": { "boxes": [], "rooms": [] },
  "detected_lang": "en",
  "canonical_en": "What is the fire rating",
  "scope": { "kind": "floor" },
  "method": "none",
  "trace": [
    { "id": "q", "title": "Question", "tag": "English · detected", "body": "“What is the fire rating of the entrance door?”", "kind": "info" },
    { "id": "intent", "parent": "q", "title": "Intent", "tag": "local router", "body": "abstain · not on drawing", "kind": "info" },
    { "id": "look", "parent": "q", "title": "Look for the data", "tag": "drawing",
      "body": "No layer or label on this drawing carries a fire rating.", "kind": "warn" },
    { "id": "answer", "parent": "q", "title": "Answer", "tag": "abstain", "body": "Abstained. Nothing guessed.", "kind": "warn" }
  ],
  "calls": 0,
  "cost_usd": 0,
  "latency_ms": 3,
  "source": "local"
}
```

### Error

The request names a drawing the backend doesn't know:

```json
{ "drawing_id": "swiss-1", "question": "How many windows?" }
```

Response, with status `404`:

```json
{ "contract_version": "1.0", "error": "Unknown drawing 'swiss-1'", "code": "unknown_drawing" }
```

## 1.1 (additive)

Everything in 1.1 is **optional or new**. A 1.0 body still validates, and the frontend accepts any `1.x` body. `/ask` keeps its 1.0 shape. Rules 5 and 7 above apply to every new field: omit, never `null`; never a number you did not derive.

### New optional fields on `Drawing` (`GET /drawings/:id`)

| Field | Type | Meaning |
|---|---|---|
| `kind` | `"cad_dxf" \| "raster" \| "ifc"` | How the drawing was read. |
| `status` | `"queued" \| "processing" \| "ready" \| "failed"` | Absent means ready. |
| `progress` | number 0–1 | While `processing`. |
| `error` | string | Human-readable reason, when `failed`. |
| `capabilities` | `Subject[]` | Subjects this drawing can answer. Drives suggestions and context menus. |
| `source` | `"library" \| "upload"` | |
| `thumbnail` | string (URL) | Small preview image. |
| `created_at`, `expires_at` | ISO 8601 strings | Uploads expire after 24 h. |
| `facts` | `{ gold_total, correct, measured_at }` | **Library only**, from the eval. Never estimated. |

`GET /drawings/:id` returns a **full** `Drawing` only once the drawing is `ready`. Before that it answers `409` with `ErrorBody` code `processing`; a `failed` drawing answers the same `409` `processing` (the client reads the reason from `GET /drawings/:id/status`). The frontend polls `GET /drawings/:id/status` until `ready` or `failed`, then fetches the full drawing once.

### New endpoints

| Method and path | Request | Response | Notes |
|---|---|---|---|
| `GET /drawings` | | `{ contract_version, drawings: DrawingSummary[] }` | Library first, then live uploads. |
| `GET /drawings/:id/status` | | `{ contract_version, id, status, progress?, error? }` | Polled every ~1.5 s by the frontend until `ready` or `failed`. |
| `POST /uploads` | `multipart/form-data`, field `file`; optional header `X-AURA-Presenter` | `UploadResult`, status `202` | DXF for everyone; PNG/JPG/PDF need the presenter token (`presenter_required`, 403). |
| `DELETE /drawings/:id` | header `X-Delete-Token` | `204` | Only uploads. Wrong token: 403 `bad_request`. |
| `POST /chat` | `ChatRequest` (JSON) | `text/event-stream` | Events below. Read with a `fetch` reader (not `EventSource`). 60 s turn deadline. |
| `GET /conversations/:id` | | `{ contract_version, id, drawing_id, turns: [{ turn_id, message, response?, error? }] }` | Restores a chat after a reload; the id is kept in localStorage. |
| `GET /answers/:id/evidence.csv` | | `text/csv` | Evidence rows of one answer. |
| `GET /budget` | | `{ contract_version, models: "on"\|"off", event_used, event_cap, day_used, day_cap }` | `models: "off"` shows the "geometry only" badge. |
| `GET /stats` | | `{ contract_version, answers_today, model_calls_today, geometry_only_share?: {value, n}, median_cost_per_checked_answer_usd?: {value, n} }` | Public counters, each ratio with its n. |
| `POST /rewrite` | (T3) | (T3) | Not specified yet. |

**URLs.** `images.*` and `thumbnail` may be absolute or root-relative (`/drawings/<id>/plan.svg`); root-relative URLs are resolved against the API base. The bundled `swiss-46639` keeps its own copies.

**CORS** additionally allows `DELETE` and the headers `X-AURA-Presenter` and `X-Delete-Token`.

```ts
interface DrawingSummary {
  id: string; title: string; kind: DrawingKind; status: DrawingStatus; thumbnail: string
  facts?: { gold_total: number; correct: number; measured_at: string }
  credit: { text: string; license: string; url?: string }
  subtitle?: string; source?: 'library' | 'upload'; progress?: number; error?: string
  created_at?: string; expires_at?: string
}
interface UploadResult { drawing_id: string; delete_token: string; status: 'queued'; contract_version?: string }
interface ChatRequest {
  conversation_id?: string; drawing_id: string; message: string
  scope?: Scope; overrides?: Override[]; lang_hint?: Lang
}
```

Example `GET /drawings` item:

```json
{
  "id": "swiss-46639",
  "title": "Swiss Dwellings · plan 46639",
  "kind": "cad_dxf",
  "status": "ready",
  "thumbnail": "/drawings/swiss-46639/thumb.png",
  "source": "library",
  "facts": { "gold_total": 38, "correct": 38, "measured_at": "2026-11-14T03:00:00Z" },
  "credit": { "text": "Swiss Dwellings dataset", "license": "CC BY 4.0" }
}
```

(The `facts` numbers above illustrate the shape only; serve the eval's real values.)

### New optional fields on `AskResponse`

| Field | Type | Meaning |
|---|---|---|
| `answer_id` | string | For `GET /answers/:id/evidence.csv`. |
| `conversation_id`, `turn_id` | string | Set on answers produced through `/chat`. |
| `route` | `"template" \| "choice" \| "agent" \| "cache"` | Which path answered. |
| `explanation` | string | Model prose **after** the provenance filter (no number that is not in a tool result). |
| `check` | `Check` plus the ranked fields below | |

Ranked check fields (all optional on `AskResponse.check`; `scope` and `ranking` are required in the `check` chat event):

| Field | Type | Meaning |
|---|---|---|
| `scope` | `"drawn_boxes"` | The check never covers objects the geometry missed. |
| `ranking` | `string[]` | Evidence ids, least certain first. |
| `least_certain_id` | string | The UI always shows this box. |
| `anchors_max` | number 0–1 | Highest known-negative anchor score in the same request. |
| `agreed`, `total` | number | Agreed means `p > anchors_max + 0.05`. |
| `image` | `"dataset_png" \| "upload" \| "none"` | What the boxes were checked against. DXF uploads: `"none"` ("No independent image to check against"). |
| `recall` | `{ value, n, source }` | Raster only, from the measured eval. |
| `calls` | number | Decisions requests made for **this** answer's check: `1` live, `0` from the cache. Not included in the top-level `calls`. |
| `cost_usd` | number | USD of those requests (`0` when cached). Not included in the top-level `cost_usd`. |
| `tokens` | number | Input tokens of those requests (`0` when cached). |
| `cached` | boolean | `true` when the check came from the cache (precomputed or an earlier answer). |

### New answer kind

```ts
{ kind: 'text'; value: string; subject: 'fire_rating' | 'label' }
```

Printed text found on the drawing, e.g. a fire rating. Its box is in `evidence.boxes`. No `gold`.

### Trace nodes: tool calls

`TraceNode.tool?: { name: ToolName; args: Record<string, unknown>; result_id: string; ms: number }`

`ToolName` is one of `count_objects`, `room_areas`, `list_rooms`, `locate`, `scope_to_region`, `compare_with_original`, `check_boxes_with_decisions`, `what_if`, `find_text`.

### `POST /chat` events

Each SSE message is `event: <name>\ndata: <json>\n\n`. Order: `turn`, `understood`, then any number of `tool_call`/`tool_result` pairs, then `check` (optional), then `answer` **or** `error`, then always `done`.

Confirmed against the live backend (14 Nov):
- A cached answer (`route: "cache"`) sends no `tool_call`/`tool_result`; it goes `turn`, `understood`, `check`, `answer`, `done`.
- `tool_call.node` is a full `TraceNode` with `tool` set; its `evidence_ids` name the boxes the matching `tool_result.evidence` carries.
- `error` is followed by `done`. At the 60 s deadline the code is `timeout`.
- Comment lines (`: …`) and unknown events are ignored by the client. A known event with a malformed body ends the turn with an error card; no number is shown.
- The client sends `conversation_id` from the first `turn` event on every later message to the same drawing (follow-ups such as "and in apartment B?"), and stores it in localStorage as `aura.conversation.<drawing_id>` to restore with `GET /conversations/:id` after a reload.
- When `/chat` answers 404/405 or with a non-SSE body, the client uses `POST /ask` for the rest of the session.

| Event | `data` |
|---|---|
| `turn` | `{ conversation_id, turn_id }` |
| `understood` | `{ detected_lang, canonical_en, route }` |
| `tool_call` | `{ node: TraceNode }` (with `tool`) |
| `tool_result` | `{ node_id, evidence: { boxes, rooms }, summary }` |
| `check` | `{ check: Check }`: ranked fields when the check ran; a plain `{ method, status: 'sample', image: 'none' }` for DXF uploads and IFC |
| `answer` | `{ response: AskResponse }`: the same body `/ask` would return |
| `error` | `{ error, code: ErrorCode }` |
| `done` | `{}` |

### New error codes

`ErrorCode` adds:

| Code | Status | When |
|---|---|---|
| `too_large` | 413 | Upload above the size cap. |
| `unsupported_type` | 415 | Upload type not accepted. |
| `rate_limited` | 429 | Per-IP limit hit (agent and raster buckets only). |
| `budget_exhausted` | 429 | Event or daily model budget used up; deterministic answers still work. |
| `presenter_required` | 403 | Raster/PDF upload without a valid `X-AURA-Presenter`. |
| `processing` | 409 | The drawing is not `ready` yet. |
| `timeout` | SSE only (`/ask`: 504) | The 60 s turn deadline passed. |

All of these except `timeout` are 4xx, so the frontend shows an error card and never falls back.


### More 1.1 additions (agreed with L1/L2)

| Where | Field | Meaning |
|---|---|---|
| `Box.subject` | adds `"label"` and `"fire_rating"` | Printed-text boxes from `find_text`; ids `text-01…`; the printed text is in `note`. A `text` answer's box uses these. |
| `RoomPoly` | `area_source?: "printed" \| "outline"` | Sent only as `"outline"` for rooms with no printed area (area from the room outline). Absent means printed. |
| `Drawing.facts` / `DrawingSummary.facts` | `languages?: string[]` | Languages the gold rows were counted in, e.g. `["en"]`. |
| `ErrorCode` | adds `"timeout"` | The 60 s chat-turn deadline: SSE `error` event. `/ask` answers `504` (5xx, so the bundled drawing falls back to local answers). |

`detected_lang` stays `en | fr | fi | de`; Italian questions report `"en"` (`canonical_en` is English anyway). A check's own model calls and cost are not added to the answer's `calls`/`cost_usd`; they are reported in `check.calls`, `check.cost_usd`, `check.tokens` and `check.cached`, and in `/stats`. The UI shows both and sums both in the session total.


### IFC, rewrite, raster and agent additions (agreed with L1/L2, 14 Nov)

**IFC (L1).**
- New answer kind `{ kind: 'quantity'; value: number; unit: 'm' | 'm2' | 'm3' }` for wall length, wall net side area and wall/slab net volume per storey. The UI shows the value as given (2 decimals, with its unit) and never recomputes it from evidence.
- New scope `{ kind: 'model' }`: the whole building model, next to `floor` (the drawing's storey).
- New optional `evidence.rows: { id: string; name?: string; value?: number }[]` for counted elements without a 2D box (walls, columns, stairs, whole-model counts); `id` is the IFC GlobalId. The card states how many there are.

**`POST /rewrite` (L2).** Request `{ drawing_id, text }` (text ≤ 2000 characters). Response 200:

```ts
interface RewriteResponse {
  contract_version: string; drawing_id: string; detected_lang: Lang
  blocks: { question: string; intent: 'count' | 'area' | 'other'; subject?: string; scope: Scope; scope_exists: boolean; routable: boolean }[]
  not_answerable: string[]
  calls: number; cost_usd: number; tokens: number; model: string   // one flash-lite call: show "1 model call · $x"
}
```

- Confirm a block by sending `{ question: block.question, scope: block.scope }` to `/ask` or `/chat`: always with the block's scope (the text alone can route differently).
- `routable: false`: shown greyed out.
- Errors: 429 `budget_exhausted`, 429 `rate_limited`, 504 `timeout`, 409 `processing`, 404 `unknown_drawing`.

**Raster uploads (presenter token).** Detection runs in the background job (status `processing` with `progress` 0.5, then `ready`, about 60 s). The drawing has `kind: 'raster'` and `capabilities: ['door', 'window']`. Door and window answers have `method: 'vision'`, no `gold`, and a check with `image: 'upload'`, `ranking` and `least_certain_id` but **no** `agreed`. Every other question abstains `not_supported_yet`. A failed detection ends with `status: 'failed'` and a readable `error`.

**Agent route.** `route` may be `'agent'`: on a router miss, flash-lite function calling streams extra `tool_call`/`tool_result` pairs (node ids `agent-N`, tag `agent · flash-lite`). The answer may carry `explanation` (prose after the provenance filter, shown under the sentence) and a `warn` trace node named `provenance` when sentences with invented numbers were removed. Top-level `calls` counts the `choice` call plus the agent calls.

### Conformance JSON

`src/api/conformance.fixtures.json` is generated from `conformance.fixtures.ts` (`npm run fixtures:export`) and a unit test keeps the two in sync. `backend/tests/test_contract.py` reads the JSON.

## Conformance

`src/api/conformance.fixtures.ts` lists the golden questions and the answers they must produce. The local adapter passes all of them (`npx vitest run`). The backend should pass the same table: the same values, abstain reasons and `gold.match`. These are the key rows:

| Question | Answer | Gold |
|---|---|---|
| How many windows are drawn on this floor plan? | 12 | 12, match |
| How many doors are drawn on this floor plan? | 22 | 22, match |
| How many washbasins are drawn on this floor plan? | 5 | 5, match |
| How many rooms are labelled Chambre à coucher? | 6 | 6, match |
| How many apartments are on this floor plan? | 2 | 2, match |
| Net floor area of apartment A | 139.34 | 139.34, match |
| Net floor area of apartment B | 90.55 | 90.54, match (within 2 %) |
| Total area of rooms labelled Chambre à coucher | 88.09 | 88.09, match |
| Net floor area of all numbered indoor rooms | 261.96 (24 rooms, incl. 3.23 and 3.24 from their outlines) | 258.0, match (within 2 %) |
| Net floor area of room 3.04 | 17.04 | 17.04, match |
| How many WCs / bathtubs / showers? | 5 / 2 / 2 (`wc-*`, `tub-*`, `shower-*` evidence) | 5 / 2 / 2, match |
| Fire rating of the entrance door | abstain `not_on_drawing` | none |

The table also includes FR, FI and DE phrasings of the window, door and apartment A area questions.
