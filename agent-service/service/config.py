"""Service settings, read from the environment once at app start-up."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_EXT = frozenset({"ifc", "dxf", "pdf", "png", "jpg", "jpeg", "svg"})
SANDBOX_MODES = ("none", "bwrap", "docker")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    model: str = ""                   # "" -> the SDK's default model (see Settings.from_env)
    bifrost_url: str | None = None    # Bifrost gateway root, e.g. http://localhost:8080; all model traffic goes there
    bifrost_api_key: str | None = field(default=None, repr=False)  # gateway virtual key, sent as the bearer token
    # Any other OpenAI-compatible endpoint (e.g. Gemini: https://generativelanguage.googleapis.com/v1beta/openai/)
    openai_base_url: str | None = None
    openai_api_key: str | None = field(default=None, repr=False)
    api_style: str = "responses"      # "responses" | "chat" (Chat Completions; default for a custom base URL)
    reasoning_effort: str | None = "low"  # OpenAI/Gemini reasoning_effort ("low" | "medium" | "high" | None)
    data_dir: Path = ROOT / ".cache" / "service"
    register_root: Path = ROOT / "dataset" / "junction-dataset"
    max_turns: int = 150
    shell_timeout: int = 600          # hard ceiling for one shell call, seconds
    max_upload_mb: int = 500
    max_concurrent: int = 4           # agent runs executing at the same time
    max_image_px: int = 1568          # long side of images handed to the model
    max_output_chars: int = 20_000    # shell output returned to the model per call (head + tail)
    max_output_bytes: int = 64 << 20  # a command that prints more than this is killed
    max_event_chars: int = 4_000      # shell output kept per call in the event log (head + tail)
    api_token: str | None = field(default=None, repr=False)  # bearer token; auth is off when unset
    sandbox: str = "none"
    docker_image: str = "alpine:3.23"  # only a rootfs skeleton: the host's /usr and the venv are mounted read-only
    tracing: bool = False             # SDK tracing uploads run data to OpenAI: off unless asked for
    python_bin: Path = field(default_factory=lambda: Path(sys.executable).parent)

    @classmethod
    def from_env(cls) -> "Settings":
        from agents.models.default_models import get_default_model
        d = cls()
        sandbox = _env("JUNCTION_SANDBOX", d.sandbox).lower()
        if sandbox not in SANDBOX_MODES:
            raise ValueError(f"JUNCTION_SANDBOX must be one of {SANDBOX_MODES}, got {sandbox!r}")
        raw_re = os.environ.get("JUNCTION_REASONING_EFFORT")
        reasoning_effort = d.reasoning_effort if raw_re is None else (raw_re.strip().lower() or None)
        if reasoning_effort == "off":
            reasoning_effort = None
        return cls(
            model=_env("JUNCTION_MODEL", get_default_model()),
            bifrost_url=_env("JUNCTION_BIFROST_URL", "").rstrip("/") or None,
            bifrost_api_key=_env("JUNCTION_BIFROST_API_KEY", "") or None,
            openai_base_url=_env("JUNCTION_OPENAI_BASE_URL", "").rstrip("/") or None,
            openai_api_key=_env("JUNCTION_OPENAI_API_KEY", "") or None,
            api_style=_env("JUNCTION_API_STYLE", "chat" if _env("JUNCTION_OPENAI_BASE_URL", "") else "responses").lower(),
            reasoning_effort=reasoning_effort,
            data_dir=Path(_env("JUNCTION_DATA_DIR", str(d.data_dir))).resolve(),
            register_root=Path(_env("JUNCTION_REGISTER_ROOT", str(d.register_root))).resolve(),
            max_turns=int(_env("JUNCTION_MAX_TURNS", str(d.max_turns))),
            shell_timeout=int(_env("JUNCTION_SHELL_TIMEOUT", str(d.shell_timeout))),
            max_upload_mb=int(_env("JUNCTION_MAX_UPLOAD_MB", str(d.max_upload_mb))),
            max_concurrent=int(_env("JUNCTION_MAX_CONCURRENT", str(d.max_concurrent))),
            max_image_px=int(_env("JUNCTION_MAX_IMAGE_PX", str(d.max_image_px))),
            api_token=os.environ.get("JUNCTION_API_TOKEN") or None,
            sandbox=sandbox,
            docker_image=_env("JUNCTION_DOCKER_IMAGE", d.docker_image),
            tracing=_env("JUNCTION_TRACING", "0") == "1",
        )

    @property
    def bifrost_api_base(self) -> str | None:
        """Base URL for the OpenAI client: Bifrost serves the OpenAI API (Responses included) under /openai/v1.
        A URL that already ends in /v1 is taken as that base path as given."""
        u = self.bifrost_url
        return None if u is None else u if u.endswith("/v1") else u + "/openai/v1"


def docker_client_env() -> dict[str, str]:
    """Minimal environment for the `docker` / rootless `podman` CLI without leaking service secrets."""
    keep = ("PATH", "HOME", "XDG_RUNTIME_DIR", "TMPDIR")
    env = {k: v for k, v in os.environ.items() if k in keep or k.startswith(("DOCKER_", "CONTAINER_"))}
    if "XDG_RUNTIME_DIR" not in env and hasattr(os, "getuid"):
        rt = Path(f"/run/user/{os.getuid()}")
        try:
            if rt.is_dir() and rt.stat().st_uid == os.getuid():
                env["XDG_RUNTIME_DIR"] = str(rt)
        except OSError:
            pass
    return env


def sandbox_available(mode: str, docker_image: str = Settings.docker_image) -> tuple[bool, str]:
    """Can this machine actually run the given sandbox mode? -> (ok, human-readable detail)."""
    if mode == "none":
        return True, "no isolation: commands run as the service user"
    if mode == "bwrap":  # accepted as a name, but this build only implements what could be tested here
        return False, "bwrap mode is not implemented (bubblewrap is not installed on this machine); use docker"
    if mode == "docker":
        exe = shutil.which("docker")
        if not exe:
            return False, "docker is not installed"
        try:
            r = subprocess.run([exe, "image", "inspect", docker_image], capture_output=True, timeout=20,
                               env=docker_client_env())
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"docker failed: {e}"
        return (True, f"{exe}, image {docker_image}") if r.returncode == 0 else (
            False, f"docker daemon unreachable or image {docker_image} not pulled")
    return False, f"unknown sandbox mode {mode!r}"


async def model_status(s: Settings) -> dict:
    """Which model, through what, and is it usable?  Never returns a key.

    Direct: is OPENAI_API_KEY present.  Bifrost: is the gateway up (GET /health) and does it list the model
    (GET <api base>/models with the gateway key; `model_known` is None when the list cannot be read)."""
    if s.openai_base_url:
        out = dict(name=s.model, provider="openai-compatible", api_style=s.api_style,
                   key_present=s.openai_api_key is not None,
                   endpoint=dict(url=re.sub(r"//[^/@]*@", "//", s.openai_base_url), reachable=False, model_known=None))
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(s.openai_base_url + "/models", headers={"Authorization": f"Bearer {s.openai_api_key}"})
                out["endpoint"]["reachable"] = r.status_code < 500
                if r.status_code == 200:
                    ids = {str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data") or []}
                    out["endpoint"]["model_known"] = s.model.removeprefix("models/") in ids
        except (httpx.HTTPError, ValueError):
            pass
        return out
    if s.bifrost_url is None:
        present = bool(os.environ.get("OPENAI_API_KEY"))
        return dict(name=s.model, provider="openai", key_present=present, missing=[] if present else ["OPENAI_API_KEY"])
    root = re.sub(r"(/openai)?/v1$", "", s.bifrost_url)
    shown = re.sub(r"//[^/@]*@", "//", s.bifrost_url)  # no userinfo in the report
    out = dict(name=s.model, provider="bifrost", key_present=s.bifrost_api_key is not None,
               gateway=dict(url=shown, reachable=False, model_known=None))
    headers = {"Authorization": f"Bearer {s.bifrost_api_key}"} if s.bifrost_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            out["gateway"]["reachable"] = (await c.get(root + "/health")).status_code == 200
            r = await c.get(f"{s.bifrost_api_base}/models", headers=headers)
            if r.status_code == 200:
                ids = {m.get("id") for m in r.json().get("data") or []}
                # the gateway lists "<provider>/<model>"; a bare name is routed to its default provider
                out["gateway"]["model_known"] = s.model in ids or any(i and i.split("/", 1)[-1] == s.model for i in ids)
    except (httpx.HTTPError, ValueError):
        pass
    return out
