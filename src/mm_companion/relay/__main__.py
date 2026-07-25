"""``python -m mm_companion.relay`` — run the session relay."""

from __future__ import annotations

import sys

from .cli import run

if __name__ == "__main__":
    sys.exit(run())
