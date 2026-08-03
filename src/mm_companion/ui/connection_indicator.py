"""A persistent read-out of whether this app is actually reaching the table.

Lives in the menu bar's top-right corner, in every window that can be in a
session. It exists because the alternative was nothing: a drop used to be
reported by a status-bar message or a GM notice, both of which fade after ten
seconds, so a player whose Wi-Fi went out at 20:04 had no way at 20:20 to tell
whether the table had gone quiet or they had.

Two rules it follows, both from the theme layer:

- **The colour is a token, never a literal.** Callers name a semantic tint
  (``tint.better`` / ``tint.warning`` / ``tint.worse`` / ``accent``) and the dot
  resolves it at paint time, so a preset switch reaches it.
- **Muted is the awkward one.** ``text.muted`` is allowed to be a
  ``palette(...)`` expression, which :class:`QColor` cannot parse. The label goes
  through a stylesheet, where Qt resolves it; the dot falls back to the widget's
  own palette when the token is not a literal.

It follows a :class:`~mm_companion.ui.session_bridge.SessionBridge` and recomputes
its whole state from that bridge on every signal, rather than mapping each signal
to a state of its own — several signals can arrive for one transition, and one
state function is what stops them disagreeing.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from mm_companion.core.session import client as session_client
from mm_companion.ui import theme
from mm_companion.ui.theme.tokens import is_literal_color
from mm_companion.ui.widgets import muted_style, tinted_style

#: What the label says in each state. Short: this sits in a menu bar, and a
#: sentence there would push the menus around every time the link hiccuped.
TEXT_OFFLINE = "Offline"
TEXT_CONNECTING = "Connecting…"
TEXT_ONLINE = "Connected"
TEXT_RECONNECTING = "Reconnecting…"
TEXT_DROPPED = "Disconnected"
TEXT_REMOVED = "Removed"
TEXT_HOSTING = "Hosting"
TEXT_PUBLISHING = "Hosting…"
TEXT_LAN = "Hosting (LAN)"
#: Short on purpose — the explanation is in the tooltip. A menu bar is no place
#: for a sentence, and every state has to fit the width reserved below.
TEXT_UNREACHABLE = "Unreachable"

#: The token whose colour means "nothing is happening". Kept as a name so the
#: fallback below is the only place that knows it might not be a literal.
MUTED_TOKEN = "text.muted"

#: Every string the label can hold. The widget reserves the widest of them, so
#: the menu bar lays it out once and correctly — see :meth:`ConnectionIndicator._reserve`.
ALL_TEXTS = (
    TEXT_OFFLINE,
    TEXT_CONNECTING,
    TEXT_ONLINE,
    TEXT_RECONNECTING,
    TEXT_DROPPED,
    TEXT_REMOVED,
    TEXT_HOSTING,
    TEXT_PUBLISHING,
    TEXT_LAN,
    TEXT_UNREACHABLE,
)


class _StatusDot(QWidget):
    """A filled circle in one theme colour. Its own widget so the label beside it
    keeps ordinary text metrics and the dot can be sized from a metric token."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._token = MUTED_TOKEN
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @property
    def token(self) -> str:
        return self._token

    def set_token(self, token: str) -> None:
        if token != self._token:
            self._token = token
            self.update()

    def sizeHint(self) -> QSize:
        size = int(theme.metric("column.status-dot"))
        return QSize(size, size)

    def _colour(self) -> QColor:
        """The dot's colour, resolved at paint time so a preset switch reaches it."""
        value = theme.color(self._token)
        if is_literal_color(value):
            return QColor(value)
        # A ``palette(...)`` token — only ``text.muted`` is, in the bundled
        # presets, and only ever for the idle state. Ask the palette ourselves
        # rather than handing QColor something it would silently render black.
        return self.palette().color(QPalette.ColorRole.PlaceholderText)

    def paintEvent(self, event) -> None:  # noqa: ARG002 - Qt's signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour())
        size = min(self.width(), self.height())
        left = (self.width() - size) // 2
        top = (self.height() - size) // 2
        painter.drawEllipse(left, top, size, size)


class ConnectionIndicator(QWidget):
    """Shows whether this window is reaching its session, and how well."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = None
        # The things a bridge cannot be asked for: they arrive once, as a signal,
        # so they are remembered here and cleared when a session starts again.
        self._drop_reason = ""
        self._kicked_reason = ""
        self._listener_lost = False
        self._retry_detail: dict = {}

        self._dot = _StatusDot(self)
        self._label = QLabel(TEXT_OFFLINE, self)

        layout = QHBoxLayout(self)
        gap = int(theme.metric("space.sm"))
        layout.setContentsMargins(gap, 0, int(theme.metric("space.md")), 0)
        layout.setSpacing(gap)
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        self._reserve()

        self._apply(TEXT_OFFLINE, MUTED_TOKEN, "This window is not in a session.")

    def _reserve(self) -> None:
        """Fix the label's width to the widest state it can ever show.

        A menu bar lays its corner widget out when the *bar* is resized, and a
        label growing from "Offline" to "Reconnecting…" resizes nothing — so the
        widget keeps the geometry it was given and the longer text is clipped,
        which is how the state that most needs reading becomes the one you cannot
        read. Reserving the maximum up front means the bar's one placement is
        always right; it also stops the read-out sliding along the bar every time
        the link hiccups, which is worth having on its own.
        """
        metrics = self._label.fontMetrics()
        self._label.setFixedWidth(max(metrics.horizontalAdvance(text) for text in ALL_TEXTS))

    # -- following a session ----------------------------------------------

    def set_bridge(self, bridge) -> None:
        """Follow *bridge*, or ``None`` to go back to showing nothing in progress.

        Every signal lands on the same :meth:`refresh`, which recomputes the whole
        state from the bridge. Mapping signals to states one-to-one looks tidier
        and is wrong: a reconnect raises ``connected`` *and* a state change, and
        whichever arrived second would win.
        """
        if self._bridge is not None:
            for signal, slot in self._wiring(self._bridge):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    # Already gone, or never connected. Not worth raising over —
                    # a bridge is routinely torn down before we are.
                    pass
        self._bridge = bridge
        self._drop_reason = ""
        self._kicked_reason = ""
        self._listener_lost = False
        self._retry_detail = {}
        if bridge is not None:
            for signal, slot in self._wiring(bridge):
                signal.connect(slot)
        self.refresh()

    def _wiring(self, bridge) -> tuple:
        """Every signal that could change what we show, paired with its slot.

        Most only mean "look again"; four carry something the bridge cannot be
        asked for afterwards — why the session ended, and whether the listener
        is still there — so those are remembered on the way past.
        """
        return (
            (bridge.started, self._on_started),
            (bridge.stopped, self._on_signal),
            (bridge.published, self._on_signal),
            (bridge.listenerLost, self._on_listener_lost),
            (bridge.connected, self._on_connected),
            (bridge.disconnected, self._on_disconnected),
            (bridge.kicked, self._on_kicked),
            (bridge.connectionStateChanged, self._on_state),
        )

    def _on_signal(self, *_args: object) -> None:
        """The plain slot: this signal only means "look again"."""
        self.refresh()

    def _on_started(self, *_args: object) -> None:
        self._listener_lost = False
        self.refresh()

    def _on_listener_lost(self, *_args: object) -> None:
        self._listener_lost = True
        self.refresh()

    def _on_connected(self, *_args: object) -> None:
        # Reaching the table clears whatever went wrong last time, so a card that
        # said "Disconnected" cannot linger behind a live connection.
        self._drop_reason = ""
        self._kicked_reason = ""
        self._retry_detail = {}
        self.refresh()

    def _on_disconnected(self, reason: str) -> None:
        self._drop_reason = str(reason)
        self.refresh()

    def _on_kicked(self, reason: str) -> None:
        self._kicked_reason = str(reason) or "the GM removed you from the session"
        self.refresh()

    def _on_state(self, _state: str, detail: object) -> None:
        if isinstance(detail, dict):
            self._retry_detail = dict(detail)
        self.refresh()

    # -- what to show ------------------------------------------------------

    def refresh(self) -> None:
        """Recompute the whole state from the bridge."""
        bridge = self._bridge
        if bridge is None:
            self._apply(TEXT_OFFLINE, MUTED_TOKEN, "This window is not in a session.")
            return
        if bridge.hosting:
            self._apply(*self._hosting_state(bridge))
            return
        self._apply(*self._joined_state(bridge))

    def _hosting_state(self, bridge) -> tuple[str, str, str]:
        if self._listener_lost:
            return (
                TEXT_UNREACHABLE,
                "tint.warning",
                "The session is still running and everyone already in it is fine, "
                "but it is no longer reachable — nobody new can join.",
            )
        reachability = bridge.reachability
        if reachability is None:
            return (
                TEXT_PUBLISHING,
                "tint.warning",
                "Working out how players reach this session.",
            )
        # The reachability's own prose, verbatim — it is the one place that knows
        # whether this needed UPnP, a relay, or a hand-forwarded port.
        advice = "\n".join(reachability.advice) or "Players can join."
        if not reachability.internet_reachable:
            return (TEXT_LAN, "accent", advice)
        return (TEXT_HOSTING, "tint.better", advice)

    def _joined_state(self, bridge) -> tuple[str, str, str]:
        state = bridge.connection_state
        if state == session_client.STATE_ONLINE:
            return (TEXT_ONLINE, "tint.better", self._online_tooltip(bridge))
        if state == session_client.STATE_CONNECTING:
            return (TEXT_CONNECTING, "tint.warning", "Dialling the session.")
        if state == session_client.STATE_RECONNECTING:
            return (TEXT_RECONNECTING, "tint.warning", self._retry_tooltip())
        if self._kicked_reason:
            return (TEXT_REMOVED, "tint.worse", self._kicked_reason)
        if self._drop_reason:
            return (TEXT_DROPPED, "tint.worse", f"The session ended: {self._drop_reason}.")
        return (TEXT_OFFLINE, MUTED_TOKEN, "This window is not in a session.")

    @staticmethod
    def _online_tooltip(bridge) -> str:
        client = bridge.client
        if client is None:
            return "Connected to the session."
        lines = [f"Connected to “{client.session_name}” as {client.display_name}."]
        if client.latency_ms is not None:
            lines.append(f"Round trip {client.latency_ms:.0f} ms.")
        return "\n".join(lines)

    def _retry_tooltip(self) -> str:
        detail = self._retry_detail
        reason = str(detail.get("reason", "")) or "the connection dropped"
        attempt = int(detail.get("attempt", 0) or 0)
        retry_in = detail.get("retry_in")
        line = f"Lost the session ({reason}); trying to get back in."
        if attempt and isinstance(retry_in, (int, float)):
            line += f"\nAttempt {attempt}, retrying in {float(retry_in):.0f}s."
        return line

    def _apply(self, text: str, token: str, tooltip: str) -> None:
        self._label.setText(text)
        # Only the one state that must not be missed is tinted; the rest recede,
        # so the menu bar is a place to glance at rather than a traffic light.
        self._label.setStyleSheet(
            tinted_style("tint.worse") if token == "tint.worse" else muted_style()
        )
        self._dot.set_token(token)
        self.setToolTip(tooltip)
        self._label.setToolTip(tooltip)

    # -- what a test reads -------------------------------------------------

    @property
    def text(self) -> str:
        """The label as shown."""
        return self._label.text()

    @property
    def token(self) -> str:
        """The theme token the dot is currently painted from."""
        return self._dot.token


def install_connection_indicator(window) -> ConnectionIndicator:
    """Put an indicator in *window*'s menu-bar corner and hand it back.

    The menu bar reparents the widget, and takes its size into account when
    laying the menus out, so nothing else has to make room for it. Also stashed
    on the window as ``connection_indicator`` so a later ``set_bridge`` — the
    player side only learns which session it is in after the join dialog — can
    find it without threading a reference through three constructors.
    """
    indicator = ConnectionIndicator(window)
    window.menuBar().setCornerWidget(indicator, Qt.Corner.TopRightCorner)
    window.connection_indicator = indicator
    return indicator
