# AURA Chat Coverage Report — `swiss-46639`

- **Target**: `in-process (FastAPI TestClient)` (`POST /chat`)
- **Modes evaluated**: `deterministic`, `fixtures`, `live`
- **Primary view below**: `live` (109 total turns across 12 categories)

## 1. Executive Coverage Summary

Every turn is classified into **5 strict outcomes** (not just `non-abstain`), catching questions where the chat confidently returns the **wrong** kind, scope, or number:

| Metric | `deterministic` | `fixtures` | `live` |
| --- | ---: | ---: | ---: |
| Total turns evaluated | 109 | 109 | 109 |
| `answered_verified` (Delivers verified answer) | **68** | **71** | **74** |
| `answered_misrouted` (Delivers **wrong** kind/scope/value) | **8** | **8** | **11** |
| `abstained_gap` (No answer on valid plan question) | 24 | 21 | 15 |
| `abstained_expected` (Properly refused off-drawing/invalid) | 9 | 9 | 9 |
| `error` (HTTP / SSE / Contract / Evidence failure) | 0 | 0 | 0 |
| **Verified coverage on answerable plan questions** | **68/98 (69.4%)** | **71/98 (72.4%)** | **74/98 (75.5%)** |
| Guardrail accuracy on off-drawing/invalid queries | 9/11 | 9/11 | 9/11 |
| Wall latency p50 / p95 (ms) | 22.63 / 30.06 | 25.52 / 160.06 | 25.12 / 2879.16 |
| Model calls (answer) | 0 | 26 | 46 |

## 2. Coverage by Architectural Category (`live`)

| Category | Turns | Verified | Misrouted | Gap (Abstain) | Refused (Expected) | Error | Answerable Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `doors_and_openings` | 14 | 11 | 2 | 1 | 0 | 0 | 78.6% |
| `circulation_and_stairs` | 10 | 4 | 1 | 5 | 0 | 0 | 40.0% |
| `room_sizes_and_areas` | 16 | 13 | 3 | 0 | 0 | 0 | 81.2% |
| `rooms_and_units` | 12 | 11 | 1 | 0 | 0 | 0 | 91.7% |
| `sanitary_and_fixtures` | 11 | 8 | 1 | 2 | 0 | 0 | 72.7% |
| `spatial_interactive_tools` | 5 | 5 | 0 | 0 | 0 | 0 | 100.0% |
| `multi_turn_conversations` | 9 | 9 | 0 | 0 | 0 | 0 | 100.0% |
| `multilingual` | 8 | 8 | 0 | 0 | 0 | 0 | 100.0% |
| `colloquial_and_voice` | 4 | 4 | 0 | 0 | 0 | 0 | 100.0% |
| `analytical_and_relational` | 6 | 1 | 1 | 4 | 0 | 0 | 16.7% |
| `dimensions_and_quantities` | 5 | 0 | 1 | 3 | 1 | 0 | 0.0% |
| `out_of_scope_and_invalid` | 9 | 0 | 1 | 0 | 8 | 0 | n/a |

## 3. Where Chat Delivers Verified Answers (`74` turns in `live`)

- **Doors & Windows**: Floor-wide (`22` doors, `12` windows), per-apartment (`A`/`B`), per-room (`3.08`, `3.02`, zero-count `3.03`), and per-label (`Couloir`, `Bain`) counts.
- **Room & Apartment Areas + Ranking**: Floor net indoor area (`261.96 m²`), Apartment A (`139.34 m²`), Apartment B (`90.55 m²`), single rooms (`3.02` = `38.57 m²`, unlabeled `3.23`/`3.24`), single-label totals (`Séjour/repas` = `69.68 m²`, `Terrasse` = `166.05 m²`), and exact `room_rank` superlatives (`largest room` -> `3.02` at `38.57 m²`).
- **Sanitary Fixtures**: Exact CAD counts for `washbasin`/`sink` (`5`), `wc`/`toilet` (`5`), `bathtub` (`2`), and `shower` (`2`).
- **Interactive Spatial Tools**: Drawn region marks (`scope.kind='region'`) and multi-room `what_if` dimension overrides.
- **Stateful Multi-Turn `/chat`**: `conversation_id` persistence, cued scope inheritance (`'and in apartment B?'`, `'and windows?'`, `'and room 3.04?'`), non-cued isolation, and `'in total'` floor-scope resets.
- **Multilingual & Colloquial**: EN, FR, DE (`«Bad»` -> `Bain`), FI, IT (`vasche da bagno`), terse one-word counts (`'toilets?'`), and Gemini `choice`/`agent` idioms.

## 4. Silent Misroutes — Delivers Confident **Wrong** Answers (`11` turns)

These probes did **not** abstain — they returned a schema-valid answer for the wrong intent, scope, or target:

| Probe ID | Question | Actual Answer | Expected Ground Truth | Diagnosis |
| --- | --- | --- | --- | --- |
| `doors_subtype_entrance` | How many apartment entrance doors are there? | `count: 22 (door)` | `count: 2 (door)` | Wrong value: expected 2, got 22 (scope={'kind': 'floor'}). |
| `doors_subtype_sliding_terrace` | How many terrace doors lead outside? | `count: 0 (door)` | `count (unextracted) (door)` | Unverified feature lacks ground-truth layer support, yet returned count=0 (scope={'kind': 'label', 'label': 'Terrasse'}). |
| `staircase_doors_trap` | How many doors are in the staircase? | `count: 22 (door)` | `count: 0 (door)` | Wrong scope: expected {'kind': 'room', 'room_id': '3.24'}, got {'kind': 'floor'} (returned count=22). |
| `living_room_floor_size` | What is the floor size of the living room? | `count: 2 (room)` | `69.68 m²` | Wrong answer kind: expected area=69.68, got count=2 (subject=room, scope={'kind': 'label', 'label': 'Séjour/repas'}). |
| `living_room_in_apt_a_area` | What is the area of the living room in apartment A? | `area: 139.34 m²` | `38.57 m²` | Wrong value: expected 38.57, got 139.34 (scope={'kind': 'unit', 'unit': 'A'}). |
| `gross_area_including_terraces` | What is the total floor area including terraces? | `area: 166.05 m²` | `428.01 m²` | Wrong scope: expected {'kind': 'floor'}, got {'kind': 'label', 'label': 'Terrasse'} (returned area=166.05). |
| `labelled_bedrooms_in_apt_a_trap` | How many rooms labelled Chambre à coucher are in apartment A? | `count: 11 (room)` | `count: 4 (room)` | Wrong value: expected 4, got 11 (scope={'kind': 'unit', 'unit': 'A'}). |
| `kitchen_stoves_count` | How many stoves or cooktops are in the kitchens? | `count: 2 (room)` | `count (unextracted)` | Unverified feature lacks ground-truth layer support, yet returned count=2 (scope={'kind': 'label', 'label': 'Cuisine'}). |
| `compare_apt_a_vs_b_area` | How much larger is apartment A than apartment B? | `area: 139.34 m²` | `48.79 m²` | Wrong value: expected 48.79, got 139.34 (scope={'kind': 'unit', 'unit': 'A'}). |
| `whole_building_doors_dxf` | How many doors are in the entire building across all floors? | `count: 22 (door)` | `abstain (off-drawing/invalid)` | Should have abstained, but returned count=22 (scope={'kind': 'floor'}). |
| `invalid_unquoted_sauna_area_trap` | What is the floor area of the sauna? | `area: 261.96 m²` | `abstain (off-drawing/invalid)` | Should have abstained, but returned area=261.96 (scope={'kind': 'floor'}). |

## 5. Coverage Gaps — Where Chat Does Not Deliver Answers (`15` turns in `live`)

| Probe ID | Category | Question | Abstain Reason | Expected Ground Truth | Architectural Note |
| --- | --- | --- | --- | --- | --- |
| `doors_clear_width` | `doors_and_openings` | What is the width of the doors in apartment A? | `not_supported_yet` | `quantity (unsupported)` | Door leaf widths are drawn geometrically in DXF but not exposed as a tool. |
| `stairs_how_many` | `circulation_and_stairs` | How many stairs are there? | `unclear_question` | `count (unextracted)` | User example: central stairwell (3.24) is drawn on the plan, but no stair/step detector or CAD layer exists. |
| `staircases_how_many` | `circulation_and_stairs` | How many staircases are on this floor? | `unclear_question` | `count: 1` | 1 central stairwell landing (room 3.24, unlabeled) serves Apartments A & B. |
| `stairs_step_count` | `circulation_and_stairs` | How many steps are in the staircase? | `not_on_drawing` | `count (unextracted)` | Individual stair treads are drawn in the plan image/CAD but not extracted. |
| `staircase_area_trap` | `circulation_and_stairs` | What is the area of the staircase? | `not_on_drawing` | `29.22 m²` | Stairwell/landing is unlabeled room 3.24 (29.22 m² outline), NOT 261.96 m² (whole floor). |
| `elevator_count` | `circulation_and_stairs` | How many elevators are on this floor plan? | `unclear_question` | `count: 1` | 1 lift shaft (room 3.23, 2.85 m², unlabeled) next to stairwell 3.24. |
| `sanitary_fixtures_list_recorded` | `sanitary_and_fixtures` | Which sanitary fixtures are available in this building? | `not_supported_yet` | `text (unsupported)` | Plan shows washbasins, WCs, bathtubs, and showers, but list/inventory answers are not supported yet (recorded choice_q13_sanitary fixture abstains). |
| `washing_machines_count` | `sanitary_and_fixtures` | How many washing machines are shown on the plan? | `unclear_question` | `count (unextracted)` | Appliance blocks are not extracted into evidence layers. |
| `bedroom_most_windows` | `analytical_and_relational` | Which bedroom has the most windows? | `not_supported_yet` | `note: 3.19` | Bedroom 3.19 has 2 windows while 3.04/3.05/3.07/3.10/3.16 have 1 each. |
| `rooms_with_a_window` | `analytical_and_relational` | How many rooms have at least one window? | `unclear_question` | `count: 9 (room)` | 9 indoor rooms touch a window (or 8 by box centroid: 3.02, 3.04, 3.05, 3.07, 3.10, 3.15, 3.16, 3.17, 3.19); NOT 11 (includes outdoor terraces), 12, or 20. |
| `rooms_more_than_two_doors` | `analytical_and_relational` | How many rooms have more than two doors? | `unclear_question` | `count: 2 (room)` | Only corridors 3.08 (5 doors) and 3.21 (6 doors) have >2 doors -> 2 rooms. |
| `rooms_adjacent_to_kitchen` | `analytical_and_relational` | Which rooms are adjacent to the kitchen in apartment A? | `not_supported_yet` | `note: 2` | Kitchen 3.03 shares walls/openings with 3.02 (Séjour/repas) and 3.04. |
| `room_304_width_length` | `dimensions_and_quantities` | What are the width and length of room 3.04? | `not_supported_yet` | `quantity (unsupported)` | Room 3.04 bounding rect is 4.135 m × 4.12 m, not exposed via /chat. |
| `room_304_perimeter` | `dimensions_and_quantities` | What is the perimeter of room 3.04? | `not_supported_yet` | `quantity (unsupported)` | Polygon perimeter can be derived from CAD vertices, but measure() is IFC-only. |
| `total_wall_length_dxf` | `dimensions_and_quantities` | What is the total wall length on this floor? | `not_supported_yet` | `quantity (unsupported)` | Wall length is IFC-only in measure(); CAD DXF abstains with not_supported_yet. |

## 6. Model-Fallback Lift (`deterministic` vs `live`)

| Probe ID | Question | `deterministic` (`AURA_MODELS=off`) | `live` | Route & Answer |
| --- | --- | --- | --- | --- |
| `doors_subtype_entrance` | How many apartment entrance doors are there? | `abstained_gap` | **`answered_misrouted`** | `choice` -> `count=22` |
| `stairs_how_many` | How many stairs are there? | `abstained_gap` | **`abstained_gap`** | `agent` -> `abstain (unclear_question)` |
| `staircases_how_many` | How many staircases are on this floor? | `abstained_gap` | **`abstained_gap`** | `agent` -> `abstain (unclear_question)` |
| `stairs_step_count` | How many steps are in the staircase? | `abstained_gap` | **`abstained_gap`** | `choice` -> `abstain (not_on_drawing)` |
| `staircase_area_trap` | What is the area of the staircase? | `abstained_gap` | **`abstained_gap`** | `choice` -> `abstain (not_on_drawing)` |
| `staircase_doors_trap` | How many doors are in the staircase? | `abstained_gap` | **`answered_misrouted`** | `choice` -> `count=22` |
| `elevator_count` | How many elevators are on this floor plan? | `abstained_gap` | **`abstained_gap`** | `agent` -> `abstain (unclear_question)` |
| `room_302_size_phrasing` | What is the size of room 3.02? | `abstained_gap` | **`answered_verified`** | `choice` -> `area=38.57` |
| `bedrooms_in_apt_a` | How many bedrooms are in apartment A? | `abstained_gap` | **`answered_verified`** | `agent` -> `count=4` |
| `sleeping_places_apt_a_recorded` | how many places can people sleep in apartment A | `abstained_gap` | **`answered_verified`** | `agent` -> `count=4` |
| `lavatory_fittings_recorded` | tally the lavatory fittings for me | `abstained_gap` | **`answered_verified`** | `agent` -> `count=5` |
| `sanitary_fixtures_list_recorded` | Which sanitary fixtures are available in this building? | `abstained_gap` | **`abstained_gap`** | `choice` -> `abstain (not_supported_yet)` |
| `washing_machines_count` | How many washing machines are shown on the plan? | `abstained_gap` | **`abstained_gap`** | `agent` -> `abstain (unclear_question)` |
| `colloquial_ways_in_and_out_flat_b` | how many ways in and out are there for flat B | `abstained_gap` | **`answered_verified`** | `choice` -> `count=9` |
| `bedroom_most_windows` | Which bedroom has the most windows? | `abstained_gap` | **`abstained_gap`** | `choice` -> `abstain (not_supported_yet)` |
| `rooms_with_a_window` | How many rooms have at least one window? | `abstained_gap` | **`abstained_gap`** | `agent` -> `abstain (unclear_question)` |
| `rooms_more_than_two_doors` | How many rooms have more than two doors? | `abstained_gap` | **`abstained_gap`** | `agent` -> `abstain (unclear_question)` |
| `compare_apt_a_vs_b_area` | How much larger is apartment A than apartment B? | `abstained_gap` | **`answered_misrouted`** | `choice` -> `area=139.34` |
| `rooms_adjacent_to_kitchen` | Which rooms are adjacent to the kitchen in apartment A? | `abstained_gap` | **`abstained_gap`** | `choice` -> `abstain (not_supported_yet)` |
| `off_drawing_building_owner_recorded` | who owns this building? | `abstained_expected` | **`abstained_expected`** | `choice` -> `abstain (not_on_drawing)` |
| `non_building_chitchat` | Tell me a joke about architects. | `abstained_expected` | **`abstained_expected`** | `choice` -> `abstain (not_on_drawing)` |
| `conv2_t4_and_in_total` | and in total? | `abstained_gap` | **`answered_verified`** | `choice` -> `area=261.96` |

## 7. Full Turn-by-Turn Results (`live`)

| Probe ID | Category | Question | Route | Actual | Expected | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `doors_all_floor` | `doors_and_openings` | How many doors are on this floor plan? | `template` | `count: 22 (door)` | `count: 22 (door)` | `answered_verified` |
| `doors_check_all_phrasing` | `doors_and_openings` | Check for all doors on this plan | `template` | `count: 22 (door)` | `count: 22 (door)` | `answered_verified` |
| `doors_unit_a` | `doors_and_openings` | How many doors are in apartment A? | `template` | `count: 13 (door)` | `count: 13 (door)` | `answered_verified` |
| `doors_unit_b` | `doors_and_openings` | How many doors are in apartment B? | `template` | `count: 9 (door)` | `count: 9 (door)` | `answered_verified` |
| `doors_corridor_308` | `doors_and_openings` | How many doors are in room 3.08? | `template` | `count: 5 (door)` | `count: 5 (door)` | `answered_verified` |
| `doors_corridors_label` | `doors_and_openings` | How many doors are in the corridors? | `template` | `count: 11 (door)` | `count: 11 (door)` | `answered_verified` |
| `doors_kitchen_303_zero` | `doors_and_openings` | How many doors are in room 3.03? | `template` | `count: 0 (door)` | `count: 0 (door)` | `answered_verified` |
| `windows_all_floor` | `doors_and_openings` | How many windows are on this floor plan? | `template` | `count: 12 (window)` | `count: 12 (window)` | `answered_verified` |
| `windows_unit_a` | `doors_and_openings` | How many windows are in apartment A? | `template` | `count: 7 (window)` | `count: 7 (window)` | `answered_verified` |
| `windows_living_302` | `doors_and_openings` | How many windows are in room 3.02? | `template` | `count: 3 (window)` | `count: 3 (window)` | `answered_verified` |
| `windows_bathrooms_zero` | `doors_and_openings` | How many windows are in the bathrooms? | `template` | `count: 0 (window)` | `count: 0 (window)` | `answered_verified` |
| `doors_subtype_entrance` | `doors_and_openings` | How many apartment entrance doors are there? | `choice` | `count: 22 (door)` | `count: 2 (door)` | `answered_misrouted` |
| `doors_subtype_sliding_terrace` | `doors_and_openings` | How many terrace doors lead outside? | `template` | `count: 0 (door)` | `count (unextracted) (door)` | `answered_misrouted` |
| `doors_clear_width` | `doors_and_openings` | What is the width of the doors in apartment A? | `template` | `abstain (not_supported_yet)` | `quantity (unsupported)` | `abstained_gap` |
| `stairs_how_many` | `circulation_and_stairs` | How many stairs are there? | `agent` | `abstain (unclear_question)` | `count (unextracted)` | `abstained_gap` |
| `staircases_how_many` | `circulation_and_stairs` | How many staircases are on this floor? | `agent` | `abstain (unclear_question)` | `count: 1` | `abstained_gap` |
| `stairs_step_count` | `circulation_and_stairs` | How many steps are in the staircase? | `choice` | `abstain (not_on_drawing)` | `count (unextracted)` | `abstained_gap` |
| `staircase_area_trap` | `circulation_and_stairs` | What is the area of the staircase? | `choice` | `abstain (not_on_drawing)` | `29.22 m²` | `abstained_gap` |
| `staircase_doors_trap` | `circulation_and_stairs` | How many doors are in the staircase? | `choice` | `count: 22 (door)` | `count: 0 (door)` | `answered_misrouted` |
| `elevator_count` | `circulation_and_stairs` | How many elevators are on this floor plan? | `agent` | `abstain (unclear_question)` | `count: 1` | `abstained_gap` |
| `unlabeled_shaft_323_area` | `circulation_and_stairs` | What is the area of room 3.23? | `template` | `area: 2.85 m²` | `2.85 m²` | `answered_verified` |
| `unlabeled_stairwell_324_area` | `circulation_and_stairs` | What is the area of room 3.24? | `template` | `area: 29.22 m²` | `29.22 m²` | `answered_verified` |
| `corridors_count` | `circulation_and_stairs` | How many corridors are on this floor? | `template` | `count: 2 (room)` | `count: 2 (room)` | `answered_verified` |
| `corridors_area` | `circulation_and_stairs` | What is the total area of the corridors? | `template` | `area: 26.38 m²` | `26.38 m²` | `answered_verified` |
| `living_room_floor_size` | `room_sizes_and_areas` | What is the floor size of the living room? | `template` | `count: 2 (room)` | `69.68 m²` | `answered_misrouted` |
| `living_room_area_explicit` | `room_sizes_and_areas` | What is the area of the living room? | `template` | `area: 69.68 m²` | `69.68 m²` | `answered_verified` |
| `living_room_how_big` | `room_sizes_and_areas` | How big is the living room? | `template` | `area: 69.68 m²` | `69.68 m²` | `answered_verified` |
| `living_room_in_apt_a_area` | `room_sizes_and_areas` | What is the area of the living room in apartment A? | `template` | `area: 139.34 m²` | `38.57 m²` | `answered_misrouted` |
| `living_room_302_area` | `room_sizes_and_areas` | What is the floor area of room 3.02? | `template` | `area: 38.57 m²` | `38.57 m²` | `answered_verified` |
| `living_room_317_area` | `room_sizes_and_areas` | What is the floor area of room 3.17? | `template` | `area: 31.11 m²` | `31.11 m²` | `answered_verified` |
| `room_302_size_phrasing` | `room_sizes_and_areas` | What is the size of room 3.02? | `choice` | `area: 38.57 m²` | `38.57 m²` | `answered_verified` |
| `floor_indoor_net_area` | `room_sizes_and_areas` | What is the total indoor floor area? | `template` | `area: 261.96 m²` | `261.96 m²` | `answered_verified` |
| `apt_a_net_area` | `room_sizes_and_areas` | What is the floor area of apartment A? | `template` | `area: 139.34 m²` | `139.34 m²` | `answered_verified` |
| `apt_b_net_area` | `room_sizes_and_areas` | What is the floor area of apartment B? | `template` | `area: 90.55 m²` | `90.55 m²` | `answered_verified` |
| `kitchens_total_area` | `room_sizes_and_areas` | What is the total area of the kitchens? | `template` | `area: 16.69 m²` | `16.69 m²` | `answered_verified` |
| `bathrooms_total_area` | `room_sizes_and_areas` | What is the total area of the bathrooms? | `template` | `area: 25.79 m²` | `25.79 m²` | `answered_verified` |
| `bedrooms_total_area` | `room_sizes_and_areas` | What is the total area of the bedrooms? | `template` | `area: 88.09 m²` | `88.09 m²` | `answered_verified` |
| `storage_reduit_area` | `room_sizes_and_areas` | What is the area of the storage room? | `template` | `area: 3.26 m²` | `3.26 m²` | `answered_verified` |
| `terraces_total_area` | `room_sizes_and_areas` | What is the total area of the terraces? | `template` | `area: 166.05 m²` | `166.05 m²` | `answered_verified` |
| `gross_area_including_terraces` | `room_sizes_and_areas` | What is the total floor area including terraces? | `template` | `area: 166.05 m²` | `428.01 m²` | `answered_misrouted` |
| `apartments_count` | `rooms_and_units` | How many apartments are on this floor? | `template` | `count: 2 (apartment)` | `count: 2 (apartment)` | `answered_verified` |
| `indoor_rooms_count_floor` | `rooms_and_units` | How many numbered indoor rooms are on this floor? | `template` | `count: 20 (room)` | `count: 20 (room)` | `answered_verified` |
| `rooms_count_apt_a` | `rooms_and_units` | How many rooms are in apartment A? | `template` | `count: 11 (room)` | `count: 11 (room)` | `answered_verified` |
| `rooms_count_apt_b` | `rooms_and_units` | How many rooms are in apartment B? | `template` | `count: 7 (room)` | `count: 7 (room)` | `answered_verified` |
| `bedrooms_count_floor` | `rooms_and_units` | How many bedrooms are on this floor? | `template` | `count: 6 (room)` | `count: 6 (room)` | `answered_verified` |
| `kitchens_count_floor` | `rooms_and_units` | How many kitchens are there? | `template` | `count: 2 (room)` | `count: 2 (room)` | `answered_verified` |
| `bathrooms_count_floor` | `rooms_and_units` | How many bathrooms are on this floor? | `template` | `count: 5 (room)` | `count: 5 (room)` | `answered_verified` |
| `terraces_count_floor` | `rooms_and_units` | How many balconies or terraces are there? | `template` | `count: 4 (room)` | `count: 4 (room)` | `answered_verified` |
| `storage_rooms_count` | `rooms_and_units` | How many storage rooms are on this floor? | `template` | `count: 1 (room)` | `count: 1 (room)` | `answered_verified` |
| `bedrooms_in_apt_a` | `rooms_and_units` | How many bedrooms are in apartment A? | `agent` | `count: 4 (room)` | `count: 4 (room)` | `answered_verified` |
| `sleeping_places_apt_a_recorded` | `rooms_and_units` | how many places can people sleep in apartment A | `agent` | `count: 4 (room)` | `count: 4 (room)` | `answered_verified` |
| `labelled_bedrooms_in_apt_a_trap` | `rooms_and_units` | How many rooms labelled Chambre à coucher are in apartment A? | `template` | `count: 11 (room)` | `count: 4 (room)` | `answered_misrouted` |
| `washbasins_floor` | `sanitary_and_fixtures` | How many washbasins are on this floor? | `template` | `count: 5 (washbasin)` | `count: 5 (washbasin)` | `answered_verified` |
| `sinks_apt_a` | `sanitary_and_fixtures` | How many sinks are in apartment A? | `template` | `count: 3 (washbasin)` | `count: 3 (washbasin)` | `answered_verified` |
| `basins_room_306` | `sanitary_and_fixtures` | How many basins are in room 3.06? | `template` | `count: 1 (washbasin)` | `count: 1 (washbasin)` | `answered_verified` |
| `wcs_floor` | `sanitary_and_fixtures` | How many toilets are on this floor? | `template` | `count: 5 (wc)` | `count: 5 (wc)` | `answered_verified` |
| `wcs_apt_b` | `sanitary_and_fixtures` | How many WCs are in apartment B? | `template` | `count: 2 (wc)` | `count: 2 (wc)` | `answered_verified` |
| `bathtubs_floor` | `sanitary_and_fixtures` | How many bathtubs are on this floor? | `template` | `count: 2 (bathtub)` | `count: 2 (bathtub)` | `answered_verified` |
| `showers_floor` | `sanitary_and_fixtures` | How many showers are on this floor? | `template` | `count: 2 (shower)` | `count: 2 (shower)` | `answered_verified` |
| `lavatory_fittings_recorded` | `sanitary_and_fixtures` | tally the lavatory fittings for me | `agent` | `count: 5 (washbasin)` | `count: 5 (washbasin)` | `answered_verified` |
| `sanitary_fixtures_list_recorded` | `sanitary_and_fixtures` | Which sanitary fixtures are available in this building? | `choice` | `abstain (not_supported_yet)` | `text (unsupported)` | `abstained_gap` |
| `kitchen_stoves_count` | `sanitary_and_fixtures` | How many stoves or cooktops are in the kitchens? | `template` | `count: 2 (room)` | `count (unextracted)` | `answered_misrouted` |
| `washing_machines_count` | `sanitary_and_fixtures` | How many washing machines are shown on the plan? | `agent` | `abstain (unclear_question)` | `count (unextracted)` | `abstained_gap` |
| `region_mark_washbasins` | `spatial_interactive_tools` | How many washbasins are in this area? | `template` | `count: 3 (washbasin)` | `count: 3 (washbasin)` | `answered_verified` |
| `region_mark_doors` | `spatial_interactive_tools` | How many doors are here? | `template` | `count: 1 (door)` | `count: 1 (door)` | `answered_verified` |
| `region_mark_area` | `spatial_interactive_tools` | What is the floor area here? | `template` | `area: 11.98 m²` | `11.98 m²` | `answered_verified` |
| `what_if_room_304_apt_a` | `spatial_interactive_tools` | What is the floor area of apartment A? | `template` | `area: 137.19 m²` | `137.19 m²` | `answered_verified` |
| `what_if_two_rooms_floor` | `spatial_interactive_tools` | What is the total indoor floor area? | `template` | `area: 261.37 m²` | `261.37 m²` | `answered_verified` |
| `fr_doors_apt_a` | `multilingual` | Combien de portes y a-t-il dans l'appartement A ? | `template` | `count: 13 (door)` | `count: 13 (door)` | `answered_verified` |
| `fr_sejour_area` | `multilingual` | Quelle est la surface du séjour/repas ? | `template` | `area: 69.68 m²` | `69.68 m²` | `answered_verified` |
| `de_windows_apt_b` | `multilingual` | Wie viele Fenster hat die Wohnung B? | `template` | `count: 5 (window)` | `count: 5 (window)` | `answered_verified` |
| `de_quoted_bad_synonym` | `multilingual` | Wie viele Räume mit der Bezeichnung «Bad» gibt es? | `template` | `count: 5 (room)` | `count: 5 (room)` | `answered_verified` |
| `de_wohnflaeche_apt_a` | `multilingual` | Wie gross ist die Wohnfläche von Wohnung A? | `template` | `area: 139.34 m²` | `139.34 m²` | `answered_verified` |
| `fi_doors_floor` | `multilingual` | Kuinka monta ovea tässä kerroksessa on? | `template` | `count: 22 (door)` | `count: 22 (door)` | `answered_verified` |
| `fi_area_apt_b` | `multilingual` | Mikä on asunnon B pinta-ala? | `template` | `area: 90.55 m²` | `90.55 m²` | `answered_verified` |
| `it_bathtubs_no_false_bagno_scope` | `multilingual` | Quante vasche da bagno ci sono in questo piano? | `template` | `count: 2 (bathtub)` | `count: 2 (bathtub)` | `answered_verified` |
| `colloquial_room_305_size` | `colloquial_and_voice` | hey quick check, room 3.05, how big is it in m2? | `template` | `area: 13.81 m²` | `13.81 m²` | `answered_verified` |
| `colloquial_ordering_sinks` | `colloquial_and_voice` | we're ordering sanitaryware — how many sinks do we need for this floor? | `template` | `count: 5 (washbasin)` | `count: 5 (washbasin)` | `answered_verified` |
| `colloquial_one_word_toilets` | `colloquial_and_voice` | toilets? | `template` | `count: 5 (wc)` | `count: 5 (wc)` | `answered_verified` |
| `colloquial_ways_in_and_out_flat_b` | `colloquial_and_voice` | how many ways in and out are there for flat B | `choice` | `count: 9 (door)` | `count: 9 (door)` | `answered_verified` |
| `largest_room_on_floor` | `analytical_and_relational` | Which room has the largest area on this floor? | `template` | `note: 38.57` | `note: 38.57` | `answered_verified` |
| `bedroom_most_windows` | `analytical_and_relational` | Which bedroom has the most windows? | `choice` | `abstain (not_supported_yet)` | `note: 3.19` | `abstained_gap` |
| `rooms_with_a_window` | `analytical_and_relational` | How many rooms have at least one window? | `agent` | `abstain (unclear_question)` | `count: 9 (room)` | `abstained_gap` |
| `rooms_more_than_two_doors` | `analytical_and_relational` | How many rooms have more than two doors? | `agent` | `abstain (unclear_question)` | `count: 2 (room)` | `abstained_gap` |
| `compare_apt_a_vs_b_area` | `analytical_and_relational` | How much larger is apartment A than apartment B? | `choice` | `area: 139.34 m²` | `48.79 m²` | `answered_misrouted` |
| `rooms_adjacent_to_kitchen` | `analytical_and_relational` | Which rooms are adjacent to the kitchen in apartment A? | `choice` | `abstain (not_supported_yet)` | `note: 2` | `abstained_gap` |
| `room_304_width_length` | `dimensions_and_quantities` | What are the width and length of room 3.04? | `template` | `abstain (not_supported_yet)` | `quantity (unsupported)` | `abstained_gap` |
| `room_304_perimeter` | `dimensions_and_quantities` | What is the perimeter of room 3.04? | `template` | `abstain (not_supported_yet)` | `quantity (unsupported)` | `abstained_gap` |
| `total_wall_length_dxf` | `dimensions_and_quantities` | What is the total wall length on this floor? | `template` | `abstain (not_supported_yet)` | `quantity (unsupported)` | `abstained_gap` |
| `whole_building_doors_dxf` | `dimensions_and_quantities` | How many doors are in the entire building across all floors? | `template` | `count: 22 (door)` | `abstain (off-drawing/invalid)` | `answered_misrouted` |
| `wall_volume_dxf` | `dimensions_and_quantities` | What is the total concrete volume of the walls? | `template` | `abstain (not_supported_yet)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `off_drawing_ceiling_height` | `out_of_scope_and_invalid` | What is the floor-to-ceiling height in apartment A? | `template` | `abstain (not_on_drawing)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `off_drawing_wall_material` | `out_of_scope_and_invalid` | What material are the exterior walls made of? | `template` | `abstain (not_on_drawing)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `off_drawing_fire_rating` | `out_of_scope_and_invalid` | What is the fire rating of the apartment entrance doors? | `template` | `abstain (not_on_drawing)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `off_drawing_building_owner_recorded` | `out_of_scope_and_invalid` | who owns this building? | `choice` | `abstain (not_on_drawing)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `invalid_room_999` | `out_of_scope_and_invalid` | How many windows are in room 9.99? | `template` | `abstain (unclear_question)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `invalid_apartment_c` | `out_of_scope_and_invalid` | What is the floor area of apartment C? | `template` | `abstain (unclear_question)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `invalid_quoted_sauna_count` | `out_of_scope_and_invalid` | How many rooms labelled 'Sauna' are on this floor? | `template` | `abstain (unclear_question)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `invalid_unquoted_sauna_area_trap` | `out_of_scope_and_invalid` | What is the floor area of the sauna? | `template` | `area: 261.96 m²` | `abstain (off-drawing/invalid)` | `answered_misrouted` |
| `non_building_chitchat` | `out_of_scope_and_invalid` | Tell me a joke about architects. | `choice` | `abstain (not_on_drawing)` | `abstain (off-drawing/invalid)` | `abstained_expected` |
| `conv1_t1_doors_apt_a` | `multi_turn_conversations` | How many doors are in apartment A? | `cache` | `count: 13 (door)` | `count: 13 (door)` | `answered_verified` |
| `conv1_t2_followup_apt_b` | `multi_turn_conversations` | and in apartment B? | `template` | `count: 9 (door)` | `count: 9 (door)` | `answered_verified` |
| `conv1_t3_followup_windows` | `multi_turn_conversations` | and windows? | `template` | `count: 5 (window)` | `count: 5 (window)` | `answered_verified` |
| `conv1_t4_fresh_question_no_leak` | `multi_turn_conversations` | How many bathtubs? | `template` | `count: 2 (bathtub)` | `count: 2 (bathtub)` | `answered_verified` |
| `conv1_t5_reset_whole_floor` | `multi_turn_conversations` | and doors in total? | `template` | `count: 22 (door)` | `count: 22 (door)` | `answered_verified` |
| `conv2_t1_area_apt_a` | `multi_turn_conversations` | What is the floor area of apartment A? | `cache` | `area: 139.34 m²` | `139.34 m²` | `answered_verified` |
| `conv2_t2_what_about_apt_b` | `multi_turn_conversations` | what about apartment B? | `template` | `area: 90.55 m²` | `90.55 m²` | `answered_verified` |
| `conv2_t3_and_room_304` | `multi_turn_conversations` | and room 3.04? | `template` | `area: 17.04 m²` | `17.04 m²` | `answered_verified` |
| `conv2_t4_and_in_total` | `multi_turn_conversations` | and in total? | `choice` | `area: 261.96 m²` | `261.96 m²` | `answered_verified` |
