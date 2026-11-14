"""The agent: same instructions as the experiment harness (agent/SYSTEM.md + agent/VISION.md), two tools,
structured output instead of an answers.json file."""
from __future__ import annotations

from typing import Literal

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.models.interface import Model
from agents.run_config import CallModelData, ModelInputData
from openai import AsyncOpenAI, BadRequestError
from openai.types.shared import Reasoning
from pydantic import BaseModel, Field

from .config import ROOT, Settings
from .tools import TOOLS, RunCtx

PROMPT_FILES = (ROOT / "agent" / "SYSTEM.md", ROOT / "agent" / "VISION.md")
MAX_EVIDENCE_ITEMS = 250
MAX_RELAYED_IMAGES = 4

# Appended to SYSTEM.md + VISION.md to adapt tool names, structured output, precomputed workspace data,
# multi-group color coding, directional "leads-to" vectors, and multi-plan floor matching.
ADAPTER = """

Service adapter - this overrides the text above wherever they differ
- Tools: the shell is the tool `shell` (wherever "bash", "Bash" or "a bash shell" is mentioned); images are shown
  with the tool `view_image` (wherever "the Read tool" is mentioned). Save crops for `view_image` as
  `crop_<x0>_<y0>_<x1>_<y1>.png` in full-plan pixel coordinates so live crop telemetry highlights the inspected area.
- Precomputed workspace fast-path: if `_precomputed/` exists in your working directory (`_precomputed/index.json`,
  `_precomputed/areas.json`, `_precomputed/project_manifest.json`, `_precomputed/tiles/`, and `_precomputed_2/` ...
  when multiple plans are present), read `_precomputed/index.json`, `_precomputed/areas.json`, and
  `_precomputed/project_manifest.json` FIRST before writing exploratory Python scripts or rendering matplotlib plots!
- Final answer: do NOT write answers.json and do NOT reply DONE. Return the answers as your final structured output:
  one entry per question, in order. Set `kind` on each evidence item to `"ifc"` (GlobalId evidence), `"cad"` (DXF
  handle evidence), `"image"` (pixel-box evidence) or `"text"` (a text/label you read), and put the IFC class into
  `ifc_class`. Cap `evidence` at `<= 250` items (`evidence_total` holds the true total count when more exist).
  Leave keys that do not apply null.
- Directional / facade filtering (e.g. "show all north windows", "east facade doors"): inspect the drawing/title block
  for a printed North arrow or compass rose first; if none is printed, use standard architectural plan-top = North and
  record `"Assumed plan-top = North (no compass arrow printed)"` in `check`. Include ONLY confirmed matching facade
  elements in `evidence` (drop non-matching candidates; never return all windows or `null` when matching ones exist).
- Reference-region similarity queries (e.g. "how many spots like this are there", "find all symbols like the marked
  area"): first crop that exact reference region (`crop_<x0>_<y0>_<x1>_<y1>.png`), call `view_image` on it (and inspect
  any CAD block/layer/polygon inside those coordinates) to identify what symbol, fixture, space, or structural element
  is marked, then search the ENTIRE drawing and return ALL matching occurrences across the full plan in `evidence`
  (never restrict the search solely inside the reference crop, and never abstain as unclear when a reference region is
  given).
- Multi-turn follow-up refinements: when the question includes a `Prior conversation context:` section and the user
  asks a follow-up refinement (e.g. `"show only north"`, `"exclude balconies"`, `"color in different colors"`), resolve
  implicit references (`"they"`, `"those"`, `"only ..."`, etc.) against the previous turn's subject and items —
  filtering that exact set (e.g. keeping only north-side rooms/areas from the prior turn) or assigning distinct `group`
  + `group_label` categories (e.g. by apartment unit, room function, or zone) across the items so AURA renders them in
  distinct categorical colors.
- Multi-group color coding, directional "leads-to" vectors & multi-plan matching:
  - Whenever multiple files are in the workspace (`drawing.<ext>`, `drawing_2.<ext>`, ...), set `file` on EVERY
    evidence item to the workspace filename it belongs to (e.g. `"drawing.pdf"` or `"drawing_2.pdf"`).
  - When asked about multiple groups, apartments, zones, stairs ("how many apartments are there", "show all stairs and
    where they lead to"), circulation routes, or matching elements across floors: assign a consistent `group` id
    (e.g. `"apt-A"`, `"apt-B"`, `"stair-S1"`) and human-readable `group_label` (e.g. `"Apartment A"`,
    `"Main Stair S1 -> 1. OG"`) across related evidence items and across floors.
  - When showing where a stair, ramp, door, shaft or route leads, populate `lead_to_px: [x_to, y_to]` (full-image
    pixels) or `lead_to_cad: [x_to, y_to]` (CAD drawing units) plus `lead_to_label` (e.g. `"Leads to 1. OG / Hallway"`),
    and set `target_file` (e.g. `"drawing_2.pdf"`) when the destination is on another floor plan in the workspace.
- `answer` is a number for counts and quantities, a string for open questions, null if the file cannot answer it.
- Room areas on floor plans: the shell has `junction-areas <file>` (PDF, DXF or PNG/JPG floor plan). It prints JSON:
  every room it found with number, label, area in m2 (printed value when the plan prints one, else measured from the
  room outline), apartment and how the area was obtained, plus the scale it used. Use it (or `_precomputed/areas.json`
  if already present) for area and apartment questions, then cross-check before answering.
"""


class Evidence(BaseModel):
    """One thing an answer rests on.  Only the keys that apply to its kind are set."""
    kind: Literal["ifc", "cad", "image", "text"]
    guid: str | None = None
    ifc_class: str | None = None
    name: str | None = None
    handle: str | None = None
    layer: str | None = None
    bbox: list[float] | None = Field(None, description="[xmin, ymin, xmax, ymax] in drawing units")
    bbox_px: list[float] | None = Field(None, description="[x0, y0, x1, y1] in pixels of the full image")
    image: str | None = Field(None, description="file name of the full-page image bbox_px refers to")
    text: str | None = None
    label: str | None = None
    note: str | None = Field(None, description="any other detail about this item, e.g. its storey or a property")
    file: str | None = Field(None, description="workspace filename this item belongs to, e.g. drawing.pdf or drawing_2.pdf")
    group: str | None = Field(None, description="categorical color-group id when grouping apartments, zones, stairs or routes, e.g. apt-A or stair-S1")
    group_label: str | None = Field(None, description="human-readable legend label for `group`, e.g. 'Apartment A' or 'Main Stair S1 -> 1. OG'")
    lead_to_px: list[float] | None = Field(None, description="[x_to, y_to] in full-image pixels indicating where a stair, ramp, door or route leads")
    lead_to_cad: list[float] | None = Field(None, description="[x_to, y_to] in CAD drawing units indicating where a stair, ramp, door or route leads")
    lead_to_label: str | None = Field(None, description="short label for the destination, e.g. 'Leads to 1. OG走廊 / Hallway 2.01'")
    target_file: str | None = Field(None, description="workspace filename of the destination floor plan when leading to another floor, e.g. drawing_2.pdf")


class Answer(BaseModel):
    n: int = Field(description="1-based number of the question")
    answer: str | float | int | None
    unit: str | None
    method: str
    evidence: list[Evidence]
    evidence_total: int | None
    check: str
    confidence: float


class Answers(BaseModel):
    answers: list[Answer]


def instructions() -> str:
    """Read at agent-build time, so the service always uses the prompt files as they are on disk."""
    return "".join(p.read_text(encoding="utf-8") for p in PROMPT_FILES) + ADAPTER


def build_model_settings(reasoning_effort: str | None = None) -> ModelSettings:
    if reasoning_effort:
        return ModelSettings(include_usage=True, reasoning=Reasoning(effort=reasoning_effort))  # type: ignore[arg-type]
    return ModelSettings(include_usage=True)


def build_agent(reasoning_effort: str | None = None) -> Agent[RunCtx]:
    # The model is chosen per run through RunConfig.model, so one definition serves real and scripted models.
    return Agent[RunCtx](name="drawing-qa", instructions=instructions(), tools=list(TOOLS), output_type=Answers,
                         model_settings=build_model_settings(reasoning_effort))


class _ReindexedStream:
    """Gemini's OpenAI-compatible stream sends parallel tool calls with the same `index` but different ids; the SDK
    groups deltas by index and would glue them into one malformed call.  Give every new call id its own index."""

    def __init__(self, stream):
        self._stream, self._by_orig, self._next = stream, {}, 0

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        async for chunk in self._stream:
            for choice in getattr(chunk, "choices", None) or []:
                for tc in getattr(choice.delta, "tool_calls", None) or []:
                    if tc.id:  # first delta of a new call
                        self._by_orig[tc.index] = self._next
                        self._next += 1
                    tc.index = self._by_orig.get(tc.index, tc.index)
            yield chunk

    def __getattr__(self, name):  # close(), response, ... go to the real stream
        return getattr(self._stream, name)


class _Completions:
    def __init__(self, inner):
        self._inner = inner
        self._drop_reasoning = False

    async def create(self, **kw):
        if self._drop_reasoning:
            kw.pop("reasoning_effort", None)
        try:
            ret = await self._inner.create(**kw)
        except BadRequestError:
            if "reasoning_effort" not in kw:
                raise
            kw = {k: v for k, v in kw.items() if k != "reasoning_effort"}
            ret = await self._inner.create(**kw)
            self._drop_reasoning = True
        return _ReindexedStream(ret) if kw.get("stream") else ret

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Chat:
    def __init__(self, inner):
        self.completions = _Completions(inner.completions)


class ChatModelWithParallelCalls(OpenAIChatCompletionsModel):
    """Chat Completions model whose client re-indexes streamed parallel tool calls and falls back cleanly on HTTP 400
    if an OpenAI-compatible proxy rejects `reasoning_effort`."""

    def _get_client(self):
        client = super()._get_client()
        if not hasattr(client, "_junction_wrapped"):
            client.chat = _Chat(client.chat)
            client._junction_wrapped = True
        return client


def build_model(s: Settings) -> str | Model:
    """What to hand the SDK as the model.

    Direct: the model name; the SDK's default provider resolves it to the OpenAI Responses API.
    Bifrost: a Responses model on an OpenAI client pointed at the gateway.  The Responses API rather than Chat
    Completions because the SDK's Chat Completions path drops images from tool outputs (view_image), and a Model
    instance rather than a name because the SDK's name routing would strip or reject the "<provider>/" prefix
    that Bifrost needs to see."""
    if s.model == "demo":  # scripted stand-in for frontend work (service/demo.py)
        from .demo import DemoModel
        return DemoModel()
    if s.openai_base_url:  # any OpenAI-compatible endpoint, e.g. Gemini's (Chat Completions only)
        client = AsyncOpenAI(base_url=s.openai_base_url, api_key=s.openai_api_key or "none")
        if s.api_style == "chat":
            return ChatModelWithParallelCalls(model=s.model, openai_client=client)
        return OpenAIResponsesModel(model=s.model, openai_client=client)
    if s.bifrost_url is None:
        return s.model
    gw_auth = getattr(s, "bifrost_api_key") or "none"
    client = AsyncOpenAI(base_url=s.bifrost_api_base, api_key=gw_auth)
    return OpenAIResponsesModel(model=s.model, openai_client=client)


def relay_tool_images(data: CallModelData) -> ModelInputData:
    """Chat Completions cannot carry images inside tool results (the SDK drops them there), so before every model
    call each image a tool returned is replaced by a short note in the tool result and re-sent as a user message
    right after the block of tool results it belongs to.  Only the latest `MAX_RELAYED_IMAGES` (4) `view_image`
    outputs keep their full base64 image blocks; older ones are replaced with a compact text note."""
    items, out, pending = list(data.model_data.input), [], []

    image_call_indices = [
        idx for idx, it in enumerate(items)
        if isinstance(it, dict)
        and it.get("type") == "function_call_output"
        and isinstance(it.get("output"), list)
        and any(isinstance(p, dict) and p.get("type") == "input_image" for p in it["output"])
    ]
    keep_indices = set(image_call_indices[-MAX_RELAYED_IMAGES:])

    def flush():
        if pending:
            out.append({"role": "user", "content": [p for pair in pending for p in pair]})
            pending.clear()

    for idx, it in enumerate(items):
        is_output = isinstance(it, dict) and it.get("type") == "function_call_output"
        if not is_output:
            flush()
            out.append(it)
            continue
        o = it.get("output")
        if isinstance(o, list) and any(isinstance(p, dict) and p.get("type") == "input_image" for p in o):
            texts = [p.get("text", "") for p in o if isinstance(p, dict) and p.get("type") == "input_text"]
            if idx in keep_indices:
                imgs = [p for p in o if isinstance(p, dict) and p.get("type") == "input_image"]
                out.append({**it, "output": " ".join(texts + ["(image attached in the next message)"]).strip()})
                for im in imgs:
                    pending.append([
                        {"type": "input_text", "text": f"Image returned by tool call {it.get('call_id')}:"},
                        {"type": "input_image", "image_url": im.get("image_url"), "detail": im.get("detail") or "high"},
                    ])
            else:
                out.append({**it, "output": " ".join(texts + ["[earlier image view omitted from context to save tokens]"]).strip()})
        else:
            out.append(it)
    flush()
    return ModelInputData(input=out, instructions=data.model_data.instructions)


def parse_final(text: str) -> Answers | None:
    """The final answer from a model message, tolerating a code fence or prose around the JSON object (models
    behind a provider that does not enforce the schema do that)."""
    start, end = text.find("{"), text.rfind("}")
    try:
        return Answers.model_validate_json(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None


def task_prompt(names: list[str], workdir: str, questions: list[str]) -> str:
    """Same wording as agent/run.py, extended to several files."""
    files = f"File: {names[0]}" if len(names) == 1 else "Files: " + ", ".join(names)
    return (f"{files} (in your working directory {workdir}).\n\nQuestions:\n"
            + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)))


def normalise(out: Answers, n_questions: int) -> list[dict]:
    """Structured output -> plain answer dicts, exactly one per question, in order.

    Strict schemas force every key to be present, so nulls are dropped from evidence; integral floats become
    ints (a count of 5.0 is 5); evidence is capped at 250 items; missing questions come back as abstentions."""
    by_n = {a.n: a for a in out.answers}
    result = []
    for n in range(1, n_questions + 1):
        a = by_n.get(n)
        if a is None:
            result.append(dict(n=n, answer=None, unit=None, method="", evidence=[], evidence_total=0,
                               check="none", confidence=0.0))
            continue
        d = a.model_dump()
        if isinstance(d["answer"], float) and d["answer"].is_integer() and abs(d["answer"]) < 2 ** 53:
            d["answer"] = int(d["answer"])
        ev = [{k: v for k, v in e.items() if v is not None} for e in d["evidence"]]
        if d["evidence_total"] is None:
            d["evidence_total"] = len(ev)
        elif len(ev) > MAX_EVIDENCE_ITEMS:
            d["evidence_total"] = max(d["evidence_total"], len(ev))
        d["evidence"] = ev[:MAX_EVIDENCE_ITEMS]
        d["confidence"] = min(1.0, max(0.0, d["confidence"]))
        result.append(d)
    return result
