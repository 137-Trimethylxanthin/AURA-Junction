"""The language of a question: the local word lists when they are clear, a model when they are not.

The router's markers are reliable on ordinary questions ("How many doors are there?", "combien de portes") but
misread short or informal ones from a single weak marker ("hallo kannst du deutsch" scored French on "du").
So a question is decided locally only when one language clearly leads; with no signal at all the interface
language is used; with a weak signal that disagrees with the interface language, one small structured call to
the agent model decides. Exact answers to clear questions keep 0 model calls. Model results are cached per
normalized question; the call goes through the budgeted client and is counted in the answer's calls and cost.
When models are off, over budget or the call fails, the interface language is used.
"""
from __future__ import annotations

import json
import unicodedata
from collections import OrderedDict
from typing import Any

from aura.budget import BudgetError
from aura.config import models_enabled
from aura.gemini.client import GeminiError
from aura.router import lang_scores, stem_scores

LANGS = ("en", "fr", "fi", "de", "it")
SCHEMA = {"type": "OBJECT", "properties": {"lang": {"type": "STRING", "enum": [*LANGS, "other"]}},
          "required": ["lang"]}
PROMPT = (
    "Which language is the user's message written in? Dialect, slang, missing umlauts and typos count as "
    "their language (Bavarian or Austrian dialect is German, Swiss German is German). If the message names a "
    "language it wants ('kannst du deutsch', 'in English please'), answer with the language it is written in "
    "unless it only consists of that request. Answer with one code: en, fr, fi, de, it, or other.\n\n"
    "Message: "
)
CACHE_MAX = 2000
_cache: OrderedDict[str, str] = OrderedDict()


def _key(question: str) -> str:
    return " ".join(unicodedata.normalize("NFC", question).casefold().split())


def local_verdict(question: str, fallback: str) -> str | None:
    """The language when the markers settle it (or there is nothing to detect); None when a model should decide."""
    sc = sorted(lang_scores(question).items(), key=lambda kv: -kv[1])
    (best, top), second = sc[0], (sc[1][1] if len(sc) > 1 else 0.0)
    if top >= 2 and top >= 2 * second:
        return best  # clearly one language
    stems = stem_scores(question)
    if top > second and stems[best] >= 1 and all(stems[best] >= v for v in stems.values()):
        return best  # a distinctive word of one language ('alle Fenster', 'kaikki ovet'), not just a shared 'du'
    if top == 0:
        return fallback  # no language content ("test", "22"): the interface language
    if dict(sc).get(fallback, 0.0) == top:
        return fallback  # a weak signal that agrees with (or ties with) the interface language
    return None


async def question_language(svc: Any, question: str, fallback: str, caller: Any, meter: Any = None) -> str:
    """Local when clear, the model when not (see the module docstring)."""
    v = local_verdict(question, fallback)
    return v if v is not None else await detect_language(svc, question, fallback, caller, meter)


async def detect_language(svc: Any, question: str, fallback: str, caller: Any, meter: Any = None) -> str:
    """'en' | 'fr' | 'fi' | 'de' | 'it' for `question`; `fallback` (the interface language) when no model answers."""
    key = _key(question)
    if not key:
        return fallback
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    client = getattr(svc, "client", None)
    if client is None or not models_enabled() or not client.available():
        return fallback
    try:
        g = await client.generate(PROMPT + question.strip()[:500], purpose="lang", model=svc.settings.agent_model,
                                  ip_hash=getattr(caller, "ip_hash", None),
                                  presenter=bool(getattr(caller, "presenter", False)), meter=meter,
                                  config={"responseMimeType": "application/json", "responseSchema": SCHEMA})
        lang = str(json.loads(g.text or "{}").get("lang", ""))
    except (BudgetError, GeminiError, ValueError, TypeError):
        return fallback
    if lang not in LANGS:  # 'other': answer in the interface language
        lang = fallback if fallback in LANGS else "en"
    _cache[key] = lang
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return lang
