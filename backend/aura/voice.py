"""Voice: dictation (speech to text), answer readout (text to speech) and live-session tokens.

    POST /voice/transcribe?lang=en   raw audio body (Content-Type audio/webm, audio/ogg, ...)
                                     -> {"text", "model", "lang"}
    POST /voice/speak                {"answer_id"?, "response"?, "lang", "voice"?}
                                     -> {"script", "audio_b64", "mime_type", "cached", "source", ...}
    POST /voice/token                -> {"token", "model", "expires_at", "new_session_expires_at"}

Every Gemini request goes through `GeminiClient._send`, so the budget reserves, counts and caps
it like any other model call. The spoken script is built here, only from fields of a stored or
returned AskResponse (headline value, group breakdown, method, caveats); no model writes it.
The live session itself runs browser-to-Gemini: the token request is counted, the audio of the
session is billed by Google outside this ledger.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from aura.budget import BudgetError
from aura.contract import CONTRACT_VERSION
from aura.gemini.client import GeminiError
from aura.netutil import client_ip_hash, is_presenter

STT_MODEL = "gemini-3.5-transcribe"
TTS_MODEL = "gemini-3.8-flash-lite-tts"
LIVE_MODEL = "gemini-3.8-live"

VOICES = ("Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr")
DEFAULT_VOICE = "Kore"

Lang = Literal["en", "fr", "fi", "de"]
BCP47 = {"en": "en-US", "fr": "fr-FR", "fi": "fi-FI", "de": "de-DE"}

AUDIO_TYPES = {
    "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/mp3", "audio/wav",
    "audio/x-wav", "audio/aac", "audio/flac", "audio/m4a", "audio/x-m4a",
}
MAX_AUDIO_BYTES = 6 * 1024 * 1024  # ~ several minutes of opus; dictation clips are seconds
MIN_AUDIO_BYTES = 256  # a container header with no sound: nothing to transcribe
VOCABULARY = ["AURA", "IFC", "DXF", "CAD", "WC", "m²", "Apartment", "OG", "EG"]

VOICE_PER_IP_HOUR = 120  # public callers; the budget's daily/event caps still apply on top
LIVE_PER_IP_HOUR = 6  # live sessions bill audio outside the ledger: keep them few
CACHE_MAX = 64

router = APIRouter(prefix="/voice", tags=["voice"])

_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_ip_calls: dict[tuple[str, str], deque[float]] = defaultdict(deque)


# ---- shared -----------------------------------------------------------------------------


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"contract_version": CONTRACT_VERSION, "error": message, "code": code},
                        status_code=status)


class _Caller:
    def __init__(self, request: Request):
        svc = getattr(request.app.state, "svc", None)
        self.svc = svc
        self.client = getattr(svc, "client", None)
        settings = getattr(svc, "settings", None)
        self.ip_hash = client_ip_hash(request, settings) if settings is not None else None
        self.presenter = is_presenter(request, settings) if settings is not None else False

    def refused(self, kind: str = "voice", per_hour: int = VOICE_PER_IP_HOUR) -> JSONResponse | None:
        if self.client is None or not self.client.available():
            return _err("internal", "Voice needs a model key on the server.", 503)
        if not self.presenter and self.ip_hash:
            q = _ip_calls[(kind, self.ip_hash)]
            now = time.monotonic()
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= per_hour:
                return _err("rate_limited", "Too many voice requests from this address.", 429)
            q.append(now)
        return None

    async def send(self, path: str, body: dict, *, model: str, purpose: str) -> dict:
        data, _tin, _tout, _ms = await self.client._send(  # the budgeted path
            path, body, model=model, purpose=purpose, bucket="public",
            ip_hash=self.ip_hash, presenter=self.presenter,
        )
        return data


def _model_error(e: Exception) -> JSONResponse:
    if isinstance(e, BudgetError):
        code = "rate_limited" if e.code == "rate_limited" else "budget_exhausted"
        return _err(code, str(e), 429)
    return _err("internal", "The voice model did not answer.", 502)


def _outputs(data: dict, kind: str) -> list[dict]:
    return [c for s in data.get("steps") or [] if s.get("type") == "model_output"
            for c in s.get("content") or [] if c.get("type") == kind]


# ---- 1. speech to text ------------------------------------------------------------------


@router.post("/transcribe")
async def transcribe(request: Request, lang: Lang = "en"):
    mime = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if mime not in AUDIO_TYPES:
        return _err("unsupported_type", "Send the recording as an audio/* body (webm, ogg, mp4, wav).", 415)
    audio = await request.body()
    if len(audio) > MAX_AUDIO_BYTES:
        return _err("too_large", "The recording is too long.", 413)
    if len(audio) < MIN_AUDIO_BYTES:
        return {"text": "", "model": STT_MODEL, "lang": lang}
    caller = _Caller(request)
    if (r := caller.refused()) is not None:
        return r
    body = {
        "model": STT_MODEL,
        "input": [{"type": "audio", "data": base64.b64encode(audio).decode(), "mime_type": mime}],
        "generation_config": {"transcription_config": {
            "mode": "smart", "language_codes": [BCP47[lang]], "custom_vocabulary": VOCABULARY,
        }},
    }
    try:
        data = await caller.send("/interactions", body, model=STT_MODEL, purpose="voice_stt")
    except (BudgetError, GeminiError) as e:
        return _model_error(e)
    raw = "\n".join(t["text"] for t in _outputs(data, "text") if t.get("text"))
    # Smart mode may format lists; the composer takes one line of plain text.
    lines = (ln.strip().lstrip("-*•").strip() for ln in raw.splitlines())
    text = " ".join(ln for ln in lines if ln)
    return {"text": text, "model": STT_MODEL, "lang": lang}


# ---- 2. text to speech ------------------------------------------------------------------


class SpeakRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answer_id: str | None = Field(default=None, max_length=64)
    response: dict[str, Any] | None = None  # the AskResponse the browser shows
    lang: Lang = "en"
    voice: str = DEFAULT_VOICE


SUBJECTS: dict[str, dict[str, tuple[str, str]]] = {
    # subject: lang -> (one, many). Finnish "many" is the partitive used after a number.
    "window": {"en": ("window", "windows"), "fr": ("fenêtre", "fenêtres"),
               "fi": ("ikkuna", "ikkunaa"), "de": ("Fenster", "Fenster")},
    "door": {"en": ("door", "doors"), "fr": ("porte", "portes"),
             "fi": ("ovi", "ovea"), "de": ("Tür", "Türen")},
    "washbasin": {"en": ("washbasin", "washbasins"), "fr": ("lavabo", "lavabos"),
                  "fi": ("pesuallas", "pesuallasta"), "de": ("Lavabo", "Lavabos")},
    "wc": {"en": ("toilet", "toilets"), "fr": ("WC", "WC"), "fi": ("WC", "WC:tä"),
           "de": ("WC", "WCs")},
    "bathtub": {"en": ("bathtub", "bathtubs"), "fr": ("baignoire", "baignoires"),
                "fi": ("kylpyamme", "kylpyammetta"), "de": ("Badewanne", "Badewannen")},
    "shower": {"en": ("shower", "showers"), "fr": ("douche", "douches"),
               "fi": ("suihku", "suihkua"), "de": ("Dusche", "Duschen")},
    "room": {"en": ("room", "rooms"), "fr": ("pièce", "pièces"),
             "fi": ("huone", "huonetta"), "de": ("Raum", "Räume")},
    "apartment": {"en": ("apartment", "apartments"), "fr": ("logement", "logements"),
                  "fi": ("asunto", "asuntoa"), "de": ("Wohnung", "Wohnungen")},
    "bedroom": {"en": ("bedroom", "bedrooms"), "fr": ("chambre", "chambres"),
                "fi": ("makuuhuone", "makuuhuonetta"), "de": ("Schlafzimmer", "Schlafzimmer")},
    "kitchen": {"en": ("kitchen", "kitchens"), "fr": ("cuisine", "cuisines"),
                "fi": ("keittiö", "keittiötä"), "de": ("Küche", "Küchen")},
    "bathroom": {"en": ("bathroom", "bathrooms"), "fr": ("salle de bain", "salles de bain"),
                 "fi": ("kylpyhuone", "kylpyhuonetta"), "de": ("Badezimmer", "Badezimmer")},
    "wc_room": {"en": ("toilet room", "toilet rooms"), "fr": ("cabinet de toilette", "cabinets de toilette"),
                "fi": ("WC-tila", "WC-tilaa"), "de": ("WC-Raum", "WC-Räume")},
    "sauna": {"en": ("sauna", "saunas"), "fr": ("sauna", "saunas"),
              "fi": ("sauna", "saunaa"), "de": ("Sauna", "Saunen")},
    "sink": {"en": ("sink", "sinks"), "fr": ("évier", "éviers"),
             "fi": ("tiskiallas", "tiskiallasta"), "de": ("Spüle", "Spülen")},
    "shower_or_bathtub": {"en": ("shower or bathtub", "showers or bathtubs"),
                          "fr": ("douche ou baignoire", "douches ou baignoires"),
                          "fi": ("suihku tai amme", "suihkua tai ammetta"),
                          "de": ("Dusche oder Badewanne", "Duschen oder Badewannen")},
    "storey": {"en": ("storey", "storeys"), "fr": ("niveau", "niveaux"),
               "fi": ("kerros", "kerrosta"), "de": ("Geschoss", "Geschosse")},
    "balcony": {"en": ("balcony", "balconies"), "fr": ("balcon", "balcons"),
                "fi": ("parveke", "parveketta"), "de": ("Balkon", "Balkone")},
}

UNITS = {
    "m": {"en": "metres", "fr": "mètres", "fi": "metriä", "de": "Meter"},
    "m2": {"en": "square metres", "fr": "mètres carrés", "fi": "neliömetriä", "de": "Quadratmeter"},
    "m3": {"en": "cubic metres", "fr": "mètres cubes", "fi": "kuutiometriä", "de": "Kubikmeter"},
}

PHRASES: dict[str, dict[str, str]] = {
    "count": {"en": "{n} {subject}.", "fr": "{n} {subject}.", "fi": "{n} {subject}.",
              "de": "{n} {subject}."},
    "area": {"en": "Net area: {v} {unit}.", "fr": "Surface nette : {v} {unit}.",
             "fi": "Nettoala: {v} {unit}.", "de": "Nettofläche: {v} {unit}."},
    "quantity": {"en": "{v} {unit}.", "fr": "{v} {unit}.", "fi": "{v} {unit}.", "de": "{v} {unit}."},
    "label": {"en": "The drawing reads: {text}.", "fr": "Le plan indique : {text}.",
              "fi": "Piirustuksessa lukee: {text}.", "de": "Auf dem Plan steht: {text}."},
    "fire_rating": {"en": "Fire rating: {text}.", "fr": "Résistance au feu : {text}.",
                    "fi": "Palonkestävyys: {text}.", "de": "Feuerwiderstand: {text}."},
    "rooms_1": {"en": "In 1 room.", "fr": "Dans 1 pièce.", "fi": "1 huoneesta.", "de": "In 1 Raum."},
    "no_area_1": {"en": "1 room has no printed area.", "fr": "1 pièce n’a pas de surface indiquée.",
                  "fi": "1 huoneelta puuttuu merkitty ala.", "de": "Bei 1 Raum ist keine Fläche angegeben."},
    "rooms_n": {"en": "Across {n} rooms.", "fr": "Sur {n} pièces.", "fi": "{n} huoneesta.",
                "de": "Über {n} Räume."},
    "check": {"en": "An independent check agreed on {a} of {t}.",
              "fr": "Une vérification indépendante en confirme {a} sur {t}.",
              "fi": "Riippumaton tarkistus vahvisti {a}/{t}.",
              "de": "Eine unabhängige Prüfung bestätigt {a} von {t}."},
    "confidence": {"en": "Confidence {p} percent.", "fr": "Confiance : {p} pour cent.",
                   "fi": "Varmuus {p} prosenttia.", "de": "Sicherheit {p} Prozent."},
    "no_area": {"en": "{n} rooms have no printed area.", "fr": "{n} pièces n’ont pas de surface indiquée.",
                "fi": "{n} huoneelta puuttuu merkitty ala.", "de": "Bei {n} Räumen ist keine Fläche angegeben."},
    "where": {"en": "Where to look: {text}.", "fr": "Où chercher : {text}.",
              "fi": "Mistä katsoa: {text}.", "de": "Wo nachsehen: {text}."},
}

METHOD = {
    "cad_geometry": {"en": "Exact, from the CAD geometry.", "fr": "Exact, d’après la géométrie CAO.",
                     "fi": "Tarkka, CAD-geometriasta.", "de": "Exakt, aus der CAD-Geometrie."},
    "ocr": {"en": "Read from the printed text on the drawing.",
            "fr": "Lu dans le texte imprimé du plan.",
            "fi": "Luettu piirustuksen tekstistä.", "de": "Aus dem Text auf dem Plan gelesen."},
    "detector": {"en": "Detected on the drawing image.", "fr": "Détecté sur l’image du plan.",
                 "fi": "Tunnistettu piirustuksen kuvasta.", "de": "Auf dem Planbild erkannt."},
    "vision": {"en": "A visual reading of the drawing.", "fr": "Une lecture visuelle du plan.",
               "fi": "Visuaalinen tulkinta piirustuksesta.", "de": "Eine visuelle Lesung des Plans."},
    "agent_estimate": {"en": "An agent visual estimate, not an exact measurement.",
                       "fr": "Une estimation visuelle de l’agent, pas une mesure exacte.",
                       "fi": "Agentin visuaalinen arvio, ei tarkka mittaus.",
                       "de": "Eine visuelle Schätzung des Agenten, keine exakte Messung."},
}

ABSTAIN = {
    "not_on_drawing": {"en": "That is not shown on this drawing.",
                       "fr": "Cela n’apparaît pas sur ce plan.",
                       "fi": "Sitä ei näy tässä piirustuksessa.",
                       "de": "Das ist auf diesem Plan nicht zu sehen."},
    "not_supported_yet": {"en": "AURA cannot answer that kind of question yet.",
                          "fr": "AURA ne sait pas encore répondre à ce type de question.",
                          "fi": "AURA ei vielä osaa vastata tällaiseen kysymykseen.",
                          "de": "Diese Art Frage kann AURA noch nicht beantworten."},
    "unclear_question": {"en": "The question was not clear enough to answer.",
                         "fr": "La question n’était pas assez claire.",
                         "fi": "Kysymys ei ollut riittävän selvä.",
                         "de": "Die Frage war nicht klar genug."},
    "low_confidence": {"en": "No answer: the estimate was not confident enough.",
                       "fr": "Pas de réponse : l’estimation n’était pas assez sûre.",
                       "fi": "Ei vastausta: arvio ei ollut riittävän varma.",
                       "de": "Keine Antwort: Die Schätzung war nicht sicher genug."},
}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _fmt(v: float, lang: str) -> str:
    s = f"{round(v, 2):.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",") if lang != "en" else s


def _clip(s: Any, n: int = 300) -> str | None:
    if not isinstance(s, str) or not s.strip():
        return None
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def spoken_facts(resp: dict[str, Any]) -> dict[str, Any]:
    """The subset of an AskResponse that the script may say. Also the cache identity."""
    ans = resp.get("answer") if isinstance(resp.get("answer"), dict) else {}
    ev = resp.get("evidence") if isinstance(resp.get("evidence"), dict) else {}
    facts: dict[str, Any] = {"kind": ans.get("kind"), "method": resp.get("method")}
    kind = ans.get("kind")
    if kind == "count":
        facts.update(value=_num(ans.get("value")), subject=ans.get("subject"))
    elif kind in ("area", "quantity"):
        facts.update(value=_num(ans.get("value")), unit=ans.get("unit", "m2"))
    elif kind == "text":
        facts.update(text=_clip(ans.get("value"), 160), subject=ans.get("subject"))
    elif kind == "note":
        facts.update(text=_clip(ans.get("value")), value=_num(ans.get("number")),
                     unit=_clip(ans.get("unit"), 24))
    elif kind == "abstain":
        facts.update(reason=ans.get("reason"), where=_clip(ans.get("where_to_find"), 160))
    # Top breakdown: evidence groups, sized by the boxes (count) or included room areas (area).
    groups = [g for g in ev.get("groups") or [] if isinstance(g, dict) and g.get("id")]
    if groups and kind in ("count", "area"):
        tally: dict[str, float] = defaultdict(float)
        if kind == "count":
            for b in ev.get("boxes") or []:
                if isinstance(b, dict) and b.get("group_id") and not b.get("duplicate_of"):
                    tally[b["group_id"]] += 1
        else:
            for r in ev.get("rooms") or []:
                if isinstance(r, dict) and r.get("included") and r.get("group_id"):
                    tally[r["group_id"]] += _num(r.get("area_m2")) or 0.0
        rows = [(_clip(g.get("label"), 60), tally[g["id"]]) for g in groups if tally.get(g["id"])]
        rows.sort(key=lambda x: -x[1])
        head = _num(ans.get("value"))
        # Only a breakdown that adds up to the headline is said: the tallies are not solver output.
        if head is not None and rows and abs(sum(v for _, v in rows) - head) < 0.05:
            facts["breakdown"] = [[label, v] for label, v in rows[:3] if label]
    if kind == "area":
        rooms = [r for r in ev.get("rooms") or [] if isinstance(r, dict) and r.get("included")]
        facts["rooms"] = len(rooms)
        facts["no_area"] = sum(1 for r in rooms if _num(r.get("area_m2")) is None)
    chk = resp.get("check") if isinstance(resp.get("check"), dict) else None
    if chk and chk.get("status") == "live" and _num(chk.get("total")):
        facts["check"] = [_num(chk.get("agreed")) or 0.0, _num(chk.get("total"))]
    est = resp.get("estimate") if isinstance(resp.get("estimate"), dict) else None
    if est and _num(est.get("confidence")) is not None:
        pct = float(est["confidence"]) * 100  # as the answer card shows it
        facts["confidence"] = (math.floor(pct + 0.5) if resp.get("method") == "agent_estimate"
                               else math.floor(pct))
    return facts


def build_script(facts: dict[str, Any], lang: str) -> str:
    """Spoken text in `lang` from `spoken_facts` only. Abstentions carry no number."""
    out: list[str] = []
    kind, v = facts.get("kind"), facts.get("value")
    if kind == "count" and v is not None:
        one, many = SUBJECTS.get(facts.get("subject") or "", {}).get(lang, ("", ""))
        noun = one if v == 1 else many
        out.append(PHRASES["count"][lang].format(n=_fmt(v, lang), subject=noun).replace(" .", "."))
    elif kind == "area" and v is not None:
        out.append(PHRASES["area"][lang].format(v=_fmt(v, lang), unit=UNITS["m2"][lang]))
    elif kind == "quantity" and v is not None:
        unit = UNITS.get(facts.get("unit") or "", {}).get(lang, facts.get("unit") or "")
        out.append(PHRASES["quantity"][lang].format(v=_fmt(v, lang), unit=unit))
    elif kind == "text" and facts.get("text"):
        key = "fire_rating" if facts.get("subject") == "fire_rating" else "label"
        out.append(PHRASES[key][lang].format(text=facts["text"]))
    elif kind == "note":
        if v is not None:
            unit = UNITS.get(facts.get("unit") or "", {}).get(lang, facts.get("unit") or "")
            out.append(PHRASES["quantity"][lang].format(v=_fmt(v, lang), unit=unit).replace(" .", "."))
        if facts.get("text"):
            out.append(facts["text"])
    elif kind == "abstain":
        out.append(ABSTAIN.get(facts.get("reason") or "", ABSTAIN["unclear_question"])[lang])
        if facts.get("where"):
            out.append(PHRASES["where"][lang].format(text=facts["where"]))
    if facts.get("breakdown"):
        sep = "; " if lang == "en" else " ; " if lang == "fr" else "; "
        parts = [f"{label}: {_fmt(n, lang)}" for label, n in facts["breakdown"]]
        out.append(sep.join(parts) + ".")
    if kind == "area" and facts.get("rooms"):
        key = "rooms_1" if facts["rooms"] == 1 else "rooms_n"
        out.append(PHRASES[key][lang].format(n=facts["rooms"]))
    if kind != "abstain" and facts.get("method") in METHOD:
        out.append(METHOD[facts["method"]][lang])
    if facts.get("confidence") is not None and kind != "abstain":
        out.append(PHRASES["confidence"][lang].format(p=facts["confidence"]))
    if facts.get("check"):
        a, t = facts["check"]
        out.append(PHRASES["check"][lang].format(a=_fmt(a, lang), t=_fmt(t, lang)))
    if facts.get("no_area"):
        key = "no_area_1" if facts["no_area"] == 1 else "no_area"
        out.append(PHRASES[key][lang].format(n=facts["no_area"]))
    return " ".join(out).strip()


MAX_SCRIPT_CHARS = 600
CLIENT_KINDS = {"count", "area", "quantity"}
CLIENT_UNITS = {"m", "m2", "m3"}


def client_facts(resp: dict[str, Any]) -> dict[str, Any] | None:
    """Facts from an answer the browser sent (no stored copy): structured kinds only, numbers and
    enums only. No free text (notes, labels, group names, where_to_find, unit strings) is kept,
    so the endpoint cannot be used to make the voice say arbitrary words."""
    facts = spoken_facts(resp)
    kind = facts.get("kind")
    if kind not in CLIENT_KINDS or facts.get("value") is None:
        return None
    if kind == "count" and facts.get("subject") not in SUBJECTS:
        return None
    if kind == "quantity" and facts.get("unit") not in CLIENT_UNITS:
        return None
    out = {k: facts[k] for k in ("kind", "value", "subject", "unit", "rooms", "no_area",
                                 "check", "confidence") if facts.get(k) is not None}
    if kind == "area":
        out["unit"] = "m2"
    if facts.get("method") in METHOD:
        out["method"] = facts["method"]
    return out


def _cap(script: str) -> str:
    if len(script) <= MAX_SCRIPT_CHARS:
        return script
    cut = script[:MAX_SCRIPT_CHARS]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[: end + 1] if end > 0 else cut.rsplit(" ", 1)[0] + "…"


@router.post("/speak")
async def speak(body: SpeakRequest, request: Request):
    if body.voice not in VOICES:
        return _err("bad_request", f"Unknown voice; use one of {', '.join(VOICES)}.", 400)
    caller = _Caller(request)
    resp, source = None, "client"
    store = getattr(caller.svc, "store", None)
    if body.answer_id and store is not None:
        try:
            resp = store.answer(body.answer_id)
        except Exception:  # noqa: BLE001 - a store miss falls back to the shown answer
            resp = None
        if resp is not None:
            source = "store"
    if resp is not None:
        facts = spoken_facts(resp) if isinstance(resp.get("answer"), dict) else None
    elif isinstance(body.response, dict) and isinstance(body.response.get("answer"), dict):
        facts = client_facts(body.response)  # numbers and enums only
    else:
        return _err("bad_request", "Send an answer_id or the answer response to read out.", 400)
    if facts is None:
        return _err("bad_request", "Only stored answers, or count, area and quantity answers, "
                                   "can be read out.", 400)
    script = _cap(build_script(facts, body.lang))
    if not script:
        return _err("bad_request", "This answer has nothing to read out.", 400)
    key = hashlib.sha256(json.dumps([facts, body.lang, body.voice, TTS_MODEL], sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()[:32]
    hit = _cache.get(key)
    if hit is not None:  # a replay costs nothing and reserves nothing
        _cache.move_to_end(key)
        return {**hit, "cached": True}
    if (r := caller.refused()) is not None:
        return r
    req = {
        "model": TTS_MODEL,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": script,
            "annotations": [{"type": "speech_metadata", "style": "calm, clear architectural briefing"}],
        }]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": body.voice}]},
    }
    try:
        data = await caller.send("/interactions", req, model=TTS_MODEL, purpose="voice_tts")
    except (BudgetError, GeminiError) as e:
        return _model_error(e)
    audio = _outputs(data, "audio")
    if not audio or not audio[-1].get("data"):
        return _err("internal", "The voice model returned no audio.", 502)
    out = {"script": script, "audio_b64": audio[-1]["data"],
           "mime_type": audio[-1].get("mime_type") or "audio/wav", "voice": body.voice,
           "lang": body.lang, "model": TTS_MODEL, "source": source, "key": key}
    _cache[key] = out
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return {**out, "cached": False}


# ---- 3. live voice co-pilot token -------------------------------------------------------

LIVE_WS = ("wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta."
           "GenerativeService.BidiGenerateContentConstrained")
LANG_NAME = {"en": "English", "fr": "French", "fi": "Finnish", "de": "German"}

LIVE_SYSTEM = (
    "You are AURA's voice co-pilot for construction drawings. For ANY question about the "
    "drawing, its rooms, areas, counts, labels or floors, call ask_blueprint with the user's "
    "question and wait for its result. Never say a number, area, count or label that did not "
    "come from an ask_blueprint result in this session; if the result has no answer, say so. "
    "Say the method from the result (for example exact from CAD geometry, or an agent visual "
    "estimate) and any caveat. Keep replies to one or two short sentences. Speak {lang} "
    "unless the user speaks another language."
)

ASK_BLUEPRINT = {
    "name": "ask_blueprint",
    "description": "Ask AURA's verified drawing pipeline a question about the open drawing. "
                   "Returns the answer with its value, unit, method and caveats.",
    "behavior": "NON_BLOCKING",
    "parameters": {"type": "OBJECT", "properties": {"question": {
        "type": "STRING", "description": "The user's question, in their words."}},
        "required": ["question"]},
}


@router.post("/token")
async def live_token(request: Request, lang: Lang = "en"):
    """A single-use token locked to the co-pilot setup (model, prompt, tool); the key stays here."""
    caller = _Caller(request)
    if (r := caller.refused("live", LIVE_PER_IP_HOUR)) is not None:
        return r
    now = dt.datetime.now(dt.UTC)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires, new_session = now + dt.timedelta(minutes=15), now + dt.timedelta(minutes=1)
    body = {
        "uses": 1,
        "expireTime": iso(expires),
        "newSessionExpireTime": iso(new_session),
        "bidiGenerateContentSetup": {
            "model": f"models/{LIVE_MODEL}",
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": DEFAULT_VOICE}}}},
            "systemInstruction": {"parts": [{"text": LIVE_SYSTEM.format(lang=LANG_NAME[lang])}]},
            "tools": [{"functionDeclarations": [ASK_BLUEPRINT]}],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        },
    }
    try:
        data = await caller.send("/auth_tokens", body, model=LIVE_MODEL, purpose="voice_live")
    except (BudgetError, GeminiError) as e:
        return _model_error(e)
    if not isinstance(data.get("name"), str):
        return _err("internal", "The token service returned no token.", 502)
    return {"token": data["name"], "model": LIVE_MODEL, "ws_url": LIVE_WS, "lang": lang,
            "expires_at": iso(expires), "new_session_expires_at": iso(new_session)}
