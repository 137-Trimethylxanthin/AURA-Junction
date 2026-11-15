# Overnight improvements (branch `overnight-improvements`)

Started 2026-11-14 22:10 UTC from `main` at cef4fa4. Hard stop 2026-11-15 06:00 UTC.
Each work stream runs in its own git worktree and branch, is verified on a private instance,
and is then merged here. This file is the running record: what was asked, what was done, how it
was checked, and what is still open.

## Result at a glance (written 02:15 UTC, last updated 05:55 UTC)

Branch `overnight-improvements`, deployed to https://aura.finanzbetrug.at as `61a1f53`. Not pushed
(no GitHub credentials on the devbox): `git push -u origin overnight-improvements`.
Suites on the final tree: backend 767 passed / 35 skipped, store 225 passed / 1 skipped,
frontend 365 passed, typecheck clean. Later rounds (PDF room outlines, a second scale estimator,
duplicate removal, Italian interface) and the open findings of the closing sweep are described at
the end of this file.

| Asked | State | Evidence |
|---|---|---|
| 1. "All N floors": `Unknown project` | Fixed | Browser and API: hitos 198 doors / 226 windows / 8,351.15 m²; two grouped Swiss floors 453.12 m² (= 344.58 + 108.54) and 38 doors; a store IFC building answers "0 windows" / "0 rooms" with the reason |
| 2. Compare for image imports | Fixed | The rebuilt side is a vector drawing made from the image (walls with openings, room polygons, door and window symbols); slider 0–100 %, aligned; checked on about 30 image drawings by two testers and an auditor |
| 3. 3D for image imports | Fixed | Walls extruded from the wall polygons on a footprint-shaped floor; wall height follows plan size where the scale is estimated |
| 4. Rectangular rooms | Fixed | Rooms from images are polygons with and without a stated scale; without one the scale is estimated and areas read "≈ 22 m² (estimate)" |
| 5. Units | Done | mm to km, in, ft, ft-in, yd, their squares, ha, acre, volumes; five languages; quantities inside a question; follow-ups convert without a model call; metric is the default and always shown |
| 6. Printed dimensions and areas | Mostly done | Dimensions on 248 drawings of the live store; length questions answer from the printed value ("21' - 0 1/4\" as printed = 6.41 m") with a highlight, else from outlines, never as a count; printed room areas are preferred and labelled "Read from the sheet". Rooms on US sheets without an outline often still abstain, with the reason |

Other fixes found on the way: inflated whole-building totals (990 for 198); budget refusals
cached and replayed; scan answers labelled exact; the landing page's sample button; `/api/api`
404s; two drawings that failed to load; suggested-question chips that led nowhere (21 → 0 in a sweep
of ten drawing kinds, by offering only what a drawing has); a library with source chips and plans
before non-plan sheets; PDF sheets shown as their page render instead of an unreadable wireframe.

**Known limits**
- The estimated scale of a scan without scale note, printed area or dimension is rough: median
  error about 6 %, one plan in ten off by more than 50 %. Areas from it are always marked "≈".
- Unlabelled spaces: a guard drops spaces that run along the page edge or are not mostly walled;
  it also drops real balconies bounded only by railings. `JSTORE_UNLABELLED_SPACES=0` turns the
  reader off for new interpretations.
- Detections drawn twice are drawn once but still counted twice: 88 door pairs of 6,806 detected
  doors and 130 window pairs of 6,440 windows in the live store overlap by more than 60 %.
- German, French and Finnish answer sentences are translated for the common cases; Italian and the
  composed dimension sentences are still English.
- A DWG site plan answers "1 door, Exact" (one CAD door entity in the store).
- Multi-page PDFs show one page (the header says which); door and window counts follow that page,
  room counts can still differ from the header (outdoor spaces left out, or a page without rooms
  answering for the whole document); dimensions on other pages are cited without a box.
- The answer cache was reset by the last deploy (cache key version 3); the main demo questions were
  asked once afterwards so they are warm.

**How to demo the new parts**
1. Library → source chip "CubiCasa" → open a plan (for example "High quality 108"): the plan is a
   vector drawing; "Compare with original"; 3D; "What is the area of the living room?" → "≈ 22 m²
   (estimate)"; "… in square feet?".
2. "Special school level00": "How many rooms are there?" → 82 (80 labelled, 2 without a label);
   "What is the circumference of the Multi-Purpose Hall in feet?" → 208.6 ft = 63.57 m.
3. An IFC building (hitos): choose "All 5 floors": doors 198, windows 226, total floor area.
4. US sheet "705 Cherokee Path": "What is the longest dimension printed on this sheet?" →
   21' - 0 1/4" as printed = 6.41 m, highlighted on the sheet.
5. A Swiss bundle: "What is the net floor area in square feet?", then "and in square yards?".

**Operations done on the live deployment tonight**
- Seven deploys of this branch (`537b0c6`, `a3c5e98`, `0e056d9`, `aa03080`, `9b0bacf`, `5094bef`, `db77e6b`).
- The live store was re-interpreted three times for image drawings (all from the Gemini cache) and
  twice with the dimensions reader (new model calls); false unlabelled spaces were pruned once.
  Backups: `/root/aura-deploy/backup/store-20261114T2301-pre-overnight.dump` and
  `store-20261115T0143-pre-prune.dump`.
- Deployment setting changed: `JSTORE_INTERPRET_TIMEOUT_S=420` (the dimensions reader needs it).
- Demo video re-cut: `/root/aura-deploy/demo/aura-demo.mp4` (84.9 s): scan scene shows the rebuilt
  plan, Compare and 3D; cost scene shows current figures (0 calls; 2 calls, $0.0015; running total).

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

### 00:50–01:15 UTC — wave 4 merged, live store re-interpreted again, fourth deploy

**Stream J — plan finish (`on/j-plan-polish`)**, all at drawing time (no re-interpretation):
labels at the pole of inaccessibility, sized to the room, dropped when they do not fit, never
overlapping; door and window openings cut out of the wall fill; loose wall slivers dropped;
duplicate door and window symbols drawn once; the 3D floor follows the building footprint;
unlabelled rooms straightened; the Compare slider runs 0–100 %. Left: stubs still attached to a
wall; store counts may be inflated by duplicate detections (drawn once, counted twice).

**Stream K — demo paths (`on/k-demo-paths`)**: suggested-question chips follow what a drawing has
(a sweep over ten drawing kinds: failing chips went from 21 to 2, both on the hitos IFC building:
"Match stairs and shafts across all floors" and "Show all stairs"); an IFC model without spaces
answers "0 rooms" with the reason; room counts read "10 rooms (8 labelled, 2 without a label)".
Not done: multi-page PDF handling beyond what already worked, source chip labels in other languages.

Merge note: both streams added an `unlabelled` field to a room; the duplicate keyword was caught
by the store suite and removed. Suites: backend 720 passed / 34 skipped, store 166 passed /
1 skipped, frontend 356 passed.

Live store: the dimensions pass over the remaining 276 image drawings finished (0 failures), then
all 316 image drawings with rooms were re-interpreted with the merged readers (0 failures, 9 min,
from cache). The store now holds dimensions on 248 drawings and 733 rooms without a label.

Deployed `aa03080`. Checked in a browser on the live site: the school plan and the CubiCasa plan
"High quality 108" render as clean vector plans (walls, door swings, tinted rooms, approximate
areas, the balcony and bathroom present), no console errors.

### 01:15–01:50 UTC — final check by a tester and an auditor, fix pass, fifth deploy

**Regression tester (live site, about 45 questions):** no must-level defect, no console errors, no
failed requests on main paths. All six items work; the one path the tester could not exercise
(saving a manual group writes data) was checked afterwards through the public API with an ad-hoc
project scope: two Swiss floors answer 453.12 m² = 344.58 + 108.54 and 38 doors, 0 model calls.

**Image-plan auditor (24 drawings opened, 36 more judged from overlays):** 9 good, 4 acceptable,
3 bad of the 16 audit picks. The bad ones had false rooms: the outside of a building, dimension
strips, the gap between two plans on one sheet. Two historic drawings failed to load.

**Fix pass (`on/l-final-fixes`, merged, deployed as `9b0bacf`):**
- `store/jstore/space_guard.py`: an unlabelled space is dropped when at least 15 % of its outline
  runs along the page edge, or under 60 % is wall, or under 75 % wall with under 50 % along
  labelled rooms. Store-wide it drops 314 of 733 spaces on 135 drawings; the balcony and bathroom
  of "High quality 108" stay. Applied when a document is built and after new interpretations; the
  live store was pruned once with `python -m jstore.cli prune-spaces` (marks rows `superseded`,
  undone with `--restore`; backup `store-20261115T0143-pre-prune.dump`).
- The two drawings that failed to load (`place_labels` crashed on a self-crossing outline) load; a
  sweep over all 329 image plans had no failures.
- "≈" stays on converted estimates ("≈ 240 sq ft"); internal entity ids no longer appear in answer
  sentences; IFC models no longer offer the two stair chips that failed; US sheets with rooms sort
  before door-only sheets.
- Known cost of the guard: a real unlabelled balcony bounded only by railings is dropped too.

Suites: backend 722 passed / 34 skipped, store 173 passed / 1 skipped, frontend 356 passed.

Demo video: the scan scene was re-recorded on the live site to show the rebuilt plan, Compare and
3D (`/root/aura-deploy/demo/aura-demo.mp4`, 84.7 s; the earlier cut is kept as `aura-demo-v1.mp4`).

### 01:50–02:15 UTC — last polish, sixth deploy

`on/m-last-polish`, merged and deployed as `5094bef`: a PDF sheet without a CAD file shows its page
render as the plan (evidence boxes on top, Compare hidden); the largest-room sentence, the outdoor
note and the "0 windows" reason are translated (de/fr/fi); German thousands match the headline; IFC
evidence rows show room name and number instead of the GUID; a placeholder IFC project name
("Default Project") is replaced by the model name; "(1 outdoor space not counted)" explains a
room count below the header. Suites: backend 723 passed / 34 skipped, store 173 passed /
1 skipped, frontend 356 passed.

After the deploy the main demo questions were asked through the public API (all answered, most
with 0 model calls) and three drawings were opened in a browser (a US PDF sheet, a CubiCasa plan,
an IFC storey): no console errors.

### 02:15–02:50 UTC — regression sweep of the final deploy, last fixes, seventh deploy

**Sweep (one tester, live site):** no must-level problem. 28 drawings opened across every kind
with two suggested questions each; Compare and 3D on three scans; German, Finnish and a phone
viewport; 157 library entries through the API with no failure and nothing slower than 0.9 s; the
five demo walkthroughs above give the numbers stated; 0 console errors, 0 failed requests.

**Four should-level findings, fixed in `on/n-regressions` (merged, deployed as `db77e6b`):**
- 3D is no longer offered where there is no wall model (PDF sheets shown as their page render):
  it had fallen back to the old image relief and extruded detail drawings into towers.
- Multi-page PDFs: header, library card and answers count the page shown and say which page
  ("page 7 of 16 · 22 rooms · 15 doors · 52 windows"). Room counts can still differ (see limits).
- A DWG that rendered as a speck (far-away stray entities blew up the extent) fills the page.
- "What does this drawing show?" is answered from what the store knows, 0 model calls, also in German.

Suites: backend 746 passed / 34 skipped, store 176 passed / 1 skipped, frontend 357 passed.

After the deploy 29 demo questions were asked through the public API: all answered, 28 with no
model call (this also warms the cache that a deploy with new document versions resets).

Left as found by the sweep, lower priority: header counts line, source chips and "sq ft" stay
English in the German and Finnish UI; one building's all-floors evidence list is incomplete while
its totals are right; a few answers show internal wording ("Rule: the cell perimeter_m = …");
library thumbnails of scans show the scan, not the rebuilt plan.

### Branches

`overnight-improvements` holds everything. The stream branches (`on/a-backend-fixes`,
`on/b-scan-vector`, `on/c-units`, `on/d-dimensions`, `on/e-dimension-answers`, `on/f-scan-polish`,
`on/g-walkthrough-fixes`, `on/h-scan-quality`, `on/i-answers-polish`, `on/j-plan-polish`,
`on/k-demo-paths`, `on/l-final-fixes`, `on/m-last-polish`, `on/n-regressions`) are merged into it
and exist only locally; their worktrees are under `/root/aura-work/` on the devbox.

### 02:50–03:10 UTC — wave 5: PDF rooms and pages, scale by a second approach, duplicates, language

**Stream O — PDF sheets (`on/o-pdf-rooms`)**
- Two approaches for outlining rooms on a PDF sheet were compared on six US sheets. Outlining on
  the page render with the scan method (a model-drawn polygon per labelled room, snapped to drawn
  lines) won clearly: rooms whose outline is within 10 % of their printed area: Cherokee 6 of 6,
  1810 S Park 9 of 9 and 3 of 3 and 7 of 8, Xcel 18 of 18, Laundry Mat 4 of 4. The existing vector
  free-space method on the same sheets: 1 of 6 on Cherokee, 0 of 18 on Xcel.
- Answers that abstained now work with no model call: "How wide is the bedroom?" 5.21 m (estimate,
  traced on the sheet image), office 4.91 × 5.11 m, utility 24.90 m² as printed, repair garage
  13.55 × 8.71 m; the room polygon is highlighted on the page.
- Each further page of a multi-page PDF that has content is its own library entry ("… · page 6",
  "page 6 of 13 · 3 rooms · 2 doors"); a set opens on the page with the most rooms. Pages are not
  grouped as floors, because project answers would sum rooms repeated on several sheets.
- Not working: "How wide is the stair?", "… the mezzanine?" (label "MEZZ.").

**Stream P — scans (`on/p-scale-dedupe`)**
- Scale by a second approach: one model request per drawing names 8–16 objects with their real
  size and the real width of the whole image. Error of metres per pixel on 127 live scans with a
  stated scale (printed areas withheld):

  | Estimator | median | 75th | 90th | off by more than 25 % |
  |---|---|---|---|---|
  | before (doors, fixtures, room sizes) | 6.5 % | 14.6 % | 52.3 % | 16.5 % |
  | model, median of objects | 2.9 % | 5.2 % | 13.2 % | 4.7 % |
  | adopted: model as one more voter | 2.3 % | 5.0 % | 10.7 % | 4.7 % |

  Caveat: these sheets state a scale, so the model may lean on scale bars or dimensions that
  scale-less scans lack; with such objects dropped the figures are 2.9 % / 14.4 %.
- Duplicate detections: the pairs are nested boxes (a run of windows boxed again, a shower head
  inside its cubicle). They are removed at detection time and by
  `python -m jstore.cli dedupe-detections` (reversible with `--restore`; CAD entities untouched).
  On the live store: 86 entities on 37 drawings (30 doors, 40 windows, 16 showers). Not validated
  against CAD truth.
- Not done: rescuing real balconies dropped by the false-room guard (a door-adjacency rule would
  restore 46 of 314, some of them doubtful).

**Stream Q — language and UI (`on/q-language-ui`)**
- A manual group saved through the UI was checked in a browser on a private instance: two Swiss
  floors, "All 2 floors": 38 doors, 453.12 m²; the same after a reload; ungroup removes it.
- Counts line, "page N of M", source chips and aria labels follow the interface language (de/fr/fi);
  Italian answer sentences for room counts, ranking and "0 … model file contains no …". The
  interface itself has no Italian; composed dimension sentences are still English.
- SQL-path answers explain themselves in plain sentences ("This is the perimeter of one room. It
  was measured on the room outline traced from the scan, so it is an estimate.") and highlight the
  room; the rule and the SQL stay in the reasoning tree.
- Molio all-floors evidence is complete (119 rows); library thumbnails of scans show the rebuilt plan.

Merged and deployed as `805eaae` (suites: backend 752 passed / 34 skipped, store 189 passed /
1 skipped, frontend 360 passed). On the live store: backup `store-20261115T0308-pre-wave5.dump`;
duplicates removed (86); re-interpretation started for the US sheet PDFs (new model calls for room
outlines) and for the 189 image drawings with an estimated scale (one model call each).

### 03:10–03:40 UTC — Italian interface, live store updated, eighth and ninth deploy

- `on/r-italian` (merged): an Italian interface (every string, switcher, contract language);
  dimension answers in en/de/fr/fi/it (58 sentences in `backend/aura/dimension_text.py`); the
  scale footnote in the plan follows the interface language; room lookup understands sheet
  abbreviations ("STAIR", "MEZZ.", "ELEV.", "BDRM", "KIT." ...) at question time: "How wide is the
  stair?" 4.51 m, the mezzanine 8.02 m on the live store. Still English: reasoning-tree node
  titles, credit lines, a few evidence-row fragments.
- Live store: the 7 showcase PDFs and the other 34 US sheet PDFs were re-interpreted (room outlines
  on the page render), then the 189 image drawings with an estimated scale (model voter), then the
  duplicate and false-room clean-ups: 0 failures in every step.
- Deployed `f5aa2e9` (answer cache version 5). Suites: backend 763 passed / 34 skipped, store 224
  passed / 1 skipped, frontend 363 passed. The library lists 828 entries (page entries of
  multi-page PDFs included).
- Checked through the public API after the deploy (36 questions, 35 with no model call): the US
  sheet "705 Cherokee Path" now answers "How wide is the bedroom?" 5.21 m, "dimensions of the
  office" 4.90 × 5.11 m, "area of the utility room" 24.90 m², "largest room" UTILITY 24.90 m²;
  everything listed in the walkthroughs above still answers the same, except the living room of
  "High quality 108", which reads ≈ 21 m² under the new scale estimate (was ≈ 22 m²).

### 03:40–05:55 UTC — closing check; final state

**Closing sweep (one tester, live site `f5aa2e9`):** no must-level problem; the five demo
walkthroughs and the six items give the stated numbers; about 45 drawing loads with 0 console
errors, 0 failed requests; 200 library entries through the API without failure. Scan areas after
the new scale estimate are plausible over all 241 CubiCasa documents (bedroom median 11.3 m²,
living room 19.8 m², toilet 2.2 m²). PDF sheets: 13 questions on 8 drawings answered with no model
call and the room highlighted at the right place.

**Should-level findings of the closing sweep, NOT fixed in the deployed version:**
- School plan, "circumference of the Multi-Purpose Hall in feet": the number is right (208.6 ft =
  63.57 m) but the sentence still shows internal wording ("Rule: the cell perimeter_m = …") and
  the room is not highlighted on this drawing.
- With the "CubiCasa" source chip active the IFC project tiles stay on top; "High quality 108" is
  the 15th card (type "108" in the search box instead).
- Three CubiCasa plans are rebuilt as fragments with implausible areas: avoid
  `st-high-quality-architectural-12166-d232fe37`, `st-high-quality-architectural-7838-74949bcd`,
  `st-colorful-11960-a0cad0a3`.
- Page entries of one PDF cannot be told apart on library cards (title and subtitle are cut).
- "Largest room" on an IFC building answers with a long per-storey sentence containing IFC GUIDs.

**Fix pass interrupted:** the agent working on these findings stopped when the account's model
usage limit was reached. Its partial work is on the local branch `on/s-final` (worktree
`/root/aura-work/s-final`, 4 commits) and is NOT merged or deployed: it was not reviewed and its
suites were not re-run, and the hard stop was minutes away. Review before merging.

**Demo video, final cut:** `/root/aura-deploy/demo/aura-demo.mp4` (86.3 s) and
`aura-demo-720p.mp4`. The library scene was re-recorded (828 drawings, source chips, rebuilt-plan
thumbnails); every caption was re-checked against the live site. Earlier cuts are kept as
`aura-demo-v1/v2/v3.mp4`.

**Final state at the hard stop:** branch `overnight-improvements`; deployed commit `f5aa2e9`
(code) — later commits on the branch are documentation only. Suites on the deployed tree: backend
763 passed / 34 skipped, store 224 passed / 1 skipped, frontend 363 passed. Not pushed.

### After 05:55 UTC — the interrupted fixes reviewed, merged and deployed; two more fixes

The work continued inline (no subagents: the model usage limit was reached).

- `on/s-final` (the four commits of the interrupted agent) was reviewed, its suites run (backend
  767 passed, store 224, frontend 365) and merged: a source or file-type chip lists only the
  projects of that source; library card titles and subtitles wrap, so page entries can be told
  apart; "largest room" over a building names one winner without IFC GUIDs and leaves gross-area
  spaces out; similar-question reuse ignores answers stored under an older cache key version.
- Explicit "All N floors" scope: "Which room is the largest / smallest?" abstained; each floor is
  now ranked and the extreme winner named with its floor ("Boksamling … (1.etg) is the largest
  room: 459.70 m²"), 0 model calls; test on the hitos bundles.
- School plan circumference (demo step 2): on the current deploy the answer reads "Circumference of
  the Multi-Purpose Hall: 208.6 ft (63.57 m). This is the perimeter of one room. It was measured on
  the room outline traced from the scan, so it is an estimate." with evidence; the internal wording
  the closing tester saw came from an answer cached before the wording fix (the cache key version
  was bumped since).
- Image plans read as a fragment: when the rooms read on a scan cover under 40 % of the area its
  detected doors, windows and fixtures spread over, the scan stays the plan instead of a rebuilt
  corner of the building. On the live store this applies to 6 of 315 image plans with rooms,
  including `st-high-quality-architectural-12166-d232fe37`. The other two reported plans
  (`st-high-quality-architectural-7838-74949bcd`, `st-colorful-11960-a0cad0a3`) are not caught:
  their labels were read too sparsely by the label reader; avoid them in a demo.

Deployed `61a1f53`. Suites: backend 767 passed / 35 skipped, store 225 passed / 1 skipped,
frontend 365 passed. Checked through the public API after the deploy: the walkthrough questions
answer as stated, all with 0 model calls.

**Still open:** the two sparse CubiCasa plans above; real balconies bounded only by railings are
dropped by the false-room guard; reasoning-tree node titles and credit lines stay English in
other interface languages; a DWG site plan answers "1 door, Exact"; the video's cost scene shows
the running total at recording time.
