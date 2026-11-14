"""Everything the service asks the database: run scope, the guarded `sql` tool, evidence regions, and the
read endpoints for a front end.  Synchronous (pg8000); the app calls these through asyncio.to_thread."""
from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import re
import threading
from pathlib import Path

from ..db import DB

# ---------------------------------------------------------------------------------------------- scope


def scope_of(db: DB, file_ids: list[str]) -> dict:
    """The drawings of the given files, and every file of those drawings (the same plan in other formats)."""
    drawings = [r["drawing_key"] for r in db.run(
        "select distinct drawing_key from jstore.source_file where file_id = any(cast(:f as text[])) order by 1",
        f=list(file_ids))]
    files = db.run("""select file_id, path, format, drawing_key, project_id, status from jstore.source_file
                      where drawing_key = any(cast(:d as text[])) order by drawing_key, format, path""", d=drawings)
    return dict(drawings=drawings, files=files)


def _lit(values: list[str]) -> str:
    return "'" + ",".join(values).replace("'", "''") + "'"


def set_scope(db: DB, drawings: list[str], files: list[str]) -> None:
    """Scope the q views on this connection.  SET, not set_config(): that function is revoked from the reader role."""
    db.script(f"set jstore.drawings = {_lit(drawings)}; set jstore.files = {_lit(files)};")


# ---------------------------------------------------------------------------------------------- sql tool
class SqlRefused(ValueError):
    """The statement is not allowed; the message goes back to the model."""


FORBIDDEN = (
    (r"set_config|current_setting", "set_config / current_setting are not available: the scope of a run is fixed"),
    (r"\bpg_|information_schema", "catalog access (pg_*, information_schema) is not available; the views are listed in the instructions"),
    (r"\bjstore\s*\.", "only the views in schema q are readable (write q.rooms, not jstore.entity)"),
    (r"_to_xml|\bts_stat\b|\bts_rewrite\b|\bdblink|\blo_[a-z]+\s*\(|\bcopy\b", "functions that run other statements or touch files are not available"),
    (r"/\*", "block comments are not accepted"),
    (r"\b(insert\s+into|delete\s+from|update\s+\S+\s+set|merge\s+into|drop\s|alter\s|create\s|truncate\s|grant\s|revoke\s)",
     "the store is read-only"),
)


def check_sql(query: str) -> str:
    """One read-only statement, or SqlRefused.  The database enforces the same things again: the reader role is
    read-only, can read nothing but schema q, and the extended protocol executes one statement per call."""
    q = re.sub(r"^(\s*--[^\n]*\n)+", "", query or "").strip().rstrip(";").strip()
    if not q:
        raise SqlRefused("empty query")
    if ";" in q:
        raise SqlRefused("one statement per call (no ';' inside the query, not even in a string)")
    if not re.match(r"\(*\s*(select|with)\b", q, re.I):
        raise SqlRefused("only a single SELECT or WITH statement is accepted")
    for pat, why in FORBIDDEN:
        if re.search(pat, q, re.I):
            raise SqlRefused(why)
    return q


WKB = re.compile(r"^(00|01)[0-9A-Fa-f]{16,}$")
NUM = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w])")


def _cell(v, max_len: int = 240):
    """A database value as the model should see it: floats to 6 significant digits, geometry and long values cut."""
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, bool) or v is None or isinstance(v, int):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else float(f"{v:.6g}")
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return str(v)
    if isinstance(v, str):
        if WKB.match(v):
            return "<geometry: use ST_Area / ST_Length / ST_AsText>"
        return v if len(v) <= max_len else v[:max_len] + f"... [{len(v)} characters]"
    if isinstance(v, (list, tuple)) and len(v) <= 300 and all(isinstance(x, (int, float, type(None))) for x in v):
        return [_cell(x) for x in v]      # id lists (array_agg) stay whole: the answer's evidence is built from them
    if isinstance(v, (list, tuple)) and len(v) <= 60 and all(isinstance(x, str) and len(x) <= 40 for x in v):
        return list(v)
    s = json.dumps(v, ensure_ascii=False, default=str)
    return json.loads(s) if len(s) <= max_len else s[:max_len] + f"... [{len(s)} characters]"


def _numbers(rows: list[dict]) -> tuple[set[float], dict[str, float]]:
    """Every number in a result (also inside text cells), and the sum of each numeric column."""
    seen: set[float] = set()
    sums: dict[str, float] = {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, decimal.Decimal):
                v = float(v)
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                if not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                    seen.add(float(v))
                    sums[k] = sums.get(k, 0.0) + float(v)
            elif isinstance(v, str) and len(v) < 400 and not WKB.match(v):
                seen.update(float(m.replace(",", ".")) for m in NUM.findall(v))
    return seen, sums


class Reader:
    """The read-only connection of one run: role jreader, scoped to the run's drawings and files."""

    def __init__(self, drawings: list[str], files: list[str], max_rows: int = 200, max_chars: int = 16_000):
        self.drawings, self.files, self.max_rows, self.max_chars = drawings, files, max_rows, max_chars
        self.db: DB | None = None
        self.lock = threading.Lock()
        self.log: list[dict] = []       # one entry per executed query: what verification checks answers against

    def close(self) -> None:
        if self.db:
            self.db.close()
            self.db = None

    def _run(self, sql: str) -> list[dict]:
        for attempt in (0, 1):
            try:
                if self.db is None:
                    self.db = DB(reader=True, timeout=30)
                set_scope(self.db, self.drawings, self.files)
                return self.db.run(sql)
            except (OSError, TimeoutError) as e:  # connection dropped: one fresh try
                self.close()
                if attempt:
                    raise RuntimeError(f"database connection failed: {e}") from None
            except Exception as e:
                if "network error" in str(e).lower() or "connection is closed" in str(e).lower():
                    self.close()
                    if not attempt:
                        continue
                raise
        return []

    def query(self, query: str) -> dict:
        """-> {ok, text (for the model), rows (total), returned}.  Never raises for a bad statement."""
        try:
            q = check_sql(query)
        except SqlRefused as e:
            return dict(ok=False, rows=0, returned=0, text=f"Refused: {e}")
        cap = self.max_rows
        with self.lock:
            try:
                # the newline keeps a trailing `-- comment` from swallowing the wrapper
                rows = self._run(f"select * from (\n{q}\n) _q limit {cap + 1}")
                total = len(rows)
                if total > cap:
                    total = self._run(f"select count(*) as n from (\n{q}\n) _q")[0]["n"]
            except Exception as e:  # noqa: BLE001 - syntax errors, unknown columns, timeouts: text for the model
                return dict(ok=False, rows=0, returned=0, text="Error: " + pg_error(e))
        shown = rows[:cap]
        seen, sums = _numbers(shown)
        self.log.append(dict(query=q, rows=total, numbers=seen, sums=sums if total <= cap else {}))
        cols = list(shown[0].keys()) if shown else []
        head = f"rows: {total}"
        if total > cap:
            head += f" (showing the first {cap}; aggregate, filter or page in SQL instead of reading rows)"
        lines, used = [head] + (["columns: " + " | ".join(cols)] if cols else []), 0
        for i, r in enumerate(shown):
            line = json.dumps([_cell(v) for v in r.values()], ensure_ascii=False, default=str)
            used += len(line) + 1
            if used > self.max_chars:
                lines.append(f"... [output cut after {i} rows: select fewer columns or aggregate]")
                break
            lines.append(line)
        return dict(ok=True, rows=total, returned=min(total, cap), text="\n".join(lines))


def pg_error(e: Exception) -> str:
    """The database's message (and hint) without the driver's wrapping."""
    d = e.args[0] if e.args and isinstance(e.args[0], dict) else None
    if d is None:
        return f"{type(e).__name__}: {e}"[:600]
    msg = d.get("M", "error")
    if d.get("C") == "57014":
        msg += " (statement timeout: 8 s; make the query cheaper)"
    return (msg + (f" Hint: {d['H']}" if d.get("H") else ""))[:600]


# ---------------------------------------------------------------------------------------------- evidence
def pct_box(box: list[float] | None, to_image: list[float] | None, w: int | None, h: int | None):
    """File coordinates -> (pixel box, percent box) on the rendered page image through page.to_image
    ([a,b,c,d,e,f]: px = a*x + b*y + c ; py = d*x + e*y + f).  None when the page has no render."""
    if not box or not to_image or len(to_image) != 6 or not w or not h or any(v is None for v in box[:4]):
        return None, None
    a, b, c, d, e, f = to_image
    x0, y0, x1, y1 = box[:4]
    xs = [a * x + b * y + c for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    ys = [d * x + e * y + f for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    px = [min(xs), min(ys), max(xs), max(ys)]
    clamp = lambda v: round(min(100.0, max(0.0, v)), 3)  # noqa: E731
    pct = [clamp(100 * px[0] / w), clamp(100 * px[1] / h), clamp(100 * px[2] / w), clamp(100 * px[3] / h)]
    return [round(v, 1) for v in px], pct


L1 = {  # ref_table -> query giving key, page (or space), box and what a front end shows for the primitive
    "cad_entity": """select handle as key, space, layer, dxftype, left(text, 120) as text,
                            array[st_xmin(geom), st_ymin(geom), st_xmax(geom), st_ymax(geom)] as box
                     from jstore.cad_entity where file_id = :f and handle = any(cast(:k as text[]))""",
    "pdf_path": """select id::text as key, page_no, kind as layer,
                          array[st_xmin(geom), st_ymin(geom), st_xmax(geom), st_ymax(geom)] as box
                   from jstore.pdf_path where file_id = :f and id::text = any(cast(:k as text[]))""",
    "pdf_text": """select id::text as key, page_no, left(text, 120) as text, array[x0, y0, x1, y1]::float8[] as box
                   from jstore.pdf_text where file_id = :f and id::text = any(cast(:k as text[]))""",
    "raster_word": """select id::text as key, page_no, left(text, 120) as text, array[x0, y0, x1, y1]::float8[] as box
                      from jstore.raster_word where file_id = :f and id::text = any(cast(:k as text[]))""",
    "raster_detection": """select id::text as key, page_no, class as layer, array[x0, y0, x1, y1]::float8[] as box
                           from jstore.raster_detection where file_id = :f and id::text = any(cast(:k as text[]))""",
    "ifc_element": """select el.guid as key, el.ifc_class, el.name, el.storey,
                             case when g.bbox is not null then array[g.bbox[1], g.bbox[2], g.bbox[4], g.bbox[5]] end as box
                      from jstore.ifc_element el left join jstore.ifc_geometry g using (file_id, guid)
                      where el.file_id = :f and el.guid = any(cast(:k as text[]))""",
}


def resolve_refs(db: DB, refs: list[dict]) -> list[dict]:
    """Complete Layer 1 references ({file_id, ref_table, ref_key} + optional entity_id, page_no, role, bbox) with
    the page, the box in file coordinates, the box in percent of the page image, and the image URL."""
    groups: dict[tuple, list[dict]] = {}
    for r in refs:
        if r.get("ref_table") in L1 and r.get("ref_key") is not None:
            groups.setdefault((r["file_id"], r["ref_table"]), []).append(r)
    for (fid, table), items in groups.items():
        found = {x["key"]: x for x in db.run(L1[table], f=fid, k=sorted({str(i["ref_key"]) for i in items}))}
        spaces = None
        for it in items:
            x = found.get(str(it["ref_key"]))
            if x is None:
                it["missing"] = True
                continue
            if it.get("bbox") is None and x.get("box") and all(v is not None for v in x["box"]):
                it["bbox"] = [float(v) for v in x["box"]]
            if x.get("page_no") is not None:
                it["page_no"] = x["page_no"]
            elif "space" in x and it.get("page_no") is None:
                if spaces is None:
                    spaces = {p["name"]: p["page_no"] for p in db.run(
                        "select name, page_no from jstore.page where file_id = :f", f=fid)}
                it["page_no"] = spaces.get(x["space"], 0)
            for k in ("layer", "dxftype", "text", "ifc_class", "name", "storey"):
                if x.get(k) is not None:
                    it[k] = x[k]
    fids = sorted({r["file_id"] for r in refs})
    pages = {(p["file_id"], p["page_no"]): p for p in db.run(
        """select p.file_id, p.page_no, p.image, p.image_w, p.image_h, p.to_image, f.format
           from jstore.source_file f left join jstore.page p using (file_id)
           where f.file_id = any(cast(:f as text[]))""", f=fids)}
    formats = {k[0]: p["format"] for k, p in pages.items()}
    out = []
    for r in refs:
        page_no = r.get("page_no") or 0
        p = pages.get((r["file_id"], page_no)) or {}
        px, pct = pct_box(r.get("bbox"), p.get("to_image"), p.get("image_w"), p.get("image_h"))
        item = dict(entity_id=r.get("entity_id"), file_id=r["file_id"], format=formats.get(r["file_id"]),
                    page_no=page_no, ref_table=r.get("ref_table"), ref_key=r.get("ref_key"), role=r.get("role"),
                    box=[round(v, 4) for v in r["bbox"][:4]] if r.get("bbox") else None, box_px=px, box_pct=pct,
                    image=f"/files/{r['file_id']}/pages/{page_no}/image" if p.get("image") else None,
                    image_w=p.get("image_w"), image_h=p.get("image_h"),
                    **{k: r[k] for k in ("layer", "dxftype", "text", "ifc_class", "name", "storey", "missing") if k in r})
        out.append({k: v for k, v in item.items() if v is not None})
    return out


def _union(boxes: list[list[float]]) -> list[float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def evidence(db: DB, entity_ids: list[int], drawings: list[str] | None = None) -> dict:
    """For each entity and each of its source files: page, box in file coordinates, box in percent of the page
    image, and the Layer 1 keys.  `drawings` limits the lookup to a run's scope."""
    ids = list(dict.fromkeys(int(i) for i in entity_ids))
    ents = {e["entity_id"]: e for e in db.run(
        """select entity_id, kind, number, label, drawing_key as drawing, storey, duplicate_of from jstore.entity
           where entity_id = any(cast(:i as bigint[])) and (cast(:d as text[]) is null or drawing_key = any(cast(:d as text[])))""",
        i=ids, d=drawings)}
    refs = db.run("""select entity_id, file_id, page_no, ref_table, ref_key, role, bbox from jstore.entity_source
                     where entity_id = any(cast(:i as bigint[])) order by entity_id, file_id, page_no, id""",
                  i=list(ents)) if ents else []
    by: dict[tuple, list[dict]] = {}
    for r in resolve_refs(db, refs):
        by.setdefault((r["entity_id"], r["file_id"], r["page_no"]), []).append(r)
    out = []
    for i in ids:
        if i not in ents:
            continue
        e = {k: v for k, v in ents[i].items() if v is not None}
        e["sources"] = []
        for (eid, fid, page_no), items in by.items():
            if eid != i:
                continue
            first = items[0]
            src = dict(file_id=fid, format=first.get("format"), page_no=page_no, image=first.get("image"),
                       image_w=first.get("image_w"), image_h=first.get("image_h"),
                       box=_union([r.get("box") for r in items]), box_pct=_union([r.get("box_pct") for r in items]),
                       refs=[{k: r[k] for k in ("ref_table", "ref_key", "role", "box", "box_pct", "layer", "ifc_class",
                                                "name", "text") if k in r} for r in items])
            e["sources"].append({k: v for k, v in src.items() if v is not None})
        out.append(e)
    return dict(entities=out, missing=[i for i in ids if i not in ents])


# ---------------------------------------------------------------------------------------------- read endpoints
COUNTS = ("page", "cad_entity", "pdf_path", "pdf_text", "raster_word", "raster_detection", "ifc_element")


def file_info(db: DB, file_id: str) -> dict | None:
    f = db.one("""select file_id, path, format, bytes, drawing_key, project_id, storey, status, error, meta, ingested_at
                  from jstore.source_file where file_id = :f""", f=file_id)
    if f is None:
        return None
    counts = db.one("select " + ", ".join(
        f"(select count(*) from jstore.{t} where file_id = :f) as {t}" for t in COUNTS), f=file_id)
    counts["entities"] = db.one("select count(*) n from jstore.entity where drawing_key = :d", d=f["drawing_key"])["n"]
    return dict(file_id=f["file_id"], name=Path(f["path"]).name, ext=f["format"], size=f["bytes"], path=f["path"],
                drawing=f["drawing_key"], project=f["project_id"], storey=f["storey"], status=f["status"],
                error=(f["error"] or "").splitlines()[0][:300] if f["error"] else None,
                ingested_at=str(f["ingested_at"]) if f["ingested_at"] else None, counts=counts)


def drawings(db: DB, project: str | None = None, text: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    where = """(cast(:p as text) is null or project_id = :p or project_id like :p || '/%')
               and (cast(:t as text) is null or drawing_key ilike '%' || :t || '%' or path ilike '%' || :t || '%')"""
    rows = db.run(f"""
        select drawing_key as drawing, min(project_id) as project,
               json_agg(json_build_object('file_id', file_id, 'path', path, 'format', format, 'status', status,
                                          'storey', storey) order by format, path) as files,
               case when bool_or(status = 'failed') then 'failed' when bool_and(status = 'interpreted') then 'interpreted'
                    when bool_or(status = 'registered') then 'registered' else 'parsed' end as status,
               (select count(*) from jstore.entity e where e.drawing_key = f.drawing_key and e.status = 'active') as entities
        from jstore.source_file f where {where}
        group by drawing_key order by drawing_key limit :l offset :o""", p=project, t=text, l=limit, o=offset)
    total = db.one(f"select count(distinct drawing_key) n from jstore.source_file where {where}", p=project, t=text)["n"]
    return dict(total=total, drawings=rows)


def drawing(db: DB, key: str) -> dict | None:
    files = db.run("""select file_id, path, format, bytes, status, storey, project_id as project,
                             split_part(error, E'\\n', 1) as error from jstore.source_file
                      where drawing_key = :k order by format, path""", k=key)
    if not files:
        return None
    pages = db.run("""select p.file_id, p.page_no, p.name, p.width, p.height, p.unit, p.image is not null as has_image,
                             p.image_w, p.image_h from jstore.page p join jstore.source_file f using (file_id)
                      where f.drawing_key = :k order by p.file_id, p.page_no""", k=key)
    for p in pages:
        p["image"] = f"/files/{p['file_id']}/pages/{p['page_no']}/image" if p.pop("has_image") else None
    for f in files:
        f["pages"] = [{k: v for k, v in p.items() if k != "file_id"} for p in pages if p["file_id"] == f["file_id"]]
    kinds = db.run("""select kind, count(*) as n, count(*) filter (where duplicate_of is not null) as duplicates
                      from jstore.entity where drawing_key = :k and status = 'active' group by 1 order by 2 desc""", k=key)
    issues = db.run("""select issue_id, entity_id, property, kind, detail, status from jstore.issue
                       where drawing_key = :k order by issue_id limit 500""", k=key)
    return dict(drawing=key, project=files[0]["project"], files=files, entities=kinds, issues=issues)


def entities(db: DB, key: str, kind: str | None = None, limit: int = 500, offset: int = 0) -> dict:
    """Entities of a drawing with, per property, the preferred value, the agreement and the value of every source."""
    set_scope_superuser(db, [key], [])
    ents = db.run("""select entity_id, kind, ifc_class, storey, number, label, label_norm, zone, host, duplicate_of, attrs
                     from jstore.entity where drawing_key = :k and status = 'active' and (cast(:kind as text) is null or kind = :kind)
                     order by kind, number nulls last, entity_id limit :l offset :o""", k=key, kind=kind, l=limit, o=offset)
    ids = [e["entity_id"] for e in ents]
    facts = db.run("""select entity_id, property, value, value_text, unit, method, from_file, n_sources, agreement
                      from q.facts where entity_id = any(cast(:i as bigint[]))""", i=ids) if ids else []
    srcs = db.run("""select entity_id, property, value, value_text, unit, method, rank, confidence, file_id, format
                     from q.sources where entity_id = any(cast(:i as bigint[])) order by entity_id, property, format""",
                  i=ids) if ids else []
    by = {e["entity_id"]: e for e in ents}
    for e in ents:
        e["values"] = {}
    for f in facts:
        by[f["entity_id"]]["values"][f["property"]] = dict(
            value=f["value"] if f["value"] is not None else f["value_text"], unit=f["unit"], method=f["method"],
            from_file=f["from_file"], n_sources=f["n_sources"], agreement=f["agreement"], sources=[])
    for s in srcs:
        v = by[s["entity_id"]]["values"].get(s["property"])
        if v is not None:
            v["sources"].append(dict(file_id=s["file_id"], format=s["format"], method=s["method"], rank=s["rank"],
                                     value=s["value"] if s["value"] is not None else s["value_text"], unit=s["unit"]))
    total = db.one("""select count(*) n from jstore.entity where drawing_key = :k and status = 'active'
                      and (cast(:kind as text) is null or kind = :kind)""", k=key, kind=kind)["n"]
    return dict(drawing=key, kind=kind, total=total, entities=ents)


def set_scope_superuser(db: DB, drawings: list[str], files: list[str]) -> None:
    db.run("select set_config('jstore.drawings', :d, false), set_config('jstore.files', :f, false)",
           d=",".join(drawings), f=",".join(files))


def answer_sources(db: DB, drawings: list[str], entity_ids: list[int], cap: int = 300) -> tuple[list[dict], dict]:
    """Per-source values (q.sources) and the agreement per property (q.facts) for the entities behind an answer."""
    if not entity_ids:
        return [], {}
    set_scope_superuser(db, drawings, [])
    srcs = db.run("""select entity_id, property, value, value_text, unit, method, rank, file_id, format from q.sources
                     where entity_id = any(cast(:i as bigint[])) order by entity_id, property, format limit :c""",
                  i=entity_ids, c=cap)
    for s in srcs:
        if s["value"] is None:
            s["value"] = s.pop("value_text")
        else:
            s.pop("value_text")
    agree: dict[str, dict[str, int]] = {}
    for r in db.run("""select property, agreement, count(*) n from q.facts where entity_id = any(cast(:i as bigint[]))
                       group by 1, 2""", i=entity_ids):
        agree.setdefault(r["property"], {})[r["agreement"]] = r["n"]
    return srcs, agree
