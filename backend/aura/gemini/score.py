"""Room and fixture counts on image-only plans: one Decisions `score` request per drawing.

Frozen from the held-out CubiCasa evaluation (.omc/research/gemini/gemini-usecases.md,
"CubiCasa evaluation (n=30, held-out)", partial result 3): the intro text, the question wording
and the 0…6 levels are the ones sent there. Rule: value = round(score), answered only when the
confidence is ≥ 0.6; otherwise abstain and show the probabilities. Doors and windows are NEVER
asked this way (2/15 and 3/15 exact in that evaluation); they use the box detector.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from aura.gemini.client import GeminiClient, Meter, image_part, text_part

MIN_CONFIDENCE = 0.6
INTRO = "Finnish apartment floor plan (CubiCasa5k).\n"
LEVELS = [str(i) for i in range(7)]
# Measured on the accepted answers (confidence >= 0.6), all families except doors and windows.
MEASURED = {"correct": 68, "n": 74, "source": "AURA held-out CubiCasa5k evaluation, 15 images, "
            "Decisions score answers at confidence >= 0.6; eval/results/cubicasa.json"}

# family key in the frozen request -> (contract subject, frozen instructions, question pattern)
FAMILIES: dict[str, tuple[str, str, re.Pattern[str]]] = {
    "bedrooms": ("bedroom", "How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?",
                 re.compile(r"bed ?rooms?|makuuhuone|schlafzimmer|chambres?|camer[ae] da letto", re.IGNORECASE)),
    "kitchens": ("kitchen", ("How many kitchens (including kitchenettes, open kitchens and utility "
                            "kitchens) are on this floor plan?"),
                 re.compile(r"kitchen|keitti|küche|kuche|cuisine|cucin", re.IGNORECASE)),
    "bathrooms": ("bathroom", ("How many bathrooms or washrooms (e.g. KH, KPH, PH, PESUH, SH) are on "
                              "this floor plan, not counting separate WC rooms?"),
                  re.compile(r"bath ?rooms?|washrooms?|kylpyhuone|pesuhuone|badezimmer|salles? de "
                             r"bains?|bagn", re.IGNORECASE)),
    "separate": ("wc_room", ("How many separate WC rooms (toilet rooms marked WC) are on this floor "
                            "plan?"),
                 re.compile(r"(?:separate )?(?:wc|toilet) rooms?|wc-tila|erillinen wc", re.IGNORECASE)),
    "saunas": ("sauna", "How many saunas are on this floor plan?",
               re.compile(r"saunas?", re.IGNORECASE)),
    "toilet": ("wc", "How many toilet bowls (WC seats) are drawn on this floor plan?",
               re.compile(r"toilets?(?! rooms?)|toilet bowls?|wc seats?|wcs?\b|lavator", re.IGNORECASE)),
    "sinks": ("sink", ("How many sinks and wash basins (kitchen and bathroom combined; a double-bowl "
                      "sink counts as one) are drawn on this floor plan?"),
              re.compile(r"sinks?|wash ?basins?|washbasins?|lavabos?|waschbecken|pesuallas", re.IGNORECASE)),
    "showers": ("shower_or_bathtub", ("How many showers and bathtubs are drawn on this floor plan in "
                                     "total?"),
                re.compile(r"showers?|bath ?tubs?|suihku|dusche|douche", re.IGNORECASE)),
    "floors": ("storey", "How many floors (storeys) does this floor plan show?",
               re.compile(r"floors|storeys?|stories|levels|kerros|etagen|étages", re.IGNORECASE)),
    "balconies": ("balcony", ("How many balconies (parveke, including glazed balconies) are on this "
                             "floor plan?"),
                  re.compile(r"balcon|balkon|parvek|loggia|terrace", re.IGNORECASE)),
}
COUNT_CUE = re.compile(r"how many|number of|count|wie viele|combien|kuinka monta|montako|quant[ie]",
                       re.IGNORECASE)


def family_of(question: str) -> str | None:
    """The score family a count question asks about (None: not a score question)."""
    if not COUNT_CUE.search(question):
        return None
    if re.search(r"\bdoors?\b|\bwindows?\b|ovi|ikkun|türen|fenster|portes?|fenêtres?", question, re.IGNORECASE):
        return None  # never through score
    hits = [k for k, (_s, _i, rx) in FAMILIES.items() if rx.search(question)]
    if "separate" in hits and "toilet" in hits:
        hits.remove("toilet")  # "toilet rooms" is the room family
    return hits[0] if len(hits) == 1 else None


def build_request(image: bytes) -> tuple[list[dict], dict]:
    questions = {f"{k}_score": {"type": "score", "instructions": instr, "criteria": LEVELS}
                 for k, (_s, instr, _rx) in FAMILIES.items()}
    return [text_part(INTRO), image_part(image, "high")], questions


async def run_score(client: GeminiClient, image_path: Path, *, ip_hash: str | None,
                    presenter: bool, meter: Meter | None = None) -> dict:
    """ONE request for every family; cacheable per drawing."""
    image = await asyncio.to_thread(image_path.read_bytes)
    parts, questions = build_request(image)
    res = await client.decisions(parts, questions, "raster_score", ip_hash=ip_hash,
                                 presenter=presenter, meter=meter)
    fams = {}
    for k in FAMILIES:
        a = res.answers.get(f"{k}_score") or {}
        if a.get("score") is None:
            continue
        fams[k] = {"score": float(a["score"]), "confidence": float(a.get("confidence") or 0),
                   "probabilities": {str(lv): round(float(p), 4)
                                     for lv, p in (a.get("probabilities") or {}).items()}}
    return {"families": fams, "model": res.model, "input_tokens": res.input_tokens,
            "cost_usd": res.cost_usd}
