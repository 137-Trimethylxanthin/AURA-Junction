# Overnight notes (14 → 15 Nov 2026)

Branch: `simon/overnight-blueprint-improvements` (pushed). Every fix is its own commit, so any one can be reverted.

## What changed

| # | Problem | What was done | Proof |
|---|---|---|---|
| 1 | "All N floors" failed with *Unknown project 'proj-…'* | `/ask` and `/chat` never sent the project token, so every saved (private) project looked unknown. The app now sends `x-project-token` / upload delete tokens with project-scoped questions, re-saves a project the server lost (e.g. after a restart) and asks again. A 404 with an error body no longer disables streaming chat for 5 min. Room-type counts across floors also work now ("how many bedrooms" → 4 + 3 = 7): the room filter is applied per floor and routing knows every floor's labels. Numbered labels (`BEDROOM #1…#4`, typical of US plans) form a family. | `frontend/src/api/source.project.test.ts`, live e2e `frontend/e2e/live.project-floors.spec.ts`, `backend/tests/test_label_families.py` |
| 2 | "Compare with original" compared a photo with itself | Image uploads get a vector rebuild (`plan.svg`: walls, windows, doors, room names) traced with OpenCV in the image's own pixel frame, so the swipe/blend is exactly aligned. Compare is hidden when there is no distinct rebuild. | `backend/tests/test_vectorize.py`, `test_rebuild.py` |
| 3 | 3D of image uploads extruded ink pixels | The 3D view reads the new `plan.svg` like a CAD plan: real walls, room floors with labels. | `frontend/src/features/viewer3d/viewer3d.test.ts` (vector plan case) |
| 4 | Rooms from images were boxes | Each labelled room takes the drawn region its label sits in (L-shapes etc.); several labels in one open region split it by nearest label. Area = polygon × scale (net, inside the walls); printed areas are never replaced. | `test_rebuild.py` (L-room net area within 5 %) |
| 5 | Unit conversion | "convert 12 m² to sq ft" → 129.17 sq ft by code (EN/DE/FR/FI/IT, `12'6"`), 0 model calls. Header menu → "Units in answers": Metric (default) or Imperial; imperial shows every area/length answer in ft / sq ft with the metric value beside it. "area of bedroom 2 in square feet" answers in m² plus a `converted` value the card shows big, with the metric value beside it. Metric stays the default; "in m²" questions are untouched. | `backend/tests/test_units.py`, `frontend/src/lib/units.test.ts` |
| 6 | Read dimensions/areas from the drawing | Dimension strings (metric and ft-in) are read from the pixels (RapidOCR + one cached Gemini pass, endpoints snapped to the drawn ticks), give the scale when chains agree with the overall, and answer "overall dimensions", "how wide", "length of wall #1" with 0 model calls. For image/PDF uploads the reasoning step now also gets the sheet image and the printed text layer, so it reads printed facts the index lacks. | `store/tests/test_dimensions.py` (45), `backend/tests/test_dimensions_answer.py` |

Other fixes found on the way:
- An area question naming one thing the drawing has no room for ("area of the Lagerhalle") no longer returns the whole-floor total.
- An SQL row count is never the answer to a non-count question ("What is the scale?" used to answer "1").
- "bedroom 2" means the printed label `BEDROOM #2`, not every bedroom.
- `scripts/dev.sh` uses docker when podman is missing.
- Upload copy says images/photos are accepted (EN/FR/FI/DE).

### Item 2 bake-off (time-boxed, two approaches in parallel)

Measured on the Swiss crop against its ground-truth rooms (`design/assets/mock.json`) and on the two phone photos of US plans.

| | A: OpenCV (chosen) | B: Gemini + snapping |
|---|---|---|
| Swiss room IoU (mean / share > 0.7) | 0.71 / 68 % | 0.87 / 95 % |
| Swiss wall-pixel IoU | 0.65 | 0.62 |
| Phone photos | clean rooms and interior walls | overlapping room polygons, missing interior walls |
| Cost / time | 0 calls, ~2 s | 2 calls (~$0.05), ~12 s |

A was chosen: it is robust on real photos, deterministic and free; the weaker room split on clean renders is covered because room *labels* come from the store's model pass and each label takes its region (merged regions are split by label). B's code stays on its worktree branch `worktree-agent-a90f9b154a1fa1a61` if wanted later.

## Results on the real US plans (b1 lower, b2 upper, phone photos)

- Scale from the dimension chains: 13.5 mm/px; 98'-0" overall within 0.1–0.4 %.
- Rooms are polygons with areas (e.g. b2 Bedroom #2 17.67 m², Kitchen 11.12 m², Baths 4.3 m²).
- "What are the overall dimensions of the building?" → 29.87 m × 7.75 m (printed 98'-0" × 25'-5"), 0 calls.
- "How wide is the building in feet?" → 29.87 m = 98 ft. "Length of wall #1" → 6.38 m (20'-11").
- All 2 floors: 7 bedrooms (4 + 3), 2 baths, 1 kitchen, 0 calls.

Lagerhalle site plan (the extreme example): BGF 120,00 m², 12.00 × 10.00 m, 45 parking bays — read from the sheet by the reasoning step (was 548.76 m² and an abstain).

## Dataset evaluation (preview set, `backend/scripts/eval_dataset.py`)

Scored like the challenge (counts exact). Same drawings, before and after tonight's fixes:

| Source | Questions | Before | After |
|---|---|---|---|
| Swiss PDF (vector) | 28 | 28 ok | 28 ok |
| Swiss PNG (image) | 42 | 0 ok, 42 abstain | **38 ok**, 0 abstain |
| CubiCasa PNG (5 plans) | 62 | 21 ok, 28 abstain | **55 of 68 ok** (more questions asked later) |
| CubiCasa PNG (5 unseen plans) | 76 | — | **46 ok**, 1 abstain |
| Swiss DXF | 40 | — | 40 ok (no regression from label families) |
| US door schedule sheet (A-801) | "doors listed" | 0 | **38** (truth 38) |

What fixed it: (1) a thread race in the store's room reader (dimension reading decoded the same PNG
concurrently) left image uploads without rooms; (2) room-less image uploads skipped the tool tier;
(3) zero counts from guessed SQL on sheets were final; the reasoning step now reads the sheet instead.
Later fixes: Finnish abbreviations (KK/AVOK kitchen, S sauna) in the store vocabulary and the SQL
planner told not to substring-match abbreviations; room-less scans no longer stop at a guessed label;
'marked MH' counts only printed markings. Remaining errors are mostly the store's symbol detector
undercounting windows drawn as thin double lines on long walls (12→6, 11→7, 18→14) — the best next
lever for CubiCasa accuracy (store/jstore/gemini_detect.py).

Noise/robustness (uploads that are not floor plans): a church-interior engraving and a photograph are
recognised as "not a floor plan" (counts abstain, "what is shown" describes them); a HAER bridge sheet
answers "0 rooms — this is a bridge elevation"; a 1930s German plan gets 58 rooms and a rebuild; a faint
archive scan indexes without a rebuild (no plausible walls). Photos/elevations never get a vector
rebuild (plausibility gate), so Compare is hidden for them.

The IFC-rendered PDF questions ask about the whole IFC model (doors in the model), which a single
rendered storey cannot answer; they abstain honestly.

## Demo script (≈5 min)

1. `scripts/dev.sh` (needs docker or podman; Gemini key in `~/.config/junction/env`). Open http://localhost:5173/app.
   The demo uses fresh uploads only. The bundled library drawings (swiss-46639 etc.) are served by the API only when
   `backend/build/drawings*` exists (build it as before, or set `AURA_BUILD_DIR`); without it the frontend shows the
   bundled drawing from its local data.
2. Upload `b1.jpg` and `b2.jpeg` together on the upload page (one project). Wait for "Indexed" (~2–3 min each).
3. Open b2: show the plan, toggle **Compare with original** (swipe photo ↔ vector rebuild, aligned), then **3D**.
4. Ask: "Show all bedrooms" (polygons light up), "What is the area of bedroom 2 in square feet?", "What are the overall dimensions of the building?", "convert 12 m² to sq ft". Then switch the header menu (EN) → Units → Imperial and ask "How big is bedroom 1?" → 251.77 sq ft = 23.39 m².
5. Switch on **All 2 floors** and ask "How many bedrooms are there?" → 7, with the per-floor split.
6. Optional wow: upload the Lagerhalle PDF and ask "What is the gross floor area of the Lagerhalle?" / "How many parking spaces are there?".

## Checks

```bash
cd backend && uv run pytest -q -m 'not dataset and not live'
cd backend && uv run pytest -q --noconftest ../store/tests/test_dimensions.py
cd frontend && npx tsc -b --noEmit && npx vitest run
cd frontend && npm run e2e:live   # against a running scripts/dev.sh; set AURA_E2E_PLANS=/path/b1.jpg,/path/b2.jpeg
```

## What's left / known limits

- Window undercount by the store's symbol detector on CubiCasa scans (see above).
- An uploaded multi-storey IFC is loaded as one storey-less drawing (`parse_worker.parse_ifc`, `storey=None`),
  so per-storey questions ("doors on '1.korrus'") abstain; the library's per-storey IFC drawings are fine.

- Uploading byte-identical files reuses the store's earlier interpretation; after a code change re-index or re-encode.
- The vectorizer occasionally closes door gaps with a short diagonal and merges rooms with no wall between them (only matters for unlabelled regions; labelled rooms are split).
- Dimension reading takes 20–30 s per image (OCR + Gemini run in parallel with the rest of ingest).
- The store package's own venv lacks pytest; its tests run from the backend venv with `--noconftest`.
