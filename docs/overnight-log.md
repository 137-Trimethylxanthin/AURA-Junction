# Overnight improvements (branch `overnight-improvements`)

Started 2026-11-14 22:10 UTC from `main` at cef4fa4. Hard stop 2026-11-15 06:00 UTC.
Each work stream runs in its own git worktree and branch, is verified on a private instance,
and is then merged here. This file is the running record: what was asked, what was done, how it
was checked, and what is still open.

## What was asked

1. "All N floors" fails: `Couldn't answer this one — Unknown project 'proj-…'`.
2. "Compare with original" is poor for drawings imported from an image: build a real vector
   plan (SVG or CAD-like model) from the image.
3. The 3D model is bad for image imports; it should work from that vector model.
4. Areas of rooms indexed from images are always rectangles: make them polygons.
5. Unit conversion to and from inches, feet, metres ... in natural language. Default: metric.
6. Read areas and dimensions printed on the sheet (dimension lines, printed areas) and use
   them for measurements.
7. Other burning issues found on the way; the result must be easy to demo.

## Work streams

| Stream | Branch | Scope |
|---|---|---|
| A | `on/a-backend-fixes` | 1, plus known bugs: whole-building totals, cached budget refusals, exact label on scan answers, sample drawing button, `/api/api` prefetch |
| B | `on/b-scan-vector` | 2, 3, 4: vector model of a scan (walls, room polygons, openings), plan.svg, compare, 3D |
| C | `on/c-units` | 5: units in questions and answers |
| D | `on/d-dimensions` | 6: printed dimensions and areas |

## Log

### 22:20–23:00 UTC — wave 1: four streams, each implemented then reviewed

Each stream ran in its own worktree, was checked on a private instance (scratch database or
the live store read-only) and reviewed by a second agent who re-did one real-app check.

**Stream A — fixes (`on/a-backend-fixes`)**

| Item | Result | How it was checked |
|---|---|---|
| "All N floors": `Unknown project 'proj-…'` | Fixed. Cause: a manual group is saved under an owner token that `/ask` and `/chat` never sent, so the server treated its own project as unknown; unsaved groups failed the same way. The backend now answers an unknown project id as an ad-hoc group when every sheet is a library drawing (uploads still need their delete tokens), and the frontend sends the tokens. | Browser: IFC building "All 5 floors" → 198 doors; unsaved manual group → 38 doors; group known only to the browser → 65 doors |
| Whole-building totals inflated (990 for 198, 1477 for 211) | Fixed: each sheet answers for its own storey | Test on the real bundles; browser |
| Budget or rate-limit refusals cached and replayed | Fixed: an abstention after a `BudgetError` is never cached; cache key version bumped so old entries are not replayed | Tests |
| Scan answers labelled "Exact · CAD geometry" | Fixed: the drawing's evidence method is applied to every answer before it is emitted | Test; live check on the school plan |
| Landing "Try it on the sample drawing" opened a drawing the server lacks | Fixed: `/demo` opens the bundled sample only if the server lists it, else the server's first ready drawing | Browser |
| `/api/api/...` prefetch 404s | Fixed: `apiUrl` is idempotent | Unit test |
| Compare label "Rebuilt from CAD" on scans | Label depends on drawing kind | Not screenshotted |

Not done: a DWG site plan still answers "1 door, Exact" (the store holds one CAD door entity there).

**Stream B — vector model of image plans (`on/b-scan-vector`)**

- Rooms from images always get polygon geometry. Where the sheet states no scale, a nominal
  scale is estimated (door widths first) and everything derived from it is marked approximate.
- Walls become vector geometry (`wall` entities, view `q.walls`). Two methods were built and
  compared: tracing thick wall pixels, and deriving walls from the gaps between room polygons;
  the reader picks per drawing by how well traced walls cover the room boundaries.
- `plan.svg` for image plans is now a vector drawing (filled walls with openings, tinted room
  polygons with name and area, door and window symbols) on white; the scan stays `original.png`.
  Compare shows drawing against scan; the 3D view extrudes the wall polygons.
- Reviewer verdict in a browser: rebuilt plan, Compare and room polygons solved; 3D solved on the
  school plan, tall-looking on small nominal-scale plans. Known rough edges: 35 of 399 doors on
  the sample set wider than 1.5 m (oversized swing arcs), a filled shaft drawn as wall on one plan.
- Risk recorded by the reviewer: on scans without printed areas the nominal area becomes the
  preferred area (95th percentile scale error 46 %); it is labelled an estimate.

**Stream C — units (`on/c-units`)**

- One module (`backend/aura/units.py`) and one hook around the pipeline: a question can ask for
  mm, cm, m, km, in, ft, ft-in, yd, their squares, ha, acre, m³, l, cu ft, cu yd, in the five
  languages the router supports; quantities inside a question are converted ("larger than
  200 sq ft"); follow-ups ("and in inches?") convert the previous answer without a model call.
  Metric stays the default and is always shown beside the converted value.
- The cache stores metric answers only; conversion happens after it.
- Checked through a private instance: 341.42 m² → 3,675 sq ft; "and in inches?" → 529,202 sq in
  with 0 calls; hall perimeter 63.57 m → 208.6 ft; "larger than 200 sq ft" → 2 rooms.
- Not built: the originally printed imperial value on US sheets, a unit that sticks across turns,
  converted units in breakdown rows, evidence rows, voice and CSV.

**Stream D — printed dimensions (`on/d-dimensions`)**

- New reader `store/jstore/interpret/dimensions.py`: CAD DIMENSION entities, and a Gemini pass
  over tiles for PDFs and scans; values parsed as metric or feet-inches-fractions; stored as
  `dimension` entities with the printed text, metres, kind (linear, room size, overall, level)
  and the room they belong to; views `q.dimensions` and `q.room_sizes`; printed areas the room
  readers missed become `printed` claims on the room.
- Reviewer check: 8 of 10 stored values matched the sheet (2 illegible at crop size).
- Answering is only partly there: "longest dimension printed" works but with empty evidence;
  "overall width of the building" can show a row count as the answer; "how wide is the bedroom"
  abstains. Evidence boxes for dimensions are not drawn.
- Cost: up to 13 Gemini calls per page, about 30 s extra per scan without a scale.

**Merge (23:00)**: all four merged into `overnight-improvements`. Conflicts were in the
frontend (compare label, area formatting, `format.ts`) and the store (reader registration,
`q.items`, prompt). Suites on the merged tree: backend 678 passed / 34 skipped, store 147 passed /
1 skipped, frontend 342 passed, typecheck clean.


**Stream E — printed dimensions answer and show (`on/e-dimension-answers`)**

- A plain length question (overall width / depth / size, a room's width / length / size, longest or
  shortest printed dimension) is answered by `backend/aura/dimension_answers.py` with 0 model calls:
  the printed overall dimension, else a chain of printed dimensions summed (said so, and only when
  it agrees with the measured extent), else the extent of the walls / room outlines (labelled
  measured or estimate); a room from its printed size, else its rotated bounding box. Never a count:
  no data means an abstention with the reason, and tool steps refuse `finish_as=count` for a
  how long / wide / high question.
- The drawing document carries `dimensions` (text, metres, box and line on the page shown; on a PDF
  page without a frame the box comes from where the reader saw it), `rooms[].size`, `rooms[].room_type`
  and `extent_m`. The dimension used is the answer's evidence box (label = printed text).
- Imperial sheets: "80' - 0\" as printed = 24.38 m"; "in feet" converts through the units hook.
- Not built: width of a plan whose page has neither an overall dimension nor a frame (lower bound is
  stated); dimensions on other pages of a multi-page PDF are cited without a box; Finnish
  abbreviations the store vocabulary does not know (TUPAK, PH) still go to the tool steps.

### 23:05–23:30 UTC — stream F: polish of the vector plans of image drawings (`on/f-scan-polish`)

- Door symbols (`store/jstore/service/plan_symbols.py`): drawn from the detected shape and the walls. A single
  leaf up to 1.25 m, a double door up to 2.1 m when a wall runs beside the opening, otherwise the wall's own gap
  or a plain opening marker without a swing. Doors and windows away from every wall and room edge are not drawn
  (they stay in the evidence). The detection rectangle is no longer drawn; the wall is cut at a placed door.
- A room that lies mostly inside wall fill (a sauna or shaft drawn dark) opens the fill, in plan and 3D.
- Label size follows the height of a tall sheet. Label collisions on dense plans are not solved.
- Room lookup uses the store's room type: "living room / Wohnzimmer / séjour / soggiorno / olohuone" resolves to
  the label the store typed as that room ("OH", "MH", "K") when no printed word matches.
- Evidence rows of rooms measured under an estimated scale read "≈ 12 m² (estimate)".
- 3D: a plan with an estimated scale starts with walls at a fifth of its shorter side (1.2–2.6 m); the camera
  frames the walls as well as the footprint. Stated-scale plans keep 2.6 m.
- Fresh upload (a CubiCasa PNG not in the store) through the upload page: indexed, plan, Compare, 3D, a room
  area by room type and a door count all worked; nothing upload-specific had to be fixed.
- Deploy: no schema change, no re-interpretation; the cached plan.svg name was bumped so plans are redrawn.

### 23:00–23:45 UTC — live store re-interpreted, wave 2 merged, first deploy

- Backup: `/root/aura-deploy/backup/store-20261114T2301-pre-overnight.dump`.
- All 759 non-IFC drawings of the live store were re-interpreted with the new readers (dimensions
  reader off for this pass): 0 failures in 427 s, no new Gemini calls (cache). Result: 316 scans
  have walls (3,289 wall polygons), 6,290 of 6,449 rooms have polygon geometry, 218 scans carry a
  nominal scale and 98 a stated one.
- Streams E and F merged (conflict: one line in `aura_model.room_poly`). Suites on the merged
  tree: backend 699 passed / 34 skipped, store 158 passed / 1 skipped, frontend 346 passed.
- Deployed `537b0c6` to https://aura.finanzbetrug.at. Smoke test through the public API: the
  school plan's `plan.svg` is a vector drawing (741 paths, no embedded scan) and its rooms have up
  to 24 points; "largest room" is labelled vision; "area of the kitchen in square feet" carries the
  display unit; the hitos building total is 198 doors.
- A second pass runs the dimensions reader on a showcase subset of the live store (all CAD
  drawings, the US sheet PDFs, the school plan, 60 CubiCasa scans).

### 23:50–00:20 UTC — live walkthrough by two testers, fixes merged, second deploy

Two testers used the deployed site in a real browser (screenshots in `/root/aura-deploy/verify/walk1`
and `walk2` on the devbox). Verdict per reported item:

| Item | Verdict on the live site |
|---|---|
| 1. "All N floors" | No "Unknown project" anywhere. Counts right (hitos 198 doors / 226 windows, esplanades 211 / 297, a manual Swiss group 38 doors). Area questions in that scope abstained: fixed in the follow-up below. |
| 2. Compare for image imports | Vector drawing against the scan, aligned, on the school plan, three CubiCasa styles, two HABS and two Finna sheets and a fresh upload. |
| 3. 3D for image imports | Extruded walls and floors, no noise. Walls on estimated-scale plans start low (1.2–1.8 m). |
| 4. Room polygons | Rooms and highlights are polygons that follow the walls. Areas sensible on most plans; one plan (Colorful 2002) clearly too small under its estimated scale. |
| 5. Units | Works in the chat: sq ft, sq yd, ft, cm, German, quantities inside a question, follow-ups without a model call, metric beside the converted value. |
| 6. Printed dimensions | Worked on US sheets after the dimensions pass; failed or returned a count on bundled Swiss drawings: fixed below. |

Fresh upload on the live site (a never-seen PNG): openable after 2.3 s, indexed after 54 s
(19 model calls, $0.09); plan, Compare, 3D, largest room and door count worked; deleted again.

Fixed from the walkthrough (`on/g-walkthrough-fixes`, merged, deployed as `a3c5e98`):

- All-floors area questions sum each floor (Swiss group 453.12 m², hitos 8,351.15 m²), 0 model calls.
- Bundled CAD drawings answer length questions from their room outlines (they had no extent or room sizes).
- "Longest printed dimension" on a sheet without dimension lines abstains with the reason, never a count.
- An IFC building without windows answers "0 windows" with the reason instead of abstaining.
- IFC wall length without a Length quantity abstains instead of "0 ft, Exact".
- Estimated areas read "≈ 12 m² (estimate)" in the headline too.
- Phone header no longer clips; wording of unit notes.

Suites: backend 711 passed / 34 skipped, store 158 passed / 1 skipped, frontend 347 passed.

Dimensions pass on the live store: 201 showcase drawings in 25 minutes, 0 failures: 2,800
dimensions on 114 drawings (US sheets 2,200, Swiss CAD 1,638 counted per file join, CubiCasa 382).
A further pass over the remaining 276 image drawings with rooms started at 00:20.

Still open after this round: unlabelled spaces vanish from the vector plan (balcony, bathroom core
on one CubiCasa plan); the estimated scale is badly off on some plans; room widths on US PDFs
abstain; method labels and wording for printed against measured values; "Model estimate" badge on
printed areas of uploads.

### 00:25–00:50 UTC — wave 3 merged, third deploy

**Stream H — scan model quality (`on/h-scan-quality`)**
- Enclosed spaces without a label become rooms (classical free-space analysis, no model calls):
  on the CubiCasa plan "High quality 108" the balcony and the upper bathroom now show in plan and
  3D. One WC there stays a void; outlines are ragged around fixtures; false extra rooms are
  possible on dense or historic sheets (12 drawings were checked). Switch: `JSTORE_UNLABELLED_SPACES=0`.
- The estimated scale now combines printed areas, printed dimensions, door width, toilet,
  washbasin and bathtub sizes and room size by type (weighted median, outliers dropped); every
  estimator and the chosen value are recorded in the definition row. "Colorful 2002": living room
  11.95 → 17.8 m². On 104 live scans with a known scale the error barely moves (median 6.0 → 5.9 %,
  90th percentile 60.5 → 53.6 %): the gain is on outliers and on sheets that print one or two areas.
- Not done: wall stubs, walls across doors, duplicate door detections, label collisions, 3D slab.

**Stream I — answers and library (`on/i-answers-polish`)**
- A room with a label but no outline takes the dimension lines printed beside its label, quoted as
  printed (Cherokee utility room: 21' - 0 1/4" = 6.41 m); width is across the page, depth up the page.
  1810 S Park still abstains: its rooms are on another page than the one shown.
- New response field `basis` (printed / measured / estimate): printed values show "Read from the
  sheet · as printed", never "Model estimate".
- "convert that to inches" after an area explains that it converted to square inches.
- Library: source chips (Swiss Dwellings 65, IFC models 40, CubiCasa 241, US sheets 74, Historic
  scans 374, Other 1) beside the type chips; the 276 sheets without rooms or doors come after the plans.
- Not done: "0 rooms" for IFC models without spaces; a sweep of the suggested-question chips.

Merged and deployed as `0e056d9`. Suites: backend 716 passed / 34 skipped, store 160 passed /
1 skipped, frontend 351 passed. The live store's image drawings are re-interpreted with these
readers once the running dimensions pass ends.
