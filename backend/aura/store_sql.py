"""Guarded SQL over the drawing store (Felix's jstore: schema q, read-only role jreader).

The tool-step answerer (`aura.steps`) offers one `sql` tool when the store is on. Everything a
query may do is decided here, reusing jstore's own guards unchanged (`check_sql`, the jreader
role, the per-run scope of the q views) and adding three of ours:

- the owner rule (`check_owner`): on drawings whose rooms / doors / windows / fixtures come from
  our solvers, the store's versions of those views are refused, so the screen never shows two
  answers to one question;
- a per-query statement timeout (STATEMENT_TIMEOUT_MS);
- numbers come from code (`take`): the model names the result shape (count | rows | cell | sum of
  a column), code reads or adds the cells; the model never types a number.

jstore is imported only inside the functions that run a query, never when the store is off.
"""

from __future__ import annotations

import functools
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from aura.contract import Method

STORE_DSN_ENV = "AURA_STORE_DSN"
STATEMENT_TIMEOUT_MS = 4000  # one slow statement + one retry still fit the 15 s tool-step turn
MAX_ROWS = 200
STORE_MD = Path(__file__).resolve().parents[2] / "store" / "prompts" / "STORE.md"

# Views of things whose answers our solvers own on bundled drawings and uploaded DXF/IFC.
# Layer-2 views (filtered by the drawing scope). On a drawing our solvers own they return nothing,
# because its reader is scoped with drawings=[] (see scope_for); the token check below only turns
# that silent empty result into a message, so the model does not report a false 0.
OWNED_VIEWS = frozenset({"rooms", "apartments", "doors", "windows", "fixtures", "items", "facts",
                         "sources", "entity_sources", "issues", "definitions"})
LAYER2_VIEWS = OWNED_VIEWS
# Which entity kinds stand behind a Layer-2 view (for the method check when a result has no ids).
VIEW_KINDS = {"rooms": ("room",), "apartments": ("apartment", "room"), "doors": ("door",),
              "windows": ("window",), "fixtures": ("toilet", "washbasin", "sink", "bathtub", "shower")}
STORE_OWNED_EXT = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
# A source method found by a model or a detector (not read from CAD/IFC/PDF text): an estimate.
MODEL_METHOD = re.compile(r"gemini|vision|detect|llm|model|raster_rooms|raster_detection", re.IGNORECASE)
OCR_METHOD = re.compile(r"ocr|raster_word", re.IGNORECASE)
ESTIMATE_VIEWS = {"detections": "vision", "ocr_words": "ocr"}
EXACT_METHOD = "store_sql" if "store_sql" in get_args(Method) else "cad_geometry"


class SqlRefused(ValueError):
    """Refused before it ran; the message goes back to the model."""


def available(svc: Any = None) -> bool:
    """The store is on: the app's DrawingStore started (`svc.drawing_store.available()`); without
    one wired into the services, a configured DSN."""
    ds = getattr(svc, "drawing_store", None)
    if ds is not None:
        return bool(ds.available())
    return bool(os.environ.get(STORE_DSN_ENV, "").strip())


@functools.cache
def store_md() -> str:
    """The schema document the model gets (static, so the prompt prefix stays cacheable)."""
    try:
        return STORE_MD.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---- scope and owner ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreScope:
    drawings: tuple[str, ...]
    files: tuple[str, ...]


def scope_for(d) -> StoreScope | None:
    """The store's drawing keys and file ids for one of our drawings: `drawing.json` "store",
    then the loaded drawing's meta, then `store-map.json` beside the drawing dirs. None: not in
    the store (no sql for it)."""
    ref = (d.drawing.get("store") or (getattr(d, "meta", None) or {}).get("store")
           or _store_map(d.dir.parent if d.dir else None).get(d.id))
    if not isinstance(ref, dict):
        return None
    keys = ref.get("drawing_keys") or ([ref["drawing_key"]] if ref.get("drawing_key") else [])
    files = ref.get("file_ids") or ([ref["file_id"]] if ref.get("file_id") else [])
    if not keys and not files:
        return None
    if not store_owns_things(d):
        # The owner rule, enforced by the database: the interpreted (Layer 2) views filter on the
        # drawing scope and stay empty; texts, layers, IFC data (Layer 1) filter on the files.
        if not files:
            return None
        keys = []
    return StoreScope(tuple(str(k) for k in keys), tuple(str(f) for f in files))


@functools.lru_cache(maxsize=8)
def _store_map_at(path: str, mtime: float) -> dict:
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_map(base: Path | None) -> dict:
    if base is None:
        return {}
    p = base / "store-map.json"
    try:
        return _store_map_at(str(p), p.stat().st_mtime)
    except OSError:
        return {}


def store_owns_things(d) -> bool:
    """Uploaded PDF / image plans are materialised from the store: it owns their rooms and items.
    Bundled drawings and uploaded DXF/IFC keep our solvers for those."""
    ext = str(d.drawing.get("source_ext") or "").lower()
    return d.source == "upload" and (ext in STORE_OWNED_EXT or d.drawing.get("kind") == "raster")


_TOKEN = re.compile(r"""
    (?P<ws>\s+) | (?P<comment>--[^\n]*) | (?P<str>'(?:[^']|'')*') | (?P<dollar>\$(\w*)\$.*?\$\3\$)
  | (?P<qid>"(?:[^"]|"")+") | (?P<id>[A-Za-z_][A-Za-z0-9_$]*) | (?P<num>\d+(?:\.\d+)?) | (?P<p>.)
""", re.VERBOSE | re.DOTALL)


def tokens(query: str) -> list[str]:
    """Identifiers (lower-cased; quoted ones unquoted) and punctuation; strings and comments out."""
    out: list[str] = []
    for m in _TOKEN.finditer(query or ""):
        kind = m.lastgroup
        if kind in ("ws", "comment", "str", "dollar", "num"):
            continue
        if kind == "qid":
            out.append(m.group()[1:-1].replace('""', '"').lower())
        else:
            out.append(m.group().lower())
    return out


def referenced_views(query: str) -> list[str]:
    """Every q.<view> the statement names, anywhere (CTEs and subqueries included)."""
    t = tokens(query)
    return sorted({t[i + 2] for i in range(len(t) - 2) if t[i] == "q" and t[i + 1] == "."})


def check_owner(query: str, d) -> None:
    """SqlRefused when the statement reads the store's rooms / apartments / doors / windows /
    fixtures on a drawing whose owner for those is our solver. The database scope (drawings=[])
    is what enforces it; this check only says why such a query would come back empty."""
    if store_owns_things(d):
        return
    hit = next((v for v in referenced_views(query) if v in OWNED_VIEWS), None)
    if hit:
        raise SqlRefused(
            f"q.{hit} is not used on this drawing: its rooms, doors, windows and fixtures come from the "
            "drawing's own exact index. Use list_rooms, rooms_containing or list_openings for those; "
            "use sql for texts, layers, IFC properties, issues and per-source values.")


# ---- run ---------------------------------------------------------------------------------------


@dataclass
class SqlResult:
    ok: bool
    query: str
    rows: list[dict] = field(default_factory=list)
    total: int = 0
    columns: list[str] = field(default_factory=list)
    ms: int = 0
    error: str = ""
    entity_ids: list[int] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)  # q.sources.method behind the entity ids
    views: list[str] = field(default_factory=list)


def entity_ids_of(rows: list[dict]) -> list[int]:
    """Entity ids in a result: an `entity_id` column and any `ids` / `*_ids` integer array."""
    out: list[int] = []
    for r in rows:
        for k, v in r.items():
            if k == "entity_id" and isinstance(v, int) and not isinstance(v, bool):
                out.append(v)
            elif (k == "ids" or k.endswith("_ids")) and isinstance(v, (list, tuple)):
                out.extend(x for x in v if isinstance(x, int) and not isinstance(x, bool))
    return list(dict.fromkeys(out))


def _reader(scope: StoreScope):
    """jstore's Reader (role jreader, scoped views) with our per-statement timeout."""
    from jstore.db import DB
    from jstore.service.store import Reader

    class TimedReader(Reader):
        def _run(self, sql: str) -> list[dict]:
            if self.db is None:
                dsn = os.environ.get(STORE_DSN_ENV) or None
                self.db = DB(dsn=dsn, reader=True, timeout=30)  # dsn None: jstore's own (configure_env)
                self.db.script(f"set statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
            return super()._run(sql)

    return TimedReader(list(scope.drawings), list(scope.files), max_rows=MAX_ROWS)


def run(query: str, d, scope: StoreScope, reader=None) -> SqlResult:
    """One guarded SELECT, as jreader, scoped to this drawing's files. Never raises for a bad
    statement: the result carries the refusal or database error for the model."""
    t0 = time.perf_counter()
    res = SqlResult(ok=False, query=(query or "").strip())
    try:
        from jstore.service.store import SqlRefused as JRefused
        from jstore.service.store import check_sql, pg_error
    except ImportError:  # the store is configured but its library is missing
        res.error = "the drawing store is not available"
        return res
    try:
        q = check_sql(query)
        check_owner(q, d)
    except (JRefused, SqlRefused) as e:
        res.error = f"Refused: {e}"
        return res
    res.query, res.views = q, referenced_views(q)
    own = reader is None
    reader = reader or _reader(scope)
    try:
        rows = reader._run(f"select * from (\n{q}\n) _q limit {MAX_ROWS + 1}")
        total = len(rows)
        if total > MAX_ROWS:
            total = int(reader._run(f"select count(*) as n from (\n{q}\n) _q")[0]["n"])
        res.rows, res.total = rows[:MAX_ROWS], total
        res.columns = list(rows[0].keys()) if rows else []
        res.entity_ids = entity_ids_of(res.rows)
        if res.entity_ids:
            lit = "array[" + ",".join(str(int(i)) for i in res.entity_ids[:5000]) + "]::bigint[]"
            res.methods = sorted({str(r["method"]) for r in reader._run(
                f"select distinct method from q.sources where entity_id = any({lit})") if r.get("method")}
                | {str(r["ref_table"]) for r in reader._run(
                    f"select distinct ref_table from q.entity_sources where entity_id = any({lit})")
                   if r.get("ref_table")})
        elif any(v in LAYER2_VIEWS for v in res.views):
            # Interpreted things without ids in the result (count(*) and the like): judge the method
            # over every entity in scope, conservatively (one model-found entity makes it an estimate).
            kinds = sorted({k for v in res.views for k in VIEW_KINDS.get(v, ("%",))})
            lit = "array[" + ",".join("'" + k + "'" for k in kinds) + "]"
            res.methods = sorted({str(r["m"]) for r in reader._run(
                f"select distinct method as m from q.sources where kind like any({lit}) union "
                f"select distinct ref_table from q.entity_sources where kind like any({lit})") if r.get("m")})
        res.ok = True
    except Exception as e:  # noqa: BLE001 - syntax errors, unknown columns, timeouts: text for the model
        res.error = "Error: " + pg_error(e)
    finally:
        if own:
            reader.close()
        res.ms = int((time.perf_counter() - t0) * 1000)
    return res


# ---- numbers by code ---------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)  # int, float, Decimal
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def unit_of(column: str) -> str | None:
    c = column.lower()
    if c.endswith("m2"):  # q.* names metric quantities *_m2 / *_m3 / *_m; bare 'area' is in drawing units
        return "m2"
    if c.endswith("m3"):
        return "m3"
    if c.endswith("_m") or c == "m":
        return "m"
    return None


_NOT_DATA = frozenset({
    "as", "cast", "numeric", "int", "integer", "bigint", "float", "real", "double", "precision", "text",
    "varchar", "null", "true", "false", "interval", "case", "when", "then", "else", "end", "and", "or",
    "not", "is", "distinct", "date", "time", "timestamp", "decimal"})
_AGGREGATES = frozenset({"count", "sum", "avg", "min", "max", "array_agg", "string_agg", "bool_or",
                         "bool_and", "every", "percentile_cont", "percentile_disc", "mode"})


def _lexemes(query: str) -> list[tuple[str, str]]:
    out = []
    for m in _TOKEN.finditer(query or ""):
        k = m.lastgroup
        if k in ("ws", "comment"):
            continue
        out.append((k or "p", m.group().lower() if k in ("id", "p") else m.group()))
    return out


def constant_column(query: str, col: str) -> bool:
    """True when every expression the query names `col` (`<expr> as col`) reads nothing from a
    table: only literals, casts and non-aggregate functions (`select 42 as n`, `round(3.5) as m2`)."""
    lx = _lexemes(query)
    exprs = []
    for i in range(1, len(lx) - 1):
        if lx[i] == ("id", "as") and lx[i + 1][1].strip('"').lower() == col.lower():
            depth, j, expr = 0, i - 1, []
            while j >= 0:
                k, v = lx[j]
                if v == ")":
                    depth += 1
                elif v == "(":
                    if depth == 0:
                        break
                    depth -= 1
                elif depth == 0 and (v == "," or (k == "id" and v in ("select", "distinct"))):
                    break
                expr.append(lx[j])
                j -= 1
            exprs.append(list(reversed(expr)))
    if not exprs:
        return False  # an unaliased column (count, sum ...) or a table column: read from data
    for expr in exprs:
        for n, (k, v) in enumerate(expr):
            nxt = expr[n + 1][1] if n + 1 < len(expr) else ""
            if k == "qid" or (k == "id" and v not in _NOT_DATA and (nxt != "(" or v in _AGGREGATES)):
                return False
    return True


def _aggregated(res: SqlResult) -> bool:
    """One row with one numeric value column, from a query that aggregates at its top level."""
    if not res.rows:
        return False
    cols = [c for c in res.rows[0] if c != "entity_id" and not (c == "ids" or c.endswith("_ids"))]
    return (len(cols) == 1 and _num(res.rows[0][cols[0]]) is not None
            and any(f"{a}(" in re.sub(r"\s+", "", res.query.lower()) for a in ("count", "sum", "avg", "min", "max")))


def take(res: SqlResult, finish_as: str) -> dict:
    """The answer's number (or text) read from the rows by code.
    count / rows: the row count. cell or value:<col>: the one cell of a one-row result.
    sum:<col>: the column summed here. Raises SqlRefused when the shape does not fit."""
    fa = (finish_as or "").strip()
    kind, _, col = fa.partition(":")
    kind, col = kind.lower(), col.strip()
    if not res.views:
        raise SqlRefused("the result reads no q.* view, so it is not data from the drawing")
    if kind == "count" and res.total == 1 and _aggregated(res):
        kind = "cell"  # the query already counted: 'count' of its one row would always say 1
    if kind in ("count", "rows"):
        return {"kind": "count", "value": res.total, "unit": None,
                "rule": f"count = {res.total} row{'s' if res.total != 1 else ''}"}
    if kind in ("cell", "value"):
        if res.total != 1:
            raise SqlRefused(f"finish_as={fa!r} needs exactly one row; the query returned {res.total}")
        row = res.rows[0]
        cols = [c for c in row if c != "entity_id" and not (c == "ids" or c.endswith("_ids"))]
        if not col:
            if len(cols) != 1:
                raise SqlRefused(f"name the column: value:<column> (columns: {', '.join(cols)})")
            col = cols[0]
        if col not in row:
            raise SqlRefused(f"no column {col!r} (columns: {', '.join(row)})")
        if constant_column(res.query, col):
            raise SqlRefused(f"column {col!r} is a constant written in the query, not data from the drawing")
        v = row[col]
        n = _num(v)
        if n is None:
            if v is None:
                raise SqlRefused(f"column {col!r} is empty in that row")
            return {"kind": "text", "value": str(v)[:500], "unit": None, "rule": f"the cell {col}"}
        return {"kind": "number", "value": n, "unit": unit_of(col), "rule": f"the cell {col} = {n:g}"}
    if kind == "sum":
        if not col:
            raise SqlRefused("finish_as sum needs a column: sum:<column>")
        if res.total > len(res.rows):
            raise SqlRefused(f"{res.total} rows exceed the {MAX_ROWS} read; sum in SQL and use value:<column>")
        if res.rows and col not in res.rows[0]:
            raise SqlRefused(f"no column {col!r} (columns: {', '.join(res.columns)})")
        if constant_column(res.query, col):
            raise SqlRefused(f"column {col!r} is a constant written in the query, not data from the drawing")
        vals = [_num(r.get(col)) for r in res.rows]
        nums = [v for v in vals if v is not None]
        if res.rows and not nums:
            raise SqlRefused(f"column {col!r} holds no numbers")
        total = round(sum(nums), 6)
        skipped = len(vals) - len(nums)
        return {"kind": "number", "value": total, "unit": unit_of(col),
                "rule": f"sum of column {col} over {len(nums)} rows = {total:g}"
                        + (f" ({skipped} empty left out)" if skipped else "")}
    raise SqlRefused("finish_as must be count, rows, cell, value:<column> or sum:<column>")


def method_of(res: SqlResult) -> tuple[str, bool, str]:
    """(contract method, is_estimate, why). Any entity behind the answer found by a model or a
    detector makes the answer an estimate ('vision'); OCR-read values give 'ocr'; never cad_geometry
    for those."""
    model = [m for m in res.methods if MODEL_METHOD.search(m)]
    if model or "detections" in res.views:
        return "vision", True, "found by " + ", ".join(model or ["image detection"])
    ocr = [m for m in res.methods if OCR_METHOD.search(m)]
    src_ocr = any(str(r.get("source", "")).lower() == "ocr" for r in res.rows)
    if ocr or src_ocr or "ocr_words" in res.views:
        return "ocr", True, "read by OCR from the page image"
    return EXACT_METHOD, False, "read from the parsed files"


def boxes_for(d, ids: list[int]) -> tuple[list[dict], list[str]]:
    """Entity ids -> our evidence boxes and room ids, through `store_ref.entity_id` on the drawing
    file's boxes and rooms (materialised store drawings carry it). Unmapped ids draw nothing."""
    want = {int(i) for i in ids}
    if not want:
        return [], []

    def eid(x: dict) -> int | None:
        ref = x.get("store_ref")  # the entity id (drawing_store.normalise), or {"entity_id": id}
        v = ref.get("entity_id") if isinstance(ref, dict) else ref
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    from aura.contract import Box

    boxes = []
    for bs in (d.drawing.get("evidence") or {}).values():
        for b in bs or []:
            if isinstance(b, dict) and eid(b) in want:
                boxes.append({k: v for k, v in b.items() if k in Box.model_fields})
    rooms = [r["room_id"] for r in d.drawing.get("rooms", []) if eid(r) in want]
    return boxes, rooms
