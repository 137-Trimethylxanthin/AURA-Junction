"""The agent's tools, bound to one run through the SDK run context:
sql (the store, read-only and scoped), view_image (a rendered page or a crop of it), shell (last resort)."""
from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agents import ToolOutputImage, ToolOutputText, function_tool
from agents.tool_context import ToolContext

from .config import Settings
from .files import ID_RE, Forbidden, contained, locate, new_id
from .store import Reader

MAX_PIXELS = 150_000_000  # refuse to decode anything larger (decompression bombs); far above any page render


@dataclass
class RunCtx:
    """What the SDK hands to every tool call of a run (`Runner.run(..., context=RunCtx(...))`)."""
    settings: Settings
    emit: Callable[..., int]   # emit(type, **fields) -> seq: append a typed event to the run log
    run_dir: Path              # runs/<id>: views/ holds what the model looked at, ws/ is the shell's directory
    reader: Reader             # the run's scoped read-only connection; reader.log feeds answer verification
    files: list[dict]          # every file in scope: {file_id, path, format, ...}
    calls: set[asyncio.Task] = field(default_factory=set)  # tool calls in flight; a cancelled run waits for them
    shell_text: list[str] = field(default_factory=list)

    @property
    def ws(self) -> Path:
        return self.run_dir / "ws"


def clip(text: str, limit: int) -> str:
    """Keep the head and the tail: the start shows what ran, the end holds results and tracebacks."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - 2 * half} characters omitted] ...\n{text[-half:]}"


# ---------------------------------------------------------------------------------------------- sql
@function_tool
async def sql(ctx: ToolContext[RunCtx], query: str) -> str:
    """Run one read-only SQL statement (a single SELECT or WITH) against the views in schema q and get the rows.
    The views only show the drawings of this run. Errors come back as text: fix the query and call again.

    Args:
        query: The statement. PostgreSQL with PostGIS; write views as q.rooms, q.cad_entities, ...
    """
    rc: RunCtx = ctx.context
    rc.calls.add(me := asyncio.current_task())
    rc.emit("tool_started", call_id=ctx.tool_call_id, tool="sql", query=query, arguments=query)
    t0, r = time.monotonic(), dict(ok=False, rows=0, returned=0, text="cancelled")
    try:
        r = await asyncio.to_thread(rc.reader.query, query)
    except Exception as e:  # noqa: BLE001 - the database is away: tell the model, do not fail the run
        r = dict(ok=False, rows=0, returned=0, text=f"Error: {type(e).__name__}: {e}"[:400])
    finally:
        rc.emit("tool_finished", call_id=ctx.tool_call_id, tool="sql", exit_code=0 if r["ok"] else 1, timed_out=False,
                ended="exit" if r["ok"] else "error", seconds=round(time.monotonic() - t0, 2), rows=r["rows"],
                returned=r["returned"], output=clip(r["text"], rc.settings.max_event_chars))
        rc.calls.discard(me)
    return r["text"]


# ---------------------------------------------------------------------------------------------- view_image
def load_page(path: Path, box: list[float] | None, max_px: int) -> tuple[bytes, tuple[int, int], tuple[int, int, int, int], tuple[int, int]]:
    """-> (PNG bytes, full size, crop box in pixels, shown size); cropped to `box` (percent), long side <= max_px."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 2
    with Image.open(path) as im:
        full = im.size
        if full[0] * full[1] > MAX_PIXELS:
            raise ValueError(f"image too large: {full[0]}x{full[1]} px")
        crop = (0, 0, full[0], full[1])
        if box:
            x0, y0, x1, y1 = (min(100.0, max(0.0, float(v))) for v in box)
            if x1 - x0 < 0.05 or y1 - y0 < 0.05:
                raise ValueError("box must be [x0, y0, x1, y1] in percent of the page image with x0 < x1 and y0 < y1")
            crop = (int(full[0] * x0 / 100), int(full[1] * y0 / 100),
                    max(int(full[0] * x0 / 100) + 1, round(full[0] * x1 / 100)),
                    max(int(full[1] * y0 / 100) + 1, round(full[1] * y1 / 100)))
            im = im.crop(crop)
        else:
            im.load()
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        (im if im.mode in ("1", "L", "LA", "P", "RGB", "RGBA") else im.convert("RGB")).save(buf, "PNG", optimize=True)
        return buf.getvalue(), full, crop, im.size


@function_tool
async def view_image(ctx: ToolContext[RunCtx], file_id: str, page_no: int,
                     box: list[float] | None = None) -> list[ToolOutputText | ToolOutputImage] | str:
    """Look at the rendered image of a page (see q.pages), or at a zoomed crop of it.

    Args:
        file_id: The file, as in q.files / q.pages.
        page_no: The page number, as in q.pages (0-based).
        box: Optional crop [x0, y0, x1, y1] in percent of the page image (0-100, origin top-left). null shows the whole page.
    """
    rc: RunCtx = ctx.context
    rc.calls.add(me := asyncio.current_task())
    seq = rc.emit("tool_started", call_id=ctx.tool_call_id, tool="view_image", path=f"{file_id}/page {page_no}",
                  arguments=dict(file_id=file_id, page_no=page_no, box=box))
    t0, error, extra = time.monotonic(), "cancelled", {}
    try:
        if not ID_RE.match(file_id or "") or file_id not in {f["file_id"] for f in rc.files}:
            raise LookupError("no such file in this run (file ids are listed in q.files)")
        page = await asyncio.to_thread(_page, rc, file_id, int(page_no))
        if page is None:
            raise LookupError(f"file {file_id} has no page {page_no} (see q.pages)")
        if not page["image"]:
            raise LookupError("this page has no rendered image; answer from the data views instead")
        path = contained(rc.settings.data_dir, page["image"])
        if not path.is_file():
            raise LookupError("the rendered image of this page is missing on disk")
        data, full, crop, shown = await asyncio.to_thread(load_page, path, box, rc.settings.max_image_px)
        view = rc.run_dir / "views" / f"{seq}.png"  # exactly what the model saw, named by the event's seq
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_bytes(data)
        extra = dict(file_id=file_id, page_no=page_no, box=box, view=f"/runs/{rc.run_dir.name}/views/{view.name}",
                     width=full[0], height=full[1], shown_width=shown[0], shown_height=shown[1])
        error = None
    except (LookupError, ValueError, Forbidden, OSError) as e:
        error = str(e)[:300] or type(e).__name__
    finally:
        rc.emit("tool_finished", call_id=ctx.tool_call_id, tool="view_image", exit_code=1 if error else 0,
                timed_out=False, ended="error" if error else "exit", seconds=round(time.monotonic() - t0, 2),
                output=error or "", **extra)
        rc.calls.discard(me)
    if error:
        return f"Error: {error}"
    note = f"file {file_id} page {page_no}: page image {full[0]}x{full[1]} px"
    if box:
        note += f", showing the crop {[round(float(v), 2) for v in box]} percent (pixels {list(crop)})"
    if shown != (crop[2] - crop[0], crop[3] - crop[1]):
        note += f", downscaled to {shown[0]}x{shown[1]}"
    return [ToolOutputText(text=note),
            ToolOutputImage(image_url=f"data:image/png;base64,{base64.b64encode(data).decode()}", detail="high")]


def _page(rc: RunCtx, file_id: str, page_no: int) -> dict | None:
    with rc.reader.lock:  # file_id is validated hex and page_no an int: safe to inline
        rows = rc.reader._run(f"select image, image_w, image_h from q.pages where file_id = '{file_id}' and page_no = {page_no}")
    return rows[0] if rows else None


# ---------------------------------------------------------------------------------------------- shell
def place_files(rc: RunCtx) -> list[str]:
    """Copy the run's original files into the shell directory as drawing.<ext>, drawing_2.<ext> ... (once).
    Copies, not links: a command must not be able to change the stored original."""
    ws = rc.ws
    marker = ws / ".placed"
    if marker.is_file():
        return marker.read_text().splitlines()
    ws.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, f in enumerate(rc.files, 1):
        src = locate(rc.settings, f["path"], f.get("meta"))
        name = f"drawing.{f['format']}" if i == 1 else f"drawing_{i}.{f['format']}"
        if src is None:
            lines.append(f"{f['file_id']}  (original not available)")
            continue
        shutil.copyfile(src, ws / name)
        lines.append(f"{name}  = file {f['file_id']}")
    marker.write_text("\n".join(lines))
    return lines


def shell_env(ws: Path, call: str) -> dict[str, str]:
    """The complete environment of a shell call, built from nothing: no API key, token or database address of
    the service process reaches the agent's commands.  JSTORE_CALL names the call so leftovers can be found."""
    return {"JSTORE_CALL": call, "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(ws), "PWD": str(ws), "TMPDIR": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "MPLBACKEND": "Agg", "MPLCONFIGDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}


def _sweep(call: str) -> None:
    """Kill what a command left running outside its process group, found by the call's marker in its environment."""
    marker = f"JSTORE_CALL={call}\0".encode()
    for env in Path("/proc").glob("[0-9]*/environ"):
        try:
            if marker in env.read_bytes():
                os.kill(int(env.parent.name), signal.SIGKILL)
        except OSError:
            continue


async def run_shell(rc: RunCtx, command: str, timeout_s: int | None) -> dict:
    """Run one command -> {exit_code, ended (exit | timeout | overflow), seconds, output, timeout_s}."""
    s, ws = rc.settings, rc.ws.resolve()
    timeout = max(1, min(timeout_s or s.shell_timeout, s.shell_timeout))
    name, t0, ended = "jstore-" + new_id(), time.monotonic(), "exit"
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", command, cwd=ws, env=shell_env(ws, name), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    head, tail, total, keep = bytearray(), bytearray(), 0, 2 * s.max_output_chars

    async def drain() -> bool:
        nonlocal total, tail
        while chunk := await proc.stdout.read(1 << 16):
            total += len(chunk)
            room = keep - len(head)
            if room > 0:
                head.extend(chunk[:room])
                chunk = chunk[room:]
            tail = (tail + chunk)[-keep:]
            if total > s.max_output_bytes:
                return True
        return False

    async def exited() -> None:  # not proc.wait(): that also waits for the pipe, which a background child can hold
        while proc.returncode is None:
            await asyncio.sleep(0.02)

    d, w = asyncio.create_task(drain()), asyncio.create_task(exited())
    try:
        async with asyncio.timeout(timeout):
            await asyncio.wait({d, w}, return_when=asyncio.FIRST_COMPLETED)
            if d.done() and d.result():
                ended = "overflow"
            else:
                await w
    except TimeoutError:
        ended = "timeout"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.to_thread(_sweep, name)
        try:
            async with asyncio.timeout(2):
                await d
        except (TimeoutError, OSError):
            d.cancel()
        await w
    return dict(exit_code=proc.returncode if ended == "exit" else None, ended=ended,
                seconds=round(time.monotonic() - t0, 2), timeout_s=timeout,
                output=clip(head.decode("utf-8", "replace") + ("" if total == len(head) + len(tail) else "\n... [output omitted] ...\n")
                            + tail.decode("utf-8", "replace"), s.max_output_chars))


@function_tool
async def shell(ctx: ToolContext[RunCtx], command: str, timeout_s: int | None = None) -> str:
    """LAST RESORT: run a bash command in a working directory that holds copies of the original files
    (drawing.<ext>, drawing_2.<ext> ...). Use it only when the views cannot answer the question, never to
    re-measure what the views already give. Python with ezdxf, shapely, ifcopenshell and pypdfium2 is available.

    Args:
        command: The bash command line (pipes, heredocs and multi-line scripts are fine).
        timeout_s: Wall-clock limit in seconds; null uses the maximum allowed.
    """
    rc: RunCtx = ctx.context
    rc.calls.add(me := asyncio.current_task())
    rc.emit("tool_started", call_id=ctx.tool_call_id, tool="shell", command=command)
    r = dict(exit_code=None, ended="cancelled", seconds=0.0, output="", timeout_s=0)
    try:
        if not rc.settings.shell:
            r = dict(r, ended="refused", output="the shell is switched off on this server; answer from the views")
        elif not any(True for _ in rc.reader.log):
            r = dict(r, ended="refused", output="query the store first (tool sql): the shell is only for what the views cannot answer")
        else:
            first = not (rc.ws / ".placed").is_file()
            listing = await asyncio.to_thread(place_files, rc)
            r = await run_shell(rc, command, timeout_s)
            if first:
                r["output"] = "Files in the working directory:\n" + "\n".join(listing) + "\n\n" + r["output"]
            rc.shell_text.append(r["output"])
    except Exception as e:  # noqa: BLE001 - bash missing, a NUL byte in the command, ...
        r = dict(r, ended="failed", output=f"shell unavailable: {type(e).__name__}: {e}"[:500])
    finally:
        rc.emit("tool_finished", call_id=ctx.tool_call_id, tool="shell", exit_code=r["exit_code"],
                timed_out=r["ended"] == "timeout", ended=r["ended"], seconds=r["seconds"],
                output=clip(r["output"], rc.settings.max_event_chars))
        rc.calls.discard(me)
    status = {"exit": f"[exit code {r['exit_code']}]", "timeout": f"[timed out after {r['timeout_s']} s, killed]",
              "overflow": f"[killed: printed more than {rc.settings.max_output_bytes >> 20} MB]",
              "refused": "[refused]"}.get(r["ended"], "[failed]")
    return f"{r['output']}\n{status}" if r["output"] else status


TOOLS = [sql, view_image, shell]
