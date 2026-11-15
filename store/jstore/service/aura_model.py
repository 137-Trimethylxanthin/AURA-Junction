"""One drawing of the store in the shape the AURA frontend draws: a page image, and every room, door, window and
fixture as a polygon or box in percent (0-100) of that image, with stable evidence ids (`win-01`, `room-3.04`).

Built on the fly from the q views; nothing is written to disk.  Synchronous (the app calls it in a thread).

The served image ("base page") is one rendered page of one file of the drawing.  An entity is placed on it
  1. through the frames: entity.geom (metres, the drawing's shared frame) -> base file coordinates (inverse of
     frame.m) -> pixels (page.to_image), when the base page has a frame that holds for the entity; otherwise
  2. through the entity's own sources on the base page (entity_source.bbox, or the Layer 1 outline it points at).
Entities that cannot be placed either way keep their values and get no geometry.
"""
from __future__ import annotations

import base64
import binascii
import dataclasses
import html
import math
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import shapely
from shapely import affinity
from shapely.geometry import box as make_box

from ..db import DB
from .. import space_guard
from . import store
from . import plan_tidy
from .plan_symbols import door_paths, door_symbol, opening_cut

CONTRACT_VERSION = "1.1"

# ---------------------------------------------------------------------------------------------- ids
_ID_SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,150}$")


def drawing_id(key: str) -> str:
    """A drawing key as a URL-safe id: the key itself when it already is one, else `k.` + base64url."""
    if _ID_SAFE.match(key) and not key.startswith("k."):
        return key
    return "k." + base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def drawing_key(did: str) -> str | None:
    if not did or len(did) > 700:
        return None
    if not did.startswith("k."):
        return did if _ID_SAFE.match(did) else None
    try:
        raw = did[2:]
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return key if key and drawing_id(key) == did else None      # one id per key: nothing but the canonical spelling


STOREY_SEP = "~"            # not a character of a drawing id, and not escaped by encodeURIComponent
_SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{0,60}$")


def split_id(did: str) -> tuple[str | None, str | None]:
    """`<drawing id>` or `<drawing id>~<storey slug>` -> (drawing key, storey slug)."""
    base, sep, slug = (did or "").partition(STOREY_SEP)
    if sep and not _SLUG.match(slug):
        return None, None
    return drawing_key(base), slug or None


def storey_norms(name: str | None) -> frozenset[str]:
    """Spellings of a storey name that count as the same storey: case, accents and punctuation do not matter
    ("4-korrus" ~ "4.korrus"), and a name whose non-ASCII letters were dropped on the way into a file name still
    matches ("klder" ~ "Kælder")."""
    low = unicodedata.normalize("NFKD", (name or "").casefold())
    low = "".join(c for c in low if not unicodedata.combining(c))
    full = "".join(c for c in low if c.isalnum())
    ascii_only = "".join(c for c in full if c.isascii())
    return frozenset(x for x in (full, ascii_only) if x)


def storey_slug(name: str | None) -> str:
    low = unicodedata.normalize("NFKD", (name or "").lower())
    return "-".join(re.findall(r"[a-z0-9]+", low))[:40].strip("-")


# kind in the store -> (AURA subject, evidence id prefix).  Kinds without a subject of their own are shown as
# labelled boxes (subject "label", the kind in `note`).
SUBJECT = {"door": ("door", "door"), "window": ("window", "win"), "washbasin": ("washbasin", "basin"),
           "toilet": ("wc", "wc"), "bathtub": ("bathtub", "tub"), "shower": ("shower", "shower"), "sink": ("sink", "sink")}
ROOM_SUBJECT = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "sauna": "sauna",
                "balcony": "balcony", "wc": "wc_room", "toilet": "wc_room"}
VECTOR = ("dxf", "dwg", "svg", "pdf")
CAD = ("dxf", "dwg")


def subject_of(kind: str) -> str:
    return SUBJECT.get(kind, ("label", ""))[0]


def prefix_of(kind: str) -> str:
    return SUBJECT[kind][1] if kind in SUBJECT else (re.sub(r"[^a-z0-9]+", "", kind.lower())[:12] or "item")


# ---------------------------------------------------------------------------------------------- affine maps
Affine = tuple[float, float, float, float, float, float]   # x' = a*x + b*y + c ; y' = d*x + e*y + f


def inv(m) -> Affine | None:
    a, b, c, d, e, f = m
    det = a * e - b * d
    if not math.isfinite(det) or abs(det) < 1e-300:
        return None
    return (e / det, -b / det, (b * f - e * c) / det, -d / det, a / det, (d * c - a * f) / det)


def mul(m2, m1) -> Affine:
    """First m1, then m2."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a2 * a1 + b2 * d1, a2 * b1 + b2 * e1, a2 * c1 + b2 * f1 + c2,
            d2 * a1 + e2 * d1, d2 * b1 + e2 * e1, d2 * c1 + e2 * f1 + f2)


def apply(m, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]


def transform(g, m):
    return affinity.affine_transform(g, [m[0], m[1], m[3], m[4], m[2], m[5]])


def _r(v: float) -> float:
    return round(float(v), 2)


def _geom(wkb) -> shapely.Geometry | None:
    if not wkb:
        return None
    try:
        g = shapely.from_wkb(wkb)
    except Exception:  # noqa: BLE001 - a geometry the driver or GEOS cannot read is simply not drawn
        return None
    return None if g is None or g.is_empty else g


def _ring(g) -> list[list[float]]:
    """The outline of a geometry as a list of points (largest polygon part, exterior ring, not closed)."""
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    if g.geom_type == "GeometryCollection":
        polys = [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")]
        return _ring(max(polys, key=lambda p: p.area)) if polys else []
    if g.geom_type != "Polygon":
        return []
    pts = [[_r(x), _r(y)] for x, y in g.exterior.coords[:-1]]
    out: list[list[float]] = []
    for p in pts:                                  # no repeated points (contract)
        if not out or out[-1] != p:
            out.append(p)
    return out if len(out) >= 3 else []


def _rect(g, pad: float = 0.0) -> dict | None:
    x0, y0, x1, y1 = g.bounds
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w, h = max(x1 - x0, 0.5), max(y1 - y0, 0.5)   # a line (a window drawn as one stroke) still gets a visible box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return dict(x=_r(cx - w / 2), y=_r(cy - h / 2), w=_r(w), h=_r(h))


# ---------------------------------------------------------------------------------------------- the model
@dataclass
class Base:
    """The page the drawing is shown on."""
    file_id: str
    page_no: int
    format: str
    name: str | None
    image: str | None     # relative to the store directory; None for a CAD page that was never rendered: the
                          # percent space is then its extent, and plan.svg is all there is to show
    w: int
    h: int
    to_image: Affine
    frame: Affine | None  # file coordinates -> metres, when usable for placing entities
    frame_method: str | None
    registered: bool      # the frame is the drawing's shared one (not a per-page scale)

    @property
    def to_pct(self) -> Affine:
        return mul((100.0 / self.w, 0, 0, 0, 100.0 / self.h, 0), self.to_image)


@dataclass
class Model:
    key: str
    id: str
    files: list[dict]
    base: Base | None
    rooms: list[dict] = field(default_factory=list)       # RoomPoly + entity_id, sources, agreement ...
    items: list[dict] = field(default_factory=list)       # doors, windows, fixtures: box + ids
    apartments: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    by_entity: dict[int, dict] = field(default_factory=dict)
    storey: "Storey | None" = None                        # the storey shown (a drawing with an IFC model)
    storeys: list = field(default_factory=list)           # every storey view of the drawing, in library order
    elsewhere: dict[int, dict] = field(default_factory=dict)   # entities of the other storeys: {kind, storey}
    n_dimensions: int = 0                                      # printed dimensions an answer may cite
    dimensions: list = field(default_factory=list)             # ... each with its box and line in percent
    extent: dict | None = None                                 # the plan's own width and depth in metres (walls, else rooms)
    shapes: list = field(default_factory=list)            # (style, geometry in percent): footprints of the storey's elements
    pages: set | None = None                              # a storey view: the (file_id, page_no) of the model that show it,
    page_files: set = field(default_factory=set)          # and the files that show it on every page; None: any page
    linework: tuple[str, int | str, Affine] | None = None  # (file_id, page or space, file coords -> percent)
    frames: dict[tuple[str, int], Affine] = field(default_factory=dict)   # pages in the drawing's shared frame
    walls: list = field(default_factory=list)             # an image plan: its wall polygons in percent (raster_plan)
    nominal: bool = False                                 # an image plan without a stated scale: metres are an estimate
    sheet: tuple[int, int] | None = None                  # a PDF of several pages: (the page shown, pages of the file)

    @property
    def vector_scan(self) -> bool:
        """An image plan with a vector model of its own (walls or room polygons): plan.svg is drawn from it."""
        b = self.base
        return (b is not None and self.linework is None and b.format in ("png", "jpg") and b.image is not None
                and (bool(self.walls) or any(len(r["points"]) >= 3 for r in self.rooms)))

    def on_page(self, file_id: str, page_no: int, box: list[float] | None) -> dict | None:
        """A box in the coordinates of one of the drawing's files as a rectangle in percent of the page shown:
        directly for the base page, through the shared frame for another registered page; else None."""
        b = self.base
        if b is None or not box or len(box) < 4 or any(v is None for v in box[:4]):
            return None
        if self.pages is not None and file_id not in self.page_files and (file_id, page_no) not in self.pages:
            return None                                   # a page of another storey: not on this plan
        if (file_id, page_no) == (b.file_id, b.page_no):
            t = b.to_pct
        elif b.frame is not None and b.registered and (file_id, page_no) in self.frames:
            t = mul(mul(b.to_pct, inv(b.frame)), self.frames[(file_id, page_no)])
        else:
            return None
        return _rect(transform(make_box(*box[:4]), t))
    signature: tuple = ()

    @property
    def upload(self) -> bool:
        return any(str(f["project_id"]).startswith("upload/") for f in self.files)

    @property
    def status(self) -> str:
        """ready as soon as one file is parsed (the others join when they are); processing while nothing is but
        something still may be; failed when nothing is left to wait for."""
        if any(f["status"] in ("parsed", "interpreted") for f in self.files):
            return "ready"
        return "processing" if any(f["status"] == "registered" and not f["error"] for f in self.files) else "failed"

    @property
    def placed(self) -> int:
        return sum(1 for r in self.rooms if r["points"]) + sum(1 for i in self.items if i.get("box"))

    @property
    def synthetic(self) -> bool:
        """The page shown is drawn by us from the model (no file page behind it)."""
        return self.storey is not None and self.base is not None and self.base.image is None

    @property
    def title(self) -> str:
        name = title_of(Path(self.files[0]["path"]).stem if self.upload else self.key)
        return f"{name} · {self.storey.name}"[:160] if self.storey is not None and self.storey.name else name


# ---------------------------------------------------------------------------------------------- storeys
# Classes whose footprint is not part of a floor plan's linework: spaces are the rooms, openings are holes,
# coverings (ceilings, floor finishes) and the site would hide everything else.
NO_SHAPE = (r"^Ifc(Space|SpatialZone|Zone|OpeningElement|OpeningStandardCase|Site|Building|BuildingStorey|Project|"
            r"Annotation|Grid|VirtualElement|Covering|GeographicElement)$|(Type|Style)$")
SOLID = re.compile(r"^Ifc(Wall|Column|CurtainWall|Member|Plate|Pile|Footing)")
MAX_SHAPES = 6000
# The storey of an entity: its own, else the one its source file shows.
ENTITY_STOREY = """coalesce(e.storey, (select min(sf.storey) from jstore.entity_source es join jstore.source_file sf using (file_id)
                                       where es.entity_id = e.entity_id and sf.storey is not null))"""


@dataclass
class Storey:
    """One storey of a drawing's IFC model(s): the IfcBuildingStorey rows with the same name, or (name '') the
    things assigned to no storey."""
    name: str
    slug: str = ""
    norms: frozenset = frozenset()
    elevation: float | None = None
    members: list = field(default_factory=list)      # (file_id, storey guid, page_no or None)
    n_rooms: int = 0
    n_items: int = 0
    n_shapes: int = 0
    plan: bool = False                               # a registered plan page of this storey exists

    @property
    def occupied(self) -> bool:
        return self.n_rooms + self.n_items > 0

    def matches(self, name: str | None) -> bool:
        return bool(self.norms & storey_norms(name)) if self.name else not storey_norms(name)


def storey_of(storeys: list[Storey], name: str | None) -> Storey | None:
    """The storey a name means; a name no storey carries (or none) means the unassigned one, when there is one."""
    norms = storey_norms(name)
    hit = next((s for s in storeys if s.name and s.norms & norms), None) if norms else None
    return hit or next((s for s in storeys if not s.name), None)


def default_storey(storeys: list[Storey]) -> Storey | None:
    """The storey a model opens on: the lowest one with rooms, else the first listed."""
    return next((s for s in storeys if s.n_rooms), None) or (storeys[0] if storeys else None)


def storey_index(db: DB, key: str | None = None) -> dict[str, list[Storey]]:
    """Per drawing with an IFC model: its storeys that have something to show (rooms, doors, windows, fixtures or
    element footprints), occupied ones first, each group from the lowest up.  For one drawing, or for the library."""
    cond = "f.drawing_key = :k" if key is not None else "f.project_id not like 'upload/%'"
    kw = dict(k=key) if key is not None else {}
    rows = db.run(f"""select f.drawing_key, st.file_id, st.guid, st.name, st.attrs->>'Elevation' as elevation, pg.page_no
                      from jstore.source_file f join jstore.ifc_element st on st.file_id = f.file_id and st.ifc_class = 'IfcBuildingStorey'
                      left join jstore.page pg on pg.file_id = st.file_id and pg.meta->>'guid' = st.guid
                      where f.format = 'ifc' and {cond} order by f.drawing_key, st.file_id, pg.page_no nulls last, st.guid""", **kw)
    shapes = db.run(f"""select f.drawing_key, e.file_id, e.storey_guid, count(*) as n
                        from jstore.source_file f join jstore.ifc_element e using (file_id)
                        join jstore.ifc_geometry g on g.file_id = e.file_id and g.guid = e.guid
                        where f.format = 'ifc' and {cond} and g.footprint is not null and e.ifc_class !~ :skip
                        group by 1, 2, 3""", skip=NO_SHAPE, **kw)
    if not rows and not shapes:
        return {}
    ents = db.run(f"""select e.drawing_key, {ENTITY_STOREY} as storey, e.kind = 'room' as room, count(*) as n
                      from jstore.entity e
                      where e.status = 'active' and e.kind <> 'apartment' and e.drawing_key in
                            (select f.drawing_key from jstore.source_file f where f.format = 'ifc' and {cond})
                      group by 1, 2, 3""", **kw)
    plans = db.run(f"""select distinct f.drawing_key, f.storey from jstore.source_file f
                       where f.format <> 'ifc' and f.storey is not null and {cond} and exists (
                         select 1 from jstore.page p join jstore.frame fr using (file_id, page_no)
                         where p.file_id = f.file_id and p.image is not null and fr.method not in ('scale_text', 'pixel_scale', 'nominal_scale'))""", **kw)
    out: dict[str, list[Storey]] = {}
    by_guid: dict[tuple[str, str], Storey] = {}
    for r in rows:
        sts = out.setdefault(r["drawing_key"], [])
        name = (r["name"] or "").strip() or f"Storey {len(sts) + 1}"
        st = next((s for s in sts if s.norms & storey_norms(name)), None)
        if st is None:
            st = Storey(name=name, norms=storey_norms(name) or frozenset({name}))
            sts.append(st)
        try:
            elev = float(r["elevation"]) if r["elevation"] is not None else None
        except ValueError:
            elev = None
        if st.elevation is None and elev is not None and math.isfinite(elev):
            st.elevation = elev
        st.members.append((r["file_id"], r["guid"], r["page_no"]))
        by_guid[(r["file_id"], r["guid"])] = st

    def loose(dk: str) -> Storey:
        sts = out.setdefault(dk, [])
        st = next((s for s in sts if not s.name), None)
        if st is None:
            st = Storey(name="")
            sts.append(st)
        return st

    for r in shapes:
        st = by_guid.get((r["file_id"], r["storey_guid"])) or loose(r["drawing_key"])
        st.n_shapes += r["n"]
        if not st.name and (r["file_id"], None, None) not in st.members:
            st.members.append((r["file_id"], None, None))
    for r in ents:
        st = storey_of(out.get(r["drawing_key"], []), r["storey"])
        if st is None or (st.name and not storey_norms(r["storey"])):
            st = loose(r["drawing_key"])
        if r["room"]:
            st.n_rooms += r["n"]
        else:
            st.n_items += r["n"]
    for r in plans:
        st = next((s for s in out.get(r["drawing_key"], []) if s.name and s.matches(r["storey"])), None)
        if st is not None:
            st.plan = True
    for dk in list(out):
        shown = [s for s in out[dk] if s.n_rooms + s.n_items + s.n_shapes > 0]
        shown.sort(key=lambda s: (not s.occupied, not s.name, s.elevation is None, s.elevation or 0.0))
        used: set[str] = set()
        for s in sorted(shown, key=lambda s: (not s.name, s.members[0][2] if s.members and s.members[0][2] is not None else 1 << 30)):
            slug = storey_slug(s.name) or ("unassigned" if not s.name else f"s{s.members[0][2] or 0}")
            n, base = 2, slug
            while slug in used:                       # two names with one spelling in URLs
                slug, n = f"{base}-{n}", n + 1
            used.add(slug)
            s.slug = slug
        if shown:
            out[dk] = shown
        else:
            del out[dk]
    return out


def signature(db: DB, key: str) -> tuple | None:
    """Changes whenever the drawing's files, pages or interpretation change; None for an unknown drawing."""
    r = db.one("""select (select count(*) from jstore.source_file where drawing_key = :k) files,
                         (select string_agg(file_id || status, ',' order by file_id) from jstore.source_file where drawing_key = :k) st,
                         (select count(*) || '/' || coalesce(max(entity_id), 0) || '/' || count(*) filter (where status = 'active')
                            from jstore.entity where drawing_key = :k) ents,
                         (select count(*) from jstore.issue where drawing_key = :k) issues,
                         (select count(p.image) || '/' || count(fr.m) from jstore.source_file f join jstore.page p using (file_id)
                            left join jstore.frame fr using (file_id, page_no) where f.drawing_key = :k) pages""", k=key)
    return None if not r or not r["files"] else tuple(r.values())


def pick_base(db: DB, key: str, only: set[str] | None = None, page: tuple[str, int] | None = None) -> Base | None:
    """The page to show: a raster or PDF page that is registered in the drawing's frame, else the CAD render,
    else the page most entities were read from, else the first rendered page.  With `only` (file ids: the plan
    files of one storey) the page must be one of those files and registered in the shared frame, else None."""
    pages = db.run("""
        select p.file_id, p.page_no, p.name, p.image, p.image_w, p.image_h, p.to_image, f.format, fr.m, fr.method,
               (select count(*) from jstore.entity_source s where s.file_id = p.file_id and s.page_no = p.page_no) as n_src,
               (select count(distinct e.entity_id) from jstore.entity_source s join jstore.entity e using (entity_id)
                 where s.file_id = p.file_id and s.page_no = p.page_no and e.kind = 'room' and e.status = 'active') as n_rooms
        from jstore.page p join jstore.source_file f using (file_id)
        left join jstore.frame fr on fr.file_id = p.file_id and fr.page_no = p.page_no
        where f.drawing_key = :k and p.image is not null and p.to_image is not null and p.image_w > 0 and p.image_h > 0
        order by p.file_id, p.page_no""", k=key)
    pages = [p for p in pages if len(p["to_image"] or []) == 6 and inv(p["to_image"])]
    if page is not None:                 # one given page of a file (a page of a PDF of several, shown on its own)
        pages = [p for p in pages if (p["file_id"], p["page_no"]) == tuple(page)]
        if not pages:
            return None
    if only is not None:
        pages = [p for p in pages if p["file_id"] in only and p["m"] and len(p["m"]) == 6 and inv(p["m"])
                 and p["method"] not in ("scale_text", "pixel_scale", "nominal_scale")]
        if not pages:
            return None
    if not pages:
        return _unrendered_cad(db, key)
    shared = any(p["m"] and p["method"] not in ("scale_text", "pixel_scale", "nominal_scale") for p in pages) or bool(db.one(
        """select 1 from jstore.frame fr join jstore.source_file f using (file_id)
           where f.drawing_key = :k and fr.method not in ('scale_text', 'pixel_scale', 'nominal_scale') limit 1""", k=key))

    def usable(p: dict) -> bool:  # a scale-only frame places nothing relative to a drawing that has a shared frame
        return bool(p["m"]) and len(p["m"]) == 6 and inv(p["m"]) is not None and (p["method"] not in ("scale_text", "pixel_scale", "nominal_scale") or not shared)

    def score(p: dict) -> tuple:
        framed = usable(p)
        tier = 3 if framed and p["format"] not in CAD else 2 if framed else 1 if p["n_src"] else 0
        # a storey's plan: of two registered pages the PDF, whose strokes plan.svg can redraw
        # pages that share no frame (the sheets of one PDF set): the one with the most rooms, whether or not it
        # states a scale; a set without rooms opens as before
        rooms_first = 0 if shared or only is not None else p["n_rooms"]
        return (rooms_first, tier, p["n_src"], p["format"] not in CAD, only is not None and p["format"] == "pdf", -p["page_no"])
    p = max(pages, key=score)
    return Base(file_id=p["file_id"], page_no=p["page_no"], format=p["format"], name=p["name"], image=p["image"],
                w=int(p["image_w"]), h=int(p["image_h"]), to_image=tuple(p["to_image"]),
                frame=tuple(p["m"]) if usable(p) else None, frame_method=p["method"] if usable(p) else None,
                registered=usable(p) and p["method"] not in ("scale_text", "pixel_scale", "nominal_scale"))


def _unrendered_cad(db: DB, key: str, width: int = 2000) -> Base | None:
    """A drawing without any page image but with CAD geometry in a frame: its model space, fitted into a page of
    our own (y up in the file, down on the page).  The extent is that of the drawing's main body: neither a stray
    point nor a small block of entities far away shrinks the drawing to a dot."""
    p = db.one("""select fr.file_id, fr.page_no, fr.m, fr.method, pg.name, f.format
                  from jstore.frame fr join jstore.source_file f using (file_id)
                  join jstore.page pg on pg.file_id = fr.file_id and pg.page_no = fr.page_no
                  where f.drawing_key = :k and f.format in ('dxf', 'dwg') and fr.method not in ('scale_text', 'pixel_scale', 'nominal_scale')
                  order by fr.page_no, f.format, fr.file_id limit 1""", k=key)
    if p is None or len(p["m"]) != 6 or inv(p["m"]) is None:
        return None
    # the extent of the main body of the drawing: entities further from the median centre than four times the
    # distance nine in ten lie within are left out (a second block of details or a title block drawn far from the
    # plan, more than half a percent of the entities, would otherwise shrink the plan to a speck in a corner), then
    # the outermost half percent of what remains
    e = db.one("""with c as (select (st_xmin(geom) + st_xmax(geom)) / 2 as cx, (st_ymin(geom) + st_ymax(geom)) / 2 as cy, geom
                             from jstore.cad_entity where file_id = :f and space = :s and geom is not null),
                       mid as (select percentile_cont(0.5) within group (order by cx) as mx,
                                      percentile_cont(0.5) within group (order by cy) as my from c),
                       d as (select c.geom, sqrt((c.cx - mid.mx) ^ 2 + (c.cy - mid.my) ^ 2) as dist from c, mid),
                       reach as (select 4 * percentile_cont(0.9) within group (order by dist) as r from d)
                  select percentile_cont(0.005) within group (order by st_xmin(geom)) as x0,
                         percentile_cont(0.995) within group (order by st_xmax(geom)) as x1,
                         percentile_cont(0.005) within group (order by st_ymin(geom)) as y0,
                         percentile_cont(0.995) within group (order by st_ymax(geom)) as y1
                  from d, reach where reach.r is null or reach.r <= 0 or d.dist <= reach.r""",
               f=p["file_id"], s=p["name"] or "model")
    if not e or any(e[k] is None for k in ("x0", "x1", "y0", "y1")) or e["x1"] <= e["x0"] or e["y1"] <= e["y0"]:
        return None
    pad = 0.03 * max(e["x1"] - e["x0"], e["y1"] - e["y0"])
    x0, x1, y0, y1 = e["x0"] - pad, e["x1"] + pad, e["y0"] - pad, e["y1"] + pad
    scale = width / (x1 - x0)
    height = int(min(max((y1 - y0) * scale, width / 8), width * 4))
    return Base(file_id=p["file_id"], page_no=p["page_no"], format=p["format"], name=p["name"], image=None, w=width, h=height,
                to_image=(scale, 0.0, -x0 * scale, 0.0, -scale, y1 * scale), frame=tuple(p["m"]), frame_method=p["method"],
                registered=True)


# ---- one page of a PDF of several: what the drawing shows is what its counts and answers are about
OFF_PAGE_SQL = """exists (select 1 from jstore.entity_source s where s.entity_id = e.entity_id and s.file_id = :f)
                  and not exists (select 1 from jstore.entity_source s where s.entity_id = e.entity_id
                                  and s.file_id = :f and s.page_no = :p)"""


def off_page(sources: list[dict], base: Base) -> bool:
    """The entity was read from the file shown, but only on other pages of it (OFF_PAGE_SQL says the same in SQL)."""
    mine = [s for s in sources if s["file_id"] == base.file_id]
    return bool(mine) and not any(s["page_no"] == base.page_no for s in mine)


def sheet_of(db: DB, base: Base | None) -> tuple[int, int] | None:
    """(the page shown, the pages of its file) for a PDF of more than one page, else None.  Pages count as the PDF
    numbers them when the store kept the numbers, else in the order stored."""
    if base is None or base.format != "pdf":
        return None
    pages = db.run("select page_no, name from jstore.page where file_id = :f order by page_no", f=base.file_id)
    if len(pages) < 2:
        return None
    numbered = all(str(p["name"] or "").isdigit() for p in pages)
    shown = next((int(p["name"]) if numbered else n for n, p in enumerate(pages, 1) if p["page_no"] == base.page_no), None)
    if shown is None:
        return None
    return shown, max(len(pages), max(int(p["name"]) for p in pages) if numbered else 0)


PAGE_SLUG = re.compile(r"^p(\d{1,4})$")          # the slug of one page of a PDF of several: p<position, from 1>
PAGE_CONTENT = ("room", "door", "window", "dimension")
PAGE_MIN_ITEMS = 3                              # a page without rooms needs this many doors, windows and dimensions


def page_of_slug(db: DB, key: str, slug: str | None) -> tuple[str, int] | None:
    """(file, page_no) a page slug names: the n-th page of the PDF the drawing is shown from."""
    hit = PAGE_SLUG.match(slug or "")
    base = pick_base(db, key) if hit else None
    if base is None or base.format != "pdf":
        return None
    pages = db.run("select page_no from jstore.page where file_id = :f order by page_no", f=base.file_id)
    n = int(hit.group(1))
    return (base.file_id, pages[n - 1]["page_no"]) if 1 <= n <= len(pages) and len(pages) > 1 else None


def other_pages(db: DB, key: str) -> list[dict]:
    """The other pages with content of a drawing shown as one page of a PDF of several: [{slug, sheet (page,
    pages), counts per kind}], in page order.  Content: a room, or PAGE_MIN_ITEMS doors, windows and dimensions
    read on the page.  Each is a drawing of its own (Models.get with the slug); the page the drawing opens on
    keeps the drawing's id and is not listed here."""
    base = pick_base(db, key)
    if sheet_of(db, base) is None:
        return []
    pages = db.run("""select p.page_no, p.image is not null and p.to_image is not null as shown from jstore.page p
                      where p.file_id = :f order by p.page_no""", f=base.file_id)
    on = db.run("""select s.page_no, e.kind, count(distinct e.entity_id) as n from jstore.entity e
                   join jstore.entity_source s using (entity_id)
                   where e.drawing_key = :k and e.status = 'active' and s.file_id = :f and e.kind = any(:kinds)
                   group by 1, 2""", k=key, f=base.file_id, kinds=list(PAGE_CONTENT))
    per: dict[int, dict[str, int]] = {}
    for r in on:
        per.setdefault(r["page_no"], {})[r["kind"]] = int(r["n"])
    out = []
    for n, p in enumerate(pages, 1):
        c = per.get(p["page_no"], {})
        if p["page_no"] == base.page_no or not p["shown"] or not (c.get("room") or sum(c.values()) >= PAGE_MIN_ITEMS):
            continue
        scoped = page_counts(db, key, (base.file_id, p["page_no"]))
        if scoped is not None:
            out.append(dict(slug=f"p{n}", sheet=scoped[0], counts=scoped[1]))
    return out


def page_counts(db: DB, key: str, page: tuple[str, int] | None = None) -> tuple[tuple[int, int], dict[str, int]] | None:
    """For a drawing shown as one page of a PDF of several: (page shown, pages) and the active entities per kind
    that are on it (or in another file of the drawing); None for every other drawing.  `page`: that page instead
    of the one the drawing opens on."""
    base = pick_base(db, key, page=page)
    sheet = sheet_of(db, base)
    if sheet is None:
        return None
    rows = db.run(f"""select e.kind, count(*) as n from jstore.entity e
                       where e.drawing_key = :k and e.status = 'active' and not ({OFF_PAGE_SQL}) group by 1""",
                  k=key, f=base.file_id, p=base.page_no)
    return sheet, {r["kind"]: int(r["n"]) for r in rows}


_L1_GEOM = {"cad_entity": "select handle as key, geom from jstore.cad_entity where file_id = :f and handle = any(cast(:k as text[]))",
            "pdf_path": "select id::text as key, geom from jstore.pdf_path where file_id = :f and id::text = any(cast(:k as text[]))"}


def _synthetic(file_id: str, page_no: int, name: str, geoms: list, width: int = 2000) -> Base | None:
    """A page of our own for a storey that has no plan file: the extent of its geometry (metres, the drawing's
    frame) fitted into `width` pixels, north up (y up in the model, down on the page).  Things far away from
    everything else (a stray object at the origin of a georeferenced model) do not count for the extent."""
    boxes = [g.bounds for g in geoms if g is not None and not g.is_empty]
    boxes = [b for b in boxes if all(math.isfinite(v) for v in b)]
    if not boxes:
        return None
    if len(boxes) >= 8:
        cx = sorted((b[0] + b[2]) / 2 for b in boxes)[len(boxes) // 2]
        cy = sorted((b[1] + b[3]) / 2 for b in boxes)[len(boxes) // 2]
        dist = sorted(math.hypot((b[0] + b[2]) / 2 - cx, (b[1] + b[3]) / 2 - cy) for b in boxes)
        reach = 4.0 * dist[int(len(dist) * 0.9)] + 5.0
        near = [b for b in boxes if math.hypot((b[0] + b[2]) / 2 - cx, (b[1] + b[3]) / 2 - cy) <= reach]
        boxes = near or boxes
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    span = max(x1 - x0, y1 - y0, 1.0)
    pad = 0.04 * span
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    if x1 - x0 < span / 4:                           # a single wall: keep the page from becoming a sliver
        x0, x1 = (x0 + x1) / 2 - span / 8, (x0 + x1) / 2 + span / 8
    if y1 - y0 < span / 4:
        y0, y1 = (y0 + y1) / 2 - span / 8, (y0 + y1) / 2 + span / 8
    scale = width / (x1 - x0)
    height = max(1, int(round((y1 - y0) * scale)))
    return Base(file_id=file_id, page_no=page_no, format="ifc", name=name, image=None, w=width, h=height,
                to_image=(scale, 0.0, -x0 * scale, 0.0, -scale, y1 * scale), frame=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
                frame_method="native", registered=True)


def _shapes(db: DB, st: Storey, frames: dict) -> list[tuple[str, shapely.Geometry]]:
    """Footprints of the storey's elements in metres of the drawing's frame, as (style, geometry)."""
    out = []
    for fid in dict.fromkeys(m[0] for m in st.members):
        guids = [m[1] for m in st.members if m[0] == fid and m[1] is not None]
        where = "e.storey_guid = any(cast(:g as text[]))" if st.name else "e.storey_guid is null"
        rows = db.run(f"""select e.ifc_class, g.footprint as geom from jstore.ifc_element e
                          join jstore.ifc_geometry g on g.file_id = e.file_id and g.guid = e.guid
                          where e.file_id = :f and {where} and g.footprint is not null and e.ifc_class !~ :skip
                          order by e.step_id""", f=fid, skip=NO_SHAPE, **(dict(g=guids) if st.name else {}))
        page = next((m[2] for m in st.members if m[0] == fid and m[2] is not None), 0)
        frame = frames.get((fid, page)) or next((v for k, v in frames.items() if k[0] == fid), None)
        for r in rows:
            g = _geom(r["geom"])
            if g is None:
                continue
            if frame is not None and frame != (1.0, 0.0, 0.0, 0.0, 1.0, 0.0):
                g = transform(g, frame)
            out.append(("solid" if SOLID.match(r["ifc_class"]) else "door" if r["ifc_class"].startswith("IfcDoor")
                        else "window" if r["ifc_class"].startswith("IfcWindow") else "light", g))
    return out


def load(db: DB, key: str) -> dict | None:
    """Everything build() reads about a drawing that does not depend on the storey shown."""
    files = db.run("""select file_id, path, format, bytes, status, storey, project_id, split_part(error, E'\\n', 1) as error,
                             meta->>'scale' as scale, ingested_at from jstore.source_file where drawing_key = :k
                      order by format, path""", k=key)
    if not files:
        return None
    d = dict(files=files)
    store.set_scope_superuser(db, [key], [f["file_id"] for f in files])
    d["rooms"] = db.run("""select entity_id, storey, number, label, room_type, apartment, area_m2, area_source, is_indoor,
                                  n_sources, agreement, is_duplicate, geom from q.rooms order by number nulls last, entity_id""")
    if any(f["format"] in ("png", "jpg") for f in files):
        # enclosed spaces without a label that are not rooms (a strip beside a dimension line, the gap to a page
        # border): left out here, so the document, the plan and the 3D view agree whether or not the store was pruned
        try:
            false, inside = space_guard.of_drawing(db, key)
        except Exception:  # noqa: BLE001 - the guard never costs a drawing
            false, inside = {}, {}
        d["rooms"] = [dict(r) | (dict(room_type="room", is_indoor=True) if r["entity_id"] in inside else {})
                      for r in d["rooms"] if r["entity_id"] not in false]
    d["items"] = db.run("""select entity_id, storey, kind, ifc_class, tag, label, room, apartment, width_m, height_m, n_sources,
                                  is_duplicate, duplicate_of, geom from q.items order by kind, entity_id""")
    # printed dimensions: not drawn or counted with the items, but an answer may cite them (boxes of its evidence)
    d["dimensions"] = db.run("""select d.entity_id, d.kind as dim_kind, d.text, d.value_m, d.unit, d.orientation, d.a_m, d.b_m,
                                       d.room, d.page_no, d.file_id, d.geom, e.attrs->'px' as px
                                from q.dimensions d join jstore.entity e using (entity_id) order by d.entity_id""")
    # the printed (else measured: the smallest rotated rectangle around the outline) length and width of each room
    d["room_sizes"] = {}
    if db.one("select to_regclass('q.room_sizes') as v")["v"]:
        d["room_sizes"] = {r["entity_id"]: r for r in db.run(
            "select entity_id, length_m, width_m, size_source, size_text from q.room_sizes where length_m is not null")}
    d["apartments"] = db.run("""select entity_id, storey, apartment, label, rooms, net_area_m2::float8 as net_area_m2
                                from q.apartments order by apartment nulls last, entity_id""")
    d["issues"] = db.run("select issue_id, entity_id, property, kind, detail, status from q.issues order by issue_id limit 1000")
    srcs: dict[int, list[dict]] = {}
    for s in db.run("select entity_id, file_id, page_no, ref_table, ref_key, role, bbox from q.entity_sources"):
        srcs.setdefault(s["entity_id"], []).append(s)
    values: dict[int, list[dict]] = {}
    for s in db.run("""select entity_id, property, value, value_text, unit, method, rank, format from q.sources
                       where kind = 'room' and property = 'net_area_m2' order by entity_id, format"""):
        values.setdefault(s["entity_id"], []).append(dict(value=s["value"], unit=s["unit"], method=s["method"],
                                                          rank=s["rank"], format=s["format"]))
    d.update(srcs=srcs, values=values, frames={}, walls=[])
    if any(f["format"] in ("png", "jpg") for f in files) and db.one("select to_regclass('q.walls') as v")["v"]:
        d["walls"] = db.run("select entity_id, geom from q.walls order by entity_id")
    for fr in db.run("""select fr.file_id, fr.page_no, fr.m from jstore.frame fr join jstore.source_file f using (file_id)
                        where f.drawing_key = :k and fr.method not in ('scale_text', 'pixel_scale', 'nominal_scale')""", k=key):
        if len(fr["m"]) == 6:
            d["frames"][(fr["file_id"], fr["page_no"])] = tuple(fr["m"])
    return d


def build(db: DB, key: str, storey: Storey | None = None, storeys: list[Storey] | None = None,
          data: dict | None = None, page_slug: str | None = None) -> Model | None:
    """The whole drawing on its page, or (a drawing with an IFC model) one storey of it: on the plan page of that
    storey when a file shows it, else on a page drawn from the model."""
    data = data or load(db, key)
    if data is None:
        return None
    files, srcs, values = data["files"], data["srcs"], data["values"]
    rooms, items = [dict(r) for r in data["rooms"]], [dict(i) for i in data["items"]]
    m = Model(key=key, id=drawing_id(key), files=files, base=None, signature=signature(db, key) or (),
              storey=storey, storeys=list(storeys or []))
    m.apartments = [{k: v for k, v in a.items() if k != "storey"} for a in data["apartments"]]
    m.issues = data["issues"]
    m.frames = dict(data["frames"])
    if storey is None:
        if page_slug is not None:            # one page of a PDF of several, as a drawing of its own
            page = page_of_slug(db, key, page_slug)
            m.base = pick_base(db, key, page=page) if page is not None else None
            if m.base is None:
                return None
            m.id = drawing_id(key) + STOREY_SEP + page_slug
        else:
            m.base = pick_base(db, key)
        m.sheet = sheet_of(db, m.base)
        if m.sheet is not None:          # one page of several: rooms, doors and fixtures of the other pages are not on it
            rooms = [r for r in rooms if not off_page(srcs.get(r["entity_id"], []), m.base)]
            items = [i for i in items if not off_page(srcs.get(i["entity_id"], []), m.base)]
    else:
        m.id = drawing_id(key) + STOREY_SEP + storey.slug

        def name_of(e: dict) -> str | None:
            if e["storey"]:
                return e["storey"]
            return next((f["storey"] for s in srcs.get(e["entity_id"], []) for f in files
                         if f["file_id"] == s["file_id"] and f["storey"]), None)

        def here(e: dict) -> bool:
            other = storey_of(m.storeys, name_of(e))
            if other is storey or other is None:
                return True
            m.elsewhere[e["entity_id"]] = dict(kind=e.get("kind") or "room", storey=other.name or "no storey")
            return False
        rooms, items = [r for r in rooms if here(r)], [i for i in items if here(i)]
        units = {r["apartment"] for r in rooms if r["apartment"]}
        for a in data["apartments"]:
            if a["apartment"] not in units and not here(dict(a, kind="apartment")):
                m.apartments = [x for x in m.apartments if x["entity_id"] != a["entity_id"]]
        mine = {e["entity_id"] for e in rooms + items} | {a["entity_id"] for a in m.apartments}
        m.issues = [i for i in m.issues if i["entity_id"] is None or i["entity_id"] in mine]
        plan_files = {f["file_id"] for f in files if f["format"] != "ifc" and f["storey"] and storey.name and storey.matches(f["storey"])}
        m.base = pick_base(db, key, only=plan_files) if plan_files else None
        # pages that show this storey: its own pages of the model, the plan files of this storey, and files that
        # name no storey; evidence on any other page is not drawn here
        m.pages = {(fid, page) for fid, _, page in storey.members if page is not None}
        m.page_files = {f["file_id"] for f in files if f["format"] != "ifc" and (not f["storey"] or f["file_id"] in plan_files)}
        if m.base is None:
            shapes = _shapes(db, storey, m.frames)
            if len(shapes) > MAX_SHAPES:              # the largest ones; a drawing of 20 000 pipe fittings stays readable
                shapes.sort(key=lambda sg: -math.hypot(sg[1].bounds[2] - sg[1].bounds[0], sg[1].bounds[3] - sg[1].bounds[1]))
                shapes = shapes[:MAX_SHAPES]
            fid, _, page = next((x for x in storey.members if x[2] is not None), None) or \
                (storey.members[0] if storey.members else (next(f["file_id"] for f in files if f["format"] == "ifc"), None, None))
            geoms = [g for g in (_geom(e["geom"]) for e in rooms + items) if g is not None] + [g for _, g in shapes]
            m.base = _synthetic(fid, -1 if page is None else page, storey.name or "no storey", geoms)
            if m.base is not None:
                t, tol = m.base.to_pct, 100.0 / 4000
                for style, g in shapes:
                    g = transform(g, t)
                    if len(shapely.get_coordinates(g)) > 12:
                        g = g.simplify(tol)
                    if not g.is_empty:
                        m.shapes.append((style, g))
    base = m.base

    # ---- geometry in percent of the base image
    to_pct = base.to_pct if base else None
    frame_to_pct = mul(to_pct, inv(base.frame)) if base and base.frame else None
    on_base: dict[int, list[dict]] = {}
    outline_keys: dict[str, set[str]] = {}
    for eid, ss in srcs.items():
        if base is None:
            break
        mine = [s for s in ss if s["file_id"] == base.file_id and s["page_no"] == base.page_no]
        on_base[eid] = mine
        for s in mine:
            if s["role"] == "outline" and s["ref_table"] in _L1_GEOM and s["ref_key"] is not None:
                outline_keys.setdefault(s["ref_table"], set()).add(str(s["ref_key"]))

    def by_frame(eid: int) -> bool:
        if frame_to_pct is None:
            return False
        if m.synthetic:                 # a page laid out in the drawing's frame: everything is placed by its geometry
            return True
        if on_base.get(eid):
            return True
        return base.registered and not any(s["file_id"] == base.file_id for s in srcs.get(eid, []))

    outlines: dict[tuple[str, str], shapely.Geometry] = {}
    need_l1 = [e["entity_id"] for e in rooms + items if not (by_frame(e["entity_id"]) and e["geom"])]
    if base is not None and need_l1:
        for table, keys in outline_keys.items():
            for r in db.run(_L1_GEOM[table], f=base.file_id, k=sorted(keys)):
                g = _geom(r["geom"])
                if g is not None:
                    outlines[(table, r["key"])] = g
        # sources stored without a box take the box of the Layer 1 primitive they point at
        bare = [s for eid in need_l1 for s in on_base.get(eid, [])
                if not s["bbox"] and s["ref_table"] in store.L1 and s["ref_key"] is not None]
        refs = [dict(file_id=s["file_id"], ref_table=s["ref_table"], ref_key=s["ref_key"], page_no=s["page_no"]) for s in bare]
        for s, r in zip(bare, store.resolve_refs(db, refs) if refs else []):
            s["bbox"] = r.get("box")

    def place(e: dict) -> shapely.Geometry | None:
        """The entity's shape in percent of the base image, or None."""
        eid = e["entity_id"]
        g = _geom(e.pop("geom", None))
        if g is not None and by_frame(eid):
            return transform(g, frame_to_pct)
        mine = on_base.get(eid) or []
        if not mine or to_pct is None:
            return None
        for s in mine:  # the Layer 1 outline itself, when it is one
            o = outlines.get((s["ref_table"], str(s["ref_key"]))) if s["role"] == "outline" else None
            if o is not None and o.geom_type in ("Polygon", "MultiPolygon"):
                return transform(o, to_pct)
        shaped = [s for s in mine if s["role"] in ("outline", "symbol") and s["bbox"]] or [s for s in mine if s["bbox"]]
        boxes = [s["bbox"] for s in shaped if len(s["bbox"]) >= 4 and all(v is not None for v in s["bbox"][:4])]
        if not boxes:
            return None
        u = store._union(boxes)
        return transform(make_box(*u), to_pct)

    # ---- rooms
    sizes = data.get("room_sizes") or {}
    seen: dict[str, int] = {}
    for r in rooms:
        num = (r["number"] or "").strip()
        seen[num] = seen.get(num, 0) + 1
    for r in rooms:
        num = (r["number"] or "").strip()
        rid = num if num and seen[num] == 1 and len(num) <= 40 else f"e{r['entity_id']}"
        g = place(r)
        pts, at = [], [50.0, 50.0]
        if g is not None:
            if not g.is_valid:
                g = g.buffer(0)
            simple = g.simplify(0.03) if len(shapely.get_coordinates(g)) > 400 else g
            pts = _ring(simple) or _ring(g.envelope)
            try:                                         # the label goes where the room is widest (in page proportions)
                k = base.h / base.w if base is not None and base.w else 1.0
                cx, cy, _ = plan_tidy.pole(affinity.scale(g, 1.0, k, origin=(0, 0)))
                at = [_r(cx), _r(cy / k)]
            except Exception:  # noqa: BLE001
                at = [_r(v) for v in g.centroid.coords[0]] if not g.is_empty else at
        area = float(r["area_m2"]) if r["area_m2"] is not None and math.isfinite(float(r["area_m2"])) else None
        # a space the sheet prints no label for is a room all the same: it is named as such, never left blank
        room = dict(room_id=rid, num=num, name=((r["label"] or "").strip()
                                                 or ("" if r["room_type"] == "room" else (r["room_type"] or "").strip()) or NO_LABEL),
                    unlabelled=(not (r["label"] or "").strip()) or None,
                    unit=r["apartment"] or None, area_m2=round(area, 2) if area is not None else None,
                    outdoor=not r["is_indoor"], points=pts, label_at=at,
                    area_source=(None if area is None else
                                 "estimate" if str(r["area_source"] or "").startswith("nominal") else
                                 "outline" if str(r["area_source"] or "").startswith("measured") else "printed"),
                    entity_id=r["entity_id"], room_type=r["room_type"], storey=r["storey"], area_method=r["area_source"],
                    n_sources=r["n_sources"], agreement=r["agreement"], is_duplicate=r["is_duplicate"],
                    sources=values.get(r["entity_id"], []), evidence_id=f"room-{rid}", kind="room",
                    size=_room_size(sizes.get(r["entity_id"]), base))
        m.rooms.append(room)
        m.by_entity[r["entity_id"]] = room
    room_ids = {r["num"]: r["room_id"] for r in m.rooms if r["num"]}

    # ---- doors, windows, fixtures: boxes numbered per kind in reading order
    placed: dict[str, list[dict]] = {}
    for it in items:
        g = place(it)
        it["box"] = _rect(g) if g is not None else None
        it["shape"] = g
        placed.setdefault(it["kind"], []).append(it)
    for kind, group in placed.items():
        group.sort(key=lambda i: (0, round(i["box"]["y"], 1), i["box"]["x"], i["entity_id"]) if i["box"]
                   else (1, 0, 0, i["entity_id"]))
        width = max(2, len(str(len(group))))
        for n, it in enumerate(group, 1):
            it["evidence_id"] = f"{prefix_of(kind)}-{n:0{width}d}"
    ids = {it["entity_id"]: it["evidence_id"] for it in items}
    for it in items:
        item = dict(id=it["evidence_id"], subject=subject_of(it["kind"]), kind=it["kind"], entity_id=it["entity_id"],
                    box=it["box"], shape=it["shape"], room_id=room_ids.get((it["room"] or "").strip()), unit=it["apartment"] or None,
                    duplicate_of=ids.get(it["duplicate_of"]) if it["duplicate_of"] else None,
                    note=None if it["kind"] in SUBJECT else it["kind"].replace("_", " "),
                    tag=it["tag"], label=it["label"], width_m=it["width_m"], height_m=it["height_m"],
                    n_sources=it["n_sources"], ifc_class=it["ifc_class"], storey=it["storey"], evidence_id=it["evidence_id"])
        m.items.append(item)
        m.by_entity[it["entity_id"]] = item
    dims = [dict(x) for x in data.get("dimensions") or []]
    m.n_dimensions = len(dims)
    for n, x in enumerate(dims, 1):
        px = x.get("px") if isinstance(x.get("px"), dict) else {}
        g = place(x)
        # the dimension line and the box of its printed text, in percent of the page shown: from the geometry in
        # the drawing's frame, else (a page without a frame) from where the reader saw them on the page itself
        line = None
        if g is not None and g.geom_type == "LineString":
            line = [[_r(a), _r(b)] for a, b in g.coords]
        on_page = base is not None and to_pct is not None and any(
            s["file_id"] == base.file_id and s["page_no"] == base.page_no for s in srcs.get(x["entity_id"], []))
        if on_page:
            try:
                if line is None and px.get("line") and len(px["line"]) >= 2:
                    line = [[_r(v) for v in apply(to_pct, float(a), float(b))] for a, b in px["line"]]
                tb = (transform(make_box(*[float(v) for v in px["box"][:4]]), to_pct)
                      if px.get("box") and len(px["box"]) >= 4 else None)
                if line is not None and len(line) >= 2:    # the line and its text: a thin box, not every source box
                    g = shapely.LineString(line)
                if tb is not None:
                    g = tb if g is None else shapely.union_all([g, tb])
            except (TypeError, ValueError):
                pass
        item = dict(id=f"dim-{n:0{max(2, len(str(len(dims))))}d}", subject="label", kind="dimension",
                    entity_id=x["entity_id"], box=_rect(g, 0.3) if g is not None else None, shape=g,
                    room_id=room_ids.get((x["room"] or "").strip()), unit=None, duplicate_of=None,
                    note=f"{x['dim_kind'] or 'dimension'} {x['text']}"[:120], tag=x["text"], label=x["text"],
                    dim_kind=x["dim_kind"], value_m=x["value_m"], dim_unit=x.get("unit"), orientation=x.get("orientation"),
                    a_m=x.get("a_m"), b_m=x.get("b_m"), line=line, page_no=x.get("page_no"))
        m.by_entity[x["entity_id"]] = item
        m.dimensions.append(item)
    m.extent = _extent(m, data, srcs, frame_to_pct)
    for a in m.apartments:
        m.by_entity[a["entity_id"]] = dict(kind="apartment", entity_id=a["entity_id"], unit=a["apartment"], label=a["label"])
    m.linework = None if m.synthetic else _linework(db, m)
    if base is not None and base.format in ("png", "jpg") and frame_to_pct is not None and m.linework is None:
        m.nominal = base.frame_method == "nominal_scale"
        for w in data.get("walls") or []:
            g = _geom(w["geom"])
            if g is None or not any(s["file_id"] == base.file_id for s in srcs.get(w["entity_id"], [])):
                continue
            g = transform(g, frame_to_pct)
            g = g if g.is_valid else g.buffer(0)
            if not g.is_empty:
                m.walls.append(g)
        m.walls = _walls_around_rooms(m.walls, m.rooms)
        _tidy_rooms(m, frame_to_pct)
    return m


def _tidy_rooms(m: Model, frame_to_pct) -> None:
    """Ragged room outlines of an image plan (spaces without a label, traced around their fixtures) straightened for the drawing: the
    points and the label position change, the stored area does not."""
    base = m.base
    if base is None or frame_to_pct is None or not base.w or not base.h:
        return
    px = (base.w / 100.0, 0.0, 0.0, 0.0, base.h / 100.0, 0.0)             # percent -> pixels (isotropic)
    back = inv(px)
    pct_to_m = mul(base.frame, inv(base.to_pct)) if base.frame and inv(base.to_pct) else None
    u = math.hypot(pct_to_m[0], pct_to_m[3]) / (base.w / 100.0) if pct_to_m else 0.0     # metres per pixel
    polys = {}
    for i, r in enumerate(m.rooms):
        if len(r["points"]) >= 3:
            p = transform(shapely.Polygon(r["points"]), px)
            polys[i] = p if p.is_valid else p.buffer(0)
    if u <= 0 or not polys:
        return
    walls = transform(shapely.union_all(m.walls), px) if m.walls else None
    for i, p in list(polys.items()):
        if not m.rooms[i].get("unlabelled") or len(m.rooms[i]["points"]) <= 4:
            continue                                     # a space found without a label: traced around its fixtures
        others = shapely.union_all([q for j, q in polys.items() if j != i]) if len(polys) > 1 else None
        try:
            t = plan_tidy.tidy_outline(p, u, walls, others)
        except Exception:  # noqa: BLE001 - the outline stays as it is stored
            continue
        if t is p:
            continue
        polys[i] = t
        g = transform(t, back)
        cx, cy, _ = plan_tidy.pole(t)
        at = apply(back, cx, cy)
        m.rooms[i]["points"], m.rooms[i]["label_at"] = _ring(g) or m.rooms[i]["points"], [_r(at[0]), _r(at[1])]


def _walls_around_rooms(walls: list, rooms: list[dict]) -> list:
    """Wall polygons without the rooms they swallow: a room the store knows that lies mostly inside wall fill (a
    shaft, a sauna or a store drawn dark on the scan) is a room, so the fill is opened for it."""
    if not walls:
        return walls
    solid = shapely.union_all(walls)
    inside = []
    for r in rooms:
        if len(r["points"]) < 3:
            continue
        p = shapely.Polygon(r["points"])
        p = p if p.is_valid else p.buffer(0)
        if p.area > 0 and solid.intersection(p).area > 0.5 * p.area:
            inside.append(p)
    if not inside:
        return walls
    hole = shapely.union_all(inside)
    out = []
    for g in walls:
        g = g.difference(hole)
        out += [x for x in getattr(g, "geoms", [g]) if x.geom_type == "Polygon" and not x.is_empty]
    return out


def _linework(db: DB, m: Model) -> tuple[str, int | str, Affine] | None:
    """The vector file whose strokes can be drawn in the base image's percent space, with the transform."""
    base = m.base
    if base is None:
        return None
    if base.format in CAD:
        return (base.file_id, base.name or "model", base.to_pct)
    if base.format == "pdf":
        return (base.file_id, base.page_no, base.to_pct)
    if base.frame is not None and base.registered:  # a raster in the shared frame: draw its CAD twin
        twin = db.one("""select fr.file_id, fr.m, p.name from jstore.frame fr join jstore.source_file f using (file_id)
                         join jstore.page p on p.file_id = fr.file_id and p.page_no = fr.page_no
                         where f.drawing_key = :k and f.format in ('dxf', 'dwg') and fr.method not in ('scale_text', 'pixel_scale', 'nominal_scale') and fr.page_no = 0
                         order by f.format, fr.file_id limit 1""", k=m.key)
        if twin and len(twin["m"]) == 6:
            return (twin["file_id"], twin["name"] or "model", mul(mul(base.to_pct, inv(base.frame)), tuple(twin["m"])))
    return None


# ---------------------------------------------------------------------------------------------- cache
class Models:
    """Models by drawing key and storey, rebuilt when the drawing's signature changes.  A drawing with an IFC
    model has one model per storey; without a storey slug it is the storey the model opens on, under the
    drawing's own id."""

    def __init__(self, size: int = 256):
        self.size, self.lock, self.items = size, threading.Lock(), {}
        self.data: dict[str, tuple[tuple, dict, list[Storey]]] = {}     # what every storey of a drawing shares

    def _hit(self, ck: tuple, sig: tuple) -> Model | None:
        with self.lock:
            hit = self.items.get(ck)
            if hit is not None and hit.signature == sig:
                self.items[ck] = self.items.pop(ck)      # most recently used last
                return hit
        return None

    def _keep(self, ck: tuple, model: Model) -> None:
        with self.lock:
            self.items.pop(ck, None)
            self.items[ck] = model
            while len(self.items) > self.size:
                self.items.pop(next(iter(self.items)))

    def _shared(self, db: DB, key: str, sig: tuple) -> tuple[dict, list[Storey]] | None:
        with self.lock:
            hit = self.data.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1], hit[2]
        data = load(db, key)
        if data is None:
            return None
        storeys = storey_index(db, key).get(key, []) if any(f["format"] == "ifc" for f in data["files"]) else []
        with self.lock:
            self.data.pop(key, None)
            self.data[key] = (sig, data, storeys)
            while len(self.data) > 24:
                self.data.pop(next(iter(self.data)))
        return data, storeys

    def get(self, db: DB, key: str, slug: str | None = None) -> Model | None:
        sig = signature(db, key)
        if sig is None:
            return None
        hit = self._hit((key, slug or ""), sig)
        if hit is not None:
            return hit
        shared = self._shared(db, key, sig)
        if shared is None:
            return None
        data, storeys = shared
        if not storeys:
            model = (build(db, key, data=data) if slug is None else
                     build(db, key, data=data, page_slug=slug) if PAGE_SLUG.match(slug) else None)
        elif slug is None:
            model = self.get(db, key, default_storey(storeys).slug)
            model = dataclasses.replace(model, id=drawing_id(key)) if model is not None else None
        else:
            st = next((s for s in storeys if s.slug == slug), None)
            model = build(db, key, st, storeys, data) if st is not None else None
        if model is not None:
            self._keep((key, slug or ""), model)
        return model


# ---------------------------------------------------------------------------------------------- documents
def kind_of(formats) -> str:
    fs = set(formats)
    return "cad_dxf" if fs & set(CAD) else "ifc" if "ifc" in fs else "raster"


def credit_of(project: str, meta: dict | None = None) -> dict:
    """The store keeps no licence per file; a project may carry one in its meta (credit, license, license_name, url)."""
    meta = meta or {}
    source = project.split("/", 1)[0]
    out = dict(text=str(meta.get("credit") or f"Source: {source}" + (f" ({project.split('/', 1)[1]})" if "/" in project else "")),
               license=str(meta.get("license") or "LicenseRef-source-dataset"),
               license_name=str(meta.get("license_name") or meta.get("license") or "Licence of the source dataset"))
    if isinstance(meta.get("url"), str) and re.match(r"https?://", meta["url"]):
        out["url"] = meta["url"]
    return out


def title_of(key: str) -> str:
    return (key.replace("_", " ").strip() or key)[:120]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _room_size(row: dict | None, base) -> dict | None:
    """A room's length and width in metres: as printed in it, else measured on its outline (an estimate when the
    page states no scale)."""
    if not row or row.get("length_m") is None or row.get("width_m") is None:
        return None
    printed = row.get("size_source") == "printed"
    source = "printed" if printed else "estimate" if base is not None and base.frame_method == "nominal_scale" else "measured"
    return _clean(dict(length_m=round(float(row["length_m"]), 3), width_m=round(float(row["width_m"]), 3), source=source,
                       text=row.get("size_text") if printed else None))


def _extent(m: Model, data: dict, srcs: dict, frame_to_pct) -> dict | None:
    """Width (along the page's x) and depth (along y) in metres of what is drawn: the walls of an image plan, else
    the room outlines. Only on a page with a frame (percent -> metres)."""
    base = m.base
    if base is None or not base.frame or not base.to_pct:
        return None
    geoms, source = list(m.walls), "walls"
    if not geoms and frame_to_pct is not None:
        for w in data.get("walls") or []:
            g = _geom(w["geom"])
            if g is not None and any(s["file_id"] == base.file_id for s in srcs.get(w["entity_id"], [])):
                geoms.append(transform(g, frame_to_pct))
    if not geoms:
        source = "rooms"
        geoms = [shapely.Polygon(r["points"]) for r in m.rooms if len(r.get("points") or []) >= 3 and not r.get("outdoor")]
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return None
    x0, y0, x1, y1 = shapely.union_all([g.envelope for g in geoms]).bounds
    pct_to_m = mul(base.frame, inv(base.to_pct))
    w = (x1 - x0) * math.hypot(pct_to_m[0], pct_to_m[3])
    d = (y1 - y0) * math.hypot(pct_to_m[1], pct_to_m[4])
    if not (math.isfinite(w) and math.isfinite(d) and w > 0 and d > 0):
        return None
    return dict(width_m=round(w, 2), depth_m=round(d, 2), source=source, estimate=base.frame_method == "nominal_scale",
                box=dict(x=_r(x0), y=_r(y0), w=_r(x1 - x0), h=_r(y1 - y0)))


def dimension_doc(it: dict) -> dict:
    """A printed dimension as an answer cites it: what is printed, metres, and where it is on the page shown."""
    return _clean(dict(id=it["id"], store_ref=it["entity_id"], kind=it["dim_kind"], text=it["label"], value_m=it["value_m"],
                       unit=it["dim_unit"], orientation=it["orientation"], a_m=it["a_m"], b_m=it["b_m"],
                       room_id=it["room_id"], page_no=it["page_no"], line=it["line"], **(it["box"] or {})))


NO_LABEL = "Room without label"   # the name of a room the sheet prints no label for (`unlabelled` in the document)


def room_poly(r: dict) -> dict:
    """The RoomPoly of the contract (`area_m2` stays null when unknown; every other absent field is omitted)."""
    out = _clean({k: r.get(k) for k in ("room_id", "num", "name", "unit", "outdoor", "points", "label_at", "area_source",
                                        "size", "room_type", "unlabelled")})   # room_type: what the store reads the label as
    out["area_m2"] = r["area_m2"]
    return out


def box_of(it: dict) -> dict | None:
    """The Box of the contract for a placed item."""
    if not it.get("box"):
        return None
    return _clean(dict(id=it["id"], **it["box"], subject=it["subject"], room_id=it["room_id"], unit=it["unit"],
                       note=it["note"], duplicate_of=it["duplicate_of"]))


def capabilities(m: Model) -> list[str]:
    caps = [s for s in ("window", "door", "washbasin", "wc", "bathtub", "shower", "sink")
            if any(i["subject"] == s for i in m.items)]
    if m.rooms:
        caps.append("room")
        caps += [s for s in ("bedroom", "kitchen", "bathroom", "balcony", "sauna")
                 if any(ROOM_SUBJECT.get(r["room_type"] or "") == s for r in m.rooms)]
    if m.apartments or any(r["unit"] for r in m.rooms):
        caps.append("apartment")
    return caps


def drawing_doc(m: Model, project_meta: dict | None = None) -> dict:
    """`GET /drawings/{id}`: the contract's Drawing."""
    base, did = m.base, m.id
    w, h = (base.w, base.h) if base else (1000, 1000)
    mx = my = 0.0
    m_per_unit, lo, hi = 1.0, [0.0, 0.0], [float(w), float(h)]
    if base:
        back = inv(base.to_image)
        corners = [apply(back, x, y) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
        lo = [round(min(c[0] for c in corners), 4), round(min(c[1] for c in corners), 4)]
        hi = [round(max(c[0] for c in corners), 4), round(max(c[1] for c in corners), 4)]
        if base.frame:
            pct_to_m = mul(base.frame, inv(base.to_pct))
            mx = round(math.hypot(pct_to_m[0], pct_to_m[3]), 6)
            my = round(math.hypot(pct_to_m[1], pct_to_m[4]), 6)
            m_per_unit = round(math.hypot(base.frame[0], base.frame[3]), 9)
    formats = sorted({f["format"] for f in m.files})
    scale = next((f["scale"] for f in m.files if f["scale"]), None)
    project = m.files[0]["project_id"]
    units = [dict(id=a["apartment"], label=a["label"] or f"Apartment {a['apartment']}") for a in m.apartments if a["apartment"]]
    for u in sorted({r["unit"] for r in m.rooms if r["unit"]} - {u["id"] for u in units}):
        units.append(dict(id=u, label=f"Apartment {u}"))
    if base is None and m.storey is not None:            # a storey without geometry: an empty page of our own
        images = dict(rebuilt=f"/drawings/{did}/plan.svg")
    elif base is not None and base.image is None:        # never rendered: the rebuilt plan is the only picture
        images = dict(rebuilt=f"/drawings/{did}/plan.svg")
    elif m.linework is not None and base.format == "pdf" and not any(f["format"] in CAD for f in m.files):
        # a PDF sheet without a CAD file beside it: its page render (fills, hatches, line weights, lettering) reads far better than the
        # strokes redrawn as hairlines, so the page is the plan; rooms, doors and dimensions are laid over it
        images = dict(rebuilt=f"/drawings/{did}/original.png")
    elif m.linework is not None and m.placed > 0:        # the plan rebuilt from the store over the page it was read from
        images = dict(rebuilt=f"/drawings/{did}/plan.svg", original=f"/drawings/{did}/original.png", original_alignment="exact")
    elif m.vector_scan:                                  # an image plan: the vector plan drawn from its model, beside the scan
        images = dict(rebuilt=f"/drawings/{did}/plan.svg", original=f"/drawings/{did}/original.png", original_alignment="exact")
    else:
        images = dict(rebuilt=f"/drawings/{did}/original.png")
    created = next((f["ingested_at"] for f in m.files if f["ingested_at"]), None)
    credit = credit_of(project, project_meta)
    if m.upload:                                        # an upload is named after its file, not after its key
        credit = dict(text="Uploaded file", license="LicenseRef-upload", license_name="Uploaded by you")
    return _clean(dict(
        printed_dimensions=m.n_dimensions or None,     # additive: lengths can be answered from the sheet
        sheet_page=m.sheet[0] if m.sheet else None, sheet_pages=m.sheet[1] if m.sheet else None,
        dimensions=[dimension_doc(x) for x in m.dimensions] or None, extent_m=m.extent,
        contract_version=CONTRACT_VERSION, id=did, title=m.title,
        subtitle=" · ".join(x for x in (project.split("/", 1)[0], " + ".join(f.upper() for f in formats), scale,
                                        storey_note(m.storey)) if x),
        frame=dict(dxf_min=lo, dxf_max=hi, margin_m=0, m_per_unit=m_per_unit, y_flip=True, px=[w, h]),
        m_per_pct_x=mx, m_per_pct_y=my, images=images,
        rooms=[room_poly(r) for r in m.rooms], units=units, credit=credit,
        kind=kind_of(formats), status=m.status, capabilities=capabilities(m),
        source="upload" if m.upload else "library",
        thumbnail=None if base is None else f"/drawings/{did}/thumb.png" if base.image or m.synthetic
        else f"/drawings/{did}/plan.svg",
        created_at=created.isoformat() if created else None))


def storey_note(st: Storey | None) -> str | None:
    if st is None or not st.name or st.elevation is None:
        return None
    return f"level {round(st.elevation, 2) + 0.0:+.2f} m"


def storey_docs(m: Model) -> list[dict]:
    """The storey views of the drawing (model.json): id, name, elevation and what each holds."""
    root = m.id.split(STOREY_SEP)[0]
    return [_clean(dict(id=root + STOREY_SEP + s.slug, name=s.name or None, elevation_m=s.elevation, rooms=s.n_rooms,
                        items=s.n_items, footprints=s.n_shapes, plan_file=s.plan, shown=s is m.storey)) for s in m.storeys]


def evidence_doc(m: Model) -> dict:
    """Every box of the drawing by subject, in reading order (the `evidence` section of the bundled drawing file)."""
    out: dict[str, list[dict]] = {"window": [], "door": [], "washbasin": []}
    for it in sorted(m.items, key=lambda i: i["id"]):
        b = box_of(it)
        if b:
            out.setdefault(it["subject"] if it["subject"] != "label" else it["kind"], []).append(b)
    return out


def checks_doc(m: Model) -> dict:
    """The store's open issues (sources that disagree, things missing in a source) as checks."""
    checks = []
    for i in m.issues:
        e = m.by_entity.get(i["entity_id"]) if i["entity_id"] is not None else None
        checks.append(_clean(dict(id=f"issue-{i['issue_id']}", kind=i["kind"], property=i["property"], status=i["status"],
                                  detail=i["detail"], entity_id=i["entity_id"],
                                  evidence_id=e.get("evidence_id") if e else None)))
    rooms = [r for r in m.rooms if r["agreement"]]
    return dict(contract_version=CONTRACT_VERSION, id=m.id,
                summary=dict(issues=len(checks), open=sum(1 for c in checks if c["status"] == "open"),
                             rooms_agree=sum(1 for r in rooms if r["agreement"] == "agree"),
                             rooms_single_source=sum(1 for r in rooms if r["agreement"] == "single"),
                             rooms_conflict=sum(1 for r in rooms if r["agreement"] == "conflict")),
                checks=checks)


def model_doc(m: Model) -> dict:
    """Everything the adapter knows about the drawing: the page shown, rooms and items with entity ids, the value
    of every source and their agreement."""
    b = m.base
    return dict(
        contract_version=CONTRACT_VERSION, id=m.id, drawing_key=m.key, status=m.status,
        storey=m.storey.name or None if m.storey else None, storeys=storey_docs(m),
        files=[_clean(dict(file_id=f["file_id"], path=f["path"], format=f["format"], status=f["status"], storey=f["storey"],
                           error=f["error"])) for f in m.files],
        page=_clean(dict(file_id=b.file_id, page_no=b.page_no, format=b.format, name=b.name, px=[b.w, b.h],
                         image=f"/files/{b.file_id}/pages/{b.page_no}/image" if b.image else None, to_image=list(b.to_image),
                         frame=list(b.frame) if b.frame else None, frame_method=b.frame_method)) if b else None,
        rooms=[dict(room_poly(r), **_clean({k: r[k] for k in ("entity_id", "room_type", "storey", "area_method", "n_sources",
                                                                "agreement", "is_duplicate", "sources")})) for r in m.rooms],
        items=[_clean({k: it[k] for k in ("id", "subject", "kind", "entity_id", "box", "room_id", "unit", "duplicate_of",
                                          "tag", "label", "width_m", "height_m", "n_sources", "ifc_class", "storey")})
               for it in m.items],
        apartments=[_clean(dict(a)) for a in m.apartments],
        unplaced=[e["evidence_id"] for e in m.rooms if not e["points"]] + [i["id"] for i in m.items if not i["box"]])


# ---------------------------------------------------------------------------------------------- plan.svg
SVG_W = 1000.0
SHAPE_LOOK = {"light": 'fill="none" stroke="#94a3b8" stroke-width="0.5"',
              "door": 'fill="none" stroke="#b45309" stroke-opacity="0.6" stroke-width="0.6"',
              "window": 'fill="none" stroke="#1d4ed8" stroke-opacity="0.6" stroke-width="0.6"',
              "solid": 'fill="#cbd5e1" stroke="#475569" stroke-width="0.6"'}
MAX_STROKES = 30_000
ITEM_COLOUR = {"door": "#b45309", "window": "#1d4ed8"}


def _scan_data_uri(base: Base, max_side: int = 2400) -> str | None:
    """The page image of a scan as a data URI (an SVG shown through <img> cannot load other files)."""
    import base64
    import io

    from PIL import Image

    from ..pipeline import store_dir
    if not base.image:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = 400_000_000
        im = Image.open(store_dir() / base.image)
        im.draft("RGB", (max_side, max_side))
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
    except Exception:  # noqa: BLE001 - no picture: the tinted rooms are still drawn
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# The layers of an image plan's vector drawing.  The 3D view reads them by these exact styles (the same ones as the
# plan.svg of a CAD bundle): walls by their fill, windows by fill + stroke, doors and fixtures by their stroke.
WALL_FILL, WINDOW_STROKE, DOOR_STROKE, FIXTURE_STROKE = "#2f343c", "#374151", "#4b5563", "#6b7280"
ESTIMATE_NOTE = "Scale estimated from the sizes of drawn objects (doors, fixtures, rooms): lengths and areas are approximate"


def area_text(r: dict) -> str | None:
    """A room's area as printed on the plan: an area measured under an estimated scale is marked approximate."""
    if r["area_m2"] is None:
        return None
    return f"≈ {r['area_m2']:.0f} m²" if r.get("area_source") == "estimate" else f"{r['area_m2']:.2f} m²"


def _scan_plan_svg(m: Model) -> str:
    """An image plan redrawn from the store's model of it, in the percent space of the scan: walls as filled
    polygons, rooms tinted with name and area, doors (opening, leaf, swing), windows and fixtures as symbols."""
    base = m.base
    H = SVG_W * base.h / base.w
    sx, sy = SVG_W / 100.0, H / 100.0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W:g} {H:.2f}" '
           f'font-family="Helvetica, Arial, sans-serif" data-plan="vector-scan" data-scale="{"nominal" if m.nominal else "stated"}">',
           '<rect width="100%" height="100%" fill="#ffffff"/>']

    def d_of(g) -> str:
        parts = []
        for geom in getattr(g, "geoms", [g]):
            if geom.geom_type != "Polygon":
                continue
            for ring in [geom.exterior, *geom.interiors]:
                parts.append("M" + " ".join(f"{x * sx:.1f},{y * sy:.1f}" for x, y in ring.coords[:-1]) + "Z")
        return "".join(parts)

    out.append('<g data-layer="rooms">')
    for r in m.rooms:
        if len(r["points"]) >= 3:
            pts = " ".join(f"{x * sx:.1f},{y * sy:.1f}" for x, y in r["points"])
            out.append(f'<polygon points="{pts}" fill="{"#f4f1e6" if r["outdoor"] else "#e8f0f8"}" stroke="#b6c2d0" stroke-width="0.6"/>')
    out.append('</g>')
    to_svg = (sx, 0.0, 0.0, 0.0, sy, 0.0)
    solid = transform(shapely.union_all(m.walls), to_svg) if m.walls else None     # in the units of the drawing
    doors, windows, fixtures, cuts, syms = [], [], [], [], []
    polys = [shapely.Polygon(r["points"]) for r in m.rooms if len(r["points"]) >= 3]
    indoors = shapely.union_all([p if p.is_valid else p.buffer(0) for p in polys]).buffer(1.0) if polys else None
    door_boxes = [(i["box"]["x"] * sx, i["box"]["y"] * sy, i["box"]["w"] * sx, i["box"]["h"] * sy)
                  for i in m.items if i["kind"] == "door" and i["box"] and not i.get("duplicate_of")]
    u = 0.0                                              # metres per unit of the drawing
    if base.frame:
        pct_to_m = mul(base.frame, inv(base.to_pct))
        u = math.hypot(pct_to_m[0], pct_to_m[3]) / sx
    if u <= 0 and door_boxes:                            # no scale at all: the typical door of the plan is 0.9 m
        u = 0.9 / max(sorted(max(b[2], b[3]) for b in door_boxes)[len(door_boxes) // 2], 1e-6)
    near = None                                          # where an opening can be: by a wall or the edge of a room
    if solid is not None or polys:
        lines = ([solid] if solid is not None else []) + [transform(p.boundary, to_svg) for p in polys]
        near = shapely.union_all(lines).buffer(0.5 / u if u > 0 else 6.0)
    win = [i for i in m.items if i["kind"] == "window" and i["box"]]
    twice = plan_tidy.duplicate_boxes([(i["box"]["x"] * sx, i["box"]["y"] * sy, i["box"]["w"] * sx, i["box"]["h"] * sy) for i in win])
    twice = {id(win[k]) for k in twice}                  # a window detected twice is drawn once
    for it in m.items:
        g, b = it.get("shape"), it["box"]
        if not b or it["subject"] == "label":
            continue
        x, y, w, h = b["x"] * sx, b["y"] * sy, b["w"] * sx, b["h"] * sy
        if it["kind"] == "window":
            if id(it) in twice:
                continue
            if near is not None and not near.intersects(shapely.box(x, y, x + w, y + h)):
                continue                                 # a window stands in a wall: one detected away from all is not drawn
            d = d_of(g) if g is not None and g.geom_type in ("Polygon", "MultiPolygon") else ""
            windows.append(f'<path d="{d or f"M{x:.1f},{y:.1f}h{w:.1f}v{h:.1f}h{-w:.1f}Z"}" fill="#ffffff" stroke="{WINDOW_STROKE}" stroke-width="0.8"/>')
            if d:                                        # a window is an opening of the wall it sits in
                cuts.append(transform(g, to_svg))
            a, c = ((x, y + h / 2), (x + w, y + h / 2)) if w >= h else ((x + w / 2, y), (x + w / 2, y + h))
            windows.append(f'<path d="M{a[0]:.1f},{a[1]:.1f}L{c[0]:.1f},{c[1]:.1f}" fill="none" stroke="{WINDOW_STROKE}" stroke-width="0.5"/>')
        elif it["kind"] == "door":
            if near is not None and not near.intersects(shapely.box(x, y, x + w, y + h)):
                continue                                 # (nor is a door)
            if g is not None and not g.is_empty:         # the detected shape itself: the box of a line is padded
                x0, y0, x1, y1 = g.bounds
                x, y, w, h = x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy
            sym = door_symbol(x, y, w, h, solid, u)
            if sym.get("oversized") and any(not (abs(o[0] - x) < 1 and abs(o[1] - y) < 1) and x <= o[0] + o[2] / 2 <= x + w and y <= o[1] + o[3] / 2 <= y + h
                                            for o in door_boxes):
                continue                                 # an oversized box around another door: that door is the symbol
            syms.append(sym)
        elif indoors is None or shapely.contains_xy(indoors, b["x"] + b["w"] / 2, b["y"] + b["h"] / 2):
            r = min(w, h) * 0.18                     # (a fixture detected outside every room is not part of the plan)
            d = (f"M{x + r:.1f},{y:.1f}h{w - 2 * r:.1f}l{r:.1f},{r:.1f}v{h - 2 * r:.1f}l{-r:.1f},{r:.1f}h{-(w - 2 * r):.1f}"
                 f"l{-r:.1f},{-r:.1f}v{-(h - 2 * r):.1f}Z")
            fixtures.append(f'<path d="{d}" fill="none" stroke="{FIXTURE_STROKE}" stroke-width="0.7"/>')
            fixtures.append(f'<path d="M{x + w * 0.25:.1f},{y + h * 0.25:.1f}h{w * 0.5:.1f}v{h * 0.5:.1f}h{-w * 0.5:.1f}Z" fill="none" '
                            f'stroke="{FIXTURE_STROKE}" stroke-width="0.4"/>')
    syms.sort(key=plan_tidy.door_rank, reverse=True)       # two symbols on one opening: the better-supported one stays
    placed = []
    for sym in syms:
        if any(plan_tidy.same_opening(sym, o, u) for o in placed):
            continue
        placed.append(sym)
        cut = opening_cut(sym, solid, u)
        if cut is not None:
            cuts.append(cut)
        doors += door_paths(sym, DOOR_STROKE)
    out.append('<g data-layer="walls">')                 # wall fill: openings cut out, loose fragments dropped
    if solid is not None:
        rooms_u = shapely.union_all([transform(p if p.is_valid else p.buffer(0), to_svg) for p in polys]) if polys else None
        holes = shapely.union_all([c if c.is_valid else c.buffer(0) for c in cuts]) if cuts else None
        for geom in plan_tidy.clean_walls([solid], u, rooms_u, holes):
            d = d_of(transform(geom, (1 / sx, 0.0, 0.0, 0.0, 1 / sy, 0.0)))
            if d:
                out.append(f'<path d="{d}" fill="{WALL_FILL}" fill-rule="evenodd"/>')
    out += ['</g><g data-layer="windows">', *windows, '</g><g data-layer="doors">', *doors,
            '</g><g data-layer="fixtures">', *fixtures, '</g><g data-layer="labels">']
    tall = max(1.0, H / SVG_W)                           # a tall sheet is shown smaller: its text grows with it
    want = []
    for r in m.rooms:
        if len(r["points"]) < 3:
            continue
        lines = [(r["num"], 700)] if r["num"] else []
        if r["name"] and r["name"] not in ("Room", NO_LABEL):
            lines.append((r["name"][:28], 400))
        if area_text(r):
            lines.append((area_text(r), 400))
        if lines:
            want.append(dict(poly=shapely.Polygon([(x * sx, y * sy) for x, y in r["points"]]), lines=[t for t, _ in lines],
                             weight=dict(lines)))
    # each label at the widest place of its room, sized to it; the area goes first, then the name; none over another
    for w_, lab in zip(want, plan_tidy.place_labels(want, 3.2 * tall, 14.0 * tall)):
        if lab is None:
            continue
        size, x = lab["size"], lab["x"]
        y = lab["y"] - (len(lab["lines"]) - 1) * size * 0.6
        for text in lab["lines"]:
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" font-weight="{w_["weight"][text]}" text-anchor="middle" '
                       f'dominant-baseline="middle" fill="#0f172a" stroke="#ffffff" stroke-width="{size * 0.22:.1f}" '
                       f'paint-order="stroke">{html.escape(text)}</text>')
            y += size * 1.2
    if m.nominal:
        out.append(f'<text x="6" y="{H - 6:.1f}" font-size="{9 * tall:.1f}" fill="#64748b" stroke="#ffffff" stroke-width="2" '
                   f'paint-order="stroke">{html.escape(ESTIMATE_NOTE)}</text>')
    out.append("</g></svg>")
    return "\n".join(out)


def plan_svg(db: DB, m: Model) -> str:
    """The drawing rebuilt from the store's geometry, in the percent space of the base image: the strokes of the
    vector file in grey, rooms tinted with number, name and area, doors, windows and fixtures in colour."""
    base = m.base
    w, h = (base.w, base.h) if base else (1000, 1000)
    sx, sy = SVG_W / 100.0, SVG_W * h / w / 100.0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W:g} {SVG_W * h / w:.2f}" '
           f'font-family="Helvetica, Arial, sans-serif">', '<rect width="100%" height="100%" fill="#ffffff"/>']

    def path(g) -> str:
        parts = []
        for geom in getattr(g, "geoms", [g]):
            rings = [geom.exterior, *geom.interiors] if geom.geom_type == "Polygon" else [geom] if geom.geom_type in ("LineString", "LinearRing") else []
            for ring in rings:
                pts = " ".join(f"{x * sx:.1f},{y * sy:.1f}" for x, y in ring.coords)
                if pts:
                    parts.append(f"M{pts}" + ("Z" if geom.geom_type == "Polygon" else ""))
        return "".join(parts)

    if m.vector_scan:
        return _scan_plan_svg(m)
    scan = _scan_data_uri(base) if base is not None and m.linework is None and base.format in ("png", "jpg") else None
    if scan:                                         # a scan has no strokes to redraw: its picture is the linework
        out.append(f'<image href="{scan}" x="0" y="0" width="{SVG_W:g}" height="{SVG_W * h / w:.2f}" '
                   'preserveAspectRatio="none"/>')
    for r in m.rooms:
        if r["points"]:
            pts = " ".join(f"{x * sx:.1f},{y * sy:.1f}" for x, y in r["points"])
            fill = "#f4f1e6" if r["outdoor"] else "#eef3f8"
            look = (f'fill="{"#eab308" if r["outdoor"] else "#3b82f6"}" fill-opacity="0.16" stroke="#3b82f6" '
                    'stroke-opacity="0.55" stroke-width="0.8"') if scan else f'fill="{fill}" stroke="#94a3b8" stroke-width="0.6"'
            out.append(f'<polygon points="{pts}" {look}/>')
    drawn = {i["kind"] for i in m.items if i.get("shape") is not None}
    for style, look in SHAPE_LOOK.items():           # the storey's own elements: slabs, stairs, pipes ... then walls and columns
        if style in drawn:                           # doors / windows are drawn as items below
            continue
        d = "".join(path(g) for s, g in m.shapes if s == style)
        if d:
            out.append(f'<path d="{d}" fill-rule="evenodd" stroke-linejoin="round" {look}/>')
    if m.linework is not None:
        fid, where, t = m.linework
        # ST_AsSVG negates y (SVG's y points down); the transform is given with y negated so the output is ours.
        aff = "ST_Affine(geom, :a, :b, :d, :e, :c, :f)"
        params = dict(a=t[0] * sx, b=t[1] * sx, c=t[2] * sx, d=-t[3] * sy, e=-t[4] * sy, f=-t[5] * sy, fid=fid, n=MAX_STROKES)
        if isinstance(where, int):
            rows = db.run(f"""select ST_AsSVG({aff}, 0, 1) as d from jstore.pdf_path
                              where file_id = :fid and page_no = :w and geom is not null
                                and geometrytype(geom) not in ('POINT', 'MULTIPOINT') order by seq limit :n""", w=where, **params)
        else:
            rows = db.run(f"""select ST_AsSVG({aff}, 0, 1) as d from jstore.cad_entity
                              where file_id = :fid and space = :w and geom is not null and dxftype <> 'INSERT'
                                and geometrytype(geom) not in ('POINT', 'MULTIPOINT') order by id limit :n""", w=where, **params)
        d = " ".join(r["d"] for r in rows if r["d"])
        if d:
            out.append(f'<path d="{html.escape(d, quote=True)}" fill="none" stroke="#475569" stroke-width="0.7" '
                       'stroke-linejoin="round" stroke-linecap="round"/>')
    for it in m.items:
        g = it.get("shape")
        if g is None:
            continue
        colour = ITEM_COLOUR.get(it["kind"], "#0f766e")
        d = path(g) if g.geom_type != "Point" else ""
        if not d and it["box"]:
            b = it["box"]
            d = f'M{b["x"] * sx:.1f},{b["y"] * sy:.1f}h{b["w"] * sx:.1f}v{b["h"] * sy:.1f}h{-b["w"] * sx:.1f}Z'
        if d:
            out.append(f'<path d="{d}" fill="{colour}" fill-opacity="0.12" stroke="{colour}" stroke-width="1"/>')
    for r in m.rooms:
        if not r["points"]:
            continue
        xs, ys = [p[0] * sx for p in r["points"]], [p[1] * sy for p in r["points"]]
        size = max(5.0, min(11.0, (max(xs) - min(xs)) / 7.0, (max(ys) - min(ys)) / 4.0))
        x, y = r["label_at"][0] * sx, r["label_at"][1] * sy
        lines = [(r["num"], 700)] if r["num"] else []
        if r["name"] and r["name"] not in ("Room", NO_LABEL):
            lines.append((r["name"][:28], 400))
        if r["area_m2"] is not None:
            lines.append((f"{r['area_m2']:.2f} m²", 400))
        y -= (len(lines) - 1) * size * 0.6
        for text, weight in lines:
            out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" font-weight="{weight}" text-anchor="middle" '
                       f'dominant-baseline="middle" fill="#0f172a">{html.escape(text)}</text>')
            y += size * 1.2
    out.append("</svg>")
    return "\n".join(out)


def page_path(data_dir: Path, m: Model) -> Path | None:
    """The base page's image file inside the store directory."""
    from .files import Forbidden, contained
    if m.base is None or m.base.image is None:
        return None
    try:
        p = contained(data_dir, m.base.image)
    except Forbidden:
        return None
    return p if p.is_file() else None


def plan_png(m: Model, side: int = 640) -> bytes:
    """A raster of the page drawn from the model (thumbnail, and `original.png` of a storey without a plan file):
    the picture of plan.svg without the texts."""
    import io

    from PIL import Image, ImageDraw
    base = m.base
    w, h = (base.w, base.h) if base else (1000, 1000)
    k = side / max(w, h)
    W, H = max(1, round(w * k)), max(1, round(h * k))
    up = 2 if max(W, H) <= 2000 else 1               # drawn larger and reduced: smooth lines without a renderer
    sx, sy = W * up / 100.0, H * up / 100.0
    im = Image.new("RGB", (W * up, H * up), "#ffffff")
    dr = ImageDraw.Draw(im, "RGBA")
    lw = max(1, round(W * up / 900))

    def rings(g):
        for geom in getattr(g, "geoms", [g]):
            if geom.geom_type == "Polygon":
                yield [(x * sx, y * sy) for x, y in geom.exterior.coords], True
            elif geom.geom_type in ("LineString", "LinearRing"):
                yield [(x * sx, y * sy) for x, y in geom.coords], False
            elif hasattr(geom, "geoms"):
                yield from rings(geom)

    def draw(g, fill, outline, width=lw):
        for pts, closed in rings(g):
            if len(pts) < 2:
                continue
            if closed and fill and len(pts) >= 3:
                dr.polygon(pts, fill=fill)
            dr.line(pts, fill=outline, width=width, joint="curve")

    for r in m.rooms:
        if r["points"]:
            pts = [(x * sx, y * sy) for x, y in r["points"]]
            dr.polygon(pts, fill="#f4f1e6" if r["outdoor"] else "#e6eef7")
            dr.line(pts + pts[:1], fill="#94a3b8", width=lw)
    if m.walls:                                      # an image plan: its wall polygons as fill, their holes left open
        mask = Image.new("L", im.size, 0)
        md = ImageDraw.Draw(mask)
        polys = [g for w in m.walls for g in getattr(w, "geoms", [w]) if g.geom_type == "Polygon" and not g.is_empty]
        for geom in sorted(polys, key=lambda g: -g.area):   # an island inside a hole is drawn after the hole
            if True:
                md.polygon([(x * sx, y * sy) for x, y in geom.exterior.coords], fill=255)
                for hole in geom.interiors:
                    md.polygon([(x * sx, y * sy) for x, y in hole.coords], fill=0)
        im.paste(Image.new("RGB", im.size, "#334155"), (0, 0), mask)
        dr = ImageDraw.Draw(im, "RGBA")
    drawn = {i["kind"] for i in m.items if i.get("shape") is not None}
    for style, fill, outline in (("light", None, "#94a3b8"), ("door", None, "#b45309"), ("window", None, "#1d4ed8"),
                                 ("solid", "#cbd5e1", "#475569")):
        if style in drawn:
            continue
        for s, g in m.shapes:
            if s == style:
                draw(g, fill, outline)
    for it in m.items:
        g = it.get("shape")
        if g is None:
            continue
        colour = ITEM_COLOUR.get(it["kind"], "#0f766e")
        rgb = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        if g.geom_type == "Point" or not any(True for _ in rings(g)):
            b = it["box"]
            if b:
                dr.rectangle([b["x"] * sx, b["y"] * sy, (b["x"] + b["w"]) * sx, (b["y"] + b["h"]) * sy], fill=rgb + (40,), outline=rgb + (255,))
            continue
        draw(g, rgb + (40,), rgb + (255,))
    if up > 1:
        im = im.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
