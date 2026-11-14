"""The agent: instructions from prompts/, three tools, a structured final answer, and what the service does
with that answer afterwards (verification against the run's sql results, evidence, per-source values)."""
from __future__ import annotations

import json
import re
from typing import Any

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel
from agents.models.interface import Model
from agents.run_config import CallModelData, ModelInputData
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel, Field

from ..db import DB
from . import store
from .config import STORE_ROOT, Settings
from .tools import TOOLS, RunCtx

PROMPT_FILES = (STORE_ROOT / "prompts" / "AGENT.md", STORE_ROOT / "prompts" / "STORE.md")


class Ref(BaseModel):
    """A Layer 1 primitive an answer rests on."""
    file_id: str
    ref_table: str = Field(description="cad_entity | pdf_path | pdf_text | raster_word | raster_detection | ifc_element")
    ref_key: str = Field(description="handle (cad_entity), guid (ifc_element), ref / row id (texts)")


class Answer(BaseModel):
    n: int = Field(description="1-based number of the question")
    answer: str | float | int | None = Field(description="number for counts and quantities, string for open questions, null if the store and files cannot answer")
    unit: str | None = Field(description='"count", "m2", "m", "m3", ... or null')
    method: str = Field(description="one sentence: which view and which rule produced the answer")
    sql: str | None = Field(description="the SQL statement that produced the number, exactly as executed")
    entity_ids: list[int] = Field(description="entity_id values (from the views) of the things the answer rests on")
    refs: list[Ref] = Field(description="Layer 1 references, when the answer does not come from entities")
    check: str = Field(description='what confirms the answer (agreement of sources, a second query), or "none"')
    confidence: float


class Answers(BaseModel):
    answers: list[Answer]


def instructions() -> str:
    """Read at agent-build time, so the service always uses the prompt files as they are on disk."""
    return "\n\n".join(p.read_text(encoding="utf-8").strip() for p in PROMPT_FILES)


def build_agent(s: Settings) -> Agent[RunCtx]:
    settings = ModelSettings(include_usage=True, parallel_tool_calls=False,  # parallel calls raced each other
                             reasoning=Reasoning(effort=s.reasoning_effort) if s.reasoning_effort != "default" else None)
    return Agent[RunCtx](name="drawing-store-qa", instructions=instructions(), tools=list(TOOLS), output_type=Answers,
                         model_settings=settings)


# ---------------------------------------------------------------------------------------------- Gemini adaptations
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

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _Completions:
    def __init__(self, inner):
        self._inner = inner

    async def create(self, **kw):
        ret = await self._inner.create(**kw)
        return _ReindexedStream(ret) if kw.get("stream") else ret

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Chat:
    def __init__(self, inner):
        self.completions = _Completions(inner.completions)


class ChatModelWithParallelCalls(OpenAIChatCompletionsModel):
    """Chat Completions model whose client re-indexes streamed parallel tool calls (see _ReindexedStream)."""

    def _get_client(self):
        client = super()._get_client()
        if not hasattr(client, "_jstore_wrapped"):
            client.chat = _Chat(client.chat)
            client._jstore_wrapped = True
        return client


def build_model(s: Settings) -> Model:
    if s.model == "demo":
        return DemoModel()
    client = AsyncOpenAI(base_url=s.openai_base_url, api_key=s.api_key or "missing-key")
    return ChatModelWithParallelCalls(model=s.model, openai_client=client)


def relay_tool_images(data: CallModelData) -> ModelInputData:
    """Chat Completions cannot carry images inside tool results (the SDK drops them there), so before every model
    call each image a tool returned is replaced by a short note in the tool result and re-sent as a user message
    right after the block of tool results it belongs to."""
    items, out, pending = list(data.model_data.input), [], []

    def flush():
        if pending:
            out.append({"role": "user", "content": [p for pair in pending for p in pair]})
            pending.clear()

    for it in items:
        if not (isinstance(it, dict) and it.get("type") == "function_call_output"):
            flush()
            out.append(it)
            continue
        o = it.get("output")
        if isinstance(o, list) and any(isinstance(p, dict) and p.get("type") == "input_image" for p in o):
            texts = [p.get("text", "") for p in o if isinstance(p, dict) and p.get("type") == "input_text"]
            imgs = [p for p in o if isinstance(p, dict) and p.get("type") == "input_image"]
            out.append({**it, "output": " ".join(texts + ["(image attached in the next message)"]).strip()})
            for im in imgs:
                pending.append([{"type": "input_text", "text": f"Image returned by tool call {it.get('call_id')}:"},
                                {"type": "input_image", "image_url": im.get("image_url"), "detail": im.get("detail") or "high"}])
        else:
            out.append(it)
    flush()
    return ModelInputData(input=out, instructions=data.model_data.instructions)


def parse_final(text: str) -> Answers | None:
    """The final answer from a model message, tolerating a code fence or prose around the JSON object."""
    start, end = text.find("{"), text.rfind("}")
    try:
        return Answers.model_validate_json(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- task prompt
def task_prompt(db: DB, scope: dict, questions: list[str]) -> str:
    """The run's scope and what the store holds for it, so the model does not spend a turn finding out."""
    drawings, fids = scope["drawings"], [f["file_id"] for f in scope["files"]]
    store.set_scope_superuser(db, drawings, fids)
    lines = [f"Drawing{'s' if len(drawings) != 1 else ''} in this run: {', '.join(drawings)}", "Files (q.files):"]
    pages = {r["file_id"]: r for r in db.run(
        "select file_id, count(*) n, count(image) renders from q.pages group by 1")}
    for f in scope["files"]:
        p = pages.get(f["file_id"], dict(n=0, renders=0))
        lines.append(f"- {f['file_id']}  {f['format']}  status {f['status']}  pages {p['n']} (rendered: {p['renders']})")
    c = db.one("""select (select count(*) from q.rooms) rooms, (select count(*) from q.apartments) apartments,
                         (select count(*) from q.doors) doors, (select count(*) from q.windows) windows,
                         (select count(*) from q.fixtures) fixtures, (select count(*) from q.issues) issues""")
    lines.append("Layer 2 rows: " + ", ".join(f"{k} {v}" for k, v in c.items())
                 + ("" if any(c.values()) else "  (nothing interpreted: answer from the Layer 1 views)"))
    l1 = db.one("""select (select count(*) from q.cad_entities) cad_entities, (select count(*) from q.pdf_paths) pdf_paths,
                          (select count(*) from q.pdf_texts) pdf_texts, (select count(*) from q.ocr_words) ocr_words,
                          (select count(*) from q.detections) detections, (select count(*) from q.ifc_elements) ifc_elements""")
    lines.append("Layer 1 rows: " + ", ".join(f"{k} {v}" for k, v in l1.items() if v))
    layers = db.run("select layer, sum(n_entities) n from q.cad_layers group by 1 order by 2 desc nulls last limit 40")
    if layers:
        lines.append("CAD layers (entities): " + ", ".join(f"{r['layer']} ({r['n']})" for r in layers))
    return "\n".join(lines) + "\n\nQuestions:\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))


# ---------------------------------------------------------------------------------------------- after the model
def normalise(out: Answers, n_questions: int) -> list[dict]:
    """Structured output -> plain answer dicts, exactly one per question, in order."""
    by_n = {a.n: a for a in out.answers}
    result = []
    for n in range(1, n_questions + 1):
        a = by_n.get(n)
        if a is None:
            result.append(dict(n=n, answer=None, unit=None, method="", sql=None, entity_ids=[], refs=[], check="none",
                               confidence=0.0))
            continue
        d = a.model_dump()
        if isinstance(d["answer"], float) and d["answer"].is_integer() and abs(d["answer"]) < 2 ** 53:
            d["answer"] = int(d["answer"])
        d["entity_ids"] = list(dict.fromkeys(d["entity_ids"]))
        d["confidence"] = min(1.0, max(0.0, d["confidence"]))
        result.append(d)
    return result


def _matches(v: float, c: float) -> bool:
    if float(v).is_integer() and float(c).is_integer():
        return v == c
    return abs(v - c) <= max(0.051, 0.005 * abs(c))  # the model rounds to one or two decimals


def backed(v: float, log: list[dict]) -> bool:
    """Is the number a cell, a row count or a column sum of one of the run's sql results?"""
    for entry in log:
        if _matches(v, float(entry["rows"])) or any(_matches(v, c) for c in entry["numbers"]) \
                or any(_matches(v, c) for c in entry["sums"].values()):
            return True
    return False


def verify(answer: dict, log: list[dict]) -> list[str]:
    """Every number in the final answer must come from a sql result of the run.  -> warnings"""
    a = answer["answer"]
    if isinstance(a, bool) or a is None:
        return []
    nums = [float(a)] if isinstance(a, (int, float)) else \
        [float(m.replace(",", ".")) for m in store.NUM.findall(a)][:20] if isinstance(a, str) else []
    missing = [v for v in dict.fromkeys(nums) if not backed(v, log)]
    out = []
    if missing:
        shown = ", ".join(f"{v:g}" for v in missing[:6])
        out.append(f"not verified: {shown} is not a cell, row count or column sum of any sql result of this run")
    if answer.get("sql") and not any(_same_sql(answer["sql"], e["query"]) for e in log):
        out.append("the sql given with the answer was not executed in this run")
    return out


def _same_sql(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s).strip().rstrip(";").strip().lower()  # noqa: E731
    return norm(a) == norm(b)


KIND = {"cad_entity": "cad", "ifc_element": "ifc", "pdf_text": "text", "raster_word": "text"}


def _evidence_item(r: dict, label: str | None = None) -> dict:
    """An evidence region in the chat contract's shape (kind cad | ifc | text | image) plus the store's keys."""
    kind = KIND.get(r.get("ref_table"), "image")
    item = dict(kind=kind, label=label, entity_id=r.get("entity_id"), file_id=r["file_id"], format=r.get("format"),
                page_no=r.get("page_no"), ref_table=r.get("ref_table"), ref_key=r.get("ref_key"), role=r.get("role"),
                bbox=r.get("box"), box_pct=r.get("box_pct"), bbox_px=r.get("box_px"), image_url=r.get("image"),
                image=f"page-{r['file_id']}-{r.get('page_no', 0)}.png" if r.get("image") else None,
                layer=r.get("layer"), text=r.get("text"), name=r.get("name"), ifc_class=r.get("ifc_class"),
                handle=r.get("ref_key") if kind == "cad" else None, guid=r.get("ref_key") if kind == "ifc" else None,
                note="reference not found in the store" if r.get("missing") else None)
    return {k: v for k, v in item.items() if v is not None}


def enrich(db: DB, answers: list[dict], scope: dict, log: list[dict], s: Settings) -> list[dict]:
    """What the service adds to the model's answers: warnings from verification, evidence regions resolved from
    the ids, the value every source gives (q.sources) and their agreement (q.facts)."""
    drawings, fids = scope["drawings"], {f["file_id"] for f in scope["files"]}
    for a in answers:
        warnings = verify(a, log)
        ids = [int(i) for i in a.get("entity_ids") or []]
        ev = store.evidence(db, ids[:s.max_evidence], drawings) if ids else dict(entities=[], missing=[])
        if ev["missing"]:
            warnings.append(f"entity ids not in this run: {ev['missing'][:10]}")
        items = []
        for e in ev["entities"]:
            label = " ".join(str(x) for x in (e["kind"], e.get("number") or e.get("label")) if x)
            for src in e["sources"]:
                first = src["refs"][0] if src["refs"] else {}
                items.append(_evidence_item(dict(first, entity_id=e["entity_id"], file_id=src["file_id"],
                                                 format=src.get("format"), page_no=src["page_no"], box=src.get("box"),
                                                 box_pct=src.get("box_pct"), image=src.get("image"),
                                                 box_px=_px(src)), label))
        refs = [r for r in a.get("refs") or [] if r["file_id"] in fids and r["ref_table"] in store.L1]
        if len(refs) < len(a.get("refs") or []):
            warnings.append("some references point outside this run or at an unknown table and were dropped")
        items += [_evidence_item(r) for r in store.resolve_refs(db, [dict(r) for r in refs[:s.max_evidence]])]
        known = [i for i in ids if i not in ev["missing"]]
        a["sources"], a["agreement"] = store.answer_sources(db, drawings, known)
        a["evidence"], a["evidence_total"] = items[:s.max_evidence], len(known) + len(refs)
        a["warnings"] = warnings
        if warnings:
            a["confidence"] = round(min(a["confidence"], 0.4), 2)
    return answers


def _px(src: dict) -> list[float] | None:
    p, w, h = src.get("box_pct"), src.get("image_w"), src.get("image_h")
    return [round(p[0] * w / 100, 1), round(p[1] * h / 100, 1), round(p[2] * w / 100, 1), round(p[3] * h / 100, 1)] \
        if p and w and h else None


# ---------------------------------------------------------------------------------------------- demo model
DEMO_SQL = ("select (select count(*) from q.files) as files, (select count(*) from q.rooms) as rooms, "
            "(select min(entity_id) from q.rooms) as first_room, "
            "(select file_id || ' ' || handle from q.cad_entities order by file_id, handle limit 1) as cad_ref")


def _texts(items: Any) -> str:
    out = []
    for it in items if isinstance(items, list) else [items]:
        c = it.get("content") if isinstance(it, dict) else it
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [p.get("text", "") for p in c if isinstance(p, dict)]
    return "\n".join(out)


def _demo_answers(prompt: str, result: str) -> str:
    row = next((json.loads(line) for line in result.splitlines() if line.startswith("[")), [0, 0, None, None])
    files, rooms, first_room, cad_ref = (row + [None] * 4)[:4]
    refs = [dict(file_id=cad_ref.split(" ", 1)[0], ref_table="cad_entity", ref_key=cad_ref.split(" ", 1)[1])] if cad_ref else []
    out = []
    for n, q in re.findall(r"^\s*(\d+)\.\s+(.*)$", prompt, re.M):
        out.append(dict(n=int(n), answer=rooms if rooms else files, unit="count",
                        method=f"Demo mode (no model was called): counted the {'rooms' if rooms else 'files'} of the run. "
                               f"Question was: {q.strip()[:100]}",
                        sql=DEMO_SQL, entity_ids=[first_room] if first_room else [], refs=refs,
                        check="none (demo mode)", confidence=0.5))
    return json.dumps(dict(answers=out))


class DemoModel(Model):
    """JSTORE_MODEL=demo: a scripted stand-in.  Every run goes through the real API, tools, store and event
    stream; only the model is fake: one sql call, then one answer per question built from its result."""

    def _step(self, args: tuple, kwargs: dict):
        from agents.testing import ScriptedModel, assistant_message, function_call
        items = kwargs.get("input", args[1] if len(args) > 1 else [])
        items = items if isinstance(items, list) else [items]
        outputs = [it for it in items if isinstance(it, dict) and it.get("type") == "function_call_output"]
        if not outputs:
            return ScriptedModel([[function_call("sql", dict(query=DEMO_SQL), call_id="demo_1")]])
        return ScriptedModel([[assistant_message(_demo_answers(_texts(items), str(outputs[-1].get("output", ""))))]])

    async def get_response(self, *args: Any, **kwargs: Any):
        return await self._step(args, kwargs).get_response(*args, **kwargs)

    def stream_response(self, *args: Any, **kwargs: Any):
        return self._step(args, kwargs).stream_response(*args, **kwargs)
