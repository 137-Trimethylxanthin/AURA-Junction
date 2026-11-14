"""Router-miss understanding with one Decisions request (E3/E4 in gemini-usecases.md).

`choice` questions pick intent, count subject and scope among options built from the drawing;
a `noul` gate asks whether the drawing can answer at all. `choice` cannot emit numbers, so this
step can never put a number into an answer. Cost ≈ $0.00005 per question (E3).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from aura.gemini.client import GeminiClient, Meter, image_part, text_part

MIN_CONFIDENCE = 0.6  # E2/E3: accepted answers ≥ 0.6 were right 22/23
GATE_OFF = 0.4  # E4: off-drawing facts ≤ 0.19, on-drawing facts ≥ 0.63

INTENTS = {
    "count": "Asks how many items there are (a number of objects, rooms or apartments).",
    "area": "Asks for a surface or floor area, or how big a room or apartment is.",
    "length": "Asks for a length, width, height, distance or volume.",
    "text": "Asks for a name, type, material, rating, list or other non-numeric fact.",
    "other": "Not a question about a building or its drawing.",
}
SUBJECTS = {
    "window": "windows",
    "door": "doors",
    "washbasin": "washbasins or sinks",
    "wc": "toilets or WC seats",
    "bathtub": "bathtubs",
    "shower": "showers",
    "room": "rooms of some kind (bedrooms, bathrooms, kitchens...)",
    "apartment": "apartments or dwelling units",
    "other": "none of these, or not a count",
}


@dataclass
class Understood:
    intent: str | None
    intent_conf: float
    subject: str | None
    subject_conf: float
    scope: dict | None
    scope_conf: float
    on_drawing: float | None
    calls: int = 1
    cost_usd: float = 0.0
    input_tokens: int = 0
    latency_ms: int = 0
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def confident(self) -> bool:
        return self.intent is not None and self.intent_conf >= MIN_CONFIDENCE

    @property
    def off_drawing(self) -> bool:
        return self.on_drawing is not None and self.on_drawing < GATE_OFF


def scope_options(units: list[str], labels: list[str]) -> tuple[dict[str, str], dict[str, dict]]:
    crit: dict[str, str] = {"floor": "the whole floor plan, or no particular part of it"}
    to_scope: dict[str, dict] = {"floor": {"kind": "floor"}}
    for u in units:
        crit[f"unit_{u}"] = f"apartment {u}"
        to_scope[f"unit_{u}"] = {"kind": "unit", "unit": u}
    for i, lab in enumerate(dict.fromkeys(labels)):
        key = f"label_{i:02d}"
        crit[key] = f"the rooms labelled '{lab}'"
        to_scope[key] = {"kind": "label", "label": lab}
    return crit, to_scope


def build_request(question: str, units: list[str], labels: list[str],
                  image: bytes | None) -> tuple[list[dict], dict, dict[str, dict]]:
    parts = [text_part(f"A user asked this question about the construction floor plan below:\n"
                       f"{question.strip()}\n")]
    if image is not None:
        parts.append(image_part(image, "low"))
    crit, to_scope = scope_options(units, labels)
    questions = {
        "intent": {"type": "choice", "instructions": "What does the question ask for?",
                   "criteria": INTENTS},
        "subject": {"type": "choice",
                    "instructions": "If the question asks for a count, what is being counted?",
                    "criteria": SUBJECTS},
        "scope": {"type": "choice",
                  "instructions": "Which part of the floor plan is the question about?",
                  "criteria": crit},
        "on_drawing": {"type": "noul",
                       "instructions": "Is the information needed to answer the question shown "
                                       "on this drawing?"},
    }
    return parts, questions, to_scope


async def understand(client: GeminiClient, question: str, *, units: list[str],
                     labels: list[str], image_path: Path | None, ip_hash: str | None,
                     presenter: bool, meter: Meter | None = None) -> Understood:
    image = (await asyncio.to_thread(image_path.read_bytes)
             if image_path is not None and image_path.is_file() else None)
    parts, questions, to_scope = build_request(question, units, labels, image)
    res = await client.decisions(parts, questions, "choice", ip_hash=ip_hash, presenter=presenter,
                                 meter=meter)
    intent, ic, ip = res.choice("intent")
    subject, sc, sp = res.choice("subject")
    scope_key, scc, scp = res.choice("scope")
    return Understood(
        intent=intent, intent_conf=ic,
        subject=None if subject in (None, "other") else subject, subject_conf=sc,
        scope=to_scope.get(scope_key or ""), scope_conf=scc,
        on_drawing=res.noul("on_drawing"),
        cost_usd=res.cost_usd, input_tokens=res.input_tokens, latency_ms=res.latency_ms,
        probabilities={"intent": ip, "subject": sp, "scope": scp},
    )
