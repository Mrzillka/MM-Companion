"""The online session layer: shared play state over the network.

Pure Python and Qt-free (respects ``ui -> core -> data``) so the whole layer is
headless-testable and can also run as a standalone server. The pieces:

- :mod:`.protocol` — the message vocabulary spoken over the wire, plus
  newline-delimited JSON framing.
- :mod:`.model` — :class:`~.model.SessionState`, the roster, and the roll log,
  in the same plain-data ``to_dict``/``from_dict`` idiom as
  :mod:`mm_companion.core.character`.
- :mod:`.store` — workspace persistence, so a session survives closing the app.
- :mod:`.net` — message framing plus the :class:`~.net.Transport` seam (direct
  TCP now, a relay later).
- :mod:`.server` — :class:`~.server.SessionServer`, what the GM's app hosts.
- :mod:`.client` — :class:`~.client.SessionClient`, what a player runs.
- :mod:`.discovery` — join codes, UPnP port mapping, the plain-language
  connectivity advice the UI shows, and the relay transport registry.
- :mod:`.relay` — the relay transport, for the many players (and GMs) whom no
  port forward can reach. Importing this package registers it, so a relay join
  code works with no further wiring.

Both ends take an ``on_event`` callback and hand it ``(kind, payload)`` pairs
where the payload is always a plain dict. Qt-side wiring (turning those into
signals) lives in ``ui/session_bridge.py`` and never leaks back into this package.
"""

from __future__ import annotations

from . import client, discovery, model, net, protocol, relay, server, store

__all__ = ["client", "discovery", "model", "net", "protocol", "relay", "server", "store"]
