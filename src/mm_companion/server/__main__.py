"""``python -m mm_companion.server`` — run a session headless."""

from __future__ import annotations

import sys

from .cli import run

if __name__ == "__main__":
    sys.exit(run())
