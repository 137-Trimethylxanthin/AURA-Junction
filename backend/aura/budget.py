"""Gemini request budget: the hard limits from the plan, enforced before any request is sent.

Every model request goes through `Budget.reserve(purpose, bucket, ...)`, then exactly one of
`commit(...)` (the request was sent; it counts even if it failed) or `release(...)` (it was never
sent). Reservation is atomic under a lock, so concurrent requests near a cap cannot overshoot.

Counters:
- event total: `seed + persisted requests + pending`. Nothing exceeds `event_cap`, presenter
  token included. The seed is configuration, never written to the DB, so restarts never add it twice.
- daily cap: public-bucket requests per Europe/Helsinki day.
- dev / measurement buckets: their own caps.
- per IP (public bucket only, skipped with the presenter token): agent and router-miss requests
  per rolling hour; raster requests need the presenter token unless `raster_public_cap` > 0.
  Checks are not limited per IP: they are cached per (drawing, subject) and bounded by the daily cap.
- kill switch: `AURA_MODELS=off`, read on every reserve.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from aura.config import Settings, models_enabled

Bucket = Literal["public", "dev", "measurement"]
# What a request is for. The per-IP kind is derived from this.
Purpose = str

AGENT_PURPOSES = {"choice", "abstain_gate", "agent", "rewrite"}
RASTER_PURPOSES = {"raster_detect", "raster_score"}

# USD per 1M tokens: (input, output incl. thinking). Measured/verified in gemini-apis.md.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-decision-1": (0.10, 0.0),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-4-hydrogen": (1.50, 7.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    pin, pout = PRICES.get(model, (1.50, 7.50))  # unknown model: assume the dearest
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 8)


class BudgetError(Exception):
    """Refused before sending. `code` is an ErrorCode of contract 1.1."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # budget_exhausted | rate_limited | presenter_required | models_off


@dataclass
class Reservation:
    id: int
    purpose: str
    bucket: Bucket
    ip_hash: str | None
    presenter: bool
    day: str
    t: float = field(default_factory=time.time)
    done: bool = False
    row_id: int | None = None  # the ledger row written before the request is sent


class Ledger:
    """H3: the request count is durable BEFORE a request leaves.

    Each reservation is INSERTed and committed synchronously on its own connection (WAL,
    synchronous=FULL), so a crash, a failed writer batch or a lost buffer can never lose a
    sent request: a row whose request never settled still counts (status -1). Commit fills in
    tokens/cost; release (never sent) deletes the row. `near_cap` triggers `flush` (the Modal
    Volume commit) so the count also survives a container that dies near the cap.
    """

    def __init__(self, db_path, flush: Callable[[], None] | None = None):
        import sqlite3

        self._con = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None,
                                    timeout=10)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._flush = flush

    def reserve(self, res: Reservation, near_cap: bool) -> int:
        cur = self._con.execute(
            "INSERT INTO requests(t, day, model, purpose, bucket, ip_hash, status) "
            "VALUES (?,?,?,?,?,?,-1)", (res.t, res.day, "", res.purpose, res.bucket, res.ip_hash))
        if near_cap and self._flush is not None:
            self._flush()
        return int(cur.lastrowid)

    def settle(self, row_id: int, rec: RequestRecord) -> None:
        self._con.execute(
            "UPDATE requests SET t=?, model=?, input_tokens=?, output_tokens=?, cost_usd=?, "
            "latency_ms=?, status=? WHERE id=?",
            (rec.t, rec.model, rec.input_tokens, rec.output_tokens, rec.cost_usd, rec.latency_ms,
             rec.status, row_id))

    def release(self, row_id: int) -> None:
        self._con.execute("DELETE FROM requests WHERE id=?", (row_id,))

    def close(self) -> None:
        self._con.close()


NEAR_CAP = 50  # flush the volume on every request once this close to the event cap


@dataclass
class RequestRecord:
    t: float
    day: str
    model: str
    purpose: str
    bucket: str
    ip_hash: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status: int

    def as_dict(self) -> dict:
        return {
            "t": self.t,
            "day": self.day,
            "model": self.model,
            "purpose": self.purpose,
            "bucket": self.bucket,
            "ip_hash": self.ip_hash,
            "tokens": self.input_tokens + self.output_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "status": self.status,
        }


def ip_hash(ip: str | None, salt: str) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


class Budget:
    def __init__(
        self,
        settings: Settings,
        *,
        persisted_total: int = 0,
        persisted_by_bucket: dict[str, int] | None = None,
        persisted_by_day: dict[str, int] | None = None,
        sink: Callable[[RequestRecord], None] | None = None,
        clock: Callable[[], float] = time.time,
        recent_ip_agent: dict[str, list[float]] | None = None,
        raster_public_used: int = 0,
        ledger: Ledger | None = None,
    ):
        self.s = settings
        self._lock = threading.Lock()
        self._clock = clock
        self._sink = sink
        self._next_id = 1
        self._used = persisted_total  # committed requests (excluding the seed)
        self._by_bucket: dict[str, int] = defaultdict(int, persisted_by_bucket or {})
        self._by_day: dict[str, int] = defaultdict(int, persisted_by_day or {})
        self._pending: dict[int, Reservation] = {}
        self._ip_agent: dict[str, deque[float]] = defaultdict(deque)
        for ip, ts in (recent_ip_agent or {}).items():  # persisted: limits survive a restart
            self._ip_agent[ip].extend(sorted(ts))
        self._raster_public = raster_public_used  # a lifetime cap over the database
        self.ledger = ledger
        self._tz = ZoneInfo(settings.budget_tz)

    # ---- views ---------------------------------------------------------------------------

    def day(self, t: float | None = None) -> str:
        return datetime.fromtimestamp(self._clock() if t is None else t, self._tz).date().isoformat()

    @property
    def seed(self) -> int:
        return self.s.event_seed

    def event_used(self) -> int:
        """Seed + committed + pending: the number that must never pass the cap."""
        with self._lock:
            return self._event_used_locked()

    def _event_used_locked(self) -> int:
        return self.s.event_seed + self._used + len(self._pending)

    def snapshot(self) -> dict:
        with self._lock:
            today = self.day()
            pend_today = sum(1 for r in self._pending.values() if r.bucket == "public")
            return {
                "models": "on" if models_enabled() else "off",
                "event": {
                    "used": self._event_used_locked(),
                    "cap": self.s.event_cap,
                    "seed": self.s.event_seed,
                },
                "today": {
                    "day": today,
                    "used": self._by_day.get(today, 0) + pend_today,
                    "cap": self.s.daily_cap,
                },
                "buckets": {
                    "dev": {"used": self._by_bucket.get("dev", 0), "cap": self.s.dev_cap},
                    "measurement": {
                        "used": self._by_bucket.get("measurement", 0),
                        "cap": self.s.measurement_cap,
                    },
                    "public": {"used": self._by_bucket.get("public", 0)},
                },
                "per_ip": {
                    "agent_per_hour": self.s.ip_agent_per_hour,
                    "raster_public_cap": self.s.raster_public_cap,
                },
            }

    def can_spend(self) -> bool:
        """Cheap pre-flight for the UI badge: models on and event cap not reached."""
        return models_enabled() and self.event_used() < self.s.event_cap

    # ---- reserve / commit / release --------------------------------------------------------

    def reserve(
        self,
        purpose: Purpose,
        bucket: Bucket = "public",
        *,
        ip_hash: str | None = None,
        presenter: bool = False,
    ) -> Reservation:
        if not models_enabled():
            raise BudgetError("models_off", "Models are switched off (AURA_MODELS=off).")
        now = self._clock()
        today = self.day(now)
        with self._lock:
            if self._event_used_locked() >= self.s.event_cap:
                raise BudgetError("budget_exhausted", "The event-wide model budget is used up.")
            pending_in = [r for r in self._pending.values() if r.bucket == bucket]
            if bucket == "dev":
                if self._by_bucket["dev"] + len(pending_in) >= self.s.dev_cap:
                    raise BudgetError("budget_exhausted", "The development budget is used up.")
            elif bucket == "measurement":
                if self._by_bucket["measurement"] + len(pending_in) >= self.s.measurement_cap:
                    raise BudgetError("budget_exhausted", "The measurement budget is used up.")
            else:
                pend_today = sum(1 for r in pending_in if r.day == today)
                if self._by_day[today] + pend_today >= self.s.daily_cap:
                    raise BudgetError("budget_exhausted", "Today's model budget is used up.")
                if not presenter:
                    if purpose in RASTER_PURPOSES:
                        if self._raster_public >= self.s.raster_public_cap:
                            raise BudgetError(
                                "presenter_required", "Raster analysis needs the presenter token."
                            )
                        self._raster_public += 1
                    elif purpose in AGENT_PURPOSES and ip_hash:
                        q = self._ip_agent[ip_hash]
                        while q and now - q[0] > 3600:
                            q.popleft()
                        if len(q) >= self.s.ip_agent_per_hour:
                            raise BudgetError(
                                "rate_limited", "Too many model questions from this address."
                            )
                        q.append(now)
            res = Reservation(
                id=self._next_id,
                purpose=purpose,
                bucket=bucket,
                ip_hash=ip_hash,
                presenter=presenter,
                day=today,
                t=now,
            )
            self._next_id += 1
            if self.ledger is not None:
                try:
                    near = self._event_used_locked() + 1 >= self.s.event_cap - NEAR_CAP
                    res.row_id = self.ledger.reserve(res, near)
                except Exception as e:  # fail closed: an uncounted request is never sent
                    if res.purpose in RASTER_PURPOSES and not presenter and bucket == "public":
                        self._raster_public = max(0, self._raster_public - 1)
                    if purpose in AGENT_PURPOSES and ip_hash and self._ip_agent[ip_hash]:
                        self._ip_agent[ip_hash].pop()
                    raise BudgetError("budget_exhausted",
                                      "The request counter could not be written.") from e
            self._pending[res.id] = res
            return res

    def commit(
        self,
        res: Reservation,
        *,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        status: int = 200,
    ) -> RequestRecord:
        """The request was sent: it counts, whatever the status."""
        with self._lock:
            if res.done:
                raise RuntimeError("reservation already settled")
            res.done = True
            self._pending.pop(res.id, None)
            self._used += 1
            self._by_bucket[res.bucket] += 1
            if res.bucket == "public":
                self._by_day[res.day] += 1
        rec = RequestRecord(
            t=self._clock(),
            day=res.day,
            model=model,
            purpose=res.purpose,
            bucket=res.bucket,
            ip_hash=res.ip_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(model, input_tokens, output_tokens),
            latency_ms=latency_ms,
            status=status,
        )
        if self.ledger is not None and res.row_id is not None:
            try:
                self.ledger.settle(res.row_id, rec)  # the row already counts; this adds cost
            except Exception:  # the count is safe (row written at reserve); only cost is lost
                logging.getLogger("aura.budget").warning("ledger settle failed", exc_info=True)
        if self._sink is not None:
            self._sink(rec)
        return rec

    def release(self, res: Reservation) -> None:
        """The request was never sent: give the slot back."""
        with self._lock:
            if res.done:
                return
            res.done = True
            self._pending.pop(res.id, None)
            if self.ledger is not None and res.row_id is not None:
                self.ledger.release(res.row_id)
            if res.purpose in RASTER_PURPOSES and not res.presenter and res.bucket == "public":
                self._raster_public = max(0, self._raster_public - 1)
            if res.purpose in AGENT_PURPOSES and res.ip_hash and not res.presenter:
                q = self._ip_agent.get(res.ip_hash)
                if q and res.t in q:
                    q.remove(res.t)
