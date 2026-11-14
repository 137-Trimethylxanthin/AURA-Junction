"""Pydantic mirror of frontend/src/api/contract.ts (the source of truth), plus ToolResult.

Rules from contract.md: optional fields are omitted, never null (serialise with
`exclude_none=True`); minor versions only add optional fields.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.2"

Lang = Literal["en", "fr", "fi", "de"]
LANGS: tuple[str, ...] = ("en", "fr", "fi", "de")
GroupColor = Literal["c1", "c2", "c3", "c4", "c5", "c6"]
GROUP_COLORS: tuple[GroupColor, ...] = ("c1", "c2", "c3", "c4", "c5", "c6")
Subject = Literal["window", "door", "washbasin", "wc", "bathtub", "shower", "room", "apartment",
                  # 1.1: image-only plans, Decisions score estimates (no boxes)
                  "bedroom", "kitchen", "bathroom", "wc_room", "sauna", "sink",
                  "shower_or_bathtub", "storey", "balcony"]
TextSubject = Literal["label", "fire_rating"]
BoxSubject = Subject | TextSubject
BOX_SUBJECTS: tuple[str, ...] = ("window", "door", "washbasin", "wc", "bathtub", "shower")
# pdf_vector (1.2 additive): read from a PDF's vector paths and text by the drawing store (no model).
Method = Literal["cad_geometry", "ocr", "detector", "vision", "agent_estimate", "pdf_vector",
                 "store_sql", "none"]  # store_sql (1.2): an exact SQL answer over the drawing store
TraceKind = Literal["exact", "model", "warn", "info"]
AbstainReason = Literal["not_on_drawing", "not_supported_yet", "unclear_question",
                        "low_confidence",  # 1.1: a model estimate below its confidence gate
                        "not_answerable"]  # 1.2: no tier could answer from the drawing's data
ErrorCode = Literal[
    "bad_request",
    "unknown_drawing",
    "internal",
    "too_large",
    "unsupported_type",
    "rate_limited",
    "budget_exhausted",
    "presenter_required",
    "processing",
    "timeout",
    "not_found",
]  # timeout: SSE `error` event; /ask answers 504
ToolName = Literal[
    "count_objects",
    "room_areas",
    "list_rooms",
    "locate",
    "scope_to_region",
    "compare_with_original",
    "check_boxes_with_decisions",
    "what_if",
    "find_text",
    "deep_agent",
    "sql",  # 1.2: a guarded SELECT over the drawing store (tool steps)
]
Route = Literal["template", "choice", "agent", "cache"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    """Inbound bodies and drawing files: tolerate extra keys."""

    model_config = ConfigDict(extra="ignore")


# ---- shared -----------------------------------------------------------------------------

Pct = Annotated[float, Field(allow_inf_nan=False, ge=0.0, le=100.0)]
PctPoint = tuple[Pct, Pct]
NonEmptyId = Annotated[str, Field(min_length=1, max_length=200)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class PctRect(_M):
    x: float
    y: float
    w: float
    h: float


class Box(_M):
    id: str
    x: float
    y: float
    w: float
    h: float
    subject: BoxSubject  # 'label' / 'fire_rating': printed-text boxes (text in `note`)
    room_id: str | None = None
    unit: str | None = None
    note: str | None = None
    duplicate_of: str | None = None  # second copy of an object drawn twice (Swiss CAD only)
    group_id: str | None = None
    drawing_id: str | None = None


class ScopeFloor(_M):
    kind: Literal["floor"] = "floor"


class ScopeModel(_M):
    """1.1: the whole building model (IFC only), not just the drawing's storey."""

    kind: Literal["model"] = "model"


class ScopeUnit(_M):
    kind: Literal["unit"] = "unit"
    unit: str


class ScopeRoom(_M):
    kind: Literal["room"] = "room"
    room_id: str


class ScopeLabel(_M):
    kind: Literal["label"] = "label"
    label: str


class ScopeRegion(_M):
    kind: Literal["region"] = "region"
    rect: PctRect
    label: str


class ScopeProject(_M):
    """1.2: multi-plan house/project bundle scope across multiple floor drawings."""

    kind: Literal["project"] = "project"
    project_id: NonEmptyId
    drawing_ids: list[NonEmptyId] = Field(min_length=1, max_length=50)


Scope = Annotated[
    ScopeFloor | ScopeModel | ScopeUnit | ScopeRoom | ScopeLabel | ScopeRegion | ScopeProject,
    Field(discriminator="kind"),
]


class Override(_M):
    room_id: str
    w_m: float
    h_m: float


class AskRequest(_Open):
    drawing_id: str = Field(min_length=1, max_length=200)
    question: str = Field(max_length=2000)
    scope: Scope | None = None
    # 1.2 additive: a sheet of a multi-plan building is answered for the WHOLE building unless the
    # question names a floor or `sheet_ids` selects some sheets (default: every sheet).
    project_id: str | None = Field(default=None, max_length=200)
    sheet_ids: list[str] | None = Field(default=None, max_length=50)
    overrides: list[Override] | None = Field(default=None, max_length=50)
    lang_hint: Lang | None = None


class ChatRequest(_Open):
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    drawing_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=2000)
    scope: Scope | None = None
    project_id: str | None = Field(default=None, max_length=200)  # see AskRequest
    sheet_ids: list[str] | None = Field(default=None, max_length=50)
    overrides: list[Override] | None = Field(default=None, max_length=50)
    lang_hint: Lang | None = None


class RewriteRequest(_Open):
    drawing_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)


# ---- answer -----------------------------------------------------------------------------


class AnswerCount(_M):
    kind: Literal["count"] = "count"
    value: int
    subject: Subject


class AnswerArea(_M):
    kind: Literal["area"] = "area"
    value: float
    unit: Literal["m2"] = "m2"


class AnswerAbstain(_M):
    kind: Literal["abstain"] = "abstain"
    reason: AbstainReason
    where_to_find: str | None = None


class AnswerText(_M):
    kind: Literal["text"] = "text"
    value: str
    subject: Literal["fire_rating", "label"]


class AnswerQuantity(_M):
    """1.1: a measured quantity given as the IFC model states it (never recomputed)."""

    kind: Literal["quantity"] = "quantity"
    value: float
    unit: Literal["m", "m2", "m3"]


class AnswerNote(_M):
    """1.2: open-ended answer from the deep agent (unmapped unit, text/comparison, or open subject)."""

    kind: Literal["note"] = "note"
    value: str
    number: float | None = None
    unit: str | None = None


Answer = Annotated[
    AnswerCount | AnswerArea | AnswerAbstain | AnswerQuantity | AnswerText | AnswerNote,
    Field(discriminator="kind"),
]


class EvidenceRoom(_M):
    room_id: str
    included: bool
    area_m2: float | None = None  # None: no printed area and no outline (never 0)
    edited: bool | None = None
    group_id: str | None = None
    drawing_id: str | None = None


class EvidenceRow(_M):
    """An element counted without a 2D box (IFC GlobalId rows)."""

    id: str
    name: str | None = None
    value: float | None = None


class EvidenceGroup(_M):
    """1.2: categorical color-coded evidence group (e.g. Apartment A, Stair S1 -> 1. OG)."""

    id: NonEmptyId
    label: Annotated[str, Field(min_length=1, max_length=200)]
    color: GroupColor
    target_drawing_id: str | None = None


class EvidenceLink(_M):
    """1.2: directional vector on a plan (e.g. stair/ramp/circulation lead-to route)."""

    id: NonEmptyId
    from_pos: PctPoint
    to_pos: PctPoint
    waypoints: list[PctPoint] | None = None
    group_id: str | None = None
    label: str | None = None
    drawing_id: str | None = None
    target_drawing_id: str | None = None


class Evidence(_M):
    boxes: list[Box] = Field(default_factory=list)
    rooms: list[EvidenceRoom] = Field(default_factory=list)
    rows: list[EvidenceRow] | None = None  # counted elements that have no 2D box (IFC)
    groups: list[EvidenceGroup] | None = None
    links: list[EvidenceLink] | None = None


class Recall(_M):
    value: float
    n: int
    source: str


class Check(_M):
    method: Literal["decisions_api"] = "decisions_api"
    status: Literal["sample", "live"]
    agreed: int | None = None
    total: int | None = None
    per_box: dict[str, float] | None = None
    # 1.1
    scope: Literal["drawn_boxes"] | None = None
    ranking: list[str] | None = None
    least_certain_id: str | None = None
    anchors_max: float | None = None
    image: Literal["dataset_png", "upload", "none"] | None = None
    recall: Recall | None = None
    # Cost of the check itself (the answer's own cost is the top-level calls/cost_usd).
    calls: int | None = None  # Decisions requests made for this answer's check: 1 live, 0 cached
    cost_usd: float | None = None
    tokens: int | None = None
    cached: bool | None = None


class Measured(_M):
    correct: int
    n: int
    source: str


class Estimate(_M):
    """A count read by a model from an image: its confidence, the probabilities of each level,
    and the measured accuracy of answers at that confidence."""

    confidence: float
    probabilities: dict[str, float] | None = None
    measured: Measured | None = None


class Gold(_M):
    dataset_qid: str
    value: float
    tolerance: Literal["exact", "2pct"]
    match: bool


class TraceTool(_M):
    name: ToolName
    args: dict[str, Any]
    result_id: str
    ms: int


class TraceNode(_M):
    id: str
    parent: str | None = None
    title: str
    tag: str
    body: str
    kind: TraceKind
    evidence_ids: list[str] | None = None
    confidence: float | Literal["sample"] | None = None
    tool: TraceTool | None = None
    # 1.2 additive (AURA_TRACE_DETAIL=dev): which answering stage this node is, plus its
    # structured detail. stage 'decision' = the routing decision (rules or gemini-decision-1);
    # its detail: {decider, model?, question?, options?, chosen, confidence?, runner_up?,
    # runner_up_confidence?, matched, route}. 'resources': {tables, grabbed, ms}.
    # 'filter': {rule, kept, dropped[{id, reason}]}. 'answer': {source, calls, cost_usd}.
    stage: Literal["resources", "decision", "filter", "answer", "scope"] | None = None
    detail: dict[str, Any] | None = None


class BreakdownItem(_M):
    """1.2 additive: one sheet's share of a whole-building answer ("ground floor 14")."""

    drawing_id: str
    label: str
    status: Literal["ok", "abstain", "error"]
    value: float | None = None  # the sheet's count / area / quantity when it is summable
    unit: str | None = None


class Converted(_M):
    value: float
    unit: str  # display symbol: "sq ft", "ft", "m²", ...
    text: str  # "124 m² = 1,334.72 sq ft"


class AskResponse(_M):
    contract_version: str = CONTRACT_VERSION
    # 1.2 additive: set only when the deep agent failed (the answer is then an abstain); the
    # frontend shows a neutral "Ask again" card instead of a verdict.
    agent_failure: Literal["unavailable", "timeout", "error"] | None = None
    answer: Answer
    evidence: Evidence
    detected_lang: Lang
    canonical_en: str
    scope: Scope
    method: Method
    check: Check | None = None
    gold: Gold | None = None
    trace: list[TraceNode]
    calls: int
    cost_usd: float
    latency_ms: int
    source: Literal["api", "local"] = "api"
    # 1.1
    answer_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    route: Route | None = None
    explanation: str | None = None
    estimate: Estimate | None = None  # 1.1: a model estimate (image-only plans), never a fact
    # 1.2 additive: per-sheet shares of a whole-building answer; evidence items carry their sheet's
    # `drawing_id`, `scope` is the project scope of the sheets that were asked.
    breakdown: list[BreakdownItem] | None = None
    # additive: the answer shown in the unit the question asked for ("in square feet"); `answer`
    # stays metric. Converted by code from `answer`, never by a model.
    converted: Converted | None = None


class ErrorBody(_M):
    contract_version: str = CONTRACT_VERSION
    error: str
    code: ErrorCode


# ---- drawings ---------------------------------------------------------------------------


class Frame(_Open):
    dxf_min: tuple[float, float]
    dxf_max: tuple[float, float]
    margin_m: float
    m_per_unit: float
    y_flip: Literal[True]
    px: tuple[int, int]


class RoomRect(_Open):
    x: float
    y: float
    w: float
    h: float
    w_m: float
    h_m: float


class RoomPoly(_Open):
    room_id: str
    num: str
    name: str
    unit: str | None = None
    area_m2: float | None = None  # None: no printed area and no outline (never 0)
    outdoor: bool
    points: list[tuple[float, float]]
    label_at: tuple[float, float]
    rect: RoomRect | None = None
    area_source: Literal["printed", "outline"] | None = None


class Credit(_Open):
    text: str
    license: str
    url: str | None = None
    license_name: str | None = None


class Facts(_Open):
    gold_total: int
    correct: int
    measured_at: str
    languages: list[str] | None = None


class Drawing(_Open):
    contract_version: str
    id: str
    title: str
    subtitle: str | None = None
    frame: Frame
    m_per_pct_x: float
    m_per_pct_y: float
    images: dict[str, str]
    rooms: list[RoomPoly]
    units: list[dict[str, str]]
    credit: Credit
    # 1.1
    kind: Literal["cad_dxf", "raster", "ifc"] | None = None
    status: Literal["queued", "processing", "ready", "failed"] | None = None
    progress: float | None = None
    error: str | None = None
    capabilities: list[Subject] | None = None
    source: Literal["library", "upload"] | None = None
    thumbnail: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    facts: Facts | None = None
    # 1.2
    source_ext: str | None = None
    project_id: str | None = None
    floor_label: str | None = None
    floor_order: int | FiniteFloat | None = None


class DrawingSummary(_M):
    id: str
    title: str
    kind: Literal["cad_dxf", "raster", "ifc"]
    status: Literal["queued", "processing", "ready", "failed"]
    thumbnail: str
    facts: Facts | None = None
    credit: Credit
    subtitle: str | None = None
    source: Literal["library", "upload"] | None = None
    progress: float | None = None
    error: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    source_ext: str | None = None
    project_id: str | None = None
    floor_label: str | None = None
    floor_order: int | FiniteFloat | None = None


class ProjectFloor(_M):
    drawing_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    order: int | FiniteFloat


class ProjectBundle(_M):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    floors: list[ProjectFloor] = Field(default_factory=list, max_length=50)
    auto_matched: bool | None = None
    created_at: str | None = None


class ProjectList(_M):
    contract_version: str = CONTRACT_VERSION
    projects: list[ProjectBundle] = Field(default_factory=list)


class Health(_M):
    ok: bool
    contract_version: str = CONTRACT_VERSION
    checks: dict[str, Any] | None = None
    answerer: Literal["hybrid", "agent", "exact"] | None = None
    agent_ok: bool | None = None


# ---- tool results (shared with L1's solvers) --------------------------------------------


class ToolResult(BaseModel):
    """What every tool returns. Answer values are set ONLY from a ToolResult (answer.py).

    `numbers` lists every number the result exposes, so the provenance filter can keep
    model prose honest. `value` is the headline number (count or m²) or None.
    """

    model_config = ConfigDict(extra="forbid")

    result_id: str
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)
    kind: Literal["count", "area", "text", "quantity", "note", "rooms", "boxes", "check", "none"]
    subject: str | None = None
    value: float | None = None
    text: str | None = None
    numbers: list[float] = Field(default_factory=list)
    boxes: list[Box] = Field(default_factory=list)
    rooms: list[EvidenceRoom] = Field(default_factory=list)
    groups: list[EvidenceGroup] = Field(default_factory=list)
    links: list[EvidenceLink] = Field(default_factory=list)
    summary: str = ""
    method: Method = "cad_geometry"
    supported: bool = True  # False: the drawing has no layer for this subject
    ms: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("groups", "links", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return [] if v is None else v
