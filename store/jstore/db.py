"""Database access.  The only module that talks to the driver (pg8000, BSD-3-Clause).

    with DB() as db:
        db.run("select * from jstore.source_file where format = :f", f="dxf")   -> list of dicts
        db.copy("jstore.cad_entity", ["file_id", "handle", ...], rows)          bulk insert
        db.script(open("schema.sql").read())                                    several statements

Values for copy(): str, int, float, bool or None; use js() for jsonb, arr() for arrays, geom() for geometry.
"""
from __future__ import annotations

import io
import json
import math
import os
import urllib.parse
from pathlib import Path

import pg8000.native

HERE = Path(__file__).resolve().parent
DSN = os.environ.get("JSTORE_DSN", "postgresql://postgres:jstore@127.0.0.1:54329/postgres")
READER_USER = "jreader"
READER_PASSWORD = os.environ.get("JSTORE_READER_PASSWORD", "jreader")


def _parts(dsn: str) -> dict:
    u = urllib.parse.urlparse(dsn)
    return dict(user=urllib.parse.unquote(u.username or "postgres"), password=urllib.parse.unquote(u.password or ""),
                host=u.hostname or "127.0.0.1", port=u.port or 5432, database=(u.path or "/postgres").lstrip("/"))


def js(obj) -> str | None:
    """A Python object for a jsonb column."""
    return None if obj is None else json.dumps(obj, ensure_ascii=False, default=_default)


def _default(o):
    if hasattr(o, "item"):       # numpy scalars
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


def arr(values) -> str | None:
    """A list of numbers for a double precision[] column."""
    if values is None:
        return None
    return "{" + ",".join("NULL" if v is None else repr(float(v)) for v in values) + "}"


def geom(g) -> str | None:
    """A shapely geometry for a geometry column (hex WKB); None for empty or missing."""
    if g is None or g.is_empty:
        return None
    return g.wkb_hex


def _cell(v) -> str:
    if v is None:
        return ""                                 # unquoted empty = NULL in csv COPY
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, float):
        return "" if (math.isnan(v) or math.isinf(v)) else repr(v)
    if isinstance(v, int):
        return str(v)
    if hasattr(v, "item"):                        # numpy scalar
        return _cell(v.item())
    s = str(v).replace("\x00", "")
    return '"' + s.replace('"', '""') + '"'       # always quoted, so '' stays an empty string


class DB:
    def __init__(self, dsn: str | None = None, reader: bool = False, timeout: float | None = None):
        p = _parts(dsn or DSN)
        if reader:
            p.update(user=READER_USER, password=READER_PASSWORD)
        self.c = pg8000.native.Connection(timeout=timeout, **p)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.c.close()
        except Exception:  # noqa: BLE001 - already closed
            pass

    def run(self, sql: str, **params) -> list[dict]:
        rows = self.c.run(sql, **params)
        cols = [c["name"] for c in (self.c.columns or [])]
        return [dict(zip(cols, r)) for r in (rows or [])]

    def one(self, sql: str, **params) -> dict | None:
        rows = self.run(sql, **params)
        return rows[0] if rows else None

    def script(self, sql: str) -> None:
        """Several statements, no parameters (simple query protocol)."""
        self.c.execute_simple(sql)

    def copy(self, table: str, columns: list[str], rows) -> int:
        buf = io.StringIO()
        n = 0
        for row in rows:
            buf.write(",".join(_cell(v) for v in row))
            buf.write("\n")
            n += 1
        if n:
            buf.seek(0)
            self.c.run(f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)", stream=buf)
        return n


def apply_schema(db: DB | None = None) -> None:
    """Create or update tables, rebuild the model-facing views, make sure the read-only role exists."""
    own = db is None
    db = db or DB()
    try:
        db.script((HERE / "schema.sql").read_text(encoding="utf-8"))
        db.script((HERE / "views.sql").read_text(encoding="utf-8"))
        # Additions by area (idempotent DDL, extra q views), applied in name order after the base files.
        for extra in sorted((HERE / "sql").glob("*.sql")):
            db.script(extra.read_text(encoding="utf-8"))
        pw = READER_PASSWORD.replace("'", "''")
        db.script(f"""
            do $$ begin
              if not exists (select from pg_roles where rolname = '{READER_USER}') then
                create role {READER_USER} login password '{pw}';
              end if;
            end $$;
            alter role {READER_USER} with password '{pw}';
            alter role {READER_USER} set statement_timeout = '8s';
            alter role {READER_USER} set default_transaction_read_only = on;
            alter role {READER_USER} set search_path = q, public;
            grant usage on schema q to {READER_USER};
            grant select on all tables in schema q to {READER_USER};
            grant execute on all functions in schema q to {READER_USER};
        """)
    finally:
        if own:
            db.close()


if __name__ == "__main__":
    apply_schema()
    print("schema applied")
