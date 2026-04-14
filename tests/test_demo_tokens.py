"""Tests for research_portal.demo_tokens."""

from __future__ import annotations

import time

import pytest

from research_portal.demo_tokens import (
    generate_token,
    is_token_valid,
    validate_token,
)

SECRET = b"test-secret-32-bytes-exactly-here"


class TestGenerate:
    def test_contains_five_parts(self):
        token = generate_token(ttl_seconds=60, secret=SECRET)
        assert token.count(".") == 4

    def test_includes_role(self):
        token = generate_token(ttl_seconds=60, role="admin", secret=SECRET)
        assert ".admin." in token

    def test_rejects_bad_ttl(self):
        with pytest.raises(ValueError):
            generate_token(ttl_seconds=0, secret=SECRET)
        with pytest.raises(ValueError):
            generate_token(ttl_seconds=-5, secret=SECRET)

    def test_rejects_overly_long_ttl(self):
        with pytest.raises(ValueError):
            generate_token(ttl_seconds=31 * 24 * 3600, secret=SECRET)

    def test_rejects_bad_role(self):
        with pytest.raises(ValueError):
            generate_token(ttl_seconds=60, role="superuser", secret=SECRET)

    def test_nonce_makes_tokens_unique(self):
        a = generate_token(ttl_seconds=60, secret=SECRET)
        b = generate_token(ttl_seconds=60, secret=SECRET)
        assert a != b


class TestValidate:
    def test_valid_token(self):
        token = generate_token(ttl_seconds=60, role="guest", secret=SECRET)
        result = validate_token(token, secret=SECRET)
        assert result is not None
        role, expires = result
        assert role == "guest"
        assert expires > time.time()

    def test_admin_role(self):
        token = generate_token(ttl_seconds=60, role="admin", secret=SECRET)
        result = validate_token(token, secret=SECRET)
        assert result is not None
        assert result[0] == "admin"

    def test_expired_token_rejected(self):
        token = generate_token(ttl_seconds=60, secret=SECRET)
        # Simulate 120 seconds elapsed
        future = time.time() + 120
        assert validate_token(token, now=future, secret=SECRET) is None

    def test_tampered_token_rejected(self):
        token = generate_token(ttl_seconds=60, secret=SECRET)
        # Flip a character in the signature portion
        parts = token.split(".")
        parts[-1] = "X" * len(parts[-1])
        tampered = ".".join(parts)
        assert validate_token(tampered, secret=SECRET) is None

    def test_wrong_secret_rejected(self):
        token = generate_token(ttl_seconds=60, secret=SECRET)
        other_secret = b"different-secret-32-bytes-xxxxxxxx"
        assert validate_token(token, secret=other_secret) is None

    def test_malformed_returns_none(self):
        for bad in ("", "not-a-token", "a.b.c", "a.b.c.d", None, 42):
            assert validate_token(bad, secret=SECRET) is None  # type: ignore[arg-type]

    def test_role_not_in_allowlist(self):
        # Construct a valid-signature token with a disallowed role by
        # forging: generate a token normally, then tamper role. The
        # signature check should fail, not the role check — this
        # verifies the signature layer.
        token = generate_token(ttl_seconds=60, role="guest", secret=SECRET)
        parts = token.split(".")
        parts[2] = "superuser"
        forged = ".".join(parts)
        assert validate_token(forged, secret=SECRET) is None


class TestIsTokenValid:
    def test_returns_bool(self):
        token = generate_token(ttl_seconds=60, secret=SECRET)
        assert is_token_valid(token, secret=SECRET) is True
        assert is_token_valid("bogus", secret=SECRET) is False
