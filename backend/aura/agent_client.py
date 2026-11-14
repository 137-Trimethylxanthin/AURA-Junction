"""HTTP/SSE bridge from AURA (`aura/pipeline.py`) to the isolated `agent-service` sidecar.

Handles:
- File upload & `sidecar_files.json` cache (plus optional `_precomputed/` zero-copy registration).
- Single-plan and multi-plan (`ScopeProject`) workspaces (`drawing.<ext>`, `drawing_2.<ext>`, ...).
- Concurrency cap (`Semaphore(2)` + bounded queue of 6 with live `progress` `queue_position` events).
- Weighted budget pre-flight (`slots=40`) and exact settlement via `Budget.record_external`.
- `<50 ms` `scan-candidates` live candidate sweep (`tool_call` + `tool_result` during `pending` ONLY;
  final `AskResponse.evidence.boxes` contains ONLY the agent's confirmed boxes, while eliminated
  candidate IDs are emitted in `dropped_ids` on the trace).
- Deictic `ScopeRegion` visual similarity (`"how many spots like this are there"`) vs spatial filter.
- Multi-turn `Prior conversation context` injection (last 3 turns + preceding turn rooms/boxes).
- Coordinate transforms across Swiss DXF (`cad_dxf`), IFC (`ifc`, margin already baked in), and
  Raster/PDF (`raster`), plus categorical color groups (`c1..c6`) and directional `Evidence.links`.
- Self-deadline (`deadline_s - 5s`) with explicit `POST /runs/{run_id}/cancel` in `finally:`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, get_args

import httpx

from aura import answer as A
from aura import tools
from aura.budget import Budget, BudgetError, Reservation
from aura.config import Settings

REPO = Path(__file__).resolve().parents[2]
from aura.contract import (
    BOX_SUBJECTS,
    GROUP_COLORS,
    Box,
    EvidenceGroup,
    EvidenceLink,
    EvidenceRoom,
    EvidenceRow,
    Subject,
)
from aura.gemini.client import GeminiClient, GeminiError, Meter
from aura.gemini.understand import pick_agent_subject
from aura.router import DEICTIC_SIMILARITY, route

log = logging.getLogger("aura.agent_client")

MAX_AGENT_SLOTS = 40
MAX_CONCURRENT_RUNS = 2
MAX_WAITING_RUNS = 6
HEALTH_TTL_S = 30.0
CROP_PATH_RE = re.compile(
    r"(?:^|/)crop_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)(?:[_.]|$)",
    re.IGNORECASE,
)

# Exact contract Subject & BoxSubject literals so Pydantic validation never fails on open-ended nouns
VALID_COUNT_SUBJECTS: frozenset[str] = frozenset(get_args(Subject))
VALID_BOX_SUBJECTS: frozenset[str] = frozenset({
    *VALID_COUNT_SUBJECTS, "label", "fire_rating",
})


def _clamp_pct(v: float) -> float:
    return round(min(100.0, max(0.0, float(v))), 2)


def cad_pt_to_pct(drawing: dict, x: float, y: float) -> list[float] | None:
    """Convert `[x, y]` in CAD/IFC world units to `[x_pct, y_pct]` in `[0, 100]`."""
    frame = drawing.get("frame")
    if not frame:
        return None
    dmin = frame.get("dxf_min") or frame.get("min")
    dmax = frame.get("dxf_max") or frame.get("max")
    if not dmin or not dmax or len(dmin) < 2 or len(dmax) < 2:
        return None
    # IFC frames already include ±MARGIN in dxf_min/dxf_max; Swiss DXF frames store raw extents + margin_m.
    margin = 0.0 if drawing.get("kind") == "ifc" else float(frame.get("margin_m") or 0.0)
    minx = float(dmin[0]) - margin
    maxx = float(dmax[0]) + margin
    miny = float(dmin[1]) - margin
    maxy = float(dmax[1]) + margin
    span_x = maxx - minx
    span_y = maxy - miny
    if span_x <= 0 or span_y <= 0:
        return None
    x_pct = (float(x) - minx) / span_x * 100.0
    y_flip = frame.get("y_flip", True)
    y_pct = ((maxy - float(y)) / span_y * 100.0) if y_flip else ((float(y) - miny) / span_y * 100.0)
    return [_clamp_pct(x_pct), _clamp_pct(y_pct)]


def cad_bbox_to_pct(drawing: dict, bbox: list[float] | tuple[float, ...], pad_m: float = 0.0) -> dict | None:
    """Convert `[xmin, ymin, xmax, ymax]` in CAD/IFC units to `{x, y, w, h}` in `Pct`."""
    if len(bbox) < 4:
        return None
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if pad_m > 0:
        x0 -= pad_m
        y0 -= pad_m
        x1 += pad_m
        y1 += pad_m
    # Top-left in screen space corresponds to (xmin, ymax) when y_flip is True.
    frame = drawing.get("frame") or {}
    y_flip = frame.get("y_flip", True)
    p_tl = cad_pt_to_pct(drawing, x0, y1 if y_flip else y0)
    p_br = cad_pt_to_pct(drawing, x1, y0 if y_flip else y1)
    if p_tl is None or p_br is None:
        return None
    left, right = min(p_tl[0], p_br[0]), max(p_tl[0], p_br[0])
    top, bottom = min(p_tl[1], p_br[1]), max(p_tl[1], p_br[1])
    w = round(right - left, 2)
    h = round(bottom - top, 2)
    # Point-like CAD entities (e.g. INSERT blocks with zero-area bbox) get a minimal clickable 1.2% box
    if w == 0.0 and x1 == x0:
        left = _clamp_pct(left - 0.6)
        w = round(min(100.0 - left, 1.2), 2)
    if h == 0.0 and y1 == y0:
        top = _clamp_pct(top - 0.6)
        h = round(min(100.0 - top, 1.2), 2)
    if w <= 0.0 or h <= 0.0:
        return None
    return {"x": left, "y": top, "w": w, "h": h}


def px_pt_to_pct(drawing: dict, x_px: float, y_px: float,
                 image_px: list[float] | tuple[float, float] | None = None) -> list[float] | None:
    """Convert `[x_px, y_px]` to `[x_pct, y_pct]`."""
    if image_px and len(image_px) >= 2 and float(image_px[0]) > 0 and float(image_px[1]) > 0:
        w_px, h_px = float(image_px[0]), float(image_px[1])
    else:
        px = (drawing.get("frame") or {}).get("px")
        if not px or len(px) < 2 or float(px[0]) <= 0 or float(px[1]) <= 0:
            return None
        w_px, h_px = float(px[0]), float(px[1])
    return [_clamp_pct(float(x_px) / w_px * 100.0), _clamp_pct(float(y_px) / h_px * 100.0)]


def px_bbox_to_pct(drawing: dict, bbox_px: list[float] | tuple[float, ...],
                   image_px: list[float] | tuple[float, float] | None = None) -> dict | None:
    """Convert `[x0, y0, x1, y1]` in full-image pixels to `{x, y, w, h}` in `Pct`."""
    if len(bbox_px) < 4:
        return None
    x0, y0, x1, y1 = (float(bbox_px[0]), float(bbox_px[1]), float(bbox_px[2]), float(bbox_px[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    p0 = px_pt_to_pct(drawing, x0, y0, image_px=image_px)
    p1 = px_pt_to_pct(drawing, x1, y1, image_px=image_px)
    if p0 is None or p1 is None:
        return None
    left, right = min(p0[0], p1[0]), max(p0[0], p1[0])
    top, bottom = min(p0[1], p1[1]), max(p0[1], p1[1])
    w = round(right - left, 2)
    h = round(bottom - top, 2)
    if w <= 0.0 or h <= 0.0:
        return None
    return {"x": left, "y": top, "w": w, "h": h}


def pct_rect_to_px(drawing: dict, rect: dict) -> list[int] | None:
    """Convert `{x, y, w, h}` in `Pct` to `[x0, y0, x1, y1]` in full-image pixels."""
    px = (drawing.get("frame") or {}).get("px")
    if not px or len(px) < 2 or float(px[0]) <= 0 or float(px[1]) <= 0:
        return None
    w_px, h_px = float(px[0]), float(px[1])
    x0 = round(float(rect["x"]) / 100.0 * w_px)
    y0 = round(float(rect["y"]) / 100.0 * h_px)
    x1 = round((float(rect["x"]) + float(rect["w"])) / 100.0 * w_px)
    y1 = round((float(rect["y"]) + float(rect["h"])) / 100.0 * h_px)
    return [max(0, x0), max(0, y0), min(int(w_px), max(x0 + 1, x1)), min(int(h_px), max(y0 + 1, y1))]


def pct_rect_to_cad(drawing: dict, rect: dict) -> list[float] | None:
    """Convert `{x, y, w, h}` in `Pct` to `[xmin, ymin, xmax, ymax]` in CAD/IFC world units."""
    if drawing.get("kind") not in ("cad_dxf", "ifc"):
        return None
    frame = drawing.get("frame")
    if not frame:
        return None
    dmin = frame.get("dxf_min") or frame.get("min")
    dmax = frame.get("dxf_max") or frame.get("max")
    if not dmin or not dmax or len(dmin) < 2 or len(dmax) < 2:
        return None
    margin = 0.0 if drawing.get("kind") == "ifc" else float(frame.get("margin_m") or 0.0)
    minx = float(dmin[0]) - margin
    maxx = float(dmax[0]) + margin
    miny = float(dmin[1]) - margin
    maxy = float(dmax[1]) + margin
    span_x = maxx - minx
    span_y = maxy - miny
    if span_x <= 0 or span_y <= 0:
        return None
    x0 = minx + float(rect["x"]) / 100.0 * span_x
    x1 = minx + (float(rect["x"]) + float(rect["w"])) / 100.0 * span_x
    if frame.get("y_flip", True):
        y1 = maxy - float(rect["y"]) / 100.0 * span_y
        y0 = maxy - (float(rect["y"]) + float(rect["h"])) / 100.0 * span_y
    else:
        y0 = miny + float(rect["y"]) / 100.0 * span_y
        y1 = miny + (float(rect["y"]) + float(rect["h"])) / 100.0 * span_y
    return [round(min(x0, x1), 3), round(min(y0, y1), 3), round(max(x0, x1), 3), round(max(y0, y1), 3)]


def _boxes_overlap(a: dict, b: dict) -> bool:
    """True when candidate box `a` and confirmed box `b` refer to the same physical feature."""
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 > ix0 and iy1 > iy0:
        inter = (ix1 - ix0) * (iy1 - iy0)
        area_a = max(0.01, a["w"] * a["h"])
        area_b = max(0.01, b["w"] * b["h"])
        if inter / min(area_a, area_b) >= 0.25:
            return True
    acx, acy = (ax0 + ax1) / 2.0, (ay0 + ay1) / 2.0
    bcx, bcy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
    return abs(acx - bcx) <= 2.2 and abs(acy - bcy) <= 2.2


def format_prior_context(history: list[dict] | None, d: tools.LoadedDrawing | None = None) -> str:
    """Build a structured `Prior conversation context` block from up to the last 3 turns."""
    if not history:
        return ""
    recent = [t for t in history if t.get("message")][-3:]
    if not recent:
        return ""
    dr_rooms = {r["room_id"]: r for r in ((d.drawing.get("rooms") or []) if d is not None else [])}
    lines = ["Prior conversation context (oldest to newest):"]
    for idx, t in enumerate(recent, 1):
        q = str(t.get("message") or "").strip()
        resp = t.get("response") or {}
        ans = resp.get("answer") or {}
        akind = ans.get("kind") or "none"
        summary_bits = [f"kind={akind}"]
        if "value" in ans:
            summary_bits.append(f"value={ans['value']}")
        if ans.get("unit"):
            summary_bits.append(f"unit={ans['unit']}")
        if ans.get("subject"):
            summary_bits.append(f"subject={ans['subject']}")
        if ans.get("number") is not None:
            summary_bits.append(f"number={ans['number']}")
        lines.append(f"- Turn {idx} user: {q!r} -> assistant ({', '.join(summary_bits)})")

    last_resp = (recent[-1].get("response") or {})
    ev = last_resp.get("evidence") or {}
    rooms = [r for r in (ev.get("rooms") or []) if r.get("included", True)]
    boxes = ev.get("boxes") or []
    if rooms:
        room_items = []
        for r in rooms[:80]:
            rmeta = dr_rooms.get(r["room_id"], {})
            rname = r.get("name") or rmeta.get("name") or ""
            at = r.get("label_at") or rmeta.get("label_at")
            pos = f", pos_pct=[{at[0]}, {at[1]}]" if isinstance(at, list) and len(at) >= 2 else ""
            area = f"{r['area_m2']} m2" if r.get("area_m2") is not None else "unknown area"
            grp = f", group={r['group_id']}" if r.get("group_id") else ""
            room_items.append(f"{r['room_id']} ({rname}: {area}{pos}{grp})")
        lines.append("Active rooms from immediately preceding turn (y_pct=0 is plan top/North, y_pct=100 is plan bottom/South): "
                     + "; ".join(room_items))
    elif boxes:
        box_items = []
        for b in boxes[:80]:
            cx = round(float(b["x"]) + float(b["w"]) / 2.0, 1)
            cy = round(float(b["y"]) + float(b["h"]) / 2.0, 1)
            note = f", note={b['note']!r}" if b.get("note") else ""
            box_items.append(f"{b['id']} ({b.get('subject', 'label')} at pct=[{cx}, {cy}]{note})")
        lines.append("Active items from immediately preceding turn (y_pct=0 is plan top/North, y_pct=100 is plan bottom/South): "
                     + "; ".join(box_items))
    return "\n".join(lines)


def lexical_subject_of(question: str) -> str | None:
    """Fast deterministic subject extraction when the prompt clearly names one contract Subject."""
    q = question.lower()
    checks: tuple[tuple[str, str], ...] = (
        ("apartment", r"\b(apartments?|wohnung(?:en)?|appartements?|asunto\w*|huoneisto\w*|units?|flats?|dwellings?)\b"),
        ("window", r"\b(windows?|fenster\w*|fen[êe]tres?|ikkun\w*)\b"),
        ("door", r"\b(doors?|t[üu]r(?:en)?|portes?|ov(?:i|et|ia)\w*)\b"),
        ("stair", r"\b(stairs?|staircases?|stairways?|treppen?\w*|escaliers?|portaat|portaikko\w*)\b"),
        ("washbasin", r"\b(sinks?|washbasins?|basins?|lavabos?|waschbecken\w*|pesuallas\w*)\b"),
        ("wc", r"\b(toilets?|wcs?|water closets?)\b"),
        ("bathtub", r"\b(bathtubs?|baths?|badewannen?|baignoires?|kylpyamme\w*)\b"),
        ("shower", r"\b(showers?|duschen?|douches?|suihku\w*)\b"),
        ("column", r"\b(columns?|pillars?|st[üu]tzen?|poteaux?|pilari\w*)\b"),
        ("beam", r"\b(beams?|tr[äa]ger\w*|poutres?|palkki\w*)\b"),
        ("wall", r"\b(walls?|w[äa]nde\w*|murs?|sein[äa]\w*)\b"),
        ("room", r"\b(rooms?|spaces?|bedrooms?|bathrooms?|kitchens?|zimmer\w*|r[äa]ume\w*|pi[èe]ces?|chambres?|huone\w*)\b"),
    )
    hits = [subj for subj, pat in checks if re.search(pat, q)]
    if len(hits) == 1:
        return hits[0]
    return hits[0] if hits else None


class AgentClient:
    """Async client bridging AURA turns to `agent-service`."""

    def __init__(
        self,
        settings: Settings,
        budget: Budget,
        client: GeminiClient | None,
        registry: tools.Registry,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.budget = budget
        self.client = client
        self.registry = registry
        base_url = (settings.sidecar_url or "http://127.0.0.1:8090").rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0),
        )
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        self._waiting = 0
        self._health_cache: tuple[float, bool] | None = None
        self._file_lock = asyncio.Lock()
        self._sha_cache: dict[tuple[str, int, int], str] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        tf = self.settings.sidecar_token_file
        if tf:
            p = Path(tf).expanduser()
            try:
                if p.is_file():
                    tok = p.read_text(encoding="utf-8").strip()
                    if tok:
                        return {"Authorization": f"Bearer {tok}"}
            except OSError:
                pass
        return {}

    async def health_ok(self) -> bool:
        """Probe `GET /health` with a 1s timeout, cached for 30s so `/health` never stalls."""
        now = time.monotonic()
        if self._health_cache is not None and now - self._health_cache[0] < HEALTH_TTL_S:
            return self._health_cache[1]
        ok = False
        try:
            r = await self._http.get("/health", timeout=1.0)
            ok = r.status_code == 200 and bool(r.json().get("ok", True))
        except Exception:  # noqa: BLE001 - any probe failure just means "not healthy"
            ok = False
        self._health_cache = (now, ok)
        return ok

    # ---- Modular file resolution & ingestion hooks ----

    def _sidecar_cache_path(self) -> Path:
        return self.settings.data_dir / "sidecar_files.json"

    def _load_file_map(self) -> dict[str, dict[str, str]]:
        p = self._sidecar_cache_path()
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_file_map(self, mapping: dict[str, dict[str, str]]) -> None:
        p = self._sidecar_cache_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            log.warning("could not persist sidecar_files.json")

    def _sha256(self, path: Path) -> str:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key in self._sha_cache:
            return self._sha_cache[key]
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        digest = h.hexdigest()
        self._sha_cache[key] = digest
        return digest

    def resolve_drawing_source(self, d: tools.LoadedDrawing) -> tuple[Path | None, Path | None]:
        """Locate `(source_file_path, precomputed_dir_or_None)` for an upload or library drawing."""
        pre_dir: Path | None = None
        if d.dir is not None:
            cand_pre = d.dir / "_precomputed"
            if cand_pre.is_dir():
                pre_dir = cand_pre
            # 1. Explicit source.<ext> inside the drawing folder (all uploads + per-floor split PDFs)
            exts = []
            if d.drawing.get("source_ext"):
                exts.append(str(d.drawing["source_ext"]))
            exts.extend([".pdf", ".dxf", ".ifc", ".png", ".jpg", ".jpeg", ".svg"])
            for ext in dict.fromkeys(exts):
                p = d.dir / f"source{ext if ext.startswith('.') else '.' + ext}"
                if p.is_file():
                    return p, pre_dir

        # 2. Library IFC: find original .ifc under dataset roots
        dataset_roots = [
            self.settings.data_dir / "junction-dataset" / "data",
            REPO / "data" / "junction-dataset" / "data",
        ]
        if d.drawing.get("kind") == "ifc" and d.dir is not None:
            mp = d.dir / "model.json"
            if mp.is_file():
                try:
                    src_name = json.loads(mp.read_text(encoding="utf-8")).get("source")
                except (OSError, ValueError):
                    src_name = None
                if src_name:
                    for root in dataset_roots:
                        ifc_root = root / "ifc_models"
                        if ifc_root.is_dir():
                            matches = list(ifc_root.rglob(src_name))
                            if matches:
                                return matches[0], pre_dir

        # 3. Library Swiss DXF (`swiss-<floor>`): find `*_floor<floor>.dxf`
        if d.id.startswith("swiss-"):
            floor_id = d.id.removeprefix("swiss-")
            for root in dataset_roots:
                sw_root = root / "swiss_dwellings"
                if sw_root.is_dir():
                    for pat in (f"*_floor{floor_id}.dxf", f"*_{floor_id}.dxf", f"*{floor_id}*.dxf"):
                        matches = sorted(sw_root.glob(pat))
                        if matches:
                            return matches[0], pre_dir

        # 4. Fallback to original.png or plan.svg (e.g. raster library or unit-test fixture drawings)
        if d.dir is not None:
            for fallback in ("original.png", "plan.svg"):
                fp = d.file(fallback)
                if fp is not None and fp.is_file():
                    return fp, pre_dir
        return None, pre_dir

    async def register_ingested_file(
        self,
        drawing_id: str,
        src_path: Path,
        precomputed_dir: Path | None = None,
    ) -> str:
        """Explicit ingestion hook for precomputed workspaces (`POST /files/register` with `/files` fallback)."""
        async with self._file_lock:
            return await self._upload_or_register_locked(drawing_id, src_path, precomputed_dir, force=True)

    async def evict_drawing(self, drawing_id: str) -> None:
        """Remove cached sidecar `file_id` (and best-effort `DELETE /files/{file_id}`) when an upload is deleted."""
        async with self._file_lock:
            mapping = self._load_file_map()
            entry = mapping.pop(drawing_id, None)
            if entry is not None:
                self._save_file_map(mapping)
        if entry and entry.get("file_id"):
            with contextlib.suppress(httpx.HTTPError):
                await self._http.delete(f"/files/{entry['file_id']}", headers=self._headers(), timeout=5.0)

    async def _ensure_file_id(self, d: tools.LoadedDrawing, *, force: bool = False) -> str:
        src_path, pre_dir = await asyncio.to_thread(self.resolve_drawing_source, d)
        if src_path is None or not src_path.is_file():
            raise GeminiError(f"No source file available for drawing '{d.id}'.")
        async with self._file_lock:
            return await self._upload_or_register_locked(d.id, src_path, pre_dir, force=force)

    async def _upload_or_register_locked(
        self,
        drawing_id: str,
        src_path: Path,
        pre_dir: Path | None,
        *,
        force: bool = False,
    ) -> str:
        sha = await asyncio.to_thread(self._sha256, src_path)
        pre_key = str(pre_dir.resolve()) if pre_dir and pre_dir.is_dir() else ""
        mapping = self._load_file_map()
        cached = mapping.get(drawing_id)
        if (
            not force
            and cached
            and cached.get("sha256") == sha
            and cached.get("precomputed", "") == pre_key
            and cached.get("file_id")
        ):
            return cached["file_id"]

        headers = self._headers()
        fid: str | None = None
        if pre_key:
            try:
                reg = await self._http.post(
                    "/files/register",
                    headers=headers,
                    json={"path": str(src_path.resolve()), "precomputed_dir": pre_key},
                )
                if reg.status_code == 200:
                    fid = reg.json()["file_id"]
            except httpx.HTTPError:
                fid = None

        if fid is None:
            data = await asyncio.to_thread(src_path.read_bytes)
            up = await self._http.post(
                "/files",
                headers=headers,
                files={"file": (src_path.name, data, "application/octet-stream")},
            )
            if up.status_code != 200:
                raise GeminiError(f"Sidecar file upload failed (HTTP {up.status_code}).")
            fid = up.json()["file_id"]

        mapping[drawing_id] = {"file_id": fid, "sha256": sha, "precomputed": pre_key}
        self._save_file_map(mapping)
        return fid

    # ---- Prompt assembly ----

    def _build_injected_question(
        self,
        question: str,
        d: tools.LoadedDrawing,
        scope: dict,
        *,
        is_reference_region: bool,
        history: list[dict] | None,
        project_drawings: list[tuple[str, tools.LoadedDrawing]] | None,
    ) -> str:
        parts: list[str] = []

        # 1. Multi-plan project manifest when ScopeProject is active
        if project_drawings:
            manifest = []
            for ws_name, pd in project_drawings:
                pdr = pd.drawing
                manifest.append({
                    "workspace_file": ws_name,
                    "drawing_id": pd.id,
                    "title": pdr.get("title", pd.id),
                    "floor_label": pdr.get("floor_label"),
                    "floor_order": pdr.get("floor_order"),
                    "kind": pdr.get("kind"),
                    "px": (pdr.get("frame") or {}).get("px"),
                    "room_count": len(pdr.get("rooms") or []),
                })
            parts.append(
                "Multi-plan project context (set `file` on every evidence item to its `workspace_file`, "
                "and set `target_file` when a stair/shaft/route leads to another floor):\n"
                + json.dumps(manifest, ensure_ascii=False)
            )
        elif d.drawing.get("kind") == "ifc" and scope.get("kind") not in ("model", "project"):
            # Single-storey IFC scope instruction
            storey = scope.get("storey")
            if not storey and d.dir is not None and (d.dir / "model.json").is_file():
                try:
                    storey = json.loads((d.dir / "model.json").read_text(encoding="utf-8")).get("storey")
                except (OSError, ValueError):
                    storey = None
            if storey:
                parts.append(f"Only the storey '{storey}'.")

        # 2. Pre-indexed rooms summary when available on single-plan CAD/IFC so threshold/filter
        #    queries ("only show things with a 30m² area or higher", "rooms in apt A > 15m²")
        #    have exact printed room IDs, names, units, areas & positions even without _precomputed/.
        raw_rooms = d.drawing.get("rooms") or []
        if raw_rooms and not project_drawings:
            compact_rooms = [
                {
                    "id": rm.get("room_id"),
                    "name": rm.get("name"),
                    **({"unit": rm["unit"]} if rm.get("unit") else {}),
                    "area_m2": rm.get("area_m2"),
                    **({"outdoor": True} if rm.get("outdoor") else {}),
                    **({"pos_pct": rm["label_at"]} if rm.get("label_at") else {}),
                }
                for rm in raw_rooms[:80]
            ]
            parts.append(
                "Pre-indexed rooms on this floor plan (pos_pct=[x%, y%] from top-left; return "
                '{"kind": "room", "id": "<room_id>"} in `evidence` ONLY for rooms matching the '
                "user's criteria so strictly those rooms are highlighted):\n"
                + json.dumps(compact_rooms, ensure_ascii=False)
            )

        # 3. Prior conversation context for multi-turn follow-ups
        prior_block = format_prior_context(history, d)
        if prior_block:
            parts.append(prior_block)

        # 3. Scope expansion: deictic visual reference template vs spatial region vs unit/room/label
        skind = scope.get("kind")
        if skind == "region" and scope.get("rect"):
            rect = scope["rect"]
            px_box = pct_rect_to_px(d.drawing, rect)
            cad_box = pct_rect_to_cad(d.drawing, rect)
            coord_bits = [f"pct_rect={json.dumps(rect)}"]
            if px_box:
                coord_bits.append(f"pixel_bbox={px_box} (in full-plan image pixels [x0, y0, x1, y1])")
            if cad_box:
                coord_bits.append(f"cad_bbox={cad_box} (in drawing CAD units [xmin, ymin, xmax, ymax])")
            coord_desc = ", ".join(coord_bits)
            if is_reference_region:
                crop_hint = (
                    f"crop_{px_box[0]}_{px_box[1]}_{px_box[2]}_{px_box[3]}.png"
                    if px_box else "crop_ref.png"
                )
                parts.append(
                    f"REFERENCE REGION VISUAL SEARCH (marked template at {coord_desc}):\n"
                    f"Step 1: Crop that exact marked reference region ({crop_hint}) and call `view_image` on it "
                    f"(and inspect any CAD block/layer/text/polygon inside those coordinates) to identify what "
                    f"architectural symbol, spot, fixture, or room feature the user marked.\n"
                    f"Step 2: Search the ENTIRE floor plan (NOT just inside the marked rectangle!) for ALL "
                    f"matching spots/instances across the whole plan (including the marked reference instance), "
                    f"and return the full-plan count and every matching instance's bbox in `evidence`."
                )
            else:
                parts.append(
                    f"SPATIAL REGION FILTER: Restrict counting and measurement strictly to items inside the "
                    f"marked region ({coord_desc})."
                )
        elif skind == "unit" and scope.get("unit"):
            parts.append(f"Restrict scope strictly to apartment / unit '{scope['unit']}'.")
        elif skind == "room" and scope.get("room_id"):
            rname = next(
                (r.get("name") for r in (d.drawing.get("rooms") or []) if r.get("room_id") == scope["room_id"]),
                None,
            )
            label_info = f" ({rname})" if rname else ""
            parts.append(f"Restrict scope strictly to room '{scope['room_id']}'{label_info}.")
        elif skind == "label" and scope.get("label"):
            parts.append(f"Restrict scope strictly to rooms labelled '{scope['label']}'.")

        parts.append(question)
        return "\n\n".join(parts)

    # ---- Live crop telemetry extraction ----

    def _focus_rect_from_event(
        self,
        ev: dict,
        default_d: tools.LoadedDrawing,
        by_ws_file: dict[str, tools.LoadedDrawing],
    ) -> tuple[dict | None, str | None]:
        """Extract `(PctRect, drawing_id)` from a `view_image` SSE event when crop coords exist."""
        target_d = default_d
        src_img = ev.get("source_image") or ev.get("path") or ""
        if isinstance(src_img, str):
            m_ws = re.search(r"drawing_(\d+)", src_img)
            if m_ws:
                idx = int(m_ws.group(1))
                ws_keys = list(by_ws_file.values())
                if 1 <= idx <= len(ws_keys):
                    target_d = ws_keys[idx - 1]
            elif src_img in by_ws_file:
                target_d = by_ws_file[src_img]

        crop_px = ev.get("crop_px")
        image_px = ev.get("image_px")
        if not crop_px and isinstance(ev.get("path"), str):
            m = CROP_PATH_RE.search(ev["path"].replace("\\", "/"))
            if m:
                crop_px = [float(g) for g in m.groups()]
        if isinstance(crop_px, list) and len(crop_px) >= 4:
            pct = px_bbox_to_pct(target_d.drawing, crop_px, image_px=image_px)
            if pct is not None:
                return pct, target_d.id
        return None, None

    # ---- Turn execution ----

    async def run_turn(
        self,
        d: tools.LoadedDrawing,
        question: str,
        lang: str,
        scope: dict,
        caller: Any,
        stats: dict,
        *,
        history: list[dict] | None = None,
        deadline_s: float = 120.0,
        intent_tag: str = "router · hybrid",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Stream live events `(event_name, payload)` and end with `("_built", A.Built)`.

        Yields `progress`, `tool_call`, and `tool_result` tuples in real time so `pipeline.run`
        can forward them directly over `/chat` SSE.
        """
        from aura.pipeline import PipelineError

        meter: Meter = stats["meter"]
        is_reference_region = (
            scope.get("kind") == "region"
            and bool(scope.get("rect"))
            and bool(DEICTIC_SIMILARITY.search(question))
        )
        response_scope: dict = {"kind": "floor"} if is_reference_region else dict(scope)

        # 1. Resolve target drawings (single drawing vs ScopeProject)
        project_drawings: list[tuple[str, tools.LoadedDrawing]] = []
        if scope.get("kind") == "project":
            dids = list(dict.fromkeys(scope.get("drawing_ids") or [d.id]))
            for idx, did in enumerate(dids, 1):
                pd = d if did == d.id else self.registry.get(did)
                if pd is None:
                    raise PipelineError("unknown_drawing", f"Unknown drawing '{did}'", 404)
                src_p, _ = await asyncio.to_thread(self.resolve_drawing_source, pd)
                ext = (src_p.suffix.lower().lstrip(".") if src_p else "png") or "png"
                ws_name = f"drawing.{ext}" if idx == 1 else f"drawing_{idx}.{ext}"
                project_drawings.append((ws_name, pd))

        # 2. Pre-flight weighted budget reservation (40 slots worst-case)
        try:
            res: Reservation = self.budget.reserve(
                "agent_run",
                "public",
                ip_hash=caller.ip_hash,
                presenter=caller.presenter,
                slots=MAX_AGENT_SLOTS,
            )
        except BudgetError as e:
            stats["no_cache"] = True
            code = "rate_limited" if e.code == "rate_limited" else "budget_exhausted"
            raise PipelineError(code, str(e), 429) from e

        settled = False
        run_id: str | None = None
        run_model = self.settings.sidecar_model  # replaced by the model `run_started` reports
        run_finished_cleanly = False

        # 3. Queue / semaphore gate (max 2 concurrent, max 6 waiting)
        if self._sem.locked():
            if self._waiting >= MAX_WAITING_RUNS:
                self.budget.release(res)
                settled = True
                raise PipelineError(
                    "rate_limited",
                    "The agent is busy, try again in a few minutes.",
                    429,
                )
            self._waiting += 1
            pos = self._waiting
            yield "progress", {"queue_position": pos}
            acquired = False
            try:
                await self._sem.acquire()
                acquired = True
            finally:
                self._waiting = max(0, self._waiting - 1)
                if not acquired and not settled:
                    self.budget.release(res)
                    settled = True
            yield "progress", {"queue_position": 0}
        else:
            await self._sem.acquire()

        yield "_ready", {"detected_lang": lang, "canonical_en": question, "route": "agent"}

        t_start = time.perf_counter()
        self_budget_s = max(1.0, deadline_s - 5.0)
        trace_nodes: list[dict] = []
        scan_boxes: list[Box] = []
        scan_subj: str | None = None

        try:
            # 4. Immediate (<50 ms) candidate sweep on single-plan CAD/IFC/Raster when applicable
            if scope.get("kind") != "project" and not history:
                r_scan = route(question, d.ctx)
                cand_subj = r_scan.subject if r_scan.subject in BOX_SUBJECTS else lexical_subject_of(question)
                if cand_subj in BOX_SUBJECTS and d.engine.supports(cand_subj):
                    scan_scope = {"kind": "floor"} if is_reference_region else (
                        scope if scope.get("kind") in ("floor", "unit", "room", "label", "region") else {"kind": "floor"}
                    )
                    scan_tr = await asyncio.to_thread(tools.locate, d, cand_subj, scan_scope)
                    if scan_tr.supported and scan_tr.boxes:
                        scan_subj = cand_subj
                        scan_boxes = list(scan_tr.boxes)
                        scan_args: dict[str, Any] = {
                            "step": f"Scanning {len(scan_boxes)} candidate {A.noun(cand_subj, len(scan_boxes))}",
                            "seq": 1,
                        }
                        if is_reference_region and scope.get("rect"):
                            scan_args["focus_rect"] = scope["rect"]
                        scan_node = {
                            "id": "scan-candidates",
                            "parent": "q",
                            "title": "Candidate scan",
                            "tag": "Agent · scan",
                            "body": f"Marked {len(scan_boxes)} candidate {A.noun(cand_subj, len(scan_boxes))} for verification.",
                            "kind": "exact",
                            "evidence_ids": [b.id for b in scan_boxes],
                            "tool": {
                                "name": "deep_agent",
                                "args": scan_args,
                                "result_id": scan_tr.result_id,
                                "ms": int(scan_tr.ms),
                            },
                        }
                        # Streamed live during `pending` ONLY; NOT added to final evidence.boxes!
                        yield "tool_call", {"node": scan_node}
                        yield "tool_result", {
                            "node_id": "scan-candidates",
                            "evidence": {
                                "boxes": [b.model_dump(exclude_none=True) for b in scan_boxes],
                                "rooms": [],
                            },
                            "summary": scan_tr.summary,
                        }
            elif is_reference_region and scope.get("rect"):
                # Emit immediate reference-region focus highlight even when no pre-indexed boxes exist
                ref_node = {
                    "id": "scan-reference",
                    "parent": "q",
                    "title": "Inspecting marked reference region",
                    "tag": "Agent · reference",
                    "body": "Inspecting the marked spot to find matching instances across the full floor plan.",
                    "kind": "model",
                    "tool": {
                        "name": "deep_agent",
                        "args": {
                            "step": "Inspecting marked reference spot",
                            "seq": 1,
                            "focus_rect": scope["rect"],
                        },
                        "result_id": "r-ref-region",
                        "ms": 0,
                    },
                }
                trace_nodes.append(ref_node)
                yield "tool_call", {"node": ref_node}
                yield "tool_result", {
                    "node_id": "scan-reference",
                    "evidence": {"boxes": [], "rooms": []},
                    "summary": "Reference region queued for visual inspection.",
                }

            # 5. Upload/resolve file_ids & start run on agent-service
            targets = project_drawings if project_drawings else [("drawing", d)]
            file_ids: list[str] = []
            for _ws_name, td in targets:
                file_ids.append(await self._ensure_file_id(td))

            injected_q = self._build_injected_question(
                question,
                d,
                scope,
                is_reference_region=is_reference_region,
                history=history,
                project_drawings=project_drawings if project_drawings else None,
            )

            headers = self._headers()
            post_run = await self._http.post(
                "/runs",
                headers=headers,
                json={"file_ids": file_ids, "questions": [injected_q]},
            )
            if post_run.status_code == 404:
                # Stale file_id after sidecar restart -> force re-upload once
                file_ids = []
                for _ws_name, td in targets:
                    file_ids.append(await self._ensure_file_id(td, force=True))
                post_run = await self._http.post(
                    "/runs",
                    headers=headers,
                    json={"file_ids": file_ids, "questions": [injected_q]},
                )
            if post_run.status_code not in (200, 202):
                raise GeminiError(f"Sidecar /runs returned HTTP {post_run.status_code}.")

            run_id = post_run.json()["run_id"]
            by_ws_file: dict[str, tools.LoadedDrawing] = {}
            if project_drawings:
                for ws_name, pd in project_drawings:
                    by_ws_file[ws_name] = pd
            else:
                src_p, _ = await asyncio.to_thread(self.resolve_drawing_source, d)
                ext = (src_p.suffix.lower().lstrip(".") if src_p else "png") or "png"
                by_ws_file[f"drawing.{ext}"] = d

            # 6. Stream `/runs/{run_id}/events` under self-deadline (`deadline_s - 5s`)
            answers_payload: list[dict] | None = None
            run_usage: dict[str, Any] = {}
            run_error: str | None = None
            pending_nodes: dict[str, dict] = {}
            step_seq = 1

            async with asyncio.timeout(max(0.5, self_budget_s - (time.perf_counter() - t_start))):
                async with self._http.stream(
                    "GET",
                    f"/runs/{run_id}/events",
                    headers=headers,
                    timeout=httpx.Timeout(connect=10.0, read=self_budget_s + 5.0, write=10.0, pool=10.0),
                ) as sse_resp:
                    if sse_resp.status_code != 200:
                        raise GeminiError(f"Sidecar events returned HTTP {sse_resp.status_code}.")
                    data_lines: list[str] = []
                    async for raw_line in sse_resp.aiter_lines():
                        if raw_line == "":
                            if not data_lines:
                                continue
                            raw_json = "\n".join(data_lines)
                            data_lines.clear()
                            try:
                                ev = json.loads(raw_json)
                            except ValueError:
                                continue
                            etype = ev.get("type")
                            if etype == "run_started":
                                if ev.get("model"):
                                    run_model = str(ev["model"])
                                # Sidecar sends workspace names as strings (CONTRACT.md); tolerate {"name": ...}
                                for idx_f, fmeta in enumerate(ev.get("files") or []):
                                    fname = fmeta.get("name") if isinstance(fmeta, dict) else fmeta
                                    # Pair actual workspace names (`drawing.pdf`, `drawing_2.pdf`) in order
                                    if isinstance(fname, str) and fname and idx_f < len(project_drawings or []):
                                        by_ws_file[fname] = project_drawings[idx_f][1]
                            elif etype == "tool_started":
                                step_seq += 1
                                call_id = str(ev.get("call_id") or f"call-{step_seq}")
                                tname = str(ev.get("tool") or "shell")
                                raw_cmd = str(ev.get("command") or ev.get("path") or "")[:300]
                                step_label = (
                                    f"Inspecting image {raw_cmd}" if tname == "view_image"
                                    else f"Running {tname}: {raw_cmd[:90]}"
                                )
                                t_args: dict[str, Any] = {
                                    "step": step_label[:160],
                                    "seq": int(ev.get("seq") or step_seq),
                                }
                                if tname == "view_image":
                                    t_args["path"] = raw_cmd
                                    f_rect, f_did = self._focus_rect_from_event(ev, d, by_ws_file)
                                    if f_rect is not None:
                                        t_args["focus_rect"] = f_rect
                                        if f_did and scope.get("kind") == "project":
                                            t_args["drawing_id"] = f_did
                                else:
                                    t_args["command"] = raw_cmd
                                node = {
                                    "id": f"agent-step-{step_seq}",
                                    "parent": "q",
                                    "title": "Inspecting crop" if tname == "view_image" else "Agent shell step",
                                    "tag": f"Agent · {tname}",
                                    "body": raw_cmd or tname,
                                    "kind": "model",
                                    "tool": {
                                        "name": "deep_agent",
                                        "args": t_args,
                                        "result_id": f"r-{call_id}",
                                        "ms": 0,
                                    },
                                }
                                pending_nodes[call_id] = node
                                trace_nodes.append(node)
                                yield "tool_call", {"node": node}
                            elif etype == "tool_finished":
                                call_id = str(ev.get("call_id") or "")
                                node = pending_nodes.get(call_id)
                                if node is not None:
                                    if ev.get("tool") == "view_image" and "focus_rect" not in node["tool"]["args"]:
                                        f_rect, f_did = self._focus_rect_from_event(ev, d, by_ws_file)
                                        if f_rect is not None:
                                            node["tool"]["args"]["focus_rect"] = f_rect
                                            if f_did and scope.get("kind") == "project":
                                                node["tool"]["args"]["drawing_id"] = f_did
                                    secs = ev.get("seconds")
                                    if isinstance(secs, (int, float)):
                                        node["tool"]["ms"] = max(0, round(float(secs) * 1000))
                                    summary = f"Completed in {secs}s" if secs is not None else "Completed"
                                    yield "tool_result", {
                                        "node_id": node["id"],
                                        "evidence": {"boxes": [], "rooms": []},
                                        "summary": summary,
                                    }
                            elif etype == "answers":
                                answers_payload = ev.get("answers") or []
                            elif etype == "run_finished":
                                run_usage = ev.get("usage") or {}
                                if ev.get("status") == "done":
                                    run_finished_cleanly = True
                                else:
                                    run_error = str(ev.get("error") or "Agent run failed.")
                                break
                        elif raw_line.startswith("data:"):
                            data_lines.append(raw_line[5:].lstrip())

            # Fallback GET /runs/{run_id} if SSE closed right before `answers` was parsed
            if answers_payload is None and run_finished_cleanly:
                r_full = await self._http.get(f"/runs/{run_id}", headers=headers, timeout=10.0)
                if r_full.status_code == 200:
                    j_full = r_full.json()
                    answers_payload = j_full.get("answers") or []
                    run_usage = j_full.get("usage") or run_usage
                    run_model = j_full.get("model") or run_model

            latency_ms = int((time.perf_counter() - t_start) * 1000)
            n_reqs = int(run_usage.get("requests") or 0)
            in_tok = int(run_usage.get("input_tokens") or 0)
            out_tok = int(run_usage.get("output_tokens") or 0)

            if not run_finished_cleanly or not answers_payload:
                if n_reqs > 0:
                    rec = self.budget.record_external(
                        res,
                        model=run_model,
                        n_requests=n_reqs,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        latency_ms=latency_ms,
                        status=502,
                    )
                    meter.calls += n_reqs
                    meter.cost_usd += rec.cost_usd
                    meter.tokens += in_tok + out_tok
                else:
                    self.budget.release(res)
                settled = True
                stats["no_cache"] = True
                stats["route"] = "agent"
                built = A.abstain(
                    question,
                    lang,
                    response_scope,
                    "low_confidence",
                    question,
                    f"The deep agent could not complete this run ({run_error or 'no answer returned'}).",
                    intent_tag=intent_tag,
                )
                yield "_built", built
                return

            # Settle exact metered usage in SQLite Ledger + Budget
            rec = self.budget.record_external(
                res,
                model=run_model,
                n_requests=max(1, n_reqs),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                status=200,
            )
            settled = True
            meter.calls += max(1, n_reqs)
            meter.cost_usd += rec.cost_usd
            meter.tokens += in_tok + out_tok
            stats["route"] = "agent"

            ans_item = answers_payload[0]
            built, filter_event = await self._map_agent_answer(
                d=d,
                question=question,
                lang=lang,
                scope=response_scope,
                is_project_scope=(scope.get("kind") == "project"),
                by_ws_file=by_ws_file,
                ans_item=ans_item,
                scan_boxes=scan_boxes,
                scan_subj=scan_subj,
                trace_nodes=trace_nodes,
                step_seq=step_seq,
                caller=caller,
                meter=meter,
                intent_tag=intent_tag,
            )
            if filter_event is not None:
                yield "tool_call", {"node": filter_event["node"]}
                yield "tool_result", filter_event["result"]
            yield "_built", built

        except TimeoutError:
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            if not settled:
                if run_id is not None:
                    # Run started on sidecar -> book worst-case 40 slots because actual count isn't known yet
                    rec = self.budget.record_external(
                        res,
                        model=run_model,
                        n_requests=MAX_AGENT_SLOTS,
                        input_tokens=0,
                        output_tokens=0,
                        latency_ms=latency_ms,
                        status=504,
                    )
                    meter.calls += MAX_AGENT_SLOTS
                    meter.cost_usd += rec.cost_usd
                else:
                    self.budget.release(res)
                settled = True
            stats["no_cache"] = True
            stats["route"] = "agent"
            elapsed_s = max(1, round(time.perf_counter() - t_start))
            built = A.abstain(
                question,
                lang,
                response_scope,
                "low_confidence",
                question,
                f"The agent did not finish in {elapsed_s} s; nothing guessed (not known yet, at most 40 requests).",
                intent_tag=intent_tag,
            )
            yield "_built", built
        except ( httpx.HTTPError, GeminiError ) as e:
            if not settled:
                self.budget.release(res)
                settled = True
            stats["no_cache"] = True
            stats["route"] = "agent"
            built = A.abstain(
                question,
                lang,
                response_scope,
                "low_confidence",
                question,
                f"The deep agent service is unavailable ({e}); nothing guessed.",
                intent_tag=intent_tag,
            )
            yield "_built", built
        finally:
            if not settled:
                self.budget.release(res)
                settled = True
            if run_id is not None and not run_finished_cleanly:
                with contextlib.suppress(httpx.HTTPError):
                    await self._http.post(f"/runs/{run_id}/cancel", headers=self._headers(), timeout=3.0)
            self._sem.release()

    # ---- Answer & evidence conversion (Table 3b) ----

    async def _map_agent_answer(
        self,
        *,
        d: tools.LoadedDrawing,
        question: str,
        lang: str,
        scope: dict,
        is_project_scope: bool,
        by_ws_file: dict[str, tools.LoadedDrawing],
        ans_item: dict[str, Any],
        scan_boxes: list[Box],
        scan_subj: str | None,
        trace_nodes: list[dict],
        step_seq: int,
        caller: Any,
        meter: Meter,
        intent_tag: str,
    ) -> tuple[A.Built, dict | None]:
        raw_val = ans_item.get("answer")
        raw_unit = (str(ans_item.get("unit")).strip() if ans_item.get("unit") is not None else None) or None
        method_prose = str(ans_item.get("method") or "").strip()
        check_prose = str(ans_item.get("check") or "").strip()
        conf = float(ans_item.get("confidence") or 0.0)
        raw_ev = ans_item.get("evidence") or []

        # Resolve count subject if needed
        lex_subj = lexical_subject_of(question)
        count_subj: str | None = lex_subj if lex_subj in VALID_COUNT_SUBJECTS else None
        if (
            count_subj is None
            and raw_unit == "count"
            and isinstance(raw_val, int)
            and not isinstance(raw_val, bool)
            and self.client is not None
            and self.client.available()
        ):
            try:
                picked, p_conf = await pick_agent_subject(
                    self.client,
                    question,
                    ip_hash=caller.ip_hash,
                    presenter=caller.presenter,
                    meter=meter,
                )
                if picked in VALID_COUNT_SUBJECTS and p_conf >= 0.80:
                    count_subj = picked
            except Exception:  # noqa: BLE001 - optional relabel after a paid run; never lose the answer
                count_subj = None

        box_subject = count_subj if count_subj in VALID_BOX_SUBJECTS else (
            lex_subj if lex_subj in VALID_BOX_SUBJECTS else "label"
        )

        # Convert evidence items -> boxes, rooms, rows, groups, links
        final_boxes: list[Box] = []
        final_rooms: list[EvidenceRoom] = []
        final_rows: list[EvidenceRow] = []
        final_links: list[EvidenceLink] = []
        seen_groups: dict[str, dict[str, Any]] = {}
        seen_room_ids: set[tuple[str, str]] = set()
        used_scan_ids: set[str] = set()

        def ensure_group(gid_raw: str | None, glabel_raw: str | None, target_did: str | None = None) -> str | None:
            if not gid_raw:
                return None
            gid = str(gid_raw).strip()
            if not gid:
                return None
            if gid not in seen_groups:
                color = GROUP_COLORS[len(seen_groups) % len(GROUP_COLORS)]
                seen_groups[gid] = {
                    "id": gid,
                    "label": (str(glabel_raw).strip() if glabel_raw else gid),
                    "color": color,
                    "count": 0,
                    "area_sum": 0.0,
                    "has_area": False,
                    "target_drawing_id": target_did,
                }
            elif target_did and not seen_groups[gid].get("target_drawing_id"):
                seen_groups[gid]["target_drawing_id"] = target_did
            return gid

        for idx, ev in enumerate(raw_ev, 1):
            if not isinstance(ev, dict):
                continue
            ws_file = ev.get("file")
            item_d = by_ws_file.get(ws_file, d) if isinstance(ws_file, str) else d
            item_did_opt = item_d.id if (is_project_scope or item_d.id != d.id) else None

            target_ws = ev.get("target_file")
            target_d = by_ws_file.get(target_ws) if isinstance(target_ws, str) else None
            target_did_opt = target_d.id if target_d is not None else None

            gid = ensure_group(ev.get("group"), ev.get("group_label"), target_did_opt)
            ekind = ev.get("kind")
            item_label = str(ev.get("label") or ev.get("name") or ev.get("text") or ev.get("note") or "").strip()

            # 1) IFC guid matching against pre-parsed model.json when available
            if ekind == "ifc" and ev.get("guid") and getattr(item_d._engine, "m", None) is not None:
                from aura.solvers import ifc as ifc_solver

                m = item_d._engine.m
                guid = str(ev["guid"])
                matched_room = next((r for r in m.rooms if r.get("room_id") == guid), None)
                if matched_room is not None:
                    rkey = (item_d.id, guid)
                    if rkey not in seen_room_ids:
                        seen_room_ids.add(rkey)
                        area_v = round(float(matched_room["area"]), 2) if matched_room.get("area") is not None else None
                        final_rooms.append(EvidenceRoom(
                            room_id=guid,
                            area_m2=area_v,
                            included=True,
                            group_id=gid,
                            drawing_id=item_did_opt,
                        ))
                        if gid:
                            seen_groups[gid]["count"] += 1
                            if area_v is not None:
                                seen_groups[gid]["area_sum"] += area_v
                                seen_groups[gid]["has_area"] = True
                    continue

                matched_fp = None
                matched_fam = None
                for fam, fps in (m.footprints or {}).items():
                    for g in fps:
                        if g.get("guid") == guid and g.get("polygon"):
                            matched_fp = g["polygon"]
                            matched_fam = fam
                            break
                    if matched_fp is not None:
                        break
                if matched_fp is not None and m.frame is not None:
                    pct_box = ifc_solver._bbox_pct(m, matched_fp)
                    b_subj = matched_fam if matched_fam in VALID_BOX_SUBJECTS else box_subject
                    final_boxes.append(Box(
                        id=f"ag-{idx:02d}",
                        x=pct_box["x"],
                        y=pct_box["y"],
                        w=pct_box["w"],
                        h=pct_box["h"],
                        subject=b_subj,  # type: ignore[arg-type]
                        note=(item_label or f"IFC {guid}")[:200],
                        group_id=gid,
                        drawing_id=item_did_opt,
                    ))
                    if gid:
                        seen_groups[gid]["count"] += 1
                    continue

            # 2) CAD room matching when agent returns a CAD room polygon/bbox or room_id/label on area/apartment queries
            pct_rect: dict | None = None
            raw_cad_bbox = ev.get("bbox") or ev.get("bbox_cad")
            if isinstance(raw_cad_bbox, list) and len(raw_cad_bbox) >= 4:
                pct_rect = cad_bbox_to_pct(item_d.drawing, raw_cad_bbox)
            if pct_rect is None and isinstance(ev.get("bbox_px"), list) and len(ev["bbox_px"]) >= 4:
                pct_rect = px_bbox_to_pct(item_d.drawing, ev["bbox_px"])

            # Check if evidence matches an indexed room on `item_d` when answering area/room/apartment questions
            if (ekind == "room" or raw_unit == "m2" or count_subj in ("room", "apartment")) and (item_d.drawing.get("rooms") or []):
                matched_dr_room = None
                ev_rid = str(
                    ev.get("room_id") or ev.get("id") or ev.get("handle") or ev.get("name") or ev.get("label") or ""
                ).strip()
                if ev_rid.startswith("room-"):
                    ev_rid = ev_rid.removeprefix("room-")
                for dr_r in item_d.drawing.get("rooms") or []:
                    if ev_rid and (
                        dr_r["room_id"] == ev_rid
                        or dr_r.get("num") == ev_rid
                        or dr_r.get("name") == ev_rid
                    ):
                        matched_dr_room = dr_r
                        break
                if matched_dr_room is None and pct_rect is not None:
                    cx = pct_rect["x"] + pct_rect["w"] / 2.0
                    cy = pct_rect["y"] + pct_rect["h"] / 2.0
                    for dr_r in item_d.drawing.get("rooms") or []:
                        lat = dr_r.get("label_at")
                        if (isinstance(lat, list) and len(lat) >= 2
                                and abs(float(lat[0]) - cx) <= 4.0 and abs(float(lat[1]) - cy) <= 4.0):
                            matched_dr_room = dr_r
                            break
                if matched_dr_room is not None:
                    rkey = (item_d.id, matched_dr_room["room_id"])
                    if rkey not in seen_room_ids:
                        seen_room_ids.add(rkey)
                        area_v = matched_dr_room.get("area_m2")
                        if area_v is None and getattr(item_d._engine, "m", None) is not None:
                            for mr in getattr(item_d._engine.m, "rooms", []):
                                if mr.get("id") == matched_dr_room["room_id"] or mr.get("room_id") == matched_dr_room["room_id"]:
                                    raw_a = mr.get("area_m2", mr.get("area"))
                                    if raw_a is not None:
                                        area_v = round(float(raw_a), 2)
                                    break
                        final_rooms.append(EvidenceRoom(
                            room_id=matched_dr_room["room_id"],
                            area_m2=area_v,
                            included=True,
                            group_id=gid,
                            drawing_id=item_did_opt,
                        ))
                        if gid:
                            seen_groups[gid]["count"] += 1
                            if area_v is not None:
                                seen_groups[gid]["area_sum"] += float(area_v)
                                seen_groups[gid]["has_area"] = True
                        if pct_rect is not None:
                            self._append_link_if_any(
                                ev=ev,
                                idx=idx,
                                from_pos=(
                                    round(pct_rect["x"] + pct_rect["w"] / 2.0, 2),
                                    round(pct_rect["y"] + pct_rect["h"] / 2.0, 2),
                                ),
                                item_d=item_d,
                                target_d=target_d,
                                gid=gid,
                                item_did_opt=item_did_opt,
                                target_did_opt=target_did_opt,
                                final_links=final_links,
                            )
                        continue

            if pct_rect is not None:
                box_id = f"ag-{idx:02d}"
                if scan_boxes and item_d.id == d.id:
                    for sb in scan_boxes:
                        if sb.id not in used_scan_ids and _boxes_overlap(sb.model_dump(), pct_rect):
                            box_id = sb.id
                            used_scan_ids.add(sb.id)
                            break
                note_txt = item_label or (str(ev.get("layer") or "")[:120]) or None
                final_boxes.append(Box(
                    id=box_id,
                    x=pct_rect["x"],
                    y=pct_rect["y"],
                    w=pct_rect["w"],
                    h=pct_rect["h"],
                    subject=box_subject,  # type: ignore[arg-type]
                    note=note_txt[:200] if note_txt else None,
                    group_id=gid,
                    drawing_id=item_did_opt,
                ))
                if gid:
                    seen_groups[gid]["count"] += 1
                self._append_link_if_any(
                    ev=ev,
                    idx=idx,
                    from_pos=(
                        round(pct_rect["x"] + pct_rect["w"] / 2.0, 2),
                        round(pct_rect["y"] + pct_rect["h"] / 2.0, 2),
                    ),
                    item_d=item_d,
                    target_d=target_d,
                    gid=gid,
                    item_did_opt=item_did_opt,
                    target_did_opt=target_did_opt,
                    final_links=final_links,
                )
            elif ekind == "ifc" or ev.get("guid"):
                guid = str(ev.get("guid") or f"ifc-{idx:02d}")
                final_rows.append(EvidenceRow(
                    id=guid,
                    name=(item_label or str(ev.get("ifc_class") or guid))[:200],
                ))

        final_groups = [
            EvidenceGroup(
                id=g["id"],
                label=g["label"],
                color=g["color"],
                target_drawing_id=g["target_drawing_id"],
            )
            for g in seen_groups.values()
        ]

        # Build candidate-filter telemetry (`kept_ids` & `dropped_ids`) when `scan_boxes` ran
        filter_event: dict | None = None
        if scan_boxes:
            kept_ids: list[str] = []
            dropped_ids: list[str] = []
            fb_dicts = [b.model_dump() for b in final_boxes]
            for sb in scan_boxes:
                sbd = sb.model_dump()
                if sb.id in used_scan_ids or any(_boxes_overlap(sbd, fb) for fb in fb_dicts):
                    kept_ids.append(sb.id)
                else:
                    dropped_ids.append(sb.id)
            filter_node = {
                "id": "agent-filter",
                "parent": "q",
                "title": "Candidate verification",
                "tag": "Agent · filter",
                "body": f"Confirmed {len(final_boxes)} matching item(s); dropped {len(dropped_ids)} non-matching candidate(s).",
                "kind": "model",
                "evidence_ids": [b.id for b in final_boxes] or None,
                "tool": {
                    "name": "deep_agent",
                    "args": {
                        "step": f"Kept {len(final_boxes)} matching, dropped {len(dropped_ids)}",
                        "seq": step_seq + 1,
                        "kept_ids": kept_ids,
                        "dropped_ids": dropped_ids,
                    },
                    "result_id": "r-agent-filter",
                    "ms": 0,
                },
            }
            filter_node = {k: v for k, v in filter_node.items() if v is not None}
            trace_nodes.append(filter_node)
            filter_event = {
                "node": filter_node,
                "result": {
                    "node_id": "agent-filter",
                    "evidence": {
                        "boxes": [b.model_dump(exclude_none=True) for b in final_boxes],
                        "rooms": [r.model_dump(exclude_none=True) | {"area_m2": r.area_m2} for r in final_rooms],
                    },
                    "summary": filter_node["body"],
                },
            }

        # Append agent method & self-check prose nodes to trace
        if method_prose:
            trace_nodes.append({
                "id": "agent-method",
                "parent": "q",
                "title": "Agent measurement method",
                "tag": "Agent",
                "body": method_prose[:1000],
                "kind": "model",
            })
        if check_prose and check_prose.lower() != "none":
            trace_nodes.append({
                "id": "agent-self-check",
                "parent": "q",
                "title": "Agent verification note",
                "tag": "Agent",
                "body": check_prose[:1000],
                "kind": "model",
            })

        # Table 3b: confidence < 0.5 -> abstain(reason='low_confidence') (`0.5` itself is kept!)
        if raw_val is None:
            where_hint = (
                check_prose[:200]
                if check_prose and check_prose.lower() != "none"
                else (method_prose[:200] or "Not shown on this drawing.")
            )
            built = A.abstain(
                question,
                lang,
                scope,
                "not_on_drawing",
                question,
                where_hint,
                where_to_find=where_hint[:160],
                intent_tag=intent_tag,
            )
            self._splice_trace(built, trace_nodes)
            return built, filter_event

        if conf < 0.5:
            built = A.abstain(
                question,
                lang,
                scope,
                "low_confidence",
                question,
                f"The agent's confidence ({conf:.2f}) is below the 0.50 threshold; nothing guessed.",
                intent_tag=intent_tag,
            )
            self._splice_trace(built, trace_nodes)
            return built, filter_event

        # Construct `ToolResult` + `A.Built` according to Table 3b
        rid = "r-agent-" + hashlib.sha1(f"{d.id}:{question}:{raw_val}:{raw_unit}".encode()).hexdigest()[:10]
        rows_meta = [r.model_dump(exclude_none=True) for r in final_rows]
        base_meta: dict[str, Any] = {"rows": rows_meta} if rows_meta else {}

        is_int_val = isinstance(raw_val, int) and not isinstance(raw_val, bool)
        is_num_val = isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool)

        if raw_unit == "count" and is_int_val and count_subj is not None:
            tr = tools.ToolResult(
                tool="deep_agent",
                args={"question": question[:160], "scope": scope},
                result_id=rid,
                kind="count",
                value=int(raw_val),
                subject=count_subj,
                boxes=final_boxes,
                rooms=final_rooms,
                groups=final_groups,
                links=final_links,
                method="agent_estimate",
                summary=method_prose[:240] or f"{int(raw_val)} {A.noun(count_subj, int(raw_val))}",
                meta=base_meta,
            )
            built = A.count(question, lang, d, tr, scope, None, intent_tag=intent_tag, intent_conf=conf)
            built.subject = count_subj if count_subj in BOX_SUBJECTS else None
            built.check_ids = [b.id for b in final_boxes] if (built.subject and not is_project_scope) else []
        elif raw_unit == "m2" and is_num_val:
            val_m2 = round(float(raw_val), 2)
            tr = tools.ToolResult(
                tool="deep_agent",
                args={"question": question[:160], "scope": scope},
                result_id=rid,
                kind="area",
                value=val_m2,
                subject="room",
                boxes=final_boxes,
                rooms=final_rooms,
                groups=final_groups,
                links=final_links,
                method="agent_estimate",
                summary=method_prose[:240] or f"{val_m2:.2f} m²",
                meta=base_meta | {"denominator": "agent_measured"},
            )
            built = A.area(question, lang, d, tr, scope, None, intent_tag=intent_tag, intent_conf=conf)
        elif raw_unit in ("m", "m3") and is_num_val:
            val_q = round(float(raw_val), 2)
            q_subj = lex_subj or ("length" if raw_unit == "m" else "volume")
            tr = tools.ToolResult(
                tool="deep_agent",
                args={"question": question[:160], "scope": scope},
                result_id=rid,
                kind="quantity",
                value=val_q,
                subject=q_subj,
                boxes=final_boxes,
                rooms=final_rooms,
                groups=final_groups,
                links=final_links,
                method="agent_estimate",
                summary=method_prose[:240] or f"{val_q:.2f} {raw_unit}",
                meta=base_meta | {"unit": raw_unit, "property": q_subj},
            )
            built = A.quantity(question, lang, d, tr, scope, intent_tag=intent_tag)
        else:
            # Open string answer, non-contract subject count (e.g. "spots"), float count, or unmapped unit -> AnswerNote
            num_opt: float | None = float(raw_val) if is_num_val else None
            if is_num_val and raw_unit and raw_unit != "count":
                note_text = f"{raw_val:g} {raw_unit}" if isinstance(raw_val, float) else f"{raw_val} {raw_unit}"
            elif is_num_val and raw_unit == "count":
                note_text = f"{raw_val:g}" if isinstance(raw_val, float) else f"{raw_val}"
                if method_prose:
                    note_text = f"{note_text} — {method_prose[:180]}"
            else:
                note_text = str(raw_val).strip()[:400]
            tr = tools.ToolResult(
                tool="deep_agent",
                args={"question": question[:160], "scope": scope},
                result_id=rid,
                kind="note",
                value=num_opt,
                text=note_text,
                subject=box_subject,
                boxes=final_boxes,
                rooms=final_rooms,
                groups=final_groups,
                links=final_links,
                method="agent_estimate",
                summary=method_prose[:240] or note_text[:240],
                meta=base_meta | ({"unit": raw_unit} if raw_unit else {}),
            )
            built = A.note(question, lang, d, tr, scope, intent_tag=intent_tag)

        # Attach `method: "agent_estimate"` and `estimate: {"confidence": ...}` (`measured` omitted!)
        built.body["method"] = "agent_estimate"
        built.body["estimate"] = {"confidence": round(min(1.0, max(0.0, conf)), 3)}
        if method_prose and "explanation" not in built.body:
            built.body["explanation"] = method_prose[:600]

        self._splice_trace(built, trace_nodes)
        return built, filter_event

    @staticmethod
    def _splice_trace(built: A.Built, extra_nodes: list[dict]) -> None:
        if not extra_nodes:
            return
        trace = built.body["trace"]
        at = next((i for i, n in enumerate(trace) if n["id"] == "answer"), len(trace))
        clean = [{k: v for k, v in n.items() if v is not None} for n in extra_nodes]
        built.body["trace"] = trace[:at] + clean + trace[at:]

    @staticmethod
    def _append_link_if_any(
        *,
        ev: dict[str, Any],
        idx: int,
        from_pos: tuple[float, float],
        item_d: tools.LoadedDrawing,
        target_d: tools.LoadedDrawing | None,
        gid: str | None,
        item_did_opt: str | None,
        target_did_opt: str | None,
        final_links: list[EvidenceLink],
    ) -> None:
        dest_d = target_d or item_d
        to_at: list[float] | None = None
        if isinstance(ev.get("lead_to_cad"), list) and len(ev["lead_to_cad"]) >= 2:
            to_at = cad_pt_to_pct(dest_d.drawing, float(ev["lead_to_cad"][0]), float(ev["lead_to_cad"][1]))
        if to_at is None and isinstance(ev.get("lead_to_px"), list) and len(ev["lead_to_px"]) >= 2:
            to_at = px_pt_to_pct(dest_d.drawing, float(ev["lead_to_px"][0]), float(ev["lead_to_px"][1]))
        if to_at is not None:
            final_links.append(EvidenceLink(
                id=f"link-{idx:02d}",
                from_pos=from_pos,
                to_pos=(float(to_at[0]), float(to_at[1])),
                group_id=gid,
                label=str(ev["lead_to_label"])[:160] if ev.get("lead_to_label") else None,
                drawing_id=item_did_opt,
                target_drawing_id=target_did_opt,
            ))
