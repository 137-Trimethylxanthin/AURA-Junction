"""Service settings, read from the environment once at app start-up."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..pipeline import store_dir

STORE_ROOT = Path(__file__).resolve().parents[2]          # the store folder (holds prompts/)
ALLOWED_EXT = frozenset({"ifc", "dxf", "dwg", "pdf", "png", "jpg", "jpeg", "svg"})


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    model: str = "gemini-4-hydrogen"  # "demo" = scripted model, no key needed
    openai_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    api_key: str | None = field(default=None, repr=False)
    reasoning_effort: str = "low"     # Gemini thinks 25-80 s per turn at its default; low answers in seconds
    api_token: str | None = field(default=None, repr=False)   # bearer token; auth is off when unset
    data_dir: Path = field(default_factory=store_dir)         # uploads/, runs/, page renders
    dataset_dir: Path = Path("/data/dataset")                  # POST /files/register only reaches inside it
    max_turns: int = 30
    max_upload_mb: int = 500
    max_concurrent: int = 4           # agent runs executing at the same time
    ingest_workers: int = 2           # worker processes parsing uploads
    ingest_wait_s: int = 600          # how long a run waits for its files to finish ingesting
    sql_max_rows: int = 200           # rows of one sql result handed to the model
    sql_max_chars: int = 16_000
    max_image_px: int = 1568          # long side of images handed to the model
    shell: bool = True                # the last-resort shell tool
    shell_timeout: int = 300
    max_output_chars: int = 12_000    # shell output returned to the model per call (head + tail)
    max_output_bytes: int = 64 << 20  # a command that prints more than this is killed
    max_event_chars: int = 4_000      # tool output kept per call in the event log
    max_evidence: int = 60            # evidence regions attached to one answer
    apply_schema: bool = True         # create / update tables, views and the reader role at start-up
    cors_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"   # browser origins that may call the API

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            model=_env("JSTORE_MODEL", d.model),
            openai_base_url=_env("JSTORE_OPENAI_BASE_URL", d.openai_base_url).rstrip("/"),
            api_key=_env("GEMINI_API_KEY", "") or _env("JSTORE_OPENAI_API_KEY", "") or None,
            reasoning_effort=_env("JSTORE_REASONING_EFFORT", d.reasoning_effort).lower(),
            api_token=os.environ.get("JSTORE_API_TOKEN") or None,
            data_dir=store_dir().resolve(),
            dataset_dir=Path(_env("JSTORE_DATASET_DIR", str(d.dataset_dir))).resolve(),
            max_turns=int(_env("JSTORE_MAX_TURNS", str(d.max_turns))),
            max_upload_mb=int(_env("JSTORE_MAX_UPLOAD_MB", str(d.max_upload_mb))),
            max_concurrent=int(_env("JSTORE_MAX_CONCURRENT", str(d.max_concurrent))),
            ingest_workers=int(_env("JSTORE_INGEST_WORKERS", str(d.ingest_workers))),
            sql_max_rows=int(_env("JSTORE_SQL_MAX_ROWS", str(d.sql_max_rows))),
            shell=_env("JSTORE_SHELL", "on").lower() not in ("off", "0", "false", "no"),
            apply_schema=_env("JSTORE_APPLY_SCHEMA", "1").lower() not in ("off", "0", "false", "no"),
            shell_timeout=int(_env("JSTORE_SHELL_TIMEOUT", str(d.shell_timeout))),
            cors_origin_regex=_env("JSTORE_CORS_ORIGINS", d.cors_origin_regex),
        )
