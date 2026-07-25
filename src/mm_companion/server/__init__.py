"""``python -m mm_companion.server`` — host a session on an always-on box.

The desktop app hosts a session while it is open; this runs the *same*
:class:`~mm_companion.core.session.server.SessionServer` headless, so a GM who
wants the table reachable around the clock can leave it on a server instead of
their laptop. It is Qt-free — it imports only ``core`` — and shares the workspace
(``MM_COMPANION_HOME`` points it at one), so a session created in the app can be
picked up here by id and vice versa.

The command line lives in :mod:`.cli`; :func:`run` is re-exported for callers
(and tests) that would rather not reach into a submodule.
"""

from __future__ import annotations

from .cli import run

__all__ = ["run"]
