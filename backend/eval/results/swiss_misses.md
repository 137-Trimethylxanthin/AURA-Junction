# Swiss misses: causes (2026-11-14)

Source: `eval/results/swiss.json`, production solver, 186 misses out of 18,792 English questions (18,606 correct).
No rule has been changed. Every gain/loss figure below was measured on the full set of 18,792 questions with a
throwaway script, and every proposal is **fitted on this same set**: if one is adopted, report it the way IFC is
reported ("rules added after reading the misses on this set; with the original rules: 18,606/18,792").

## Misses by family

| Family | Misses | n |
|---|---|---|
| floor area | 76 | 1,040 |
| apartment area | 75 | 1,875 |
| room area | 10 | 2,076 |
| washbasins | 10 | 1,040 |
| rooms by label | 5 | 6,365 |
| bathtubs | 3 | 897 |
| WCs | 3 | 1,040 |
| showers | 2 | 322 |
| label area | 2 | 1,017 |
| apartments, doors, windows | 0 | 3,120 |

All 76 floor-area misses and 73 of the 75 apartment-area misses are over-counts.

## Causes of the 151 area misses

Each miss was explained by the smallest set of 1–2 rooms whose areas add up to the difference (within 0.5 %).

| Cause | Floor | Apt | Example (questions.jsonl line: gold → ours) |
|---|---|---|---|
| A named room with no printed area (outline area used) is in the sum but not in the gold. For 29 of the floor cases, two room numbers (typically stairwell and lift) resolve to **one shared outline**, so its area is summed twice (fix P1) | 47 | 15 | 90177 site1008 floor: 303.64 → 326.33 ("Treppenhaus" 4.30 and "Lift" 4.31 both resolve to the same 21.17 m² outline) |
| A room with **no name and no printed area** (only a number; probably a shaft or void) | 2 | 41 | 91543 site1005 apt H: 52.17 → 55.53 (room 2.50, unnamed, 3.36 m²) |
| **Garten / Wintergarten / Patio** counted as indoor (the outdoor regex only knows balcony/loggia/terrace/Sitzplatz) | 8 | 11 | 114139 site11680 floor: 139.94 → 167.07 |
| Named room with a printed area (Zimmer, Pièce, Cuisine, Korridor, Bad, Ascenseur…), 1–2 rooms, sometimes with an unnamed one | 16 | 4 | 123615 site246 floor: 369.48 → 385.94 |
| No 1–2 room subset explains it (incl. site3418, where a room number falls inside a floor-sized outline: 898.89 vs 113.39) | 3 | 2 | 117943 site3418 floor |
| Under-count | 0 | 2 | 115807 site1273 apt C: 261.05 → 231.93 |

## Causes of the other 35 misses

| Cause | Misses | Example |
|---|---|---|
| Washbasin over-count: a larger fixture rectangle overlaps a basin and also picks up the basin's ellipse and dot marks, so both count as basins. The number of extra basins equals the number of overlapping rectangle pairs in all 7 drawings. | 7 | site778: gold 3 → 6 |
| WC +1 and washbasin −1 on the same drawing: probably one basin whose dot mark is missing, so it reads as a WC (not checked by eye) | 3 drawings, 6 misses | site194: WC 3 → 4, basins 5 → 4 |
| Bathtub −1 and shower +1 on the same drawing: probably a tub without its tub mark (not checked by eye) | 2 drawings, 4 misses | site317: tubs 6 → 5, showers 2 → 3 |
| One bathtub missed, no other change | 1 | site86: 6 → 5 |
| Room with no printed area whose outline area is 3–20 % above the gold (6 stairwells, 2 corridors, 1 Reduit). In 2 of them the outline is shared with another room number | 9 of 10 room-area misses | site853 room 2.42 "Treppenhaus": 14.04 → 16.87 |
| Room number inside a floor-sized outline shared by 5 numbers (site3418) | 1 room area + 1 label area | 2.12 "Treppenhaus": 7.56 → 210.4 |
| Label count +1: in 3 of the 5 cases the extra room is an unprinted second number in an outline another room already has | 5 | site253 "Cage d'escalier": 2 → 3 |
| Label area, site593 "Zimmer" | 1 | 123.14 → 131.57 |

## Proposed fixes (measured on all 18,792 questions; the lead decides)

| # | Fix | Bug or convention | Gain | Loss | Net |
|---|---|---|---|---|---|
| P1 | In area sums, count an outline once when several room numbers without a printed area resolve to the same outline | **Solver bug** (the same polygon's area added twice) | +29 floor | 0 | **+29** |
| P2 | Treat Garten / Wintergarten / Patio (and jardin / giardino) as outdoor | Dataset convention ("other outdoor spaces"); a Wintergarten is arguably indoor, but the gold excludes it | +8 floor, +11 apt | 0 | **+19** |
| P1+P2 | Both | | +37 floor, +11 apt | 0 | **+48 → 18,654 / 18,792 = 99.26 %** |
| P3 | An ellipse or dot mark belongs only to the smallest fixture rectangle that contains it (bathtub and burner marks unchanged) | Solver bug (one mark counted by two rectangles) | +7 basins | −1 WC | **+6** |

Tested and **not** proposed, because they lose more answers than they gain:
- Exclude rooms with no name and no printed area: apartments +36 / −154, floors +25 / −123. The gold sometimes includes these rooms and sometimes not, and the DXF text alone can't tell a shaft from a room without a label.
- Exclude every room with no printed area: floors +8 / −732.
- Exclude lifts: floors +26 / −27.
- Don't count an unprinted duplicate number in a shared outline when counting labels: +3 / −5. For counts the gold keeps both labels; for areas it counts the outline once.
- A fixture rectangle ignores marks inside a smaller rectangle it contains (the containment version of P3): +2 basins, −1 WC, −1 bathtub.

If adopted, P1 and P2 change only area sums: no count, no box and no evidence id changes. P3 changes basin and WC boxes on the affected drawings only. The bundled swiss-46639 has no shared outlines and no garden rooms, so P1 and P2 cannot change it. None of its washbasin/WC/bathtub/shower rectangles overlap, so P3 should leave it unchanged too. `diff_drawing.py` would confirm that before any regeneration.

## Decision (Sat 14 Nov, measured by eval/swiss_full.py)

All four rules are adopted (aura/solvers/swiss.py `ADOPTED`). Each was measured alone against the original probe rules: P1 shared outline +29/−0, Garten/Patio (with jardin, giardino) +13/−0, Wintergarten +11/−0, fixture marks to the smallest rectangle +7/−1. Together: 18,660/18,792 (+55, −1). Wintergarten: 5 rooms in 5 drawings. On each drawing it is labelled like that drawing's own outdoor rooms: on 4 drawings neither the Wintergarten nor the balconies/terraces print an area (outline only), and on the fifth both are printed. Where it decides the answer, the gold equals the solver's sum minus exactly the Wintergarten outline (186.29 vs 186.30, 113.01, 344.56, 51.50), so the gold treats it as an outdoor space; no question goes the other way. The one loss (site790_bldg1731_floor7704, WC 10 → 9) is a WC drawn as two nested rectangles around the same two marks; the gold counts it twice, the rule counts one fixture. The original figure stays reproducible with `--probe-rules` (18,606/18,792). These rules were fitted on this same set, so the new figure is not a held-out measure.

P3 loss, looked at (DXF overlay rendered at 300 px/m, .omc/research/p3/site790_wc.png): it is ONE WC position drawn as two superimposed WC symbols (rectangles 0.84 × 0.59 m and 0.52 × 0.53 m sharing the left edge, each with its own nearly concentric ellipse and tank line, overlapping in a ~1 m wide bathroom); two toilets cannot occupy that space, so P3 stays. The gold counts both copies, as it does for doors drawn twice.
