"""The 9 tools over a loaded drawing, and the drawing registry.

A drawing is a folder `<DATA_DIR>/drawings/<id>/` or `backend/build/drawings/<id>/` holding
`drawing.json` (contract Drawing, optionally with `evidence`/`gold`/`texts`), and either
`model.json` (L1's SwissModel: answers come from `aura.solvers.swiss`) or the evidence in
`drawing.json` itself (the frontend's bundled file format: answers follow `local.ts`).

Every tool returns a ToolResult; no tool ever invents a number.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from aura.contract import (
    BOX_SUBJECTS,
    GROUP_COLORS,
    Box,
    EvidenceGroup,
    EvidenceLink,
    EvidenceRoom,
    ToolResult,
)
from aura.router import LABEL_CONCEPTS, STORE_ROOM_CONCEPT, DrawingCtx

log = logging.getLogger("aura.tools")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
NOUN = {
    "window": ("window", "windows"),
    "door": ("door", "doors"),
    "washbasin": ("washbasin", "washbasins"),
    "wc": ("WC", "WCs"),
    "bathtub": ("bathtub", "bathtubs"),
    "shower": ("shower", "showers"),
    "bedroom": ("bedroom", "bedrooms"),
    "kitchen": ("kitchen", "kitchens"),
    "bathroom": ("bathroom", "bathrooms"),
    "wc_room": ("separate WC room", "separate WC rooms"),
    "sauna": ("sauna", "saunas"),
    "sink": ("sink or washbasin", "sinks and washbasins"),
    "shower_or_bathtub": ("shower or bathtub", "showers and bathtubs"),
    "storey": ("storey", "storeys"),
    "balcony": ("balcony", "balconies"),
    "room": ("room", "rooms"),
    "apartment": ("apartment", "apartments"),
}


def noun(subject: str, n: float) -> str:
    one, many = NOUN.get(subject, (subject, subject + "s"))
    return one if n == 1 else many


def where_en(scope: dict) -> str:
    k = scope.get("kind", "floor")
    if k == "floor":
        return "on this floor plan"
    if k == "model":
        return "in the whole building model"
    if k == "project":
        return "across the project floors"
    if k == "unit":
        return f"in apartment {scope['unit']}"
    if k == "room":
        return f"in room {scope['room_id']}"
    if k == "label":
        return f"in rooms labelled {scope['label']}"
    return f"inside {scope.get('label', 'the marked region')}"


def scope_text(scope: dict) -> str:
    k = scope.get("kind", "floor")
    if k == "project":
        n = len(scope.get("drawing_ids") or [])
        return f"project ({n} floor{'s' if n != 1 else ''})"
    return {
        "floor": "whole floor",
        "model": "whole building model",
        "unit": f"apartment {scope.get('unit')}",
        "room": f"room {scope.get('room_id')}",
        "label": f"rooms labelled {scope.get('label')}",
        "region": str(scope.get("label", "marked region")),
    }[k]


def _area(r: dict) -> float | None:
    """A room's area, or None when it is unknown (never 0)."""
    a = r.get("area_m2")
    return None if a is None else float(a)


def _rid(drawing_id: str, tool: str, args: dict) -> str:
    key = json.dumps([drawing_id, tool, args], sort_keys=True, ensure_ascii=False)
    return "r-" + hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:12]


def _in_rect(x: float, y: float, r: dict) -> bool:
    return r["x"] <= x <= r["x"] + r["w"] and r["y"] <= y <= r["y"] + r["h"]


# ---- engines -------------------------------------------------------------------------------


class Engine(Protocol):
    """Answer source. Results use L1's dict shape: {answer, value, numbers, evidence, summary}."""

    def answer(self, family: str, scope: dict, overrides: list[dict] | None) -> dict: ...
    def find_text(self, pattern: str) -> dict: ...
    def room_list(self, scope: dict | None) -> list[dict]: ...
    def capabilities(self) -> list[str]: ...
    def supports(self, subject: str) -> bool: ...


class BundleEngine:
    """Answers from a DrawingFile (Drawing + evidence + gold), exactly like frontend local.ts."""

    def __init__(self, data: dict):
        self.d = data
        self.rooms: list[dict] = data.get("rooms", [])
        self.evidence: dict[str, list[dict]] = data.get("evidence", {}) or {}
        self.texts: list[dict] = data.get("texts", []) or []
        self.unit_ids = [u["id"] for u in data.get("units", [])]
        # Set on drawings materialised from the drawing store: how each subject was found.
        self.methods: dict[str, str] = data.get("evidence_method") or {}

    def _method(self, family: str) -> dict:
        """method/estimate fields for a result. An upload indexed by the store is never cad_geometry;
        a store-library subject is, when every entity behind it has a CAD or IFC source."""
        m = self.methods.get("room" if family in ("room", "apartment", "area") else family)
        if not m:
            return {}
        return {"method": m} | ({"estimate": True} if m == "vision" else {})

    def supports(self, subject: str) -> bool:
        if subject in BOX_SUBJECTS:
            return subject in self.evidence
        if subject == "apartment":
            return bool(self.unit_ids)
        return bool(self.rooms)  # room counts and areas

    def capabilities(self) -> list[str]:
        caps = [s for s in BOX_SUBJECTS if s in self.evidence]
        if self.rooms:
            caps.append("room")
        if self.unit_ids:
            caps.append("apartment")
        return caps

    def _exists(self, scope: dict) -> bool:
        k = scope.get("kind")
        if k == "unit":
            return scope.get("unit") in self.unit_ids
        if k == "room":
            return any(r["room_id"] == scope.get("room_id") for r in self.rooms)
        if k == "label":
            return any(r["name"] == scope.get("label") for r in self.rooms)
        return True

    def _rooms(self, scope: dict) -> list[dict]:
        k = scope.get("kind", "floor")
        if k == "floor":
            return list(self.rooms)
        if k == "unit":
            return [r for r in self.rooms if r.get("unit") == scope["unit"]]
        if k == "room":
            return [r for r in self.rooms if r["room_id"] == scope["room_id"]]
        if k == "label":
            return [r for r in self.rooms if r["name"] == scope["label"]]
        return [r for r in self.rooms if _in_rect(r["label_at"][0], r["label_at"][1], scope["rect"])]

    def _boxes(self, subject: str, scope: dict) -> list[dict]:
        allb = self.evidence.get(subject, [])
        k = scope.get("kind", "floor")
        if k == "floor":
            return list(allb)
        if k == "unit":
            return [b for b in allb if b.get("unit") == scope["unit"]]
        if k == "room":
            return [b for b in allb if b.get("room_id") == scope["room_id"]]
        if k == "label":
            ids = {r["room_id"] for r in self.rooms if r["name"] == scope["label"]}
            return [b for b in allb if b.get("room_id") in ids]
        rect = scope["rect"]
        return [b for b in allb if _in_rect(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, rect)]

    def answer(self, family: str, scope: dict, overrides: list[dict] | None) -> dict:
        ov = {o["room_id"]: o for o in overrides or []}
        if not self._exists(scope):
            return {"answer": {"kind": "abstain", "reason": "unclear_question"}, "value": None,
                    "numbers": [], "evidence": {"boxes": [], "rooms": []},
                    "summary": f"This drawing has no {scope_text(scope)}."}
        where = where_en(scope)
        if family in BOX_SUBJECTS:
            boxes = self._boxes(family, scope)
            n = len(boxes)
            layer = self.evidence.get(family, [])
            doubles = sum(1 for b in layer if b.get("note")) // 2
            how = self.methods.get(family)
            summary = (f"{len(layer)} {noun(family, len(layer))} on the DXF {family} layer." if not how else
                       f"{len(layer)} {noun(family, len(layer))} read from the file's own geometry by the "
                       "drawing store." if how == "cad_geometry" else
                       f"{len(layer)} {noun(family, len(layer))} found by the drawing store's symbol "
                       "detection (a model estimate)." if how == "vision" else
                       f"{len(layer)} {noun(family, len(layer))} read from the PDF's vectors.")
            if family == "door" and doubles:
                summary += (f" {doubles * 2} are drawn twice at the same spot in the CAD file; "
                            "the dataset counts both.")
            if scope.get("kind", "floor") != "floor":
                summary += f" {n} {where}."
            return {"answer": {"kind": "count", "value": n, "subject": family}, "value": n,
                    "numbers": [n, len(layer)], "evidence": {"boxes": boxes, "rooms": []},
                    "summary": summary} | self._method(family)
        rooms = self._rooms(scope)
        explicit = scope.get("kind") in ("label", "room")
        if family in ("room", "apartment"):
            ev = [{"room_id": r["room_id"], "area_m2": r["area_m2"],
                   "included": family == "apartment" or explicit or not r.get("outdoor")}
                  for r in rooms]
            groups: list[dict[str, Any]] = []
            if family == "apartment":
                units = sorted({r["unit"] for r in rooms if r.get("unit")})
                n = len(units)
                groups = [
                    {
                        "id": f"unit-{u}",
                        "label": f"Apartment {u}",
                        "color": GROUP_COLORS[i % len(GROUP_COLORS)],
                    }
                    for i, u in enumerate(units)
                ]
                for e, r in zip(ev, rooms, strict=False):
                    if r.get("unit"):
                        e["group_id"] = f"unit-{r['unit']}"
                summary = (f"{len(rooms)} numbered rooms; each carries an apartment letter: "
                           f"{', '.join(units)}.")
            else:
                n = sum(1 for e in ev if e["included"])
                out = sum(1 for r in rooms if r.get("outdoor"))
                summary = (f"{len(self.rooms)} numbered rooms on the drawing; {len(rooms)} are "
                           f"labelled {scope['label']}." if scope.get("kind") == "label" else
                           f"{len(rooms)} numbered rooms {where}"
                           + (f"; {out} outdoor left out." if out and not explicit else "."))
            ev_dict: dict[str, Any] = {"boxes": [], "rooms": ev}
            if groups:
                ev_dict["groups"] = groups
            return {"answer": {"kind": "count", "value": n, "subject": family}, "value": n,
                    "numbers": [n, len(rooms)], "evidence": ev_dict,
                    "summary": summary} | self._method(family)
        if family == "area":
            ev, parts = [], []
            for r in rooms:
                inc = explicit or not r.get("outdoor")
                row: dict[str, Any] = {"room_id": r["room_id"], "included": inc,
                                       "area_m2": r["area_m2"]}
                if r["room_id"] in ov:
                    o = ov[r["room_id"]]
                    row["area_m2"] = round(o["w_m"] * o["h_m"], 2)
                    row["edited"] = True
                if row["area_m2"] is None:  # unknown area: listed, never summed as 0
                    row["included"] = inc = False
                ev.append(row)
                if inc:
                    parts.append(row["area_m2"])
            value = round(sum(parts), 2)
            n_unknown = sum(1 for e in ev if e["area_m2"] is None)
            n_out = len(ev) - len(parts) - n_unknown
            if scope.get("kind") == "room" and rooms and ev[0]["area_m2"] is None:
                return {"answer": {"kind": "abstain", "reason": "unclear_question"},
                        "value": None, "numbers": [], "evidence": {"boxes": [], "rooms": ev},
                        "summary": f"Room {rooms[0]['room_id']} has no printed area."}
            if scope.get("kind") == "room" and rooms:
                r0 = rooms[0]
                summary = f"Room {r0['room_id']} ({r0['name']}), net area {ev[0]['area_m2']:.2f} m²."
                if r0.get("area_source") == "estimate" and not ev[0].get("edited"):
                    summary = (f"Room {r0['room_id']} ({r0['name']}), net area ≈ {ev[0]['area_m2']:.0f} m² (estimate: "
                               "the drawing states no scale, so the outline was measured under a scale estimated "
                               "from door and fixture sizes).")
            else:
                summary = (f"Sum of {len(parts)} net room areas {where} = {value:.2f} m²"
                           + (f"; {n_out} outdoor left out" if n_out else "")
                           + (f"; {n_unknown} without a printed area left out" if n_unknown else "")
                           + ".")
            est = sum(1 for r, e in zip(rooms, ev, strict=False)
                      if e["included"] and not e.get("edited") and r.get("area_source") == "estimate")
            if est and scope.get("kind") != "room":
                summary += (f" Approximate: {est} of these areas are estimates, measured under a scale estimated from "
                            "door and fixture sizes because the drawing states none.")
            return {"answer": {"kind": "area", "value": value, "unit": "m2"} | ({"approximate": True} if est else {}),
                    "value": value,
                    "numbers": [value, *parts], "evidence": {"boxes": [], "rooms": ev},
                    "summary": summary} | self._method(family)
        raise ValueError(f"unknown family {family!r}")

    def find_text(self, pattern: str) -> dict:
        rx = re.compile(pattern, re.IGNORECASE)
        hits = [t for t in self.texts if rx.search(t.get("text", "")[:500])]
        boxes = [{k: t[k] for k in ("id", "x", "y", "w", "h")} | {"subject": "label", "note": t["text"]}
                 for t in hits]
        return {
            "answer": ({"kind": "text", "value": hits[0]["text"], "subject": "label"} if hits
                       else {"kind": "abstain", "reason": "not_on_drawing"}),
            "value": hits[0]["text"] if hits else None,
            "numbers": [],
            "texts": [h["text"] for h in hits],
            "evidence": {"boxes": boxes, "rooms": []},
            "summary": (f"{len(hits)} printed texts match: " + "; ".join(h["text"] for h in hits[:5])
                        if hits else f"No printed text on this drawing matches /{pattern}/."),
        }

    def room_list(self, scope: dict | None) -> list[dict]:
        return [{"room_id": r["room_id"], "name": r["name"], "unit": r.get("unit"),
                 "area_m2": r["area_m2"], "outdoor": r.get("outdoor", False)}
                for r in self._rooms(scope or {"kind": "floor"})]


class RasterEngine:
    """Model-estimate door/window counts from raster.json (L1's solvers.raster), 0 calls."""

    def __init__(self, result: dict):
        from aura.solvers import raster

        self.raster = raster
        self.result = result

    def supports(self, subject: str) -> bool:
        return subject in self.raster.SUBJECTS

    def capabilities(self) -> list[str]:
        return list(self.raster.SUBJECTS)

    def answer(self, family: str, scope: dict, overrides: list[dict] | None) -> dict:
        return self.raster.answer(self.result, family, scope)

    def find_text(self, pattern: str) -> dict:
        return {"answer": {"kind": "abstain", "reason": "not_on_drawing"}, "value": None,
                "numbers": [], "texts": [], "evidence": {"boxes": [], "rooms": []},
                "summary": "Printed text is not read from uploaded images."}

    def room_list(self, scope: dict | None) -> list[dict]:
        return []


class IfcEngine:
    """L1's IFC solver over model.json (no ifcopenshell at serve time). Answers counts of the
    contract subjects it has and room areas, on the drawing's storey."""

    SUBJECTS = ("door", "window", "room")

    def __init__(self, model: Any):
        from aura.solvers import ifc

        self.ifc = ifc
        self.model = model

    def supports(self, subject: str) -> bool:
        if subject == "area":
            return bool(self.model.rooms)
        return subject in self.capabilities()

    def capabilities(self) -> list[str]:
        return [c for c in self.ifc.capabilities(self.model) if c in self.SUBJECTS]

    def answer(self, family: str, scope: dict, overrides: list[dict] | None) -> dict:
        if overrides:
            return {"answer": {"kind": "abstain", "reason": "not_supported_yet"}, "value": None,
                    "numbers": [], "evidence": {"boxes": [], "rooms": []},
                    "summary": "What-if sizes are not supported on IFC models."}
        return self.ifc.answer(self.model, family, scope)

    def find_text(self, pattern: str) -> dict:
        return {"answer": {"kind": "abstain", "reason": "not_on_drawing"}, "value": None,
                "numbers": [], "texts": [], "evidence": {"boxes": [], "rooms": []},
                "summary": "Printed text is not read from IFC models yet."}

    def room_list(self, scope: dict | None) -> list[dict]:
        st = self.model.storey
        return [{"room_id": r["room_id"], "name": r["name"], "unit": None,
                 "area_m2": None if r["area"] is None else round(r["area"], 2), "outdoor": False}
                for r in self.model.rooms if r["storey"] == st]


class SwissEngine:
    """L1's solver over model.json."""

    def __init__(self, model: Any):
        from aura.solvers import swiss

        self.swiss = swiss
        self.model = model

    def supports(self, subject: str) -> bool:
        if subject in ("room", "area"):
            return bool(self.model.rooms)
        return subject in self.capabilities() or subject == "apartment"

    def capabilities(self) -> list[str]:
        return list(self.swiss.capabilities(self.model))

    def answer(self, family: str, scope: dict, overrides: list[dict] | None) -> dict:
        return self.swiss.answer(self.model, family, scope, overrides)

    def find_text(self, pattern: str) -> dict:
        return self.swiss.find_text(self.model, pattern)

    def room_list(self, scope: dict | None) -> list[dict]:
        return self.swiss.room_list(self.model, scope)


# ---- loaded drawing --------------------------------------------------------------------------


@dataclass
class LoadedDrawing:
    id: str
    dir: Path | None
    drawing: dict  # contract Drawing (+ maybe extra sections)
    engine: Engine
    gold: list[dict] = field(default_factory=list)
    anchors: list[dict] = field(default_factory=list)
    source: str = "library"
    #: The registry this drawing was found in (set by Registry), so a whole-building answer can reach sibling floors.
    registry: Any = field(default=None, repr=False, compare=False)

    @property
    def ctx(self) -> DrawingCtx:
        rooms = self.drawing.get("rooms", [])
        return DrawingCtx(
            labels=[r["name"] for r in rooms],
            room_ids=[r["room_id"] for r in rooms],
            units=[u["id"] for u in self.drawing.get("units", [])],
            kind=self.drawing.get("kind", "cad_dxf"),
            printed_dimensions=int(self.drawing.get("printed_dimensions") or 0),
            label_concepts={r["name"]: STORE_ROOM_CONCEPT[r["room_type"]] for r in rooms
                            if r.get("room_type") in STORE_ROOM_CONCEPT},
        )

    def public_drawing(self) -> dict:
        """Drawing as served: contract fields only (evidence/gold/texts stripped)."""
        out = {k: v for k, v in self.drawing.items()
               if k not in ("evidence", "gold", "texts", "anchors", "detection", "dimensions")}
        out.setdefault("capabilities", self.engine.capabilities())
        out.setdefault("kind", "cad_dxf")
        out.setdefault("source", self.source)
        out["images"] = self._versioned(out.get("images"))
        if isinstance(out.get("thumbnail"), str):
            out["thumbnail"] = self._versioned({"t": out["thumbnail"]})["t"]
        return out

    def _versioned(self, images):
        """Image URLs that change when the file changes (`?v=<mtime>`): files are served with a public cache, so
        without this a re-imported plan.svg or original.png stays stale in browsers for up to an hour."""
        if not isinstance(images, dict) or self.dir is None:
            return images
        out = dict(images)
        for k, url in images.items():
            if not isinstance(url, str) or "?" in url:
                continue
            p = self.file(url.rsplit("/", 1)[-1])
            if p is not None:
                out[k] = f"{url}?v={int(p.stat().st_mtime)}"
        return out

    def image_path(self) -> Path | None:
        """The independent dataset image the check cuts tiles from."""
        if self.dir is None:
            return None
        for name in ("original.png", "original-crop.png"):
            p = self.dir / name
            if p.exists():
                return p
        return None

    def file(self, name: str) -> Path | None:
        if self.dir is None or "/" in name or name.startswith("."):
            return None
        p = self.dir / name
        return p if p.is_file() else None


class Registry:
    """Finds and caches drawings by id across the drawing dirs."""

    def __init__(self, dirs: list[Path], extra: dict[str, LoadedDrawing] | None = None,
                 uploads_dir: Path | None = None):
        self.dirs = dirs
        self.uploads_dir = uploads_dir  # looked up by id only, never listed
        self._cache: dict[str, LoadedDrawing] = dict(extra or {})
        self.skipped: set[str] = set()  # unreadable entries (count shown in /health)
        self._loaded_at: dict[str, float | None] = {}  # drawing.json mtime when each cached entry was read
        #: aura.store_library.StoreLibrary when AURA_STORE_LIBRARY is on: `st-…` ids are its drawings (built
        #: lazily, cached there). They are listed by GET /drawings, not by library_ids().
        self.store_library: Any = None

    def evict(self, drawing_id: str) -> None:
        self._cache.pop(drawing_id, None)

    def add(self, d: LoadedDrawing) -> None:
        d.registry = self
        self._cache[d.id] = d

    def library_ids(self) -> list[str]:
        """Library drawings only. Uploads are addressable by id but never listed: the
        unguessable id is their only access control. Every listing must use this."""
        seen = [k for k, v in self._cache.items() if v.source != "upload"]
        for base in self.dirs:
            index = base / "index.json"
            try:
                rows = json.loads(index.read_text()) if index.is_file() else []
            except ValueError:
                rows = []
            if isinstance(rows, list):  # the library order written by preprocess
                for row in rows:
                    rid = row.get("id") if isinstance(row, dict) else None
                    if rid and rid not in seen and (base / rid / "drawing.json").is_file():
                        seen.append(rid)
            if base.is_dir():
                for p in sorted(base.iterdir()):
                    if (p / "drawing.json").is_file() and p.name not in seen:
                        seen.append(p.name)
        return [i for i in seen if not i.startswith("up-")]

    def get(self, drawing_id: str) -> LoadedDrawing | None:
        if self.store_library is not None and drawing_id.startswith("st-"):
            return self.store_library.get(drawing_id, self)
        if drawing_id in self._cache:
            cached = self._cache[drawing_id]
            # A drawing re-imported on disk (scripts/import_building.py, preprocess) is read again, not served stale.
            if drawing_id not in self._loaded_at or self._loaded_at[drawing_id] == _mtime(cached.dir):
                return cached
            del self._cache[drawing_id]
        if not ID_RE.match(drawing_id):
            return None
        bases = [*self.dirs, *([self.uploads_dir] if self.uploads_dir else [])]
        for base in bases:
            p = base / drawing_id
            if (p / "drawing.json").is_file():
                try:
                    d = load_dir(p)
                except Exception as e:  # noqa: BLE001 - one bad entry never breaks the rest
                    if drawing_id not in self.skipped:
                        log.warning("skipped an unreadable drawing (%s)", type(e).__name__)
                    self.skipped.add(drawing_id)
                    return None
                self.skipped.discard(drawing_id)
                d.registry = self
                self._cache[drawing_id] = d
                self._loaded_at[drawing_id] = _mtime(p)
                return d
        return None


def data_version(d: LoadedDrawing, scope: dict | None = None) -> int:
    """Newest drawing.json time among this drawing and the floors an all-floors scope names (0 when unknown)."""
    ids = [d.id, *((scope or {}).get("drawing_ids") or [])] if (scope or {}).get("kind") == "project" else [d.id]
    newest = 0.0
    for i in ids:
        fd = d if i == d.id else (d.registry.get(i) if d.registry is not None else None)
        t = _mtime(fd.dir) if fd is not None and fd.dir is not None else None
        newest = max(newest, t or 0.0)
    return int(newest)


def _mtime(d: Path) -> float | None:
    try:
        return (d / "drawing.json").stat().st_mtime
    except OSError:
        return None


class UnreadableDrawing(Exception):
    pass


def load_dir(p: Path, drawing_id: str | None = None) -> LoadedDrawing:
    """Dispatch on the drawing's kind: raster.json → RasterEngine; kind 'ifc' → IfcEngine;
    a Swiss model.json → SwissEngine; evidence in drawing.json → BundleEngine.
    Anything else raises UnreadableDrawing."""
    data = json.loads((p / "drawing.json").read_text())
    if not isinstance(data, dict) or not isinstance(data.get("rooms", []), list):
        raise UnreadableDrawing("drawing.json is not a Drawing")
    gold = data.get("gold") or []
    if not gold and (p / "gold.json").is_file():
        g = json.loads((p / "gold.json").read_text())
        gold = g.get("gold", g) if isinstance(g, dict) else g
    kind = data.get("kind", "cad_dxf")
    engine: Engine
    if (p / "raster.json").is_file():
        engine = RasterEngine(json.loads((p / "raster.json").read_text()))
    elif kind == "ifc":
        from aura.solvers import ifc

        engine = IfcEngine(ifc.IfcModel.from_json(json.loads((p / "model.json").read_text())))
    elif (p / "model.json").is_file():
        if kind != "cad_dxf":
            raise UnreadableDrawing(f"unknown drawing kind {kind!r}")
        from aura.solvers import swiss

        m = json.loads((p / "model.json").read_text())
        if "schema" in m:
            raise UnreadableDrawing("model.json is not a Swiss model")
        engine = SwissEngine(swiss.SwissModel.from_json(m))
    elif kind in ("cad_dxf", "raster"):
        engine = BundleEngine(data)
    else:
        raise UnreadableDrawing(f"unknown drawing kind {kind!r}")
    return LoadedDrawing(id=drawing_id or data.get("id") or p.name, dir=p, drawing=data,
                         engine=engine, gold=list(gold), anchors=list(data.get("anchors") or []),
                         source=data.get("source", "library"))


def load_file(path: Path, assets_dir: Path | None = None) -> LoadedDrawing:
    data = json.loads(path.read_text())
    return LoadedDrawing(id=data["id"], dir=assets_dir, drawing=data, engine=BundleEngine(data),
                         gold=list(data.get("gold") or []), anchors=list(data.get("anchors") or []))


# ---- tools ---------------------------------------------------------------------------------


def _to_result(d: LoadedDrawing, tool: str, args: dict, raw: dict, ms: int,
               kind: str, subject: str | None, supported: bool = True) -> ToolResult:
    ans = raw.get("answer") or {}
    ev = raw.get("evidence") or {}
    text_subject = subject if kind == "text" else None
    boxes = [Box(**{k: v for k, v in b.items() if k in Box.model_fields}
                 | ({"subject": text_subject} if text_subject else {}))
             for b in ev.get("boxes", [])
             if b.get("subject") in NOUN or (text_subject and b.get("subject") == "label")]
    rooms = [EvidenceRoom(**{k: v for k, v in r.items() if k in EvidenceRoom.model_fields})
             for r in ev.get("rooms", [])]
    groups = [EvidenceGroup(**{k: v for k, v in g.items() if k in EvidenceGroup.model_fields})
              for g in ev.get("groups", []) or []]
    links = [EvidenceLink(**{k: v for k, v in lk.items() if k in EvidenceLink.model_fields})
             for lk in ev.get("links", []) or []]
    is_abstain = ans.get("kind") == "abstain"
    return ToolResult(
        result_id=_rid(d.id, tool, args),
        tool=tool,  # type: ignore[arg-type]
        args=args,
        kind="none" if is_abstain else kind,  # type: ignore[arg-type]
        subject=subject,
        value=None if is_abstain else raw.get("value") if not isinstance(raw.get("value"), str) else None,
        text=raw.get("value") if isinstance(raw.get("value"), str) else None,
        numbers=[float(x) for x in raw.get("numbers") or [] if isinstance(x, (int, float))],
        boxes=boxes,
        rooms=rooms,
        groups=groups,
        links=links,
        summary=str(raw.get("summary", "")),
        method=raw.get("method", "cad_geometry"),
        supported=supported,
        ms=ms,
        meta=({"abstain": ans.get("reason")} if is_abstain else
              ({"texts": raw.get("texts", [])} if kind == "text" else {}))
             | ({"estimate": True, "measured": raw.get("measured")} if raw.get("estimate") else {})
             | ({"rows": [{k: r[k] for k in ("id", "name", "value") if r.get(k) is not None}
                          for r in ev["rows"]]} if ev.get("rows") else {}),
    )


def _model_scope_unsupported(d: LoadedDrawing, tool: str, args: dict) -> ToolResult | None:
    """Only IFC models have a whole-building scope; a single plan never widens to it."""
    if (args.get("scope") or {}).get("kind") == "model" and not isinstance(d.engine, IfcEngine):
        return ToolResult(result_id=_rid(d.id, tool, args), tool=tool, args=args, kind="none",
                          supported=False, meta={"abstain": "not_supported_yet"},
                          summary="This drawing is one floor plan; it has no whole-building model.")
    return None


def measure(d: LoadedDrawing, family: str, scope: dict | None = None) -> ToolResult:
    """An IFC quantity (wall length, wall side area, wall/slab volume) given as the model has it."""
    scope = scope or {"kind": "floor"}
    args = {"family": family, "scope": scope}
    if not isinstance(d.engine, IfcEngine):
        return ToolResult(result_id=_rid(d.id, "room_areas", args), tool="room_areas", args=args,
                          kind="none", supported=False, meta={"abstain": "not_supported_yet"},
                          summary="Lengths and volumes are only read from IFC models.")
    t0 = time.perf_counter()
    raw = d.engine.answer(family, scope, None)
    tr = _to_result(d, "room_areas", args, raw, _ms(t0), "quantity", family)
    unit = (raw.get("answer") or {}).get("unit")
    return tr.model_copy(update={"meta": tr.meta | ({"unit": unit} if unit else {})})


def _element_name(subject: str) -> str:
    """What a model file calls the elements of a subject (its rooms are "space" elements)."""
    return "room (space)" if subject == "room" else subject


def count_objects(d: LoadedDrawing, subject: str, scope: dict | None = None) -> ToolResult:
    scope = scope or {"kind": "floor"}
    args = {"subject": subject, "scope": scope}
    if scope.get("kind") == "project":
        return _count_across_floors(d, subject, scope, args)
    if (bad := _model_scope_unsupported(d, "count_objects", args)) is not None:
        return bad
    t0 = time.perf_counter()
    if (not d.engine.supports(subject) and d.drawing.get("kind") == "ifc" and subject in ("door", "window", "room")
            and scope.get("kind") in ("floor", "model")):
        # A model file lists its elements: when it has none of this class, the count is 0, not unknown.
        where = "this storey of the model file" if scope.get("kind") == "floor" else "the model file"
        return ToolResult(result_id=_rid(d.id, "count_objects", args), tool="count_objects", args=args,
                          kind="count", subject=subject, value=0.0, numbers=[0.0], ms=_ms(t0),
                          summary=f"0 {noun(subject, 2)}: {where} contains no {_element_name(subject)} elements.",
                          meta={"none_in_model": True})
    if not d.engine.supports(subject):
        return ToolResult(result_id=_rid(d.id, "count_objects", args), tool="count_objects",
                          args=args, kind="none", subject=subject, supported=False,
                          summary=f"No {noun(subject, 2)} are extracted from this drawing yet, "
                                  "so there is nothing to count.")
    raw = d.engine.answer(subject, scope, None)
    if scope.get("kind") == "model":
        raw = _box_on_sibling_floors(d, subject, raw)
    return _to_result(d, "count_objects", args, raw, _ms(t0), "count", subject)


def _count_across_floors(d: LoadedDrawing, subject: str, scope: dict, args: dict) -> ToolResult:
    """An exact count over the floors of a building ("All N floors"): each floor drawing in the scope is counted
    on its own, the counts are summed, and every box and room is tagged with its floor. A floor that cannot be
    loaded or does not extract this subject is named in the summary and left out of the sum."""
    t0 = time.perf_counter()
    ids = [i for i in scope.get("drawing_ids") or [] if isinstance(i, str)] or [d.id]
    total = 0
    boxes: list[Box] = []
    rooms: list[EvidenceRoom] = []
    parts: list[str] = []
    missing: list[str] = []
    counted = 0
    none_in_model = True
    for fid in ids:
        fd = d if fid == d.id else (d.registry.get(fid) if d.registry is not None else None)
        if fd is None:
            missing.append(fid)
            continue
        tr = count_objects(fd, subject, {"kind": "floor"})
        if not tr.supported or tr.kind == "none" or tr.value is None:
            missing.append(fd.drawing.get("floor_label") or fd.drawing.get("title") or fid)
            continue
        counted += 1
        none_in_model &= bool(tr.meta.get("none_in_model"))
        n = int(tr.value)
        total += n
        tag = fid.split("--", 1)[-1] if "--" in fid else fid
        boxes += [b.model_copy(update={"id": f"{b.id}@{tag}", "drawing_id": fid}) for b in tr.boxes]
        rooms += [r.model_copy(update={"drawing_id": fid}) for r in tr.rooms]
        parts.append(f"{fd.drawing.get('floor_label') or fd.drawing.get('title') or fid}: {n}")
    if counted == 0:
        return ToolResult(result_id=_rid(d.id, "count_objects", args), tool="count_objects", args=args,
                          kind="none", subject=subject, supported=False, meta={"abstain": "not_supported_yet"},
                          summary=f"No floor of this building extracts {noun(subject, 2)}.", ms=_ms(t0))
    summary = f"{total} {noun(subject, total)} across {counted} floors ({'; '.join(parts)})."
    if missing:
        summary += f" Not counted: {', '.join(missing)}."
    if none_in_model and total == 0:
        summary = (f"0 {noun(subject, 2)}: the model file contains no {_element_name(subject)} elements on "
                   f"{'these ' + str(counted) + ' floors' if counted > 1 else 'this floor'}.")
    return ToolResult(result_id=_rid(d.id, "count_objects", args), tool="count_objects", args=args,
                      kind="count", subject=subject, value=float(total), numbers=[float(total)],
                      boxes=boxes, rooms=rooms, summary=summary, ms=_ms(t0),
                      meta={"floors_counted": counted, "floors_missing": missing}
                           | ({"none_in_model": True} if none_in_model and total == 0 else {}))


def _box_on_sibling_floors(d: LoadedDrawing, subject: str, raw: dict) -> dict:
    """A whole-building IFC count lists its elements by GlobalId without boxes (one drawing is one storey). When
    the building's other storeys are library drawings too (`ifc-<model>--<storey>`), each element is boxed on the
    storey drawing whose evidence holds it (matched by the GUID in the box note), tagged with that drawing's id.
    Elements no storey drawing boxes stay as rows. The count itself is unchanged."""
    ev = raw.get("evidence") or {}
    rows = ev.get("rows") or []
    if ev.get("boxes") or not rows or d.registry is None or "--" not in d.id:
        return raw
    stem = d.id.split("--", 1)[0]
    by_guid: dict[str, tuple[str, dict]] = {}
    for sid in d.registry.library_ids():
        if not sid.startswith(stem + "--"):
            continue
        sd = d.registry.get(sid)
        for b in ((sd.drawing.get("evidence") or {}).get(subject) or []) if sd is not None else []:
            m = re.search(r"\bIFC (\S+)$", b.get("note") or "")
            if m:
                by_guid[m.group(1)] = (sid, b)
    boxes, left = [], []
    for r in rows:
        hit = by_guid.get(str(r.get("id")))
        if hit is None:
            left.append(r)
            continue
        sid, b = hit
        # Box ids repeat per storey (win-01 on every floor): qualify them with the storey.
        boxes.append({**b, "id": f"{b['id']}@{sid.split('--', 1)[1]}", "drawing_id": sid})
    if not boxes:
        return raw
    floors = len({b["drawing_id"] for b in boxes})
    summary = raw.get("summary", "")
    summary += (f" {len(boxes)} of them boxed on their storey plans ({floors} floors)"
                + (f"; {len(left)} have no box." if left else "."))
    return {**raw, "evidence": {**ev, "boxes": boxes, "rows": left}, "summary": summary}


def count_rooms_labelled(d: LoadedDrawing, label: str, scope: dict | None = None) -> ToolResult:
    """Rooms with one printed label inside a scope (e.g. bedrooms in apartment A)."""
    scope = scope or {"kind": "floor"}
    args = {"subject": "room", "label": label, "scope": scope}
    if (bad := _model_scope_unsupported(d, "count_objects", args)) is not None:
        return bad
    t0 = time.perf_counter()
    rows = d.engine.room_list(scope)
    hits = [r for r in rows if r["name"].casefold() == label.casefold()]
    n = len(hits)
    names = sorted({r["name"] for r in rows})
    summary = (f"{n} rooms labelled {hits[0]['name'] if hits else label} {where_en(scope)}."
               if hits or label in names else
               f"No room is labelled {label} {where_en(scope)}; labels there: {', '.join(names)}.")
    return ToolResult(
        result_id=_rid(d.id, "count_objects", args), tool="count_objects", args=args,
        kind="count", subject="room", value=n, numbers=[float(n)],
        rooms=[EvidenceRoom(room_id=r["room_id"], included=True,
                            area_m2=_area(r)) for r in hits],
        summary=summary, ms=_ms(t0))


def room_areas(d: LoadedDrawing, scope: dict | None = None,
               overrides: list[dict] | None = None) -> ToolResult:
    scope = scope or {"kind": "floor"}
    tool = "what_if" if overrides else "room_areas"
    args: dict[str, Any] = {"scope": scope}
    if overrides:
        args["overrides"] = overrides
    if scope.get("kind") == "project":
        return _areas_across_floors(d, scope, overrides, tool, args)
    if (bad := _model_scope_unsupported(d, tool, args)) is not None:
        return bad
    t0 = time.perf_counter()
    raw = d.engine.answer("area", scope, overrides)
    return _to_result(d, tool, args, raw, _ms(t0), "area", None)


def _areas_across_floors(d: LoadedDrawing, scope: dict, overrides: list[dict] | None, tool: str,
                         args: dict) -> ToolResult:
    """The net floor area over the floors of a building or group ("All N floors"): each floor drawing in the scope
    is summed on its own (the same rule as on one floor), the sums are added, and every room is tagged with its
    floor. A floor that cannot be loaded or has no room areas is named in the summary and left out of the sum."""
    t0 = time.perf_counter()
    ids = list(dict.fromkeys(i for i in scope.get("drawing_ids") or [] if isinstance(i, str))) or [d.id]
    total, summed = 0.0, 0
    rooms: list[EvidenceRoom] = []
    parts: list[str] = []
    missing: list[str] = []
    methods: list[str] = []
    approx = False
    rank = {"cad_geometry": 0, "pdf_vector": 1, "ocr": 2, "vision": 3}
    for fid in ids:
        fd = d if fid == d.id else (d.registry.get(fid) if d.registry is not None else None)
        if fd is None:
            missing.append(fid)
            continue
        name = fd.drawing.get("floor_label") or fd.drawing.get("title") or fid
        tr = (room_areas(fd, {"kind": "floor"}, overrides if fid == d.id else None)
              if fd.engine.supports("area") else None)
        if tr is None or not tr.supported or tr.kind == "none" or tr.value is None:
            missing.append(name)
            continue
        summed += 1
        total += float(tr.value)
        methods.append(tr.method)
        estimates = {r.get("room_id") for r in fd.drawing.get("rooms", []) if r.get("area_source") == "estimate"}
        approx |= any(r.included and r.room_id in estimates for r in tr.rooms)
        rooms += [r.model_copy(update={"drawing_id": fid}) for r in tr.rooms]
        parts.append(f"{name}: {float(tr.value):.2f} m²")
    if summed == 0:
        return ToolResult(result_id=_rid(d.id, tool, args), tool=tool, args=args, kind="none", supported=False,
                          meta={"abstain": "not_supported_yet"},
                          summary="No floor in this scope has rooms with areas.", ms=_ms(t0))
    total = round(total, 2)
    summary = f"{total:.2f} m² across {summed} floor{'s' if summed != 1 else ''} ({'; '.join(parts)})."
    if missing:
        summary += f" Not included: {', '.join(missing)}."
    return ToolResult(result_id=_rid(d.id, tool, args), tool=tool, args=args, kind="area", value=total,
                      numbers=[total], rooms=rooms, summary=summary, ms=_ms(t0),
                      method=max(methods, key=lambda m: rank.get(m, 3)),
                      meta={"floors_counted": summed, "floors_missing": missing, "floor_sums": parts}
                           | ({"approximate": True} if approx else {}))


def _label_hits(name: str, printed: list[str]) -> bool:
    n = (name or "").casefold().strip()
    return any(n == p.casefold() or n.startswith(p.casefold() + "/") for p in printed)


def rank_rooms(d: LoadedDrawing, order: str, scope: dict | None = None,
               printed: list[str] | None = None, concept: str | None = None) -> ToolResult:
    """Stage 1 grab every indexed room with its area; stage 2 keep indoor rooms (of the room type,
    if one is named) and pick the largest (`order='max'`) or smallest. 0 model calls."""
    scope = scope or {"kind": "floor"}
    args: dict[str, Any] = {"scope": scope, "order": order, **({"room_type": concept} if concept else {})}
    if (bad := _model_scope_unsupported(d, "list_rooms", args)) is not None:
        return bad
    t0 = time.perf_counter()
    rows = d.engine.room_list(scope)
    kept: list[tuple[dict, float]] = []
    dropped: list[dict] = []
    for r in rows:
        a = _area(r)
        reason = ("no printed or outlined area" if a is None else
                  "outdoor space" if r.get("outdoor") else
                  f"not a {concept}" if printed and not _label_hits(r.get("name", ""), printed) else None)
        if reason:
            dropped.append({"id": r["room_id"], "name": r.get("name", ""), "reason": reason})
        else:
            kept.append((r, float(a)))  # type: ignore[arg-type]
    if not kept:
        return ToolResult(result_id=_rid(d.id, "list_rooms", args), tool="list_rooms", args=args,
                          kind="none", subject="room",
                          summary=f"No indoor {concept or 'room'} with an area {where_en(scope)}.",
                          ms=_ms(t0), meta={"grabbed": len(rows), "dropped": dropped})
    kept.sort(key=lambda x: x[1], reverse=(order == "max"))
    win, wa = kept[0]
    runner = kept[1] if len(kept) > 1 else None
    word = "largest" if order == "max" else "smallest"
    label = f"{win.get('name') or 'Room'} {win['room_id']}"
    tie = runner is not None and round(runner[1], 2) == round(wa, 2)
    summary = (f"Grabbed {len(rows)} rooms {where_en(scope)}; kept {len(kept)} indoor "
               f"{noun(concept, 2) if concept else 'rooms'} with an area. The {word} is {label}: {wa:.2f} m²"
               + (f" (next: {runner[0].get('name') or 'Room'} {runner[0]['room_id']}, {runner[1]:.2f} m²)"
                  if runner else "") + (" — tied." if tie else "."))
    return ToolResult(
        result_id=_rid(d.id, "list_rooms", args), tool="list_rooms", args=args, kind="area",
        subject="room", value=wa, numbers=[a for _, a in kept] + [float(len(rows))],
        rooms=[EvidenceRoom(room_id=r["room_id"], included=(r is win), area_m2=a) for r, a in kept],
        summary=summary, ms=_ms(t0),
        meta={"grabbed": len(rows), "winner": {"id": win["room_id"], "name": win.get("name", ""),
                                               "area_m2": wa},
              "runner_up": ({"id": runner[0]["room_id"], "name": runner[0].get("name", ""),
                             "area_m2": runner[1]} if runner else None),
              "kept": [r["room_id"] for r, _ in kept], "dropped": dropped, "order": order,
              "concept": concept, "tie": tie})


# A door's box spans its wall opening, so it touches the outline of the room on each side of the
# wall (a door on a shared wall belongs to both). Boxes merely near a room (wardrobe doors
# parallel to the wall, ~0.2 m away behind it) must not count, hence a small tolerance.
WALL_REACH_M = 0.08


def adjacent_boxes(d: LoadedDrawing, subject: str, room_ids: list[str]) -> ToolResult:
    """Doors/windows that open into any of `room_ids`: grab every box of the subject, keep the ones
    whose box touches the room outline (within WALL_REACH_M). A door on a wall shared by
    two rooms belongs to both. 0 model calls."""
    from shapely.geometry import Polygon
    from shapely.geometry import box as sbox

    args: dict[str, Any] = {"subject": subject, "scope": {"kind": "room", "room_id": room_ids[0]},
                            "room_ids": room_ids, "rule": "adjacent"}
    t0 = time.perf_counter()
    base = count_objects(d, subject, {"kind": "floor"})
    rooms = {r["room_id"]: r for r in d.drawing.get("rooms", [])}
    missing = [rid for rid in room_ids if rid not in rooms or len(rooms[rid].get("points") or []) < 3]
    if not base.supported or missing:
        return ToolResult(result_id=_rid(d.id, "count_objects", args), tool="count_objects", args=args,
                          kind="none", subject=subject, supported=base.supported,
                          summary=(f"No outline for room {', '.join(missing)}." if missing else base.summary),
                          ms=_ms(t0))
    mx = float(d.drawing.get("m_per_pct_x") or 0.1)
    my = float(d.drawing.get("m_per_pct_y") or mx)
    reach = WALL_REACH_M / max(1e-6, (mx + my) / 2)  # metres -> percent of the plan
    polys = {rid: Polygon(rooms[rid]["points"]).buffer(0) for rid in room_ids}
    kept: list[Box] = []
    dropped_n = 0
    for b in base.boxes:
        g = sbox(b.x, b.y, b.x + b.w, b.y + b.h).buffer(reach)
        hit = [rid for rid, poly in polys.items() if g.intersects(poly)]
        if hit:
            kept.append(b)
        else:
            dropped_n += 1
    names = ", ".join(f"{rooms[r].get('name') or 'Room'} {r}" for r in room_ids)
    n = len(kept)
    return ToolResult(
        result_id=_rid(d.id, "count_objects", args), tool="count_objects", args=args, kind="count",
        subject=subject, value=n, numbers=[float(n), float(len(base.boxes))], boxes=kept,
        summary=(f"Grabbed {len(base.boxes)} {noun(subject, 2)} on this floor; kept {n} that open into "
                 f"{names} (box within {WALL_REACH_M:.2f} m of the room outline), dropped {dropped_n}."),
        ms=_ms(t0),
        meta={"grabbed": len(base.boxes), "kept": [b.id for b in kept],
              "dropped": [b.id for b in base.boxes if b not in kept], "room_ids": room_ids,
              "reach_m": WALL_REACH_M})


def what_if(d: LoadedDrawing, scope: dict | None, overrides: list[dict]) -> ToolResult:
    return room_areas(d, scope, overrides)


def list_rooms(d: LoadedDrawing, scope: dict | None = None) -> ToolResult:
    scope = scope or {"kind": "floor"}
    if (bad := _model_scope_unsupported(d, "list_rooms", {"scope": scope})) is not None:
        return bad
    t0 = time.perf_counter()
    rows = d.engine.room_list(scope)
    rooms = [EvidenceRoom(room_id=r["room_id"], included=not r.get("outdoor", False),
                          area_m2=_area(r)) for r in rows]
    by_label: dict[str, int] = {}
    for r in rows:
        by_label[r["name"]] = by_label.get(r["name"], 0) + 1
    return ToolResult(
        result_id=_rid(d.id, "list_rooms", {"scope": scope}), tool="list_rooms",
        args={"scope": scope}, kind="rooms", rooms=rooms,
        numbers=[a for r in rows if (a := _area(r)) is not None] + [float(len(rows))]
                + [float(v) for v in by_label.values()],
        summary=f"{len(rows)} rooms {where_en(scope)}; by label: "
                + ", ".join(f"{k} {v}" for k, v in sorted(by_label.items())),
        ms=_ms(t0), meta={"rooms": rows, "by_label": by_label},
    )


def locate(d: LoadedDrawing, subject: str, scope: dict | None = None) -> ToolResult:
    """The boxes of a subject in a scope, without answering a count."""
    r = count_objects(d, subject, scope)
    return r.model_copy(update={"tool": "locate", "kind": "boxes" if r.supported else "none",
                                "result_id": _rid(d.id, "locate", r.args)})


def scope_to_region(d: LoadedDrawing, rect: dict, label: str = "Area 1") -> ToolResult:
    scope = {"kind": "region", "rect": rect, "label": label}
    rooms = d.engine.room_list(scope)
    return ToolResult(
        result_id=_rid(d.id, "scope_to_region", scope), tool="scope_to_region", args=scope,
        kind="rooms",
        rooms=[EvidenceRoom(room_id=r["room_id"], included=True, area_m2=_area(r))
               for r in rooms],
        summary=f"{len(rooms)} rooms have their label inside {label}.",
        meta={"scope": scope},
    )


def compare_with_original(d: LoadedDrawing) -> ToolResult:
    imgs = d.drawing.get("images", {})
    has = bool(imgs.get("original"))
    return ToolResult(
        result_id=_rid(d.id, "compare_with_original", {}), tool="compare_with_original", args={},
        kind="none",
        summary=("The original image is available for comparison; alignment is "
                 f"{imgs.get('original_alignment', 'unknown')}." if has else
                 "No original image to compare with."),
        meta={"images": imgs},
    )


MAX_LITERAL = 100


def find_text_literal(d: LoadedDrawing, text: str, subject: str = "label") -> ToolResult | None:
    """Case-insensitive substring search for model- or user-supplied text: escaped, never
    compiled as a pattern of its own."""
    lit = (text or "").strip()[:MAX_LITERAL]
    if not lit:
        return None
    return find_text(d, re.escape(lit), subject)


def find_text(d: LoadedDrawing, pattern: str, subject: str = "label") -> ToolResult:
    """`pattern` must be one of OUR fixed regexes (router.FIRE_PATTERN) or an escaped literal."""
    t0 = time.perf_counter()
    raw = d.engine.find_text(pattern)
    return _to_result(d, "find_text", {"pattern": pattern, "subject": subject}, raw, _ms(t0),
                      "text", subject)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# Function declarations for the agent (flash-lite function calling).
TOOL_DECLARATIONS: list[dict] = [
    {
        "name": "count_objects",
        "description": "Count drawn objects of one kind in a scope, from the CAD layers.",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "enum": list(BOX_SUBJECTS) + ["room", "apartment"]},
                "scope": {"type": "string",
                          "description": "floor | unit:<letter> | room:<id> | label:<printed label>"},
                "label": {"type": "string",
                          "description": "Only with subject 'room': count rooms with this printed "
                                         "label inside the scope (use a label from list_rooms)."},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "room_areas",
        "description": "Net floor area (m²) of a scope, summed room by room from printed areas.",
        "parameters": {
            "type": "object",
            "properties": {"scope": {"type": "string",
                                     "description": "floor | unit:<letter> | room:<id> | label:<label>"}},
        },
    },
    {
        "name": "list_rooms",
        "description": "List rooms (id, printed label, apartment, area) in a scope, with the "
                       "number of rooms per printed label.",
        "parameters": {"type": "object", "properties": {"scope": {"type": "string"}}},
    },
    {
        "name": "find_text",
        "description": "Find printed text on the drawing containing this literal text "
                       "(case-insensitive, not a regex), e.g. 'EI30'.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                       "required": ["pattern"]},
    },
    {
        "name": "final_answer",
        "description": "Finish: name the tool result that answers the question and explain briefly. "
                       "Never write a number that is not in a tool result.",
        "parameters": {
            "type": "object",
            "properties": {
                "result_id": {"type": "string"},
                "explanation": {"type": "string"},
                "label": {"type": "string",
                          "description": "Only when result_id names a list_rooms result: the "
                                         "printed room label whose count answers the question."},
            },
            "required": ["result_id", "explanation"],
        },
    },
]


UNIT_ALIASES = {"unit", "apartment", "flat", "apt", "appartement", "appartamento", "wohnung",
                "asunto", "dwelling"}


def parse_scope_arg(s: str | None) -> dict | None:
    """'floor' | 'unit:A' | 'room:3.04' | 'label:Bain' (| 'model') -> contract Scope.
    Anything else is None: the caller must reject it, never widen it to the whole floor."""
    if s is None or not str(s).strip() or str(s).strip().lower() in ("floor", "whole floor"):
        return {"kind": "floor"}
    s = str(s).strip()
    if s.lower() in ("model", "building", "whole building"):
        return {"kind": "model"}
    k, sep, v = s.partition(":")
    if not sep:
        k, _, v = s.partition(" ")
    k, v = k.strip().lower(), v.strip()
    if k in UNIT_ALIASES and re.fullmatch(r"[A-Za-z]", v):
        return {"kind": "unit", "unit": v.upper()}
    if k == "room" and v:
        return {"kind": "room", "room_id": v}
    if k == "label" and v:
        return {"kind": "label", "label": v}
    return None


_CATEGORY_LABELS: dict[str, str] = {
    "living": "Living & Dining",
    "bedroom": "Bedrooms",
    "kitchen": "Kitchen",
    "bathroom": "Bathrooms & WC",
    "outdoor": "Balcony & Terrace",
    "corridor": "Circulation & Hall",
    "storage": "Storage",
    "stairs": "Stairs",
}


def _room_category(name: str, outdoor: bool = False, store_type: str | None = None) -> tuple[str, str]:
    if outdoor:
        return "cat-outdoor", "Balcony & Terrace"
    raw = (name or "").strip()
    if re.search(r"\bwc\b|toilet|vessa|gabinett", raw, re.IGNORECASE):
        return "cat-bathroom", "Bathrooms & WC"
    for concept, rx, _printed in LABEL_CONCEPTS:
        if rx.search(raw):
            return f"cat-{concept}", _CATEGORY_LABELS.get(concept, concept.title())
    if store_type in STORE_ROOM_CONCEPT:               # an abbreviation the store's vocabulary knows ("MH", "OH")
        concept = STORE_ROOM_CONCEPT[store_type]
        return f"cat-{concept}", _CATEGORY_LABELS.get(concept, concept.title())
    if raw:
        slug = re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-")[:24] or "other"
        return f"cat-{slug}", raw
    return "cat-other", "Other Rooms"


def color_code_rooms(
    d: LoadedDrawing, rooms_ev: list[dict[str, Any]], *, by_apartment: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assign <2ms categorical groups (c1..c6) to room evidence by apartment or room category."""
    meta_by_id = {r["room_id"]: r for r in d.drawing.get("rooms", [])}
    out_rooms = [dict(r) for r in rooms_ev]
    groups_map: dict[str, str] = {}

    if by_apartment:
        for row in out_rooms:
            if not row.get("included", True):
                continue
            rm = meta_by_id.get(row["room_id"], {})
            u = rm.get("unit")
            if u:
                gid = f"unit-{u}"
                groups_map.setdefault(gid, f"Apartment {u}")
                row["group_id"] = gid
    if not groups_map:
        for row in out_rooms:
            if not row.get("included", True):
                continue
            rm = meta_by_id.get(row["room_id"], {})
            gid, glabel = _room_category(rm.get("name", ""), bool(rm.get("outdoor")), rm.get("room_type"))
            groups_map.setdefault(gid, glabel)
            row["group_id"] = gid
        # If every included room fell into a single category, color individual rooms distinctly
        if len(groups_map) <= 1 and sum(1 for r in out_rooms if r.get("included", True)) > 1:
            groups_map.clear()
            for row in out_rooms:
                if not row.get("included", True):
                    continue
                rm = meta_by_id.get(row["room_id"], {})
                rid = row["room_id"]
                gid = f"rm-{rid}"
                lbl = f"{rid} · {rm.get('name')}" if rm.get("name") else f"Room {rid}"
                groups_map[gid] = lbl[:60]
                row["group_id"] = gid

    groups = [
        {"id": gid, "label": glabel, "color": GROUP_COLORS[i % len(GROUP_COLORS)]}
        for i, (gid, glabel) in enumerate(groups_map.items())
    ]
    return out_rooms, groups
