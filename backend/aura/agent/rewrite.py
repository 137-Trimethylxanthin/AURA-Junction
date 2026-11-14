"""POST /rewrite: rambling text or a voice transcript → question blocks the user confirms.

One gemini-3.1-flash-lite call (purpose 'rewrite': counted, per-IP limited, shown to the user
as a model call). The model never answers: it only proposes questions. Each block is then
checked against the drawing (scope must exist) and against the deterministic router, so the
user sees which blocks will be answered from geometry at 0 further calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from aura import tools
from aura.gemini.client import GeminiClient
from aura.router import contract_lang, route

PROMPT = (
    "Rewrite the user's message about a construction floor plan into separate, plain English "
    "questions that the drawing could answer: counts (windows, doors, washbasins, WCs, "
    "bathtubs, showers, rooms by label, apartments) or net floor areas (whole floor, an "
    "apartment, a room number, rooms with a label). Keep only what the user asked. Use the "
    "drawing's own apartment letters, room numbers and room labels: apartments [{units}]; "
    "room labels [{labels}]. Return JSON: {{\"detected_lang\": \"en|fr|fi|de|it|other\", "
    "\"questions\": [{{\"question\": str, \"intent\": \"count|area\", \"subject\": "
    "\"window|door|washbasin|wc|bathtub|shower|room|apartment|none\", \"scope\": "
    "\"floor|unit <letter>|room <number>|label <printed label>\"}}], \"not_answerable\": "
    "[parts of the message a floor plan cannot answer, in the user's words]}}. Do not answer "
    "the questions and do not write any number that is not in the user's message.\n\n"
    "Message:\n{text}"
)


@dataclass
class Rewrite:
    blocks: list[dict]
    not_answerable: list[str]
    detected_lang: str
    calls: int
    cost_usd: float
    tokens: int
    model: str


def _scope(raw: str, d: tools.LoadedDrawing) -> tuple[dict | None, bool]:
    """(scope, exists on the drawing)."""
    s = (raw or "floor").strip()
    k, _, v = s.partition(" ")
    k, v = k.lower(), v.strip().strip("'\"«»")
    ctx = d.ctx
    if k == "unit" and v:
        u = v[:1].upper()
        return {"kind": "unit", "unit": u}, u in ctx.units
    if k == "room" and v:
        return {"kind": "room", "room_id": v}, v in ctx.room_ids
    if k == "label" and v:
        hit = next((lab for lab in ctx.labels if lab.casefold() == v.casefold()), None)
        return {"kind": "label", "label": hit or v}, hit is not None
    return {"kind": "floor"}, True


def parse(text: str, d: tools.LoadedDrawing) -> tuple[list[dict], list[str], str]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        data = json.loads(m.group(0) if m else text)
    except (ValueError, AttributeError):
        return [], [], "en"
    blocks = []
    for q in (data.get("questions") or [])[:8]:
        if not isinstance(q, dict) or not str(q.get("question", "")).strip():
            continue
        question = str(q["question"]).strip()[:300]
        scope, exists = _scope(str(q.get("scope", "floor")), d)
        r = route(question, d.ctx)
        block = {"question": question, "intent": q.get("intent") if q.get("intent") in (
            "count", "area") else "other", "scope": scope, "scope_exists": exists,
                 "routable": r.intent in ("count", "area") and exists}
        subj = q.get("subject")
        if subj and subj != "none":
            block["subject"] = str(subj)
        blocks.append(block)
    na = [str(x)[:300] for x in (data.get("not_answerable") or []) if str(x).strip()][:8]
    lang = str(data.get("detected_lang", "en"))
    return blocks, na, lang


async def rewrite(client: GeminiClient, d: tools.LoadedDrawing, text: str, *,
                  ip_hash: str | None, presenter: bool) -> Rewrite:
    units = ", ".join(d.ctx.units)
    labels = ", ".join(sorted(set(d.ctx.labels)))[:600]
    prompt = PROMPT.format(units=units, labels=labels, text=text.strip()[:2000])
    g = await client.generate(prompt, purpose="rewrite", ip_hash=ip_hash, presenter=presenter,
                              config={"responseMimeType": "application/json"})
    blocks, na, lang = parse(g.text, d)
    return Rewrite(blocks=blocks, not_answerable=na, detected_lang=contract_lang(lang),
                   calls=1, cost_usd=g.cost_usd, tokens=g.input_tokens + g.output_tokens,
                   model=g.model)
