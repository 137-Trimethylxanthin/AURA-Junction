"""Settings (pydantic-settings). Secrets are SecretStr and never logged.

The kill switch AURA_MODELS is deliberately NOT cached here: `models_enabled()` reads the
environment on every call, so flipping it takes effect on the next request.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


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
    # Cloudflare Pages production + preview subdomains.
    allowed_origin_regex: str = Field(
        default=r"^https://([a-z0-9-]+\.)?aura[a-z0-9-]*\.pages\.dev$",
        alias="ALLOWED_ORIGIN_REGEX",
    )

    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    presenter_token: SecretStr | None = Field(default=None, alias="AURA_PRESENTER_TOKEN")
    ip_salt: SecretStr = Field(default=SecretStr("aura-local-salt"), alias="AURA_IP_SALT")

    # Budget (plan: "Gemini request budget").
    event_cap: int = Field(default=1000, alias="AURA_EVENT_CAP")
    event_seed: int = Field(default=94, alias="AURA_BUDGET_SEED")
    daily_cap: int = Field(default=350, alias="AURA_DAILY_CAP")
    dev_cap: int = Field(default=100, alias="AURA_DEV_CAP")
    measurement_cap: int = Field(default=120, alias="AURA_MEASUREMENT_CAP")
    ip_agent_per_hour: int = Field(default=40, alias="AURA_IP_AGENT_PER_HOUR")
    raster_public_cap: int = Field(default=0, alias="AURA_RASTER_PUBLIC_CAP")
    budget_tz: str = Field(default="Europe/Helsinki", alias="AURA_BUDGET_TZ")

    decision_model: str = Field(default="gemini-decision-1", alias="AURA_DECISION_MODEL")
    agent_model: str = Field(default="gemini-3.1-flash-lite", alias="AURA_AGENT_MODEL")

    turn_deadline_s: float = Field(default=60.0, alias="AURA_TURN_DEADLINE_S")
    volume_commit_every_s: float = Field(default=60.0, alias="AURA_VOLUME_COMMIT_S")
    cleanup_every_s: float = Field(default=3600.0, alias="AURA_CLEANUP_S")
    upload_ttl_s: float = Field(default=24 * 3600.0, alias="AURA_UPLOAD_TTL_S")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, v: object) -> object:
        return _split_csv(v)

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
    """Kill switch, read from the environment on every call."""
    return os.environ.get("AURA_MODELS", "on").strip().lower() not in {"off", "0", "false", "no"}


def live_allowed() -> bool:
    return os.environ.get("AURA_LIVE", "") == "1"
