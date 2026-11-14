"""Put all pages of one drawing into one shared frame in metres (rows in `frame`).

Reference: the model space of the drawing's CAD file, whose frame the CAD reader wrote (units -> metres).
Every PDF, SVG or raster page of the same drawing is then placed by, in this order:

  text_anchors  texts that occur exactly once in the CAD file and exactly once on the page are paired; a similarity
                transform (uniform scale, rotation, shift; y mirrored or not) is fitted with outlier rejection.
                residual = RMS distance of the kept anchors in metres.
  twin_dpi      a raster that shows the same sheet as a one-page PDF of the drawing (equal aspect) inherits the
                PDF's frame through the pixel size.  Chosen over the raster's own text fit unless it places the
                raster's anchors worse by more than two pixels; residual = RMS of those anchors under it.
  scale_text    the one trusted scale printed on the page ("Scale 1:100", 1/4" = 1'-0") and the paper size (points, the physical
                size of an SVG, or the DPI stored in an image).  This gives true lengths and areas but NOT a
                position relative to the other files: origin = bottom-left of the page, y up, no anchors.

Without CAD but with a model (IFC, whose storeys are in world metres already):

  model_anchors a page whose paths carry model element ids (data-guid, as model-rendered SVGs do) is placed by
                pairing each element's drawn outline with its footprint in the model; a PDF of the same storey
                then follows through `text_anchors` against the texts of that placed page.

Otherwise a drawing without CAD gets `scale_text` frames only; a page with no scale, or with several different
ones, gets no frame.  Paper-space layouts of CAD files are not placed.
"""
from __future__ import annotations

import math
import re
import unicodedata

import numpy as np

from .db import DB, arr

PT_M = 0.0254 / 72
MIN_ANCHORS = 4

_METRIC = re.compile(r"(?<![\d:.,/])1\s?:\s?(\d{1,5})(?![\d:/])")
_IMPERIAL = re.compile(r"""(?<![\d/])(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:"|in\.?|inch(?:es)?)\s*=\s*
                           (\d+)\s*'\s*(?:-?\s*(\d+(?:\s+\d+/\d+|\.\d+)?)\s*(?:"|in\b)?)?""", re.X | re.I)
_SCALE_WORD = re.compile(r"""(?:scale|ma(?:ss|ß|s)stab|mst\.?|[ée]chelle|scala|escala|schaal|m[åa]lestokk?|skala|
                             mittakaava|m[õo][õo]tkava|m[ěe][řr][ií]tko|\bmk|\bm)\s*[:=.]?\s*$""", re.X | re.I)
_STANDARD = {20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000, 1250, 2000, 2500, 5000, 10000}
_QUOTES = str.maketrans({"\u2033": '"', "\u201d": '"', "\u201c": '"', "\u2032": "'", "\u2019": "'", "\u2018": "'"})


def _num(s: str) -> float:
    total = 0.0
    for part in s.split():
        if "/" in part:
            a, b = part.split("/")
            total += float(a) / float(b) if float(b) else 0.0
        else:
            total += float(part)
    return total


def scales_in(text: str) -> list[dict]:
    """Scale statements in a text -> [{text, ratio, kind, labelled}]; ratio = real size / drawn size (1:100 -> 100).
    labelled: a word for "scale" stands right before it, or it is written as paper length = real length.  A bare
    "1:12" can as well be a slope or a mix ratio, so it is reported but marked unlabelled."""
    t = (text or "").translate(_QUOTES).replace("''", '"')
    out = []
    for m in _METRIC.finditer(t):
        n = int(m[1])
        if n >= 1:
            out.append(dict(text=f"1:{n}", ratio=float(n), kind="metric",
                            labelled=bool(_SCALE_WORD.search(t[max(0, m.start() - 24):m.start()]))))
    for m in _IMPERIAL.finditer(t):
        paper = _num(m[1])
        real = float(m[2]) * 12 + (_num(m[3]) if m[3] else 0.0)
        if paper > 0 and real > 0:
            out.append(dict(text=re.sub(r"\s+", " ", m[0].strip()), ratio=round(real / paper, 4), kind="imperial",
                            labelled=True))
    return out


def trusted_scale(scales) -> dict | None:
    """The one scale a page (or file) can be measured with: labelled statements, and bare ones with a customary
    denominator of 20 or more; None unless all of them give the same ratio."""
    pick = [s for s in scales or [] if s.get("labelled") or (s["kind"] == "metric" and s["ratio"] in _STANDARD)]
    pick.sort(key=lambda s: not s.get("labelled"))
    return pick[0] if pick and len({round(float(s["ratio"]), 3) for s in pick}) == 1 else None


def norm_text(s: str) -> str:
    """Key for comparing a text across sources: case, spacing and typographic variants removed."""
    s = unicodedata.normalize("NFKC", s or "").casefold().replace(",", ".")
    return re.sub(r"\s+", "", s).strip(".:;")


def apply(m, pts) -> np.ndarray:
    p = np.asarray(pts, dtype=float).reshape(-1, 2)
    return np.stack([m[0] * p[:, 0] + m[1] * p[:, 1] + m[2], m[3] * p[:, 0] + m[4] * p[:, 1] + m[5]], axis=1)


def compose(outer, inner) -> list[float]:
    """The transform `inner` followed by `outer`."""
    a, b, c, d, e, f = outer
    g, h, i, j, k, l = inner
    return [a * g + b * j, a * h + b * k, a * i + b * l + c, d * g + e * j, d * h + e * k, d * i + e * l + f]


def _solve(P, Q, mirror: bool):
    """Least-squares similarity P -> Q.  mirror: X = a x + b y + c, Y = b x - a y + f; else Y = -b x + a y + f."""
    n = len(P)
    A = np.zeros((2 * n, 4))
    A[0::2, 0], A[0::2, 1], A[0::2, 2] = P[:, 0], P[:, 1], 1
    if mirror:
        A[1::2, 0], A[1::2, 1], A[1::2, 3] = -P[:, 1], P[:, 0], 1
    else:
        A[1::2, 0], A[1::2, 1], A[1::2, 3] = P[:, 1], -P[:, 0], 1
    (a, b, c, f), *_ = np.linalg.lstsq(A, Q.reshape(-1), rcond=None)
    return [a, b, c, b, -a, f] if mirror else [a, b, c, -b, a, f]


def fit_similarity(P, Q, tol: float) -> dict | None:
    """Robust fit of page points P onto metre points Q.  -> {m, residual, n, inliers} or None."""
    P, Q = np.asarray(P, float), np.asarray(Q, float)
    n = len(P)
    if n < 2:
        return None
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(pairs) > 400:
        rng = np.random.default_rng(0)
        pairs = [pairs[k] for k in rng.choice(len(pairs), 400, replace=False)]
    best = None
    for mirror in (True, False):
        for i, j in pairs:
            if np.hypot(*(P[i] - P[j])) < 1e-9:
                continue
            m = _solve(P[[i, j]], Q[[i, j]], mirror)
            inl = np.hypot(*(apply(m, P) - Q).T) <= tol
            k = int(inl.sum())
            if best is None or k > best[0]:
                best = (k, mirror, inl)
    if best is None or best[0] < 2:
        return None
    _, mirror, inl = best
    for _ in range(3):                                  # refit, then trim what sits far outside the bulk
        m = _solve(P[inl], Q[inl], mirror)
        r = np.hypot(*(apply(m, P) - Q).T)
        keep = r <= min(tol, max(3 * float(np.median(r[inl])), tol / 10))
        if keep.sum() < 2 or (keep == inl).all():
            break
        inl = keep
    m = _solve(P[inl], Q[inl], mirror)
    r = np.hypot(*(apply(m, P[inl]) - Q[inl]).T)
    return dict(m=[float(v) for v in m], residual=float(np.sqrt((r ** 2).mean())), n=int(inl.sum()), inliers=inl,
                scale=float(math.hypot(m[0], m[1])), rotation=float(math.degrees(math.atan2(m[3], m[0]))))


# ------------------------------------------------------------------ anchors
def _cad_texts(db: DB, file_id: str, m_cad) -> dict:
    """Texts that occur once in the model space -> {key: (point in metres, fx, fy, rotation, height in metres)}."""
    rows = db.run("""select text, dxftype, st_x(st_centroid(geom)) x, st_y(st_centroid(geom)) y, attrs
                     from jstore.cad_entity
                     where file_id = :f and space = 'model' and text is not null and text <> '' and geom is not null
                       and dxftype in ('TEXT', 'MTEXT', 'ATTRIB')""", f=file_id)
    k = math.hypot(m_cad[0], m_cad[3])
    seen: dict[str, list] = {}
    for r in rows:
        key = norm_text(r["text"])
        if len(key) < 2 or not any(ch.isalnum() for ch in key):
            continue
        a = r["attrs"] or {}
        if r["dxftype"] == "MTEXT":
            at = int(a.get("attachment") or 1) - 1
            fx, fy = (at % 3) / 2, (at // 3) / 2
        else:
            h, v = int(a.get("halign") or 0), int(a.get("valign") or 0)
            fx = {0: 0.0, 1: 0.5, 2: 1.0, 4: 0.5}.get(h)
            fy = 0.5 if h == 4 else {0: None, 1: 1.0, 2: 0.5, 3: 0.0}[v if v in (0, 1, 2, 3) else 0]
        pt = apply(m_cad, [(r["x"], r["y"])])[0]
        seen.setdefault(key, []).append((pt, fx, fy, float(a.get("rotation") or 0.0) % 360, float(a.get("height") or 0) * k))
    return {key: v[0] for key, v in seen.items() if len(v) == 1}


def _page_texts(db: DB, file_id: str, page_no: int) -> tuple[dict, str]:
    """Texts that occur once on a page -> ({key: (x0, y0, x1, y1, rot)}, source)."""
    words = db.run("""select text, x0, y0, x1, y1, line_no, coalesce(rot, 0) rot from jstore.pdf_text
                      where file_id = :f and page_no = :p order by seq""", f=file_id, p=page_no)
    units, source = [], "pdf_text"
    if words:
        lines: dict[int, list] = {}
        for w in words:
            units.append((w["text"], w["x0"], w["y0"], w["x1"], w["y1"], w["rot"]))
            if w["line_no"] is not None:
                lines.setdefault(w["line_no"], []).append(w)
        for ws in lines.values():
            if len(ws) > 1:
                units.append((" ".join(w["text"] for w in ws), min(w["x0"] for w in ws), min(w["y0"] for w in ws),
                              max(w["x1"] for w in ws), max(w["y1"] for w in ws), ws[0]["rot"]))
    else:
        source = "raster_word"
        units = [(w["text"], w["x0"], w["y0"], w["x1"], w["y1"], w["rot"]) for w in db.run(
            """select text, x0, y0, x1, y1, coalesce(rot, 0) rot from jstore.raster_word
               where file_id = :f and page_no = :p and conf >= 0.5""", f=file_id, p=page_no)]
    seen: dict[str, list] = {}
    for u in units:
        key = norm_text(u[0])
        if len(key) >= 2 and any(ch.isalnum() for ch in key):
            seen.setdefault(key, []).append(u[1:])
    return {key: v[0] for key, v in seen.items() if len(v) == 1}, source


def _anchors(cad: dict, page: dict, source: str, aligned: bool):
    """Paired points.  aligned: the point of the text box that the CAD alignment names, upright texts only;
    otherwise box centres of every pair."""
    base = 0.8 if source == "pdf_text" else 1.0        # a PDF word box includes the descender
    P, Q = [], []
    for key, (pt, fx, fy, rot, _h) in cad.items():
        if key not in page:
            continue
        x0, y0, x1, y1, prot = page[key]
        if aligned:
            if fx is None or min(rot, 360 - rot) > 1 or prot != 0:
                continue
            P.append((x0 + fx * (x1 - x0), y0 + (base if fy is None else fy) * (y1 - y0)))
        else:
            P.append(((x0 + x1) / 2, (y0 + y1) / 2))
        Q.append(pt)
    return np.array(P, float).reshape(-1, 2), np.array(Q, float).reshape(-1, 2)


def _fit_page(cad: dict, page: dict, source: str) -> dict | None:
    heights = [v[4] for v in cad.values() if v[4] > 0]
    tol = max(0.15, 1.5 * float(np.median(heights))) if heights else 0.5
    for aligned in (True, False):
        P, Q = _anchors(cad, page, source, aligned)
        if len(P) < MIN_ANCHORS:
            continue
        fit = fit_similarity(P, Q, tol)
        if fit and fit["n"] >= MIN_ANCHORS and fit["n"] >= 0.5 * len(P):
            fit.update(P=P, Q=Q, candidates=len(P))
            return fit
    return None


def _fit_model(db: DB, ifc_file: str, file_id: str, page_no: int) -> dict | None:
    """Place a model-rendered page by the element ids its paths carry (attrs.data-guid): the centre of each
    element's drawn outline against the centre of its footprint in the model (metres, world XY)."""
    rows = db.run("""
        with drawn as (
          select attrs->>'data-guid' guid, st_centroid(st_convexhull(st_collect(geom))) c
          from jstore.pdf_path where file_id = :f and page_no = :p and attrs ? 'data-guid' group by 1)
        select st_x(d.c) x, st_y(d.c) y, st_x(st_centroid(st_convexhull(g.footprint))) mx,
               st_y(st_centroid(st_convexhull(g.footprint))) my
        from drawn d join jstore.ifc_geometry g on g.file_id = :i and g.guid = d.guid
        where g.footprint is not null and not st_isempty(g.footprint)""", f=file_id, p=page_no, i=ifc_file)
    if len(rows) < MIN_ANCHORS:
        return None
    P = np.array([(r["x"], r["y"]) for r in rows], float)
    Q = np.array([(r["mx"], r["my"]) for r in rows], float)
    fit = fit_similarity(P, Q, 0.25)
    if fit and fit["n"] >= MIN_ANCHORS and fit["n"] >= 0.3 * len(P):
        fit["candidates"] = len(P)
        return fit
    return None


def _texts_in_frame(db: DB, file_id: str, page_no: int, m) -> dict:
    """Unique texts of a placed page as reference anchors (box centres in metres) for its twins."""
    texts, _ = _page_texts(db, file_id, page_no)
    k = math.hypot(m[0], m[3])
    return {key: (apply(m, [((x0 + x1) / 2, (y0 + y1) / 2)])[0], 0.5, 0.5, 0.0, abs(y1 - y0) * k)
            for key, (x0, y0, x1, y1, rot) in texts.items() if rot == 0}


# ------------------------------------------------------------------ scale only
def _scale_frame(page: dict) -> list[float] | None:
    """Metres from the single scale printed on a page and its physical size; None when either is unknown."""
    meta = page["meta"] or {}
    scale = trusted_scale(meta.get("scales"))
    if scale is None:
        return None
    if meta.get("unit_m"):
        unit = float(meta["unit_m"])
    elif page["unit"] == "pt":
        unit = PT_M
    elif page["unit"] == "px" and meta.get("dpi") and float(meta["dpi"][0]) > 96:
        unit = 0.0254 / float(meta["dpi"][0])          # 72 and 96 are writer defaults, not a statement about paper
    else:
        return None
    k = unit * float(scale["ratio"])
    return [k, 0, 0, 0, -k, k * float(page["height"] or 0)]


def _write(db: DB, file_id: str, page_no: int, m, method: str, residual=None, n=None) -> dict:
    db.run("""insert into jstore.frame (file_id, page_no, m, method, residual, n_anchors)
              values (:f, :p, cast(:m as double precision[]), :how, :r, :n)
              on conflict (file_id, page_no) do update set m = excluded.m, method = excluded.method,
                residual = excluded.residual, n_anchors = excluded.n_anchors""",
           f=file_id, p=page_no, m=arr(m), how=method, r=residual, n=n)
    return dict(file_id=file_id, page_no=page_no, method=method, residual=residual, n_anchors=n)


def register_drawing(db: DB, drawing_key: str) -> dict:
    """Write frame rows for every PDF, SVG and raster page of a drawing.  -> what was done per page."""
    files = db.run("""select file_id, format, storey from jstore.source_file where drawing_key = :d
                      order by format, file_id""", d=drawing_key)
    by_fmt = {f["file_id"]: f["format"] for f in files}
    storey = {f["file_id"]: f["storey"] for f in files}
    model = next((f["file_id"] for f in files if f["format"] == "ifc"), None)
    placed: dict = {}                                   # storey -> reference texts of a page placed in the model
    docs = [f for f in by_fmt if by_fmt[f] in ("pdf", "svg", "png", "jpg")]
    if docs:
        db.run("delete from jstore.frame where file_id = any(cast(:f as text[]))", f=docs)
    ref, cad = None, {}
    for f in files:
        if f["format"] in ("dxf", "dwg"):
            fr = db.one("select m from jstore.frame where file_id = :f and page_no = 0", f=f["file_id"])
            if fr:
                ref, cad = f["file_id"], _cad_texts(db, f["file_id"], fr["m"])
                break
    pages = db.run("""select file_id, page_no, width, height, unit, meta from jstore.page
                      where file_id = any(cast(:f as text[])) order by file_id, page_no""", f=docs)
    out, done = [], {}
    rank = {"svg": 0, "pdf": 1}
    order = sorted(pages, key=lambda p: (rank.get(by_fmt[p["file_id"]], 2), p["file_id"], p["page_no"]))
    vector_twins = [p for p in pages if by_fmt[p["file_id"]] == "pdf"
                    and sum(q["file_id"] == p["file_id"] for q in pages) == 1]
    for p in order:
        fid, no, raster = p["file_id"], p["page_no"], by_fmt[p["file_id"]] in ("png", "jpg")
        fit, how = None, "text_anchors"
        if cad:
            texts, source = _page_texts(db, fid, no)
            fit = _fit_page(cad, texts, source)
        elif model:
            fit = _fit_model(db, model, fid, no)
            if fit:
                how = "model_anchors"
                placed.setdefault(storey[fid], _texts_in_frame(db, fid, no, fit["m"]))
            elif placed.get(storey[fid]):
                texts, source = _page_texts(db, fid, no)
                fit = _fit_page(placed[storey[fid]], texts, source)
        twin = None
        if raster and p["width"] and p["height"]:
            for t in vector_twins:
                if (t["file_id"], t["page_no"]) in done and t["width"] and t["height"] and \
                        abs(p["width"] / p["height"] / (t["width"] / t["height"]) - 1) < 0.01:
                    s = t["width"] / p["width"]
                    twin = (compose(done[(t["file_id"], t["page_no"])]["m"], [s, 0, 0, 0, s, 0]),
                            done[(t["file_id"], t["page_no"])])
                    break
        if twin and twin[1]["method"] != "scale_text":
            m, src = twin
            if fit:                                     # judge the inherited frame on this raster's own anchors
                r = np.hypot(*(apply(m, fit["P"][fit["inliers"]]) - fit["Q"][fit["inliers"]]).T)
                rms = float(np.sqrt((r ** 2).mean()))
                # OCR boxes sit a pixel or two off the true text position, a bias the free fit absorbs but
                # the geometry does not share: the twin wins unless it is worse by more than two pixels
                if rms <= fit["residual"] + 2 * math.hypot(m[0], m[3]):
                    row = _write(db, fid, no, m, "twin_dpi", rms, fit["n"])
                else:
                    row = _write(db, fid, no, fit["m"], "text_anchors", fit["residual"], fit["n"])
                row.update(twin_rms=rms, fit_rms=fit["residual"])
            else:
                row = _write(db, fid, no, m, "twin_dpi", src["residual"], 0)
            row["m"] = m if row["method"] == "twin_dpi" else fit["m"]
        elif fit:
            row = _write(db, fid, no, fit["m"], how, fit["residual"], fit["n"])
            row.update(m=fit["m"], scale=fit["scale"], rotation=fit["rotation"], candidates=fit["candidates"])
        else:
            m = _scale_frame(p) or (twin[0] if twin else None)
            if m is None:
                out.append(dict(file_id=fid, page_no=no, method=None))
                continue
            row = _write(db, fid, no, m, "scale_text" if _scale_frame(p) else "twin_dpi", None, 0)
            row["m"] = m
        done[(fid, no)] = row
        out.append({k: v for k, v in row.items() if k != "m"})
    return dict(drawing=drawing_key, reference=ref, pages=out)
