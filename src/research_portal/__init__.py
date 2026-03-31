"""Research Portal -- Zero-config research workstation dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

__version__ = "0.1.5"
__all__ = ["__version__", "create_app"]


def create_app(*, no_auth: bool = False) -> Flask:
    """Create and return the Flask application.

    Parameters
    ----------
    no_auth:
        If *True*, disable HTTP Basic authentication (useful for
        local-only development).
    """
    from research_portal.app import build_app

    return build_app(no_auth=no_auth)
