"""Test-only fault injection at system boundaries.

The environment variable is consumed only by tests that need to observe a real
process dying before it can hand a result back to the model. It is not a
user-facing product capability and never changes normal execution.
"""

from __future__ import annotations

import os

CRASH_EXIT_CODE = 70


def crash(point: str) -> None:
    """Terminate the current process abruptly when the named fault is injected."""
    if os.environ.get("CTA_FAULT_CRASH") == point:
        os._exit(CRASH_EXIT_CODE)
