"""Time-limited HMAC-signed demo links.

Goal: let an admin share a read-only dashboard URL with someone
outside Tailscale / the Basic auth perimeter, without provisioning a
real account or weakening the primary auth.

The token is an HMAC-SHA256 of ``"{expires_ts}.{role}.{nonce}"`` using
a per-deployment secret. It lives in the URL as ``?demo=<token>`` and
is validated on each request; if valid and unexpired, the request
proceeds with the token's assigned role (default: ``guest``).

Security notes
--------------

- Tokens are *bearer* credentials. Anyone with the URL can view.
- Role is encoded in the token and signed; a viewer can't escalate.
- Expiry is encoded in the token and signed.
- Tokens can be revoked by rotating the deployment secret (all existing
  tokens become invalid).
- Clock skew > ~30 s between admin's machine and the server will
  cause false expirations; admins using the generator below will have
  tokens that match the server's clock because they call it on the
  server itself.
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
import time
from hashlib import sha256

TOKEN_VERSION = "v1"
DEFAULT_ROLE = "guest"

# Allowed roles; extend in your application if needed.
_VALID_ROLES = frozenset({"guest", "admin"})


def _get_secret() -> bytes:
    """Read the signing secret from the environment.

    Lazily computed so apps can set the env var after import. If not
    set, a process-local random secret is used — fine for dev, but
    tokens won't survive restart.
    """
    raw = os.environ.get("PORTAL_DEMO_SECRET")
    if raw:
        return raw.encode("utf-8")
    # Dev fallback: random process-local secret. Documented behavior.
    return _process_secret()


_PROC_SECRET: bytes | None = None


def _process_secret() -> bytes:
    global _PROC_SECRET
    if _PROC_SECRET is None:
        _PROC_SECRET = secrets.token_bytes(32)
    return _PROC_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def generate_token(
    *,
    ttl_seconds: int = 3600,
    role: str = DEFAULT_ROLE,
    secret: bytes | None = None,
) -> str:
    """Create a new demo token valid for ``ttl_seconds``.

    Parameters
    ----------
    ttl_seconds:
        How long the token is valid. Default: 1 hour. Must be > 0 and
        ≤ 30 days.
    role:
        The role the token grants. Must be in ``_VALID_ROLES``.
    secret:
        Override the signing secret (tests only).
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be > 0")
    if ttl_seconds > 30 * 24 * 3600:
        raise ValueError("ttl_seconds must be <= 30 days")
    if role not in _VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")

    key = secret or _get_secret()
    expires = int(time.time()) + ttl_seconds
    nonce = _b64(secrets.token_bytes(6))
    body = f"{TOKEN_VERSION}.{expires}.{role}.{nonce}"
    mac = hmac.new(key, body.encode("utf-8"), sha256).digest()
    sig = _b64(mac)
    return f"{body}.{sig}"


def validate_token(
    token: str,
    *,
    now: float | None = None,
    secret: bytes | None = None,
) -> tuple[str, int] | None:
    """Return ``(role, expires_ts)`` if valid; else None.

    Never raises. Constant-time comparison for the HMAC.
    """
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 5:
        return None
    version, expires_s, role, nonce, sig = parts
    if version != TOKEN_VERSION:
        return None
    if role not in _VALID_ROLES:
        return None
    try:
        expires = int(expires_s)
    except ValueError:
        return None

    key = secret or _get_secret()
    body = f"{version}.{expires_s}.{role}.{nonce}"
    expected_mac = hmac.new(key, body.encode("utf-8"), sha256).digest()
    try:
        provided_mac = _unb64(sig)
    except Exception:
        return None
    if not hmac.compare_digest(expected_mac, provided_mac):
        return None

    current = now if now is not None else time.time()
    if current >= expires:
        return None
    return role, expires


def is_token_valid(token: str, *, now: float | None = None, secret: bytes | None = None) -> bool:
    """Convenience: bool version of :func:`validate_token`."""
    return validate_token(token, now=now, secret=secret) is not None


__all__ = [
    "DEFAULT_ROLE",
    "TOKEN_VERSION",
    "generate_token",
    "is_token_valid",
    "validate_token",
]
