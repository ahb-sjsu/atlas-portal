"""Tests for research_portal.redaction."""

from __future__ import annotations

from research_portal.redaction import (
    MAX_PREVIEW_CHARS,
    add_preview_keys,
    add_sensitive_keys,
    redact_payload,
)


class TestSensitiveKeys:
    def test_api_key_redacted(self):
        result = redact_payload({"api_key": "sk-1234567890"})
        assert result == {"api_key": "<redacted>"}

    def test_case_insensitive(self):
        result = redact_payload({"API_KEY": "sk-xxx", "Authorization": "Bearer y"})
        assert result == {"API_KEY": "<redacted>", "Authorization": "<redacted>"}

    def test_other_keys_preserved(self):
        result = redact_payload({"api_key": "sk-xxx", "user_id": 42})
        assert result["user_id"] == 42

    def test_nested_dicts(self):
        result = redact_payload({"headers": {"authorization": "Bearer secret"}})
        assert result == {"headers": {"authorization": "<redacted>"}}


class TestPreviewKeys:
    def test_email_scrubbed(self):
        result = redact_payload({"query": "email me at alice@example.com please"})
        assert "alice@example.com" not in result["query"]
        assert "<email>" in result["query"]

    def test_phone_scrubbed(self):
        # NANP exchange codes start with 2-9 so "555-234-5678" is our
        # test number (555-123-4567 uses 123 which isn't a valid exchange).
        result = redact_payload({"query": "call (555) 234-5678 for info"})
        assert "555" not in result["query"] or "<phone>" in result["query"]
        assert "<phone>" in result["query"]

    def test_ssn_scrubbed(self):
        result = redact_payload({"text": "SSN: 123-45-6789 on file"})
        assert "123-45-6789" not in result["text"]
        assert "<ssn>" in result["text"]

    def test_truncation(self):
        long_text = "a" * 1000
        result = redact_payload({"query": long_text})
        assert len(result["query"]) <= MAX_PREVIEW_CHARS

    def test_non_preview_key_not_truncated(self):
        # A 1000-char string under a non-preview key should pass through unchanged.
        long_value = "b" * 1000
        result = redact_payload({"metadata": long_value})
        assert result["metadata"] == long_value


class TestNonDict:
    def test_non_dict_passes_through(self):
        assert redact_payload("just a string") == "just a string"  # type: ignore[arg-type]
        assert redact_payload(42) == 42  # type: ignore[arg-type]

    def test_list_of_dicts(self):
        result = redact_payload({"items": [{"api_key": "x"}, {"api_key": "y"}, "plain"]})
        assert result["items"][0] == {"api_key": "<redacted>"}
        assert result["items"][1] == {"api_key": "<redacted>"}
        assert result["items"][2] == "plain"


class TestRegistration:
    def test_add_sensitive_keys(self):
        add_sensitive_keys("custom_secret")
        try:
            result = redact_payload({"custom_secret": "shh"})
            assert result == {"custom_secret": "<redacted>"}
        finally:
            # Note: no unregister; module-global state. OK for tests
            # because names don't collide with real production keys.
            pass

    def test_add_preview_keys(self):
        add_preview_keys("custom_text")
        try:
            result = redact_payload({"custom_text": "alice@example.com"})
            assert "<email>" in result["custom_text"]
        finally:
            pass
