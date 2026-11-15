"""Budget hard limits (plan: "Gemini request budget", P1 acceptance)."""

from __future__ import annotations

from datetime import UTC

import pytest

from aura.budget import Budget, BudgetError
from aura.config import Settings


def make(clock=None, **kw) -> Budget:
    s = Settings(**kw)
    return Budget(s, clock=clock) if clock else Budget(s)


def test_seed_event_total_starts_at_94():
    # 41 requests before the build + 53 for the held-out CubiCasa eval.
    b = make()
    assert b.event_used() == 94
    assert b.snapshot()["event"] == {"used": 94, "cap": 1000, "seed": 94}


def test_defaults_are_bounded():
    """A deployment that sets nothing is capped (fail closed): the key and the disk are bounded."""
    s = Settings()
    assert not s.unlimited
    assert (s.event_cap, s.daily_cap, s.ip_agent_per_hour, s.agent_runs_per_ip_hour,
            s.agent_runs_per_day, s.uploads_per_ip_hour, s.max_queued_uploads) == (1000, 350, 40, 3, 15, 10, 20)
    assert s.upload_quota_bytes == 2 * 1024**3 and s.raster_public_cap == 0  # raster: presenter only
    b = Budget(s)
    for _ in range(3):
        b.commit(b.reserve("agent_run", ip_hash="ip1"), model="gemini-3.8-flash")
    with pytest.raises(BudgetError) as e:
        b.reserve("agent_run", ip_hash="ip1")
    assert e.value.code == "rate_limited"
    with pytest.raises(BudgetError) as e:
        b.reserve("raster_detect", ip_hash="ip2")
    assert e.value.code == "presenter_required"


def test_aura_unlimited_lifts_every_cap_but_spend_is_still_counted():
    s = Settings(AURA_UNLIMITED=True)
    assert (s.event_cap, s.daily_cap, s.ip_agent_per_hour, s.agent_runs_per_ip_hour,
            s.agent_runs_per_day, s.uploads_per_ip_hour) == (0, 0, 0, 0, 0, 0)
    b = Budget(s)
    for _ in range(60):  # far past every old default (3/h, 15/day, 40/h)
        b.commit(b.reserve("agent_run", ip_hash="ip1"), model="gemini-3.8-flash")
        b.commit(b.reserve("agent", ip_hash="ip1"), model="gemini-3.8-flash")
    b.reserve("raster_detect", ip_hash="ip1")
    assert b.event_used() == 94 + 120 + 1
    assert b.can_spend()


def test_seed_is_configurable_and_not_persisted_twice():
    b = make(AURA_BUDGET_SEED=111)
    assert b.event_used() == 111
    # A restart loads the persisted request count; the seed is added once, from config.
    b2 = Budget(Settings(AURA_BUDGET_SEED=111), persisted_total=5)
    assert b2.event_used() == 116


def test_event_total_never_exceeds_cap_even_with_presenter():
    b = Budget(Settings(AURA_EVENT_CAP=1000, AURA_DAILY_CAP=10_000), persisted_total=1000 - 94 - 2)
    r1 = b.reserve("check", presenter=True)
    r2 = b.reserve("agent", presenter=True)
    assert b.event_used() == 1000
    with pytest.raises(BudgetError) as e:
        b.reserve("check", presenter=True)
    assert e.value.code == "budget_exhausted"
    b.commit(r1, model="gemini-decision-1", input_tokens=10)
    b.release(r2)
    assert b.event_used() == 999
    b.reserve("check", presenter=True)
    with pytest.raises(BudgetError):
        b.reserve("check", presenter=True, bucket="dev")


def test_concurrent_reservations_do_not_overshoot():
    import threading

    b = Budget(Settings(AURA_EVENT_CAP=60, AURA_BUDGET_SEED=10, AURA_DAILY_CAP=10_000))
    ok, refused = [], []

    def worker():
        try:
            ok.append(b.reserve("check", presenter=True))
        except BudgetError:
            refused.append(1)

    ts = [threading.Thread(target=worker) for _ in range(80)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 50 and len(refused) == 30 and b.event_used() == 60


def test_daily_cap_holds_and_resets_next_eet_day():
    now = [1_763_100_000.0]  # 2025-11-14 ~06:00 UTC
    b = Budget(Settings(AURA_DAILY_CAP=3), clock=lambda: now[0])
    for _ in range(3):
        b.commit(b.reserve("check"), model="gemini-decision-1")
    with pytest.raises(BudgetError) as e:
        b.reserve("check", presenter=True)
    assert e.value.code == "budget_exhausted"
    # dev/measurement buckets are not part of the public daily cap
    b.commit(b.reserve("check", bucket="dev"), model="gemini-decision-1")
    now[0] += 24 * 3600
    b.reserve("check")


def test_daily_cap_uses_helsinki_day():
    # 22:30 UTC on 14 Nov is 00:30 on 15 Nov in Helsinki (EET, UTC+2).
    from datetime import datetime

    t = datetime(2026, 11, 14, 22, 30, tzinfo=UTC).timestamp()
    b = Budget(Settings(), clock=lambda: t)
    assert b.day() == "2026-11-15"


def test_per_ip_limit_applies_to_agent_bucket_only():
    assert Settings().ip_agent_per_hour == 40  # the venue shares one IP
    b = Budget(Settings(AURA_IP_AGENT_PER_HOUR=15))
    for _ in range(15):
        b.commit(b.reserve("agent", ip_hash="ip1"), model="gemini-3.1-flash-lite")
    with pytest.raises(BudgetError) as e:
        b.reserve("choice", ip_hash="ip1")
    assert e.value.code == "rate_limited"
    # another IP is unaffected
    b.reserve("agent", ip_hash="ip2")
    # checks are not limited per IP
    for _ in range(20):
        b.commit(b.reserve("check", ip_hash="ip1"), model="gemini-decision-1")


def test_per_ip_window_rolls_after_an_hour():
    now = [1000.0]
    b = Budget(Settings(AURA_IP_AGENT_PER_HOUR=2), clock=lambda: now[0])
    b.reserve("agent", ip_hash="x")
    b.reserve("agent", ip_hash="x")
    with pytest.raises(BudgetError):
        b.reserve("agent", ip_hash="x")
    now[0] += 3601
    b.reserve("agent", ip_hash="x")


def test_presenter_bypasses_only_per_ip_limits():
    b = Budget(Settings(AURA_IP_AGENT_PER_HOUR=1, AURA_DAILY_CAP=3))
    b.commit(b.reserve("agent", ip_hash="x"), model="m")
    with pytest.raises(BudgetError):
        b.reserve("agent", ip_hash="x")
    b.commit(b.reserve("agent", ip_hash="x", presenter=True), model="m")
    b.commit(b.reserve("agent", ip_hash="x", presenter=True), model="m")
    with pytest.raises(BudgetError) as e:  # daily cap still holds for the presenter
        b.reserve("agent", ip_hash="x", presenter=True)
    assert e.value.code == "budget_exhausted"


def test_raster_needs_presenter_unless_public_cap():
    b = Budget(Settings())
    with pytest.raises(BudgetError) as e:
        b.reserve("raster_detect", ip_hash="x")
    assert e.value.code == "presenter_required"
    b.reserve("raster_detect", ip_hash="x", presenter=True)
    b2 = Budget(Settings(AURA_RASTER_PUBLIC_CAP=1))
    b2.reserve("raster_detect", ip_hash="x")
    with pytest.raises(BudgetError):
        b2.reserve("raster_detect", ip_hash="y")


def test_kill_switch_read_on_every_reserve(monkeypatch):
    b = make()
    b.release(b.reserve("check"))
    monkeypatch.setenv("AURA_MODELS", "off")
    with pytest.raises(BudgetError) as e:
        b.reserve("check", presenter=True)
    assert e.value.code == "models_off"
    assert b.snapshot()["models"] == "off"
    monkeypatch.setenv("AURA_MODELS", "on")
    b.reserve("check")


def test_dev_bucket_cap():
    b = Budget(Settings(AURA_DEV_CAP=2))
    b.commit(b.reserve("agent", bucket="dev"), model="m")
    b.commit(b.reserve("agent", bucket="dev"), model="m")
    with pytest.raises(BudgetError):
        b.reserve("agent", bucket="dev")


def test_commit_records_cost_and_calls_sink():
    rows = []
    b = Budget(Settings(), sink=rows.append)
    rec = b.commit(b.reserve("check", ip_hash="h"), model="gemini-decision-1",
                   input_tokens=25854, latency_ms=1310)
    assert rows == [rec]
    assert rec.cost_usd == pytest.approx(0.0025854)
    assert rec.as_dict()["tokens"] == 25854
    assert b.event_used() == 95


def test_per_ip_and_raster_limits_survive_a_restart(tmp_path):
    """F14: the per-IP hour and the public raster cap are seeded from the requests table."""
    import time

    from aura.budget import AGENT_PURPOSES, RASTER_PURPOSES
    from aura.store import Store, init_db

    db = tmp_path / "a.sqlite3"
    init_db(db)
    import sqlite3

    con = sqlite3.connect(db)
    now = time.time()
    for i in range(3):
        con.execute("INSERT INTO requests(t, day, model, purpose, bucket, ip_hash) VALUES "
                    "(?,?,?,?,?,?)", (now - 60 * i, "2026-11-14", "m", "agent", "public", "ipA"))
    con.execute("INSERT INTO requests(t, day, model, purpose, bucket, ip_hash) VALUES "
                "(?,?,?,?,?,?)", (now - 7200, "2026-11-14", "m", "agent", "public", "ipA"))
    con.execute("INSERT INTO requests(t, day, model, purpose, bucket, ip_hash) VALUES "
                "(?,?,?,?,?,?)", (now, "2026-11-14", "m", "raster_detect", "public", "ipB"))
    con.commit()
    con.close()
    recent, raster = Store(db).recent_limits(now, AGENT_PURPOSES, RASTER_PURPOSES)
    assert len(recent["ipA"]) == 3 and raster == 1  # the 2 h old request is outside the hour
    s = Settings(AURA_IP_AGENT_PER_HOUR=3, AURA_RASTER_PUBLIC_CAP=1)
    b = Budget(s, recent_ip_agent=recent, raster_public_used=raster)
    with pytest.raises(BudgetError):
        b.reserve("agent", "public", ip_hash="ipA")
    with pytest.raises(BudgetError):
        b.reserve("raster_detect", "public", ip_hash="ipC")
    b.reserve("agent", "public", ip_hash="ipD")


def test_ledger_counts_a_request_before_it_is_sent_and_survives_a_crash(tmp_path):
    """H3: the row is committed at reserve time on its own connection; a process that dies
    before commit (or whose writer batch fails) still leaves the request counted."""
    import sqlite3

    from aura.budget import Ledger
    from aura.store import Store, init_db

    db = tmp_path / "a.sqlite3"
    init_db(db)
    b = Budget(Settings(), ledger=Ledger(db))
    res = b.reserve("agent", "public", ip_hash="ip1")
    # Visible to another connection immediately, before anything was sent:
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
    # "Crash": a new Budget over the same database counts it although it never settled.
    total, by_bucket, _ = Store(db).request_counts()
    assert total == 1 and by_bucket == {"public": 1}
    b.commit(res, model="gemini-3.1-flash-lite", input_tokens=100, output_tokens=10)
    row = sqlite3.connect(db).execute("SELECT model, input_tokens, status FROM requests").fetchone()
    assert row == ("gemini-3.1-flash-lite", 100, 200)
    never_sent = b.reserve("agent", "public", ip_hash="ip1")
    b.release(never_sent)
    assert Store(db).request_counts()[0] == 1
    # Fail closed: if the counter cannot be written, nothing is sent.
    b.ledger.close()
    with pytest.raises(BudgetError):
        b.reserve("agent", "public", ip_hash="ip1")
    assert b.event_used() == b.seed + 1  # the refused reservation is not left pending


def test_kill_switch_file_turns_models_off_live(tmp_path, monkeypatch):
    from aura.config import models_enabled

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert models_enabled()
    (tmp_path / "MODELS_OFF").touch()
    assert not models_enabled()


def test_a_failing_volume_flush_never_refuses_or_orphans(tmp_path):
    from aura.budget import Ledger
    from aura.store import Store, init_db

    db = tmp_path / "a.sqlite3"
    init_db(db)

    def boom():
        raise OSError("volume")

    b = Budget(Settings(AURA_BUDGET_SEED=990), ledger=Ledger(db, flush=boom))  # near the cap
    res = b.reserve("agent", "public", ip_hash="ip1")
    b.commit(res, model="gemini-3.1-flash-lite", input_tokens=1)
    assert Store(db).request_counts()[0] == 1


def test_ip_buckets_map_ipv4_mapped_and_group_real_ipv6():
    from aura.netutil import ip_bucket

    assert ip_bucket("::ffff:1.2.3.4") == ip_bucket("1.2.3.4") == "1.2.3.4"
    assert ip_bucket("::ffff:1.2.3.4") != ip_bucket("::ffff:5.6.7.8")
    assert ip_bucket("2001:db8:1:2::1") == ip_bucket("2001:db8:1:2:ffff::9")
    assert ip_bucket("2001:db8:1:2::1") != ip_bucket("2001:db8:1:3::1")
    assert ip_bucket("not an ip") == ip_bucket("also bad") == "invalid"
    assert ip_bucket(None) is None


def test_retention_answer_cache_expires_and_old_ip_hashes_are_cleared(tmp_path):
    """Privacy page: nothing a user typed outlives 24 h in memory; old request rows keep their
    count (event cap) but lose the IP hash; the audit file never has one."""
    import json as _json
    import sqlite3
    import time as _t

    from fastapi.testclient import TestClient

    from aura.cache import AnswerCache
    from aura.main import cleanup_once
    from tests.conftest import make_app

    now = [1000.0]
    c = AnswerCache(ttl_s=10, clock=lambda: now[0])
    c.put("k", {"a": 1})
    assert c.get("k") == {"a": 1}
    now[0] += 11
    assert c.get("k") is None

    with TestClient(make_app(tmp_path)) as cl:
        svc = cl.app.state.svc
        cl.portal.call(svc.writer.execute, "INSERT INTO requests(t, day, model, purpose, bucket, "
                       "ip_hash, status) VALUES (?,?,?,?,?,?,200)",
                       (_t.time() - 3 * 86400, "2026-11-11", "m", "agent", "public", "iphash"))
        cl.portal.call(cleanup_once, svc)
        cl.portal.call(svc.writer.flush)
    row = sqlite3.connect(tmp_path / "data" / "aura.sqlite3").execute(
        "SELECT ip_hash, model FROM requests").fetchone()
    assert row == (None, "m")
    for f in (tmp_path / "data").rglob("requests.jsonl"):
        assert all("ip_hash" not in _json.loads(x) for x in f.read_text().splitlines())


def test_admin_models_switch_needs_the_presenter_and_works_live(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aura.config import models_enabled
    from tests.conftest import make_app

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(make_app(tmp_path, AURA_PRESENTER_TOKEN="pres")) as c:
        assert c.post("/admin/models", json={"on": False}).status_code == 403
        r = c.post("/admin/models", json={"on": False}, headers={"x-aura-presenter": "pres"})
        assert r.json()["models"] is False and not models_enabled()
        r = c.post("/admin/models", json={"on": True}, headers={"x-aura-presenter": "pres"})
        assert r.json()["models"] is True and models_enabled()


def test_weighted_slots_and_record_external_and_agent_run_caps(tmp_path):
    import sqlite3
    import time as _t

    from aura.budget import Ledger, cost_usd
    from aura.store import Store, init_db

    # Gemini 4 sourced prices & default upper bound
    assert cost_usd("gemini-4-argon", 1_000_000, 100_000) == pytest.approx(3.0)
    assert cost_usd("gemini-4-carbon", 1_000_000, 100_000) == pytest.approx(3.0)
    assert cost_usd("gemini-3.8-flash", 1_000_000, 100_000) == pytest.approx(1.125)
    assert cost_usd("unknown-future-model", 1_000_000, 100_000) == pytest.approx(3.0)

    db = tmp_path / "ext.sqlite3"
    init_db(db)
    s = Settings(
        AURA_BUDGET_SEED=10,
        AURA_EVENT_CAP=100,
        AURA_DAILY_CAP=80,
        AURA_AGENT_RUNS_PER_IP_HOUR=2,
        AURA_AGENT_RUNS_PER_DAY=3,
    )
    b = Budget(s, ledger=Ledger(db))
    # Weighted 40-slot reservation atomically blocks concurrent overshoot
    r1 = b.reserve("agent_run", "public", ip_hash="ip1", slots=40)
    r2 = b.reserve("agent_run", "public", ip_hash="ip1", slots=40)
    assert b.event_used() == 90  # 10 seed + 40 + 40
    # 3rd 40-slot reservation would exceed daily_cap (80) and event_cap (100)
    with pytest.raises(BudgetError) as exc:
        b.reserve("agent_run", "public", ip_hash="ip2", slots=40)
    assert exc.value.code == "budget_exhausted"

    # Settle r1 with actual 16 requests -> frees 24 unused slots, writes 1 agent_run + 15 agent_step
    rec = b.record_external(
        r1, model="gemini-4-argon", n_requests=16, input_tokens=100_000, output_tokens=5_000
    )
    assert rec.cost_usd == pytest.approx(0.25)
    assert b.event_used() == 10 + 16 + 40
    b.release(r2)
    assert b.event_used() == 26

    con = sqlite3.connect(db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    assert con.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 16
    assert con.execute("SELECT COUNT(*) FROM requests WHERE purpose='agent_run'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM requests WHERE purpose='agent_step'").fetchone()[0] == 15
    assert con.execute("SELECT SUM(cost_usd) FROM requests").fetchone()[0] == pytest.approx(0.25)
    con.close()

    # Restart persistence: Store.recent_agent_runs counts 1 agent_run (not 16 steps!)
    store = Store(db)
    by_ip, today_runs = store.recent_agent_runs(_t.time(), b.day())
    assert len(by_ip["ip1"]) == 1 and today_runs == 1

    # Per-IP agent_run cap (2/hour) enforced, presenter exempt, daily agent_run cap (3/day) enforced
    r3 = b.reserve("agent_run", "public", ip_hash="ip1", slots=10)
    b.record_external(r3, model="gemini-4-argon", n_requests=5)
    with pytest.raises(BudgetError) as exc2:
        b.reserve("agent_run", "public", ip_hash="ip1", slots=10)
    assert exc2.value.code == "rate_limited"
    r4 = b.reserve("agent_run", "public", ip_hash="ip1", presenter=True, slots=10)
    b.record_external(r4, model="gemini-4-argon", n_requests=5)
    with pytest.raises(BudgetError) as exc3:
        b.reserve("agent_run", "public", ip_hash="ip2", presenter=True, slots=10)
    assert exc3.value.code == "budget_exhausted"


def test_sqlite_answer_cache_persists_across_restarts(tmp_path):
    import asyncio

    from aura.cache import AnswerCache, answer_key
    from aura.store import Store, init_db
    from aura.writer import Writer

    db = tmp_path / "cache.sqlite3"
    init_db(db)
    store = Store(db)

    async def go():
        writer = Writer(db)
        await writer.start()
        try:
            c1 = AnswerCache(store=store, writer=writer)
            k = answer_key("swiss-46639", "Show all north windows", None, None, None, True) + "|agent|m"
            c1.put(k, {"answer": {"kind": "count", "value": 4, "subject": "window"}},
                   drawing_id="swiss-46639", question="Show all north windows")
            await writer.flush()
        finally:
            await writer.stop()

        # Brand new AnswerCache over SQLite store recovers both get(k) and entries_for_drawing!
        c2 = AnswerCache(store=store)
        hit = c2.get(k)
        assert hit is not None and hit["answer"]["value"] == 4
        entries = c2.entries_for_drawing("swiss-46639", scope=None, overrides=None, models_on=True)
        assert entries == [(k, "Show all north windows")]

    asyncio.run(go())


def test_similar_question_candidates_skip_answers_stored_under_an_older_key_version(tmp_path):
    """An answer written before a KEY_VERSION bump (old wording, no evidence) must not come back as
    'the same question' through the similar-question reuse: only current-version keys are offered."""
    import json

    from aura.cache import KEY_VERSION, AnswerCache, answer_key

    c = AnswerCache()
    new = answer_key("d1", "circumference of the hall in m?", None, None, "en", True) + "|router:12|data:1"
    arr = json.loads(new.split("|", 1)[0])
    arr[6] = KEY_VERSION - 3
    old = json.dumps(arr, sort_keys=True, ensure_ascii=False) + "|router:12|data:1"
    unversioned = json.dumps(arr[:6], sort_keys=True, ensure_ascii=False) + "|router:12|data:1"
    for k in (old, unversioned, new):
        c.put(k, {"answer": {"kind": "quantity", "value": 63.57, "unit": "m"}}, drawing_id="d1",
              question="circumference of the hall in m?")
    assert [k for k, _ in c.entries_for_drawing("d1", scope=None, overrides=None, models_on=True)] == [new]
