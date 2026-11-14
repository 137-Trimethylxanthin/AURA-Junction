"""Client identity: IP from `request.client.host` only (modal-facts.md); X-Forwarded-For is
ignored. The raw IP never leaves memory; logs and the DB get a salted hash."""

from __future__ import annotations

import hmac

from starlette.requests import Request

from aura.budget import ip_hash
from aura.config import Settings


def client_ip(request: Request) -> str | None:
    """IPv4 as is; IPv6 bucketed by /64 (one subscriber's prefix), so rotating addresses inside
    it does not reset the per-IP limits (L6)."""
    host = request.client.host if request.client else None
    if host and ":" in host:
        import ipaddress

        try:
            return str(ipaddress.ip_network(f"{host}/64", strict=False))
        except ValueError:
            return host
    return host


def client_ip_hash(request: Request, settings: Settings) -> str | None:
    return ip_hash(client_ip(request), settings.ip_salt.get_secret_value())


def is_presenter(request: Request, settings: Settings) -> bool:
    token = settings.presenter_token.get_secret_value() if settings.presenter_token else ""
    given = request.headers.get("x-aura-presenter", "")
    return bool(token) and bool(given) and hmac.compare_digest(given.encode(), token.encode())
