"""Answer cache (in memory, LRU) and check cache (memory → SQLite → precomputed bundle file)."""

from __future__ import annotations

import json
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path

from aura.store import Store
from aura.writer import Writer


def answer_key(drawing_id: str, question: str, scope: dict | None, overrides: list | None,
               lang_hint: str | None, models_on: bool) -> str:
    q = " ".join(unicodedata.normalize("NFC", question).casefold().split())
    return json.dumps([drawing_id, q, scope, overrides, lang_hint, models_on], sort_keys=True,
                      ensure_ascii=False)


class AnswerCache:
    def __init__(self, max_items: int = 2000):
        self._d: OrderedDict[str, dict] = OrderedDict()
        self.max = max_items

    def get(self, key: str) -> dict | None:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key: str, body: dict) -> None:
        self._d[key] = body
        self._d.move_to_end(key)
        while len(self._d) > self.max:
            self._d.popitem(last=False)

    def drop_drawing(self, drawing_id: str) -> None:
        prefix = json.dumps([drawing_id])[:-1] + ","
        for k in [k for k in self._d if k.startswith(prefix)]:
            del self._d[k]

    def clear(self) -> None:
        self._d.clear()


class CheckCache:
    """Full-layer check results keyed by check_key(drawing, subject, ids)."""

    def __init__(self, store: Store | None, writer: Writer | None):
        self.store = store
        self.writer = writer
        self._mem: dict[str, dict] = {}

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
            p = bundle_dir / "checks.json"
            if p.is_file():
                v = json.loads(p.read_text()).get(subject)
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
