# Lossless project store

A database of construction drawings that keeps everything. Every source file (IFC, DXF/DWG, PDF, PNG/JPG, SVG) is
mirrored into PostgreSQL without taking any decision, interpretations (rooms, doors, windows, fixtures and the
value each source gives for them) are stored on top with pointers back to the primitives they were read from, and
a question-answering model reads the result through a small set of read-only views.

## The layers

- **Layer 0, registry**: projects, source files, pages, and which files show the same drawing (`drawing_key`).
- **Layer 1, mirror**: every CAD entity, PDF path and text, OCR word, detection, IFC element, property and relation, in the file's own coordinates. No decisions.
- **Layer 2, interpretations**: entities (room, door, window, fixture ...), one `claim` per property and source, `entity_source` rows pointing back at Layer 1, `issue` rows where sources disagree.
- **Schema `q`**: the only thing the model can read: views over both layers, scoped to the drawings of the current run, through the read-only role `jreader`.
- Tables: `jstore/schema.sql`; views: `jstore/views.sql` and `jstore/sql/*.sql`; the model's schema document: `prompts/STORE.md`.

## Start

```bash
cd store
cat .env            # GEMINI_API_KEY, JSTORE_API_TOKEN, JSTORE_MODEL, JSTORE_OPENAI_BASE_URL (git-ignored)
docker compose up -d --build                      # database on 127.0.0.1:54329, API on 127.0.0.1:8000
docker compose run --rm api python -m jstore.cli ingest /data/dataset     # preprocess the mounted dataset folder
docker compose run --rm api python -m jstore.cli status
```

The dataset folder is mounted read-only at `/data/dataset` from `JSTORE_DATASET_HOST_DIR`; renders, uploads and
run logs live in the named volume `store` (`/data/store`). `WITH_LIBREDWG=1 docker compose build api` adds
`dwg2dxf` for DWG files (GPL-3.0, see THIRD_PARTY.md). `JSTORE_MODEL=demo` runs without an API key: a scripted
model makes one `sql` call and answers.

Without Docker (development): `export JSTORE_DSN=postgresql://postgres:jstore@127.0.0.1:54329/<database>`, then
`python -m jstore.cli ingest <folder> --base <dataset root>` and `uvicorn jstore.service.app:app --port 8000`.
Tests: `python -m pytest tests -q` (scratch database, no API key). After any change to the views:
`python -m jstore.service.prompt` regenerates `prompts/STORE.md`; a test fails when it is stale.

| Variable | Default | Meaning |
|---|---|---|
| `JSTORE_DSN` | `...@127.0.0.1:54329/postgres` | database |
| `JSTORE_API_TOKEN` | unset = no auth | bearer token of the API |
| `JSTORE_MODEL` | `gemini-4-hydrogen` | model name, or `demo` |
| `JSTORE_OPENAI_BASE_URL`, `GEMINI_API_KEY` | Gemini's OpenAI-compatible endpoint | model access |
| `JSTORE_REASONING_EFFORT` | `low` | `low` / `medium` / `high` / `default` |
| `JSTORE_DATA_DIR`, `JSTORE_DATASET_DIR` | `.data`, `/data/dataset` | store directory; folder `POST /files/register` may reach |
| `JSTORE_SHELL` | `on` | `off` removes the last-resort shell from the agent |
| `JSTORE_SQL_MAX_ROWS`, `JSTORE_MAX_TURNS`, `JSTORE_MAX_UPLOAD_MB`, `JSTORE_MAX_CONCURRENT`, `JSTORE_INGEST_WORKERS` | 200, 30, 500, 4, 2 | limits |
| `JSTORE_APPLY_SCHEMA` | `1` | create / update tables, views and the reader role when the API starts |
| `JSTORE_CORS_ORIGINS` | `https?://(localhost\|127\.0\.0\.1)(:\d+)?` | browser origins that may call the API (regular expression) |

## API

Every route except `GET /health` (liveness) and `GET /` (reference chat page) needs
`Authorization: Bearer $JSTORE_API_TOKEN`, or `?token=` for `<img>` and `EventSource`
(or, under `/aura` only, the token as a path prefix: `/aura/t/<token>/...`).

```bash
API=http://127.0.0.1:8000;  H="Authorization: Bearer $JSTORE_API_TOKEN"

curl -s $API/health                                                    # {"status":"ok"}; with the token: model, database, runs
curl -s -H "$H" -F file=@plan.dxf $API/files                           # upload -> {file_id, status, ingesting, drawing, ...}
curl -s -H "$H" -H 'Content-Type: application/json' \
     -d '{"path":"data/swiss_dwellings/site1000_bldg2082_floor9385.dxf"}' $API/files/register   # a file of the dataset folder
curl -s -H "$H" $API/files/$FILE_ID                                    # status registered|parsed|interpreted|failed + row counts
curl -s -H "$H" -OJ $API/files/$FILE_ID/raw                            # the original bytes

curl -s -H "$H" -H 'Content-Type: application/json' \
     -d '{"file_ids":["'$FILE_ID'"],"questions":["How many doors are there?"]}' $API/ask         # blocks, returns the answers
curl -s -H "$H" -H 'Content-Type: application/json' \
     -d '{"file_ids":["'$FILE_ID'"],"questions":["Area of apartment B?"]}' $API/runs             # 202 {run_id}
curl -sN -H "$H" $API/runs/$RUN_ID/events                              # server-sent events, resumable with ?after=<seq>
curl -s -H "$H" $API/runs/$RUN_ID                                      # the run: status, answers, usage, events
curl -s -H "$H" $API/runs/$RUN_ID/artifacts                            # files the shell tool wrote; /artifacts/<name>, /views/<n>.png

curl -s -H "$H" "$API/drawings?project=swiss_dwellings&q=site1000"     # drawing keys with files and status
curl -s -H "$H" $API/drawings/$KEY                                     # files, pages (image URL, size), entity counts, issues
curl -s -H "$H" "$API/drawings/$KEY/entities?kind=room"                # entities with the value of every source
curl -s -H "$H" -o page.png $API/files/$FILE_ID/pages/0/image          # the rendered page
curl -s -H "$H" "$API/evidence?entity_ids=21,22,23"                    # per entity and source file: page, box, box_pct, Layer 1 keys
```

A run's scope is the drawings of its files, and with them every file of those drawings (the same plan as DXF,
PDF and image). An upload becomes its own project `upload/<file_id>`; parsing and interpretation run in a worker
process, and a run started before they finish waits for them.

Events (`run_started`, `ingest`, `tool_started`, `tool_finished`, `message`, `answers`, `run_finished`) and the run
object follow the chat contract of the earlier service (`/root/junction/service/CONTRACT.md`); additions are
`sql` tool events (`query`, `rows`, `returned`) and these answer fields:

```json
{"n": 1, "answer": 29, "unit": "count", "method": "...", "check": "...", "confidence": 1.0,
 "sql": "select count(*) as n, array_agg(entity_id) as ids from q.doors where not is_duplicate",
 "entity_ids": [56, 57], "refs": [{"file_id": "...", "ref_table": "cad_entity", "ref_key": "C1"}],
 "evidence_total": 29,
 "evidence": [{"kind": "cad", "entity_id": 56, "label": "door", "file_id": "...", "page_no": 0, "ref_table": "cad_entity",
               "ref_key": "161", "handle": "161", "layer": "DOOR", "bbox": [3.78, -5.79, 4.72, -4.96],
               "box_pct": [41.2, 55.0, 43.9, 57.1], "bbox_px": [..], "image_url": "/files/<id>/pages/0/image"}],
 "sources": [{"entity_id": 56, "property": "width_m", "value": 0.9, "unit": "m", "method": "measured_geometry", "format": "dxf"}],
 "agreement": {"width_m": {"single": 29}},
 "warnings": []}
```

The model only names ids; the service resolves them into evidence regions (`box_pct` is in percent of the page
image, through `page.to_image`; it is absent while a page has no render), attaches the per-source values from
`q.sources` and the agreement from `q.facts`, and checks the answer: every number must be a cell, a row count or a
column sum of a `sql` result of the run, otherwise the answer gets a warning and its confidence is capped at 0.4.

## AURA frontend (`/aura`)

`jstore/service/aura.py` serves the contract of the AURA frontend (`frontend/src/api/contract.ts`, version 1.1)
from the store, so that frontend runs against this API unchanged. The frontend sends no credentials, so the token
is part of the base URL: `VITE_API_URL=http://localhost:8000/aura/t/<JSTORE_API_TOKEN>` (the server's access log
shows such requests as `/aura/...`, without the token).

```bash
ssh -L 8000:127.0.0.1:8000 <server>                                  # the API stays bound to the server's localhost
cd frontend && npm ci
VITE_API_URL=http://localhost:8000/aura/t/$JSTORE_API_TOKEN npm run dev      # then open http://localhost:5173/app
```

`VITE_API_URL` is read when the dev server starts (or at `npm run build`); `frontend/.env.local` (git-ignored) is the
place to keep it. Browser origins `http://localhost:*` and `http://127.0.0.1:*` may call the API; set
`JSTORE_CORS_ORIGINS` (a regular expression) for any other origin.

- **Drawings.** One AURA drawing per drawing key; its id is the key when that is URL-safe, else `k.<base64url>`.
  `GET /aura/drawings` lists every dataset drawing (never uploads), `GET /aura/drawings/{id}` is built on the fly
  from the q views: rooms as polygons, units from the apartments, capabilities from what was interpreted.
- **Coordinates** are percent of one page of the drawing ("the page shown"): a raster or PDF page registered in
  the drawing's frame, else the CAD render, else the page most entities were read from. Entity geometry goes
  through `frame` and `page.to_image`, or through the entity's own sources on that page when there is no frame.
  `original.png` is that page; `plan.svg` is the same space redrawn from the store (strokes of the vector file,
  rooms, doors, windows, fixtures) and is what the frontend shows, with the page behind "Compare with original".
  A raster-only drawing shows the page itself; a CAD file that was never rendered shows `plan.svg` only;
  so does a storey of a model (below).
- **Models (IFC).** A drawing with an IFC file is one library entry per storey: id `<drawing id>~<storey>` (the
  storey name in lower case, runs of other characters as `-`: `hitos_arc~1-etg`), title `<model> · <storey>`,
  storeys with rooms, doors, windows or fixtures first and from the lowest up, then storeys that only have element
  footprints (foundations, roofs; "outlines only"); storeys with nothing to draw are left out. The drawing's own id
  still works and opens the lowest storey with rooms; a model with a single storey view is listed under that id.
  Things assigned to no storey form a view of their own (`~unassigned`). Ids of drawings without IFC are unchanged.
  - A storey view is a page of our own: the extent of that storey's geometry, north up, 2000 px wide. `plan.svg`
    draws the footprints of the storey's elements (`ifc_geometry`: walls and columns filled grey, slabs, stairs,
    pipes and the rest as thin outlines; spaces, openings, coverings and the site are left out), the rooms with
    number, name and area, doors, windows and fixtures. `thumb.png` / `original.png` are a PIL raster of the same
    picture. Only that storey's entities, issues and evidence are in the drawing; evidence ids count per storey.
  - A storey that a plan file shows (the file's `storey` equals the storey's name up to case, accents and
    punctuation: `4-korrus` ~ `4.korrus`, `klder` ~ `Kælder`) and whose page is registered in the model's frame is
    shown on that page instead (of two such pages the PDF), with only that storey's entities.
  - Questions on a storey view run on the whole drawing (the agent needs the model); the question tells the agent
    which storey is open and that "this floor" means it. Entities behind the answer that lie on other storeys are
    not drawn; the explanation says how many there are and on which storeys. References to pages of other storeys
    are listed as rows, never as boxes.
  - Limits: an element belongs to the storey it is assigned to in the model, so a stair, shaft or split-level
    part is drawn on one storey only, and elements modelled on the wrong storey show there; storeys with the same
    name (after normalisation) are one view; a storey without spaces shows outlines and items only; very large
    storeys keep their 6000 largest footprints.
- **Evidence ids** are `door-01`, `win-01`, `basin-01`, `wc-01`, `tub-01`, `shower-01`, `sink-01` (reading order per
  kind over the whole drawing) and `room-<number>`; a thing drawn twice names the first copy in `duplicate_of`.
- **Questions.** `POST /aura/chat` (server-sent events) and `POST /aura/ask` start an ordinary run and translate its
  answer: counts with boxes, areas with the rooms that add up to the value, lengths and volumes as quantities,
  anything else as text, `null` as an abstain. The UI's scope (apartment, room, label, marked region) and what-if
  sizes are handed to the agent in words. Tool calls stream as steps and form the reasoning tree. Conversations are
  kept in `<store>/aura/conversations`.
- **Also served:** `/aura/drawings/{id}/{drawing.json, model.json, evidence.json, checks.json}` (the drawing with
  all boxes; rooms and items with entity ids, the value of every source and their agreement; the open issues).
- **Stubs:** `/aura/rewrite` (returns the text as one question, no model call), `/aura/budget` and `/aura/stats`
  (counters since process start; the cap is `JSTORE_AURA_BUDGET`, nothing enforces it), `/aura/admin/models`
  (reports, does not switch). `cost_usd` is 0 unless `JSTORE_PRICE_IN` / `JSTORE_PRICE_OUT` (USD per million tokens)
  are set. A turn is cut off after `JSTORE_AURA_DEADLINE_S` (300).

## The agent

OpenAI Agents SDK on Gemini's OpenAI-compatible endpoint, low reasoning effort, parallel tool calls off.
Instructions: `prompts/AGENT.md` + `prompts/STORE.md`. Tools: `sql(query)`, `view_image(file_id, page_no, box?)`
(a page render or a crop in page-image percent) and `shell(command)` as the last resort (refused until the run has
queried the store; works on copies of the original files in the run directory).

## Security notes

- **Ports** are published on 127.0.0.1 only (API 8000, database 54329). Reach them through an SSH tunnel; put a TLS
  reverse proxy in front before exposing anything. Set `JSTORE_API_TOKEN`: without it the API is open.
- **The model reads through `jreader`**: read-only, 8 s statement timeout, usage on schema `q` only. The `sql` tool
  accepts one SELECT / WITH statement and refuses `set_config`, `current_setting`, `pg_*`, `information_schema`,
  `jstore.` and a second statement. Independently of that text check, `jstore/sql/50_service.sql` revokes
  `set_config` and the functions that execute SQL given as text (`query_to_xml` ..., `ts_stat`) from PUBLIC in this
  database, so a query cannot change its own scope (the service sets the scope with `SET` on its connection).
  Superusers are not affected. Change `JSTORE_READER_PASSWORD` and `JSTORE_DB_PASSWORD` from their defaults.
- **Drawing content is untrusted input to the model** (texts, layer names). The store limits what a misled model can
  do to reading the run's own drawings. The `shell` tool is the weak point: it runs as the service user inside the
  API container with an empty environment (no key, token or database address), but it is not a sandbox: it has
  network access and, being the same user, could read the service process's own environment. Set `JSTORE_SHELL=off` where that matters.
- **Uploads** are size-limited before they are read, stored under their content hash, never executed, and served
  back only as downloads (`nosniff`, CSP `sandbox`); only PNG/JPEG render inline.
- The API key and the token live in `.env` only (git-ignored, excluded from the image by `.dockerignore`); neither
  is returned by any endpoint. SDK tracing is off, so no run data goes to OpenAI.
- Licences: THIRD_PARTY.md. No AGPL/GPL Python code; PostGIS (GPL-2.0) runs only inside the database container.
