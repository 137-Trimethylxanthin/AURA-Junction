"""Single or double door, decided by the decision model looking at a crop of each door.

A width threshold mislabels wide single doors and narrow double doors. Here each door box is cut out of
the drawing's page image with a margin (so both leaves and their swing arcs are visible) and the
decision model (Decisions API, `choice`) says whether the crop shows one leaf, two leaves, a sliding /
other door, or cannot be told. The verdict decides membership of "double doors" / "single doors".

- Whole door layer per drawing, `BATCH` crops per request, requests in parallel, at most `MAX_CALLS`;
  doors beyond the cap stay "unclear" (never silently counted).
- Cached per door: key = drawing id + door id + image sha1 + box geometry, in memory and in the
  drawing's folder (`door_leaves.json`), so the second question costs 0 calls and an ingest step can
  fill the same file later (`classify` takes no request context beyond the client).
- No page image, no client, or a failed request: the width rule (`steps.DOUBLE_DOOR_M`) answers and the
  trace says so.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image, ImageDraw

from aura import answer as A
from aura.budget import BudgetError
from aura.gemini.client import GeminiError, Meter, image_part, text_part

log = logging.getLogger("aura.door_leaves")

BATCH = 20  # preferred crops per request ...
MAX_BATCH = 40  # ... grown up to this when the question has fewer calls left; beyond that doors stay unclear
MAX_CALLS = 4  # requests per classification, also bounded by the question's remaining call budget
PURPOSE = "door_leaves"  # budget purpose: counted against the per-IP model-question limit
FAIL_TTL_S = 120.0  # a failed classification of a drawing is not retried for this long
MAX_CROP_PX = 1024  # absolute cap on the crop cut from the page (it is downscaled to TILE_PX anyway)
TIMEOUT_S = 40.0
TILE_PX = 384
CROP_SCALE = 2.6  # crop half-size = this × half the box's long side (the swing arc reaches one door width out) ...
CROP_MARGIN = 0.012  # ... + this share of the image's long side
MAGENTA = (220, 0, 120)
CACHE_FILE = "door_leaves.json"
VERDICTS = ("single", "double", "sliding", "unclear")
# A leaf verdict below its floor is reported as unclear. Measured on swiss-46639 / swiss-13399: lift doors read
# as "double" at 0.72-0.73 (true doubles 0.87-0.98); plain single doors with the whole arc in view at 0.70-0.78.
MIN_CONFIDENCE = {"double": 0.8, "single": 0.6}
CRITERIA = {
    "single": "ONE door leaf: a single swing arc (quarter circle) or a single door panel in the marked opening.",
    "double": "TWO door leaves in the same marked opening: two swing arcs or two panels side by side, "
              "meeting in the middle (a double / French / two-leaf door, equal or unequal leaves).",
    "sliding": "A sliding, pocket, folding, revolving or lift (elevator) door, or another door without "
               "a swing arc.",
    "unclear": "No door is visible in the marked rectangle, or the number of leaves cannot be told.",
}
INTRO = ("Zoomed tiles cut from one architectural floor plan. Each tile has one magenta rectangle "
         "marking a door. Count the door leaves of THAT door only (ignore other doors in the tile).\n")

# The user's own words for the two sets, in the five UI languages (en, de, fr, it, fi).
DOUBLE_WORDS = re.compile(
    r"\b(double|two[- ]leaf|french\s+doors?|doppel\w*|zweifl[üu]gel\w*|porte\s+double|portes\s+doubles|"
    r"double\s+porte|porta\s+doppia|porte\s+doppie|a\s+due\s+ante|pariov\w*|kaksilehti\w*)", re.IGNORECASE)
SINGLE_WORDS = re.compile(
    r"\b(single|one[- ]leaf|einfl[üu]gel\w*|einzelt[üu]r\w*|einfache?\s+t[üu]r\w*|porte\s+simple|"
    r"portes\s+simples|porta\s+singola|porte\s+singole|a\s+un'?\s*anta|yksilehti\w*|yksioven\w*|"
    r"tavalli\w*\s+ov\w*)", re.IGNORECASE)

_MEM: dict[str, dict] = {}  # per-door cache key -> verdict row
_LOCKS: dict[str, asyncio.Lock] = {}
_SHA: dict[tuple[str, float], str] = {}
_FAILED: dict[str, float] = {}  # drawing id + image sha -> time of the last failed classification


def wanted(args: dict, question: str) -> str | None:
    """'double' / 'single' when this list_openings call asks for one of the two door sets."""
    if str(args.get("kind") or "") != "door":
        return None
    if args.get("double"):
        return "double"
    if SINGLE_WORDS.search(question or "") and not DOUBLE_WORDS.search(question or ""):
        return "single"
    return None


def image_sha(path: Path) -> str:
    k = (str(path), path.stat().st_mtime)
    if k not in _SHA:
        _SHA[k] = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    return _SHA[k]


def door_key(drawing_id: str, box: Any, sha: str) -> str:
    return f"{drawing_id}:{box.id}:{sha}:{box.x:.2f},{box.y:.2f},{box.w:.2f},{box.h:.2f}"


def make_crop(im: Image.Image, box: Any) -> bytes:
    """Square crop around the door (pixels, not percent), the door marked, resized to TILE_PX."""
    W, H = im.size
    x, y, w, h = _clamped(box)
    bx, by, bw, bh = x / 100 * W, y / 100 * H, w / 100 * W, h / 100 * H
    cx, cy = bx + bw / 2, by + bh / 2
    # Box geometry comes from uploaded drawings: the crop side is bounded whatever the box says.
    half = max(bw, bh) / 2 * CROP_SCALE + CROP_MARGIN * max(W, H)
    side = max(8, min(int(2 * half), int(1.2 * max(W, H)), MAX_CROP_PX))
    half = side / 2
    x0, y0 = int(cx - half), int(cy - half)
    tile = Image.new("RGB", (side, side), "white")  # pad at the image border instead of squashing
    tile.paste(im.crop((max(0, x0), max(0, y0), min(W, x0 + side), min(H, y0 + side))),
               (max(0, -x0), max(0, -y0)))
    pad = 0.15 * max(bw, bh)
    ImageDraw.Draw(tile).rectangle([bx - x0 - pad, by - y0 - pad, bx + bw - x0 + pad, by + bh - y0 + pad],
                                   outline=MAGENTA, width=max(2, side // 160))
    tile = tile.resize((TILE_PX, TILE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    tile.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def _clamped(box: Any) -> tuple[float, float, float, float]:
    """x, y, w, h in percent, finite and inside the page (ValueError for non-finite values)."""
    vals = [float(v) for v in (box.x, box.y, box.w, box.h)]
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("non-finite door box")
    x, y = (min(100.0, max(0.0, v)) for v in vals[:2])
    w, h = min(100.0 - x, max(0.0, vals[2])), min(100.0 - y, max(0.0, vals[3]))
    return x, y, w, h


def _finite(box: Any) -> bool:
    try:
        _clamped(box)
        return True
    except (TypeError, ValueError):
        return False


def groups_of(boxes: list) -> list[tuple[Any, list[str]]]:
    """One crop per distinct rectangle; a door drawn twice at the same spot shares its crop."""
    out: dict[tuple, tuple[Any, list[str]]] = {}
    for b in boxes:
        k = (round(b.x, 2), round(b.y, 2), round(b.w, 2), round(b.h, 2))
        if k in out:
            out[k][1].append(b.id)
        else:
            out[k] = (b, [b.id])
    return list(out.values())


def _load_file(d) -> dict:
    p = d.dir / CACHE_FILE if d.dir is not None else None
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("doors") or {}
    except (OSError, ValueError):
        return {}


def _save_file(d, rows: dict) -> None:
    if d.dir is None:
        return
    p = d.dir / CACHE_FILE
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "doors": rows}, indent=1))
        tmp.replace(p)
    except OSError:
        pass  # a read-only drawing folder: the memory cache still serves this process


async def _ask_batch(client, im: Image.Image, batch: list[tuple[Any, list[str]]], meter: Meter,
                     caller) -> tuple[dict, dict]:
    def build() -> tuple[list[dict], dict]:
        parts, qs = [text_part(INTRO)], {}
        for n, (box, _ids) in enumerate(batch, 1):
            parts += [text_part(f"\nTile {n}:\n"), image_part(make_crop(im, box), "medium")]
            qs[f"d{n:02d}"] = {"type": "choice", "criteria": CRITERIA,
                               "instructions": f"Tile {n}: how many door leaves does the door in the "
                                               f"magenta rectangle have?"}
        return parts, qs

    parts, qs = await asyncio.to_thread(build)
    async with asyncio.timeout(TIMEOUT_S):
        res = await client.decisions(parts, qs, PURPOSE, ip_hash=getattr(caller, "ip_hash", None),
                                     presenter=bool(getattr(caller, "presenter", False)), meter=meter)
    out = {}
    for n, (box, ids) in enumerate(batch, 1):
        choice, conf, probs = res.choice(f"d{n:02d}")
        v = choice if choice in VERDICTS else "unclear"
        if conf < MIN_CONFIDENCE.get(v, 0.0):
            v = "unclear"
        for i in ids:
            out[i] = {"verdict": v, "choice": choice, "confidence": round(float(conf), 3),
                      "probabilities": {k: round(float(p), 3) for k, p in probs.items()}, "model": res.model}
    info = {"crops": len(batch), "model": res.model, "latency_ms": res.latency_ms, "cost_usd": res.cost_usd}
    return out, info


def usable(svc) -> bool:
    client = getattr(svc, "client", None)
    if client is None or not hasattr(client, "decisions"):
        return False
    ok = getattr(svc, "models_usable", None)
    return bool(ok()) if callable(ok) else True


async def classify(svc, d, boxes: list, caller=None, meter: Meter | None = None,
                   max_calls: int = MAX_CALLS) -> dict:
    """Verdicts for every door box: {door_id: {verdict, confidence, probabilities, model, source}}, plus
    `_batches` (one entry per request sent) and `_why` when the model could not be used at all.
    At most `max_calls` requests, metered on `meter` (the question's own meter)."""
    image = d.image_path()
    if image is None:
        return {"_why": "no page image for this drawing", "_batches": []}
    sha = image_sha(image)
    bad = [b.id for b in boxes if not _finite(b)]
    boxes = [b for b in boxes if _finite(b)]
    lock = _LOCKS.setdefault(f"{d.id}:{sha}", asyncio.Lock())
    async with lock:  # two questions at once share one classification
        disk = _load_file(d)
        rows: dict[str, dict] = {}
        todo = []
        for b in boxes:
            k = door_key(d.id, b, sha)
            hit = _MEM.get(k) or (disk.get(b.id) if disk.get(b.id, {}).get("key") == k else None)
            if hit:
                rows[b.id] = {**hit, "source": "cache"}
            else:
                todo.append(b)
        out: dict[str, Any] = {**rows, "_batches": []}
        if not todo:
            return out
        for i in bad:
            out[i] = {"verdict": "unclear", "confidence": 0.0, "source": "invalid", "note": "invalid door box"}
        if not usable(svc):
            return {**out, "_why": "the decision model is not available"}
        failed = _FAILED.get(f"{d.id}:{sha}")
        if failed is not None and time.time() - failed < FAIL_TTL_S:
            return {**out, "_why": f"the decision model failed on this drawing {time.time() - failed:.0f} s ago; "
                                   f"not retried for {FAIL_TTL_S:.0f} s"}
        calls = max(0, min(max_calls, MAX_CALLS))
        if calls == 0:
            return {**out, "_why": "no model calls left for this question"}
        groups = groups_of(todo)
        n_batches = min(calls, -(-len(groups) // BATCH))
        size = min(MAX_BATCH, -(-len(groups) // n_batches))
        capped = groups[size * n_batches:]
        groups = groups[:size * n_batches]
        with Image.open(image) as src:
            im = src.convert("RGB")
        dm = meter if meter is not None else Meter()
        batches = [groups[i:i + size] for i in range(0, len(groups), size)]
        results = await asyncio.gather(*(_ask_batch(svc.client, im, bt, dm, caller) for bt in batches),
                                       return_exceptions=True)
        fresh = {}
        for bt, r in zip(batches, results):
            if isinstance(r, BaseException):
                if not isinstance(r, (GeminiError, BudgetError, TimeoutError)):
                    log.exception("door leaf batch failed", exc_info=r)
                out["_batches"].append({"crops": len(bt), "error": str(r) or type(r).__name__})
                continue
            verdicts, info = r
            counts: dict[str, int] = {}
            for v in verdicts.values():
                counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
            out["_batches"].append({**info, "verdicts": counts,
                                    "confidence": round(min((v["confidence"] for v in verdicts.values()),
                                                            default=0.0), 3)})
            fresh.update(verdicts)
        by_id = {b.id: b for b in todo}
        for i, v in fresh.items():
            k = door_key(d.id, by_id[i], sha)
            row = {**v, "key": k, "t": time.time()}
            _MEM[k] = row
            disk[i] = row
            out[i] = {**v, "source": "model"}
        if fresh:
            _save_file(d, disk)
        for _box, ids in capped:
            for i in ids:
                out[i] = {"verdict": "unclear", "confidence": 0.0, "source": "cap",
                          "note": f"not checked: more than {size * n_batches} doors for the calls left"}
        if not fresh:
            _FAILED[f"{d.id}:{sha}"] = time.time()
        if not fresh and not rows:
            out["_why"] = "the decision model request failed: " + "; ".join(
                b.get("error", "") for b in out["_batches"])[:200]
        return out


async def list_openings(svc, d, sess, ref: str, a: dict, caller, meter: Meter, trace: list[dict],
                        parent: str, calls_left: int = MAX_CALLS):
    """list_openings for 'double doors' / 'single doors', membership decided per door by the model."""
    from aura import steps  # steps imports this module lazily; no cycle at import time

    want = wanted(a, getattr(sess, "question", ""))
    t0 = time.perf_counter()
    tb = sess.tb
    base_args = {k: v for k, v in a.items() if k != "double"}
    for side in ("min_width_m", "max_width_m"):  # the old width proxy for the leaf count
        if steps._num(base_args.get(side)) is not None and abs(steps._num(base_args[side]) - steps.DOUBLE_DOOR_M) < 0.011:
            base_args.pop(side)
    # One classification attempt per drawing per question; a repeat reads the cache only.
    tried: set = sess.__dict__.setdefault("leaves_tried", set())
    calls0 = meter.calls
    res = await classify(svc, d, tb.boxes.get("door", []), caller, meter,
                         max_calls=0 if d.id in tried else calls_left)
    tried.add(d.id)
    dm = SimpleNamespace(calls=meter.calls - calls0,
                         cost_usd=sum(b.get("cost_usd") or 0.0 for b in res.get("_batches", [])))
    batches = res.pop("_batches", [])
    why = res.pop("_why", None)
    for k, b in enumerate(batches, 1):
        trace.append(A.stage_node(
            "decision", f"leaves-{ref}-{k}", f"Door leaves · batch {k}", f"{b.get('model', '?')} · door crops",
            (f"{b['crops']} door crops: " + ", ".join(f"{n} {v}" for v, n in sorted(b["verdicts"].items()))
             + f"; lowest confidence {b['confidence']:.2f} ({b.get('latency_ms', 0)} ms).")
            if "verdicts" in b else f"{b['crops']} door crops: request failed ({b['error'][:120]}).",
            parent=parent, kind="model",
            detail={"decider": b.get("model"), "question": "One door leaf or two?", "options": list(VERDICTS),
                    "crops": b["crops"], "verdicts": b.get("verdicts"), "latency_ms": b.get("latency_ms"),
                    "cost_usd": b.get("cost_usd")},
            confidence=b.get("confidence")))
    cached = sum(1 for v in res.values() if v.get("source") == "cache")
    if cached and not batches:
        counts: dict[str, int] = {}
        for v in res.values():
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        trace.append(A.stage_node(
            "decision", f"leaves-{ref}-cache", "Door leaves · cached", "decision model · earlier door crops",
            f"{cached} door verdicts from an earlier run on this drawing and image (0 calls): "
            + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())) + ".",
            parent=parent, kind="model", detail={"cached": cached, "verdicts": counts}))
    if why:
        # The width rule, as before, and the trace says why.
        trace.append(A.stage_node(
            "decision", f"leaves-{ref}-fallback", "Door leaves · width rule",
            "code · clear width", f"Leaf count not seen on the drawing ({why}); "
            f"double door = clear width ≥ {steps.DOUBLE_DOOR_M:.2f} m.", parent=parent, kind="exact",
            detail={"fallback": "width_rule", "why": why}))
        fa = dict(a)
        if want == "single":
            fa.update(max_width_m=steps.DOUBLE_DOOR_M, max_strict=True)
            fa.pop("double", None)
        else:
            fa["double"] = True
        rs = sess.t_list_openings(ref, fa)
        rs.rule += f"; width rule (leaf count not seen: {why})"
        rs.ms = int((time.perf_counter() - t0) * 1000)
        sess.sets[ref] = rs
        return rs
    rs = sess.t_list_openings(ref, base_args)
    kept, dropped, confs, unclear = [], list(rs.dropped), [], []
    for i in rs.ids:
        v = res.get(i) or {"verdict": "unclear", "confidence": 0.0}
        if v["verdict"] == want:
            kept.append(i)
            confs.append(v["confidence"])
        elif v["verdict"] == "unclear":
            unclear.append(i)
            dropped.append({"id": i, "reason": "model: unclear" + (f" ({v['note']})" if v.get("note") else "")})
        else:
            dropped.append({"id": i, "reason": f"model: {v['verdict']} ({v['confidence']:.2f})"})
    model = next((v.get("model") for v in res.values() if v.get("model")), "decision model")
    per_door = ", ".join(f"{i} {res[i]['confidence']:.2f}" for i in kept[:12] if i in res)
    rs.ids = kept
    rs.dropped = dropped
    rs.rule = (f"{want} door (leaf count seen by {model} on a crop of each door"
               + (f"; confidence per door: {per_door}" if per_door else "") + ")"
               + (f"; {len(unclear)} unclear, not counted: {', '.join(unclear[:12])}" if unclear else "")
               + (f"; among {rs.rule}" if rs.rule and rs.rule != "all" else ""))
    rs.desc = rs.desc.replace("doors", f"{want} doors", 1)
    rs.tables = rs.tables + ["page image (door crops)"]
    rs.judged = {"model": model, "confidence": round(min(confs), 3) if confs else 0.0,
                 "cost_usd": dm.cost_usd, "calls": dm.calls,
                 "per_door": {i: res[i]["confidence"] for i in kept if i in res}, "unclear": unclear}
    rs.ms = int((time.perf_counter() - t0) * 1000)
    sess.sets[ref] = rs
    return rs
