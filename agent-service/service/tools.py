"""The agent's two tools: a shell and an image viewer, bound to one run's workspace through the SDK run context."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agents import ToolOutputImage, ToolOutputText, function_tool
from agents.tool_context import ToolContext

from .config import ROOT, Settings
from .workspace import Forbidden, NotFound, Workspace, contained, new_id

IMAGE_EXT = {".png", ".jpg", ".jpeg"}
MAX_PIXELS = 150_000_000  # refuse to decode anything larger (decompression bombs); far above any drawing render
CROP_NAME_RE = re.compile(
    r"(?:^|/)crop_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)(?:[_.]|$)",
    re.I,
)


@dataclass
class RunCtx:
    """What the SDK hands to every tool call of a run (`Runner.run(..., context=RunCtx(...))`)."""
    workspace: Workspace
    settings: Settings
    emit: Callable[..., int]   # emit(type, **fields) -> seq: append a typed event to the run log
    run_dir: Path              # service-side directory of the run (viewed images are kept in run_dir/views)
    calls: set[asyncio.Task] = field(default_factory=set)  # tool calls in flight; a cancelled run waits for them


def shell_env(settings: Settings, ws: Path, call: str, containerized: bool = False) -> dict[str, str]:
    """The complete environment of a shell call.  Built from nothing, so no API key, gateway key, token or
    other variable of the service process can reach the agent's commands.  JUNCTION_CALL names the call, so
    processes it leaves behind can be found again (see _sweep)."""
    bin_dir = "/opt/junction/bin" if containerized else str(ROOT / "bin")
    py_root = "/opt/junction" if containerized else str(ROOT)
    return {
        "JUNCTION_CALL": call,
        "PATH": f"{settings.python_bin}:{bin_dir}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONPATH": py_root,
        "HOME": str(ws), "PWD": str(ws), "TMPDIR": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "MPLBACKEND": "Agg", "MPLCONFIGDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
    }


def _argv(settings: Settings, ws: Path, command: str, name: str) -> list[str]:
    """The process to start for `command` under the configured sandbox."""
    if settings.sandbox == "none":
        return ["bash", "-c", command]
    env = shell_env(settings, ws, name, containerized=True)
    venv = settings.python_bin.parent
    py_exe = settings.python_bin / "python"
    real_py = Path(os.path.realpath(py_exe)).parent.parent  # prefix the venv was made from
    ro = {"/usr", "/etc/alternatives", "/etc/ssl", "/etc/fonts", str(venv), str(real_py)}
    cur = py_exe
    for _ in range(8):
        if not cur.is_symlink():
            break
        cur = Path(os.path.abspath(os.path.join(cur.parent, os.readlink(cur))))
        ro.add(str(cur.parent.parent))
    # docker/podman: no network, read-only root, only the workspace writable; the image is just a skeleton, the
    # host's /usr (bash, python, tesseract), the venv, and bin/areas are mounted read-only.
    argv = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:exec",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "512", "--memory", "8g",
            "-v", "/usr/bin:/bin:ro", "-v", "/usr/lib:/lib:ro", "-v", "/usr/lib64:/lib64:ro",
            "-v", f"{ROOT / 'bin'}:/opt/junction/bin:ro",
            "-v", f"{ROOT / 'areas'}:/opt/junction/areas:ro"]
    for p in sorted(ro):
        if Path(p).exists():
            argv += ["-v", f"{p}:{p}:ro"]
    for k, v in env.items():
        argv += ["-e", f"{k}={v}"]
    return argv + ["-v", f"{ws}:{ws}", "-w", str(ws), settings.docker_image, "bash", "-c", command]


def _client_env() -> dict[str, str]:
    """Environment of the `docker` / rootless `podman` client process itself (the command's environment is set with -e)."""
    keep = ("PATH", "HOME", "XDG_RUNTIME_DIR", "TMPDIR")
    return {k: v for k, v in os.environ.items() if k in keep or k.startswith(("DOCKER_", "CONTAINER_"))}


def clip(text: str, limit: int) -> str:
    """Keep the head and the tail: the start shows what ran, the end holds results and tracebacks."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - 2 * half} characters omitted] ...\n{text[-half:]}"


class Capture:
    """Head and tail of a byte stream in bounded memory, so a command that prints gigabytes costs neither
    service memory nor disk."""

    def __init__(self, keep: int, budget: int):
        self.keep, self.budget, self.total = keep, budget, 0
        self.head, self.tail = bytearray(), bytearray()

    async def drain(self, stream: asyncio.StreamReader) -> bool:
        """Read to end of stream -> False, or stop once more than `budget` bytes were printed -> True."""
        while chunk := await stream.read(1 << 16):
            self.total += len(chunk)
            room = self.keep - len(self.head)
            if room > 0:
                self.head += chunk[:room]
                chunk = chunk[room:]
            self.tail = (self.tail + chunk)[-self.keep:]
            if self.total > self.budget:
                return True
        return False

    def text(self, limit: int) -> str:
        head, tail = self.head.decode("utf-8", "replace"), self.tail.decode("utf-8", "replace")
        if self.total == len(self.head) + len(self.tail):
            return clip(head + tail, limit)
        half = limit // 2
        return f"{head[:half]}\n... [about {self.total - 2 * half} characters omitted] ...\n{tail[-half:]}"


def _sweep(call: str) -> None:
    """Sandbox mode none: kill what a command left running outside its process group (setsid, daemons).
    They are found by the call's marker in their environment, so a process that wipes its environment escapes."""
    marker = f"JUNCTION_CALL={call}\0".encode()
    for env in Path("/proc").glob("[0-9]*/environ"):
        try:
            if marker in env.read_bytes():
                os.kill(int(env.parent.name), signal.SIGKILL)
        except OSError:
            continue


async def _kill(s: Settings, proc: asyncio.subprocess.Process, name: str) -> None:
    """End everything a call started; runs after every call, whether it exited, timed out or was cancelled."""
    try:  # the command runs in its own session: kill the whole group, background children included
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if s.sandbox == "none":
        await asyncio.to_thread(_sweep, name)
    elif proc.returncode is None:  # docker: killing the client does not stop the container
        k = await asyncio.create_subprocess_exec("docker", "kill", name, stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL, env=_client_env())
        await k.wait()


async def _exited(proc: asyncio.subprocess.Process) -> None:
    """Until the command itself has exited.  Not proc.wait(): asyncio resolves that only once the output pipe
    is closed as well, and a background child can hold the pipe open for as long as it likes."""
    while proc.returncode is None:
        await asyncio.sleep(0.02)


async def run_shell(ctx: RunCtx, command: str, timeout_s: int | None) -> dict:
    """Run one command -> {exit_code, ended (exit | timeout | overflow), seconds, output, timeout_s}."""
    s, ws = ctx.settings, ctx.workspace.root.resolve()
    timeout = max(1, min(timeout_s or s.shell_timeout, s.shell_timeout))
    name = "junction-" + new_id()
    t0, ended = time.monotonic(), "exit"
    proc = await asyncio.create_subprocess_exec(
        *_argv(s, ws, command, name), cwd=ws, env=shell_env(s, ws, name) if s.sandbox == "none" else _client_env(),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True)
    cap = Capture(s.max_output_chars * 2, s.max_output_bytes)
    drain, wait = asyncio.create_task(cap.drain(proc.stdout)), asyncio.create_task(_exited(proc))
    try:
        async with asyncio.timeout(timeout):
            await asyncio.wait({drain, wait}, return_when=asyncio.FIRST_COMPLETED)
            if drain.done() and drain.result():
                ended = "overflow"
            else:  # exited (children may still hold the pipe), or closed its stdout and is still running
                await wait
    except TimeoutError:
        ended = "timeout"
    finally:
        await _kill(s, proc, name)
        try:  # the pipe is closed now: take what was still in it (after an overflow: discard it)
            async with asyncio.timeout(2):
                await drain
                while await proc.stdout.read(1 << 16):
                    pass
        except (TimeoutError, OSError):
            pass
        await wait
    return dict(exit_code=proc.returncode if ended == "exit" else None, ended=ended,
                seconds=round(time.monotonic() - t0, 2), output=cap.text(s.max_output_chars), timeout_s=timeout)


def _num_or_int(v: float) -> int | float:
    return int(v) if v.is_integer() else v


def _peek_image_size(path: Path) -> tuple[int, int] | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _read_crop_telemetry(ws: Workspace, raw_path: str, resolved: Path | None,
                         orig: tuple[int, int] | None = None,
                         shown: tuple[int, int] | None = None,
                         max_px: int | None = None) -> dict[str, Any]:
    """Extract live crop telemetry (`crop_px`, `image_px`, `scale`, `source_image`) from `crop_<x0>_<y0>_<x1>_<y1>`
    filename or `<path>.crop.json` sidecar + image headers."""
    out: dict[str, Any] = {}
    m = CROP_NAME_RE.search(raw_path.replace("\\", "/"))
    if m:
        coords = [_num_or_int(float(g)) for g in m.groups()]
        if coords[2] > coords[0] and coords[3] > coords[1]:
            out["crop_px"] = coords

    if resolved is not None:
        try:
            sidecar = contained(ws.root, str(resolved) + ".crop.json")
            if sidecar.is_file() and sidecar.stat().st_size <= 65536:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(meta, dict):
                    cpx = meta.get("crop_px")
                    if isinstance(cpx, list) and len(cpx) == 4 and all(isinstance(x, (int, float)) for x in cpx):
                        out["crop_px"] = [_num_or_int(float(x)) for x in cpx]
                    ipx = meta.get("image_px")
                    if isinstance(ipx, list) and len(ipx) == 2 and all(isinstance(x, (int, float)) for x in ipx):
                        out["image_px"] = [int(ipx[0]), int(ipx[1])]
                    if isinstance(meta.get("source_image"), str) and meta["source_image"]:
                        out["source_image"] = meta["source_image"][:200]
                    if isinstance(meta.get("scale"), (int, float)) and meta["scale"] > 0:
                        out["scale"] = float(meta["scale"])
        except Exception:
            pass

    if orig is None and resolved is not None and resolved.suffix.lower() in IMAGE_EXT:
        orig = _peek_image_size(resolved)

    if "crop_px" in out and "image_px" not in out:
        ws_root = ws.root.resolve()
        for cand_name in ("drawing.png", "drawing.jpg", "drawing.jpeg", "page1.png", "full.png", "_precomputed/plan.png"):
            cand = ws_root / cand_name
            if cand.is_file() and (resolved is None or cand.resolve() != resolved.resolve()):
                sz = _peek_image_size(cand)
                if sz:
                    out["image_px"] = [sz[0], sz[1]]
                    out.setdefault("source_image", cand_name)
                    break

    if "image_px" not in out and orig is not None:
        out["image_px"] = [orig[0], orig[1]]

    if "scale" not in out and orig is not None and orig[0] > 0:
        if shown is not None:
            out["scale"] = round(shown[0] / orig[0], 4)
        elif max_px is not None and max(orig) > max_px:
            out["scale"] = round(max_px / max(orig), 4)
        else:
            out["scale"] = 1.0
    return out


def load_image(path: Path, max_px: int) -> tuple[bytes, str, tuple[int, int], tuple[int, int]]:
    """-> (encoded bytes, mime, original size, shown size); downscaled so the long side is <= max_px."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 2  # Pillow's own hard stop; the exact limit is enforced below
    with Image.open(path) as im:
        orig = im.size
        if orig[0] * orig[1] > MAX_PIXELS:  # checked on the header, before any pixel is decoded
            raise ValueError(f"image too large: {orig[0]}x{orig[1]} px (limit {MAX_PIXELS // 10 ** 6} million pixels)")
        im.load()
        if max(orig) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        if path.suffix.lower() == ".png":
            (im if im.mode in ("1", "L", "LA", "P", "RGB", "RGBA") else im.convert("RGBA")).save(buf, "PNG", optimize=True)
            return buf.getvalue(), "image/png", orig, im.size
        im.convert("RGB").save(buf, "JPEG", quality=90)
        return buf.getvalue(), "image/jpeg", orig, im.size


@function_tool
async def shell(ctx: ToolContext[RunCtx], command: str, timeout_s: int | None = None) -> str:
    """Run a bash command in your working directory and get its combined stdout/stderr and exit code.

    Args:
        command: The bash command line (pipes, heredocs and multi-line scripts are fine).
        timeout_s: Wall-clock limit in seconds; the command and its children are killed when it expires.
            null uses the maximum allowed.
    """
    rc: RunCtx = ctx.context
    rc.calls.add(me := asyncio.current_task())
    rc.emit("tool_started", call_id=ctx.tool_call_id, tool="shell", command=command)
    r = dict(exit_code=None, ended="cancelled", seconds=0.0, output="", timeout_s=0)  # what a cancelled call reports
    try:
        r = await run_shell(rc, command, timeout_s)
    except Exception as e:  # the sandbox could not be started, a NUL byte in the command, ...
        r = dict(r, ended="failed", output=f"shell unavailable: {type(e).__name__}: {e}"[:500])
    finally:  # every tool_started gets its tool_finished
        rc.emit("tool_finished", call_id=ctx.tool_call_id, tool="shell", exit_code=r["exit_code"],
                timed_out=r["ended"] == "timeout", ended=r["ended"], seconds=r["seconds"],
                output=clip(r["output"], rc.settings.max_event_chars))
        rc.calls.discard(me)
    status = {"exit": f"[exit code {r['exit_code']}]", "timeout": f"[timed out after {r['timeout_s']} s, killed]",
              "overflow": f"[killed: printed more than {rc.settings.max_output_bytes >> 20} MB]"}.get(r["ended"], "[failed]")
    return f"{r['output']}\n{status}" if r["output"] else status


@function_tool
async def view_image(ctx: ToolContext[RunCtx], path: str) -> list[ToolOutputText | ToolOutputImage] | str:
    """Look at an image file (PNG or JPEG) in your working directory. Render PDFs, CAD and models to PNG first.

    Args:
        path: Path of the image, relative to the working directory.
    """
    rc: RunCtx = ctx.context
    rc.calls.add(me := asyncio.current_task())
    resolved_peek: Path | None = None
    rel_peek = path
    try:
        resolved_peek = rc.workspace.resolve(path)
        rel_peek = str(resolved_peek.relative_to(rc.workspace.root.resolve()))
    except Exception:
        pass
    start_telem = await asyncio.to_thread(
        _read_crop_telemetry, rc.workspace, rel_peek, resolved_peek, None, None, rc.settings.max_image_px
    )
    seq = rc.emit("tool_started", call_id=ctx.tool_call_id, tool="view_image", path=rel_peek, **start_telem)
    t0, error, extra = time.monotonic(), "cancelled", {}
    try:
        p = resolved_peek or rc.workspace.resolve(path)
        if p.suffix.lower() not in IMAGE_EXT:
            raise ValueError("not a PNG/JPEG image; render it to PNG first")
        data, mime, orig, shown = await asyncio.to_thread(load_image, p, rc.settings.max_image_px)
        # Keep exactly what the model saw: the agent reuses names like crop.png, so the path alone would show
        # a later picture.  Named by the event's seq, never by anything the model chose.
        view = rc.run_dir / "views" / f"{seq}.{'png' if mime == 'image/png' else 'jpg'}"
        view.parent.mkdir(exist_ok=True)
        view.write_bytes(data)
        rel = str(p.relative_to(rc.workspace.root.resolve()))
        telem = await asyncio.to_thread(_read_crop_telemetry, rc.workspace, rel, p, orig, shown, rc.settings.max_image_px)
        extra = dict(path=rel, view=f"/runs/{rc.run_dir.name}/views/{view.name}", width=orig[0], height=orig[1],
                     shown_width=shown[0], shown_height=shown[1], **telem)
        error = None
    except Forbidden:
        error = "refused: path is outside the working directory"
    except NotFound:
        error = f"no such file: {path}"[:300]
    except Exception as e:  # unreadable / oversized image: tell the model, do not fail the run
        error = f"cannot read image: {e}"[:300]
    finally:
        rc.emit("tool_finished", call_id=ctx.tool_call_id, tool="view_image", exit_code=1 if error else 0,
                timed_out=False, seconds=round(time.monotonic() - t0, 2), output=error or "", **extra)
        rc.calls.discard(me)
    if error:
        return f"Error: {error}"
    note = f"{rel}: {orig[0]}x{orig[1]} px"
    if shown != orig:  # evidence boxes must be in full-image pixels, so say how to convert
        note += (f", shown downscaled to {shown[0]}x{shown[1]} (multiply coordinates you read off by "
                 f"{orig[0] / shown[0]:.4f} to get full-image pixels)")
    return [ToolOutputText(text=note),
            ToolOutputImage(image_url=f"data:{mime};base64,{base64.b64encode(data).decode()}", detail="high")]


TOOLS = [shell, view_image]
