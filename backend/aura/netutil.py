"""Client identity: IP from `request.client.host` only (modal-facts.md); X-Forwarded-For is
ignored. The raw IP never leaves memory; logs and the DB get a salted hash."""

from __future__ import annotations

import hmac

from starlette.requests import Request

from aura.budget import ip_hash
from aura.config import Settings


def ip_bucket(host: str | None) -> str | None:
    """The rate-limit bucket of a client address. IPv4 as is; an IPv4-mapped IPv6 address
    (::ffff:a.b.c.d) as its IPv4 address (same client, same bucket); a real IPv6 address by its
    /64 (one subscriber's prefix), so rotating inside it does not reset the limits (L6).
    Anything unparseable shares one conservative bucket."""
    if not host:
        return None
    import ipaddress

    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return "invalid"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(request: Request) -> str | None:
    return ip_bucket(request.client.host if request.client else None)


def client_ip_hash(request: Request, settings: Settings) -> str | None:
    return ip_hash(client_ip(request), settings.ip_salt.get_secret_value())


def is_presenter(request: Request, settings: Settings) -> bool:
    token = settings.presenter_token.get_secret_value() if settings.presenter_token else ""
    given = request.headers.get("x-aura-presenter", "")
    return bool(token) and bool(given) and hmac.compare_digest(given.encode(), token.encode())
