"""Settings (pydantic-settings). Secrets are SecretStr and never logged.

The kill switch AURA_MODELS is deliberately NOT cached here: `models_enabled()` reads the
environment on every call, so flipping it takes effect on the next request.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


# The caps AURA_UNLIMITED lifts (each also accepts 0 = unlimited on its own).
UNLIMITED_CAPS = ("event_cap", "daily_cap", "dev_cap", "measurement_cap", "ip_agent_per_hour",
                  "agent_runs_per_ip_hour", "agent_runs_per_day", "uploads_per_ip_hour",
                  "max_queued_uploads", "upload_quota_bytes")


def _split_csv(v: object) -> object:
    """ALLOWED_ORIGINS: a comma-separated string or a JSON list."""
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            return [str(x).strip() for x in json.loads(t) if str(x).strip()]
        return [s.strip() for s in t.split(",") if s.strip()]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore", populate_by_name=True)

    data_dir: Path = Field(default=BACKEND_DIR / ".data", alias="DATA_DIR")
    # Preprocessed drawings (L1's build output). DATA_DIR/drawings is searched first.
    build_dir: Path = Field(default=BACKEND_DIR / "build" / "drawings", alias="AURA_BUILD_DIR")

    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        alias="ALLOWED_ORIGINS",
    )
    # Cloudflare Pages production + preview subdomains of ONE pinned project (M2: a prefix match
    # let anyone's "aura-…" project in). Set ALLOWED_ORIGIN_REGEX if the project is named otherwise.
    allowed_origin_regex: str = Field(
        default=r"^https://([a-z0-9-]+\.)?aura-j26\.pages\.dev$",
        alias="ALLOWED_ORIGIN_REGEX",
    )

    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    presenter_token: SecretStr | None = Field(default=None, alias="AURA_PRESENTER_TOKEN")
    ip_salt: SecretStr = Field(default=SecretStr("aura-local-salt"), alias="AURA_IP_SALT")

    # Budget (plan: "Gemini request budget"). Bounded by default; a cap set to 0 is unlimited, and
    # AURA_UNLIMITED=true lifts every request/upload cap at once (local dev and demo only:
    # scripts/dev-backend.sh sets it). Spend is always counted (/budget, /health).
    unlimited: bool = Field(default=False, alias="AURA_UNLIMITED")
    event_cap: int = Field(default=1000, alias="AURA_EVENT_CAP")
    event_seed: int = Field(default=94, alias="AURA_BUDGET_SEED")
    daily_cap: int = Field(default=350, alias="AURA_DAILY_CAP")
    dev_cap: int = Field(default=100, alias="AURA_DEV_CAP")
    measurement_cap: int = Field(default=120, alias="AURA_MEASUREMENT_CAP")
    ip_agent_per_hour: int = Field(default=40, alias="AURA_IP_AGENT_PER_HOUR")
    # Raster analyses without the presenter token: 0 = presenter only, N > 0 = N public analyses
    # (AURA_UNLIMITED opens them to everyone).
    raster_public_cap: int = Field(default=0, alias="AURA_RASTER_PUBLIC_CAP")
    budget_tz: str = Field(default="Europe/Helsinki", alias="AURA_BUDGET_TZ")

    decision_model: str = Field(default="gemini-decision-1", alias="AURA_DECISION_MODEL")
    agent_model: str = Field(default="gemini-3.1-flash-lite", alias="AURA_AGENT_MODEL")

    answerer: Literal["hybrid", "agent", "exact"] = Field(
        default="hybrid", alias="AURA_ANSWERER"
    )
    sidecar_url: str | None = Field(default=None, alias="AURA_SIDECAR_URL")
    sidecar_token_file: Path | None = Field(default=None, alias="AURA_SIDECAR_TOKEN_FILE")
    sidecar_model: str = Field(default="gemini-3.8-flash", alias="AURA_SIDECAR_MODEL")
    agent_runs_per_ip_hour: int = Field(default=3, alias="AURA_AGENT_RUNS_PER_IP_HOUR")
    agent_runs_per_day: int = Field(default=15, alias="AURA_AGENT_RUNS_PER_DAY")

    turn_deadline_s: float = Field(default=120.0, alias="AURA_TURN_DEADLINE_S")
    # dev: every answering stage in the trace (grab, decision, filter, answer) with its detail;
    # demo: the short trace (decision and answer nodes only, no detail payloads).
    trace_detail: Literal["dev", "demo"] = Field(default="dev", alias="AURA_TRACE_DETAIL")
    # /chat turns routed to the sidecar agent (SSE heartbeats keep the client alive meanwhile).
    agent_deadline_s: float = Field(default=150.0, alias="AURA_AGENT_DEADLINE_S")
    volume_commit_every_s: float = Field(default=60.0, alias="AURA_VOLUME_COMMIT_S")
    cleanup_every_s: float = Field(default=3600.0, alias="AURA_CLEANUP_S")
    upload_ttl_s: float = Field(default=24 * 3600.0, alias="AURA_UPLOAD_TTL_S")
    # Upload abuse limits (checked before the body is read). 0 = unlimited.
    uploads_per_ip_hour: int = Field(default=10, alias="AURA_UPLOADS_PER_IP_HOUR")
    max_queued_uploads: int = Field(default=20, alias="AURA_MAX_QUEUED_UPLOADS")
    upload_quota_bytes: int = Field(default=2 * 1024**3, alias="AURA_UPLOAD_QUOTA_BYTES")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, v: object) -> object:
        return _split_csv(v)

    @model_validator(mode="after")
    def _unlimited(self) -> Settings:
        """AURA_UNLIMITED: every request/upload cap becomes 0 (= unlimited); counting goes on."""
        if self.unlimited:
            for name in UNLIMITED_CAPS:
                object.__setattr__(self, name, 0)
        return self

    @property
    def db_path(self) -> Path:
        return self.data_dir / "aura.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def drawing_dirs(self) -> list[Path]:
        return [self.data_dir / "drawings", self.build_dir]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def models_enabled() -> bool:
    """Kill switch, read on every call: AURA_MODELS=off, or a file MODELS_OFF in DATA_DIR (H3e:
    on Modal the environment is fixed at start, but a file on the Volume can be created live)."""
    if os.environ.get("AURA_MODELS", "on").strip().lower() in {"off", "0", "false", "no"}:
        return False
    return not kill_file().exists()


def kill_file() -> Path:
    """The live kill switch (POST /admin/models writes it; on Modal it lives on the Volume)."""
    return Path(os.environ.get("DATA_DIR") or BACKEND_DIR / ".data") / "MODELS_OFF"


def live_allowed() -> bool:
    return os.environ.get("AURA_LIVE", "") == "1"
