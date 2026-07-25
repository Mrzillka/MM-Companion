"""The player's half of a session: the sheet's two live wires to the GM.

:class:`SnapshotPusher` sends this sheet *out* — a character snapshot on join and
on every change, so the GM's card and the read-only sheet behind it are live
rather than a stale copy from join time. :class:`ConditionReceiver` takes the one
command that comes back *in*: the GM applying or removing a condition on this
character.

**Debounced from the start.** Editing a sheet publishes bus topics far faster
than a human thinks about them — dragging a spin box fires one per step, and a
snapshot is the largest message the protocol carries (see
:func:`snapshot_size`). A per-keystroke push is the one thing that could make
relayed traffic expensive, so every change only *arms* a single-shot timer and
one send goes out :data:`SNAPSHOT_DEBOUNCE_MS` after the last of a burst. If the
measured size ever warrants more, the next lever is sending deltas rather than
whole sheets — the debounce is what buys the time to decide that.

The pusher listens on the sheet's :class:`~mm_companion.ui.blocks.bus.SignalBus`
rather than on named section signals: a mod block that publishes ``edited`` or
``build-changed`` keeps the GM in sync for free, with no edit here.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer

from mm_companion.core.character import Character
from mm_companion.core.session.protocol import CharacterSnapshot, encode, sanitize_snapshot
from mm_companion.ui.blocks.bus import BUILD_CHANGED, CONDITION_CHANGED, EDITED
from mm_companion.ui.session_bridge import SessionBridge

#: How long a burst of edits is allowed to coalesce before one snapshot goes out.
SNAPSHOT_DEBOUNCE_MS = 400

#: The bus topics that mean "the GM's copy of this sheet is now out of date".
#: ``edited`` covers build edits and conditions; ``build-changed`` additionally
#: catches a runtime power toggle, which is deliberately *not* an edit.
SNAPSHOT_TOPICS = (EDITED, BUILD_CHANGED, CONDITION_CHANGED)


def snapshot_size(character: Character) -> int:
    """The encoded size, in bytes, of one snapshot of *character* on the wire.

    The whole relay bandwidth estimate rests on this number, so it is measurable
    rather than asserted: it is the real framed message, sanitized exactly as
    :meth:`~mm_companion.core.session.client.SessionClient.send_snapshot` would
    send it.
    """
    return len(encode(CharacterSnapshot(character=sanitize_snapshot(character.to_dict()))))


class SnapshotPusher(QObject):
    """Pushes a sheet's character to the session it is joined to, coalesced.

    Constructing one sends the join snapshot immediately; after that a send only
    happens once the sheet has been quiet for :data:`SNAPSHOT_DEBOUNCE_MS`.
    """

    def __init__(
        self,
        sheet,
        bridge: SessionBridge,
        *,
        debounce_ms: int = SNAPSHOT_DEBOUNCE_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._sheet = sheet
        self._bridge = bridge
        self._attached = True
        #: How many snapshots have actually gone out — the coalescing is visible here.
        self.sent = 0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(debounce_ms))
        self._timer.timeout.connect(self.push_now)

        # The bus has no unsubscribe, so :meth:`detach` gates the handler instead
        # of removing it; a detached pusher is inert but still subscribed.
        for topic in SNAPSHOT_TOPICS:
            sheet.bus.subscribe(topic, self.schedule)

        self.push_now()

    @property
    def pending(self) -> bool:
        """Whether a coalesced send is armed and waiting."""
        return self._timer.isActive()

    def schedule(self) -> None:
        """Note that the sheet changed; the send happens once the burst settles."""
        if self._attached:
            self._timer.start()

    def push_now(self) -> bool:
        """Send the current sheet at once, cancelling any armed send."""
        self._timer.stop()
        if not self._attached:
            return False
        client = self._bridge.client
        if client is None or not client.connected:
            return False
        if not client.send_snapshot(self._sheet.character.to_dict()):
            return False
        self.sent += 1
        return True

    def detach(self) -> None:
        """Stop pushing — the sheet closed, or the session ended."""
        self._attached = False
        self._timer.stop()


class ConditionReceiver(QObject):
    """Applies the GM's condition commands to this player's live sheet.

    The GM's card only *asks*; the change happens here, through the conditions
    block's own :meth:`~mm_companion.ui.sections.conditions.ConditionsSection.apply_condition_by_id`
    — which means the same :mod:`~mm_companion.core.rules` resolver the player's
    own "+" runs, so a GM-applied condition bundles its umbrella members,
    supersedes what it should, stacks a Hit, and marks the sheet dirty exactly
    like a local one. The edit publishes the sheet's bus topics, so the
    :class:`SnapshotPusher` beside it bounces the result straight back and the
    GM's chips restate from the player's real model.

    A sheet whose conditions block a mod removed simply receives nothing.
    """

    def __init__(self, sheet, bridge: SessionBridge, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sheet = sheet
        self._bridge = bridge
        self._attached = True
        #: How many commands actually landed on the character.
        self.applied = 0
        bridge.conditionCommand.connect(self.handle)

    def handle(self, action: str, payload: object) -> None:
        """Apply one ``("apply" | "remove", payload)`` command from the GM."""
        if not self._attached or not isinstance(payload, dict):
            return
        section = getattr(self._sheet, "conditions", None)
        if section is None:
            return
        if not self._is_for_us(str(payload.get("player_id", ""))):
            return
        condition_id = str(payload.get("condition_id", "") or "")
        raw = payload.get("parameter")
        parameter = str(raw) if raw else None
        if action == "apply":
            changed = section.apply_condition_by_id(condition_id, parameter)
        else:
            changed = section.remove_condition_by_id(condition_id, parameter)
        if changed:
            self.applied += 1

    def _is_for_us(self, player_id: str) -> bool:
        """The server only sends a command down its target's own connection, so
        this is belt and braces — but a command naming somebody else is not ours
        to apply."""
        client = self._bridge.client
        if client is None or not player_id or not client.player_id:
            return True
        return player_id == client.player_id

    def detach(self) -> None:
        """Stop applying — the sheet closed, or the session ended."""
        self._attached = False
