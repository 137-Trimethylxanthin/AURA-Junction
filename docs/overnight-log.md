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
