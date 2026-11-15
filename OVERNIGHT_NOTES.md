# Overnight Improvements Notes (`feat/overnight-improvements`)

Branch: `feat/overnight-improvements` (branched cleanly off latest `origin/main` `886263e`; previous branch `feat/chat-coverage-tool` left completely untouched).

---

## 1. What Changed

### A. Fixed `"All x Floors"` Multi-Plan Error (`Unknown project 'proj-...'`) — Commit `fe7a22e`
- **Root Cause**:
  1. `POST /projects` issues an owner `token` (`token_hash` in SQLite), and `backend/aura/main.py` (`_scope_refused`) returns `404 Unknown project '<id>'` for any token-owned project or `up-*` upload-backed floor unless `x-project-token` / `x-delete-token` headers are sent. While `GET /projects` sent `myProjects.listHeaders()`, `POST /chat` (`streamChat`) and `POST /ask` (`postAsk`) omitted those headers entirely.
  2. `streamChat` treated *all* HTTP `404` responses as `ChatUnavailable` ("endpoint missing"), disabling SSE streaming for the session instead of surfacing AURA's `ErrorBody` (`code: 'not_found' | 'unknown_drawing'`).
  3. Navigating from `/upload` back to `/library` before background upload parsing finished unmounted `useGroupFinisher`, leaving multi-file uploads unbundled on the server, and local-only/post-restart projects in `localStorage` were not re-synced before `/chat` or `/ask`.
- **Fix**:
  - Forwarded `myProjects.listHeaders()` (`x-project-token` + `x-delete-token`) and `project_id` (`activeProjId`) on every `/chat` and `/ask` request (`frontend/src/api/{client,stream,source}.ts`, `frontend/src/store/appStore.ts`).
  - Distinguished AURA `404` domain errors (`not_found` / `unknown_drawing`) from missing-endpoint `404`s in `frontend/src/api/stream.ts`.
  - Added pre-flight sync + automatic 1-shot `POST /projects` self-healing retry on `404 not_found` in `frontend/src/api/source.ts`, storing the newly issued project token in `myProjects`.
  - Mounted `useGroupFinisher` on `LibraryPage.tsx` in addition to `UploadPage.tsx`.
  - Added regression suite in `frontend/src/api/projects.test.ts` (all 62 API/store/upload vitest tests passing).

### B. Fixed Windows Upload & Multi-Page PDF Parsing Crashes — Commit `fe7a22e`
- Guarded `import resource` (`None` on Windows) and preserved required Windows OS environment variables (`SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT`, `TEMP`, `TMP`, `USERPROFILE`) in `backend/aura/parse_worker.py` so upload subprocesses no longer crash on Windows.
- Closed `pdfium.PdfDocument` before `tmp_p0.replace(src)` in `backend/aura/solvers/pdf.py` (`render_pdf`) to fix `PermissionError` file-locking failures on multi-page PDF uploads on Windows (`tests/test_pdf_upload.py` and `tests/test_building.py` 100% green).

### C. Natural-Language Unit Conversion (Default: Metric) — Commit `caafa44`
- Added `backend/aura/units.py` wired into `router.py`, `pipeline.py`, and `building.py`:
  - **Standalone conversions (`0` model calls, `<1ms`)**: Supports length (`mm`, `cm`, `m`, `km`, `in`/`"`, `ft`/`'`, compound `14' 6"` / `14 ft 6 in`, `feet and inches`, `yd`), area (`mm²`, `cm²`, `m²`/`sqm`, `sq in`/`in²`, `sq ft`/`ft²`/`sqft`/`square feet`, `sq yd`, `ha`, `acres`), and volume (`cm³`, `L`, `m³`, `cu in`, `cu ft`, `cu yd`, `gal`) across EN/DE/FR/FI.
    - `"convert 12 m² to sq ft"` → **`129.17 sq ft`** (`12 m² = 129.17 sq ft`)
    - `"convert 250 sq ft"` (no target unit) → **defaults to metric `23.23 m²`**
    - `"14' 6\" in m"` → **`4.42 m`**
    - `"convert 4.42 m to feet and inches"` → **`14' 6" (14.50 ft)`**
  - **Blueprint area & measurement queries in any unit (default metric)**:
    - Queries without a non-SI target unit (`"What is the net floor area of apartment A?"`) continue returning metric (`m²` / `m` / `m³`) untouched.
    - Queries requesting a target unit (`"What is the net floor area of apartment A in sq ft?"`, `"How many square feet is room 3.04?"`, `"square footage of the kitchen"`) compute exact geometry in metric, convert deterministically to the target unit via `AnswerNote`, and **preserve 100% of room/box/group evidence highlights on the plan**.
    - Works seamlessly with **`"All x Floors"`** whole-building queries, converting both the building total and every floor's `breakdown` row without double-rounding.
  - **Conversational follow-ups**:
    - Asking `"in sq ft?"`, `"convert to square feet"`, or `"and in metric?"` after a measurement turn converts the previous turn's value in `<1ms` (`0` model calls) while keeping the rooms/boxes highlighted on the canvas.
  - Verified by `backend/tests/test_units.py` + `tests/test_router.py` (55 passed).

---

## 2. What’s Left (Stopped at Morning Cutoff)

> **Note on Overnight Timeline**: Execution experienced an upstream stream outage between `01:20` and `07:50`. Per the `07:30` / `08:00` stop-new-work rule, no half-baked changes were started after `07:50`; instead, completed items were verified, built (`npm run build` + `pytest`), and committed cleanly.

Remaining items for post-cutoff continuation, with architectural design ready:
1. **Image-Imported Vectorization Chain (#2 Compare with Original → #3 3D CAD Model → #4 Polygonal Room Areas)**:
   - *Design*: Generate pixel-aligned `plan.svg` (`viewBox="0 0 pw ph"`) on raster upload with `#2f343c` wall polygons, `#ffffff`/`#374151` windows, `#4b5563` doors, `#6b7280` fixtures, and inner-wall-snapped multi-vertex room contours (`points`), setting `images.rebuilt = "/drawings/<id>/plan.svg"`, `images.original = "/drawings/<id>/original.png"`, `original_alignment = "exact"`, and `evidence_method` in `drawing.json`.
   - Because `Viewer3D.tsx` (`isRebuiltPlanSvg` + `parsePlanSvg` + `buildPlanModel`), `CompareLayer.tsx`, and `BundleEngine` (`tools.py`) already consume this exact schema, writing `plan.svg` + polygonal `rooms` unlocks true Compare swipe, 3D extruded walls/rooms/multi-floor stacks, and non-rectangular polygon areas simultaneously without removing the Compare button.
2. **Blueprint Side Dimensions & Printed Area Calibration (#6)**:
   - *Design*: OCR + extension-line/tick span matching (`rapidocr-onnxruntime` + OpenCV + Gemini structured vision fallback) to extract exterior margin dimensions (`top`/`bottom`/`left`/`right`) and interior dimension strings, auto-calibrating `m_per_pct_x` / `m_per_pct_y` and indexing `dimensions` for direct natural-language width/length/overall-size queries.
3. **Playwright E2E Spec**:
   - Add browser E2E coverage once #2–#4 and #6 land (multi-floor `"All x Floors"` + unit conversions are currently covered by Vitest + FastAPI TestClient integration tests in `frontend/src/api/projects.test.ts` and `backend/tests/test_units.py`).

---

## 3. Live Demo Script

Both dev servers run locally on:
- **Frontend**: `http://localhost:5173`
- **Backend**: `http://127.0.0.1:8000`

### Step 1 — Multi-Floor `"All x Floors"` Query (Bug #1 Fix)
1. Open `http://localhost:5173/library` (or `/upload` and drop 2+ floor plans / multi-page PDF to create a multi-floor project).
2. Open a multi-floor project in the workspace and ensure **`All x Floors`** is checked in the floor switcher / chat scope bar.
3. Ask:
   - `"How many doors are there?"`
   - `"What is the total net floor area?"`
4. **Expected**: Instant combined building total with per-floor breakdown chips and clickable room/door evidence across sheets — **no** `Unknown project 'proj-...'` error.

### Step 2 — Natural-Language Unit Conversions & Metric Default (Item #5)
1. In the chat box on any plan, ask a standalone conversion:
   - `"convert 12 m² to sq ft"` → Displays **`129.17 sq ft`** (`12 m² = 129.17 sq ft`) in `<5 ms`, `0` model calls.
   - `"14' 6\" in m"` → Displays **`4.42 m`**.
   - `"convert 250 sq ft"` (no target unit) → Defaults to metric **`23.23 m²`**.
2. Ask a blueprint room/apartment area question in metric (default) vs imperial:
   - `"What is the net floor area of apartment A?"` → Returns exact area in **`m²`** (metric default) and highlights Apartment A's rooms.
   - `"What is the net floor area of apartment A in sq ft?"` → Returns exact converted area in **`sq ft`** (`... sq ft (... m²)`) while **keeping every room of Apartment A highlighted on the plan**.
3. Follow up conversationally in the same chat thread:
   - `"in sq in?"` → Converts the previous answer to square inches while keeping the room highlights.
   - `"and in metric?"` → Returns cleanly to **`m²`**.
4. With **`All x Floors`** checked on a multi-floor project, ask:
   - `"What is the net floor area in sq ft?"` → Sums across all floors and reports both the building total and each floor's breakdown in **`sq ft`**.
