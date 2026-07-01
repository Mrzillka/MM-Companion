"""Developer entry point: launch the MM-Companion UI.

Convenience wrapper so the interface can be started straight from an IDE's
Run button (or ``python run.py``) while iterating on the UI. Equivalent to
``python -m mm_companion`` / the ``mm-companion`` console script.
"""

from mm_companion.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
