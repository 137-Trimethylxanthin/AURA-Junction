"""The store library: every drawing already in the drawing store is a shared library drawing.

Switch: AURA_STORE_LIBRARY (needs the store on, AURA_STORE_DSN). The pre-built bundles stay as they are;
this adds, for every visitor, each store drawing that is

    - not an upload (`upload/%` projects are never read here),
    - not already shown by a bundle (its drawing key is in a `store-map.json` beside the drawing dirs),
    - not excluded (AURA_STORE_LIBRARY_EXCLUDE: drawing keys or glob patterns),
    - and has something to show (a page render, CAD linework, or an IFC storey with content).

Ids: `st-<slug of the drawing key>-<8 hex of sha1(key)>`, and `...--<storey slug>` for one storey of an IFC
model (a model is one entry per storey, like the IFC bundles). Stable, URL-safe, never `up-`.

Lifetime: these drawings are permanent. They have no upload row (so no TTL, quota, delete or sweep), and
`permanent_file_ids()` names every dataset file of the store so `DrawingStore.forget/sweep` keep them.

How a drawing is served (all in-process; no HTTP to the store API):

    listing    one grouped query + the storey index, cached in memory and re-read only when the store's
               cheap signature changes (checked at most every LIST_TTL_S). No document is built for it.
    document   built on first open by jstore's `aura_model` (the "/aura" layer), converted to contract 1.2
               by `drawing_store.normalise` (the same conversion a store-indexed upload gets), written to
               DATA_DIR/store-library/<id>/drawing.json and kept in memory; rebuilt when the drawing's
               own signature changes.
    images     original.png is the store's page render, found under AURA_STORE_DATA_DIR and then under
               AURA_STORE_RENDER_DIRS (read-only); plan.svg and thumbnails are generated on first request
               into DATA_DIR/store-library/<id>/.

Honesty of methods (`evidence_method`, per subject, the weakest entity decides):
    an entity with a CAD or IFC source is exact (cad_geometry);
    PDF vectors/text only -> pdf_vector; OCR words -> ocr; a model detection, a pixel region or
    anything unknown -> vision (an estimate).

jstore is imported only inside JStoreSource, so the module itself loads without the store.
"""
from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aura import tools
from aura.contract import CONTRACT_VERSION, DrawingSummary
from aura.drawing_store import normalise

if TYPE_CHECKING:
    from aura.config import Settings

log = logging.getLogger("aura.store_library")

PREFIX = "st-"
VERSION = 2            # bump when the generated document or images change shape: every cache is rebuilt
LIST_TTL_S = 30.0      # how often the listing's store signature is re-read
DOC_TTL_S = 30.0       # how often an open drawing's signature is re-read
FILE_IDS_TTL_S = 60.0
DB_TIMEOUT_S = 30.0    # socket timeout of every store connection opened here
MAX_DOCS = 512         # documents kept in memory (each is a few KB to a few hundred KB)
MAX_FLOORS = 50        # ProjectBundle.floors max_length

EXACT_TABLES = {"cad_entity", "ifc_element"}
VECTOR_TABLES = {"pdf_path", "pdf_text"}
OCR_TABLES = {"raster_word"}
FIXTURE_KINDS = ("toilet", "washbasin", "sink", "bathtub", "shower")
SOURCE_NAMES = {"cubicasa5k": "CubiCasa5K", "swiss_dwellings": "Swiss Dwellings", "wikimedia": "Wikimedia Commons",
                "finna": "Finna", "habs": "HABS/HAER", "europeana": "Europeana", "opening_design": "OpeningDesign",
                "ifc_models": "IFC models", "ifc_bench": "IFC bench", "stress": "Stress test"}
SOURCE_ORDER = ["stress", "swiss_dwellings", "opening_design", "ifc_models", "ifc_bench", "cubicasa5k",
                "wikimedia", "finna", "habs", "europeana"]
#: the collection a source belongs to (the library's source filter)
ORIGIN = {"swiss_dwellings": "swiss", "cubicasa5k": "cubicasa", "opening_design": "us_sheets", "ifc_models": "ifc",
          "ifc_bench": "ifc", "wikimedia": "historic", "finna": "historic", "habs": "historic", "europeana": "historic"}
SWISS_KEY = re.compile(r"^site(\d+)_bldg(\d+)_floor(\d+)$")


def make_id(key: str, slug: str | None = None) -> str:
    """The backend id of a store drawing (and of one storey of it). Fits tools.ID_RE (<= 100 chars)."""
    text = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode().lower()
    stem = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-") or "d"
    out = f"{PREFIX}{stem}-{hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:8]}"
    return f"{out}--{slug}" if slug else out


def patterns_of(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def excluded(key: str, patterns: list[str]) -> bool:
    return any(key == p or fnmatch.fnmatchcase(key, p) for p in patterns)


# ---- licences: data/<source>/LICENSE.md of the dataset the store was filled from ------------------


class Licences:
    """Per-file title, licence, credit and source link from the dataset's LICENSE.md files (a table
    `| file | title | licence | credit | source |`, or one `All files: <SPDX>. Credit: ...` line).
    Read once per LICENSE.md mtime; a missing dataset just means the generic credit."""

    def __init__(self, dataset_dir: Path | None):
        self.root = Path(dataset_dir) / "data" if dataset_dir else None
        self._by_source: dict[str, tuple[float, dict]] = {}

    def _source(self, source: str) -> dict:
        if self.root is None or "/" in source or source.startswith("."):
            return {}
        p = self.root / source / "LICENSE.md"
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return {}
        hit = self._by_source.get(source)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        files: dict[str, dict] = {}
        links: dict[str, str] = {}
        everything: dict | None = None
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            line = line.strip()
            if line.startswith("|") and not line.startswith("|---"):
                cells = [c.strip() for c in line.strip("|").split(" | ")]
                if len(cells) >= 5 and cells[0] != "file":
                    files[cells[0]] = {"title": cells[1], "license": cells[2],
                                       "credit": " | ".join(cells[3:-1]), "url": cells[-1]}
            elif (m := re.match(r"^All files: (.+?)\. Credit: (.+)$", line)):
                everything = {"license": m.group(1).strip(), "credit": m.group(2).strip()}
            elif (m := re.match(r"^- (\S+): (https?://\S+)$", line)):
                links[m.group(1)] = m.group(2)
        data = {"files": files, "all": everything, "links": links}
        self._by_source[source] = (mtime, data)
        return data

    def lookup(self, path: str) -> dict | None:
        """{title?, license, credit, url?} for a store path `data/<source>/<file>`; None when unknown."""
        parts = (path or "").split("/", 2)
        if len(parts) != 3 or parts[0] != "data":
            return None
        data = self._source(parts[1])
        row = (data.get("files") or {}).get(parts[2]) or data.get("all")
        if not row:
            return None
        out = dict(row)
        if not re.match(r"https?://", out.get("url") or ""):
            out["url"] = (data.get("links") or {}).get(out.get("license") or "")
        return out


def credit_of(project: str, lic: dict | None) -> dict:
    if lic and lic.get("credit") and lic.get("license"):
        out = {"text": str(lic["credit"])[:600], "license": str(lic["license"])[:80]}
        if lic.get("url"):
            out["url"] = str(lic["url"])
        return out
    source, _, rest = project.partition("/")
    return {"text": f"Source: {SOURCE_NAMES.get(source, source)}" + (f" ({rest})" if rest else ""),
            "license": "LicenseRef-source-dataset", "license_name": "Licence of the source dataset"}


# ---- the listing (pure: rows from the source -> entries) -----------------------------------------


@dataclass
class Entry:
    id: str
    key: str
    slug: str | None          # the storey slug (a drawing with an IFC model), else None
    storey: str | None        # the storey name ('' = things assigned to no storey)
    project: str
    file_ids: tuple[str, ...]
    summary: dict             # the DrawingSummary served by GET /drawings
    siblings: int = 1         # storey views of the same model that are listed
    rank: tuple = field(default_factory=tuple)


def title_of(key: str, lic: dict | None) -> str:
    if lic and lic.get("title"):
        return str(lic["title"])[:160]
    if (m := SWISS_KEY.match(key)):
        return f"Site {m.group(1)} · Building {m.group(2)} · Floor {m.group(3)}"
    text = " ".join(re.sub(r"-(\d+)$", r" \1", key.replace("_", " ")).split())
    return (text[:1].upper() + text[1:])[:160] or key


def _n(n: int, one: str) -> str | None:
    return f"{n} {one}{'' if n == 1 else 's'}" if n else None


def _ext_of(formats: set[str]) -> str | None:
    """`source_ext` for the library's type filter (it has no DWG type: those stay under their kind)."""
    if "dwg" in formats and "dxf" not in formats:
        return None
    return next(("." + f for f in ("dxf", "ifc", "pdf", "png", "jpg", "svg") if f in formats), None)


def _kind_of(formats: set[str]) -> str:
    return "cad_dxf" if formats & {"dxf", "dwg"} else "ifc" if "ifc" in formats else "raster"


def build_entries(rows: list[dict], covered: set[str], patterns: list[str],
                  licences: Licences | None = None) -> list[Entry]:
    """The store's listing rows as library entries, in library order. Never: drawings a bundle already
    shows (`covered` drawing keys), excluded keys, unreadable files, drawings with nothing to show."""
    out: list[Entry] = []
    seen: set[str] = set()
    for r in rows:
        key = r["key"]
        project = str(r.get("project") or "")
        if (key in covered or excluded(key, patterns) or project.startswith("upload/") or not r.get("usable")
                or not r.get("files")):
            continue
        formats = {f["format"] for f in r["files"]}
        storeys = [s for s in (r.get("storeys") or [])]
        if storeys:  # a model: storeys with rooms or objects; a model that has none shows its outlines
            occupied = [s for s in storeys if s.get("n_rooms", 0) + s.get("n_items", 0) > 0]
            storeys = occupied or [s for s in storeys if s.get("n_shapes", 0) > 0]
        if not storeys and not r.get("has_image") and not r.get("has_cad"):
            continue  # nothing to show
        lic = next((x for x in (licences.lookup(f["path"]) for f in r["files"]) if x), None) if licences else None
        source = project.split("/", 1)[0]
        source_name = SOURCE_NAMES.get(source, source)
        if source == "opening_design" and "/" in project:
            source_name += " · " + project.split("/", 1)[1].replace("_", " ")
        title = title_of(key, lic)
        counts = r.get("counts") or {}
        fixtures = sum(counts.get(k, 0) for k in FIXTURE_KINDS)
        interpreted = sum(v for k, v in counts.items() if k != "apartment") > 0
        fmt = " + ".join(f.upper() for f in sorted(formats))
        ingested = r.get("ingested")
        version = f"?v={int(ingested)}" if ingested else ""
        base = {
            "title": title, "kind": _kind_of(formats), "status": "ready",
            "credit": credit_of(project, lic), "source": "library",
            "created_at": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ingested)) if ingested else None),
            "source_ext": _ext_of(formats),
            "origin": "ifc" if _kind_of(formats) == "ifc" else ORIGIN.get(source, "other"),
        }
        src_rank = SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER)
        file_ids = tuple(f["file_id"] for f in r["files"])
        if not storeys:
            did = make_id(key)
            what = [_n(counts.get("room", 0), "room"), _n(counts.get("door", 0), "door"),
                    _n(counts.get("window", 0), "window"), _n(fixtures, "fixture")]
            summ = base | {
                "id": did, "n_rooms": int(counts.get("room", 0)), "n_doors": int(counts.get("door", 0)),
                "thumbnail": (f"/drawings/{did}/thumb.png{version}" if r.get("has_image")
                              else f"/drawings/{did}/plan.svg{version}"),
                "subtitle": " · ".join(x for x in (source_name, fmt, r.get("scale"),
                                                   *(what if interpreted else ["not interpreted"])) if x)[:300],
            }
            out.append(Entry(did, key, None, None, project, file_ids, summ,
                             rank=(not interpreted, src_rank, title.lower(), 0)))
            continue
        by_height = sorted(storeys, key=lambda s: (s.get("elevation") is None, s.get("elevation") or 0.0,
                                                   s.get("name") or ""))
        group = make_id(key) if 2 <= len(storeys) <= MAX_FLOORS else None
        for st in storeys:
            did = make_id(key, st["slug"])
            name = st.get("name") or ""
            level = (f"level {round(st['elevation'], 2) + 0.0:+.2f} m"
                     if name and st.get("elevation") is not None else None)
            occupied = st.get("n_rooms", 0) + st.get("n_items", 0) > 0
            summ = base | {
                "id": did, "title": (f"{title} · {name}" if name else title)[:160],
                "n_rooms": int(st.get("n_rooms", 0)),
                "thumbnail": f"/drawings/{did}/thumb.png{version}",
                "subtitle": " · ".join(x for x in (
                    name or None, level, _n(st.get("n_rooms", 0), "room"), _n(st.get("n_items", 0), "object"),
                    None if occupied else "outlines only", source_name, fmt) if x)[:300],
                "project_id": group,
                "floor_label": (name or "Unassigned")[:100] if group else None,
                "floor_order": by_height.index(st) if group else None,
            }
            out.append(Entry(did, key, st["slug"], name, project, file_ids, summ, siblings=len(storeys),
                             rank=(not occupied, src_rank, title.lower(), by_height.index(st))))
    out.sort(key=lambda e: e.rank)
    good: list[Entry] = []
    for e in out:
        if e.id in seen or not tools.ID_RE.match(e.id):
            continue
        try:
            e.summary = DrawingSummary.model_validate(e.summary).model_dump(exclude_none=True)
        except ValueError:
            log.warning("skipped a store drawing with an invalid summary")
            continue
        seen.add(e.id)
        good.append(e)
    return good


DOC_VERSION = 2          # raised when the document of an unchanged drawing is built differently (cached ones are rebuilt)

# ---- the document (pure: what the source built -> our drawing.json) -------------------------------


def method_of(tables_per_entity: list[set[str]]) -> str | None:
    """How a subject was found, the weakest of its entities deciding. None = exact (CAD / IFC)."""
    worst = 0
    for t in tables_per_entity:
        if t & EXACT_TABLES:
            rank = 0
        elif t and t <= VECTOR_TABLES:
            rank = 1
        elif t and t <= VECTOR_TABLES | OCR_TABLES:
            rank = 2
        else:
            rank = 3  # a model detection, a pixel region, or unknown: never presented as measured
        worst = max(worst, rank)
    return (None, "pdf_vector", "ocr", "vision")[worst]


def to_drawing(entry: Entry, built: dict, signature: str) -> dict:
    """Our drawing file for a store drawing: `normalise` (the conversion of a store-indexed upload) over
    the store's documents, then our id, URLs, methods and the store reference the sql tool scopes by."""
    sources = {int(k): set(v) for k, v in (built.get("sources") or {}).items()}
    sdoc = built["doc"]
    doc = normalise({"contract_version": CONTRACT_VERSION, "id": entry.id}, sdoc, built["evidence"],
                    built["model"], {k: sorted(v) for k, v in sources.items()})
    methods: dict[str, str] = {}
    for subject, boxes in doc["evidence"].items():
        methods[subject] = method_of([sources.get(b.get("store_ref"), set()) for b in boxes]) or "cad_geometry"
    for r in doc["rooms"]:
        r.setdefault("name", "Room")
        r.setdefault("label_at", [50.0, 50.0])
        r.setdefault("points", [])
    if doc["rooms"]:
        methods["room"] = method_of([sources.get(r.get("store_ref"), set()) for r in doc["rooms"]]) or "cad_geometry"
    doc["evidence_method"] = methods

    def ours(url: Any) -> Any:  # the store's /drawings/<its id>/<name> under our id
        return f"/drawings/{entry.id}/{url.rsplit('/', 1)[-1]}" if isinstance(url, str) and "/" in url else url

    doc["images"] = {k: ours(v) for k, v in (sdoc.get("images") or {}).items()}
    doc["thumbnail"] = ours(sdoc.get("thumbnail")) or doc["images"].get("rebuilt")
    doc["status"] = "ready"
    doc["source"] = "library"
    if sdoc.get("printed_dimensions"):                 # dimensions printed on the sheet (q.dimensions): lengths are askable
        doc["printed_dimensions"] = int(sdoc["printed_dimensions"])
    # Layer 2 (q.rooms, q.doors ...) has no storey scope: on one storey of a multi-storey model a query
    # would count the whole model, so there the document's own index stays the owner.
    owner = "store" if entry.slug is None or entry.siblings <= 1 else "ours"
    doc["store"] = {"drawing_key": entry.key, "file_ids": list(entry.file_ids), "owner": owner,
                    "signature": signature, "doc": DOC_VERSION, "page": built.get("page"), "synthetic": bool(built.get("synthetic")),
                    **({"storey": entry.storey} if entry.slug is not None else {})}
    return apply_entry(doc, entry)


def apply_entry(doc: dict, entry: Entry) -> dict:
    """Title, credit, kind and grouping come from the listing (one place decides them)."""
    s = entry.summary
    for k in ("title", "subtitle", "credit", "kind", "created_at", "source_ext", "project_id", "floor_label",
              "floor_order"):
        if s.get(k) is not None:
            doc[k] = s[k]
        else:
            doc.pop(k, None)
    return doc


# ---- the loaded drawing ---------------------------------------------------------------------------


@dataclass
class StoreDrawing(tools.LoadedDrawing):
    """A library drawing served from the store. `dir` is its cache folder (drawing.json for the answer
    cache's data version; generated images), not a bundle: files come from `StoreLibrary.file_of`."""
    library: Any = field(default=None, repr=False, compare=False)
    entry: Any = field(default=None, repr=False, compare=False)
    signature: str = ""
    store_library = True  # read by steps._box_pad (boxes are the store's, not padded by our solvers)

    @property
    def store_owned(self) -> bool:
        """The store owns this drawing's rooms and items for the sql tool (Layer 2 views readable)."""
        return (self.drawing.get("store") or {}).get("owner") == "store"

    @property
    def sql_note(self) -> str:
        """A storey view: the file views (q.elements ...) hold the whole model, so the model is told."""
        st = self.drawing.get("store") or {}
        if "storey" not in st or self.entry is None or self.entry.siblings <= 1:
            return ""
        if st["storey"]:
            return (f"This drawing shows one storey of an IFC model: '{st['storey']}' (column storey of "
                    f"q.elements and q.ifc_elements = '{st['storey']}'). Restrict every query to it unless "
                    "the question asks about the whole building.\n\n")
        return ("This drawing shows the things of an IFC model that are assigned to no storey (column storey "
                "is null). Restrict every query to those unless the question asks about the whole building.\n\n")

    def _versioned(self, images):
        if not isinstance(images, dict):
            return images
        v = hashlib.sha1(self.signature.encode(), usedforsecurity=False).hexdigest()[:10]
        return {k: (f"{u}?v={v}" if isinstance(u, str) and u.startswith("/drawings/") and "?" not in u else u)
                for k, u in images.items()}

    def image_path(self) -> Path | None:
        """The page image, only for a drawing the store has not interpreted (image questions need it).
        Interpreted drawings answer from their index: no image check is run on the public library."""
        if self.drawing.get("evidence_method") or self.drawing.get("rooms") or self.library is None:
            return None
        page = (self.drawing.get("store") or {}).get("page")
        return self.library.render_path(page) if page else None

    def file(self, name: str) -> Path | None:
        return self.library.file_of(self, name) if self.library is not None else None


# ---- the library ----------------------------------------------------------------------------------


class StoreLibrary:
    """One per app (when AURA_STORE_LIBRARY is on and the store started). Thread-safe: requests reach it
    from the event loop and from worker threads."""

    def __init__(self, settings: Settings, source: Any = None, registry: Any = None):
        self.s = settings
        self.source = source if source is not None else JStoreSource()
        self.registry = registry
        self.dir = settings.store_library_dir
        self.patterns = patterns_of(settings.store_library_exclude)
        self.render_roots = [Path(settings.store_data_dir),
                             *(Path(p) for p in patterns_of(settings.store_render_dirs))]
        self.licences = Licences(Path(settings.store_data_dir) / "dataset")
        self.lock = threading.RLock()            # guards the dicts below; never held across the database
        self._refresh_lock = threading.Lock()    # one listing refresh at a time
        self._build_locks: dict[str, threading.Lock] = {}
        self._entries: dict[str, Entry] = {}
        self._summaries: list[dict] = []
        self._list_sig: str | None = None
        self._list_checked = 0.0
        self._docs: OrderedDict[str, StoreDrawing] = OrderedDict()
        self._doc_checked: dict[str, float] = {}
        self._file_ids: set[str] | None = None
        self._file_ids_at = 0.0

    # -- listing
    def covered_keys(self) -> tuple[set[str], str]:
        """Drawing keys the bundles already show (store-map.json beside the drawing dirs), and a
        signature of those files."""
        keys: set[str] = set()
        sig = []
        for build in self.s.drawing_dirs():
            for cand in (Path(build).parent / "store-map.json", Path(build) / "store-map.json"):
                try:
                    mtime = cand.stat().st_mtime
                    data = json.loads(cand.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                sig.append(f"{cand}:{mtime}")
                for v in data.values() if isinstance(data, dict) else []:
                    for f in (v.get("files") or []) if isinstance(v, dict) else []:
                        if isinstance(f, dict) and f.get("drawing_key"):
                            keys.add(str(f["drawing_key"]))
        return keys, "|".join(sig)

    def _refresh(self) -> None:
        """Re-read the listing when the store changed (checked at most every LIST_TTL_S). The database is
        asked without the state lock held, by one thread at a time; the others, and every caller on the
        event loop, keep the listing that is loaded (the app loads the first one at startup)."""
        if self._list_sig is not None and (time.monotonic() - self._list_checked < LIST_TTL_S or _on_loop()):
            return
        if _on_loop() and self._list_checked:
            return  # the store was down at the last try: a worker thread retries, never the event loop
        if not self._refresh_lock.acquire(blocking=self._list_sig is None):
            return  # another thread is refreshing: the loaded listing serves meanwhile
        try:
            if self._list_checked and time.monotonic() - self._list_checked < LIST_TTL_S:
                return
            try:
                covered, cover_sig = self.covered_keys()
                sig = f"{VERSION}|{self.source.signature()}|{cover_sig}|{','.join(self.patterns)}"
                entries = None if sig == self._list_sig else build_entries(
                    self.source.rows(), covered, self.patterns, self.licences)
            except Exception as e:  # noqa: BLE001 - Postgres down: the last listing stays (or none yet)
                log.warning("store library listing not refreshed: %s", type(e).__name__)
                self._list_checked = time.monotonic()
                return
            with self.lock:
                self._list_checked = time.monotonic()
                if entries is None:
                    return
                # an entry that did not change keeps its object, so its loaded drawing stays valid
                old = self._entries
                entries = [o if (o := old.get(e.id)) is not None and o == e else e for e in entries]
                self._entries = {e.id: e for e in entries}
                self._summaries = [e.summary for e in entries]
                self._list_sig = sig
                for did in [d for d in self._docs if d not in self._entries]:
                    self._docs.pop(did, None)
                    self._doc_checked.pop(did, None)
        finally:
            self._refresh_lock.release()

    def summaries(self) -> list[dict]:
        self._refresh()
        return self._summaries

    def entry(self, drawing_id: str) -> Entry | None:
        self._refresh()
        return self._entries.get(drawing_id)

    def count(self) -> int:
        return len(self.summaries())

    def projects(self) -> list[dict]:
        """The storeys of one IFC model as a building (ProjectBundle fields), like the IFC bundles."""
        self._refresh()
        groups: dict[str, list[Entry]] = {}
        for e in self._entries.values():
            if e.summary.get("project_id"):
                groups.setdefault(e.summary["project_id"], []).append(e)
        out = []
        for pid, members in groups.items():
            if len(members) < 2:
                continue
            floors = sorted(({"drawing_id": e.id, "label": e.summary["floor_label"],
                              "order": e.summary["floor_order"]} for e in members),
                            key=lambda f: (float(f["order"]), f["label"], f["drawing_id"]))
            title = str(members[0].summary["title"]).split(" · ")[0].strip() or pid
            # Storeys of one model, not a guess from names: auto_matched False (as for `ifc-<stem>--`).
            out.append({"id": pid, "title": title[:200], "floors": floors, "auto_matched": False})
        return out

    # -- retention
    def permanent_file_ids(self) -> set[str]:
        """Every dataset file of the store (all projects except `upload/%`): never deleted with an
        upload, even one with the same bytes. Raises when the store cannot be asked and nothing is
        cached, so a delete waits for the sweep instead of guessing. Call it off the event loop."""
        with self.lock:
            ids, at = self._file_ids, self._file_ids_at
        if ids is None or time.monotonic() - at >= FILE_IDS_TTL_S:
            try:
                ids = set(self.source.file_ids())
            except Exception:
                if ids is None:
                    raise
            else:
                with self.lock:
                    self._file_ids, self._file_ids_at = ids, time.monotonic()
        return set(ids)

    # -- documents
    def warm(self, drawing_id: str) -> None:
        """Build (or re-validate) a drawing and the other storeys of its building, in a worker thread:
        the registry lookups that follow on the event loop then only read the cache."""
        e = self.entry(drawing_id)
        if e is None:
            return
        self.get(drawing_id)
        group = e.summary.get("project_id")
        for other in [o.id for o in list(self._entries.values())
                      if group and o.summary.get("project_id") == group and o.id != drawing_id]:
            self.get(other)

    def _cached_doc(self, drawing_id: str, e: Entry, stale_ok: bool) -> StoreDrawing | None:
        with self.lock:
            d = self._docs.get(drawing_id)
            if d is None:
                return None
            fresh = d.entry is e and time.monotonic() - self._doc_checked.get(drawing_id, 0.0) < DOC_TTL_S
            if fresh or stale_ok:
                self._docs.move_to_end(drawing_id)
                return d
            return None

    def get(self, drawing_id: str, registry: Any = None) -> StoreDrawing | None:
        """The loaded drawing. On the event loop a loaded drawing is returned as it is (no database
        call; `warm` keeps it current from a worker thread). The state lock is never held while the
        database is asked or a document is built; one lock per drawing keeps a build from running twice."""
        e = self.entry(drawing_id)
        if e is None:
            with self.lock:
                self._docs.pop(drawing_id, None)
            return None
        on_loop = _on_loop()
        if (d := self._cached_doc(drawing_id, e, on_loop)) is not None:
            return d
        if on_loop:  # a path that did not warm it first: built here once, then cached
            log.info("store library: a drawing was built on the event loop")
        with self.lock:
            build_lock = self._build_locks.setdefault(drawing_id, threading.Lock())
        with build_lock:
            if (d := self._cached_doc(drawing_id, e, False)) is not None:  # built while we waited
                return d
            with self.lock:
                d = self._docs.get(drawing_id)
            try:
                sig = self.source.drawing_signature(e.key)
            except Exception as ex:  # noqa: BLE001 - Postgres down: what is loaded stays usable
                log.warning("store library: signature of a drawing not read: %s", type(ex).__name__)
                return d
            want = f"{VERSION}:{sig}"
            if sig is None:
                d = None
            elif d is None or d.signature != want or d.entry is not e:
                try:
                    d = self._load(e, want)
                except Exception:  # one unreadable drawing never breaks the rest
                    log.exception("store library: a drawing could not be built")
                    d = None
            with self.lock:
                if d is None:
                    self._docs.pop(drawing_id, None)
                    self._doc_checked.pop(drawing_id, None)
                    return None
                d.registry = registry or self.registry
                self._docs[drawing_id] = d
                self._docs.move_to_end(drawing_id)
                self._doc_checked[drawing_id] = time.monotonic()
                while len(self._docs) > MAX_DOCS:
                    old, _ = self._docs.popitem(last=False)
                    self._doc_checked.pop(old, None)
                return d

    def _load(self, e: Entry, want: str) -> StoreDrawing | None:
        folder = self.dir / e.id
        path = folder / "drawing.json"
        doc = None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            st = cached.get("store") or {}
            if st.get("signature") == want and st.get("doc") == DOC_VERSION and cached.get("id") == e.id:
                doc = apply_entry(cached, e)
        except (OSError, ValueError, AttributeError):
            doc = None
        if doc is None:
            built = self.source.build(e.key, e.slug)
            if built is None:
                return None
            doc = to_drawing(e, built, want)
            folder.mkdir(parents=True, exist_ok=True)
            _write(path, json.dumps(doc, ensure_ascii=False).encode())
            # Images carry the version in their name (file_of), so one written late for the previous
            # version is never served for this one; the old ones are removed here.
            keep = _image_tag(want)
            for old in folder.iterdir():
                if old.name != path.name and keep not in old.name:
                    with contextlib.suppress(OSError):
                        old.unlink()
        return StoreDrawing(id=e.id, dir=folder, drawing=doc, engine=tools.BundleEngine(doc), source="library",
                            library=self, entry=e, signature=want)

    # -- images
    def render_path(self, rel: str | None) -> Path | None:
        """A page render (`renders/<file_id>/pN.png`, relative to a store data dir) under the store data
        dir, then under the extra render roots. Never outside the root it was found in."""
        if not rel or Path(rel).is_absolute():
            return None
        for root in self.render_roots:
            try:
                base = root.resolve()
                p = (base / rel).resolve()
            except OSError:
                continue
            if p.is_file() and base in p.parents:
                return p
        return None

    def file_of(self, d: StoreDrawing, name: str) -> Path | None:
        st = d.drawing.get("store") or {}
        e: Entry = d.entry
        if d.dir is None or e is None:
            return None
        page = self.render_path(st.get("page"))
        synthetic = bool(st.get("synthetic"))
        tag = _image_tag(d.signature)
        try:
            if name == "original.png":
                if page is not None:
                    return page  # served as it is (a JPEG keeps its type: the response goes by the suffix)
                if synthetic:
                    return _cached(d.dir / f"original-{tag}.png", lambda: self.source.plan_png(e.key, e.slug, 2000))
                return None
            if name == "thumb.png":
                if page is not None:
                    return _cached(d.dir / f"thumb-{tag}.jpg", lambda: _thumb_jpeg(page))
                if synthetic:
                    return _cached(d.dir / f"thumb-{tag}.png", lambda: self.source.plan_png(e.key, e.slug, 640))
                return None
            if name == "plan.svg":
                urls = [*(d.drawing.get("images") or {}).values(), d.drawing.get("thumbnail")]
                if not any(isinstance(u, str) and "/plan.svg" in u for u in urls):
                    return None  # this drawing shows its page; no rebuilt plan to make
                return _cached(d.dir / f"plan-v5-{tag}.svg", lambda: _bytes(self.source.plan_svg(e.key, e.slug, page)))
        except Exception:  # a missing image is a 404, never a 500
            log.exception("store library: %s could not be made", name)
        return None


def _on_loop() -> bool:
    """True in a thread that runs an event loop (a request handler), False in a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _image_tag(signature: str) -> str:
    return "v" + hashlib.sha1(signature.encode(), usedforsecurity=False).hexdigest()[:10]


SVG_ROOT = re.compile(r"<svg\b[^>]*>")


def sized_svg(text: str) -> str:
    """The store's plan.svg has a viewBox but no width/height, so as an <img> it has no size of its own
    and the 3D view (which draws it to a canvas and a texture) gets noise. Give it the viewBox size,
    like the bundles' plans."""
    m = SVG_ROOT.search(text)
    if m is None or re.search(r'\s(width|height)="', m.group(0)):
        return text
    box = re.search(r'viewBox="([^"]+)"', m.group(0))
    try:
        _, _, w, h = (float(v) for v in re.split(r"[\s,]+", box.group(1).strip())) if box else (0, 0, 0, 0)
    except ValueError:
        return text
    if not (w > 0 and h > 0):
        return text
    return f'{text[:m.start()]}<svg width="{w:g}" height="{h:g}"{m.group(0)[4:]}{text[m.end():]}'


def _bytes(text: str | None) -> bytes | None:
    return sized_svg(text).encode("utf-8") if text else None


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _cached(path: Path, make) -> Path | None:
    if path.is_file():
        return path
    data = make()
    if not data:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, data)
    return path


def _thumb_jpeg(page: Path, side: int = 640) -> bytes:
    import io

    from PIL import Image

    with Image.open(page) as im:  # PIL's process-wide pixel limit is left as it is
        if im.size[0] * im.size[1] > 400_000_000:
            raise ValueError("page render too large for a thumbnail")
        im.draft("RGB", (side * 2, side * 2))
        im = im.convert("RGB")
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


# ---- the store (jstore, in-process) ---------------------------------------------------------------


class JStoreSource:
    """Everything the library reads from the store. Read-only: plain SELECTs and jstore's own
    `aura_model` (which sets session scope settings, never writes a table)."""

    def __init__(self, models: int = 64):
        self._models: Any = None
        self._size = models
        self._lock = threading.Lock()

    @staticmethod
    def _db():
        from jstore.db import DB
        return DB(timeout=DB_TIMEOUT_S)  # a stalled connection fails; it never holds a request for good

    def _am(self):
        from jstore.service import aura_model as am
        with self._lock:
            if self._models is None:
                self._models = am.Models(size=self._size)
        return am

    def signature(self) -> str:
        """Changes when files, entities, pages, frames or IFC shapes of the library change."""
        with self._db() as db:
            # Uploads are left out of every part: ingesting one never rebuilds the listing.
            r = db.one("""with lib as (select file_id, status, storey from jstore.source_file
                                       where project_id not like 'upload/%')
                          select (select count(*) || '/' || coalesce(max(entity_id), 0) from jstore.entity
                                    where project_id not like 'upload/%') as ents,
                                 (select md5(string_agg(file_id || status || coalesce(storey, ''), ',' order by file_id))
                                    from lib) as files,
                                 (select count(*) from jstore.frame join lib using (file_id)) as frames,
                                 (select count(*) || '/' || count(image) from jstore.page join lib using (file_id)) as pages,
                                 (select count(*) from jstore.ifc_geometry join lib using (file_id)) as shapes""")
        return "/".join(str(v) for v in (r or {}).values())

    def file_ids(self) -> set[str]:
        with self._db() as db:
            return {r["file_id"] for r in
                    db.run("select file_id from jstore.source_file where project_id not like 'upload/%'")}

    def rows(self) -> list[dict]:
        am = self._am()
        with self._db() as db:
            rows = db.run("""
                select f.drawing_key as key, min(f.project_id) as project,
                       array_agg(f.file_id order by f.format, f.path) as file_ids,
                       array_agg(f.path order by f.format, f.path) as paths,
                       array_agg(f.format order by f.format, f.path) as formats,
                       bool_or(f.status in ('parsed', 'interpreted')) as usable,
                       max(f.meta->>'scale') as scale, extract(epoch from max(f.ingested_at)) as ingested,
                       bool_or(exists (select 1 from jstore.page p where p.file_id = f.file_id and p.image is not null)) as has_image,
                       bool_or(f.format in ('dxf', 'dwg')
                               and exists (select 1 from jstore.frame fr where fr.file_id = f.file_id)
                               and exists (select 1 from jstore.cad_entity c where c.file_id = f.file_id)) as has_cad
                from jstore.source_file f
                where f.drawing_key not in (select u.drawing_key from jstore.source_file u where u.project_id like 'upload/%')
                group by f.drawing_key order by f.drawing_key""")
            counts: dict[str, dict[str, int]] = {}
            for c in db.run("""select drawing_key, kind, count(*) as n from jstore.entity
                               where status = 'active' group by 1, 2"""):
                counts.setdefault(c["drawing_key"], {})[c["kind"]] = int(c["n"])
            storeys = am.storey_index(db) if any("ifc" in r["formats"] for r in rows) else {}
        out = []
        for r in rows:
            out.append({
                "key": r["key"], "project": r["project"], "usable": bool(r["usable"]), "scale": r["scale"],
                "ingested": float(r["ingested"]) if r["ingested"] is not None else None,
                "has_image": bool(r["has_image"]), "has_cad": bool(r["has_cad"]),
                "files": [{"file_id": i, "path": p, "format": f}
                          for i, p, f in zip(r["file_ids"], r["paths"], r["formats"], strict=True)],
                "counts": counts.get(r["key"], {}),
                "storeys": [{"slug": s.slug, "name": s.name, "elevation": s.elevation, "n_rooms": s.n_rooms,
                             "n_items": s.n_items, "n_shapes": s.n_shapes} for s in storeys.get(r["key"], [])],
            })
        return out

    def drawing_signature(self, key: str) -> str | None:
        am = self._am()
        with self._db() as db:
            sig = am.signature(db, key)
        return None if sig is None else hashlib.sha1(repr(sig).encode(), usedforsecurity=False).hexdigest()[:16]

    def _model(self, db, key: str, slug: str | None):
        self._am()
        m = self._models.get(db, key, slug)
        if m is None or m.upload or m.status != "ready":  # an upload is never a library drawing
            return None
        return m

    def build(self, key: str, slug: str | None) -> dict | None:
        am = self._am()
        with self._db() as db:
            m = self._model(db, key, slug)
            if m is None:
                return None
            sources: dict[int, list[str]] = {}
            for r in db.run("""select es.entity_id, es.ref_table from jstore.entity_source es
                               join jstore.entity e using (entity_id) where e.drawing_key = :k
                               group by 1, 2""", k=key):
                sources.setdefault(int(r["entity_id"]), []).append(r["ref_table"])
            return {"doc": am.drawing_doc(m), "evidence": am.evidence_doc(m), "model": am.model_doc(m),
                    "sources": sources, "page": m.base.image if m.base is not None else None,
                    "synthetic": bool(m.synthetic)}

    def plan_svg(self, key: str, slug: str | None, page: Path | None) -> str | None:
        import dataclasses

        am = self._am()
        with self._db() as db:
            m = self._model(db, key, slug)
            if m is None:
                return None
            if page is not None and m.base is not None and m.base.image:
                # aura_model embeds a scan from <its store dir>/<image>; an absolute path wins the join,
                # so a render found under another root is embedded too.
                m = dataclasses.replace(m, base=dataclasses.replace(m.base, image=str(page)))
            return am.plan_svg(db, m)

    def plan_png(self, key: str, slug: str | None, side: int) -> bytes | None:
        am = self._am()
        with self._db() as db:
            m = self._model(db, key, slug)
        return am.plan_png(m, side) if m is not None else None
