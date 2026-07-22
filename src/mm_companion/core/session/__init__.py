"""The online session layer: shared play state over the network.

Pure Python and Qt-free (respects ``ui -> core -> data``) so the whole layer is
headless-testable and can also run as a standalone server. The pieces:

- :mod:`.protocol` — the message vocabulary spoken over the wire, plus
  newline-delimited JSON framing.
- :mod:`.model` — :class:`~.model.SessionState`, the roster, and the roll log,
  in the same plain-data ``to_dict``/``from_dict`` idiom as
  :mod:`mm_companion.core.character`.
- :mod:`.store` — workspace persistence, so a session survives closing the app.

Qt-side wiring (turning the callbacks below into signals) lives in
``ui/session_bridge.py`` and never leaks back into this package.
"""

from __future__ import annotations

from . import model, protocol, store

__all__ = ["model", "protocol", "store"]
