"""Per-box answer check with the Decisions API: 384 px zoom tiles, ranked within one request.

Shipping layout (gemini-usecases.md E1d/E7):
- one subject per request; one tile per distinct box (double-door leaves share a tile);
- tile = box ± 4 % of the image, square, only that box drawn in magenta, resized to 384 px,
  sent at `medium`;
- 3 known-negative anchor tiles in the same request (room-label text or given anchors);
- "agreed" means p > max(anchor p) + 0.05. Scores drift between batches, so no fixed threshold;
- the check covers drawn boxes only (`scope: drawn_boxes`); it cannot find missed objects.

The check always runs on the WHOLE layer of a subject and is cached per (drawing, subject,
evidence-id set); a scoped answer slices the cached result, so it costs 0 calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

from aura.gemini.client import GeminiClient, Meter, image_part, text_part

TILE_PX = 384
MARGIN_PCT = 4.0
BOX_PAD_PCT = 0.4
MAGENTA = (220, 0, 120)
AGREE_MARGIN = 0.05
INTRO = ("Zoomed tiles cut from one architectural floor plan. Each tile has one magenta "
         "rectangle marking a candidate object.\n")
SUBJECT_WORD = {"window": "window", "door": "door", "washbasin": "washbasin", "wc": "toilet (WC)",
                "bathtub": "bathtub", "shower": "shower"}


def check_key(drawing_id: str, subject: str, ids: list[str]) -> str:
    h = hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:12]
    return f"{drawing_id}:{subject}:{h}"


def make_tile(im: Image.Image, box: dict) -> bytes:
    """Crop first, then draw the box offset into the crop (no full-image copy per tile)."""
    W, H = im.size
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    half = max(box["w"], box["h"]) / 2 + MARGIN_PCT
    x0, y0, x1, y1 = (int(v) for v in ((cx - half) / 100 * W, (cy - half) / 100 * H,
                                       (cx + half) / 100 * W, (cy + half) / 100 * H))
    cx0, cy0 = max(0, x0), max(0, y0)
    tile = im.crop((cx0, cy0, min(W, x1), min(H, y1)))
    if box.get("draw", True):
        p = BOX_PAD_PCT
        ImageDraw.Draw(tile).rectangle(
            [(box["x"] - p) / 100 * W - cx0, (box["y"] - p) / 100 * H - cy0,
             (box["x"] + box["w"] + p) / 100 * W - cx0, (box["y"] + box["h"] + p) / 100 * H - cy0],
            outline=MAGENTA, width=2)
    tile = tile.resize((TILE_PX, TILE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    tile.save(buf, format="PNG", compress_level=6)  # optimize=True cost 56 ms a tile
    return buf.getvalue()


def default_anchors(rooms: list[dict], boxes: list[dict], n: int = 3) -> list[dict]:
    """Known negatives: small boxes on room-label text, away from every evidence box."""
    def overlaps(a: dict, b: dict) -> bool:
        return not (a["x"] + a["w"] < b["x"] - 2 or b["x"] + b["w"] < a["x"] - 2 or
                    a["y"] + a["h"] < b["y"] - 2 or b["y"] + b["h"] < a["y"] - 2)

    out: list[dict] = []
    for r in sorted(rooms, key=lambda r: -float(r.get("area_m2") or 0)):
        lx, ly = r["label_at"]
        a = {"id": f"anchor-{len(out) + 1}", "x": lx - 2.0, "y": ly - 1.0, "w": 4.0, "h": 2.0}
        if a["x"] < 0 or a["y"] < 0 or a["x"] + a["w"] > 100 or a["y"] + a["h"] > 100:
            continue
        if any(overlaps(a, b) for b in boxes):
            continue
        out.append(a)
        if len(out) == n:
            break
    return out


def group_tiles(boxes: list[dict]) -> list[tuple[dict, list[str]]]:
    """One tile per distinct rectangle; identical rectangles (double-door leaves) share it."""
    groups: dict[tuple, tuple[dict, list[str]]] = {}
    for b in boxes:
        k = (round(b["x"], 2), round(b["y"], 2), round(b["w"], 2), round(b["h"], 2))
        if k in groups:
            groups[k][1].append(b["id"])
        else:
            groups[k] = (b, [b["id"]])
    return list(groups.values())


class Cancelled(Exception):
    """The caller gave up (deadline) while tiles were being cut."""


def build_request(image_path: Path, subject: str, boxes: list[dict], anchors: list[dict],
                  cancel: threading.Event | None = None,
                  ) -> tuple[list[dict], dict, dict[str, list[str]]]:
    """(input_parts, questions, question key → evidence ids). Anchor keys start with 'a'.
    The image is opened once; each tile is a crop (never a full-image copy). `cancel` stops the
    work between tiles when the awaiting request was cancelled."""
    with Image.open(image_path) as src:
        im = src.convert("RGB")
    word = SUBJECT_WORD.get(subject, subject)
    parts = [text_part(INTRO)]
    questions: dict[str, dict] = {}
    key_ids: dict[str, list[str]] = {}
    n = 0
    for box, ids in group_tiles(boxes):
        if cancel is not None and cancel.is_set():
            raise Cancelled
        n += 1
        parts += [text_part(f"\nTile {n}:\n"), image_part(make_tile(im, box), "medium")]
        key = f"b{n:02d}"
        questions[key] = {"type": "noul",
                          "instructions": f"In tile {n}, does the magenta rectangle mark a {word}?"}
        key_ids[key] = ids
    for a in anchors:
        n += 1
        parts += [text_part(f"\nTile {n}:\n"), image_part(make_tile(im, a), "medium")]
        questions[f"a{n:02d}"] = {
            "type": "noul",
            "instructions": f"In tile {n}, does the magenta rectangle mark a {word}?"}
    return parts, questions, key_ids


async def run_check(
    client: GeminiClient,
    *,
    drawing_id: str,
    image_path: Path,
    subject: str,
    boxes: list[dict],
    anchors: list[dict],
    bucket: str = "public",
    ip_hash: str | None = None,
    presenter: bool = False,
    meter: Meter | None = None,
) -> dict:
    """One Decisions request over the whole layer. Returns the cacheable full result."""
    # Tiles are cut off the event loop (Pillow work for 70+ boxes takes a while); a deadline
    # cancels the await at once and tells the worker thread to stop at the next tile.
    cancel = threading.Event()
    try:
        parts, questions, key_ids = await asyncio.to_thread(build_request, image_path, subject,
                                                            boxes, anchors, cancel)
    except BaseException:
        cancel.set()
        raise
    res = await client.decisions(parts, questions, "check", bucket=bucket,  # type: ignore[arg-type]
                                 ip_hash=ip_hash, presenter=presenter, meter=meter)
    per_box: dict[str, float] = {}
    for key, ids in key_ids.items():
        p = res.noul(key)
        if p is None:
            continue
        for i in ids:
            per_box[i] = round(p, 4)
    anchor_ps = [res.noul(k) for k in questions if k.startswith("a")]
    anchor_ps = [round(p, 4) for p in anchor_ps if p is not None]
    return {
        "drawing_id": drawing_id,
        "subject": subject,
        "per_box": per_box,
        "anchors": anchor_ps,
        "anchors_max": max(anchor_ps) if anchor_ps else None,
        "model": res.model,
        "input_tokens": res.input_tokens,
        "cost_usd": res.cost_usd,
        "latency_ms": res.latency_ms,
        "tiles": len(key_ids) + len(anchor_ps),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def slice_check(full: dict, ids: list[str], image: str = "dataset_png") -> dict | None:
    """The contract Check (1.1 ranked fields) for the evidence ids of one answer."""
    per = {i: full["per_box"][i] for i in ids if i in full.get("per_box", {})}
    if not per:
        return None
    ranking = sorted(per, key=lambda i: (per[i], i))
    out: dict = {"method": "decisions_api", "status": "live", "per_box": per, "scope": "drawn_boxes",
                 "ranking": ranking, "least_certain_id": ranking[0], "total": len(per),
                 "image": image}
    if len(set(ids)) > len(per):
        out["unscored"] = len(set(ids)) - len(per)  # boxes the model gave no score for
    amax = full.get("anchors_max")
    if amax is not None:
        out["anchors_max"] = amax
        out["agreed"] = sum(1 for p in per.values() if p > amax + AGREE_MARGIN)
    return out
