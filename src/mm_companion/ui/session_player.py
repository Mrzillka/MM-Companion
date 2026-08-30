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
from mm_companion.core.session import client as session_client
from mm_companion.core.session.protocol import CharacterSnapshot, encode, sanitize_snapshot
from mm_companion.ui.blocks.bus import BUILD_CHANGED, CONDITION_CHANGED, EDITED
from mm_companion.ui.session_bridge import SessionBridge, set_active_session
from mm_companion.ui.session_portrait import encode_portrait
from mm_companion.ui.undo import absorbing

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
        # The last portrait source encoded and its result, so a burst of edits does
        # not re-encode the (unchanged) image every push. ``_portrait_source`` starts
        # as a sentinel so even a ``None`` path is computed once.
        self._portrait_source: object = object()
        self._portrait_cache: str | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(debounce_ms))
        self._timer.timeout.connect(self.push_now)

        # :meth:`detach` gates the handler rather than removing it, even though
        # ``SignalBus.forget`` exists now: the sheet outlives the pusher and drops it
        # on the way out, so what matters here is that a detached pusher sends
        # nothing, not that the bus has forgotten it. A detached pusher is inert but
        # still subscribed.
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
        snapshot = self._sheet.character.to_dict()
        portrait = self._portrait_payload(self._sheet.character.image_path)
        if portrait:
            # Rides in the snapshot dict; sanitize_snapshot keeps it (it only strips
            # image_path), so the GM's card and read-only sheet get the picture.
            snapshot["portrait"] = portrait
        if not client.send_snapshot(snapshot):
            return False
        self.sent += 1
        return True

    def _portrait_payload(self, image_path: str | None) -> str | None:
        """The base64 thumbnail for *image_path*, encoding only when it has changed."""
        if image_path == self._portrait_source:
            return self._portrait_cache
        self._portrait_source = image_path
        self._portrait_cache = encode_portrait(image_path)
        return self._portrait_cache

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
        bridge.heroPointsCommand.connect(self.handle_hero_points)

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
        # Absorbed, not recorded: this is the GM's change, and Ctrl+Z is for
        # taking back what *this* player did. Undoing the table's Stunned — and
        # pushing that back up as a snapshot — is not something to offer.
        with absorbing(self._sheet):
            if action == "apply":
                changed = section.apply_condition_by_id(condition_id, parameter)
            else:
                changed = section.remove_condition_by_id(condition_id, parameter)
        if changed:
            self.applied += 1

    def handle_hero_points(self, value: int) -> None:
        """Set this sheet's hero-point total from the GM's command.

        Like a condition command, the change lands on the player's own model
        through the system-info section, and the snapshot pusher beside it bounces
        the result back so the GM's card restates from the real value.
        """
        if not self._attached:
            return
        section = getattr(self._sheet, "system_info", None)
        if section is None:
            return
        with absorbing(self._sheet):  # the GM's, not this player's — see handle()
            section.set_hero_points(value)
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


def attach_player_session(window, bridge: SessionBridge) -> None:
    """Wire a joined session to a character-sheet window.

    Sets up the :class:`SnapshotPusher` and :class:`ConditionReceiver` on the
    window's sheet, leaves the session (and clears the process-wide handle) when
    the window closes, and reports a disconnect/kick on its status bar. The wires
    are stashed on the window so they live exactly as long as it does.

    The single seam both entry points share: the launcher's "Join Session" (with a
    freshly loaded character) and a sheet's own ``Session ▸ Join session…`` (with
    the character already open) both call this after ``bridge.join(...)`` succeeds.

    It is also where the sheet's blocks are told a session began or ended, through
    :meth:`~mm_companion.ui.character_sheet.CharacterSheet.sync_session` — the Dice
    block swaps its private roll history for the table's shared one off that.
    """
    pusher = SnapshotPusher(window.sheet, bridge, parent=window)
    receiver = ConditionReceiver(window.sheet, bridge, parent=window)

    indicator = getattr(window, "connection_indicator", None)
    if indicator is not None:
        indicator.set_bridge(bridge)

    def leave() -> None:
        pusher.detach()
        receiver.detach()
        bridge.stop()
        set_active_session(None)
        if indicator is not None:
            indicator.set_bridge(None)
        window.sheet.sync_session()

    def on_state(state: str, detail: object) -> None:
        """Say it on the status bar too, for the change worth narrating.

        The indicator carries the standing truth; this is the moment it changed,
        which is what someone looking at the sheet rather than the menu bar sees.
        """
        if state == session_client.STATE_RECONNECTING:
            window.statusBar().showMessage("Lost the session — trying to get back in…", 10000)
        elif state == session_client.STATE_ONLINE and isinstance(detail, dict):
            window.statusBar().showMessage("Back in the session.", 5000)

    window.closed.connect(leave)
    # A reconnect raises this again with a fresh welcome, and ``push_now`` drops
    # any send it makes while offline — so without this the GM's copy of the
    # sheet would stay frozen at whatever it was when the link went down.
    bridge.connected.connect(lambda _welcome: pusher.push_now())
    bridge.connectionStateChanged.connect(on_state)
    bridge.disconnected.connect(
        lambda reason: window.statusBar().showMessage(f"Left the session: {reason}", 10000)
    )
    bridge.kicked.connect(
        lambda reason: window.statusBar().showMessage(
            f"The GM removed you from the session: {reason}", 10000
        )
    )
    # The blocks were built before the session existed, so tell them it does now.
    # (A session *ending* reaches them on its own: the Dice block follows the
    # bridge's disconnected/stopped/kicked signals once it has one.)
    window.sheet.sync_session()
    window.statusBar().showMessage(f"Joined “{bridge.client.session_name}”.", 10000)
    # Keep the wires alive for the window's lifetime.
    window._session_pusher = pusher
    window._session_receiver = receiver
    window._session_bridge = bridge
