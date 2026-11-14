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
