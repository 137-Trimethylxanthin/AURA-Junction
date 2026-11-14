"""The AURA compatibility layer (/aura): every body is checked against the frontend's contract.

The guards below are line-by-line ports of the runtime guards in frontend/src/api/contract.ts (isDrawing,
isAskResponse, ...), which the frontend applies to every response; a body they reject is an error card there.
When Node >= 22.6 is available (on PATH or in JSTORE_TEST_NODE), test_frontend_guards_accept_the_bodies also runs
the real TypeScript guards over the same bodies.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import ezdxf
import pytest

from jstore.db import DB, arr, js
from jstore.service import aura, aura_model as am

from conftest import A1, TOKEN, call, final, script

BASE = f"/aura/t/{TOKEN}"
CONTRACT_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "contract.ts"

# ---------------------------------------------------------------------------------------------- contract.ts guards
LANGS = ("en", "fr", "fi", "de")
SUBJECTS = ("window", "door", "washbasin", "wc", "bathtub", "shower", "room", "apartment", "bedroom", "kitchen", "bathroom",
            "wc_room", "sauna", "sink", "shower_or_bathtub", "storey", "balcony")
BOX_SUBJECTS = SUBJECTS + ("label", "fire_rating")
ERROR_CODES = ("bad_request", "unknown_drawing", "internal", "too_large", "unsupported_type", "rate_limited",
               "budget_exhausted", "presenter_required", "processing", "timeout", "not_found")
STATUSES = ("queued", "processing", "ready", "failed")
KINDS = ("cad_dxf", "raster", "ifc")


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def is_str(v) -> bool:
    return isinstance(v, str)


def opt(check, v) -> bool:        # contract: an optional field is absent, never null
    return v is MISSING or check(v)


MISSING = object()


def g(o: dict, k: str):
    return o.get(k, MISSING)


def is_pair(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and is_num(v[0]) and is_num(v[1])


def is_rect(v) -> bool:
    return isinstance(v, dict) and all(is_num(v.get(k)) for k in "xywh")


def is_scope(v) -> bool:
    if not isinstance(v, dict):
        return False
    k = v.get("kind")
    return k in ("floor", "model") or (k == "unit" and is_str(v.get("unit"))) or (k == "room" and is_str(v.get("room_id"))) \
        or (k == "label" and is_str(v.get("label"))) or (k == "region" and is_rect(v.get("rect")) and is_str(v.get("label")))


def is_box(v) -> bool:
    return isinstance(v, dict) and is_rect(v) and is_str(v.get("id")) and v.get("subject") in BOX_SUBJECTS \
        and all(opt(is_str, g(v, k)) for k in ("room_id", "note", "duplicate_of", "unit"))


def is_answer(v) -> bool:
    if not isinstance(v, dict):
        return False
    k = v.get("kind")
    if k == "count":
        return is_num(v.get("value")) and v.get("subject") in SUBJECTS
    if k == "area":
        return is_num(v.get("value")) and v.get("unit") == "m2"
    if k == "quantity":
        return is_num(v.get("value")) and v.get("unit") in ("m", "m2", "m3")
    if k == "text":
        return is_str(v.get("value")) and v.get("subject") in ("fire_rating", "label")
    if k == "abstain":
        return v.get("reason") in ("not_on_drawing", "not_supported_yet", "unclear_question", "low_confidence") \
            and opt(is_str, g(v, "where_to_find"))
    return False


def is_trace_node(v) -> bool:
    return isinstance(v, dict) and all(is_str(v.get(k)) for k in ("id", "title", "tag", "body")) \
        and v.get("kind") in ("exact", "model", "warn", "info") and opt(is_str, g(v, "parent")) \
        and opt(lambda x: isinstance(x, list) and all(map(is_str, x)), g(v, "evidence_ids")) \
        and opt(lambda x: x == "sample" or is_num(x), g(v, "confidence")) and g(v, "tool") is MISSING


def ev_room_ok(r) -> bool:
    return isinstance(r, dict) and is_str(r.get("room_id")) and isinstance(r.get("included"), bool) \
        and (r.get("area_m2", MISSING) is None or is_num(r.get("area_m2")))


def ev_row_ok(r) -> bool:
    return isinstance(r, dict) and is_str(r.get("id")) and opt(is_str, g(r, "name")) and opt(is_num, g(r, "value"))


def is_ask_response(v) -> bool:
    if not isinstance(v, dict):
        return False
    ev = v.get("evidence")
    if not isinstance(ev, dict) or not isinstance(ev.get("boxes"), list) or not isinstance(ev.get("rooms"), list):
        return False
    if not all(map(is_box, ev["boxes"])) or not all(map(ev_room_ok, ev["rooms"])):
        return False
    if "rows" in ev and not (isinstance(ev["rows"], list) and all(map(ev_row_ok, ev["rows"]))):
        return False
    if "gold" in v or "check" in v:     # the adapter sends neither
        return False
    return is_str(v.get("contract_version")) and is_answer(v.get("answer")) and v.get("detected_lang") in LANGS \
        and is_str(v.get("canonical_en")) and is_scope(v.get("scope")) \
        and v.get("method") in ("cad_geometry", "ocr", "detector", "vision", "none") \
        and isinstance(v.get("trace"), list) and all(map(is_trace_node, v["trace"])) \
        and is_num(v.get("calls")) and is_num(v.get("cost_usd")) and is_num(v.get("latency_ms")) \
        and v.get("source") in ("api", "local") and all(opt(is_str, g(v, k)) for k in ("answer_id", "conversation_id", "turn_id", "explanation")) \
        and opt(lambda r: r in ("template", "choice", "agent", "cache"), g(v, "route"))


def is_room_poly(v) -> bool:
    return isinstance(v, dict) and all(is_str(v.get(k)) for k in ("room_id", "num", "name")) \
        and ("area_m2" in v and (v["area_m2"] is None or is_num(v["area_m2"]))) and isinstance(v.get("outdoor"), bool) \
        and isinstance(v.get("points"), list) and all(map(is_pair, v["points"])) and is_pair(v.get("label_at")) \
        and opt(lambda s: s in ("printed", "outline"), g(v, "area_source")) and opt(is_str, g(v, "unit")) \
        and opt(lambda r: is_rect(r) and is_num(r.get("w_m")) and is_num(r.get("h_m")), g(v, "rect"))


def is_credit(v) -> bool:
    return isinstance(v, dict) and is_str(v.get("text")) and is_str(v.get("license")) and opt(is_str, g(v, "url")) \
        and opt(is_str, g(v, "license_name"))


def is_drawing(v) -> bool:
    if not isinstance(v, dict):
        return False
    f, img = v.get("frame"), v.get("images")
    return is_str(v.get("contract_version")) and is_str(v.get("id")) and is_str(v.get("title")) and isinstance(f, dict) \
        and is_pair(f.get("dxf_min")) and is_pair(f.get("dxf_max")) and is_num(f.get("margin_m")) and is_num(f.get("m_per_unit")) \
        and f.get("y_flip") is True and is_pair(f.get("px")) and is_num(v.get("m_per_pct_x")) and is_num(v.get("m_per_pct_y")) \
        and isinstance(img, dict) and is_str(img.get("rebuilt")) and opt(is_str, g(img, "original")) \
        and opt(lambda a: a in ("exact", "approximate"), g(img, "original_alignment")) \
        and isinstance(v.get("rooms"), list) and all(map(is_room_poly, v["rooms"])) \
        and isinstance(v.get("units"), list) and all(isinstance(u, dict) and is_str(u.get("id")) and is_str(u.get("label")) for u in v["units"]) \
        and is_credit(v.get("credit")) and opt(lambda k: k in KINDS, g(v, "kind")) and opt(lambda s: s in STATUSES, g(v, "status")) \
        and opt(is_num, g(v, "progress")) and opt(is_str, g(v, "error")) \
        and opt(lambda c: isinstance(c, list) and all(x in SUBJECTS for x in c), g(v, "capabilities")) \
        and opt(lambda s: s in ("library", "upload"), g(v, "source")) \
        and all(opt(is_str, g(v, k)) for k in ("thumbnail", "created_at", "expires_at", "subtitle")) and "facts" not in v


def is_summary(v) -> bool:
    return isinstance(v, dict) and is_str(v.get("id")) and is_str(v.get("title")) and v.get("kind") in KINDS \
        and v.get("status") in STATUSES and is_str(v.get("thumbnail")) and is_credit(v.get("credit")) \
        and opt(is_str, g(v, "subtitle")) and opt(lambda s: s in ("library", "upload"), g(v, "source")) \
        and opt(is_num, g(v, "progress")) and opt(is_str, g(v, "error"))


def is_error(v) -> bool:
    return isinstance(v, dict) and is_str(v.get("contract_version")) and is_str(v.get("error")) and v.get("code") in ERROR_CODES


def no_nulls(v, path="") -> None:
    """Contract: optional fields are omitted, never null; area_m2 is the one field where null is a value."""
    if isinstance(v, dict):
        for k, x in v.items():
            assert x is not None or k == "area_m2", f"null at {path}/{k}"
            no_nulls(x, f"{path}/{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            no_nulls(x, f"{path}[{i}]")


def safe_url(u: str) -> bool:      # safeUrl(): root-relative or http(s)
    return (u.startswith("/") and not u.startswith("//")) or re.match(r"https?://", u) is not None


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        event, data = "message", []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data:
            out.append((event, json.loads("\n".join(data))))
    return out


BODIES: dict[str, object] = {}      # bodies collected for the run against the real TypeScript guards


def keep(name: str, body):
    BODIES[name] = body
    return body


# ---------------------------------------------------------------------------------------------- a framed drawing
F_DXF, F_PNG = "c" * 15 + "1", "c" * 15 + "2"
FRAMED = "aura plan/level 1"          # a key that is not URL-safe


@pytest.fixture(scope="module")
def framed(seeded: dict) -> dict:
    """A drawing as a DXF in millimetres (no render) and a PNG registered in the same frame: 800 x 400 px at 50 px
    per metre, y down, the frame's origin in the image's bottom-left corner."""
    from PIL import Image
    Image.new("RGB", (800, 400), "white").save(seeded["data"] / "renders" / "framed.png")
    with DB() as db:
        db.run("delete from jstore.issue where project_id = 'test/aura'")
        db.run("delete from jstore.project where project_id = 'test/aura'")
        db.run("insert into jstore.project (project_id, title, source) values ('test/aura', 'aura', 'test')")
        for fid, path, fmt in ((F_DXF, "uploads/seed/f.dxf", "dxf"), (F_PNG, "uploads/seed/f.png", "png")):
            db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status, meta)
                      values (:f, 'test/aura', :p, :fmt, 6, :k, 'interpreted', '{"scale": "1:100"}')""", f=fid, p=path, fmt=fmt, k=FRAMED)
        page = """insert into jstore.page (file_id, page_no, name, width, height, unit, image, image_w, image_h, to_image)
                  values (:f, 0, :n, :w, :h, :u, :i, :iw, :ih, cast(:t as double precision[]))"""
        db.run(page, f=F_DXF, n="model", w=16000, h=8000, u="unit", i=None, iw=None, ih=None, t=None)
        db.run(page, f=F_PNG, n="scan", w=800, h=400, u="px", i="renders/framed.png", iw=800, ih=400, t=arr([1, 0, 0, 0, 1, 0]))
        frame = "insert into jstore.frame (file_id, page_no, m, method) values (:f, 0, cast(:m as double precision[]), :how)"
        db.run(frame, f=F_DXF, m=arr([0.001, 0, 0, 0, 0.001, 0]), how="units")
        db.run(frame, f=F_PNG, m=arr([0.02, 0, 0, 0, -0.02, 8]), how="twin_dpi")
        ent = """insert into jstore.entity (project_id, drawing_key, kind, number, label, label_norm, zone, host, geom, attrs, duplicate_of)
                 values ('test/aura', :k, :kind, :n, :l, :ln, :z, :h, ST_GeomFromText(:g), cast(:a as jsonb), :d) returning entity_id"""

        def add(kind, g, n=None, l=None, ln=None, z="A", h=None, a="{}", d=None):
            return db.one(ent, k=FRAMED, kind=kind, n=n, l=l, ln=ln, z=z, h=h, g=g, a=a, d=d)["entity_id"]
        r1 = add("room", "POLYGON((2 2, 6 2, 6 6, 2 6, 2 2))", n="1.01", l="Séjour", ln="living", a=js({"indoor": True}))
        r2 = add("room", "POLYGON((6 2, 10 2, 10 4, 6 4, 6 2))", n="1.02", l="Balkon", ln="balcony", a=js({"indoor": False}))
        r3 = add("room", "POLYGON((10 2, 12 2, 12 4, 10 4, 10 2))", n="1.03", l="Bad", ln="bathroom", z="B", a=js({"indoor": True}))
        ap = add("apartment", "POLYGON((2 2, 10 2, 10 6, 2 6, 2 2))", n="A", l="Whg. A", z=None)
        d1 = add("door", "POLYGON((5.9 3, 6.1 3, 6.1 3.9, 5.9 3.9, 5.9 3))", h="1.01")
        d2 = add("door", "POLYGON((5.9 3, 6.1 3, 6.1 3.9, 5.9 3.9, 5.9 3))", h="1.01", d=d1)
        w1 = add("window", "LINESTRING(3 6, 5 6)", h="1.01")
        tub = add("bathtub", "POLYGON((10.2 2.2, 11.8 2.2, 11.8 2.9, 10.2 2.9, 10.2 2.2))", h="1.03", z="B")
        odd = add("urinal", "POLYGON((11 3, 11.4 3, 11.4 3.4, 11 3.4, 11 3))", h="1.03", z="B")
        src = """insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role)
                 values (:e, :f, 0, 'cad_entity', :k, 'outline') returning id"""
        claim = """insert into jstore.claim (entity_id, property, value_num, unit, file_id, method, source_id, confidence, rank)
                   values (:e, 'net_area_m2', :v, 'm2', :f, :m, :s, 0.9, :r)"""
        for e, v in ((r1, 16.0), (r2, 8.0)):
            s = db.one(src, e=e, f=F_DXF, k=f"H{e}")["id"]
            db.run(claim, e=e, v=v, f=F_DXF, m="printed", s=s, r="preferred")
            db.run(claim, e=e, v=v * 1.2, f=F_PNG, m="measured_outline", s=None, r="normal")     # the scan disagrees
        db.run("""insert into jstore.issue (project_id, drawing_key, entity_id, property, kind, detail)
                  values ('test/aura', :k, :e, 'net_area_m2', 'value_conflict', 'scan differs by more than 2 %')""", k=FRAMED, e=r1)
        db.run("""insert into jstore.cad_entity (file_id, handle, dxftype, layer, geom)
                  values (:f, 'W1', 'LINE', 'A-GLAZ', ST_GeomFromText('LINESTRING(3000 6000, 5000 6000)'))""", f=F_DXF)
    yield dict(rooms=[r1, r2, r3], apartment=ap, doors=[d1, d2], window=w1, tub=tub, odd=odd, id=am.drawing_id(FRAMED))
    with DB() as db:
        db.run("delete from jstore.issue where project_id = 'test/aura'")
        db.run("delete from jstore.project where project_id = 'test/aura'")


# ---------------------------------------------------------------------------------------------- ids and auth
def test_drawing_ids_are_url_safe_and_reversible() -> None:
    for key in ("site1000_bldg2082_floor9385", "colorful-11397", FRAMED, "20150922 - XSC - A-100.A - SITE PLAN",
                "plan~1a2b3c4d", "k.x", "ä/ö?#%", ".hidden"):
        did = am.drawing_id(key)
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+", did) and am.drawing_key(did) == key
    assert am.drawing_id("site1000_bldg2082_floor9385") == "site1000_bldg2082_floor9385"      # readable when possible
    assert am.drawing_key("k.!!!") is None and am.drawing_key("a/b") is None and am.drawing_key("") is None


async def test_the_token_rides_in_the_base_url(make_client, seeded) -> None:
    """The frontend sends no credentials: VITE_API_URL carries the token as a path prefix."""
    client = make_client(auth=False)
    assert (await client.get("/aura/drawings")).status_code == 401
    assert (await client.get("/aura/health")).status_code == 401
    assert (await client.get("/aura/t/wrong/drawings")).status_code == 401
    assert (await client.get("/aura/t//drawings")).status_code in (401, 404)
    assert (await client.get(f"{BASE}/drawings")).status_code == 200
    assert (await client.get("/aura/drawings", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
    assert (await client.get("/aura/drawings", params={"token": TOKEN})).status_code == 200
    assert (await client.get(f"/aura/t/{TOKEN}")).status_code in (404, 405)        # nothing is served at the prefix itself
    assert (await client.get(f"/t/{TOKEN}/drawings")).status_code == 401           # the prefix only exists under /aura
    health = (await client.get(f"{BASE}/health")).json()
    assert health["ok"] is True and health["contract_version"].split(".")[0] == "1" and TOKEN not in json.dumps(health)


def test_the_path_token_never_reaches_the_access_log() -> None:
    """The server logs scope["path"]; the outermost middleware rewrites it before any other layer runs."""
    scope = dict(type="http", path=f"/aura/t/{TOKEN}/drawings/x", raw_path=f"/aura/t/{TOKEN}/drawings/x".encode())
    assert aura.path_token(scope) == TOKEN and scope["path"] == "/aura/drawings/x" and scope["raw_path"] == b"/aura/drawings/x"
    assert aura.path_token(scope) == TOKEN                                  # asked again further in: still known
    assert aura.path_token(dict(type="http", path="/drawings")) is None


async def test_cors_for_a_local_dev_server_only(make_client, seeded) -> None:
    client = make_client(auth=False)
    pre = await client.options(f"{BASE}/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                                                        "Access-Control-Request-Headers": "content-type,x-aura-presenter"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "http://localhost:5173"
    ok = await client.get(f"{BASE}/health", headers={"Origin": "http://127.0.0.1:4173"})
    assert ok.headers["access-control-allow-origin"] == "http://127.0.0.1:4173"
    denied = await client.get("/aura/health", headers={"Origin": "http://localhost:5173"})
    assert denied.status_code == 401 and "access-control-allow-origin" in denied.headers       # the page can read the 401
    other = await client.get(f"{BASE}/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in other.headers


# ---------------------------------------------------------------------------------------------- drawings
async def test_library_lists_drawings_not_uploads(make_client, seeded, framed) -> None:
    client = make_client()
    body = keep("list", (await client.get("/aura/drawings")).json())
    assert body["contract_version"] == "1.1" and all(map(is_summary, body["drawings"]))
    no_nulls(body)
    by_id = {d["id"]: d for d in body["drawings"]}
    assert {"test_a", "test_b", framed["id"]} <= set(by_id)
    assert by_id["test_a"]["kind"] == "cad_dxf" and by_id["test_a"]["status"] == "ready"
    assert by_id["test_a"]["thumbnail"] == "/drawings/test_a/thumb.png" and by_id["test_b"]["thumbnail"] == ""   # no render
    assert all(d.get("source") == "library" for d in body["drawings"])
    with DB() as db:
        uploads = {r["drawing_key"] for r in db.run("select drawing_key from jstore.source_file where project_id like 'upload/%'")}
    assert not {am.drawing_id(k) for k in uploads} & set(by_id)


async def test_drawing_placed_through_its_sources(make_client, seeded) -> None:
    """test_a has no frame on a rendered page: rooms and boxes come from the Layer 1 primitives of the page shown."""
    client = make_client()
    d = keep("drawing_sources", (await client.get("/aura/drawings/test_a")).json())
    assert is_drawing(d), d
    no_nulls(d)
    assert d["frame"]["px"] == [1000, 500] and d["images"] == {"rebuilt": "/drawings/test_a/plan.svg",
                                                               "original": "/drawings/test_a/original.png",
                                                               "original_alignment": "exact"}
    assert all(safe_url(u) for u in (d["images"]["rebuilt"], d["images"]["original"], d["thumbnail"]))
    room, = d["rooms"]
    # R1 is the box (10,10)-(30,20) in drawing units, rendered at 10 px per unit with y flipped on 1000 x 500 px
    assert room["room_id"] == room["num"] == "1.01" and room["name"] == "Küche" and room["unit"] == "A"
    assert sorted(map(tuple, room["points"])) == [(10.0, 60.0), (10.0, 80.0), (30.0, 60.0), (30.0, 80.0)]
    assert room["area_m2"] == 12.6 and room["area_source"] == "printed" and room["outdoor"] is False
    assert 10 < room["label_at"][0] < 30 and 60 < room["label_at"][1] < 80
    assert {"door", "room", "apartment"} <= set(d["capabilities"]) and d["units"] == [{"id": "A", "label": "Apartment A"}]
    ev = (await client.get("/aura/drawings/test_a/evidence.json")).json()
    doors = ev["boxes"]["door"]
    assert [b["id"] for b in doors] == ["door-01", "door-02"] and all(map(is_box, doors))
    # reading order: D2 (29,15)-(30,16) lies above D1 (10,10)-(11,11) on the page
    assert (doors[0]["x"], doors[0]["y"], doors[0]["w"], doors[0]["h"]) == (29.0, 68.0, 1.0, 2.0)
    assert doors[1]["y"] == 78.0 and doors[0]["room_id"] == "1.01" and doors[0]["unit"] == "A"


async def test_drawing_placed_through_the_frames(make_client, framed) -> None:
    """The framed drawing is shown on its scan: entity geometry in metres goes through frame and to_image."""
    client = make_client()
    r = await client.get(f"/aura/drawings/{framed['id']}")
    d = keep("drawing_framed", r.json())
    assert r.status_code == 200 and is_drawing(d), d
    no_nulls(d)
    assert d["id"] == framed["id"] and d["id"].startswith("k.") and d["frame"]["px"] == [800, 400]
    assert d["m_per_pct_x"] == pytest.approx(0.16) and d["m_per_pct_y"] == pytest.approx(0.08)   # 16 m x 8 m sheet
    assert d["kind"] == "cad_dxf" and d["subtitle"] == "test · DXF + PNG · 1:100"
    rooms = {x["room_id"]: x for x in d["rooms"]}
    # room 1.01 is (2,2)-(6,6) m: x 12.5-37.5 % of 16 m; y is flipped: 6 m up is 25 %, 2 m up is 75 %
    assert sorted(map(tuple, rooms["1.01"]["points"])) == [(12.5, 25.0), (12.5, 75.0), (37.5, 25.0), (37.5, 75.0)]
    assert rooms["1.01"]["area_m2"] == 16.0 and rooms["1.02"]["outdoor"] is True
    assert rooms["1.03"]["area_m2"] is None and "area_source" not in rooms["1.03"]        # unknown stays null, never 0
    assert d["units"] == [{"id": "A", "label": "Whg. A"}, {"id": "B", "label": "Apartment B"}]
    assert set(d["capabilities"]) >= {"door", "window", "bathtub", "room", "balcony", "bathroom", "apartment"}
    model = keep("model", (await client.get(f"/aura/drawings/{framed['id']}/model.json")).json())
    items = {i["id"]: i for i in model["items"]}
    assert set(items) == {"door-01", "door-02", "win-01", "tub-01", "urinal-01"} and model["unplaced"] == []
    assert items["door-02"]["duplicate_of"] == "door-01" and items["door-01"]["box"] == items["door-02"]["box"]
    assert items["door-01"]["box"] == {"x": 36.88, "y": 51.25, "w": 1.25, "h": 11.25}
    assert items["win-01"]["box"]["h"] == 0.5 and items["win-01"]["box"]["w"] == 12.5      # a stroke still gets a box
    assert items["urinal-01"]["subject"] == "label" and items["tub-01"]["room_id"] == "1.03"
    assert model["page"]["file_id"] == F_PNG and model["page"]["frame_method"] == "twin_dpi"
    room = next(x for x in model["rooms"] if x["room_id"] == "1.01")
    assert room["agreement"] == "conflict" and {s["method"] for s in room["sources"]} == {"printed", "measured_outline"}
    checks = keep("checks", (await client.get(f"/aura/drawings/{framed['id']}/checks.json")).json())
    assert checks["summary"]["issues"] == 1 and checks["summary"]["rooms_conflict"] == 2
    assert checks["checks"][0]["evidence_id"] == "room-1.01" and checks["checks"][0]["kind"] == "value_conflict"
    file = keep("drawing_file", (await client.get(f"/aura/drawings/{framed['id']}/drawing.json")).json())
    assert is_drawing(file) and all(is_box(b) for boxes in file["evidence"].values() for b in boxes)
    assert {"window", "door", "washbasin"} <= set(file["evidence"]) and isinstance(file["gold"], list)   # isDrawingFile


async def test_images_of_a_drawing(make_client, seeded, framed) -> None:
    client = make_client()
    svg = await client.get("/aura/drawings/test_a/plan.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.text.startswith("<svg") and 'viewBox="0 0 1000 500.00"' in svg.text and "1.01" in svg.text and "<script" not in svg.text
    assert "sandbox" in svg.headers["content-security-policy"] and svg.headers["x-content-type-options"] == "nosniff"
    assert "M 100 -400" not in svg.text and re.search(r'<path d="M 100 400', svg.text)      # the room outline R1, y down
    framed_svg = (await client.get(f"/aura/drawings/{framed['id']}/plan.svg")).text
    assert "Séjour" in framed_svg and "16.00 m²" in framed_svg
    for name in ("original.png", "thumb.png"):
        r = await client.get(f"/aura/drawings/test_a/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (seeded["data"] / "aura" / "thumbs" / f"{A1}-0.png").is_file()        # cached under the store directory
    r = await client.get("/aura/drawings/test_a/secret.json")
    assert r.status_code == 404 and is_error(r.json()) and r.json()["code"] == "not_found"
    # a drawing without any render is still a valid, empty-handed drawing
    d = keep("drawing_bare", (await client.get("/aura/drawings/test_b")).json())
    assert is_drawing(d) and d["images"] == {"rebuilt": "/drawings/test_b/original.png"} and "thumbnail" not in d
    assert d["rooms"][0]["points"] == [] and d["rooms"][0]["area_m2"] == 77.7
    r = await client.get("/aura/drawings/test_b/original.png")
    assert r.status_code == 404 and is_error(r.json())


async def test_unknown_drawing(make_client, seeded) -> None:
    client = make_client()
    for path in ("/aura/drawings/nope", "/aura/drawings/nope/status", "/aura/drawings/nope/plan.svg", "/aura/drawings/k.!!"):
        r = await client.get(path)
        assert r.status_code == 404 and is_error(r.json()) and r.json()["code"] == "unknown_drawing", path
    r = await client.post("/aura/ask", json=dict(drawing_id="nope", question="How many doors?"))
    assert r.status_code == 404 and r.json()["code"] == "unknown_drawing"
    r = await client.post("/aura/chat", json=dict(drawing_id="nope", message="How many doors?"))
    assert r.status_code == 404 and is_error(r.json())
    st = (await client.get("/aura/drawings/test_a/status")).json()
    assert st == {"contract_version": "1.1", "id": "test_a", "status": "ready"}


async def test_status_follows_the_files(make_client, seeded) -> None:
    late, lone = "d" * 15 + "1", "d" * 15 + "2"
    add = """insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status)
             values (:f, 'test/seed', :p, 'ifc', 6, :k, 'registered')"""
    client = make_client()
    try:
        with DB() as db:
            db.run(add, f=late, p="uploads/seed/late.ifc", k="test_a")       # a second file of a drawing that is usable
            db.run(add, f=lone, p="uploads/seed/lone.ifc", k="test_wait")    # a drawing with nothing parsed yet
        assert (await client.get("/aura/drawings/test_a/status")).json()["status"] == "ready"
        assert (await client.get("/aura/drawings/test_a")).status_code == 200
        st = (await client.get("/aura/drawings/test_wait/status")).json()
        assert st == {"contract_version": "1.1", "id": "test_wait", "status": "processing"}
        r = await client.get("/aura/drawings/test_wait")
        assert r.status_code == 409 and is_error(r.json()) and r.json()["code"] == "processing"      # the frontend then polls
        listed = {d["id"]: d["status"] for d in (await client.get("/aura/drawings")).json()["drawings"]}
        assert listed["test_wait"] == "processing" and listed["test_a"] == "ready"
        with DB() as db:
            db.run("update jstore.source_file set status = 'failed', error = 'ValueError: not a model' where file_id = :f", f=lone)
        st = (await client.get("/aura/drawings/test_wait/status")).json()
        assert st["status"] == "failed" and st["error"] == "ValueError: not a model"
    finally:
        with DB() as db:
            db.run("delete from jstore.source_file where file_id = any(cast(:f as text[]))", f=[late, lone])


# ---------------------------------------------------------------------------------------------- answers
DOORS = "select count(*) as n, array_agg(entity_id) as ids from q.doors"


def check_response(r: dict) -> dict:
    assert is_ask_response(r), r
    no_nulls(r)
    known = {b["id"] for b in r["evidence"]["boxes"]} | {f"room-{x['room_id']}" for x in r["evidence"]["rooms"]}
    for node in r["trace"]:                       # obligation 8: a trace can only light evidence the response carries
        assert set(node.get("evidence_ids", [])) <= known
    assert r["source"] == "api" and r["contract_version"] == "1.1" and len(r["answer_id"]) == 16
    return r


async def test_ask_counts_with_boxes(make_client, framed) -> None:
    client = make_client(script(call("sql", query=DOORS), final(answer=2, unit="count", sql=DOORS, entity_ids=framed["doors"])))
    r = await client.post("/aura/ask", json=dict(drawing_id=framed["id"], question="Combien de portes y a-t-il ?"))
    assert r.status_code == 200, r.text
    body = check_response(keep("ask_count", r.json()))
    assert body["answer"] == {"kind": "count", "value": 2, "subject": "door"} and body["method"] == "cad_geometry"
    boxes = body["evidence"]["boxes"]
    assert [b["id"] for b in boxes] == ["door-01", "door-02"] and boxes[1]["duplicate_of"] == "door-01"
    assert body["detected_lang"] == "fr" and body["scope"] == {"kind": "unit", "unit": "A"} and body["calls"] == 2
    assert [n["id"] for n in body["trace"]] == ["q", "t1", "answer"] and body["trace"][1]["body"] == DOORS
    assert body["trace"][-1]["evidence_ids"] == ["door-01", "door-02"] and body["route"] == "agent"
    csv = await client.get(f"/aura/answers/{body['answer_id']}/evidence.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.split("\r\n")
    assert lines[0].startswith("kind,id,subject,room_id,room_name") and lines[1].startswith("box,door-01,door,1.01,Séjour,A,36.88")
    assert (await client.get("/aura/answers/0000000000000000/evidence.csv")).json()["code"] == "not_found"


async def test_ask_areas_text_quantities_and_abstains(make_client, framed) -> None:
    r1, r2, r3 = framed["rooms"]
    area = "select sum(area_m2) as m2, array_agg(entity_id) as ids from q.rooms where is_indoor"

    async def ask(question, scope=None, **answer):
        client = make_client(script(call("sql", query=area), final(sql=area, **answer)))
        r = await client.post("/aura/ask", json=dict(drawing_id=framed["id"], question=question, **({"scope": scope} if scope else {})))
        assert r.status_code == 200, r.text
        return check_response(r.json())

    # net area: the indoor rooms are summed, the balcony is listed and left out, the room without an area never counts
    b = keep("ask_area", await ask("What is the net floor area?", answer=16.0, unit="m2", entity_ids=[r1, r2, r3]))
    assert b["answer"] == {"kind": "area", "value": 16.0, "unit": "m2"}
    assert b["evidence"]["rooms"] == [{"room_id": "1.01", "included": True, "area_m2": 16.0},
                                      {"room_id": "1.02", "included": False, "area_m2": 8.0},
                                      {"room_id": "1.03", "included": False, "area_m2": None}]
    assert sum(x["area_m2"] for x in b["evidence"]["rooms"] if x["included"]) == b["answer"]["value"]   # the UI recomputes it
    assert "conflict" in b["explanation"] and any(n["id"] == "sources" and n["kind"] == "warn" for n in b["trace"])
    # an apartment stands for its rooms; with the balcony the sum is the gross one
    b = await ask("Area of apartment A with balcony?", answer=24.0, unit="m2", entity_ids=[framed["apartment"]])
    assert b["answer"]["kind"] == "area" and [x["included"] for x in b["evidence"]["rooms"]] == [True, True]
    assert b["scope"] == {"kind": "unit", "unit": "A"}
    # a number that is not the sum of its rooms is given as it is
    b = await ask("Average room area?", answer=12.0, unit="m2", entity_ids=[r1, r2])
    assert b["answer"] == {"kind": "quantity", "value": 12.0, "unit": "m2"}
    b = await ask("How long is the window?", answer=2.0, unit="m", entity_ids=[framed["window"]])
    assert b["answer"] == {"kind": "quantity", "value": 2.0, "unit": "m"} and b["evidence"]["boxes"][0]["id"] == "win-01"
    # counts of rooms, room kinds, apartments, and of things the contract has no subject for
    b = keep("ask_rooms", await ask("How many rooms?", answer=3, unit="count", entity_ids=[r1, r2, r3]))
    assert b["answer"] == {"kind": "count", "value": 3, "subject": "room"} and len(b["evidence"]["rooms"]) == 3
    b = await ask("How many bathrooms?", answer=1, unit="count", entity_ids=[r3])
    assert b["answer"]["subject"] == "bathroom" and b["scope"] == {"kind": "unit", "unit": "B"}
    b = await ask("How many apartments?", answer=1, unit="count", entity_ids=[framed["apartment"]])
    assert b["answer"]["subject"] == "apartment" and {x["room_id"] for x in b["evidence"]["rooms"]} == {"1.01", "1.02"}
    b = await ask("How many windows are there?", answer=0, unit="count", entity_ids=[])
    assert b["answer"] == {"kind": "count", "value": 0, "subject": "window"} and b["evidence"] == {"boxes": [], "rooms": []}
    b = keep("ask_text", await ask("How many urinals?", answer=1, unit="count", entity_ids=[framed["odd"]]))
    assert b["answer"] == {"kind": "text", "value": "1", "subject": "label"}
    assert b["evidence"]["boxes"] == [{"id": "urinal-01", "x": 68.75, "y": 57.5, "w": 2.5, "h": 5.0, "subject": "label",
                                       "room_id": "1.03", "unit": "B", "note": "urinal"}]
    b = await ask("Which room is the largest?", answer="1.01 Séjour", unit=None, entity_ids=[r1])
    assert b["answer"] == {"kind": "text", "value": "1.01 Séjour", "subject": "label"} and b["evidence"]["rooms"][0]["included"]
    b = keep("ask_abstain", await ask("What is the fire rating of the door?", answer=None, unit=None, method="No property holds it."))
    assert b["answer"] == {"kind": "abstain", "reason": "not_on_drawing", "where_to_find": "No property holds it."}
    assert b["method"] == "none" and b["trace"][-1]["kind"] == "warn"
    # Layer 1 references: a stroke of the DXF (millimetres) lands on the scan through the two frames; what has no
    # place on the page shown is listed as a row
    refs = [dict(file_id=F_DXF, ref_table="cad_entity", ref_key="W1"), dict(file_id=F_DXF, ref_table="cad_entity", ref_key="ZZ")]
    b = keep("ask_refs", await ask("Which layer holds the glazing?", answer="A-GLAZ", unit=None, entity_ids=[], refs=refs))
    assert b["answer"]["kind"] == "text" and b["evidence"]["rows"] == [{"id": "cad_entity:ZZ", "name": "ZZ"}]
    assert b["evidence"]["boxes"] == [{"id": "text-01", "x": 18.75, "y": 24.75, "w": 12.5, "h": 0.5, "subject": "label", "note": "A-GLAZ"}]
    # a count answered from Layer 1: the referenced strokes are the things counted
    b = await ask("How many sinks?", answer=1, unit="count", entity_ids=[], refs=refs[:1])
    assert b["answer"] == {"kind": "count", "value": 1, "subject": "sink"}
    assert [(x["id"], x["subject"], x["note"]) for x in b["evidence"]["boxes"]] == [("sink-01", "sink", "A-GLAZ")]
    # the UI's scope is echoed and handed to the agent in words it can turn into SQL
    region = {"kind": "region", "rect": {"x": 25, "y": 25, "w": 25, "h": 50}, "label": "Area 1"}
    b = await ask("How many doors here?", scope=region, answer=2, unit="count", entity_ids=framed["doors"])
    assert b["scope"] == region


async def test_scope_and_what_if_reach_the_agent(make_client, framed) -> None:
    with DB() as db:
        m = am.build(db, FRAMED)
    region = aura.clean_scope({"kind": "region", "rect": {"x": 25, "y": 25, "w": 25, "h": 50}, "label": "Area 1"})
    text = aura.compose(m, "How many doors here?", region, [{"room_id": "1.01", "w_m": 4, "h_m": 5}], [dict(q="Rooms?", a="3 room")])
    assert "ST_MakeEnvelope(4.000, 2.000, 8.000, 6.000)" in text            # 25-50 % of 16 m, 25-75 % of 8 m with y flipped
    assert "4 m x 5 m" in text and "20 m2" in text and f"entity_id {framed['rooms'][0]}" in text and "Q: Rooms? A: 3 room" in text
    assert "apartment 'B'" in aura.compose(m, "x", aura.clean_scope({"kind": "unit", "unit": "B"}), None, [])
    assert aura.clean_scope({"kind": "region", "rect": {"x": "1"}}) == {"kind": "floor"} and aura.clean_scope(None) == {"kind": "floor"}
    assert [aura.detect_lang(q) for q in ("How many doors are there?", "Combien de fenêtres dans la cuisine ?",
                                          "Wie viele Türen gibt es?", "Montako ovea on asunnossa A?")] == ["en", "fr", "de", "fi"]
    assert aura.detect_lang("3.04?", "fi") == "fi" and aura.detect_lang("???") == "en"
    assert [aura.guess_subject(q) for q in ("How many bedrooms are there?", "Combien de fenêtres ?", "Montako saunaa?",
                                            "Wie viele Wohnungen?", "What is the scale?")] == ["bedroom", "window", "sauna", "apartment", None]


async def test_chat_stream_and_conversation(make_client, framed) -> None:
    model = script(call("sql", query=DOORS), final(answer=2, unit="count", sql=DOORS, entity_ids=framed["doors"]),
                   call("sql", n=2, query=DOORS), final(answer=1, unit="count", sql=DOORS, entity_ids=[framed["tub"]]))
    client = make_client(model)
    r = await client.post("/aura/chat", json=dict(drawing_id=framed["id"], message="How many doors?", conversation_id="made-up",
                                                  lang_hint="en"), headers={"accept": "text/event-stream"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = keep("chat_events", sse(r.text))
    assert [e for e, _ in events] == ["turn", "understood", "tool_call", "tool_result", "answer", "done"]
    turn, understood, tool_call, tool_result, answer, done = (d for _, d in events)
    conv = turn["conversation_id"]
    assert conv != "made-up" and is_str(turn["turn_id"])                 # the server issues conversation ids
    assert understood == {"detected_lang": "en", "canonical_en": "How many doors?", "route": "agent"}
    assert is_trace_node(tool_call["node"]) and tool_result["node_id"] == tool_call["node"]["id"]
    assert tool_result["evidence"] == {"boxes": [], "rooms": []} and is_str(tool_result["summary"]) and done == {}
    first = check_response(answer["response"])
    assert first["answer"]["value"] == 2 and first["conversation_id"] == conv and first["turn_id"] == turn["turn_id"]
    r = await client.post("/aura/chat", json=dict(drawing_id=framed["id"], message="And bathtubs?", conversation_id=conv))
    second = sse(r.text)
    assert second[0][1]["conversation_id"] == conv and second[-2][1]["response"]["answer"] == {"kind": "count", "value": 1, "subject": "bathtub"}
    run = client.app.state.registry.runs[sorted(client.app.state.registry.runs, key=lambda k: client.app.state.registry.runs[k].created)[-1]]
    assert "Earlier in this conversation" in run.questions[0] and "Q: How many doors? A: 2 door" in run.questions[0]
    stored = keep("conversation", (await client.get(f"/aura/conversations/{conv}")).json())
    assert stored["id"] == conv and stored["drawing_id"] == framed["id"] and stored["contract_version"] == "1.1"
    assert [t["message"] for t in stored["turns"]] == ["How many doors?", "And bathtubs?"]
    assert all(is_ask_response(t["response"]) and is_str(t["turn_id"]) for t in stored["turns"])
    assert (await client.get("/aura/conversations/unknown")).status_code == 404
    again = make_client()                                                 # a restarted service still knows the conversation
    assert len((await again.get(f"/aura/conversations/{conv}")).json()["turns"]) == 2
    other = sse((await again.post("/aura/chat", json=dict(drawing_id="test_a", message="Doors?", conversation_id=conv))).text)
    assert other[0][1]["conversation_id"] != conv                         # a conversation belongs to one drawing


async def test_a_failed_run_is_an_error_not_an_answer(make_client, framed) -> None:
    client = make_client(script("not an answer"), max_turns=1)
    events = sse((await client.post("/aura/chat", json=dict(drawing_id=framed["id"], message="Doors?"))).text)
    assert [e for e, _ in events] == ["turn", "understood", "error", "done"]
    assert events[2][1]["code"] == "internal" and is_str(events[2][1]["error"]) and is_num(events[2][1]["calls"])
    r = await make_client(script("not an answer"), max_turns=1).post("/aura/ask", json=dict(drawing_id=framed["id"], question="Doors?"))
    assert r.status_code == 502 and is_error(r.json()) and r.json()["code"] == "internal"
    r = await client.post("/aura/ask", json=dict(drawing_id=framed["id"], question=""))
    assert r.status_code == 422 and "detail" in r.json()                 # FastAPI's body, which the frontend also reads


# ---------------------------------------------------------------------------------------------- uploads and stubs
async def test_upload_status_open_delete(make_client, tmp_path) -> None:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    doc.modelspace().add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True, dxfattribs={"layer": "ROOM_OUTLINE"})
    doc.saveas(tmp_path / "plan aura.dxf")
    client = make_client()
    r = await client.post("/aura/uploads", files={"file": ("plan aura.dxf", (tmp_path / "plan aura.dxf").read_bytes())})
    assert r.status_code == 202, r.text
    up = keep("upload", r.json())
    assert up["status"] == "queued" and is_str(up["drawing_id"]) and len(up["delete_token"]) >= 24      # isUploadResult
    did = up["drawing_id"]
    try:
        await client.app.state.ingestor.wait([am.drawing_key(did) and client.app.state.aura.upload(did)["file_id"]], 120)
        st = (await client.get(f"/aura/drawings/{did}/status")).json()
        assert st["status"] == "ready" and st["id"] == did, st
        d = (await client.get(f"/aura/drawings/{did}")).json()
        assert is_drawing(d) and d["source"] == "upload" and d["title"].startswith("plan aura")
        assert (await client.get(f"/aura/drawings/{did}/original.png")).headers["cache-control"] == "private, no-store"
        assert did not in {x["id"] for x in (await client.get("/aura/drawings")).json()["drawings"]}    # never listed
        assert (await client.delete(f"/aura/drawings/{did}", headers={"X-Delete-Token": "wrong"})).status_code == 404
        assert (await client.delete("/aura/drawings/test_a", headers={"X-Delete-Token": up["delete_token"]})).status_code == 404
    finally:
        r = await client.delete(f"/aura/drawings/{did}", headers={"X-Delete-Token": up["delete_token"]})
    assert r.status_code == 204
    assert (await client.get(f"/aura/drawings/{did}")).status_code == 404
    assert (await client.get("/aura/drawings/test_a")).status_code == 200            # nothing else was touched
    r = await client.post("/aura/uploads", files={"file": ("notes.txt", b"hello")})
    assert r.status_code == 415 and r.json()["code"] == "unsupported_type"
    r = await client.post("/aura/uploads", files={"file": ("empty.dxf", b"")})
    assert r.status_code == 400 and is_error(r.json())


async def test_optional_endpoints_answer_with_valid_bodies(make_client, framed) -> None:
    client = make_client()
    b = keep("budget", (await client.get("/aura/budget")).json())
    assert b["models"] in ("on", "off") and all(is_num(b[k]) and b[k] >= 0 for k in ("day_used", "day_cap", "event_used", "event_cap"))
    s = (await client.get("/aura/stats")).json()
    assert is_num(s["answers_today"]) and is_num(s["model_calls_today"]) and s["contract_version"] == "1.1"
    r = keep("rewrite", (await client.post("/aura/rewrite", json=dict(drawing_id=framed["id"], text="so  um how many doors"))).json())
    assert r["drawing_id"] == framed["id"] and r["detected_lang"] in LANGS and r["not_answerable"] == [] and r["model"] == "none"
    assert r["blocks"] == [{"question": "so um how many doors", "intent": "count", "subject": "door", "scope": {"kind": "floor"},
                            "scope_exists": True, "routable": True}]
    assert all(is_num(r[k]) for k in ("calls", "cost_usd", "tokens"))
    assert (await client.post("/aura/admin/models", json={"on": False})).json()["models"] is True     # a stub: nothing switches


# ---------------------------------------------------------------------------------------------- a model: one view per storey
M_IFC, M_PNG, M_ONE = "d" * 15 + "1", "d" * 15 + "2", "d" * 15 + "3"
MODEL, SINGLE = "aura_model", "aura_single"


@pytest.fixture(scope="module")
def model(seeded: dict) -> dict:
    """An IFC model with four storeys in a georeferenced frame (x from 100 m, y from 200 m): a foundation with one
    footing, '1. Floor' (a room, a door, a window, walls), '2.Floor' (a room, two doors, a wall) and an empty roof;
    plus a scan of the second floor (file storey '2-floor': 600 x 400 px at 50 px per metre, y down) registered
    in the model's frame.  And a second model with one storey."""
    from PIL import Image
    Image.new("RGB", (600, 400), "white").save(seeded["data"] / "renders" / "model2.png")
    with DB() as db:
        db.run("delete from jstore.issue where project_id = 'test/model'")
        db.run("delete from jstore.project where project_id = 'test/model'")
        db.run("insert into jstore.project (project_id, title, source) values ('test/model', 'model', 'test')")
        for fid, path, fmt, key, storey in ((M_IFC, "uploads/seed/m.ifc", "ifc", MODEL, None),
                                            (M_PNG, "uploads/seed/m-2-floor.png", "png", MODEL, "2-floor"),
                                            (M_ONE, "uploads/seed/one.ifc", "ifc", SINGLE, None)):
            db.run("""insert into jstore.source_file (file_id, project_id, path, format, bytes, drawing_key, status, storey)
                      values (:f, 'test/model', :p, :fmt, 6, :k, 'interpreted', :s)""", f=fid, p=path, fmt=fmt, k=key, s=storey)
        el = """insert into jstore.ifc_element (file_id, guid, step_id, ifc_class, name, storey, storey_guid, attrs)
                values (:f, :g, :n, :c, :name, :st, :sg, cast(:a as jsonb))"""
        page = """insert into jstore.page (file_id, page_no, name, unit, image, image_w, image_h, to_image, meta)
                  values (:f, :n, :name, :u, :i, :iw, :ih, cast(:t as double precision[]), cast(:m as jsonb))"""
        frame = "insert into jstore.frame (file_id, page_no, m, method) values (:f, :n, cast(:m as double precision[]), :how)"
        names = ("Foundation", "1. Floor", "2.Floor", "Roof")
        for n, name in enumerate(names):
            db.run(el, f=M_IFC, g=f"S{n}", n=n, c="IfcBuildingStorey", name=name, st=None, sg=None, a=js({"Elevation": -1 + 3.0 * n}))
            db.run(page, f=M_IFC, n=n, name=name, u="m", i=None, iw=None, ih=None, t=None, m=js({"guid": f"S{n}"}))
            db.run(frame, f=M_IFC, n=n, m=arr([1, 0, 0, 0, 1, 0]), how="native")
        db.run(el, f=M_ONE, g="S0", n=0, c="IfcBuildingStorey", name="Ground", st=None, sg=None, a="{}")
        db.run(page, f=M_ONE, n=0, name="Ground", u="m", i=None, iw=None, ih=None, t=None, m=js({"guid": "S0"}))
        db.run(page, f=M_PNG, n=0, name="scan", u="px", i="renders/model2.png", iw=600, ih=400, t=arr([1, 0, 0, 0, 1, 0]), m="{}")
        db.run(frame, f=M_PNG, n=0, m=arr([0.02, 0, 98, 0, -0.02, 206]), how="model_anchors")
        shape = """insert into jstore.ifc_geometry (file_id, guid, footprint, footprint_m2) values (:f, :g, ST_GeomFromText(:w), 1)"""
        shapes = ((M_IFC, "F1", "IfcFooting", 0, "POLYGON((100 200, 101 200, 101 201, 100 201, 100 200))"),
                  (M_IFC, "W1", "IfcWallStandardCase", 1, "POLYGON((99.8 199.8, 104.2 199.8, 104.2 200, 99.8 200, 99.8 199.8))"),
                  (M_IFC, "W2", "IfcWallStandardCase", 1, "POLYGON((99.8 203, 104.2 203, 104.2 203.2, 99.8 203.2, 99.8 203))"),
                  (M_IFC, "SP1", "IfcSpace", 1, "POLYGON((100 200, 104 200, 104 203, 100 203, 100 200))"),
                  (M_IFC, "SL1", "IfcSlab", 1, "POLYGON((99.8 199.8, 104.2 199.8, 104.2 203.2, 99.8 203.2, 99.8 199.8))"),
                  (M_IFC, "W3", "IfcWall", 2, "POLYGON((99.8 199.8, 106.2 199.8, 106.2 200, 99.8 200, 99.8 199.8))"),
                  (M_ONE, "W1", "IfcWall", 0, "POLYGON((0 0, 5 0, 5 0.2, 0 0.2, 0 0))"))
        for n, (fid, guid, cls, st, wkt) in enumerate(shapes, 10):
            db.run(el, f=fid, g=guid, n=n, c=cls, name=guid, st=names[st] if fid == M_IFC else "Ground", sg=f"S{st}", a="{}")
            db.run(shape, f=fid, g=guid, w=wkt)
        ent = """insert into jstore.entity (project_id, drawing_key, storey, kind, ifc_class, number, label, label_norm, host, geom, attrs)
                 values ('test/model', :k, :st, :kind, :c, :n, :l, :ln, :h, ST_GeomFromText(:g), '{"indoor": true}') returning entity_id"""
        src = """insert into jstore.entity_source (entity_id, file_id, page_no, ref_table, ref_key, role, bbox)
                 values (:e, :f, :n, 'ifc_element', :k, 'object', cast(:b as double precision[]))"""

        def add(st, kind, g, n=None, l=None, ln=None, h=None):
            e = db.one(ent, k=MODEL, st=names[st], kind=kind, c="IfcSpace" if kind == "room" else "IfcDoor", n=n, l=l, ln=ln, h=h, g=g)["entity_id"]
            db.run(src, e=e, f=M_IFC, n=st, k=f"E{e}", b=arr([100, 200, 101, 201]))
            return e
        r1 = add(1, "room", "POLYGON((100 200, 104 200, 104 203, 100 203, 100 200))", n="101", l="Office", ln="office")
        d1 = add(1, "door", "POLYGON((101 199.8, 101.9 199.8, 101.9 200, 101 200, 101 199.8))", h="101")
        w1 = add(1, "window", "POLYGON((102 203, 103.5 203, 103.5 203.2, 102 203.2, 102 203))", h="101")
        r2 = add(2, "room", "POLYGON((100 200, 106 200, 106 203, 100 203, 100 200))", n="201", l="Hall", ln="hall")
        d2 = add(2, "door", "POLYGON((101 199.8, 101.9 199.8, 101.9 200, 101 200, 101 199.8))", h="201")
        d3 = add(2, "door", "POLYGON((104 199.8, 104.9 199.8, 104.9 200, 104 200, 104 199.8))", h="201")
    yield dict(rooms=[r1, r2], doors=[d1, d2, d3], window=w1)
    with DB() as db:
        db.run("delete from jstore.issue where project_id = 'test/model'")
        db.run("delete from jstore.project where project_id = 'test/model'")


def test_storey_ids_and_names() -> None:
    assert am.split_id("hitos_arc~1-etg") == ("hitos_arc", "1-etg") and am.split_id("hitos_arc") == ("hitos_arc", None)
    key = "a model/with spaces"
    assert am.split_id(am.drawing_id(key) + "~level-2") == (key, "level-2")
    assert am.split_id("x~") == (None, None) and am.split_id("x~UP") == (None, None) and am.split_id("x~a~b") == (None, None)
    assert am.storey_norms("4-korrus") & am.storey_norms("4.korrus") and am.storey_norms("klder") & am.storey_norms("Kælder")
    assert not am.storey_norms("1. Sal") & am.storey_norms("2. Sal") and not am.storey_norms(None)
    assert am.storey_slug("1. Obergeschoss") == "1-obergeschoss" and am.storey_slug("OK OG2") == "ok-og2" and am.storey_slug("—") == ""


async def test_a_model_is_listed_one_entry_per_storey(make_client, seeded, framed, model) -> None:
    client = make_client()
    body = keep("list_storeys", (await client.get(f"{BASE}/drawings")).json())
    assert all(is_summary(d) for d in body["drawings"])
    by_id = {d["id"]: d for d in body["drawings"]}
    ids = [d["id"] for d in body["drawings"] if d["id"].startswith(MODEL)]
    assert ids == [f"{MODEL}~1-floor", f"{MODEL}~2-floor", f"{MODEL}~foundation"]      # by elevation; outlines only last; no roof
    assert by_id[ids[0]]["title"] == "aura model · 1. Floor" and by_id[ids[0]]["kind"] == "ifc"
    assert by_id[ids[0]]["subtitle"].startswith("1. Floor · level +2.00 m · 1 room · 2 objects")
    assert "1 room" in by_id[ids[0]]["subtitle"] and "2 objects" in by_id[ids[0]]["subtitle"] and "level +2.00 m" in by_id[ids[0]]["subtitle"]
    assert "outlines only" in by_id[ids[2]]["subtitle"] and by_id[ids[1]]["thumbnail"] == f"/drawings/{ids[1]}/thumb.png"
    assert by_id[SINGLE]["title"] == "aura single · Ground" and f"{SINGLE}~ground" not in by_id       # one storey: the drawing's id
    assert {"test_a", "test_b", framed["id"]} <= set(by_id)                                         # other ids did not change
    for d in ids + [SINGLE]:
        for name in ("thumb.png", "plan.svg"):
            assert (await client.get(f"{BASE}/drawings/{d}/{name}")).status_code == 200, (d, name)


async def test_a_storey_is_drawn_from_the_model(make_client, model) -> None:
    client = make_client()
    sid = f"{MODEL}~1-floor"
    r = await client.get(f"{BASE}/drawings/{sid}")
    assert r.status_code == 200, r.text
    d = keep("drawing_storey", r.json())
    assert is_drawing(d) and d["id"] == sid and d["title"] == "aura model · 1. Floor" and d["kind"] == "ifc"
    assert d["images"] == {"rebuilt": f"/drawings/{sid}/plan.svg"} and d["thumbnail"] == f"/drawings/{sid}/thumb.png"
    assert [x["num"] for x in d["rooms"]] == ["101"] and d["rooms"][0]["area_m2"] is None
    pts = d["rooms"][0]["points"]
    assert len(pts) == 4 and all(0 < v < 100 for p in pts for v in p)
    xs, ys = sorted({p[0] for p in pts}), sorted({p[1] for p in pts})
    assert xs[0] < d["rooms"][0]["label_at"][0] < xs[-1] and ys[0] < d["rooms"][0]["label_at"][1] < ys[-1]
    assert (xs[-1] - xs[0]) * d["m_per_pct_x"] == pytest.approx(4, abs=0.02) and (ys[-1] - ys[0]) * d["m_per_pct_y"] == pytest.approx(3, abs=0.02)
    mj = (await client.get(f"{BASE}/drawings/{sid}/model.json")).json()
    assert mj["storey"] == "1. Floor" and [s["id"] for s in mj["storeys"]] == [sid, f"{MODEL}~2-floor", f"{MODEL}~foundation"]
    assert mj["storeys"][1]["plan_file"] is True and mj["storeys"][0]["shown"] is True and mj["unplaced"] == []
    items = {i["id"]: i for i in mj["items"]}
    assert sorted(items) == ["door-01", "win-01"] and {i["entity_id"] for i in mj["items"]} == {model["doors"][0], model["window"]}
    assert all(0 <= b["x"] and b["x"] + b["w"] <= 100 and 0 <= b["y"] and b["y"] + b["h"] <= 100 for b in (i["box"] for i in mj["items"]))
    assert items["door-01"]["box"]["y"] > ys[-1] - 0.01 and items["win-01"]["box"]["y"] < ys[0] + 0.01      # north (y up) is up
    svg = (await client.get(f"{BASE}/drawings/{sid}/plan.svg")).text
    assert ">101<" in svg and ">Office<" in svg and 'fill="#cbd5e1"' in svg and svg.count("<path") >= 4   # slab, walls, door, window
    for name in ("thumb.png", "original.png"):
        png = await client.get(f"{BASE}/drawings/{sid}/{name}")
        assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n" and png.headers["content-type"] == "image/png"
    # the drawing's own id opens the lowest storey with rooms, under that id
    bare = (await client.get(f"{BASE}/drawings/{MODEL}")).json()
    assert is_drawing(bare) and bare["id"] == MODEL and bare["rooms"] == d["rooms"] and bare["images"]["rebuilt"] == f"/drawings/{MODEL}/plan.svg"
    low = (await client.get(f"{BASE}/drawings/{MODEL}~foundation")).json()
    assert is_drawing(low) and low["rooms"] == [] and "<path" in (await client.get(f"{BASE}/drawings/{MODEL}~foundation/plan.svg")).text
    for bad in (f"{MODEL}~roof", f"{MODEL}~nope", "test_a~model", f"{SINGLE}~x"):
        r = await client.get(f"{BASE}/drawings/{bad}")
        assert r.status_code == 404 and is_error(r.json()), bad


async def test_a_storey_with_a_plan_file_is_shown_on_it(make_client, model) -> None:
    client = make_client()
    sid = f"{MODEL}~2-floor"
    d = (await client.get(f"{BASE}/drawings/{sid}")).json()
    assert is_drawing(d) and [x["num"] for x in d["rooms"]] == ["201"]
    assert d["images"] == {"rebuilt": f"/drawings/{sid}/original.png"} or d["images"].get("original") == f"/drawings/{sid}/original.png"
    assert sorted(d["rooms"][0]["points"]) == [[16.67, 37.5], [16.67, 75.0], [66.67, 37.5], [66.67, 75.0]]   # through the scan's frame
    mj = (await client.get(f"{BASE}/drawings/{sid}/model.json")).json()
    assert mj["page"]["file_id"] == M_PNG and sorted(i["id"] for i in mj["items"]) == ["door-01", "door-02"]
    assert {i["entity_id"] for i in mj["items"]} == set(model["doors"][1:])


async def test_questions_on_a_storey(make_client, model) -> None:
    sid = f"{MODEL}~1-floor"
    client = make_client(script(call("sql", query=DOORS), final(answer=3, unit="count", sql=DOORS, entity_ids=model["doors"])))
    r = await client.post("/aura/ask", json=dict(drawing_id=sid, question="How many doors are in the building?"))
    assert r.status_code == 200, r.text
    body = check_response(keep("ask_storey", r.json()))
    assert body["answer"] == {"kind": "count", "value": 3, "subject": "door"}                # the count is the agent's
    assert [b["id"] for b in body["evidence"]["boxes"]] == ["door-01"]                       # only this storey's door is drawn
    assert "2 of the things behind this answer are on other storeys (2.Floor: 2)" in body["explanation"]
    with DB() as db:
        models = am.Models()
        m1, m2 = models.get(db, MODEL, "1-floor"), models.get(db, MODEL, "2-floor")
        assert models.get(db, MODEL, "1-floor") is m1 and models.get(db, MODEL).rooms is m1.rooms       # cached; the bare id is the same view
    text = aura.compose(m1, "How many doors are on this floor?", aura.clean_scope(None), None, [])
    assert "storey '1. Floor' of the model, elevation 2 m" in text and "column storey" in text
    assert set(m1.by_entity) == {model["rooms"][0], model["doors"][0], model["window"]} and set(m1.elsewhere) == {model["rooms"][1], *model["doors"][1:]}
    # a reference to something on another storey's page of the model is not drawn here; on this storey's page it is
    assert m1.on_page(M_IFC, 2, [100, 200, 101, 201]) is None and m1.on_page(M_PNG, 0, [100, 150, 150, 200]) is None
    assert m1.on_page(M_IFC, 1, [100, 200, 101, 201]) is not None
    assert m2.on_page(M_PNG, 0, [100, 150, 150, 200]) == {"x": 16.67, "y": 37.5, "w": 8.33, "h": 12.5}
    assert m2.on_page(M_IFC, 2, [100, 200, 101, 201]) is not None and m2.on_page(M_IFC, 1, [100, 200, 101, 201]) is None
    region = aura.clean_scope({"kind": "region", "rect": {"x": 0, "y": 0, "w": 100, "h": 100}, "label": "All"})
    env = re.search(r"ST_MakeEnvelope\(([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+)\)", aura.compose(m1, "x", region, None, []))
    assert env and float(env[1]) < 99.8 and float(env[3]) > 104.2 and float(env[2]) < 199.8 and float(env[4]) > 203.2   # the page in metres


def test_frontend_guards_accept_the_bodies() -> None:
    """The real guards of frontend/src/api/contract.ts over the bodies the tests above collected."""
    node = os.environ.get("JSTORE_TEST_NODE") or shutil.which("node")
    if not node or not CONTRACT_TS.is_file():
        pytest.skip("no Node, or no frontend checkout next to the store")
    if len(BODIES) < 10:
        pytest.skip("run the whole module: the bodies come from the other tests")
    code = """
      import { readFileSync } from 'node:fs'
      const c = await import(process.env.CONTRACT_TS)
      const b = JSON.parse(readFileSync(0, 'utf8'))
      const checks = {
        list: [c.isDrawingList], drawing_sources: [c.isDrawing], drawing_framed: [c.isDrawing], drawing_bare: [c.isDrawing],
        drawing_file: [c.isDrawingFile], list_storeys: [c.isDrawingList], drawing_storey: [c.isDrawing], ask_storey: [c.isAskResponse], ask_count: [c.isAskResponse], ask_area: [c.isAskResponse], ask_rooms: [c.isAskResponse],
        ask_text: [c.isAskResponse], ask_abstain: [c.isAskResponse], ask_refs: [c.isAskResponse], upload: [c.isUploadResult], rewrite: [c.isRewriteResponse],
        conversation: [(v) => v.turns.every((t) => c.isAskResponse(t.response))],
        chat_events: [(v) => v.every(([name, data]) => name !== 'answer' || c.isAskResponse(data.response)),
                      (v) => v.every(([name, data]) => name !== 'tool_result' || (data.evidence.boxes.every(c.isBox))),
                      (v) => v.every(([name]) => c.CHAT_EVENTS.includes(name))],
      }
      const bad = []
      for (const [name, fns] of Object.entries(checks)) {
        if (!(name in b)) { bad.push(name + ': missing'); continue }
        const body = name.startsWith('ask_') ? c.salvageAskResponse(b[name]) : b[name]
        if (name.startsWith('ask_') && body.dropped_items) bad.push(name + ': items dropped')
        fns.forEach((fn, i) => { if (!fn(body)) bad.push(name + ': guard ' + i) })
      }
      for (const d of b.list.drawings) if (c.safeUrl(d.thumbnail) === null && d.thumbnail !== '') bad.push('thumbnail ' + d.id)
      console.log(JSON.stringify(bad))
    """
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", code],
                         input=json.dumps(BODIES, default=str), capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, CONTRACT_TS=str(CONTRACT_TS)))
    if out.returncode != 0 and "strip-types" in out.stderr:
        pytest.skip("this Node cannot load TypeScript")
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
