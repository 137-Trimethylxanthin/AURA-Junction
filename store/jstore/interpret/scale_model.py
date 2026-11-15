"""Metres per pixel of a plan image as the model judges it from the things drawn on it: a second, independent
approach beside the box and room-size estimators of raster_plan.estimate_scale.

    estimate(image, budget_s) -> {mpp, n, objects_mpp, whole_mpp, spread, objects: [{what, m, px}]} or None

One request per drawing (MODEL, cached under <store>/cache/gemini like every request of jstore.gemini_detect): the
page, long side PAGE_SIDE pixels, and the question for 8 to 16 objects of known real size (door openings, beds,
bathtubs, toilets, worktops, stair flights, parking spaces, rooms judged by their furniture), each with its box and
the real length of the box's longer side, and for the real width the whole image covers.  The lengths in pixels are
taken from the boxes, not asked for.  objects_mpp = median of metres / pixels over the objects; whole_mpp = image
width in metres / pixels; mpp = their geometric mean (the objects alone when there is no usable width).

Measured on 127 scans of the store with a stated scale (error of metres per pixel: median / 90th percentile):
estimate_scale without printed areas 6.5 % / 52 %; this estimate alone 2.9 % / 14 %; as one more voter of
estimate_scale (SIGMA["model"] there) 2.3 % / 10.7 %.

Settings: JSTORE_SCALE_MODEL (default on when GEMINI_API_KEY is set; 0 turns it off).
"""
from __future__ import annotations

import math
import os
import time

import numpy as np

PAGE_SIDE = 1536
TIMEOUT_S = 90.0
PROMPT = (
    "The image is an architectural floor plan WITHOUT a usable scale. Estimate its scale from things whose real size "
    "is known. Pick 8 to 16 clearly drawn objects of DIFFERENT kinds spread over the plan, preferring: single door "
    "openings (leaf about 0.8-0.9 m), beds (about 2.0 m long), bathtubs (about 1.7 m), toilets (about 0.7 m deep), "
    "washbasins, kitchen worktops (0.6 m deep), cookers/fridges (0.6 m), stair flights (tread depth about 0.27 m, give "
    "the run of N treads), parking spaces (5.0 m), wardrobes (0.6 m deep), lift cars, and whole rooms whose "
    "size you can judge from their furniture. For each give: what (short name), box_2d = [ymin, xmin, ymax, xmax] "
    "(integers 0-1000 relative to this image, tight around the object), and long_side_m = your estimate of the "
    "real-world length in metres of the LONGER side of that box. Judge each object on its own; do not derive one "
    "from another. Finally give image_width_m: your overall estimate of the real-world width in metres that the "
    "full image width covers.")
SCHEMA = {"type": "object", "properties": {
    "objects": {"type": "array", "items": {"type": "object", "properties": {
        "what": {"type": "string"}, "box_2d": {"type": "array", "items": {"type": "integer"}},
        "long_side_m": {"type": "number"}}, "required": ["what", "box_2d", "long_side_m"]}},
    "image_width_m": {"type": "number"}}, "required": ["objects", "image_width_m"]}


def off() -> bool:
    return (os.environ.get("JSTORE_SCALE_MODEL", "1").strip().lower() in ("0", "off", "false", "no")
            or not os.environ.get("GEMINI_API_KEY"))


def read(answer: dict | None, W: int, H: int) -> dict | None:
    """The model's answer as metres per pixel of an image W x H.  None when it names nothing usable."""
    if not isinstance(answer, dict):
        return None
    objs = []
    for o in answer.get("objects") or []:
        try:
            ymin, xmin, ymax, xmax = (float(c) for c in o["box_2d"])
            m = float(o["long_side_m"])
        except (KeyError, TypeError, ValueError):
            continue
        px = max(abs(xmax - xmin) / 1000 * W, abs(ymax - ymin) / 1000 * H)
        if px > 2 and 0.05 < m < 200:
            objs.append(dict(what=str(o.get("what") or "")[:60], m=round(m, 2), px=round(px, 1)))
    try:
        whole = float(answer.get("image_width_m") or 0) / W
    except (TypeError, ValueError):
        whole = 0.0
    whole = whole if 0 < whole < 10 else None
    if len(objs) < 3 and not (objs and whole):              # one or two objects alone are a guess
        return None
    per = [o["m"] / o["px"] for o in objs]
    med = float(np.median(per))
    return dict(mpp=math.sqrt(med * whole) if whole else med, n=len(objs), objects_mpp=round(med, 6),
                whole_mpp=round(whole, 6) if whole else None,
                spread=round(float(np.std([math.log(v) for v in per])), 3) if len(per) > 1 else None, objects=objs[:16])


def estimate(image, budget_s: float = TIMEOUT_S) -> dict | None:
    """Metres per pixel of `image` (a path or a PIL image), in ITS pixels.  None without a key, time or an answer."""
    from .. import gemini_detect as G
    if off() or budget_s < 15:
        return None
    t0 = time.time()
    im = G.load(image)
    stats = G.Stats()
    ans = G.generate(G._fit(im, PAGE_SIDE), PROMPT, SCHEMA, stats, tag="scale", timeout=min(TIMEOUT_S, budget_s))
    out = read(ans, *im.size)
    if out:
        out.update(model=G.MODEL, requests=stats.calls, cached=stats.cached, seconds=round(time.time() - t0, 1))
    return out
