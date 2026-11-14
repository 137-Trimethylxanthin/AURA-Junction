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

