"""Run registry: create, execute, observe and persist agent runs (runs/<id>/run.json below the store directory)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from agents import ItemHelpers, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model

from ..db import DB
from . import store
from .agent import Answers, build_agent, build_model, enrich, normalise, parse_final, relay_tool_images, task_prompt
from .config import Settings
from .files import Ingestor, NotFound, check_id, new_id
from .tools import RunCtx, clip

TERMINAL = ("done", "error")
PERSISTED = ("id", "status", "model", "files", "drawings", "questions", "answers", "usage", "error", "created",
             "seconds", "events")


@dataclass
class Run:
    id: str
    files: list[dict]                 # the files asked about: [{file_id, name, original_name, format, drawing}]
    questions: list[str]
    model: str
    drawings: list[str] = field(default_factory=list)   # the scope: drawing keys (every file of them is readable)
    status: str = "queued"            # queued | running | done | error
    answers: list[dict] | None = None
    usage: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    seconds: float | None = None
    events: list[dict] = field(default_factory=list)
    _changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def emit(self, type: str, **fields: Any) -> int:
        """Append one event -> its seq (dense, from 1, so a client can detect gaps and resume)."""
        self.events.append(dict(seq=len(self.events) + 1, t=round(time.monotonic() - self._t0, 2), type=type, **fields))
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()
        return len(self.events)

    def public(self, with_events: bool = True) -> dict:
        return {k: getattr(self, k) for k in PERSISTED if with_events or k != "events"}


class Registry:
    def __init__(self, settings: Settings, ingestor: Ingestor, model: Model | None = None):
        self.settings, self.ingestor, self.injected = settings, ingestor, model
        self.model: Model = model or build_model(settings)  # a Model instance is how tests plug in a scripted model
        self.dir = settings.data_dir / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runs: dict[str, Run] = {}
        self.tasks: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(settings.max_concurrent)
        self._load()

    # ---- persistence
    def _save(self, run: Run) -> None:
        fp = self.dir / run.id / "run.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_suffix(".tmp")
        tmp.write_text(json.dumps(run.public(), ensure_ascii=False, default=str))
        os.replace(tmp, fp)  # atomic: a crash never leaves a half-written run.json

    def _load(self) -> None:
        for fp in self.dir.glob("*/run.json"):
            try:
                run = Run(**json.loads(fp.read_text()))
            except (OSError, ValueError, TypeError):
                continue
            if run.status not in TERMINAL:  # the process that was executing it is gone
                run.status, run.error = "error", "interrupted by a service restart"
                run.emit("run_finished", status=run.status, seconds=None, usage=run.usage, error=run.error)
                self._save(run)
            self.runs[run.id] = run

    # ---- access
    def get(self, run_id: str) -> Run:
        run = self.runs.get(check_id(run_id))
        if run is None:
            raise NotFound(run_id)
        return run

    def run_dir(self, run: Run) -> Path:
        return self.dir / run.id

    # ---- lifecycle
    def create(self, file_ids: list[str], questions: list[str]) -> Run:
        ids = [check_id(f) for f in dict.fromkeys(file_ids)]
        with DB() as db:
            rows = {r["file_id"]: r for r in db.run(
                """select file_id, path, format, drawing_key, status, meta from jstore.source_file
                   where file_id = any(cast(:f as text[]))""", f=ids)}
        for f in ids:
            if f not in rows:
                raise NotFound(f)  # before anything is written
            self.ingestor.ensure(rows[f])  # registered but never parsed (or failed): try now, the run waits for it
        files = [dict(file_id=f, name=f"{f}.{rows[f]['format']}", original_name=Path(rows[f]["path"]).name,
                      format=rows[f]["format"], drawing=rows[f]["drawing_key"]) for f in ids]
        run = Run(id=new_id(), files=files, questions=questions,
                  drawings=sorted({r["drawing_key"] for r in rows.values()}),
                  model=self.settings.model if self.injected is None else type(self.injected).__name__)
        self.runs[run.id] = run
        self._save(run)
        return run

    def start(self, run: Run) -> asyncio.Task:
        task = asyncio.create_task(self.execute(run), name=f"run-{run.id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def execute(self, run: Run) -> Run:
        """Run the agent to completion.  Never raises for an agent failure: the run ends with status "error"."""
        try:
            async with self._slots:
                run.status, run._t0 = "running", time.monotonic()
                run.emit("run_started", run_id=run.id, model=run.model, files=[f["name"] for f in run.files],
                         drawings=run.drawings, questions=run.questions)
                self._save(run)
                await self._drive(run)
                run.status = "done"
        except asyncio.CancelledError:
            run.status, run.error = "error", "cancelled (service shutting down)"
            raise
        except Exception as e:  # noqa: BLE001 - model/provider errors, max turns, invalid final output, ...
            run.status, run.error = "error", clip(f"{type(e).__name__}: {e}", 2000)
        finally:
            run.seconds = round(time.monotonic() - run._t0, 2)
            run.emit("run_finished", status=run.status, seconds=run.seconds, usage=run.usage, error=run.error)
            self._save(run)
        return run

    async def _drive(self, run: Run) -> None:
        s, ids = self.settings, [f["file_id"] for f in run.files]
        waiting = self.ingestor.pending(ids)
        if waiting:  # an upload is still being parsed: the store has nothing to say about it yet
            run.emit("ingest", status="waiting", files=waiting)
            await self.ingestor.wait(ids, s.ingest_wait_s)
            run.emit("ingest", status="finished" if not self.ingestor.pending(ids) else "still running", files=waiting)

        def prepare() -> tuple[dict, str]:
            with DB() as db:
                scope = store.scope_of(db, ids)
                return scope, task_prompt(db, scope, run.questions)
        scope, prompt = await asyncio.to_thread(prepare)
        run.drawings = scope["drawings"]
        reader = store.Reader(scope["drawings"], [f["file_id"] for f in scope["files"]], s.sql_max_rows, s.sql_max_chars)
        ctx = RunCtx(settings=s, emit=run.emit, run_dir=self.run_dir(run), reader=reader, files=scope["files"])
        # tracing would upload run data to OpenAI: always off
        config = RunConfig(model=self.model, tracing_disabled=True, workflow_name="jstore drawing QA",
                           tool_not_found_behavior="return_error_to_model", call_model_input_filter=relay_tool_images)
        result = Runner.run_streamed(build_agent(s), prompt, context=ctx, max_turns=s.max_turns, run_config=config)
        calls: dict[str, tuple[str, str]] = {}
        last_text = ""
        try:
            async for ev in result.stream_events():
                if ev.type != "run_item_stream_event":
                    continue
                if ev.name == "message_output_created":
                    last_text = ItemHelpers.text_message_output(ev.item).strip()
                    if last_text and parse_final(last_text) is None:  # the final message is sent as "answers"
                        run.emit("message", text=clip(last_text, 4000))
                elif ev.name == "reasoning_item_created":
                    text = "\n".join(t.text for t in ev.item.raw_item.summary or [] if getattr(t, "text", None))
                    if text:
                        run.emit("reasoning", text=clip(text, 4000))
                elif ev.name == "tool_called":
                    calls[ev.item.call_id or ""] = (ev.item.tool_name or "?", str(getattr(ev.item.raw_item, "arguments", "")))
                elif ev.name == "tool_output" and not any(
                        e["type"] == "tool_finished" and e["call_id"] == ev.item.call_id for e in run.events):
                    # the SDK answered the call itself (unknown tool, arguments that do not fit): our tool never ran
                    tool, arguments = calls.get(ev.item.call_id or "", ("?", ""))
                    run.emit("tool_started", call_id=ev.item.call_id, tool=tool, arguments=clip(arguments, 2000))
                    run.emit("tool_finished", call_id=ev.item.call_id, tool=tool, exit_code=None, timed_out=False,
                             ended="rejected", seconds=0.0, output=clip(str(ev.item.output), s.max_event_chars))
            final = result.final_output_as(Answers, raise_if_incorrect_type=True)
        except asyncio.CancelledError:
            result.cancel()
            if ctx.calls:  # let a running shell call kill its command
                await asyncio.wait(set(ctx.calls), timeout=10)
            raise
        except (ModelBehaviorError, TypeError):  # the final message did not parse as it stands: salvage it
            final = parse_final(last_text)
            if final is None:
                raise
        finally:
            u = result.context_wrapper.usage  # the SDK reports tokens, not money; requests = model calls
            run.usage = dict(requests=u.requests, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                             total_tokens=u.total_tokens, sql_calls=len(reader.log))
            await asyncio.to_thread(reader.close)

        def finish() -> list[dict]:
            with DB() as db:
                return enrich(db, normalise(final, len(run.questions)), scope, reader.log, s)
        run.answers = await asyncio.to_thread(finish)
        run.emit("answers", answers=run.answers)

    def stop(self) -> None:
        """Cancel every run in flight; each ends as an error run, which also ends its event streams."""
        for t in list(self.tasks):
            t.cancel()

    async def shutdown(self) -> None:
        self.stop()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # ---- live trace
    async def subscribe(self, run: Run, after: int = 0) -> AsyncIterator[dict]:
        """Events with seq > after: the backlog first, then live ones until the run is finished."""
        sent = max(0, after)
        while True:
            changed = run._changed  # grab before reading, so an emit in between is not missed
            while sent < len(run.events):
                yield run.events[sent]
                sent += 1
            if run.status in TERMINAL and sent >= len(run.events):
                return
            await changed.wait()
