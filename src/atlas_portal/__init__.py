"""Atlas Portal — Atlas-specific extensions on top of research-portal.

This module holds the code that only makes sense on the Atlas AI
workstation: the Superego/Id/Council trinity event pipeline, the
Divine Council dashboard, Pitch Mode presentation logic, and demo-link
tokens for investor viewing.

Generic dashboard infrastructure (hardware discovery, pipeline
detection, auth, template rendering) continues to live in
``research_portal`` and stays suitable for any research workstation.

This split is a first step toward eventually extracting
``research_portal`` to its own repository. For now, both modules
live in the same package so development on Atlas-specific features
can move fast without coordinating releases.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "__version__",
]
