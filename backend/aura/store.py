"""SQLite schema and reads. All writes go through writer.Writer (one writer task).

Journal mode is DELETE (not WAL): the DB lives on a Modal Volume, and a single file
is the safest thing to commit and reload.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  t REAL NOT NULL, day TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL,
  bucket TEXT NOT NULL, ip_hash TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cost_usd REAL, latency_ms INTEGER, status INTEGER
);
CREATE INDEX IF NOT EXISTS requests_day ON requests(day);
CREATE TABLE IF NOT EXISTS answers (
  id TEXT PRIMARY KEY, t REAL NOT NULL, day TEXT NOT NULL, drawing_id TEXT NOT NULL,
  question TEXT, route TEXT, method TEXT, calls INTEGER, cost_usd REAL,
  check_calls INTEGER DEFAULT 0, check_cost_usd REAL DEFAULT 0, response TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answers_day ON answers(day);
CREATE TABLE IF NOT EXISTS check_cache (
  key TEXT PRIMARY KEY, t REAL NOT NULL, drawing_id TEXT NOT NULL, subject TEXT NOT NULL,
  result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, t REAL NOT NULL, drawing_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, t REAL NOT NULL, message TEXT,
  response TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS turns_conv ON turns(conversation_id);
CREATE TABLE IF NOT EXISTS drawings (
  id TEXT PRIMARY KEY, t REAL NOT NULL, kind TEXT, status TEXT, progress REAL, error TEXT,
  title TEXT, delete_token_hash TEXT, expires_at REAL, meta TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, drawing_id TEXT NOT NULL, t REAL NOT NULL, status TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS answer_cache (
  key TEXT PRIMARY KEY, t REAL NOT NULL, drawing_id TEXT NOT NULL,
  question TEXT, response TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answer_cache_drawing ON answer_cache(drawing_id);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, t REAL NOT NULL, title TEXT NOT NULL,
  floors TEXT NOT NULL, auto_matched INTEGER DEFAULT 0, body TEXT NOT NULL
);
"""


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(path: Path) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


class Store:
    """Read side. Short-lived connections; sqlite reads are cheap."""

    def __init__(self, path: Path):
        self.path = path

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = connect(self.path, readonly=True)
        try:
            return list(conn.execute(sql, params))
        finally:
            conn.close()

    def ok(self) -> bool:
        try:
            self._q("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    # budget
    def request_counts(self) -> tuple[int, dict[str, int], dict[str, int]]:
        total = self._q("SELECT COUNT(*) n FROM requests")[0]["n"]
        by_bucket = {r["bucket"]: r["n"] for r in self._q(
            "SELECT bucket, COUNT(*) n FROM requests GROUP BY bucket")}
        by_day = {r["day"]: r["n"] for r in self._q(
            "SELECT day, COUNT(*) n FROM requests WHERE bucket='public' GROUP BY day")}
        return total, by_bucket, by_day

    def recent_limits(self, now: float, purposes_agent: set[str], purposes_raster: set[str],
                      ) -> tuple[dict[str, list[float]], int]:
        """Per-IP agent request times in the last hour, and raster requests ever, so the
        per-IP and public-raster limits survive a restart (they were in memory only)."""
        qa = ",".join("?" * len(purposes_agent))
        by_ip: dict[str, list[float]] = {}
        for r in self._q(f"SELECT ip_hash, t FROM requests WHERE purpose IN ({qa}) AND "
                         "ip_hash IS NOT NULL AND t > ? ORDER BY t", (*purposes_agent, now - 3600)):
            by_ip.setdefault(r["ip_hash"], []).append(r["t"])
        qr = ",".join("?" * len(purposes_raster))
        raster = self._q(f"SELECT COUNT(*) n FROM requests WHERE purpose IN ({qr})",
                         tuple(purposes_raster))[0]["n"]
        return by_ip, raster

    def recent_agent_runs(self, now: float, today: str) -> tuple[dict[str, list[float]], int]:
        """Per-IP sidecar agent_run timestamps in the last hour, plus public agent_runs today."""
        by_ip: dict[str, list[float]] = {}
        for r in self._q(
            "SELECT ip_hash, t FROM requests WHERE purpose='agent_run' AND bucket='public' "
            "AND ip_hash IS NOT NULL AND t > ? ORDER BY t",
            (now - 3600,),
        ):
            by_ip.setdefault(r["ip_hash"], []).append(r["t"])
        today_runs = self._q(
            "SELECT COUNT(*) n FROM requests WHERE purpose='agent_run' AND bucket='public' AND day=?",
            (today,),
        )[0]["n"]
        return by_ip, int(today_runs)

    # answers
    def answer(self, answer_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT response FROM answers WHERE id=?", (answer_id,))
        return json.loads(rows[0]["response"]) if rows else None

    def answer_drawing(self, answer_id: str) -> str | None:
        rows = self._q("SELECT drawing_id FROM answers WHERE id=?", (answer_id,))
        return rows[0]["drawing_id"] if rows else None

    def recent_turns(self, conv_id: str, n: int = 6) -> list[dict]:
        rows = self._q("SELECT message, response FROM turns WHERE conversation_id=? AND response "
                       "IS NOT NULL ORDER BY t DESC LIMIT ?", (conv_id, n))
        return [{"message": r["message"], "response": json.loads(r["response"])}
                for r in reversed(rows)]

    def stats(self, day: str) -> dict[str, Any]:
        rows = self._q(
            "SELECT calls, check_calls, check_cost_usd, cost_usd FROM answers WHERE day=?", (day,))
        calls_today = self._q("SELECT COUNT(*) n FROM requests WHERE day=?", (day,))[0]["n"]
        return {"answers": [dict(r) for r in rows], "model_calls_today": calls_today}

    # check cache
    def check(self, key: str) -> dict | None:
        rows = self._q("SELECT result FROM check_cache WHERE key=?", (key,))
        return json.loads(rows[0]["result"]) if rows else None

    # conversations
    def conversation(self, conv_id: str) -> dict | None:
        c = self._q("SELECT id, drawing_id FROM conversations WHERE id=?", (conv_id,))
        if not c:
            return None
        turns = self._q(
            "SELECT id, message, response, error FROM turns WHERE conversation_id=? ORDER BY t",
            (conv_id,))
        return {
            "id": c[0]["id"],
            "drawing_id": c[0]["drawing_id"],
            "turns": [
                {
                    "turn_id": t["id"],
                    "message": t["message"],
                    **({"response": json.loads(t["response"])} if t["response"] else {}),
                    **({"error": json.loads(t["error"])} if t["error"] else {}),
                }
                for t in turns
            ],
        }

    # uploads
    def drawing_row(self, drawing_id: str) -> dict | None:
        rows = self._q("SELECT * FROM drawings WHERE id=?", (drawing_id,))
        return dict(rows[0]) if rows else None

    def upload_rows(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM drawings ORDER BY t DESC")]

    # answer cache
    def cached_answer(self, key: str, min_t: float) -> tuple[dict[str, Any], float, str] | None:
        rows = self._q(
            "SELECT response, t, question FROM answer_cache WHERE key=? AND t >= ?",
            (key, min_t),
        )
        if not rows:
            return None
        return json.loads(rows[0]["response"]), float(rows[0]["t"]), rows[0]["question"] or ""

    def cached_entries_for_drawing(self, drawing_id: str, min_t: float,
                                   limit: int = 50) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT key, question, t FROM answer_cache WHERE drawing_id=? AND t >= ? "
            "ORDER BY t DESC LIMIT ?",
            (drawing_id, min_t, limit),
        )
        return [{"key": r["key"], "question": r["question"] or "", "t": float(r["t"])} for r in rows]

    # projects
    @staticmethod
    def _decode_project(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        raw_floors = d.get("floors")
        if isinstance(raw_floors, str):
            try:
                d["floors"] = json.loads(raw_floors)
            except ValueError:
                d["floors"] = []
        d["auto_matched"] = bool(d.get("auto_matched"))
        return d

    def project_row(self, project_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM projects WHERE id=?", (project_id,))
        return self._decode_project(rows[0]) if rows else None

    def project_rows(self) -> list[dict[str, Any]]:
        return [self._decode_project(r) for r in self._q("SELECT * FROM projects ORDER BY t DESC")]
