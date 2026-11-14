"""HTTP API: endpoints, cost reporting, the check, the choice step, budget behaviour per IP."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aura.gemini.client import FakeTransport, UnknownFixture
from tests.conftest import FIXTURES, make_app, no_nulls

GEMINI = FIXTURES / "gemini"
SWISS = "swiss-46639"


def ask(c: TestClient, question: str, **kw):
    r = c.post("/ask", json={"drawing_id": SWISS, "question": question, **kw},
               headers=kw.pop("headers", None) or {})
    assert r.status_code == 200, r.text
    body = r.json()
    no_nulls(body)
    return body


@pytest.fixture
def live_client(tmp_path):
    """Gemini wired to FakeTransport (recorded/derived fixtures), dataset image available."""
    fake = FakeTransport(GEMINI)
    with TestClient(make_app(tmp_path, transport=fake, with_image=True,
                             AURA_PRESENTER_TOKEN="pres-token")) as c:
        c.fake = fake  # type: ignore[attr-defined]
        yield c


def test_geometry_answer_costs_zero_calls(client):
    b = ask(client, "How many windows are drawn on this floor plan?")
    assert b["answer"] == {"kind": "count", "value": 12, "subject": "window"}
    assert b["calls"] == 0 and b["cost_usd"] == 0 and b["route"] == "template"
    assert b["method"] == "cad_geometry" and b["answer_id"].startswith("a-")
    assert "check" not in b  # no model ran, so no check numbers
    assert any(n.get("tool", {}).get("name") == "count_objects" for n in b["trace"])


def test_check_is_counted_separately_and_cached(live_client):
    c = live_client
    before = c.get("/budget").json()["event_used"]
    b = ask(c, "How many washbasins are drawn on this floor plan?")
    assert b["answer"]["value"] == 5
    assert b["calls"] == 0 and b["cost_usd"] == 0  # the answer itself is geometry
    ch = b["check"]
    assert ch["status"] == "live" and ch["scope"] == "drawn_boxes" and ch["image"] == "dataset_png"
    assert ch["total"] == 5 and set(ch["per_box"]) == {f"basin-0{i}" for i in range(1, 6)}
    # recorded: basin-04 0.26 lowest; anchors max 0.0836 → all 5 above 0.1336
    assert ch["least_certain_id"] == "basin-04" == ch["ranking"][0]
    assert ch["anchors_max"] == pytest.approx(0.0836) and ch["agreed"] == 5
    assert ch["calls"] == 1 and ch["cached"] is False and ch["tokens"] == 4784
    assert ch["cost_usd"] == pytest.approx(0.0004784)
    node = next(n for n in b["trace"] if n["id"] == "check")
    assert "1 model call" in node["body"] and node["confidence"] == pytest.approx(0.2615, abs=1e-3)
    assert c.get("/budget").json()["event_used"] == before + 1
    assert len(c.fake.calls) == 1

    # Region scope: sliced from the cached full-layer check, 0 new calls.
    r = ask(c, "Combien de lavabos ici ?",
            scope={"kind": "region", "rect": {"x": 47.4, "y": 39.2, "w": 13.6, "h": 15.2},
                   "label": "Area 1"})
    assert r["answer"]["value"] == 3 and r["check"]["total"] == 3
    assert r["check"]["calls"] == 0 and r["check"]["cached"] is True and r["calls"] == 0
    assert "Cached check: 0 model calls" in next(
        n for n in r["trace"] if n["id"] == "check")["body"]
    assert c.get("/budget").json()["event_used"] == before + 1 and len(c.fake.calls) == 1

    # Same question again: answer cache.
    again = ask(c, "How many washbasins are drawn on this floor plan?")
    assert again["route"] == "cache" and again["calls"] == 0
    stats = c.get("/stats").json()
    assert stats["model_calls_today"] == 1 and stats["answers_today"] == 2
    assert stats["median_cost_per_checked_answer_usd"]["n"] == 1


def test_no_check_for_subjects_without_a_recording_raises_loudly(live_client):
    # Windows have no fixture: FakeTransport must refuse rather than go live.
    with pytest.raises(UnknownFixture):
        ask(live_client, "How many windows are drawn on this floor plan?")


def test_kill_switch_means_geometry_only(live_client, monkeypatch):
    monkeypatch.setenv("AURA_MODELS", "off")
    b = ask(live_client, "How many washbasins are drawn on this floor plan?")
    assert b["answer"]["value"] == 5 and "check" not in b
    assert live_client.get("/budget").json()["models"] == "off"
    u = ask(live_client, "tally the lavatory fittings for me")
    assert u["answer"]["reason"] == "unclear_question" and u["calls"] == 0
    assert live_client.fake.calls == []


def test_choice_step_answers_a_router_miss(live_client):
    b = ask(live_client, "tally the lavatory fittings for me")  # synthetic fixture
    assert b["route"] == "choice" and b["calls"] == 1 and b["cost_usd"] > 0
    assert b["answer"] == {"kind": "count", "value": 5, "subject": "washbasin"}
    intent = next(n for n in b["trace"] if n["id"] == "intent")
    assert intent["tag"].startswith("Decisions API") and intent["confidence"] == 0.9


def test_choice_step_text_list_and_low_subject(live_client):
    q13 = ask(live_client, "Which sanitary fixtures are available in this building?")
    assert q13["answer"]["reason"] == "not_supported_yet" and q13["route"] == "choice"
    q16 = ask(live_client, "how many places can people sleep in apartment A")
    assert q16["answer"]["reason"] == "unclear_question"  # subject 'other': no tool fits


def test_abstain_gate(live_client):
    b = ask(live_client, "who owns this building?")
    assert b["answer"]["reason"] == "not_on_drawing" and b["calls"] == 1


def test_fire_rating_searches_text_before_abstaining(client):
    b = ask(client, "What is the fire rating of the entrance door?")
    assert b["answer"]["kind"] == "abstain" and b["answer"]["reason"] == "not_on_drawing"
    assert b["answer"]["where_to_find"]
    look = next(n for n in b["trace"] if n["id"] == "look")
    assert "Searched the printed text" in look["body"]
    assert b["calls"] == 0


def test_500_deterministic_asks_from_one_ip_never_rate_limited(tmp_path):
    with TestClient(make_app(tmp_path, AURA_IP_AGENT_PER_HOUR=1, AURA_DAILY_CAP=1)) as c:
        for i in range(500):
            rect = {"x": float(i % 50), "y": 10.0, "w": 30.0, "h": 40.0 + i / 100}
            r = c.post("/ask", json={"drawing_id": SWISS, "question": "How many doors?",
                                     "scope": {"kind": "region", "rect": rect, "label": "Area"}})
            assert r.status_code == 200
            assert r.json()["answer"]["kind"] == "count"


def test_forged_x_forwarded_for_changes_nothing(tmp_path):
    fake = FakeTransport(GEMINI)
    app = make_app(tmp_path, transport=fake, with_image=True, AURA_IP_AGENT_PER_HOUR=1,
                   AURA_PRESENTER_TOKEN="pres-token")
    with TestClient(app) as c:
        a = ask(c, "tally the lavatory fittings for me", headers={"x-forwarded-for": "1.1.1.1"})
        assert a["calls"] == 1
        b = ask(c, "who owns this building?", headers={"x-forwarded-for": "2.2.2.2"})
        # Same request.client.host → per-IP agent limit (1/h) applies despite the header.
        assert b["calls"] == 0 and b["answer"]["reason"] == "unclear_question"
        assert "Too many model questions" in next(
            n for n in b["trace"] if n["id"] == "look")["body"]
        choice_calls = [b for _, b in fake.calls if "intent" in b.get("questions", {})]
        assert len(choice_calls) == 1
        # The presenter token skips the per-IP limit (only that).
        p = ask(c, "who owns this building?", headers={"x-aura-presenter": "pres-token"})
        assert p["answer"]["reason"] == "not_on_drawing" and p["calls"] == 1
        # A wrong token is just a normal caller.
        w = ask(c, "Which sanitary fixtures are available in this building?",
                headers={"x-aura-presenter": "nope"})
        assert w["calls"] == 0


def test_budget_and_stats_shapes(client):
    b = client.get("/budget").json()
    assert b == {"contract_version": "1.1", "models": "on", "event_used": 94, "event_cap": 1000,
                 "day_used": 0, "day_cap": 350}
    s = client.get("/stats").json()
    assert s == {"contract_version": "1.1", "answers_today": 0, "model_calls_today": 0}
    ask(client, "How many doors?")
    s = client.get("/stats").json()
    assert s["answers_today"] == 1 and s["geometry_only_share"] == {"value": 1.0, "n": 1}


def test_cors_allowlist(client):
    ok = client.options("/ask", headers={"origin": "https://aura-demo.pages.dev",
                                         "access-control-request-method": "POST",
                                         "access-control-request-headers": "content-type"})
    assert ok.headers.get("access-control-allow-origin") == "https://aura-demo.pages.dev"
    preview = client.options("/ask", headers={"origin": "https://abc123.aura-demo.pages.dev",
                                              "access-control-request-method": "DELETE"})
    assert preview.headers.get("access-control-allow-origin")
    bad = client.options("/ask", headers={"origin": "https://evil.example",
                                          "access-control-request-method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_unknown_drawing_file_and_ids(client):
    assert client.get("/drawings/../etc/status").status_code == 404
    assert client.get("/drawings/nope").json()["code"] == "unknown_drawing"
    assert client.get("/drawings/swiss-46639/secret.txt").status_code == 404


def test_answers_persist_to_sqlite(client):
    b = ask(client, "What is the net floor area of apartment A?")
    assert b["answer"]["value"] == 139.34
    svc = client.app.state.svc
    client.portal.call(svc.writer.flush)  # type: ignore[attr-defined]
    assert svc.store.answer(b["answer_id"])["answer"]["value"] == 139.34


@pytest.mark.dataset
def test_built_library_drawing_answers_with_evidence(tmp_path):
    """On L1's preprocess output (backend/build/drawings): the real layers, not the mock."""
    from aura.config import Settings
    from aura.main import create_app
    from tests.conftest import BACKEND

    build = BACKEND / "build" / "drawings"
    if not (build / SWISS / "model.json").is_file():
        pytest.skip("dataset: run scripts/preprocess.py first")
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=build)
    with TestClient(create_app(s, use_gemini=False)) as c:
        w = ask(c, "How many windows are drawn on this floor plan?")
        assert w["answer"]["value"] == 12 and len(w["evidence"]["boxes"]) == 12
        assert w["gold"]["match"] is True and w["calls"] == 0
        wc = ask(c, "How many toilets (WC) are drawn on this floor plan?")
        assert wc["answer"]["value"] == 5 and wc["gold"]["match"] is True
        fl = ask(c, "What is the total net floor area of all numbered indoor rooms?")
        assert fl["gold"]["value"] == 258.0 and fl["gold"]["match"] is True
        lib = c.get("/drawings").json()["drawings"]
        assert lib[0]["id"] == SWISS and len(lib) >= 13 and all("facts" in x for x in lib)
        assert c.get(f"/drawings/{SWISS}/plan.svg").status_code == 200


class _Boom:
    """A transport whose network fails after the request may have left."""

    def __init__(self):
        self.calls = 0

    async def post(self, path, body):
        import httpx

        self.calls += 1
        raise httpx.ConnectTimeout("simulated")


def test_gemini_network_failure_never_breaks_a_geometry_answer(tmp_path):
    boom = _Boom()
    with TestClient(make_app(tmp_path, transport=boom, with_image=True)) as c:
        b = ask(c, "How many washbasins are drawn on this floor plan?")
        assert b["answer"]["value"] == 5 and "check" not in b
        assert "skipped" in next(n for n in b["trace"] if n["id"] == "check")["tag"]
        u = ask(c, "tally the lavatory fittings for me")
        assert u["answer"]["reason"] == "unclear_question" and u["calls"] == 0
        assert boom.calls == 2
        assert c.get("/budget").json()["event_used"] == 96  # sent requests count
        # Not cached: the next ask tries again.
        ask(c, "How many washbasins are drawn on this floor plan?")
        assert boom.calls == 3


def test_cached_answer_does_not_replay_live_check_cost(live_client):
    ask(live_client, "How many washbasins are drawn on this floor plan?")
    again = ask(live_client, "How many washbasins are drawn on this floor plan?")
    node = next(n for n in again["trace"] if n["id"] == "check")
    assert again["route"] == "cache" and "0 model calls" in node["body"]
    assert again["check"]["calls"] == 0 and again["check"]["cached"] is True
    assert "1 model call" not in node["body"]


@pytest.mark.dataset
def test_every_gold_line_of_the_built_library_through_ask(tmp_path):
    """Real dataset question text, every language twin, every built drawing, with context."""
    import json

    from aura.config import Settings
    from aura.main import create_app
    from tests.conftest import BACKEND, REPO

    build = BACKEND / "build" / "drawings"
    if not (build / SWISS / "model.json").is_file():
        pytest.skip("dataset: run scripts/preprocess.py first")
    lines = (REPO / "data/junction-dataset/questions.jsonl").read_text().splitlines()
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=build)
    failures, n = [], 0
    with TestClient(create_app(s, use_gemini=False)) as c:
        for d in sorted(p for p in build.iterdir() if (p / "gold.json").is_file()):
            no_nulls(c.get(f"/drawings/{d.name}").json())
            fire = c.post("/ask", json={"drawing_id": d.name,
                                        "question": "What is the fire rating of the doors?"}).json()
            assert fire["answer"]["kind"] in ("abstain", "text"), fire
            assert fire["answer"].get("reason") != "unclear_question"
            for g in json.loads((d / "gold.json").read_text()):
                for key in [k for k in g if k.startswith("dataset_qid")]:
                    qid = g[key]
                    q = json.loads(lines[int(qid.split(":")[1]) - 1])["question"]
                    b = c.post(
                        "/ask", json={"drawing_id": d.name, "question": q}).json()
                    n += 1
                    got = b["answer"].get("value")
                    want = g.get("solver_value", g["value"])
                    ok = (got is not None and abs(float(got) - float(want)) < 1e-6
                          and b["calls"] == 0 and b.get("gold", {}).get("dataset_qid") == qid)
                    if not ok:
                        failures.append((d.name, qid, q[:70], got, want, b.get("gold")))
    assert n > 400 and not failures, f"{len(failures)}/{n}: {failures[:8]}"


def test_drawing_not_ready_is_409_processing(tmp_path):
    import json as _json

    from aura import tools
    from tests.conftest import FRONTEND_DRAWING

    data = _json.loads(FRONTEND_DRAWING.read_text()) | {"id": "up-1", "status": "processing"}
    d = tools.LoadedDrawing(id="up-1", dir=None, drawing=data,
                            engine=tools.BundleEngine(data), source="upload")
    app = make_app(tmp_path)
    with TestClient(app) as c:
        c.app.state.svc.registry.add(d)
        r = c.get("/drawings/up-1")
        assert r.status_code == 409 and r.json()["code"] == "processing"
        assert c.get("/drawings/up-1/status").json()["status"] == "processing"


def test_fire_rating_found_as_printed_text_with_its_box(tmp_path):
    import json as _json

    from aura import tools
    from tests.conftest import FRONTEND_DRAWING

    data = _json.loads(FRONTEND_DRAWING.read_text())
    data["texts"] = [{"id": "text-01", "text": "EI30", "x": 40.0, "y": 50.0, "w": 2.0, "h": 1.0},
                     {"id": "text-02", "text": "Cuisine", "x": 10.0, "y": 10.0, "w": 4.0, "h": 1.0}]
    data["id"] = "fire-demo"
    d = tools.LoadedDrawing(id="fire-demo", dir=None, drawing=data, engine=tools.BundleEngine(data))
    with TestClient(make_app(tmp_path)) as c:
        c.app.state.svc.registry.add(d)
        r = c.post("/ask", json={"drawing_id": "fire-demo",
                                 "question": "What is the fire rating of the entrance door?"})
        b = r.json()
        no_nulls(b)
        assert b["answer"] == {"kind": "text", "value": "EI30", "subject": "fire_rating"}
        assert b["evidence"]["boxes"] == [{"id": "text-01", "x": 40.0, "y": 50.0, "w": 2.0,
                                           "h": 1.0, "subject": "fire_rating", "note": "EI30"}]
        assert b["calls"] == 0 and "gold" not in b
        ids = {n["id"]: n for n in b["trace"]}
        assert ids["answer"]["evidence_ids"] == ["text-01"]


class _Slow:
    async def post(self, path, body):
        import asyncio

        await asyncio.sleep(5)
        raise AssertionError("unreachable")


def test_timeout_is_504_timeout(tmp_path):
    app = make_app(tmp_path, transport=_Slow(), AURA_TURN_DEADLINE_S=0.3)
    with TestClient(app) as c:
        r = c.post("/ask", json={"drawing_id": SWISS, "question": "tally the lavatory fittings"})
        assert r.status_code == 504 and r.json()["code"] == "timeout"
        assert c.get("/budget").json()["event_used"] == 95  # the cancelled request counts


@pytest.mark.parametrize("raw", ["http://a.test, http://b.test", '["http://a.test","http://b.test"]'])
def test_allowed_origins_env_forms(monkeypatch, tmp_path, raw):
    from aura.config import Settings

    monkeypatch.setenv("ALLOWED_ORIGINS", raw)
    assert Settings().allowed_origins == ["http://a.test", "http://b.test"]
    with TestClient(make_app(tmp_path)) as c:  # startup does not crash
        r = c.options("/ask", headers={"origin": "http://b.test",
                                       "access-control-request-method": "POST"})
        assert r.headers.get("access-control-allow-origin") == "http://b.test"


def test_precomputed_checks_json_is_used_at_zero_calls(tmp_path):
    import json as _json
    import shutil

    from aura import tools
    from aura.gemini.check import check_key
    from tests.conftest import FRONTEND_DRAWING, SWISS_IMAGE

    data = _json.loads(FRONTEND_DRAWING.read_text())
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    shutil.copy(SWISS_IMAGE, bundle / "original.png")
    ids = [b["id"] for b in data["evidence"]["washbasin"]]
    rec = _json.loads((GEMINI / "check_washbasin.json").read_text())["response"]["answers"]
    full = {"subject": "washbasin", "key": check_key(SWISS, "washbasin", ids),
            "per_box": {i: rec[f"b0{n + 1}"]["noul"] for n, i in enumerate(ids)},
            "anchors": [0.08], "anchors_max": 0.08, "input_tokens": 4784, "cost_usd": 0.0004784}
    (bundle / "checks.json").write_text(_json.dumps({"washbasin": full}))
    d = tools.LoadedDrawing(id=SWISS, dir=bundle, drawing=data, engine=tools.BundleEngine(data))
    fake = FakeTransport(tmp_path / "no-fixtures")
    from aura.config import Settings
    from aura.main import create_app

    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild")
    with TestClient(create_app(s, transport=fake, extra_drawings=[d])) as c:
        b = ask(c, "How many washbasins are drawn on this floor plan?")
        assert b["check"]["cached"] is True and b["check"]["calls"] == 0
        assert b["check"]["total"] == 5 and fake.calls == []
