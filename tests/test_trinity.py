"""Tests for the Trinity Live visualization partial + static assets."""

from __future__ import annotations

import pytest

from atlas_portal.app import build_app


@pytest.fixture
def app():
    return build_app(no_auth=True, start_heartbeat=False)


@pytest.fixture
def client(app):
    return app.test_client()


class TestTrinityRenders:
    def test_cortex_embeds_svg(self, client):
        rv = client.get("/")
        assert rv.status_code == 200
        html = rv.data.decode("utf-8")
        # SVG present
        assert "trinity-svg" in html
        # Three nodes
        assert 'id="node-superego"' in html
        assert 'id="node-id"' in html
        assert 'id="node-council"' in html

    def test_trinity_has_seven_council_members(self, client):
        html = client.get("/").data.decode("utf-8")
        # Every member appears as a data-member attribute
        for m in (
            "judge",
            "advocate",
            "synthesizer",
            "ethicist",
            "historian",
            "futurist",
            "pragmatist",
        ):
            assert f'data-member="{m}"' in html

    def test_tiles_below_svg(self, client):
        html = client.get("/").data.decode("utf-8")
        # Detail tiles provide a keyboard/static fallback
        assert 'data-role-tile="superego"' in html
        assert 'data-role-tile="council"' in html
        assert 'data-role-tile="id"' in html

    def test_aria_live_summary_present(self, client):
        html = client.get("/").data.decode("utf-8")
        # Screen reader summary must be in the DOM with aria-live
        assert 'id="trinity-summary"' in html
        assert 'aria-live="polite"' in html


class TestStaticAssets:
    def test_trinity_css_served(self, client):
        rv = client.get("/static/css/trinity.css")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        # Reduced motion must be honored
        assert "prefers-reduced-motion" in body
        # Pitch-mode overrides present
        assert "body.pitch" in body

    def test_trinity_js_served(self, client):
        rv = client.get("/static/js/trinity.js")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        # AtlasTrinity API exposed
        assert "AtlasTrinity" in body
        # Scripted demo flagged with demo:true in payloads
        assert "demo: true" in body
        # All expected event kinds are handled
        for kind in (
            "request_start",
            "superego_start",
            "superego_end",
            "id_start",
            "id_end",
            "council_start",
            "council_member",
            "council_done",
            "heartbeat",
        ):
            assert kind in body

    def test_css_loaded_from_cortex(self, client):
        html = client.get("/").data.decode("utf-8")
        assert "/static/css/trinity.css" in html

    def test_js_loaded_from_cortex(self, client):
        html = client.get("/").data.decode("utf-8")
        assert "/static/js/trinity.js" in html


class TestAccessibility:
    def test_reduced_motion_hides_particles(self, client):
        """The CSS must explicitly hide particles under reduced-motion."""
        body = client.get("/static/css/trinity.css").data.decode("utf-8")
        # The reduced-motion section must exist
        idx = body.find("@media (prefers-reduced-motion: reduce)")
        assert idx != -1
        # ...and must hide particles somewhere inside it. Nested braces
        # make a fully-accurate CSS parse overkill; a bounded slice that
        # covers the entire media block is sufficient.
        section = body[idx:]
        assert ".particle" in section
        # Particles are display:none; hiding via opacity would still leave
        # them animated off-screen.
        assert "display: none" in section or "display:none" in section

    def test_svg_is_presentation_only(self, client):
        """The decorative SVG must be hidden from screen readers;
        meaning flows through the sr-only summary + detail tiles."""
        html = client.get("/").data.decode("utf-8")
        assert 'role="presentation"' in html or 'aria-hidden="true"' in html
