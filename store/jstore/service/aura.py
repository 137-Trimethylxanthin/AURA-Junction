"""AURA compatibility layer: the HTTP contract of the AURA frontend (frontend/src/api/contract.ts, version 1.1),
served from the store under the prefix /aura.

    VITE_API_URL=http://localhost:8000/aura/t/<JSTORE_API_TOKEN>   (the frontend sends no credentials of its own)

A drawing here is a drawing key of the store; its id in URLs is the key when that is URL-safe, else `k.<base64url>`.
A drawing with an IFC model is shown one storey at a time: `<id>~<storey>` (the bare id opens the lowest storey with rooms).
Drawings, rooms, boxes and checks are generated on the fly from the q views (aura_model.py).  /ask and /chat start
an ordinary run of the store's agent and translate its answer and evidence into the contract's AskResponse.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.types import Scope

from ..db import DB
from . import aura_model as am
from .aura_model import CONTRACT_VERSION, Model
from .config import Settings
from .files import extension, new_id, store_upload
from .runs import TERMINAL, Registry, Run

router = APIRouter(prefix="/aura")
PREFIX = "/aura"
_PATH_TOKEN = re.compile(r"^/aura/t/([^/]+)(/.*)?$")
NO_STORE = "private, no-store"
SAFE = {"X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"}
LANGS = ("en", "fr", "fi", "de")
LANG_NAME = {"en": "English", "fr": "French", "fi": "Finnish", "de": "German"}
JSON_FILES = ("drawing.json", "model.json", "evidence.json", "checks.json")
IMAGE_FILES = ("plan.svg", "original.png", "thumb.png")
KEEPALIVE_S = 15            # the frontend gives up after 75 s without a byte
MAX_CONVERSATIONS = 500


def path_token(scope: Scope) -> str | None:
    """`/aura/t/<token>/...` -> the request becomes `/aura/...`; returns the token.  The frontend builds every URL
    (fetch, <img>, XHR) as VITE_API_URL + path and sends no Authorization header, so the token rides in the base URL.
    The scope is rewritten in place: the server's access log reads the same scope, so it shows no token."""
    if "aura.token" in scope:
        return scope["aura.token"]
    m = _PATH_TOKEN.match(scope.get("path", ""))
    if not m:
        return None
    scope["path"] = PREFIX + (m[2] or "/")
    scope["raw_path"] = scope["path"].encode()
    scope["aura.token"] = m[1]
    return m[1]


class PathToken:
    """Outermost middleware: takes the token out of the path before anything else (CORS preflights included)
    sees or logs the request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive, send) -> None:
        if scope["type"] == "http":
            path_token(scope)
        await self.app(scope, receive, send)


def err(code: str, message: str, status: int, **extra: Any) -> JSONResponse:
    return JSONResponse(dict(contract_version=CONTRACT_VERSION, error=message, code=code, **extra), status,
                        headers={"Cache-Control": NO_STORE})


# ---------------------------------------------------------------------------------------------- state
class Aura:
    """What the adapter keeps between requests: drawing models, conversations, answers, upload tokens."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.models = am.Models()
        self.dir = settings.data_dir / "aura"
        self.conversations: dict[str, dict] = {}
        self.answers: dict[str, tuple[str, dict]] = {}       # answer_id -> (drawing id, response)
        self.tokens: dict[str, dict] = {}                     # upload drawing id -> {file_id, token}
        self.requests = 0                                     # model requests made for AURA turns since start
        self.answered = 0
        self.t0 = time.time()
        self.storeys: tuple[tuple, dict] | None = None        # the library's storey index, by its signature
        self.rasters: dict[tuple, bytes] = {}                 # pages drawn from a model: (id, signature, side) -> PNG
        self._load()

    # conversations are small JSON files, so a reload of the page finds its turns after a service restart
    def _load(self) -> None:
        files = sorted((self.dir / "conversations").glob("*.json"), key=lambda p: p.stat().st_mtime)[-MAX_CONVERSATIONS:] \
            if (self.dir / "conversations").is_dir() else []
        for fp in files:
            try:
                conv = json.loads(fp.read_text())
                self.conversations[conv["id"]] = conv
                for t in conv["turns"]:
                    if t.get("response", {}).get("answer_id"):
                        self.answers[t["response"]["answer_id"]] = (conv["drawing_id"], t["response"])
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def save(self, conv: dict) -> None:
        try:
            d = self.dir / "conversations"
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{conv['id']}.tmp"
            tmp.write_text(json.dumps(conv, ensure_ascii=False, default=str))
            tmp.replace(d / f"{conv['id']}.json")
        except OSError:
            pass  # a read-only store directory: conversations then live as long as the process

    def conversation(self, conv_id: str | None, drawing_id: str) -> dict:
        """The conversation to continue; an unknown id, or one of another drawing, gets a new one."""
        conv = self.conversations.get(conv_id or "")
        if conv is None or conv["drawing_id"] != drawing_id:
            conv = dict(id=new_id(), drawing_id=drawing_id, turns=[])
            self.conversations[conv["id"]] = conv
            while len(self.conversations) > MAX_CONVERSATIONS:
                self.conversations.pop(next(iter(self.conversations)))
        return conv

    # upload tokens: only someone holding the token returned by POST /uploads may delete that upload
    def _tokens(self) -> dict:
        try:
            self.tokens.update(json.loads((self.dir / "uploads.json").read_text()))
        except (OSError, ValueError):
            pass
        return self.tokens

    def _save_tokens(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "uploads.json").write_text(json.dumps(self.tokens))
        except OSError:
            pass  # a read-only store directory: the tokens then live as long as the process

    def remember_upload(self, drawing_id: str, file_id: str, token: str) -> None:
        self._tokens()[drawing_id] = dict(file_id=file_id, token=token)
        self._save_tokens()

    def upload(self, drawing_id: str) -> dict | None:
        return self._tokens().get(drawing_id)

    def forget_upload(self, drawing_id: str) -> None:
        tokens = self._tokens()
        if tokens.pop(drawing_id, None) is not None:
            self._save_tokens()


def state(request: Request) -> Aura:
    app = request.app
    if getattr(app.state, "aura", None) is None:
        app.state.aura = Aura(app.state.settings)
    return app.state.aura


async def in_db(fn: Callable, *args: Any, **kw: Any) -> Any:
    def call():
        with DB() as db:
            return fn(db, *args, **kw)
    return await asyncio.to_thread(call)


async def model_of(request: Request, drawing_id: str) -> Model | None:
    key, slug = am.split_id(drawing_id)
    if key is None:
        return None
    return await in_db(state(request).models.get, key, slug)


def unknown(drawing_id: str) -> JSONResponse:
    return err("unknown_drawing", f"Unknown drawing '{drawing_id[:80]}'", 404)


def not_ready(m: Model) -> JSONResponse | None:
    if m.status == "ready":
        return None
    why = next((f["error"] for f in m.files if f["error"]), None) if m.status == "failed" else None
    return err("processing", (why or f"Drawing '{m.id}' is {m.status}.")[:300], 409)


# ---------------------------------------------------------------------------------------------- drawings
@router.get("/health")
async def health(request: Request) -> JSONResponse:
    s: Settings = request.app.state.settings
    try:
        n = (await in_db(lambda db: db.one("select count(distinct drawing_key) n from jstore.source_file")))["n"]
        db_ok = True
    except Exception:  # noqa: BLE001
        n, db_ok = 0, False
    ok = db_ok and s.data_dir.is_dir()
    return JSONResponse(dict(ok=ok, contract_version=CONTRACT_VERSION,
                             checks=dict(db=db_ok, data_dir=s.data_dir.is_dir(), drawings=n,
                                         models="on" if models_on(request) else "off")), 200 if ok else 503)


def models_on(request: Request) -> bool:
    s: Settings = request.app.state.settings
    return request.app.state.registry.injected is not None or s.model == "demo" or s.api_key is not None


def _storeys(db: DB, a: Aura | None) -> dict[str, list[am.Storey]]:
    """The storey views of every library drawing with an IFC model, kept until the store changes."""
    sig = tuple(db.one("""select (select count(*) || '/' || coalesce(max(entity_id), 0) from jstore.entity) as ents,
                                 (select md5(string_agg(file_id || status || coalesce(storey, ''), ',' order by file_id))
                                    from jstore.source_file where project_id not like 'upload/%') as files,
                                 (select count(*) from jstore.frame) as frames, (select count(*) from jstore.page) as pages,
                                 (select count(*) from jstore.ifc_geometry) as shapes""").values())
    if a is not None and a.storeys is not None and a.storeys[0] == sig:
        return a.storeys[1]
    index = am.storey_index(db)
    if a is not None:
        a.storeys = (sig, index)
    return index


def _list(db: DB, limit: int, a: Aura | None = None) -> list[dict]:
    rows = db.run("""
        select d.*, (select pr.meta from jstore.project pr where pr.project_id = d.project) as project_meta,
               (select count(*) from jstore.entity e where e.drawing_key = d.drawing_key and e.status = 'active') as entities
        from (select f.drawing_key, min(f.project_id) as project, array_agg(distinct f.format) as formats, count(*) as n_files,
                     bool_or(f.status in ('parsed', 'interpreted')) as usable,
                     bool_or(f.status = 'registered' and f.error is null) as waiting,
                     max(f.meta->>'scale') as scale, max(f.ingested_at) as ingested,
                     bool_or(exists (select 1 from jstore.page p where p.file_id = f.file_id and p.image is not null)) as has_image,
                     bool_or(f.format in ('dxf', 'dwg') and exists (select 1 from jstore.frame fr where fr.file_id = f.file_id)
                             and exists (select 1 from jstore.cad_entity c where c.file_id = f.file_id)) as has_cad
              from jstore.source_file f where f.project_id not like 'upload/%' group by f.drawing_key) d
        order by (select count(*) from jstore.entity e where e.drawing_key = d.drawing_key and e.status = 'active') = 0, d.n_files desc, d.drawing_key limit :n""", n=limit)
    storeys = _storeys(db, a) if any("ifc" in r["formats"] for r in rows) else {}
    out = []
    for r in rows:
        did = am.drawing_id(r["drawing_key"])
        n = r["entities"]
        entry = dict(
            id=did, title=am.title_of(r["drawing_key"]), kind=am.kind_of(r["formats"]),
            status="ready" if r["usable"] else "processing" if r["waiting"] else "failed",
            thumbnail=f"/drawings/{did}/thumb.png" if r["has_image"] else f"/drawings/{did}/plan.svg" if r["has_cad"] else "",
            credit=am.credit_of(r["project"], r["project_meta"]),
            subtitle=" · ".join(x for x in (r["project"].split("/", 1)[0], " + ".join(f.upper() for f in sorted(r["formats"])),
                                            r["scale"], f"{n} object{"s" if n != 1 else ""}" if n else "not interpreted") if x),
            source="library", created_at=r["ingested"].isoformat() if r["ingested"] else None)
        views = storeys.get(r["drawing_key"]) if r["usable"] else None
        for st in views or [None]:                      # a model: one entry per storey, lowest first
            e = dict(entry)
            if st is not None:
                sid = did if len(views) == 1 else did + am.STOREY_SEP + st.slug   # one storey: the drawing's own id opens it
                what = [f"{st.n_rooms} room{"s" if st.n_rooms != 1 else ""}" if st.n_rooms else None,
                        f"{st.n_items} object{"s" if st.n_items != 1 else ""}" if st.n_items else None]
                e.update(id=sid, title=f"{entry['title']} · {st.name}"[:160] if st.name else entry["title"],
                         thumbnail=f"/drawings/{sid}/thumb.png",
                         subtitle=" · ".join(x for x in (                 # the storey first: cards cut long lines
                             st.name or None, am.storey_note(st), *what, None if st.occupied else "outlines only",
                             r["project"].split("/", 1)[0], " + ".join(f.upper() for f in sorted(r["formats"]))) if x))
            out.append(am._clean(e))
    return out


@router.get("/drawings")
async def list_drawings(request: Request, limit: int = 2000) -> dict:
    """Library drawings (never uploads), interpreted ones first; a model as one entry per storey."""
    return dict(contract_version=CONTRACT_VERSION, drawings=await in_db(_list, max(1, min(limit, 5000)), state(request)))


@router.get("/drawings/{drawing_id}/status")
async def drawing_status(drawing_id: str, request: Request):
    m = await model_of(request, drawing_id)
    if m is None:
        return unknown(drawing_id)
    body = dict(contract_version=CONTRACT_VERSION, id=m.id, status=m.status)
    if m.status == "failed":
        body["error"] = (next((f["error"] for f in m.files if f["error"]), None) or "The file could not be read.")[:300]
    return body


@router.get("/drawings/{drawing_id}")
async def get_drawing(drawing_id: str, request: Request):
    m = await model_of(request, drawing_id)
    if m is None:
        return unknown(drawing_id)
    if (r := not_ready(m)) is not None:
        return r
    meta = await in_db(lambda db: db.one("select meta from jstore.project where project_id = :p", p=m.files[0]["project_id"]))
    return JSONResponse(am.drawing_doc(m, (meta or {}).get("meta")),
                        headers={"Cache-Control": NO_STORE if m.upload else "private, max-age=5"})


def _thumb(path: Path, cache: Path, side: int = 640) -> bytes:
    """A small PNG of a page render, cached under <store>/aura/thumbs when that directory is writable."""
    try:
        if cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
            return cache.read_bytes()
    except OSError:
        pass
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 400_000_000
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{new_id()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(cache)
    except OSError:
        pass
    return data


@router.get("/drawings/{drawing_id}/{name}")
async def drawing_file(drawing_id: str, name: str, request: Request):
    """plan.svg, original.png and thumb.png as the frontend loads them; drawing.json, model.json, evidence.json and
    checks.json are the same data as documents, for other clients."""
    if name not in IMAGE_FILES + JSON_FILES:
        return err("not_found", "No such file.", 404)
    m = await model_of(request, drawing_id)
    if m is None:
        return unknown(drawing_id)
    if (r := not_ready(m)) is not None:
        return r
    s: Settings = request.app.state.settings
    cache = NO_STORE if m.upload else "private, max-age=60"
    headers = {**SAFE, "Cache-Control": cache}
    if name in JSON_FILES:
        if name == "drawing.json":
            body = dict(am.drawing_doc(m), evidence=am.evidence_doc(m), gold=[])
        elif name == "model.json":
            body = am.model_doc(m)
        elif name == "evidence.json":
            body = dict(contract_version=CONTRACT_VERSION, id=m.id, boxes=am.evidence_doc(m),
                        rooms=[dict(room_id=r["room_id"], included=r["area_m2"] is not None and not r["outdoor"],
                                    area_m2=r["area_m2"]) for r in m.rooms])
        else:
            body = am.checks_doc(m)
        return JSONResponse(body, headers={"Cache-Control": cache})
    if name == "plan.svg":
        svg = await in_db(am.plan_svg, m)
        return Response(svg, media_type="image/svg+xml", headers=headers)
    if m.synthetic:                                      # a storey drawn from the model: a raster of the same picture
        a, k = state(request), (m.id, m.signature, 640 if name == "thumb.png" else 2000)
        data = a.rasters.get(k)
        if data is None:
            data = a.rasters[k] = await asyncio.to_thread(am.plan_png, m, k[2])
            while len(a.rasters) > 300:
                a.rasters.pop(next(iter(a.rasters)))
        return Response(data, media_type="image/png", headers=headers)
    path = am.page_path(s.data_dir, m)
    if path is None:
        return err("not_found", "This drawing has no rendered page.", 404)
    if name == "thumb.png":
        b = m.base
        data = await asyncio.to_thread(_thumb, path, s.data_dir / "aura" / "thumbs" / f"{b.file_id}-{b.page_no}.png")
        return Response(data, media_type="image/png", headers=headers)
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=mime, headers=headers)


# ---------------------------------------------------------------------------------------------- uploads
@router.post("/uploads", status_code=202)
async def post_upload(file: UploadFile, request: Request):
    """An upload becomes a drawing of its own in the store; it is never listed, and its id is not guessable."""
    s: Settings = request.app.state.settings
    if extension(file.filename or "") is None:
        return err("unsupported_type", "This file type is not supported.", 415)
    limit, size = s.max_upload_mb * 1024 * 1024, 0
    uploads = s.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    tmp = uploads / f".incoming-{new_id()}.{extension(file.filename or '')}"
    try:
        with open(tmp, "wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    return err("too_large", f"The file is larger than {s.max_upload_mb} MB.", 413)
                out.write(chunk)
        if size == 0:
            return err("bad_request", "The file is empty.", 400)
        row = await asyncio.to_thread(store_upload, s, tmp, file.filename or tmp.name)
    finally:
        tmp.unlink(missing_ok=True)
    request.app.state.ingestor.ensure(row)
    did, token = am.drawing_id(row["drawing_key"]), secrets.token_urlsafe(24)
    if str(row["project_id"]).startswith("upload/"):   # the same bytes as a dataset file stay a dataset drawing
        await asyncio.to_thread(state(request).remember_upload, did, row["file_id"], token)
    return dict(contract_version=CONTRACT_VERSION, drawing_id=did, delete_token=token, status="queued")


def _delete_upload(db: DB, s: Settings, file_id: str) -> None:
    project = f"upload/{file_id}"
    db.run("delete from jstore.issue where project_id = :p", p=project)
    db.run("delete from jstore.project where project_id = :p", p=project)     # cascades to files, pages, entities
    for d in (s.data_dir / "uploads" / file_id, s.data_dir / "renders" / file_id):
        shutil.rmtree(d, ignore_errors=True)


@router.delete("/drawings/{drawing_id}")
async def delete_drawing(drawing_id: str, request: Request):
    a = state(request)
    up = await asyncio.to_thread(a.upload, drawing_id)
    given = request.headers.get("x-delete-token", "")
    if up is None or not secrets.compare_digest(given.encode(), up["token"].encode()):
        return err("not_found", "No such upload, or the delete token does not match.", 404)
    await in_db(_delete_upload, request.app.state.settings, up["file_id"])
    await asyncio.to_thread(a.forget_upload, drawing_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------------------------- questions
class AskIn(BaseModel):
    drawing_id: str = Field(min_length=1, max_length=700)
    question: str = Field(min_length=1, max_length=4000)
    scope: dict | None = None
    overrides: list[dict] | None = Field(default=None, max_length=50)
    lang_hint: str | None = None


class ChatIn(BaseModel):
    drawing_id: str = Field(min_length=1, max_length=700)
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=64)
    scope: dict | None = None
    overrides: list[dict] | None = Field(default=None, max_length=50)
    lang_hint: str | None = None


class RewriteIn(BaseModel):
    drawing_id: str = Field(min_length=1, max_length=700)
    text: str = Field(min_length=1, max_length=2000)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def clean_scope(scope: dict | None) -> dict:
    """The request's scope as a valid contract Scope (anything else counts as the whole drawing)."""
    s = scope or {}
    kind = s.get("kind")
    if kind == "unit" and isinstance(s.get("unit"), str):
        return dict(kind="unit", unit=s["unit"][:40])
    if kind == "room" and isinstance(s.get("room_id"), str):
        return dict(kind="room", room_id=s["room_id"][:60])
    if kind == "label" and isinstance(s.get("label"), str):
        return dict(kind="label", label=s["label"][:120])
    rect = s.get("rect")
    if kind == "region" and isinstance(rect, dict) and all(_num(rect.get(k)) for k in "xywh"):
        return dict(kind="region", rect={k: float(rect[k]) for k in "xywh"}, label=str(s.get("label") or "Area")[:60])
    return dict(kind="model") if kind == "model" else dict(kind="floor")


_STOP = {"fr": r"combien|quelle?s?|surface|pièces?|est|dans|les|des|une?|du|de la|y a-t-il|fenêtres?|portes?|où",
         "de": r"wie ?viele|welche[rs]?|fläche|zimmer|räume|gibt es|sind|der|die|das|und|türen|fenster|wo|ist",
         "fi": r"montako|kuinka|mikä|missä|pinta-ala|huone\w*|ovea|ovia|ikkuna\w*|on|ja|asunno\w*|paljonko",
         "en": r"how many|what|which|where|area|rooms?|doors?|windows?|is|are|the|of|in|there"}


def detect_lang(text: str, hint: str | None = None) -> str:
    low = text.lower()
    score = {lang: len(re.findall(rf"(?<![\w-])(?:{pat})(?![\w-])", low)) for lang, pat in _STOP.items()}
    best = max(score, key=lambda k: score[k])
    if score[best] == 0 or sum(1 for v in score.values() if v == score[best]) > 1:
        return hint if hint in LANGS else "en" if score["en"] == score[best] else best
    return best


_SUBJECT_WORDS = (
    ("window", r"windows?|fen[êe]tres?|ikkun\w*|fenster"),
    ("washbasin", r"wash ?basins?|hand ?basins?|basins?|lavabos?|pesualta\w*|waschbecken|waschtisch\w*"),
    ("sink", r"sinks?|éviers?|tiskialta\w*|spülen?"),
    ("bathtub", r"bath ?tubs?|tubs?|baignoires?|kylpyam\w*|badewannen?"),
    ("shower", r"showers?|douches?|suihku\w*|duschen?"),
    ("wc", r"wcs?|toilets?|toilettes?|vessa\w*|wc-istui\w*|klosetts?"),
    ("door", r"doors?|portes?|ovi|ovea|ovia|ovet|ovien|türen?"),
    ("apartment", r"apartments?|flats?|dwellings?|appartements?|logements?|asunto\w*|asunno\w*|huoneisto\w*|wohnung(?:en)?"),
    ("bedroom", r"bed ?rooms?|chambres?(?: à coucher)?|makuuhuone\w*|schlafzimmer"),
    ("kitchen", r"kitchens?|cuisines?|keittiö\w*|küchen?"),
    ("bathroom", r"bath ?rooms?|salles? de bains?|kylpyhuone\w*|badezimmer|bäder"),
    ("sauna", r"sauna\w*|sauno\w*"),
    ("balcony", r"balcon(?:y|ies|s)?|parveke\w*|parvekke\w*|balkone?"),
    ("room", r"rooms?|pièces?|huone\w*|zimmer|räume?"),
)


def guess_subject(question: str) -> str | None:
    for subject, pat in _SUBJECT_WORDS:
        if re.search(rf"(?<![\w])(?:{pat})(?![\w])", question, re.I):
            return subject
    return None


def compose(m: Model, text: str, scope: dict, overrides: list[dict] | None, history: list[dict]) -> str:
    """The question as the store's agent gets it: the user's words plus what the UI set outside the text."""
    parts = [text.strip()]
    kind = scope["kind"]
    if m.storey is not None and len(m.storeys) > 1:
        st = m.storey
        if st.name:
            level = f", elevation {st.elevation:g} m" if st.elevation is not None else ""
            parts.append(f"Shown in the UI: storey '{st.name}' of the model{level} (column storey of q.rooms, q.items, "
                         f"q.elements and q.spaces = '{st.name}'). "
                         "'This floor', 'this storey', 'here', or a question that names no storey and no whole building, "
                         "means this storey only: restrict the answer and its entity_ids to it.")
        else:
            parts.append("Shown in the UI: the things of the model that are assigned to no storey (column storey is null). "
                         "'This floor' or 'here' means those only.")
    if kind == "unit":
        parts.append(f"Scope set in the UI: only apartment '{scope['unit']}' (column apartment of q.rooms and q.items).")
    elif kind == "room":
        room = next((r for r in m.rooms if r["room_id"] == scope["room_id"]), None)
        where = f"entity_id {room['entity_id']}, number '{room['num']}'" if room else f"number '{scope['room_id']}'"
        parts.append(f"Scope set in the UI: only the room with {where} (q.rooms; things in it have room = its number).")
    elif kind == "label":
        parts.append(f"Scope set in the UI: only rooms labelled '{scope['label']}' (q.rooms.label).")
    elif kind == "region" and m.base is not None:
        r, b = scope["rect"], m.base
        if b.frame is not None:
            to_m = am.mul(b.frame, am.inv(b.to_pct))
            pts = [am.apply(to_m, x, y) for x, y in ((r["x"], r["y"]), (r["x"] + r["w"], r["y"] + r["h"]))]
            x0, x1 = sorted(p[0] for p in pts)
            y0, y1 = sorted(p[1] for p in pts)
            parts.append("Scope set in the UI: only things whose centre lies inside the marked region "
                         f"ST_MakeEnvelope({x0:.3f}, {y0:.3f}, {x1:.3f}, {y1:.3f}) of the drawing frame "
                         "(the geom columns of q.rooms and q.items are in this frame; test ST_Centroid(geom) && that envelope).")
        else:
            parts.append(f"Scope set in the UI: only the marked region of the page image of file {b.file_id} page {b.page_no}, "
                         f"from {r['x']:.1f} % to {r['x'] + r['w']:.1f} % of its width and {r['y']:.1f} % to "
                         f"{r['y'] + r['h']:.1f} % of its height (q.entity_sources.bbox of that file is in its pixels, "
                         f"the image is {b.w} x {b.h} px).")
    for o in overrides or []:
        if isinstance(o, dict) and isinstance(o.get("room_id"), str) and _num(o.get("w_m")) and _num(o.get("h_m")):
            room = next((r for r in m.rooms if r["room_id"] == o["room_id"]), None)
            name = f"room number '{room['num']}' (entity_id {room['entity_id']})" if room else f"room '{o['room_id']}'"
            parts.append(f"What-if set in the UI: assume {name} measures {o['w_m']:g} m x {o['h_m']:g} m, so its net area "
                         f"is {round(o['w_m'] * o['h_m'], 2):g} m2 instead of the stored value.")
    if history:
        parts.append("Earlier in this conversation (for context only): " + " | ".join(
            f"Q: {h['q'][:200]} A: {h['a'][:120]}" for h in history[-3:]))
    return "\n".join(parts)


def _history(conv: dict) -> list[dict]:
    out = []
    for t in conv["turns"]:
        a = (t.get("response") or {}).get("answer") or {}
        if a.get("kind") in ("count", "area", "quantity", "text"):
            out.append(dict(q=t["message"], a=f"{a.get('value')} {a.get('subject') or a.get('unit') or ''}".strip()))
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.051, 0.005 * abs(b))


def _room_ev(r: dict, included: bool, edited: set[str]) -> dict:
    out = dict(room_id=r["room_id"], included=bool(included and r["area_m2"] is not None), area_m2=r["area_m2"])
    if r["room_id"] in edited:
        out["edited"] = True
    return out


def translate(m: Model, a: dict, question: str, overrides: list[dict] | None = None) -> dict:
    """One answer of the store's agent -> {answer, evidence, method, explanation, estimate?} of the contract.
    The evidence is rebuilt from the entity ids on the drawing's page, so every box and room the response names
    exists in GET /drawings/{id}."""
    named = list(dict.fromkeys(a.get("entity_ids") or []))
    ents = [m.by_entity[i] for i in named if i in m.by_entity]
    away: dict[str, int] = {}                     # things behind the answer that lie on other storeys: counted, not drawn
    for i in named:
        if i in m.elsewhere:
            away[m.elsewhere[i]["storey"]] = away.get(m.elsewhere[i]["storey"], 0) + 1
    rooms = [e for e in ents if e["kind"] == "room"]
    apts = [e for e in ents if e["kind"] == "apartment"]
    items = [e for e in ents if e["kind"] not in ("room", "apartment")]
    units = {x["unit"] for x in apts if x["unit"]}
    apt_rooms = [r for r in m.rooms if r["unit"] in units and r not in rooms]
    edited = {o["room_id"] for o in overrides or [] if isinstance(o, dict) and isinstance(o.get("room_id"), str)}
    value, unit = a.get("answer"), (a.get("unit") or "").strip().lower().replace("²", "2").replace("³", "3")
    number = _num(value)
    warnings = list(a.get("warnings") or [])

    boxes = sorted((b for b in (am.box_of(i) for i in items) if b), key=lambda b: (round(b["y"], 1), b["x"]))  # reading order
    rows = [dict(id=i["id"], name=" ".join(x for x in (i["kind"].replace("_", " "), i["tag"] or i["label"]) if x))
            for i in items if not i["box"]]
    n_text = 0
    for e in a.get("evidence") or []:          # Layer 1 references (texts, strokes): a box when they lie on the page shown
        if e.get("entity_id") is not None:
            continue
        label = str(e.get("text") or e.get("layer") or e.get("name") or e.get("ref_key") or "reference")[:120]
        rect = m.on_page(e.get("file_id"), e.get("page_no") or 0, e.get("bbox"))
        if rect is not None:
            n_text += 1
            boxes.append(dict(id=f"text-{n_text:02d}", **rect, subject="label", note=label))
        else:
            rows.append(dict(id=f"{e.get('ref_table', 'ref')}:{e.get('ref_key', '')}"[:80], name=label))

    included = {r["room_id"]: True for r in rooms + apt_rooms}
    answer: dict
    if value is None or (isinstance(value, str) and not value.strip()):
        why = " ".join(x for x in (a.get("method"), *warnings) if x).strip()
        answer = am._clean(dict(kind="abstain", reason="not_on_drawing", where_to_find=why[:300] or None))
    elif number and unit in ("count", "", "pcs", "items", "pieces") and float(value).is_integer() and \
            (subject := _count_subject(rooms, apts, items, question, bool(unit))) is not None:
        answer = dict(kind="count", value=int(value), subject=subject)
    elif number and unit == "m2" and (rooms or apt_rooms) and not items:
        pool = rooms + apt_rooms
        total = sum(r["area_m2"] or 0 for r in pool)
        indoor = sum(r["area_m2"] or 0 for r in pool if not r["outdoor"])
        if _close(indoor, float(value)) and not _close(total, float(value)):
            included = {r["room_id"]: not r["outdoor"] for r in pool}
            answer = dict(kind="area", value=round(float(value), 2), unit="m2")
        elif _close(total, float(value)):
            answer = dict(kind="area", value=round(float(value), 2), unit="m2")
        else:  # not the plain sum of these rooms (a what-if, a difference, an average): shown as given
            answer = dict(kind="quantity", value=round(float(value), 3), unit="m2")
    elif number and unit in ("m", "m2", "m3"):
        answer = dict(kind="quantity", value=round(float(value), 3), unit=unit)
    else:
        text = f"{value:g}" if number else str(value).strip()
        if number and unit and unit != "count":
            text += f" {a.get('unit')}"
        answer = dict(kind="text", value=text[:600], subject="label")

    # A count answered from Layer 1 (a drawing not interpreted yet): the referenced primitives are the things
    # counted, so they carry the subject and are numbered in reading order like interpreted items would be.
    refs = [b for b in boxes if b["subject"] == "label" and b["id"].startswith("text-")]
    if answer["kind"] == "count" and answer["subject"] not in ("room", "apartment") and refs and not items and not rooms \
            and not any(i["subject"] == answer["subject"] for i in m.items):
        prefix = next((v[1] for v in am.SUBJECT.values() if v[0] == answer["subject"]), answer["subject"].replace("_", ""))
        refs.sort(key=lambda b: (round(b["y"], 1), b["x"]))
        for n, b in enumerate(refs, 1):
            b.update(id=f"{prefix}-{n:0{max(2, len(str(len(refs))))}d}", subject=answer["subject"])
        boxes = refs
    ev_rooms = [_room_ev(r, included.get(r["room_id"], True), edited) for r in rooms + apt_rooms]
    evidence: dict = dict(boxes=boxes, rooms=ev_rooms)
    if rows:
        evidence["rows"] = rows[:200]

    used = {e["tool"] for e in a.get("_tools") or []}
    formats = {s.get("format") for s in a.get("sources") or []} | {e.get("format") for e in a.get("evidence") or []}
    formats = {f for f in formats if f} or {f["format"] for f in m.files}
    methods = {s.get("method") for s in a.get("sources") or []}
    if answer["kind"] == "abstain":
        method = "none"
    elif "view_image" in used and (warnings or "sql" not in used):
        method = "vision"
    elif formats & {"dxf", "dwg", "ifc", "svg", "pdf"}:
        method = "cad_geometry"
    else:  # a raster: read from its texts, or from what was detected on it
        texts = any(e.get("ref_table") in ("raster_word", "pdf_text") for e in a.get("evidence") or [])
        method = "ocr" if texts or methods & {"ocr", "printed"} else "detector"
    out = dict(answer=answer, evidence=evidence, method=method)
    in_units = {x.get("unit") for x in items + rooms + apt_rooms}
    whole = answer["kind"] == "area" or (answer["kind"] == "count" and answer["value"] in (len(items), len(rooms), len(apts)))
    if whole and len(in_units) == 1 and None not in in_units and len({r["unit"] for r in m.rooms if r["unit"]}) > 1:
        out["unit_scope"] = next(iter(in_units))     # everything behind the answer lies in one apartment
    if method == "vision":
        out["estimate"] = dict(confidence=round(float(a.get("confidence") or 0), 2))
    agreement = "; ".join(f"{prop}: " + ", ".join(f"{n} {k}" for k, n in sorted(v.items()))
                          for prop, v in sorted((a.get("agreement") or {}).items()))
    unplaced = sum(1 for i in items if not i["box"]) + sum(1 for r in rooms if not r["points"])
    notes = [a.get("method"),
             (f"{sum(away.values())} of the things behind this answer are on other storeys ("
              + ", ".join(f"{k}: {v}" for k, v in list(away.items())[:8]) + ") and are not drawn on this plan"
              + (f" of {m.storey.name}." if m.storey is not None and m.storey.name else ".")) if away else None,
             None if (a.get("check") or "none").lower().startswith("none") else f"Check: {a['check']}",
             f"Sources: {agreement}." if agreement else None,
             f"{unplaced} of the things behind this answer have no position on the page shown." if unplaced else None,
             *[f"Warning: {w}" for w in warnings]]
    explanation = " ".join(str(x).strip() for x in notes if x and str(x).strip())
    if explanation:
        out["explanation"] = explanation[:900]
    return out


def _count_subject(rooms: list, apts: list, items: list, question: str, explicit: bool) -> str | None:
    if items and not rooms and not apts:
        kinds = {i["kind"] for i in items}
        if len(kinds) == 1 and next(iter(kinds)) in am.SUBJECT:
            return am.SUBJECT[next(iter(kinds))][0]
        return guess_subject(question)
    if apts and not items and not rooms:
        return "apartment"
    if rooms and not items and not apts:
        types = {am.ROOM_SUBJECT.get(r["room_type"] or "") for r in rooms}
        return next(iter(types)) if len(types) == 1 and None not in types else "room"
    guess = guess_subject(question)
    return guess if (explicit or rooms or apts or items) else guess if re.search(
        r"how many|number of|combien|nombre|montako|kuinka monta|wie ?viele|anzahl", question, re.I) else None


TOOL_TITLE = {"sql": "Query the store", "view_image": "Look at the page", "shell": "Work on the original file"}


def tool_node(started: dict, n: int) -> dict:
    tool = started.get("tool", "?")
    args = started.get("query") or started.get("command") or started.get("arguments") or ""
    body = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return dict(id=f"t{n}", parent="q", title=TOOL_TITLE.get(tool, tool), tag=tool, body=body[:1200],
                kind="model" if tool == "view_image" else "exact" if tool == "sql" else "info")


def tool_summary(finished: dict) -> str:
    if finished.get("tool") == "sql" and finished.get("ended") == "exit":
        return f"{finished.get('rows', 0)} row(s)"
    if finished.get("ended") not in ("exit", None):
        return str(finished.get("output") or finished.get("ended"))[:200]
    return "done"


def trace_of(run: Run, question: str, lang: str, a: dict, part: dict) -> list[dict]:
    """The run's steps as the reasoning tree: the question, one node per tool call, sources, warnings, the answer."""
    ids = [b["id"] for b in part["evidence"]["boxes"]] + [f"room-{r['room_id']}" for r in part["evidence"]["rooms"]]
    nodes = [dict(id="q", title="Question", tag=f"{LANG_NAME[lang]} · detected", body=f"“{question[:500]}”", kind="info")]
    started: dict[str, dict] = {}
    n = 0
    for e in run.events:
        if e["type"] == "tool_started":
            n += 1
            started[e.get("call_id") or str(n)] = tool_node(e, n)
        elif e["type"] == "tool_finished":
            node = started.pop(e.get("call_id") or "", None) or tool_node(e, (n := n + 1))
            ok = e.get("ended") == "exit" and not e.get("exit_code")
            node["tag"] = f"{node['tag']} · {tool_summary(e)}"[:80]
            if not ok:
                node["kind"] = "warn"
                node["body"] = f"{node['body']}\n→ {str(e.get('output') or e.get('ended'))[:400]}"
            nodes.append(node)
    nodes += started.values()
    if a.get("agreement"):
        conflict = any("conflict" in v for v in a["agreement"].values())
        nodes.append(dict(id="sources", parent="q", title="Compare the sources", tag="q.facts",
                          body="; ".join(f"{p}: " + ", ".join(f"{c} {k}" for k, c in sorted(v.items()))
                                         for p, v in sorted(a["agreement"].items())),
                          kind="warn" if conflict else "exact", evidence_ids=ids[:400]))
    for i, w in enumerate(a.get("warnings") or [], 1):
        nodes.append(dict(id=f"warn{i}", parent="q", title="Verification", tag="service", body=str(w)[:600], kind="warn"))
    ans = part["answer"]
    body = "Nothing stated." if ans["kind"] == "abstain" else \
        f"{ans['value']} ({ans['subject']})." if ans["kind"] == "count" else f"{ans['value']} {ans.get('unit') or ''}".strip() + "."
    node = dict(id="answer", parent="q", title="Answer", tag=ans["kind"], body=f"{body} {a.get('method') or ''}".strip()[:700],
                kind="warn" if ans["kind"] == "abstain" or a.get("warnings") else "model" if part["method"] == "vision" else "exact",
                evidence_ids=ids[:400])
    if _num(a.get("confidence")):
        node["confidence"] = round(float(a["confidence"]), 2)
    nodes.append(node)
    return nodes


def cost_of(s: Settings, usage: dict | None) -> float:
    """USD from the token counts, when prices are configured (JSTORE_PRICE_IN / JSTORE_PRICE_OUT per million tokens)."""
    try:
        pin, pout = float(os.environ.get("JSTORE_PRICE_IN") or 0), float(os.environ.get("JSTORE_PRICE_OUT") or 0)
    except ValueError:
        return 0.0
    u = usage or {}
    return round(((u.get("input_tokens") or 0) * pin + (u.get("output_tokens") or 0) * pout) / 1e6, 6)


class Turn:
    """One question on its way through the store's agent."""

    def __init__(self, request: Request, m: Model, text: str, scope: dict | None, overrides: list[dict] | None,
                 lang_hint: str | None, history: list[dict] | None = None):
        self.aura, self.registry = state(request), request.app.state.registry
        self.settings: Settings = request.app.state.settings
        self.m, self.text, self.overrides = m, text.strip(), overrides
        self.scope = clean_scope(scope)
        self.lang = detect_lang(text, lang_hint)
        self.question = compose(m, text, self.scope, overrides, history or [])
        self.t0 = time.monotonic()
        self.run: Run | None = None
        self.task: asyncio.Task | None = None

    async def start(self) -> Run:
        registry: Registry = self.registry
        self.run = await asyncio.to_thread(registry.create, [self.m.files[0]["file_id"]], [self.question])
        self.task = registry.start(self.run)
        return self.run

    def spent(self) -> dict:
        u = (self.run.usage if self.run else None) or {}
        return dict(calls=int(u.get("requests") or 0), cost_usd=cost_of(self.settings, u))

    async def response(self, conversation_id: str | None = None, turn_id: str | None = None) -> dict:
        """The AskResponse of the finished run (raises RuntimeError when the run failed)."""
        run = self.run
        self.aura.requests += self.spent()["calls"]
        if run.status != "done" or not run.answers:
            raise RuntimeError(run.error or "the run ended without an answer")
        a = dict(run.answers[0], _tools=[e for e in run.events if e["type"] == "tool_started"])
        slug = am.split_id(self.m.id)[1]
        model = await in_db(self.aura.models.get, self.m.key, slug) or self.m     # an upload may have been interpreted meanwhile
        part = translate(model, a, self.text, self.overrides)
        unit = part.pop("unit_scope", None)
        if unit and self.scope["kind"] == "floor":
            self.scope = dict(kind="unit", unit=unit)
        out = dict(contract_version=CONTRACT_VERSION, **part, detected_lang=self.lang, canonical_en=self.text[:500],
                   scope=self.scope, trace=trace_of(run, self.text, self.lang, a, part), **self.spent(),
                   latency_ms=int((time.monotonic() - self.t0) * 1000), source="api", answer_id=new_id(), route="agent")
        if conversation_id:
            out.update(conversation_id=conversation_id, turn_id=turn_id)
        self.aura.answers[out["answer_id"]] = (self.m.id, out)
        self.aura.answered += 1
        while len(self.aura.answers) > 5000:
            self.aura.answers.pop(next(iter(self.aura.answers)))
        return out

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


def deadline_s() -> float:
    try:
        return max(5.0, float(os.environ.get("JSTORE_AURA_DEADLINE_S") or 300))
    except ValueError:
        return 300.0


async def _ready_model(request: Request, drawing_id: str) -> Model | JSONResponse:
    m = await model_of(request, drawing_id)
    if m is None:
        return unknown(drawing_id)
    return not_ready(m) or m


@router.post("/ask")
async def post_ask(body: AskIn, request: Request):
    m = await _ready_model(request, body.drawing_id)
    if isinstance(m, JSONResponse):
        return m
    turn = Turn(request, m, body.question, body.scope, body.overrides, body.lang_hint)
    await turn.start()
    done, _ = await asyncio.wait({turn.task}, timeout=deadline_s())
    if not done:
        turn.cancel()
        return err("timeout", "The answer took too long.", 504, **turn.spent())
    try:
        return JSONResponse(await turn.response(), headers={"Cache-Control": NO_STORE})
    except RuntimeError as e:
        return err("internal", f"The question could not be answered: {str(e)[:300]}", 502, **turn.spent())


def _frame(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


@router.post("/chat")
async def post_chat(body: ChatIn, request: Request):
    """One chat turn as server-sent events: turn, understood, (tool_call, tool_result)*, answer | error, done."""
    m = await _ready_model(request, body.drawing_id)
    if isinstance(m, JSONResponse):
        return m
    a = state(request)
    conv = a.conversation(body.conversation_id, m.id)
    turn_id = new_id()
    turn = Turn(request, m, body.message, body.scope, body.overrides, body.lang_hint, _history(conv))
    empty = dict(boxes=[], rooms=[])

    async def events() -> AsyncIterator[bytes]:
        record: dict = dict(turn_id=turn_id, message=turn.text)
        try:
            yield _frame("turn", dict(conversation_id=conv["id"], turn_id=turn_id))
            yield _frame("understood", dict(detected_lang=turn.lang, canonical_en=turn.text[:500], route="agent"))
            run = await turn.start()
            sent, n, open_nodes, end = 0, 0, {}, time.monotonic() + deadline_s()
            while True:
                changed = run._changed
                while sent < len(run.events):
                    e = run.events[sent]
                    sent += 1
                    if e["type"] == "tool_started":
                        n += 1
                        node = tool_node(e, n)
                        open_nodes[e.get("call_id") or ""] = node["id"]
                        yield _frame("tool_call", dict(node=node))
                    elif e["type"] == "tool_finished" and (e.get("call_id") or "") in open_nodes:
                        yield _frame("tool_result", dict(node_id=open_nodes.pop(e.get("call_id") or ""), evidence=empty,
                                                         summary=tool_summary(e)))
                if run.status in TERMINAL and sent >= len(run.events):
                    break
                if time.monotonic() > end:
                    turn.cancel()
                    record["error"] = dict(contract_version=CONTRACT_VERSION, error="The answer took too long.",
                                           code="timeout", **turn.spent())
                    yield _frame("error", dict(error="The answer took too long.", code="timeout", **turn.spent()))
                    yield _frame("done", {})
                    return
                try:
                    await asyncio.wait_for(changed.wait(), KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
            try:
                response = await turn.response(conv["id"], turn_id)
                record["response"] = response
                yield _frame("answer", dict(response=response))
            except RuntimeError as e:
                msg = f"The question could not be answered: {str(e)[:300]}"
                record["error"] = dict(contract_version=CONTRACT_VERSION, error=msg, code="internal")
                yield _frame("error", dict(error=msg, code="internal", **turn.spent()))
            yield _frame("done", {})
        finally:
            turn.cancel()                      # the client stopped or went away: do not keep paying for the run
            if "response" in record or "error" in record:
                conv["turns"].append(record)
                await asyncio.to_thread(a.save, conv)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": NO_STORE, "X-Accel-Buffering": "no"})


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, request: Request):
    conv = state(request).conversations.get(conv_id)
    if conv is None:
        return err("not_found", "Unknown conversation.", 404)
    return JSONResponse(dict(contract_version=CONTRACT_VERSION, **conv), headers={"Cache-Control": NO_STORE})


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(v: Any) -> Any:
    return "'" + v if isinstance(v, str) and v.startswith(FORMULA_START) else v


def evidence_csv(response: dict, rooms: dict[str, dict]) -> str:
    """The evidence rows of one answer (the columns of the AURA backend's export); formula-like cells are escaped."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["kind", "id", "subject", "room_id", "room_name", "unit", "x", "y", "w", "h", "included", "area_m2",
                "edited", "note", "check_p"])
    ev, ans = response.get("evidence", {}), response.get("answer", {})
    for b in ev.get("boxes", []):
        room = rooms.get(b.get("room_id", ""), {})
        w.writerow([_cell(x) for x in ("box", b["id"], b.get("subject", ""), b.get("room_id", ""), room.get("name", ""),
                                       b.get("unit", ""), b["x"], b["y"], b["w"], b["h"], "", "", "", b.get("note", ""), "")])
    for r in ev.get("rooms", []):
        room = rooms.get(r["room_id"], {})
        w.writerow([_cell(x) for x in ("room", f"room-{r['room_id']}", "room", r["room_id"], room.get("name", ""),
                                       room.get("unit") or "", "", "", "", "", "yes" if r["included"] else "no",
                                       "" if r.get("area_m2") is None else r["area_m2"], "yes" if r.get("edited") else "", "", "")])
    for r in ev.get("rows", []):
        w.writerow([_cell(x) for x in ("element", r["id"], "", "", "", "", "", "", "", "", "", "", "", r.get("name", ""), "")])
    w.writerow([])
    summary = f"abstain: {ans.get('reason')}" if ans.get("kind") == "abstain" else \
        f"{ans.get('value')} {ans.get('subject') or ans.get('unit') or ''}".strip()
    w.writerow([_cell(x) for x in ("answer", response.get("answer_id", ""), summary, response.get("canonical_en", ""))])
    return buf.getvalue()


@router.get("/answers/{answer_id}/evidence.csv")
async def get_evidence_csv(answer_id: str, request: Request):
    hit = state(request).answers.get(answer_id)
    if hit is None:
        return err("not_found", "Unknown answer.", 404)
    m = await model_of(request, hit[0])
    rooms = {r["room_id"]: r for r in (m.rooms if m else [])}
    return Response(evidence_csv(hit[1], rooms), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="aura-{answer_id}.csv"', "Cache-Control": NO_STORE})


# ---------------------------------------------------------------------------------------------- stubs
@router.post("/rewrite")
async def post_rewrite(body: RewriteIn, request: Request):
    """Stub: no model call.  The text comes back as one question for the whole drawing, so the frontend's
    "tidy" step works and costs nothing; nothing is split or re-scoped."""
    m = await _ready_model(request, body.drawing_id)
    if isinstance(m, JSONResponse):
        return m
    text = " ".join(body.text.split())
    intent = "area" if re.search(r"area|surface|fläche|pinta-ala|m2|m²", text, re.I) else \
        "count" if guess_subject(text) else "other"
    block = am._clean(dict(question=text, intent=intent, subject=guess_subject(text), scope=dict(kind="floor"),
                           scope_exists=True, routable=True))
    return dict(contract_version=CONTRACT_VERSION, drawing_id=m.id, detected_lang=detect_lang(text), blocks=[block],
                not_answerable=[], calls=0, cost_usd=0, tokens=0, model="none")


def budget_cap() -> int:
    try:
        return max(1, int(os.environ.get("JSTORE_AURA_BUDGET") or 1000))
    except ValueError:
        return 1000


@router.get("/budget")
async def get_budget(request: Request) -> dict:
    """Stub: the store enforces no request budget.  `*_used` is the number of model requests made for AURA
    turns since this process started; the caps are a display figure (JSTORE_AURA_BUDGET, default 1000)."""
    used, cap = state(request).requests, budget_cap()
    return dict(contract_version=CONTRACT_VERSION, models="on" if models_on(request) else "off",
                event_used=used, event_cap=cap, day_used=used, day_cap=cap)


@router.get("/stats")
async def get_stats(request: Request) -> dict:
    """Stub: counters since this process started (not per day)."""
    a = state(request)
    return dict(contract_version=CONTRACT_VERSION, answers_today=a.answered, model_calls_today=a.requests)


@router.post("/admin/models")
async def admin_models(request: Request) -> dict:
    """Stub: reports whether a model is configured; it cannot be switched from here."""
    return dict(contract_version=CONTRACT_VERSION, models=models_on(request))
