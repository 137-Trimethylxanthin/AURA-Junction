"""Run registry: create, execute, observe, cancel and persist agent runs."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from agents import ItemHelpers, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model
from openai import BadRequestError

from .agent import Answers, build_agent, build_model, normalise, parse_final, relay_tool_images, task_prompt
from .config import Settings
from .tools import RunCtx, clip
from .workspace import FileStore, NotFound, Workspace, check_id, new_id

TERMINAL = ("done", "error", "cancelled")
PERSISTED = ("id", "status", "model", "files", "questions", "answers", "usage", "error", "created", "seconds", "events")


@dataclass
class Run:
    id: str
    files: list[dict]                 # [{file_id, name (in the workspace), original_name}]
    questions: list[str]
    model: str
    status: str = "queued"            # queued | running | done | error | cancelled
    answers: list[dict] | None = None
    usage: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    seconds: float | None = None
    events: list[dict] = field(default_factory=list)
    _changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _t0: float = field(default_factory=time.monotonic, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)

    def emit(self, type: str, **fields: Any) -> int:
        """Append one event -> its seq.  `seq` is dense and starts at 1, so a client can detect gaps and resume."""
        self.events.append(dict(seq=len(self.events) + 1, t=round(time.monotonic() - self._t0, 2), type=type, **fields))
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()  # wake every subscriber waiting on the previous event object
        return len(self.events)

    def public(self, with_events: bool = True) -> dict:
        return {k: getattr(self, k) for k in PERSISTED if with_events or k != "events"}


class Registry:
    def __init__(self, settings: Settings, files: FileStore, model: str | Model | None = None):
        self.settings, self.files, self.injected = settings, files, model
        # a Model instance is how tests plug in a scripted model; otherwise OpenAI directly or the Bifrost gateway
        self.model: str | Model = model or build_model(settings)
        self.dir = settings.data_dir / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runs: dict[str, Run] = {}
        self.tasks: set[asyncio.Task] = set()
        self.task_by_id: dict[str, asyncio.Task] = {}
        self._slots = asyncio.Semaphore(settings.max_concurrent)
        self._load()

    # ---- persistence ------------------------------------------------------------------------------------
    def _save(self, run: Run) -> None:
        fp = self.dir / run.id / "run.json"
        tmp = fp.with_suffix(".tmp")
        tmp.write_text(json.dumps(run.public(), ensure_ascii=False))
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

    # ---- access -----------------------------------------------------------------------------------------
    def get(self, run_id: str) -> Run:
        run = self.runs.get(check_id(run_id))
        if run is None:
            raise NotFound(run_id)
        return run

    def workspace(self, run: Run) -> Workspace:
        ws = Workspace(self.dir / run.id / "ws")
        ws.inputs = [f["name"] for f in run.files]
        return ws

    # ---- lifecycle --------------------------------------------------------------------------------------
    def create(self, file_ids: list[str], questions: list[str]) -> Run:
        metas = [self.files.get(fid) for fid in file_ids]  # NotFound for unknown ids, before anything is written
        run_id = new_id()
        placed = Workspace(self.dir / run_id / "ws").place(metas)
        run = Run(id=run_id, files=placed, questions=questions,
                  model=self.settings.model if self.injected is None else type(self.injected).__name__)
        self.runs[run.id] = run
        self._save(run)
        return run

    def start(self, run: Run) -> asyncio.Task:
        task = asyncio.create_task(self.execute(run), name=f"run-{run.id}")
        self.tasks.add(task)
        self.task_by_id[run.id] = task

        def _cleanup(t: asyncio.Task) -> None:
            self.tasks.discard(t)
            if self.task_by_id.get(run.id) is t:
                self.task_by_id.pop(run.id, None)

        task.add_done_callback(_cleanup)
        return task

    async def cancel(self, run_id: str) -> Run:
        """Cancel a queued or running run cleanly (including any active shell subprocess / container)."""
        run = self.get(run_id)
        if run.status in TERMINAL:
            return run
        run._cancel_requested = True
        task = self.task_by_id.get(run.id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif run.status not in TERMINAL:
            run.status, run.error = "cancelled", "cancelled by client"
            run.seconds = round(time.monotonic() - run._t0, 2)
            run.emit("run_finished", status=run.status, seconds=run.seconds, usage=run.usage, error=run.error)
            self._save(run)
        return run

    async def execute(self, run: Run) -> Run:
        """Run the agent to completion.  Never raises for an agent failure: the run ends with status "error"."""
        try:
            async with self._slots:
                if run._cancel_requested:
                    raise asyncio.CancelledError()
                run.status, run._t0 = "running", time.monotonic()
                run.emit("run_started", run_id=run.id, model=run.model, files=[f["name"] for f in run.files],
                         questions=run.questions)
                self._save(run)
                await self._drive(run)
                run.status = "done"
        except asyncio.CancelledError:
            if run._cancel_requested:
                run.status, run.error = "cancelled", "cancelled by client"
            else:
                run.status, run.error = "error", "cancelled (service shutting down)"
            raise
        except Exception as e:  # model/provider errors, max turns, invalid final output, ...
            run.status, run.error = "error", clip(f"{type(e).__name__}: {e}", 2000)
        finally:
            run.seconds = round(time.monotonic() - run._t0, 2)
            run.emit("run_finished", status=run.status, seconds=run.seconds, usage=run.usage, error=run.error)
            self._save(run)
        return run

    async def _drive(self, run: Run) -> None:
        ws, s = self.workspace(run), self.settings
        ctx = RunCtx(workspace=ws, settings=s, emit=run.emit, run_dir=self.dir / run.id)
        prompt = task_prompt(ws.inputs, str(ws.root.resolve()), run.questions)
        # Tracing uploads run data to OpenAI with OPENAI_API_KEY; behind a gateway there may be no such key, and
        # the gateway key is never offered to the exporter (it only lives in the gateway client).
        tracing = s.tracing and s.openai_base_url is None and (s.bifrost_url is None or bool(os.environ.get("OPENAI_API_KEY")))
        config = RunConfig(model=self.model, tracing_disabled=not tracing, workflow_name="junction drawing QA",
                           tool_not_found_behavior="return_error_to_model",  # a mis-named tool costs a turn, not the run
                           call_model_input_filter=relay_tool_images if s.api_style == "chat" else None)

        async def _stream_once(effort: str | None) -> list[dict]:
            result = Runner.run_streamed(build_agent(effort), prompt, context=ctx, max_turns=s.max_turns, run_config=config)
            calls: dict[str, tuple[str, str]] = {}  # call_id -> (tool, arguments) as the model sent them
            last_text, out = "", None
            try:
                async for ev in result.stream_events():
                    if ev.type != "run_item_stream_event":
                        continue  # token deltas are not forwarded: see README, "Events"
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
            except asyncio.CancelledError:
                result.cancel()  # stop the SDK's background run loop too
                if ctx.calls:  # the SDK cancels a running tool call without waiting for it: let it kill its command
                    await asyncio.wait(set(ctx.calls), timeout=10)
                raise
            except ModelBehaviorError:  # the final message did not parse as it stands: salvage it if the answer is in there
                out = parse_final(last_text)
                if out is None:
                    raise
            finally:
                u = result.context_wrapper.usage  # the SDK reports tokens, not money
                prev = run.usage or dict(requests=0, input_tokens=0, output_tokens=0, total_tokens=0)
                run.usage = dict(
                    requests=prev["requests"] + u.requests,
                    input_tokens=prev["input_tokens"] + u.input_tokens,
                    output_tokens=prev["output_tokens"] + u.output_tokens,
                    total_tokens=prev["total_tokens"] + u.total_tokens,
                )
            return normalise(out or result.final_output_as(Answers, raise_if_incorrect_type=True), len(run.questions))

        effort = s.reasoning_effort if self.injected is None and s.model != "demo" else None
        try:
            run.answers = await _stream_once(effort)
        except BadRequestError as e:
            if effort is not None and "reasoning" in str(e).lower():
                run.answers = await _stream_once(None)
            else:
                raise
        run.emit("answers", answers=run.answers)

    def stop(self) -> None:
        """Cancel every run in flight; each ends as an error run, which also ends its event streams."""
        for t in list(self.tasks):
            t.cancel()

    async def shutdown(self) -> None:
        self.stop()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # ---- live trace -------------------------------------------------------------------------------------
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
