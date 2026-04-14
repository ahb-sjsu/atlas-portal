"""Tests for the Atlas Portal Flask factory and routes."""

from __future__ import annotations

import pytest

from atlas_portal.app import build_app
from research_portal.demo_tokens import generate_token


@pytest.fixture
def app():
    # no_auth + no heartbeat for isolated tests
    return build_app(no_auth=True, start_heartbeat=False)


@pytest.fixture
def client(app):
    return app.test_client()


class TestPages:
    def test_cortex_renders(self, client):
        rv = client.get("/")
        assert rv.status_code == 200
        assert b"Cortex" in rv.data or b"cortex" in rv.data
        assert b"ATLAS" in rv.data  # brand name in header

    def test_council_renders(self, client):
        rv = client.get("/council")
        assert rv.status_code == 200
        assert b"Divine Council" in rv.data

    def test_platform_renders(self, client):
        rv = client.get("/platform")
        assert rv.status_code == 200
        assert b"Platform" in rv.data

    def test_archive_renders(self, client):
        rv = client.get("/archive")
        assert rv.status_code == 200
        assert b"Archive" in rv.data

    def test_chat_renders(self, client):
        rv = client.get("/chat")
        assert rv.status_code == 200

    def test_healthz(self, client):
        rv = client.get("/healthz")
        assert rv.status_code == 200
        assert rv.json == {"status": "ok"}


class TestApi:
    def test_status_returns_expected_keys(self, client):
        rv = client.get("/api/status")
        assert rv.status_code == 200
        data = rv.get_json()
        for key in ("cpu_temps", "gpu", "memory", "load", "disk", "raid", "sessions"):
            assert key in data

    def test_events_snapshot(self, client):
        rv = client.get("/api/events/trinity/snapshot")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "events" in data
        assert "latest_seq" in data


class TestAuth:
    def test_no_auth_allows_access(self):
        app = build_app(no_auth=True, start_heartbeat=False)
        rv = app.test_client().get("/")
        assert rv.status_code == 200

    def test_basic_auth_required_when_enabled(self, monkeypatch):
        monkeypatch.setenv("PORTAL_USER", "admin")
        monkeypatch.setenv("PORTAL_PASS", "secret")
        app = build_app(no_auth=False, start_heartbeat=False)
        client = app.test_client()

        rv = client.get("/")
        assert rv.status_code == 401
        assert "WWW-Authenticate" in rv.headers

    def test_basic_auth_succeeds(self, monkeypatch):
        monkeypatch.setenv("PORTAL_USER", "admin")
        monkeypatch.setenv("PORTAL_PASS", "secret")
        app = build_app(no_auth=False, start_heartbeat=False)
        client = app.test_client()

        # Base64 "admin:secret"
        import base64

        creds = base64.b64encode(b"admin:secret").decode()
        rv = client.get("/", headers={"Authorization": f"Basic {creds}"})
        assert rv.status_code == 200

    def test_demo_token_succeeds(self, monkeypatch):
        monkeypatch.setenv("PORTAL_DEMO_SECRET", "test-secret-bytes-here-32-pls-xx")
        monkeypatch.setenv("PORTAL_USER", "admin")
        monkeypatch.setenv("PORTAL_PASS", "secret")
        app = build_app(no_auth=False, start_heartbeat=False)
        client = app.test_client()

        token = generate_token(ttl_seconds=300, role="guest")
        rv = client.get(f"/?demo={token}")
        assert rv.status_code == 200

    def test_invalid_demo_token_rejected(self, monkeypatch):
        monkeypatch.setenv("PORTAL_DEMO_SECRET", "test-secret-bytes-here-32-pls-xx")
        monkeypatch.setenv("PORTAL_USER", "admin")
        monkeypatch.setenv("PORTAL_PASS", "secret")
        app = build_app(no_auth=False, start_heartbeat=False)
        client = app.test_client()

        rv = client.get("/?demo=not-a-real-token")
        assert rv.status_code == 401


class TestPitchMode:
    def test_default_off(self, client):
        rv = client.get("/")
        # Class shouldn't be set
        assert b'class="pitch"' not in rv.data
        # The toggle shows "Pitch Mode" (entry label)
        assert b"Pitch Mode" in rv.data

    def test_url_override_turns_on(self, client):
        rv = client.get("/?pitch=1")
        assert rv.status_code == 200
        assert b"pitch" in rv.data

    def test_toggle_sets_cookie(self, client):
        rv = client.post("/mode/pitch/on", follow_redirects=False)
        assert rv.status_code == 302
        assert "atlas_pitch" in rv.headers.get("Set-Cookie", "")

    def test_toggle_off_clears_cookie(self, client):
        rv = client.post("/mode/pitch/off", follow_redirects=False)
        assert rv.status_code == 302


class TestStatic:
    def test_css_served(self, client):
        rv = client.get("/static/css/atlas.css")
        assert rv.status_code == 200
        assert b"Atlas Portal" in rv.data

    def test_js_served(self, client):
        rv = client.get("/static/js/sse.js")
        assert rv.status_code == 200
        assert b"AtlasSSE" in rv.data


class TestSecurityHeaders:
    def test_headers_present(self, client):
        rv = client.get("/")
        assert rv.headers.get("X-Content-Type-Options") == "nosniff"
        assert rv.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert "Content-Security-Policy" in rv.headers
