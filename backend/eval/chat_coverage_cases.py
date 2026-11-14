"""Curated and dynamic architectural chat-coverage probes for AURA floor plans.

Primary benchmark target: `swiss-46639` ("Plan 3e étage · Site 11420 · 1:200").
Also provides `build_dynamic_probes(drawing_data)` so arbitrary drawings can be
probed with ground-truth expectations derived directly from their DrawingFile JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

AnswerKind = Literal["count", "area", "text", "quantity", "note", "abstain"]

SWISS_DRAWING_ID = "swiss-46639"

MOCK_BATH_CLUSTER_REGION: dict[str, Any] = {
    "kind": "region",
    "rect": {"x": 47.4, "y": 39.2, "w": 13.6, "h": 15.2},
    "label": "Area 1",
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "doors_and_openings": (
        "Doors, windows, per-unit/per-room/per-label openings, and door sub-types/dimensions"
    ),
    "circulation_and_stairs": (
        "Stairs, staircases, elevators/lifts, corridors, and unlabeled shafts/landings"
    ),
    "room_sizes_and_areas": (
        "Floor sizes, living room areas, apartment net areas, room IDs, and gross vs indoor areas"
    ),
    "rooms_and_units": (
        "Apartment counts, indoor/outdoor room counts, room-type counts, and compound unit+label"
    ),
    "sanitary_and_fixtures": (
        "Washbasins, WCs, bathtubs, showers, fixture lists, and kitchen/laundry appliances"
    ),
    "spatial_interactive_tools": (
        "UI region-mark (`scope.kind='region'`) queries and `what_if` room dimension overrides"
    ),
    "multi_turn_conversations": (
        "Stateful `/chat` follow-ups: `conversation_id` reuse, scope inheritance, and floor reset"
    ),
    "multilingual": (
        "French, German, Finnish, and Italian architectural queries and CAD label synonyms"
    ),
    "colloquial_and_voice": (
        "Informal, terse, or rambling site-walkthrough phrasings"
    ),
    "analytical_and_relational": (
        "Superlatives, comparisons, conditional filters, and room adjacency"
    ),
    "dimensions_and_quantities": (
        "Linear room dimensions, perimeters, wall lengths/volumes, and multi-storey totals"
    ),
    "out_of_scope_and_invalid": (
        "Off-drawing specs (height, material, fire rating, owner), bad IDs, and non-building chat"
    ),
}


@dataclass(frozen=True)
class ProbeExpectation:
    """Ground-truth architectural expectation for a single chat turn."""

    answerable_on_drawing: bool
    expected_kind: AnswerKind
    expected_value: float | int | str | None = None
    expected_subject: str | None = None
    expected_scope: dict[str, Any] | None = None
    allowed_scopes: tuple[dict[str, Any], ...] = ()
    allowed_kinds: tuple[AnswerKind, ...] = ()
    expected_substrings: tuple[str, ...] = ()
    allow_any_value: bool = False
    tolerance_abs: float = 0.02
    tolerance_pct: float = 0.0
    rationale: str = ""


@dataclass(frozen=True)
class ChatProbe:
    """A single-turn `/chat` (or `/ask`) probe."""

    id: str
    category: str
    question: str
    expectation: ProbeExpectation
    lang: str = "en"
    drawing_id: str = SWISS_DRAWING_ID
    scope: dict[str, Any] | None = None
    overrides: list[dict[str, Any]] | None = None
    lang_hint: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationTurnSpec:
    """One turn in a multi-turn `/chat` conversation probe."""

    turn_index: int
    id: str
    message: str
    expectation: ProbeExpectation
    lang: str = "en"
    scope: dict[str, Any] | None = None
    overrides: list[dict[str, Any]] | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationProbe:
    """A stateful multi-turn `/chat` probe sharing `conversation_id` across turns."""

    conversation_key: str
    category: str
    title: str
    turns: tuple[ConversationTurnSpec, ...]
    drawing_id: str = SWISS_DRAWING_ID


def _floor() -> dict[str, Any]:
    return {"kind": "floor"}


def _unit(u: str) -> dict[str, Any]:
    return {"kind": "unit", "unit": u}


def _room(rid: str) -> dict[str, Any]:
    return {"kind": "room", "room_id": rid}


def _label(lbl: str) -> dict[str, Any]:
    return {"kind": "label", "label": lbl}


def _count_exp(
    value: int,
    subject: str,
    scope: dict[str, Any] | None = None,
    *,
    allowed_scopes: tuple[dict[str, Any], ...] = (),
    rationale: str = "",
) -> ProbeExpectation:
    return ProbeExpectation(
        answerable_on_drawing=True,
        expected_kind="count",
        expected_value=value,
        expected_subject=subject,
        expected_scope=scope,
        allowed_scopes=allowed_scopes,
        tolerance_abs=0.0,
        rationale=rationale,
    )


def _area_exp(
    value: float,
    scope: dict[str, Any] | None = None,
    *,
    allowed_scopes: tuple[dict[str, Any], ...] = (),
    tolerance_abs: float = 0.02,
    tolerance_pct: float = 0.0,
    rationale: str = "",
) -> ProbeExpectation:
    return ProbeExpectation(
        answerable_on_drawing=True,
        expected_kind="area",
        expected_value=value,
        expected_scope=scope,
        allowed_scopes=allowed_scopes,
        tolerance_abs=tolerance_abs,
        tolerance_pct=tolerance_pct,
        rationale=rationale,
    )


def _gap_exp(
    kind: AnswerKind,
    *,
    value: float | str | None = None,
    subject: str | None = None,
    scope: dict[str, Any] | None = None,
    allowed_kinds: tuple[AnswerKind, ...] = (),
    expected_substrings: tuple[str, ...] = (),
    tolerance_abs: float = 0.02,
    rationale: str,
) -> ProbeExpectation:
    """Architecturally answerable from the floor plan, even if current solvers lack support."""
    return ProbeExpectation(
        answerable_on_drawing=True,
        expected_kind=kind,
        expected_value=value,
        expected_subject=subject,
        expected_scope=scope,
        allowed_kinds=allowed_kinds,
        expected_substrings=expected_substrings,
        tolerance_abs=tolerance_abs,
        rationale=rationale,
    )


def _refuse_exp(rationale: str) -> ProbeExpectation:
    """Fact is genuinely absent from the floor plan or references an invalid target."""
    return ProbeExpectation(
        answerable_on_drawing=False,
        expected_kind="abstain",
        expected_value=None,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Curated benchmark suite for `swiss-46639`
# ---------------------------------------------------------------------------

SWISS_46639_PROBES: tuple[ChatProbe, ...] = (
    # ---- 1. Doors & openings ------------------------------------------------
    ChatProbe(
        id="doors_all_floor",
        category="doors_and_openings",
        question="How many doors are on this floor plan?",
        expectation=_count_exp(
            22, "door", _floor(),
            rationale="22 door symbols on DXF door layer (including 2 CAD double-drawn pairs).",
        ),
        tags=("user_example", "gold"),
    ),
    ChatProbe(
        id="doors_check_all_phrasing",
        category="doors_and_openings",
        question="Check for all doors on this plan",
        expectation=_count_exp(
            22, "door", _floor(),
            rationale="Imperative 'check for all doors' phrasing; 22 doors on floor.",
        ),
        tags=("user_example",),
    ),
    ChatProbe(
        id="doors_unit_a",
        category="doors_and_openings",
        question="How many doors are in apartment A?",
        expectation=_count_exp(13, "door", _unit("A"), rationale="13 door boxes in Apartment A."),
    ),
    ChatProbe(
        id="doors_unit_b",
        category="doors_and_openings",
        question="How many doors are in apartment B?",
        expectation=_count_exp(9, "door", _unit("B"), rationale="9 door boxes in Apartment B."),
    ),
    ChatProbe(
        id="doors_corridor_308",
        category="doors_and_openings",
        question="How many doors are in room 3.08?",
        expectation=_count_exp(
            5, "door", _room("3.08"),
            rationale="Corridor 3.08 in Apartment A has 5 door boxes (including door-15/16 pair).",
        ),
    ),
    ChatProbe(
        id="doors_corridors_label",
        category="doors_and_openings",
        question="How many doors are in the corridors?",
        expectation=_count_exp(
            11, "door", _label("Couloir"),
            rationale="3.08 has 5 doors and 3.21 has 6 doors -> 11 total in Couloir rooms.",
        ),
    ),
    ChatProbe(
        id="doors_kitchen_303_zero",
        category="doors_and_openings",
        question="How many doors are in room 3.03?",
        expectation=_count_exp(
            0, "door", _room("3.03"),
            rationale="Open-plan kitchen 3.03 has 0 door boxes.",
        ),
    ),
    ChatProbe(
        id="windows_all_floor",
        category="doors_and_openings",
        question="How many windows are on this floor plan?",
        expectation=_count_exp(12, "window", _floor(), rationale="12 window boxes on floor."),
        tags=("gold",),
    ),
    ChatProbe(
        id="windows_unit_a",
        category="doors_and_openings",
        question="How many windows are in apartment A?",
        expectation=_count_exp(7, "window", _unit("A"), rationale="7 windows in Apartment A."),
    ),
    ChatProbe(
        id="windows_living_302",
        category="doors_and_openings",
        question="How many windows are in room 3.02?",
        expectation=_count_exp(
            3, "window", _room("3.02"), rationale="Living room 3.02 has 3 windows."
        ),
    ),
    ChatProbe(
        id="windows_bathrooms_zero",
        category="doors_and_openings",
        question="How many windows are in the bathrooms?",
        expectation=_count_exp(
            0, "window", _label("Bain"),
            rationale="All 5 Bain rooms are internal and have 0 windows.",
        ),
    ),
    ChatProbe(
        id="doors_subtype_entrance",
        category="doors_and_openings",
        question="How many apartment entrance doors are there?",
        expectation=_gap_exp(
            "count", value=2, subject="door", scope=_floor(),
            rationale=(
                "2 unit entrance doors open from stairwell 3.24 into corridors 3.08 and 3.21 "
                "(4 CAD boxes because both entrance doors are double-drawn, NOT all 22 doors)."
            ),
        ),
        tags=("subtype_trap",),
    ),
    ChatProbe(
        id="doors_subtype_sliding_terrace",
        category="doors_and_openings",
        question="How many terrace doors lead outside?",
        expectation=_gap_exp(
            "count", value=None, subject="door",
            rationale=(
                "Asks specifically for exterior terrace doors, neither all 22 doors nor indoor "
                "partition doors; CAD door layer does not classify exterior vs interior."
            ),
        ),
        tags=("subtype_trap",),
    ),
    ChatProbe(
        id="doors_clear_width",
        category="doors_and_openings",
        question="What is the width of the doors in apartment A?",
        expectation=_gap_exp(
            "quantity",
            rationale="Door leaf widths are drawn geometrically in DXF but not exposed as a tool.",
        ),
    ),

    # ---- 2. Circulation & stairs --------------------------------------------
    ChatProbe(
        id="stairs_how_many",
        category="circulation_and_stairs",
        question="How many stairs are there?",
        expectation=_gap_exp(
            "count",
            rationale=(
                "User example: central stairwell (3.24) is drawn on the plan, "
                "but no stair/step detector or CAD layer exists."
            ),
        ),
        tags=("user_example",),
    ),
    ChatProbe(
        id="staircases_how_many",
        category="circulation_and_stairs",
        question="How many staircases are on this floor?",
        expectation=_gap_exp(
            "count", value=1,
            rationale="1 central stairwell landing (room 3.24, unlabeled) serves Apartments A & B.",
        ),
        tags=("user_example",),
    ),
    ChatProbe(
        id="stairs_step_count",
        category="circulation_and_stairs",
        question="How many steps are in the staircase?",
        expectation=_gap_exp(
            "count",
            rationale="Individual stair treads are drawn in the plan image/CAD but not extracted.",
        ),
    ),
    ChatProbe(
        id="staircase_area_trap",
        category="circulation_and_stairs",
        question="What is the area of the staircase?",
        expectation=_gap_exp(
            "area", value=29.22, scope=_room("3.24"),
            rationale=(
                "Stairwell/landing is unlabeled room 3.24 (29.22 m² outline), NOT 261.96 m² "
                "(whole floor)."
            ),
        ),
        tags=("scope_collapse_trap",),
    ),
    ChatProbe(
        id="staircase_doors_trap",
        category="circulation_and_stairs",
        question="How many doors are in the staircase?",
        expectation=_gap_exp(
            "count", value=0, subject="door", scope=_room("3.24"),
            rationale=(
                "Unlabeled stairwell 3.24 has 0 assigned door boxes (adjacent corridor entrance "
                "doors are assigned to 3.08/3.21); must NOT return all 22 floor doors."
            ),
        ),
        tags=("scope_collapse_trap",),
    ),
    ChatProbe(
        id="elevator_count",
        category="circulation_and_stairs",
        question="How many elevators are on this floor plan?",
        expectation=_gap_exp(
            "count", value=1,
            rationale="1 lift shaft (room 3.23, 2.85 m², unlabeled) next to stairwell 3.24.",
        ),
    ),
    ChatProbe(
        id="unlabeled_shaft_323_area",
        category="circulation_and_stairs",
        question="What is the area of room 3.23?",
        expectation=_area_exp(
            2.85, _room("3.23"),
            rationale="Unlabeled shaft 3.23 has outline area 2.85 m².",
        ),
    ),
    ChatProbe(
        id="unlabeled_stairwell_324_area",
        category="circulation_and_stairs",
        question="What is the area of room 3.24?",
        expectation=_area_exp(
            29.22, _room("3.24"),
            rationale="Unlabeled stairwell/landing 3.24 has outline area 29.22 m².",
        ),
    ),
    ChatProbe(
        id="corridors_count",
        category="circulation_and_stairs",
        question="How many corridors are on this floor?",
        expectation=_count_exp(
            2, "room", _label("Couloir"),
            rationale="2 rooms labelled Couloir (3.08 in A, 3.21 in B).",
        ),
    ),
    ChatProbe(
        id="corridors_area",
        category="circulation_and_stairs",
        question="What is the total area of the corridors?",
        expectation=_area_exp(
            26.38, _label("Couloir"),
            rationale="Couloir 3.08 (15.87) + Couloir 3.21 (10.51) = 26.38 m².",
        ),
    ),

    # ---- 3. Room sizes & areas ----------------------------------------------
    ChatProbe(
        id="living_room_floor_size",
        category="room_sizes_and_areas",
        question="What is the floor size of the living room?",
        expectation=_area_exp(
            69.68, _label("Séjour/repas"),
            rationale=(
                "User example: 'floor size of the living room' asks for area of Séjour/repas "
                "(38.57 + 31.11 = 69.68 m²), NOT room count = 2."
            ),
        ),
        tags=("user_example", "misroute_trap"),
    ),
    ChatProbe(
        id="living_room_area_explicit",
        category="room_sizes_and_areas",
        question="What is the area of the living room?",
        expectation=_area_exp(
            69.68, _label("Séjour/repas"),
            rationale="Both Séjour/repas rooms sum to 69.68 m².",
        ),
        tags=("user_example",),
    ),
    ChatProbe(
        id="living_room_how_big",
        category="room_sizes_and_areas",
        question="How big is the living room?",
        expectation=_area_exp(
            69.68, _label("Séjour/repas"),
            rationale="'How big' triggers AREA intent on label Séjour/repas -> 69.68 m².",
        ),
    ),
    ChatProbe(
        id="living_room_in_apt_a_area",
        category="room_sizes_and_areas",
        question="What is the area of the living room in apartment A?",
        expectation=_area_exp(
            38.57,
            allowed_scopes=(_room("3.02"), _unit("A"), _label("Séjour/repas")),
            rationale=(
                "Apartment A's Séjour/repas is room 3.02 (38.57 m²), NOT 139.34 m² (all of Apt A)."
            ),
        ),
        tags=("compound_scope", "user_example"),
    ),
    ChatProbe(
        id="living_room_302_area",
        category="room_sizes_and_areas",
        question="What is the floor area of room 3.02?",
        expectation=_area_exp(38.57, _room("3.02"), rationale="Room 3.02 printed area is 38.57 m²."),
    ),
    ChatProbe(
        id="living_room_317_area",
        category="room_sizes_and_areas",
        question="What is the floor area of room 3.17?",
        expectation=_area_exp(31.11, _room("3.17"), rationale="Room 3.17 printed area is 31.11 m²."),
    ),
    ChatProbe(
        id="room_302_size_phrasing",
        category="room_sizes_and_areas",
        question="What is the size of room 3.02?",
        expectation=_area_exp(
            38.57, _room("3.02"),
            rationale="'size of room 3.02' asks for room 3.02's 38.57 m² area.",
        ),
    ),
    ChatProbe(
        id="floor_indoor_net_area",
        category="room_sizes_and_areas",
        question="What is the total indoor floor area?",
        expectation=_area_exp(
            261.96, _floor(),
            rationale="20 indoor rooms sum to 261.96 m² (4 outdoor terraces excluded).",
        ),
        tags=("gold",),
    ),
    ChatProbe(
        id="apt_a_net_area",
        category="room_sizes_and_areas",
        question="What is the floor area of apartment A?",
        expectation=_area_exp(
            139.34, _unit("A"),
            rationale="11 indoor rooms in Apartment A sum to 139.34 m².",
        ),
        tags=("gold",),
    ),
    ChatProbe(
        id="apt_b_net_area",
        category="room_sizes_and_areas",
        question="What is the floor area of apartment B?",
        expectation=_area_exp(
            90.55, _unit("B"),
            rationale="7 indoor rooms in Apartment B sum to 90.55 m².",
        ),
        tags=("gold",),
    ),
    ChatProbe(
        id="kitchens_total_area",
        category="room_sizes_and_areas",
        question="What is the total area of the kitchens?",
        expectation=_area_exp(
            16.69, _label("Cuisine"),
            rationale="Cuisine 3.03 (7.83) + Cuisine 3.15 (8.86) = 16.69 m².",
        ),
    ),
    ChatProbe(
        id="bathrooms_total_area",
        category="room_sizes_and_areas",
        question="What is the total area of the bathrooms?",
        expectation=_area_exp(
            25.79, _label("Bain"),
            rationale="5 Bain rooms sum to 25.79 m².",
        ),
    ),
    ChatProbe(
        id="bedrooms_total_area",
        category="room_sizes_and_areas",
        question="What is the total area of the bedrooms?",
        expectation=_area_exp(
            88.09, _label("Chambre à coucher"),
            rationale="6 Chambre à coucher rooms sum to 88.09 m².",
        ),
    ),
    ChatProbe(
        id="storage_reduit_area",
        category="room_sizes_and_areas",
        question="What is the area of the storage room?",
        expectation=_area_exp(
            3.26, _label("Réduit"),
            rationale="Réduit 3.11 has printed area 3.26 m².",
        ),
    ),
    ChatProbe(
        id="terraces_total_area",
        category="room_sizes_and_areas",
        question="What is the total area of the terraces?",
        expectation=_area_exp(
            166.05, _label("Terrasse"),
            rationale="4 Terrasse polygons (3.01, 3.13, 3.14, 3.22) sum to 166.05 m².",
        ),
    ),
    ChatProbe(
        id="gross_area_including_terraces",
        category="room_sizes_and_areas",
        question="What is the total floor area including terraces?",
        expectation=_area_exp(
            428.01, _floor(),
            rationale=(
                "Indoor area (261.96) + outdoor terraces (166.05) = 428.01 m², "
                "NOT 166.05 m² (terraces alone)."
            ),
        ),
        tags=("misroute_trap",),
    ),

    # ---- 4. Rooms & units ---------------------------------------------------
    ChatProbe(
        id="apartments_count",
        category="rooms_and_units",
        question="How many apartments are on this floor?",
        expectation=_count_exp(2, "apartment", _floor(), rationale="2 units: App. A and App. B."),
        tags=("gold",),
    ),
    ChatProbe(
        id="indoor_rooms_count_floor",
        category="rooms_and_units",
        question="How many numbered indoor rooms are on this floor?",
        expectation=_count_exp(
            20, "room", _floor(),
            rationale="24 numbered spaces minus 4 outdoor terraces = 20 indoor rooms.",
        ),
    ),
    ChatProbe(
        id="rooms_count_apt_a",
        category="rooms_and_units",
        question="How many rooms are in apartment A?",
        expectation=_count_exp(
            11, "room", _unit("A"),
            rationale="Apartment A has 13 spaces, 2 outdoor terraces excluded -> 11 indoor rooms.",
        ),
    ),
    ChatProbe(
        id="rooms_count_apt_b",
        category="rooms_and_units",
        question="How many rooms are in apartment B?",
        expectation=_count_exp(
            7, "room", _unit("B"),
            rationale="Apartment B has 9 spaces, 2 outdoor terraces excluded -> 7 indoor rooms.",
        ),
    ),
    ChatProbe(
        id="bedrooms_count_floor",
        category="rooms_and_units",
        question="How many bedrooms are on this floor?",
        expectation=_count_exp(
            6, "room", _label("Chambre à coucher"),
            rationale="6 rooms labelled Chambre à coucher (4 in A, 2 in B).",
        ),
    ),
    ChatProbe(
        id="kitchens_count_floor",
        category="rooms_and_units",
        question="How many kitchens are there?",
        expectation=_count_exp(
            2, "room", _label("Cuisine"), rationale="2 rooms labelled Cuisine (3.03, 3.15)."
        ),
    ),
    ChatProbe(
        id="bathrooms_count_floor",
        category="rooms_and_units",
        question="How many bathrooms are on this floor?",
        expectation=_count_exp(
            5, "room", _label("Bain"), rationale="5 rooms labelled Bain (3 in A, 2 in B)."
        ),
    ),
    ChatProbe(
        id="terraces_count_floor",
        category="rooms_and_units",
        question="How many balconies or terraces are there?",
        expectation=_count_exp(
            4, "room", _label("Terrasse"), rationale="4 outdoor rooms labelled Terrasse."
        ),
    ),
    ChatProbe(
        id="storage_rooms_count",
        category="rooms_and_units",
        question="How many storage rooms are on this floor?",
        expectation=_count_exp(
            1, "room", _label("Réduit"), rationale="1 room labelled Réduit (3.11 in Apt A)."
        ),
    ),
    ChatProbe(
        id="bedrooms_in_apt_a",
        category="rooms_and_units",
        question="How many bedrooms are in apartment A?",
        expectation=_count_exp(
            4, "room", _unit("A"),
            rationale="Apartment A has 4 Chambre à coucher rooms (3.04, 3.05, 3.07, 3.10).",
        ),
        tags=("compound_scope",),
    ),
    ChatProbe(
        id="sleeping_places_apt_a_recorded",
        category="rooms_and_units",
        question="how many places can people sleep in apartment A",
        expectation=_count_exp(
            4, "room", _unit("A"),
            rationale=(
                "Recorded agent fixture: list_rooms(unit:A) filtered to Chambre à coucher -> 4."
            ),
        ),
        tags=("compound_scope", "recorded_fixture"),
    ),
    ChatProbe(
        id="labelled_bedrooms_in_apt_a_trap",
        category="rooms_and_units",
        question="How many rooms labelled Chambre à coucher are in apartment A?",
        expectation=_count_exp(
            4, "room", _unit("A"),
            rationale=(
                "Apartment A has 4 rooms labelled Chambre à coucher, NOT 11 (all rooms in A)."
            ),
        ),
        tags=("compound_scope", "misroute_trap"),
    ),

    # ---- 5. Sanitary & fixtures ---------------------------------------------
    ChatProbe(
        id="washbasins_floor",
        category="sanitary_and_fixtures",
        question="How many washbasins are on this floor?",
        expectation=_count_exp(5, "washbasin", _floor(), rationale="5 washbasins on floor."),
        tags=("gold",),
    ),
    ChatProbe(
        id="sinks_apt_a",
        category="sanitary_and_fixtures",
        question="How many sinks are in apartment A?",
        expectation=_count_exp(3, "washbasin", _unit("A"), rationale="3 washbasins in Apt A."),
    ),
    ChatProbe(
        id="basins_room_306",
        category="sanitary_and_fixtures",
        question="How many basins are in room 3.06?",
        expectation=_count_exp(1, "washbasin", _room("3.06"), rationale="1 washbasin in Bain 3.06."),
    ),
    ChatProbe(
        id="wcs_floor",
        category="sanitary_and_fixtures",
        question="How many toilets are on this floor?",
        expectation=_count_exp(5, "wc", _floor(), rationale="5 WCs across the 5 Bain rooms."),
        tags=("gold",),
    ),
    ChatProbe(
        id="wcs_apt_b",
        category="sanitary_and_fixtures",
        question="How many WCs are in apartment B?",
        expectation=_count_exp(2, "wc", _unit("B"), rationale="2 WCs in Apartment B (3.18, 3.20)."),
    ),
    ChatProbe(
        id="bathtubs_floor",
        category="sanitary_and_fixtures",
        question="How many bathtubs are on this floor?",
        expectation=_count_exp(
            2, "bathtub", _floor(), rationale="2 bathtubs (3.09 in A, 3.18 in B)."
        ),
        tags=("gold",),
    ),
    ChatProbe(
        id="showers_floor",
        category="sanitary_and_fixtures",
        question="How many showers are on this floor?",
        expectation=_count_exp(
            2, "shower", _floor(), rationale="2 showers (3.06 in A, 3.20 in B)."
        ),
        tags=("gold",),
    ),
    ChatProbe(
        id="lavatory_fittings_recorded",
        category="sanitary_and_fixtures",
        question="tally the lavatory fittings for me",
        expectation=_count_exp(
            5, "washbasin", _floor(),
            rationale="Synthetic choice fixture maps 'lavatory fittings' -> washbasin count (5).",
        ),
        tags=("recorded_fixture",),
    ),
    ChatProbe(
        id="sanitary_fixtures_list_recorded",
        category="sanitary_and_fixtures",
        question="Which sanitary fixtures are available in this building?",
        expectation=_gap_exp(
            "text",
            rationale=(
                "Plan shows washbasins, WCs, bathtubs, and showers, but list/inventory answers "
                "are not supported yet (recorded choice_q13_sanitary fixture abstains)."
            ),
        ),
        tags=("recorded_fixture",),
    ),
    ChatProbe(
        id="kitchen_stoves_count",
        category="sanitary_and_fixtures",
        question="How many stoves or cooktops are in the kitchens?",
        expectation=_gap_exp(
            "count",
            rationale="Cooktop symbols appear in kitchens 3.03 & 3.15 on PNG, no CAD detector.",
        ),
    ),
    ChatProbe(
        id="washing_machines_count",
        category="sanitary_and_fixtures",
        question="How many washing machines are shown on the plan?",
        expectation=_gap_exp(
            "count",
            rationale="Appliance blocks are not extracted into evidence layers.",
        ),
    ),

    # ---- 6. Spatial interactive tools (region + what-if) --------------------
    ChatProbe(
        id="region_mark_washbasins",
        category="spatial_interactive_tools",
        question="How many washbasins are in this area?",
        scope=MOCK_BATH_CLUSTER_REGION,
        expectation=_count_exp(
            3, "washbasin", MOCK_BATH_CLUSTER_REGION,
            rationale="Marked bathroom cluster in Apt A contains 3 washbasin box centers.",
        ),
    ),
    ChatProbe(
        id="region_mark_doors",
        category="spatial_interactive_tools",
        question="How many doors are here?",
        scope=MOCK_BATH_CLUSTER_REGION,
        expectation=_count_exp(
            1, "door", MOCK_BATH_CLUSTER_REGION,
            rationale="Marked region contains 1 door box center.",
        ),
    ),
    ChatProbe(
        id="region_mark_area",
        category="spatial_interactive_tools",
        question="What is the floor area here?",
        scope=MOCK_BATH_CLUSTER_REGION,
        expectation=_area_exp(
            11.98, MOCK_BATH_CLUSTER_REGION,
            rationale="Rooms with label_at inside region: 3.06 (6.00) + 3.09 (5.98) = 11.98 m².",
        ),
    ),
    ChatProbe(
        id="what_if_room_304_apt_a",
        category="spatial_interactive_tools",
        question="What is the floor area of apartment A?",
        overrides=[{"room_id": "3.04", "w_m": 4.135, "h_m": 3.60}],
        expectation=_area_exp(
            137.19, _unit("A"),
            rationale=(
                "Room 3.04 resized from 17.04 m² to round(4.135*3.60, 2)=14.89 m² -> "
                "Apt A becomes 139.34 - 17.04 + 14.89 = 137.19 m²."
            ),
        ),
    ),
    ChatProbe(
        id="what_if_two_rooms_floor",
        category="spatial_interactive_tools",
        question="What is the total indoor floor area?",
        overrides=[
            {"room_id": "3.04", "w_m": 4.00, "h_m": 4.00},
            {"room_id": "3.16", "w_m": 3.50, "h_m": 4.00},
        ],
        expectation=_area_exp(
            261.37, _floor(),
            rationale=(
                "3.04: 17.04 -> 16.00 (-1.04); 3.16: 13.55 -> 14.00 (+0.45); "
                "261.96 - 1.04 + 0.45 = 261.37 m²."
            ),
        ),
    ),

    # ---- 8. Multilingual (FR / DE / FI / IT) --------------------------------
    ChatProbe(
        id="fr_doors_apt_a",
        category="multilingual",
        lang="fr",
        question="Combien de portes y a-t-il dans l'appartement A ?",
        expectation=_count_exp(13, "door", _unit("A"), rationale="FR door count in Apt A = 13."),
    ),
    ChatProbe(
        id="fr_sejour_area",
        category="multilingual",
        lang="fr",
        question="Quelle est la surface du séjour/repas ?",
        expectation=_area_exp(
            69.68, _label("Séjour/repas"), rationale="FR surface of Séjour/repas = 69.68 m²."
        ),
    ),
    ChatProbe(
        id="de_windows_apt_b",
        category="multilingual",
        lang="de",
        question="Wie viele Fenster hat die Wohnung B?",
        expectation=_count_exp(5, "window", _unit("B"), rationale="DE window count in Apt B = 5."),
    ),
    ChatProbe(
        id="de_quoted_bad_synonym",
        category="multilingual",
        lang="de",
        question="Wie viele Räume mit der Bezeichnung «Bad» gibt es?",
        expectation=_count_exp(
            5, "room", _label("Bain"),
            rationale="DE quoted synonym «Bad» maps to French CAD label Bain -> 5.",
        ),
    ),
    ChatProbe(
        id="de_wohnflaeche_apt_a",
        category="multilingual",
        lang="de",
        question="Wie gross ist die Wohnfläche von Wohnung A?",
        expectation=_area_exp(139.34, _unit("A"), rationale="DE net area of Apt A = 139.34 m²."),
    ),
    ChatProbe(
        id="fi_doors_floor",
        category="multilingual",
        lang="fi",
        question="Kuinka monta ovea tässä kerroksessa on?",
        expectation=_count_exp(22, "door", _floor(), rationale="FI door count on floor = 22."),
    ),
    ChatProbe(
        id="fi_area_apt_b",
        category="multilingual",
        lang="fi",
        question="Mikä on asunnon B pinta-ala?",
        expectation=_area_exp(90.55, _unit("B"), rationale="FI net area of Apt B = 90.55 m²."),
    ),
    ChatProbe(
        id="it_bathtubs_no_false_bagno_scope",
        category="multilingual",
        lang="it",
        question="Quante vasche da bagno ci sono in questo piano?",
        expectation=_count_exp(
            2, "bathtub", _floor(),
            rationale="IT 'vasche da bagno' counts bathtubs on whole floor (2), not Bagno label.",
        ),
    ),

    # ---- 9. Colloquial & voice phrasings ------------------------------------
    ChatProbe(
        id="colloquial_room_305_size",
        category="colloquial_and_voice",
        question="hey quick check, room 3.05, how big is it in m2?",
        expectation=_area_exp(
            13.81, _room("3.05"), rationale="Informal phrasing for room 3.05 area = 13.81 m²."
        ),
        tags=("colloquial",),
    ),
    ChatProbe(
        id="colloquial_ordering_sinks",
        category="colloquial_and_voice",
        question="we're ordering sanitaryware — how many sinks do we need for this floor?",
        expectation=_count_exp(
            5, "washbasin", _floor(), rationale="Site-procurement phrasing for washbasins = 5."
        ),
        tags=("colloquial",),
    ),
    ChatProbe(
        id="colloquial_one_word_toilets",
        category="colloquial_and_voice",
        question="toilets?",
        expectation=_count_exp(
            5, "wc", _floor(), rationale="Single-noun terse query defaults to count -> 5 WCs."
        ),
        tags=("colloquial",),
    ),
    ChatProbe(
        id="colloquial_ways_in_and_out_flat_b",
        category="colloquial_and_voice",
        question="how many ways in and out are there for flat B",
        expectation=_count_exp(
            9, "door", _unit("B"),
            rationale="Idiom 'ways in and out' for Flat B -> 9 doors via recorded agent fixture.",
        ),
        tags=("colloquial", "recorded_fixture"),
    ),

    # ---- 10. Analytical & relational ----------------------------------------
    ChatProbe(
        id="largest_room_on_floor",
        category="analytical_and_relational",
        question="Which room has the largest area on this floor?",
        expectation=_gap_exp(
            "note",
            value=38.57,
            scope=_floor(),
            allowed_kinds=("note", "text"),
            expected_substrings=("3.02",),
            rationale=(
                "Identifies the largest indoor room (3.02 Séjour/repas at 38.57 m²); "
                "must NOT return 261.96 m² (sum of whole floor)."
            ),
        ),
        tags=("misroute_trap",),
    ),
    ChatProbe(
        id="bedroom_most_windows",
        category="analytical_and_relational",
        question="Which bedroom has the most windows?",
        expectation=_gap_exp(
            "note",
            value="3.19",
            allowed_kinds=("note", "text"),
            expected_substrings=("3.19",),
            rationale="Bedroom 3.19 has 2 windows while 3.04/3.05/3.07/3.10/3.16 have 1 each.",
        ),
    ),
    ChatProbe(
        id="rooms_with_a_window",
        category="analytical_and_relational",
        question="How many rooms have at least one window?",
        expectation=_gap_exp(
            "count", value=9, subject="room", tolerance_abs=1.0,
            rationale=(
                "9 indoor rooms touch a window (or 8 by box centroid: 3.02, 3.04, 3.05, 3.07, "
                "3.10, 3.15, 3.16, 3.17, 3.19); NOT 11 (includes outdoor terraces), 12, or 20."
            ),
        ),
        tags=("misroute_trap",),
    ),
    ChatProbe(
        id="rooms_more_than_two_doors",
        category="analytical_and_relational",
        question="How many rooms have more than two doors?",
        expectation=_gap_exp(
            "count", value=2, subject="room",
            rationale="Only corridors 3.08 (5 doors) and 3.21 (6 doors) have >2 doors -> 2 rooms.",
        ),
    ),
    ChatProbe(
        id="compare_apt_a_vs_b_area",
        category="analytical_and_relational",
        question="How much larger is apartment A than apartment B?",
        expectation=_gap_exp(
            "area", value=48.79, allowed_kinds=("area", "note"),
            rationale="Difference is 139.34 - 90.55 = 48.79 m², neither A nor B alone.",
        ),
    ),
    ChatProbe(
        id="rooms_adjacent_to_kitchen",
        category="analytical_and_relational",
        question="Which rooms are adjacent to the kitchen in apartment A?",
        expectation=_gap_exp(
            "note",
            value=2,
            allowed_kinds=("note", "text", "count"),
            expected_substrings=("3.02", "3.04"),
            rationale="Kitchen 3.03 shares walls/openings with 3.02 (Séjour/repas) and 3.04.",
        ),
    ),

    # ---- 11. Dimensions & quantities ----------------------------------------
    ChatProbe(
        id="room_304_width_length",
        category="dimensions_and_quantities",
        question="What are the width and length of room 3.04?",
        expectation=_gap_exp(
            "quantity",
            rationale="Room 3.04 bounding rect is 4.135 m × 4.12 m, not exposed via /chat.",
        ),
    ),
    ChatProbe(
        id="room_304_perimeter",
        category="dimensions_and_quantities",
        question="What is the perimeter of room 3.04?",
        expectation=_gap_exp(
            "quantity",
            rationale="Polygon perimeter can be derived from CAD vertices, but measure() is IFC-only.",
        ),
    ),
    ChatProbe(
        id="total_wall_length_dxf",
        category="dimensions_and_quantities",
        question="What is the total wall length on this floor?",
        expectation=_gap_exp(
            "quantity",
            rationale="Wall length is IFC-only in measure(); CAD DXF abstains with not_supported_yet.",
        ),
    ),
    ChatProbe(
        id="whole_building_doors_dxf",
        category="dimensions_and_quantities",
        question="How many doors are in the entire building across all floors?",
        expectation=_refuse_exp(
            "swiss-46639 is a single 3rd-floor DXF plan; other storeys are not on this drawing."
        ),
    ),
    ChatProbe(
        id="wall_volume_dxf",
        category="dimensions_and_quantities",
        question="What is the total concrete volume of the walls?",
        expectation=_refuse_exp(
            "2D floor plan has no wall heights or 3D solid volumes (requires IFC)."
        ),
    ),

    # ---- 12. Out-of-scope & invalid targets ---------------------------------
    ChatProbe(
        id="off_drawing_ceiling_height",
        category="out_of_scope_and_invalid",
        question="What is the floor-to-ceiling height in apartment A?",
        expectation=_refuse_exp("Ceiling height is shown on sections, not on this 2D plan."),
    ),
    ChatProbe(
        id="off_drawing_wall_material",
        category="out_of_scope_and_invalid",
        question="What material are the exterior walls made of?",
        expectation=_refuse_exp("Wall materials/specs are not printed on swiss-46639."),
    ),
    ChatProbe(
        id="off_drawing_fire_rating",
        category="out_of_scope_and_invalid",
        question="What is the fire rating of the apartment entrance doors?",
        expectation=_refuse_exp(
            "No EI30/EI60/REI fire-resistance text exists among the 70 printed texts."
        ),
    ),
    ChatProbe(
        id="off_drawing_building_owner_recorded",
        category="out_of_scope_and_invalid",
        question="who owns this building?",
        expectation=_refuse_exp("Building ownership is not on the floor plan (recorded fixture)."),
        tags=("recorded_fixture",),
    ),
    ChatProbe(
        id="invalid_room_999",
        category="out_of_scope_and_invalid",
        question="How many windows are in room 9.99?",
        expectation=_refuse_exp("Room 9.99 does not exist on swiss-46639 (valid: 3.01–3.24)."),
    ),
    ChatProbe(
        id="invalid_apartment_c",
        category="out_of_scope_and_invalid",
        question="What is the floor area of apartment C?",
        expectation=_refuse_exp("Apartment C does not exist on swiss-46639 (valid: A, B)."),
    ),
    ChatProbe(
        id="invalid_quoted_sauna_count",
        category="out_of_scope_and_invalid",
        question="How many rooms labelled 'Sauna' are on this floor?",
        expectation=_refuse_exp("No room is labelled Sauna on swiss-46639."),
    ),
    ChatProbe(
        id="invalid_unquoted_sauna_area_trap",
        category="out_of_scope_and_invalid",
        question="What is the floor area of the sauna?",
        expectation=_refuse_exp(
            "swiss-46639 has no sauna; must abstain (or 0), NEVER return 261.96 m² floor area."
        ),
        tags=("scope_collapse_trap",),
    ),
    ChatProbe(
        id="non_building_chitchat",
        category="out_of_scope_and_invalid",
        question="Tell me a joke about architects.",
        expectation=_refuse_exp("Chitchat unrelated to floor plan evaluation."),
    ),
)


# ---------------------------------------------------------------------------
# Multi-turn conversation probes (`category="multi_turn_conversations"`)
# ---------------------------------------------------------------------------

SWISS_46639_CONVERSATIONS: tuple[ConversationProbe, ...] = (
    ConversationProbe(
        conversation_key="conv_openings_walkthrough",
        category="multi_turn_conversations",
        title="Openings follow-up chain: Apt A doors -> Apt B -> windows -> non-cued -> floor reset",
        turns=(
            ConversationTurnSpec(
                turn_index=1,
                id="conv1_t1_doors_apt_a",
                message="How many doors are in apartment A?",
                expectation=_count_exp(
                    13, "door", _unit("A"), rationale="Turn 1 sets count+door in unit:A -> 13."
                ),
            ),
            ConversationTurnSpec(
                turn_index=2,
                id="conv1_t2_followup_apt_b",
                message="and in apartment B?",
                expectation=_count_exp(
                    9, "door", _unit("B"),
                    rationale="Cued follow-up inherits subject=door with new scope unit:B -> 9.",
                ),
            ),
            ConversationTurnSpec(
                turn_index=3,
                id="conv1_t3_followup_windows",
                message="and windows?",
                expectation=_count_exp(
                    5, "window", _unit("B"),
                    rationale="Cued follow-up switches subject=window while keeping unit:B -> 5.",
                ),
            ),
            ConversationTurnSpec(
                turn_index=4,
                id="conv1_t4_fresh_question_no_leak",
                message="How many bathtubs?",
                expectation=_count_exp(
                    2, "bathtub", _floor(),
                    rationale="Non-cued question must NOT inherit unit:B; floor bathtubs -> 2.",
                ),
            ),
            ConversationTurnSpec(
                turn_index=5,
                id="conv1_t5_reset_whole_floor",
                message="and doors in total?",
                expectation=_count_exp(
                    22, "door", _floor(),
                    rationale="'in total' resets scope to whole floor -> 22 doors.",
                ),
            ),
        ),
    ),
    ConversationProbe(
        conversation_key="conv_areas_walkthrough",
        category="multi_turn_conversations",
        title="Area follow-up chain: Apt A area -> Apt B -> room 3.04 -> whole floor",
        turns=(
            ConversationTurnSpec(
                turn_index=1,
                id="conv2_t1_area_apt_a",
                message="What is the floor area of apartment A?",
                expectation=_area_exp(
                    139.34, _unit("A"), rationale="Turn 1 sets area intent in unit:A -> 139.34 m²."
                ),
            ),
            ConversationTurnSpec(
                turn_index=2,
                id="conv2_t2_what_about_apt_b",
                message="what about apartment B?",
                expectation=_area_exp(
                    90.55, _unit("B"),
                    rationale="Cued follow-up inherits area intent for unit:B -> 90.55 m².",
                ),
            ),
            ConversationTurnSpec(
                turn_index=3,
                id="conv2_t3_and_room_304",
                message="and room 3.04?",
                expectation=_area_exp(
                    17.04, _room("3.04"),
                    rationale="Cued follow-up inherits area intent for room 3.04 -> 17.04 m².",
                ),
            ),
            ConversationTurnSpec(
                turn_index=4,
                id="conv2_t4_and_in_total",
                message="and in total?",
                expectation=_area_exp(
                    261.96, _floor(),
                    rationale="'in total' resets area scope to whole floor -> 261.96 m².",
                ),
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Universal dynamic probe builder for ANY drawing (CAD DXF, IFC, Raster, HTTP)
# ---------------------------------------------------------------------------

_CONCEPT_SYNONYMS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "living",
        "living room",
        "living rooms",
        (
            "séjour/repas", "séjour", "wohnen/essen", "wohnen",
            "soggiorno/pranzo", "soggiorno", "living", "living room", "olohuone",
        ),
    ),
    (
        "bedroom",
        "bedroom",
        "bedrooms",
        (
            "chambre à coucher", "schlafzimmer", "camera da letto",
            "bedroom", "chambre", "zimmer", "pièce", "stanza", "makuuhuone",
        ),
    ),
    (
        "kitchen",
        "kitchen",
        "kitchens",
        ("cuisine", "küche", "küche/essen", "cucina", "kitchen", "keittiö"),
    ),
    (
        "bathroom",
        "bathroom",
        "bathrooms",
        ("bain", "bad", "bagno", "bathroom", "bath", "kylpyhuone"),
    ),
    (
        "corridor",
        "corridor",
        "corridors",
        ("couloir", "korridor", "corridoio", "corridor", "hall", "käytävä", "eteinen"),
    ),
    (
        "storage",
        "storage room",
        "storage rooms",
        ("réduit", "reduit", "ripostiglio", "kellerabteil", "vano cantina", "storage", "varasto"),
    ),
    (
        "outdoor",
        "terrace",
        "terraces",
        ("terrasse", "balcon", "balkon", "balcone", "loggia", "balcony", "terrace", "parveke"),
    ),
)

_STAIR_LABELS: frozenset[str] = frozenset({
    "cage d'escalier", "treppenhaus", "vano scala", "staircase", "stair", "porrashuone",
})


def _plural_subject(subj: str) -> str:
    if subj == "wc":
        return "WCs"
    return f"{subj}s"


def _match_plan_concepts(rooms: list[dict[str, Any]]) -> dict[str, tuple[str, str, str]]:
    """Map concept keys -> `(printed_label, singular_en, plural_en)` present on this plan."""
    raw_names = [str(r.get("name") or "").strip() for r in rooms if str(r.get("name") or "").strip()]
    unique_names = list(dict.fromkeys(raw_names))
    found: dict[str, tuple[str, str, str]] = {}
    for concept, sing_en, plur_en, candidates in _CONCEPT_SYNONYMS:
        for cand in candidates:
            hit = next(
                (
                    nm for nm in unique_names
                    if nm.casefold() == cand or nm.casefold().startswith(cand + "/")
                ),
                None,
            )
            if hit is not None:
                found[concept] = (hit, sing_en, plur_en)
                break
    return found


def build_dynamic_probes(
    drawing_data: dict[str, Any],
    loaded_drawing: Any | None = None,
) -> tuple[list[ChatProbe], list[ConversationProbe]]:
    """Derive a comprehensive architectural coverage suite directly from ANY plan's engine/JSON.

    Supports:
    - Swiss CAD DXF (`SwissEngine` `model.json` or `BundleEngine` `drawing.json`, incl. uploads)
    - IFC 3D/2D BIM (`IfcEngine` `model.json`, incl. wall/slab quantities & `ScopeModel`)
    - Raster PNG/PDF (`RasterEngine` `raster.json`)
    - Remote HTTP `--api-url` (`loaded_drawing=None`, where `public_drawing` strips `evidence`)
    """
    from aura import tools

    did = str(drawing_data.get("id", "unknown"))
    kind = str(drawing_data.get("kind", "cad_dxf"))
    rooms: list[dict[str, Any]] = list(drawing_data.get("rooms") or [])
    units: list[str] = [str(u["id"]) for u in (drawing_data.get("units") or []) if "id" in u]
    has_inline_ev = isinstance(drawing_data.get("evidence"), dict)
    evidence: dict[str, list[dict[str, Any]]] = dict(drawing_data.get("evidence") or {})
    raw_caps = drawing_data.get("capabilities")
    if isinstance(raw_caps, list):
        cap_subjects = {str(x) for x in raw_caps}
    elif isinstance(raw_caps, dict):
        cap_subjects = {str(x) for x in (raw_caps.get("counts") or [])}
    else:
        cap_subjects = set()

    probes: list[ChatProbe] = []
    conversations: list[ConversationProbe] = []

    def _subject_supported(subj: str) -> bool:
        if loaded_drawing is not None:
            return bool(loaded_drawing.engine.supports(subj))
        if has_inline_ev:
            return subj in evidence
        return subj in cap_subjects

    def _exact_count(subj: str, scope: dict[str, Any]) -> tuple[bool, int | None]:
        """Return `(supported, count_or_none_if_remote_stripped)`."""
        if loaded_drawing is not None:
            tr = tools.count_objects(loaded_drawing, subj, scope)
            if not tr.supported or tr.kind == "none" or tr.value is None:
                return False, None
            return True, int(tr.value)
        if has_inline_ev and subj in evidence:
            boxes = evidence[subj]
            sk = scope.get("kind", "floor")
            if sk == "floor":
                return True, len(boxes)
            if sk == "unit":
                return True, sum(1 for b in boxes if b.get("unit") == scope.get("unit"))
            if sk == "room":
                return True, sum(1 for b in boxes if b.get("room_id") == scope.get("room_id"))
            if sk == "label":
                lbl_rooms = {
                    str(r["room_id"])
                    for r in rooms
                    if str(r.get("name") or "").casefold() == str(scope.get("label") or "").casefold()
                }
                return True, sum(1 for b in boxes if str(b.get("room_id") or "") in lbl_rooms)
            return False, None
        if subj in cap_subjects and scope.get("kind") == "floor":
            # Remote `--api-url` stripped `evidence`; verify kind, subject, scope, and box count invariant
            return True, None
        return False, None

    def _make_count_exp(
        val: int | None,
        subj: str,
        scope: dict[str, Any],
        rationale: str,
    ) -> ProbeExpectation:
        if val is None:
            return ProbeExpectation(
                answerable_on_drawing=True,
                expected_kind="count",
                expected_value=None,
                expected_subject=subj,
                expected_scope=scope,
                allow_any_value=True,
                rationale=rationale,
            )
        return _count_exp(val, subj, scope, rationale=rationale)

    # ------------------------------------------------------------------
    # 1. Doors, Windows & Sanitary Fixtures (floor, locate, unit, room)
    # ------------------------------------------------------------------
    box_specs = (
        ("door", "doors_and_openings"),
        ("window", "doors_and_openings"),
        ("washbasin", "sanitary_and_fixtures"),
        ("wc", "sanitary_and_fixtures"),
        ("bathtub", "sanitary_and_fixtures"),
        ("shower", "sanitary_and_fixtures"),
    )
    for subj, cat in box_specs:
        if not _subject_supported(subj):
            continue
        ok_f, cnt_f = _exact_count(subj, _floor())
        if not ok_f:
            continue
        plural = _plural_subject(subj)
        probes.append(
            ChatProbe(
                id=f"dyn_{subj}_floor",
                category=cat,
                drawing_id=did,
                question=f"How many {plural} are on this floor plan?",
                expectation=_make_count_exp(
                    cnt_f, subj, _floor(),
                    f"Dynamic {subj} count on floor = {cnt_f if cnt_f is not None else 'remote'}.",
                ),
            )
        )
        if subj in ("door", "window"):
            verb = "Check for all" if subj == "door" else "Show all"
            probes.append(
                ChatProbe(
                    id=f"dyn_{subj}_check_all",
                    category=cat,
                    drawing_id=did,
                    question=f"{verb} {plural} on this plan",
                    expectation=_make_count_exp(
                        cnt_f, subj, _floor(),
                        f"Imperative '{verb.lower()} {plural}' phrasing on {did}.",
                    ),
                )
            )
        for u in units[:2]:
            ok_u, cnt_u = _exact_count(subj, _unit(u))
            if ok_u:
                probes.append(
                    ChatProbe(
                        id=f"dyn_{subj}_unit_{u}",
                        category=cat,
                        drawing_id=did,
                        question=f"How many {plural} are in apartment {u}?",
                        expectation=_make_count_exp(
                            cnt_u, subj, _unit(u),
                            f"Dynamic {subj} count in unit {u} = {cnt_u}.",
                        ),
                    )
                )
        if subj in ("door", "window") and rooms:
            pos_room: tuple[str, int] | None = None
            zero_room: str | None = None
            for r in rooms:
                if r.get("outdoor"):
                    continue
                rid = str(r["room_id"])
                ok_r, cnt_r = _exact_count(subj, _room(rid))
                if ok_r and cnt_r is not None:
                    if cnt_r > 0 and pos_room is None:
                        pos_room = (rid, cnt_r)
                    elif cnt_r == 0 and zero_room is None:
                        zero_room = rid
            if pos_room is not None:
                probes.append(
                    ChatProbe(
                        id=f"dyn_{subj}_room_{pos_room[0]}",
                        category=cat,
                        drawing_id=did,
                        question=f"How many {plural} are in room {pos_room[0]}?",
                        expectation=_count_exp(
                            pos_room[1], subj, _room(pos_room[0]),
                            rationale=f"Room {pos_room[0]} contains {pos_room[1]} {subj}(s).",
                        ),
                    )
                )
            if zero_room is not None:
                probes.append(
                    ChatProbe(
                        id=f"dyn_{subj}_room_{zero_room}_zero",
                        category=cat,
                        drawing_id=did,
                        question=f"How many {plural} are in room {zero_room}?",
                        expectation=_count_exp(
                            0, subj, _room(zero_room),
                            rationale=f"Indoor room {zero_room} has 0 {subj}(s); must answer 0.",
                        ),
                    )
                )

    if _subject_supported("door"):
        probes.append(
            ChatProbe(
                id="dyn_doors_subtype_sliding",
                category="doors_and_openings",
                drawing_id=did,
                question="How many sliding doors are on this floor?",
                expectation=_gap_exp(
                    "count", subject="door",
                    rationale="Door sub-types (sliding vs hinged) are not classified; must not return total doors.",
                ),
                tags=("subtype_trap",),
            )
        )

    # ------------------------------------------------------------------
    # 2. Circulation & Stairs
    # ------------------------------------------------------------------
    stair_rooms = [
        r for r in rooms
        if str(r.get("name") or "").strip().casefold() in _STAIR_LABELS
    ]
    probes.extend([
        ChatProbe(
            id="dyn_stairs_how_many",
            category="circulation_and_stairs",
            drawing_id=did,
            question="How many stairs are there?",
            expectation=_gap_exp("count", rationale="Stair/step detector coverage probe."),
        ),
        ChatProbe(
            id="dyn_stairs_steps",
            category="circulation_and_stairs",
            drawing_id=did,
            question="How many steps are in the staircase?",
            expectation=_gap_exp("count", rationale="Individual stair treads are not extracted."),
        ),
    ])
    if stair_rooms and stair_rooms[0].get("area_m2") is not None:
        sr = stair_rooms[0]
        probes.append(
            ChatProbe(
                id=f"dyn_stairwell_room_{sr['room_id']}_area",
                category="circulation_and_stairs",
                drawing_id=did,
                question=f"What is the floor area of room {sr['room_id']}?",
                expectation=_area_exp(
                    round(float(sr["area_m2"]), 2), _room(str(sr["room_id"])),
                    rationale=f"Stairwell room {sr['room_id']} area = {sr['area_m2']} m².",
                ),
            )
        )

    # ------------------------------------------------------------------
    # 3. Rooms, Units & Concept Synonym Mapping
    # ------------------------------------------------------------------
    concepts = _match_plan_concepts(rooms)
    indoor_rooms = [r for r in rooms if not r.get("outdoor")]
    indoor_with_area = [r for r in indoor_rooms if r.get("area_m2") is not None]

    if units:
        probes.append(
            ChatProbe(
                id="dyn_apartments_count",
                category="rooms_and_units",
                drawing_id=did,
                question="How many apartments are on this floor?",
                expectation=_count_exp(
                    len(units), "apartment", _floor(),
                    rationale=f"Plan defines {len(units)} unit(s): {', '.join(units)}.",
                ),
            )
        )

    if indoor_rooms:
        probes.append(
            ChatProbe(
                id="dyn_indoor_rooms_count",
                category="rooms_and_units",
                drawing_id=did,
                question="How many numbered indoor rooms are on this floor?",
                expectation=_count_exp(
                    len(indoor_rooms), "room", _floor(),
                    rationale=f"Plan defines {len(indoor_rooms)} non-outdoor rooms.",
                ),
            )
        )
        if units:
            u0 = units[0]
            u0_rooms = [r for r in indoor_rooms if r.get("unit") == u0]
            if u0_rooms:
                probes.append(
                    ChatProbe(
                        id=f"dyn_rooms_unit_{u0}",
                        category="rooms_and_units",
                        drawing_id=did,
                        question=f"How many rooms are in apartment {u0}?",
                        expectation=_count_exp(
                            len(u0_rooms), "room", _unit(u0),
                            rationale=f"Unit {u0} has {len(u0_rooms)} indoor rooms.",
                        ),
                    )
                )

    for concept_key in ("bedroom", "bathroom", "kitchen", "living", "outdoor"):
        if concept_key not in concepts:
            continue
        lbl, _sing_en, plur_en = concepts[concept_key]
        lbl_rooms = [r for r in rooms if str(r.get("name") or "").casefold() == lbl.casefold()]
        if lbl_rooms:
            probes.append(
                ChatProbe(
                    id=f"dyn_rooms_concept_{concept_key}",
                    category="rooms_and_units",
                    drawing_id=did,
                    question=f"How many {plur_en} are on this floor?",
                    expectation=_count_exp(
                        len(lbl_rooms), "room", _label(lbl),
                        rationale=f"{len(lbl_rooms)} room(s) match concept {concept_key!r} ({lbl!r}).",
                    ),
                )
            )
            probes.append(
                ChatProbe(
                    id=f"dyn_rooms_quoted_{concept_key}",
                    category="rooms_and_units",
                    drawing_id=did,
                    question=f"How many rooms are labelled '{lbl}'?",
                    expectation=_count_exp(
                        len(lbl_rooms), "room", _label(lbl),
                        rationale=f"Exact quoted label '{lbl}' appears on {len(lbl_rooms)} room(s).",
                    ),
                )
            )
            break  # keep suite focused while still testing concept + quoted label

    # ------------------------------------------------------------------
    # 4. Room Sizes, Floor Areas & Natural-Phrasing Misroute Traps
    # ------------------------------------------------------------------
    if indoor_with_area:
        floor_area = round(sum(float(r["area_m2"]) for r in indoor_with_area), 2)
        probes.append(
            ChatProbe(
                id="dyn_floor_area",
                category="room_sizes_and_areas",
                drawing_id=did,
                question="What is the total indoor floor area?",
                expectation=_area_exp(
                    floor_area, _floor(),
                    rationale=f"Sum of {len(indoor_with_area)} indoor rooms = {floor_area} m².",
                ),
            )
        )
        for r in indoor_with_area[:2]:
            rid = str(r["room_id"])
            ra = round(float(r["area_m2"]), 2)
            probes.append(
                ChatProbe(
                    id=f"dyn_room_{rid}_area",
                    category="room_sizes_and_areas",
                    drawing_id=did,
                    question=f"What is the floor area of room {rid}?",
                    expectation=_area_exp(
                        ra, _room(rid),
                        rationale=f"Room {rid} ({r.get('name', '')}) area = {ra} m².",
                    ),
                )
            )
        for u in units[:2]:
            u_in = [r for r in indoor_with_area if r.get("unit") == u]
            if u_in:
                ua = round(sum(float(r["area_m2"]) for r in u_in), 2)
                probes.append(
                    ChatProbe(
                        id=f"dyn_unit_{u}_area",
                        category="room_sizes_and_areas",
                        drawing_id=did,
                        question=f"What is the floor area of apartment {u}?",
                        expectation=_area_exp(
                            ua, _unit(u),
                            rationale=f"Unit {u} indoor rooms sum to {ua} m².",
                        ),
                    )
                )

        # Concept area + natural "floor size" phrasing + compound unit+label scope probe
        area_concept = next(
            (k for k in ("living", "bedroom", "kitchen", "bathroom") if k in concepts),
            None,
        )
        if area_concept is not None:
            c_lbl, c_sing, _c_plur = concepts[area_concept]
            c_rooms = [
                r for r in rooms
                if str(r.get("name") or "").casefold() == c_lbl.casefold()
                and r.get("area_m2") is not None
            ]
            if c_rooms:
                c_area = round(sum(float(r["area_m2"]) for r in c_rooms), 2)
                probes.append(
                    ChatProbe(
                        id=f"dyn_{area_concept}_area_explicit",
                        category="room_sizes_and_areas",
                        drawing_id=did,
                        question=f"What is the area of the {c_sing}?",
                        expectation=_area_exp(
                            c_area, _label(c_lbl),
                            rationale=f"Explicit area of {c_sing} ({c_lbl!r}) = {c_area} m².",
                        ),
                    )
                )
                if c_area != floor_area:
                    allowed_sc = (
                        (_room(str(c_rooms[0]["room_id"])),) if len(c_rooms) == 1 else ()
                    )
                    probes.append(
                        ChatProbe(
                            id=f"dyn_{area_concept}_floor_size_phrasing",
                            category="room_sizes_and_areas",
                            drawing_id=did,
                            question=f"What is the floor size of the {c_sing}?",
                            expectation=_area_exp(
                                c_area, _label(c_lbl),
                                allowed_scopes=allowed_sc,
                                rationale=(
                                    f"Natural phrasing 'floor size of the {c_sing}' must return "
                                    f"{c_area} m² ({c_lbl!r}), NOT {floor_area} m² whole floor."
                                ),
                            ),
                            tags=("misroute_trap",),
                        )
                    )
                if len(units) >= 2:
                    u0 = units[0]
                    u0_c_rooms = [r for r in c_rooms if r.get("unit") == u0]
                    u0_all = [r for r in indoor_with_area if r.get("unit") == u0]
                    if u0_c_rooms and u0_all:
                        u0_c_area = round(sum(float(r["area_m2"]) for r in u0_c_rooms), 2)
                        u0_tot_area = round(sum(float(r["area_m2"]) for r in u0_all), 2)
                        if u0_c_area != c_area and u0_c_area != u0_tot_area:
                            allowed_u0 = (
                                (_room(str(u0_c_rooms[0]["room_id"])),)
                                if len(u0_c_rooms) == 1
                                else ()
                            )
                            probes.append(
                                ChatProbe(
                                    id=f"dyn_{area_concept}_in_unit_{u0}_area",
                                    category="room_sizes_and_areas",
                                    drawing_id=did,
                                    question=f"What is the area of the {c_sing} in apartment {u0}?",
                                    expectation=_area_exp(
                                        u0_c_area,
                                        allowed_scopes=allowed_u0,
                                        rationale=(
                                            f"Compound unit+label scope: {c_sing} in apartment {u0} "
                                            f"= {u0_c_area} m² (neither whole unit {u0_tot_area} m² "
                                            f"nor all {c_lbl} {c_area} m²)."
                                        ),
                                    ),
                                    tags=("compound_scope_trap",),
                                )
                            )

    elif kind == "raster":
        # Raster plans have no CAD room outlines; area should abstain cleanly (`not_supported_yet`)
        probes.append(
            ChatProbe(
                id="dyn_raster_area_gap",
                category="room_sizes_and_areas",
                drawing_id=did,
                question="What is the total indoor floor area?",
                expectation=_gap_exp(
                    "area",
                    rationale="Raster image has no calibrated CAD room polygons yet.",
                ),
            )
        )

    # ------------------------------------------------------------------
    # 5. Spatial Interactive Tools (Region Mark + What-If Overrides)
    # ------------------------------------------------------------------
    labelled_indoor = [
        r for r in indoor_with_area
        if isinstance(r.get("label_at"), list) and len(r["label_at"]) == 2
    ]
    if labelled_indoor:
        seed = labelled_indoor[0]
        lx, ly = float(seed["label_at"][0]), float(seed["label_at"][1])
        rx = round(max(0.0, lx - 6.0), 2)
        ry = round(max(0.0, ly - 6.0), 2)
        rw = round(min(100.0 - rx, 12.0), 2)
        rh = round(min(100.0 - ry, 12.0), 2)
        dyn_region = {"kind": "region", "rect": {"x": rx, "y": ry, "w": rw, "h": rh}, "label": "Area 1"}
        if loaded_drawing is not None:
            reg_tr = tools.room_areas(loaded_drawing, dyn_region)
            reg_val = round(float(reg_tr.value), 2) if reg_tr.value is not None else None
        else:
            reg_hits = [
                r for r in indoor_with_area
                if isinstance(r.get("label_at"), list)
                and len(r["label_at"]) == 2
                and rx <= float(r["label_at"][0]) <= rx + rw
                and ry <= float(r["label_at"][1]) <= ry + rh
            ]
            reg_val = round(sum(float(r["area_m2"]) for r in reg_hits), 2) if reg_hits else None
        if reg_val is not None and reg_val > 0:
            probes.append(
                ChatProbe(
                    id="dyn_region_mark_area",
                    category="spatial_interactive_tools",
                    drawing_id=did,
                    question="What is the floor area here?",
                    scope=dyn_region,
                    expectation=_area_exp(
                        reg_val, dyn_region,
                        rationale=f"Synthesized region around room {seed['room_id']} = {reg_val} m².",
                    ),
                )
            )

    rect_rooms = [
        r for r in indoor_with_area
        if isinstance(r.get("rect"), dict) or r.get("area_m2") is not None
    ]
    if rect_rooms and indoor_with_area:
        target_r = rect_rooms[0]
        tr_id = str(target_r["room_id"])
        new_w, new_h = 4.0, 4.0
        ov = [{"room_id": tr_id, "w_m": new_w, "h_m": new_h}]
        if loaded_drawing is not None:
            wi_tr = tools.what_if(loaded_drawing, _floor(), ov)
            wi_val = round(float(wi_tr.value), 2) if wi_tr.value is not None else None
        else:
            base_floor = sum(float(r["area_m2"]) for r in indoor_with_area)
            wi_val = round(base_floor - float(target_r["area_m2"]) + round(new_w * new_h, 2), 2)
        if wi_val is not None:
            probes.append(
                ChatProbe(
                    id=f"dyn_what_if_room_{tr_id}",
                    category="spatial_interactive_tools",
                    drawing_id=did,
                    question="What is the total indoor floor area?",
                    overrides=ov,
                    expectation=_area_exp(
                        wi_val, _floor(),
                        rationale=f"Resizing room {tr_id} to 4.0m × 4.0m yields {wi_val} m² floor area.",
                    ),
                )
            )

    # ------------------------------------------------------------------
    # 6. Analytical & Relational (Room Ranking + Unit Comparison)
    # ------------------------------------------------------------------
    if len(indoor_with_area) >= 2:
        sorted_desc = sorted(indoor_with_area, key=lambda r: float(r["area_m2"]), reverse=True)
        max_area = round(float(sorted_desc[0]["area_m2"]), 2)
        top_tied = [r for r in sorted_desc if round(float(r["area_m2"]), 2) == max_area]
        expected_ids = (str(top_tied[0]["room_id"]),) if len(top_tied) == 1 else ()
        probes.append(
            ChatProbe(
                id="dyn_largest_room_on_floor",
                category="analytical_and_relational",
                drawing_id=did,
                question="Which room has the largest area on this floor?",
                expectation=_gap_exp(
                    "note",
                    value=max_area,
                    scope=_floor(),
                    allowed_kinds=("note", "text"),
                    expected_substrings=expected_ids,
                    rationale=f"Largest indoor room area = {max_area} m² ({sorted_desc[0]['room_id']}).",
                ),
            )
        )
        sorted_asc = list(reversed(sorted_desc))
        min_area = round(float(sorted_asc[0]["area_m2"]), 2)
        bot_tied = [r for r in sorted_asc if round(float(r["area_m2"]), 2) == min_area]
        min_ids = (str(bot_tied[0]["room_id"]),) if len(bot_tied) == 1 else ()
        probes.append(
            ChatProbe(
                id="dyn_smallest_room_on_floor",
                category="analytical_and_relational",
                drawing_id=did,
                question="Which room has the smallest area on this floor?",
                expectation=_gap_exp(
                    "note",
                    value=min_area,
                    scope=_floor(),
                    allowed_kinds=("note", "text"),
                    expected_substrings=min_ids,
                    rationale=f"Smallest indoor room area = {min_area} m² ({sorted_asc[0]['room_id']}).",
                ),
            )
        )

    if len(units) >= 2 and indoor_with_area:
        u0, u1 = units[0], units[1]
        a0 = round(sum(float(r["area_m2"]) for r in indoor_with_area if r.get("unit") == u0), 2)
        a1 = round(sum(float(r["area_m2"]) for r in indoor_with_area if r.get("unit") == u1), 2)
        if a0 > 0 and a1 > 0 and a0 != a1:
            big_u, small_u = (u0, u1) if a0 > a1 else (u1, u0)
            diff = round(abs(a0 - a1), 2)
            probes.append(
                ChatProbe(
                    id=f"dyn_compare_unit_{big_u}_vs_{small_u}",
                    category="analytical_and_relational",
                    drawing_id=did,
                    question=f"How much larger is apartment {big_u} than apartment {small_u}?",
                    expectation=_gap_exp(
                        "area",
                        value=diff,
                        allowed_kinds=("area", "note"),
                        rationale=f"Unit {big_u} minus Unit {small_u} = {diff} m².",
                    ),
                )
            )

    # ------------------------------------------------------------------
    # 7. Dimensions & Quantities (IFC measure() when kind=='ifc', else gap)
    # ------------------------------------------------------------------
    if kind == "ifc" and loaded_drawing is not None:
        for fam, q_text in (
            ("wall_length", "What is the total wall length on this floor?"),
            ("wall_volume", "What is the total wall volume on this floor?"),
            ("slab_volume", "What is the slab volume on this floor?"),
        ):
            mtr = tools.measure(loaded_drawing, fam, _floor())
            if mtr.supported and mtr.kind == "quantity" and mtr.value is not None:
                probes.append(
                    ChatProbe(
                        id=f"dyn_ifc_{fam}",
                        category="dimensions_and_quantities",
                        drawing_id=did,
                        question=q_text,
                        expectation=ProbeExpectation(
                            answerable_on_drawing=True,
                            expected_kind="quantity",
                            expected_value=round(float(mtr.value), 2),
                            expected_scope=_floor(),
                            rationale=f"IFC quantity {fam} = {mtr.value}.",
                        ),
                    )
                )
    else:
        probes.extend([
            ChatProbe(
                id="dyn_wall_length_gap",
                category="dimensions_and_quantities",
                drawing_id=did,
                question="What is the total wall length on this floor?",
                expectation=_gap_exp(
                    "quantity",
                    rationale="Wall length is IFC-only in measure(); 2D plans abstain not_supported_yet.",
                ),
            ),
            ChatProbe(
                id="dyn_whole_building_doors",
                category="dimensions_and_quantities",
                drawing_id=did,
                question="How many doors are in the entire building across all floors?",
                expectation=_refuse_exp("Single-storey plan has no multi-storey building model."),
            ),
        ])

    # ------------------------------------------------------------------
    # 8. Multilingual (FR / DE / FI / IT) & Colloquial Phrasings
    # ------------------------------------------------------------------
    if _subject_supported("door"):
        _, d_cnt = _exact_count("door", _floor())
        probes.extend([
            ChatProbe(
                id="dyn_fr_doors_floor",
                category="multilingual",
                drawing_id=did,
                lang="fr",
                question="Combien de portes y a-t-il sur ce plan ?",
                expectation=_make_count_exp(d_cnt, "door", _floor(), "FR floor door count."),
            ),
            ChatProbe(
                id="dyn_fi_doors_floor",
                category="multilingual",
                drawing_id=did,
                lang="fi",
                question="Kuinka monta ovea tässä kerroksessa on?",
                expectation=_make_count_exp(d_cnt, "door", _floor(), "FI floor door count."),
            ),
        ])
    if _subject_supported("window"):
        _, w_cnt = _exact_count("window", _floor())
        probes.extend([
            ChatProbe(
                id="dyn_de_windows_floor",
                category="multilingual",
                drawing_id=did,
                lang="de",
                question="Wie viele Fenster gibt es auf diesem Grundriss?",
                expectation=_make_count_exp(w_cnt, "window", _floor(), "DE floor window count."),
            ),
            ChatProbe(
                id="dyn_it_windows_floor",
                category="multilingual",
                drawing_id=did,
                lang="it",
                question="Quante finestre ci sono in questa pianta?",
                expectation=_make_count_exp(w_cnt, "window", _floor(), "IT floor window count."),
            ),
        ])
    if indoor_with_area:
        r0 = indoor_with_area[0]
        probes.append(
            ChatProbe(
                id=f"dyn_colloquial_room_{r0['room_id']}_size",
                category="colloquial_and_voice",
                drawing_id=did,
                question=f"hey quick check, room {r0['room_id']}, how big is it in m2?",
                expectation=_area_exp(
                    round(float(r0["area_m2"]), 2), _room(str(r0["room_id"])),
                    rationale=f"Colloquial room size check for {r0['room_id']}.",
                ),
                tags=("colloquial",),
            )
        )

    # ------------------------------------------------------------------
    # 9. Out-of-Scope & Dynamically Guaranteed Invalid IDs
    # ------------------------------------------------------------------
    existing_room_ids = {str(r.get("room_id", "")) for r in rooms}
    missing_room_id = next(
        cand for cand in ("99.99", "88.88", "77.77", "999.99") if cand not in existing_room_ids
    )
    existing_units = {u.upper() for u in units}
    missing_unit = next(
        cand for cand in ("Z", "Y", "X", "W", "Q", "V") if cand not in existing_units
    )

    probes.extend([
        ChatProbe(
            id="dyn_off_ceiling_height",
            category="out_of_scope_and_invalid",
            drawing_id=did,
            question="What is the floor-to-ceiling height?",
            expectation=_refuse_exp("Ceiling height is not shown on a 2D floor plan."),
        ),
        ChatProbe(
            id="dyn_off_wall_material",
            category="out_of_scope_and_invalid",
            drawing_id=did,
            question="What material are the exterior walls made of?",
            expectation=_refuse_exp("Wall materials/specs are not printed on the plan."),
        ),
        ChatProbe(
            id=f"dyn_invalid_room_{missing_room_id}",
            category="out_of_scope_and_invalid",
            drawing_id=did,
            question=f"How many doors are in room {missing_room_id}?",
            expectation=_refuse_exp(f"Room {missing_room_id} does not exist on {did}."),
        ),
        ChatProbe(
            id=f"dyn_invalid_apartment_{missing_unit}",
            category="out_of_scope_and_invalid",
            drawing_id=did,
            question=f"What is the floor area of apartment {missing_unit}?",
            expectation=_refuse_exp(f"Apartment {missing_unit} does not exist on {did}."),
        ),
    ])

    # ------------------------------------------------------------------
    # 10. Multi-Turn Stateful Follow-Up Chains
    # ------------------------------------------------------------------
    if _subject_supported("door"):
        _, d_all = _exact_count("door", _floor())
        if len(units) >= 2:
            u0, u1 = units[0], units[1]
            _, d0 = _exact_count("door", _unit(u0))
            _, d1 = _exact_count("door", _unit(u1))
            conversations.append(
                ConversationProbe(
                    conversation_key=f"dyn_conv_doors_{did}",
                    category="multi_turn_conversations",
                    title=f"Dynamic door follow-up chain ({u0} -> {u1} -> total)",
                    drawing_id=did,
                    turns=(
                        ConversationTurnSpec(
                            turn_index=1,
                            id="dyn_conv_doors_t1",
                            message=f"How many doors are in apartment {u0}?",
                            expectation=_make_count_exp(d0, "door", _unit(u0), f"Doors in {u0}."),
                        ),
                        ConversationTurnSpec(
                            turn_index=2,
                            id="dyn_conv_doors_t2",
                            message=f"and in apartment {u1}?",
                            expectation=_make_count_exp(d1, "door", _unit(u1), f"Doors in {u1}."),
                        ),
                        ConversationTurnSpec(
                            turn_index=3,
                            id="dyn_conv_doors_t3",
                            message="and doors in total?",
                            expectation=_make_count_exp(d_all, "door", _floor(), "Total doors."),
                        ),
                    ),
                )
            )
        elif indoor_rooms:
            r0_id = str(indoor_rooms[0]["room_id"])
            ok_r0, dr0 = _exact_count("door", _room(r0_id))
            if ok_r0 and dr0 is not None:
                conversations.append(
                    ConversationProbe(
                        conversation_key=f"dyn_conv_room_doors_{did}",
                        category="multi_turn_conversations",
                        title=f"Dynamic room-to-floor door follow-up ({r0_id} -> total)",
                        drawing_id=did,
                        turns=(
                            ConversationTurnSpec(
                                turn_index=1,
                                id="dyn_conv_rdoors_t1",
                                message=f"How many doors are in room {r0_id}?",
                                expectation=_count_exp(dr0, "door", _room(r0_id)),
                            ),
                            ConversationTurnSpec(
                                turn_index=2,
                                id="dyn_conv_rdoors_t2",
                                message="and doors in total on this floor?",
                                expectation=_make_count_exp(d_all, "door", _floor(), "Total doors."),
                            ),
                        ),
                    )
                )

    if len(indoor_with_area) >= 2:
        r0, r1 = indoor_with_area[0], indoor_with_area[1]
        r0_id, r1_id = str(r0["room_id"]), str(r1["room_id"])
        tot_a = round(sum(float(r["area_m2"]) for r in indoor_with_area), 2)
        conversations.append(
            ConversationProbe(
                conversation_key=f"dyn_conv_areas_{did}",
                category="multi_turn_conversations",
                title=f"Dynamic area scope shift ({r0_id} -> {r1_id} -> total)",
                drawing_id=did,
                turns=(
                    ConversationTurnSpec(
                        turn_index=1,
                        id="dyn_conv_area_t1",
                        message=f"What is the floor area of room {r0_id}?",
                        expectation=_area_exp(round(float(r0["area_m2"]), 2), _room(r0_id)),
                    ),
                    ConversationTurnSpec(
                        turn_index=2,
                        id="dyn_conv_area_t2",
                        message=f"and of room {r1_id}?",
                        expectation=_area_exp(round(float(r1["area_m2"]), 2), _room(r1_id)),
                    ),
                    ConversationTurnSpec(
                        turn_index=3,
                        id="dyn_conv_area_t3",
                        message="and in total?",
                        expectation=_area_exp(tot_a, _floor()),
                    ),
                ),
            )
        )

    return probes, conversations


def suite_for_drawing(
    drawing_id: str,
    drawing_data: dict[str, Any] | None = None,
    *,
    loaded_drawing: Any | None = None,
    dynamic_only: bool = False,
) -> tuple[list[ChatProbe], list[ConversationProbe]]:
    """Return curated suite for `swiss-46639` (unless `dynamic_only=True`), else universal dynamic probes."""
    if drawing_id == SWISS_DRAWING_ID and not dynamic_only:
        return list(SWISS_46639_PROBES), list(SWISS_46639_CONVERSATIONS)
    if drawing_data is None:
        if loaded_drawing is not None:
            drawing_data = loaded_drawing.drawing
        else:
            raise ValueError(
                f"Drawing '{drawing_id}' requires drawing_data or loaded_drawing to build dynamic probes."
            )
    return build_dynamic_probes(drawing_data, loaded_drawing=loaded_drawing)
