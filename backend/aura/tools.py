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

from aura.contract import BOX_SUBJECTS, Box, EvidenceRoom, ToolResult
from aura.router import DrawingCtx

log = logging.getLogger("aura.tools")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
NOUN = {
    "window": ("window", "windows"),
    "door": ("door", "doors"),
    "washbasin": ("washbasin", "washbasins"),
    "wc": ("WC", "WCs"),
    "bathtub": ("bathtub", "bathtubs"),
    "shower": ("shower", "showers"),
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
    if k == "unit":
        return f"in apartment {scope['unit']}"
    if k == "room":
        return f"in room {scope['room_id']}"
    if k == "label":
        return f"in rooms labelled {scope['label']}"
    return f"inside {scope.get('label', 'the marked region')}"


def scope_text(scope: dict) -> str:
    k = scope.get("kind", "floor")
    return {
        "floor": "whole floor",
        "unit": f"apartment {scope.get('unit')}",
        "room": f"room {scope.get('room_id')}",
        "label": f"rooms labelled {scope.get('label')}",
        "region": str(scope.get("label", "marked region")),
    }[k]


def _rid(drawing_id: str, tool: str, args: dict) -> str:
    key = json.dumps([drawing_id, tool, args], sort_keys=True, ensure_ascii=False)
    return "r-" + hashlib.sha1(key.encode()).hexdigest()[:12]


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
            summary = f"{len(layer)} {noun(family, len(layer))} on the DXF {family} layer."
            if family == "door" and doubles:
                summary += f" {doubles} double doors are drawn as two leaves each, and each leaf counts."
            if scope.get("kind", "floor") != "floor":
                summary += f" {n} {where}."
            return {"answer": {"kind": "count", "value": n, "subject": family}, "value": n,
                    "numbers": [n, len(layer)], "evidence": {"boxes": boxes, "rooms": []},
                    "summary": summary}
        rooms = self._rooms(scope)
        explicit = scope.get("kind") in ("label", "room")
        if family in ("room", "apartment"):
            ev = [{"room_id": r["room_id"], "area_m2": r["area_m2"],
                   "included": family == "apartment" or explicit or not r.get("outdoor")}
                  for r in rooms]
            if family == "apartment":
                units = sorted({r["unit"] for r in rooms if r.get("unit")})
                n = len(units)
                summary = (f"{len(rooms)} numbered rooms; each carries an apartment letter: "
                           f"{', '.join(units)}.")
            else:
                n = sum(1 for e in ev if e["included"])
                out = sum(1 for r in rooms if r.get("outdoor"))
                summary = (f"{len(self.rooms)} numbered rooms on the drawing; {len(rooms)} are "
                           f"labelled {scope['label']}." if scope.get("kind") == "label" else
                           f"{len(rooms)} numbered rooms {where}"
                           + (f"; {out} outdoor left out." if out and not explicit else "."))
            return {"answer": {"kind": "count", "value": n, "subject": family}, "value": n,
                    "numbers": [n, len(rooms)], "evidence": {"boxes": [], "rooms": ev},
                    "summary": summary}
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
                ev.append(row)
                if inc:
                    parts.append(row["area_m2"])
            value = round(sum(parts), 2)
            n_out = len(ev) - len(parts)
            if scope.get("kind") == "room" and rooms:
                r0 = rooms[0]
                summary = f"Room {r0['room_id']} ({r0['name']}), net area {ev[0]['area_m2']:.2f} m²."
            else:
                summary = (f"Sum of {len(parts)} net room areas {where} = {value:.2f} m²"
                           + (f"; {n_out} outdoor left out." if n_out else "."))
            return {"answer": {"kind": "area", "value": value, "unit": "m2"}, "value": value,
                    "numbers": [value, *parts], "evidence": {"boxes": [], "rooms": ev},
                    "summary": summary}
        raise ValueError(f"unknown family {family!r}")

    def find_text(self, pattern: str) -> dict:
        rx = re.compile(pattern, re.IGNORECASE)
        hits = [t for t in self.texts if rx.search(t.get("text", ""))]
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

    @property
    def ctx(self) -> DrawingCtx:
        rooms = self.drawing.get("rooms", [])
        return DrawingCtx(
            labels=[r["name"] for r in rooms],
            room_ids=[r["room_id"] for r in rooms],
            units=[u["id"] for u in self.drawing.get("units", [])],
        )

    def public_drawing(self) -> dict:
        """Drawing as served: contract fields only (evidence/gold/texts stripped)."""
        out = {k: v for k, v in self.drawing.items()
               if k not in ("evidence", "gold", "texts", "anchors")}
        out.setdefault("capabilities", self.engine.capabilities())
        out.setdefault("kind", "cad_dxf")
        out.setdefault("source", self.source)
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

    def evict(self, drawing_id: str) -> None:
        self._cache.pop(drawing_id, None)

    def add(self, d: LoadedDrawing) -> None:
        self._cache[d.id] = d

    def library_ids(self) -> list[str]:
        """Library drawings only. Uploads are addressable by id but never listed: the
        unguessable id is their only access control. Every listing must use this."""
        seen = [k for k, v in self._cache.items() if v.source != "upload"]
        for base in self.dirs:
            index = base / "index.json"
            if index.is_file():  # the library order written by preprocess
                for row in json.loads(index.read_text()):
                    rid = row.get("id") if isinstance(row, dict) else None
                    if rid and rid not in seen and (base / rid / "drawing.json").is_file():
                        seen.append(rid)
            if base.is_dir():
                for p in sorted(base.iterdir()):
                    if (p / "drawing.json").is_file() and p.name not in seen:
                        seen.append(p.name)
        return [i for i in seen if not i.startswith("up-")]

    def get(self, drawing_id: str) -> LoadedDrawing | None:
        if drawing_id in self._cache:
            return self._cache[drawing_id]
        if not ID_RE.match(drawing_id):
            return None
        bases = [*self.dirs, *([self.uploads_dir] if self.uploads_dir else [])]
        for base in bases:
            p = base / drawing_id
            if (p / "drawing.json").is_file():
                d = load_dir(p)
                self._cache[drawing_id] = d
                return d
        return None


def load_dir(p: Path, drawing_id: str | None = None) -> LoadedDrawing:
    data = json.loads((p / "drawing.json").read_text())
    gold = data.get("gold") or []
    if not gold and (p / "gold.json").is_file():
        g = json.loads((p / "gold.json").read_text())
        gold = g.get("gold", g) if isinstance(g, dict) else g
    engine: Engine
    if (p / "model.json").is_file():
        from aura.solvers import swiss

        engine = SwissEngine(swiss.SwissModel.from_json(json.loads((p / "model.json").read_text())))
    else:
        engine = BundleEngine(data)
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
        summary=str(raw.get("summary", "")),
        method="cad_geometry",
        supported=supported,
        ms=ms,
        meta={"abstain": ans.get("reason")} if is_abstain else
             ({"texts": raw.get("texts", [])} if kind == "text" else {}),
    )


def count_objects(d: LoadedDrawing, subject: str, scope: dict | None = None) -> ToolResult:
    scope = scope or {"kind": "floor"}
    args = {"subject": subject, "scope": scope}
    t0 = time.perf_counter()
    if not d.engine.supports(subject):
        return ToolResult(result_id=_rid(d.id, "count_objects", args), tool="count_objects",
                          args=args, kind="none", subject=subject, supported=False,
                          summary=f"No {noun(subject, 2)} are extracted from this drawing yet, "
                                  "so there is nothing to count.")
    raw = d.engine.answer(subject, scope, None)
    return _to_result(d, "count_objects", args, raw, _ms(t0), "count", subject)


def room_areas(d: LoadedDrawing, scope: dict | None = None,
               overrides: list[dict] | None = None) -> ToolResult:
    scope = scope or {"kind": "floor"}
    tool = "what_if" if overrides else "room_areas"
    args: dict[str, Any] = {"scope": scope}
    if overrides:
        args["overrides"] = overrides
    t0 = time.perf_counter()
    raw = d.engine.answer("area", scope, overrides)
    return _to_result(d, tool, args, raw, _ms(t0), "area", None)


def what_if(d: LoadedDrawing, scope: dict | None, overrides: list[dict]) -> ToolResult:
    return room_areas(d, scope, overrides)


def list_rooms(d: LoadedDrawing, scope: dict | None = None) -> ToolResult:
    scope = scope or {"kind": "floor"}
    t0 = time.perf_counter()
    rows = d.engine.room_list(scope)
    rooms = [EvidenceRoom(room_id=r["room_id"], included=not r.get("outdoor", False),
                          area_m2=float(r.get("area_m2") or 0)) for r in rows]
    return ToolResult(
        result_id=_rid(d.id, "list_rooms", {"scope": scope}), tool="list_rooms",
        args={"scope": scope}, kind="rooms", rooms=rooms,
        numbers=[float(r.get("area_m2") or 0) for r in rows] + [float(len(rows))],
        summary=f"{len(rows)} rooms {where_en(scope)}: "
                + ", ".join(f"{r['room_id']} {r['name']}" for r in rows[:30]),
        ms=_ms(t0), meta={"rooms": rows},
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
        rooms=[EvidenceRoom(room_id=r["room_id"], included=True, area_m2=float(r.get("area_m2") or 0))
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


def find_text(d: LoadedDrawing, pattern: str, subject: str = "label") -> ToolResult:
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
        "description": "List rooms (id, printed label, apartment, area) in a scope.",
        "parameters": {"type": "object", "properties": {"scope": {"type": "string"}}},
    },
    {
        "name": "find_text",
        "description": "Find printed text on the drawing (e.g. fire ratings like EI30) by regex.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                       "required": ["pattern"]},
    },
    {
        "name": "final_answer",
        "description": "Finish: name the tool result that answers the question and explain briefly. "
                       "Never write a number that is not in a tool result.",
        "parameters": {
            "type": "object",
            "properties": {"result_id": {"type": "string"}, "explanation": {"type": "string"}},
            "required": ["result_id", "explanation"],
        },
    },
]


def parse_scope_arg(s: str | None) -> dict:
    """'floor' | 'unit:A' | 'room:3.04' | 'label:Bain' -> contract Scope."""
    if not s or s.strip().lower() in ("floor", "whole floor", ""):
        return {"kind": "floor"}
    k, _, v = s.partition(":")
    k, v = k.strip().lower(), v.strip()
    if k == "unit" and v:
        return {"kind": "unit", "unit": v.upper()[:1]}
    if k == "room" and v:
        return {"kind": "room", "room_id": v}
    if k == "label" and v:
        return {"kind": "label", "label": v}
    return {"kind": "floor"}
