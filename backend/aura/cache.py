"""Answer cache (in memory, LRU) and check cache (memory → SQLite → precomputed bundle file)."""

from __future__ import annotations

import json
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any

from aura.store import Store
from aura.writer import Writer


# Bumped when stored answers would be wrong under the current code (2: replayed budget refusals and
# scan answers stored as exact); older entries are then never hit and expire with their TTL.
KEY_VERSION = 7  # 7: zero counts with their reason on store sheets


def answer_key(drawing_id: str, question: str, scope: dict | None, overrides: list | None,
               lang_hint: str | None, models_on: bool) -> str:
    q = " ".join(unicodedata.normalize("NFC", question).casefold().split())
    return json.dumps([drawing_id, q, scope, overrides, lang_hint, models_on, KEY_VERSION],
                      sort_keys=True, ensure_ascii=False)


class AnswerCache:
    """In-memory + optional SQLite answers: LRU in RAM, and gone after `ttl_s` like stored
    answers, so nothing a user typed outlives the 24 h retention."""

    def __init__(
        self,
        max_items: int = 2000,
        ttl_s: float = 24 * 3600.0,
        clock=time.time,
        *,
        store: Store | None = None,
        writer: Writer | None = None,
    ):
        self._d: OrderedDict[str, dict] = OrderedDict()
        self._t: dict[str, float] = {}
        self._q: dict[str, str] = {}
        self.max = max_items
        self.ttl_s = ttl_s
        self._clock = clock
        self.store = store
        self.writer = writer

    @staticmethod
    def _parse_key(key: str) -> tuple[str | None, str | None, Any, Any, Any]:
        """Extract (drawing_id, q_norm, scope, overrides, models_on) from answer_key JSON prefix."""
        base = key.split("|", 1)[0]
        try:
            arr = json.loads(base)
        except (ValueError, TypeError):
            return None, None, None, None, None
        if isinstance(arr, list) and len(arr) >= 6:
            return str(arr[0]), str(arr[1]), arr[2], arr[3], arr[5]
        return None, None, None, None, None

    @staticmethod
    def _key_version(key: str) -> int | None:
        """The KEY_VERSION an answer was stored under (None for keys older than versioning)."""
        try:
            arr = json.loads(key.split("|", 1)[0])
        except (ValueError, TypeError):
            return None
        return arr[6] if isinstance(arr, list) and len(arr) >= 7 and isinstance(arr[6], int) else None

    def get(self, key: str) -> dict | None:
        now = self._clock()
        v = self._d.get(key)
        if v is not None and now - self._t.get(key, 0) > self.ttl_s:
            del self._d[key]
            self._t.pop(key, None)
            self._q.pop(key, None)
            v = None
        if v is not None:
            self._d.move_to_end(key)
            return v
        if self.store is not None:
            hit = self.store.cached_answer(key, now - self.ttl_s)
            if hit is not None:
                body, t, q = hit
                self._d[key] = body
                self._t[key] = t
                if q:
                    self._q[key] = q
                self._d.move_to_end(key)
                return body
        return None

    def put(self, key: str, body: dict, *, drawing_id: str | None = None,
            question: str | None = None) -> None:
        now = self._clock()
        parsed_did, parsed_q, *_ = self._parse_key(key)
        d_id = drawing_id or parsed_did
        q_text = question if question is not None else parsed_q
        self._d[key] = body
        self._t[key] = now
        if q_text:
            self._q[key] = q_text
        self._d.move_to_end(key)
        while len(self._d) > self.max:
            old, _ = self._d.popitem(last=False)
            self._t.pop(old, None)
            self._q.pop(old, None)
        if self.writer is not None and d_id:
            self.writer.submit(
                "INSERT OR REPLACE INTO answer_cache(key, t, drawing_id, question, response) "
                "VALUES (?,?,?,?,?)",
                (key, now, d_id, q_text or "", json.dumps(body)),
            )

    def entries_for_drawing(
        self,
        drawing_id: str,
        *,
        scope: dict | None = None,
        overrides: list | None = None,
        models_on: bool = True,
        limit: int = 40,
    ) -> list[tuple[str, str]]:
        """Return up to `limit` non-expired (key, question) pairs for `drawing_id` matching scope/overrides."""
        now = self._clock()
        min_t = now - self.ttl_s
        seen: set[str] = set()
        out: list[tuple[str, str]] = []

        def _matches_context(k: str) -> tuple[bool, str | None]:
            if "|prev:" in k:
                return False, None
            d_id, q_norm, k_scope, k_overrides, k_models = self._parse_key(k)
            if d_id != drawing_id or self._key_version(k) != KEY_VERSION:
                return False, None  # an answer stored under an older key version is never "the same question"
            if k_scope != scope or k_overrides != overrides or k_models != models_on:
                return False, None
            return True, q_norm

        for k in reversed(list(self._d.keys())):
            if self._t.get(k, 0) < min_t:
                continue
            ok, q_norm = _matches_context(k)
            if not ok:
                continue
            q = self._q.get(k) or q_norm or ""
            if q:
                seen.add(k)
                out.append((k, q))
                if len(out) >= limit:
                    return out

        if self.store is not None and len(out) < limit:
            for row in self.store.cached_entries_for_drawing(drawing_id, min_t, limit=limit * 2):
                k = row["key"]
                if k in seen:
                    continue
                ok, q_norm = _matches_context(k)
                if not ok:
                    continue
                q = row["question"] or q_norm or ""
                if q:
                    seen.add(k)
                    out.append((k, q))
                    if len(out) >= limit:
                        break
        return out

    def drop_drawing(self, drawing_id: str) -> None:
        prefix = json.dumps([drawing_id])[:-1] + ","
        for k in [k for k in self._d if k.startswith(prefix)]:
            del self._d[k]
            self._t.pop(k, None)
            self._q.pop(k, None)
        if self.writer is not None:
            self.writer.submit("DELETE FROM answer_cache WHERE drawing_id=?", (drawing_id,))

    def clear(self) -> None:
        self._d.clear()
        self._t.clear()
        self._q.clear()
        if self.writer is not None:
            self.writer.submit("DELETE FROM answer_cache")


class CheckCache:
    """Full-layer check results keyed by check_key(drawing, subject, ids)."""

    def __init__(self, store: Store | None, writer: Writer | None):
        self.store = store
        self.writer = writer
        self._mem: dict[str, dict] = {}
        self._bundles: dict[Path, dict] = {}  # parsed checks.json per library dir (read once)

    def forget_drawing(self, drawing_id: str) -> None:
        """Deleting an upload also drops its cached checks from memory."""
        for k in [k for k, v in self._mem.items() if v.get("drawing_id") == drawing_id]:
            del self._mem[k]

    def get(self, key: str, bundle_dir: Path | None = None, subject: str | None = None) -> dict | None:
        if key in self._mem:
            return self._mem[key]
        if self.store is not None:
            v = self.store.check(key)
            if v is not None:
                self._mem[key] = v
                return v
        if bundle_dir is not None and subject:
            # Precomputed library checks: checks.json = {subject: run_check(...) result + "key"}.
            if bundle_dir not in self._bundles:
                p = bundle_dir / "checks.json"
                self._bundles[bundle_dir] = json.loads(p.read_text()) if p.is_file() else {}
            v = self._bundles[bundle_dir].get(subject)
            if v and v.get("key") == key:
                self._mem[key] = v
                return v
        return None

    def put(self, key: str, drawing_id: str, subject: str, result: dict) -> None:
        self._mem[key] = result
        if self.writer is not None:
            self.writer.submit(
                "INSERT OR REPLACE INTO check_cache(key, t, drawing_id, subject, result) "
                "VALUES (?,?,?,?,?)",
                (key, time.time(), drawing_id, subject, json.dumps(result)),
            )
