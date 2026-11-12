"""Hosting the session: the bridge, connecting, the relay fallback, and the trouble toast.

Most of this is about reachability, which is hard. A home
connection is often not reachable from the internet at all — carrier-grade NAT,
or an ISP router the user does not control — and the failure is silent unless
someone says so. So:

- :func:`~mm_companion.core.session.discovery.publish_session` returns finished
  prose in ``Reachability.advice``, and it is rendered **verbatim** under the
  join code rather than being summarised or hidden behind a details toggle.
- "Reachable over the internet" and "only on this network" are visibly different
  states, not a silently-LAN address that looks like success until nobody can
  join.
- A GM who runs a tunnel (playit.gg, ngrok, Tailscale) pastes the address it gave
  them into **Tunnel address**, and the join code carries that instead.
- Failing all of that, the session falls back to a **relay**: the GM's app dials
  *out* to a public box, as every player does, so nothing has to be reachable at
  all. Direct is always tried first — a direct connection costs the relay
  nothing — so the relay is the last rung of the ladder, never the default.

All the networking is in :mod:`mm_companion.core.session`; this mixin only talks
to :class:`~mm_companion.ui.session_bridge.SessionBridge`.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QWidget,
)

from mm_companion.core.session import client as session_client
from mm_companion.core.session import discovery, store
from mm_companion.core.session import server as session_server
from mm_companion.core.session.model import (
    PlayerSlot,
)
from mm_companion.ui import theme
from mm_companion.ui.gm.model import (
    _wrapped,
)
from mm_companion.ui.session_bridge import SessionBridge
from mm_companion.ui.session_dialogs import HostOptions
from mm_companion.ui.toasts import ToastCard


class GMHostingMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    def _build_notice(self) -> ToastCard:
        self._notice = ToastCard()
        self._notice_label = _wrapped("")
        self._notice.add_widget(self._notice_label)
        return self._notice

    def _build_trouble(self) -> ToastCard:
        """The card that says the table cannot be reached, and offers the way back.

        Held rather than poked (see :class:`ToastCard`): this is a condition, not a
        message, and one that fades after ten seconds is one a GM will not see
        when they next look up.
        """
        self._trouble = ToastCard()
        self._trouble.set_accent(theme.color("tint.worse"))
        self._trouble_label = _wrapped("")
        self._trouble.add_widget(self._trouble_label)
        self._trouble_button = QPushButton("Reconnect now")
        self._trouble_button.clicked.connect(self.reconnect_session)
        # In a row of its own with a stretch after it: the card's body is a
        # column, and a button added straight to it spans the whole board.
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self._trouble_button)
        row_layout.addStretch(1)
        self._trouble.add_widget(row)
        return self._trouble

    def _connect_bridge(self) -> None:
        self._bridge.started.connect(self._on_started)
        self._bridge.stopped.connect(self._on_stopped)
        self._bridge.published.connect(self._on_published)
        self._bridge.rosterChanged.connect(self._show_roster)
        self._bridge.snapshotReceived.connect(self._on_snapshot)
        # How a player's initiative reaches the board. No message of its own: a
        # roll already carries the spec that says what it was.
        self._bridge.rollAdded.connect(self._on_roll_added)
        # A player's save off a request card or a chip: what it did lands on them.
        self._bridge.rollAdded.connect(self._on_player_save)
        self._bridge.playerJoined.connect(self._on_player_joined)
        self._bridge.refused.connect(self._on_refused)
        self._bridge.error.connect(self._on_error)
        # Whether anybody can still *get in*, which is a different question from
        # whether the session is running and was previously asked nowhere in this
        # window — only the menu bar's corner read-out ever heard about it.
        self._bridge.listenerLost.connect(self._on_trouble)
        self._bridge.hostStateChanged.connect(self._on_trouble)
        self._bridge.started.connect(self._on_trouble)
        self._bridge.stopped.connect(self._on_trouble)
        # ...and the same question from the other seat, where this app is a
        # client of a session hosted elsewhere.
        self._bridge.connectionStateChanged.connect(self._on_trouble)
        # The other way of being in a session: dialled in to one hosted on a
        # server rather than hosting it here.
        self._bridge.connected.connect(self._on_joined)
        self._bridge.disconnected.connect(self._on_left)
        # The session's own settings (its minion rules) ride the shared state.
        self._bridge.modStateChanged.connect(self._on_mod_state)
        self._bridge.stopped.connect(self._apply_minion_rules)

    # -- hosting -----------------------------------------------------------

    @property
    def bridge(self) -> SessionBridge:
        """The session this window drives — the seam later phases attach to."""
        return self._bridge

    def connect_to_server(self, entry: dict, server_label: str = "") -> bool:
        """Take the GM's seat on a session hosted elsewhere.

        *entry* is one row of the hub's catalog: it carries the join code and the
        session's gm token, which are the two things this app cannot work out for
        itself. Returns whether the connection was made; the caller reports why
        not, since it owns the dialog the GM is standing in.
        """
        try:
            code = discovery.decode_join_code(entry.get("join_code", ""))
        except discovery.JoinCodeError as exc:
            self._show_notice(str(exc), theme.color("tint.worse"))
            return False
        # Both are set before the join, not after: connecting raises `connected`
        # synchronously, and _on_joined writes the status line that names them.
        self._server_label = server_label
        # The catalog handed us the code; the GM should not have to go back and
        # ask the server for the one thing they need to invite anyone.
        # Rewritten in this app's own form, so a server still minting the long
        # version-1 codes hands the GM the short one to send all the same.
        self._join_code = discovery.canonical_join_code(str(entry.get("join_code", "")))
        try:
            self._bridge.join(
                code,
                display_name="GM",
                gm_token=str(entry.get("gm_token", "")),
            )
        except Exception as exc:  # noqa: BLE001 - every failure is one message
            self._server_label = ""
            self._join_code = ""
            self._show_notice(f"Could not reach the session: {exc}", theme.color("tint.worse"))
            return False
        self._copy_code_action.setEnabled(bool(self._join_code))
        self._refresh_trouble()
        return True

    def _on_joined(self, welcome: dict) -> None:
        """Seed the window from the Welcome of a session hosted elsewhere.

        The GM window is written against a SessionState, and a session on a server
        is not ours to own — so what we keep is a **mirror**, filled from the wire
        and never saved to disk. Every read in this window goes on working; only
        the writes were rerouted through the bridge, which knows whose session it
        is.
        """
        if not self._bridge.is_gm:
            # Refused the GM seat but let in as a player: possible only if the
            # token went stale between listing the catalog and connecting. Say so
            # rather than presenting a console whose buttons all quietly fail.
            self._show_notice(
                "This app is connected as a player, not the GM — its GM controls will "
                "not work. Reopen GM Mode to pick the session up again.",
                theme.color("tint.worse"),
            )
        self._state.id = str(welcome.get("session_id", "")) or self._state.id
        self._state.name = str(welcome.get("session_name", "")) or self._state.name
        self._state.npc_paths = [str(p) for p in welcome.get("npc_paths", [])]
        self._mirror_roster(welcome.get("roster", []))
        where = f" on {self._server_label}" if self._server_label else ""
        self._set_status(f"Connected to “{self._state.name}”{where}", theme.color("accent"))
        self._clear_advice()
        self._show_advice(
            (
                "Players join with this session's join code, whether or not you are here.",
                "Closing this window leaves the table running.",
            )
        )
        self.setWindowTitle(f"GM Mode — {self._state.name}")
        self._refresh_rolls()
        self._refresh_npcs()
        self._refresh_idle_status()
        self.publish_table_rules()

    def _on_left(self, reason: str) -> None:
        self._set_status(f"Disconnected ({reason})", theme.color("tint.worse"))

    def _mirror_roster(self, roster: list) -> None:
        """Rebuild the mirrored roster from a wire roster, keeping snapshots.

        Roster entries carry no character (they never do — the table's combined
        sheets would not fit in one message), so the snapshots this window has
        collected separately are preserved rather than blanked.
        """
        players: dict[str, PlayerSlot] = {}
        for entry in roster:
            if not isinstance(entry, dict):
                continue
            slot = PlayerSlot.from_dict(entry)
            slot.character = self._snapshots.get(slot.player_id, {})
            players[slot.player_id] = slot
        self._state.players = players

    def start_hosting(self, options: HostOptions) -> None:
        """Begin hosting with *options* (from the launch dialog) and publish.

        The connection choices are held for the run so the relay fallback and the
        reachability probe read the same values the GM picked in the launch dialog.
        """
        manual_host, manual_port = "", 0
        if options.tunnel:
            try:
                manual_host, manual_port = discovery.parse_address(options.tunnel)
            except discovery.AddressError as exc:
                QMessageBox.warning(self, "Tunnel address", str(exc))
                return

        self._host_options = options
        self._relay_attempted = False
        self._state.name = options.name
        self._rename_session()
        # A resumed session already carries each seat's last snapshot, so the cards
        # come up populated rather than blank until everyone reconnects and pushes.
        self._snapshots = {
            player_id: dict(slot.character)
            for player_id, slot in self._state.players.items()
            if slot.character
        }
        if not self._begin_hosting():
            return

        self._set_status("Working out how players can reach you…", theme.color("accent"))
        self._clear_advice()
        self._bridge.publish(manual_host=manual_host, external_port=manual_port)

    def _begin_hosting(self, relay: str = "") -> bool:
        """Start the server (directly, or through *relay*), reporting failure once."""
        try:
            self._bridge.host(
                self._state,
                port=self._host_options.port,
                bind=self._bind,
                gm_name="GM",
                relay_url=relay,
            )
        except OSError as exc:
            if relay:
                # The relay is a fallback, so its failure is a notice and a
                # return to the direct connection, not a dead end.
                self._show_notice(
                    f"{discovery.ADVICE_RELAY_UNREACHABLE} ({exc})", theme.color("tint.worse")
                )
                return False
            QMessageBox.warning(
                self,
                "Could not host",
                f"The session could not start listening: {exc}\n\n"
                "Another program may already be using that port — try a "
                "different one, or set the port to automatic.",
            )
            return False
        except ValueError as exc:  # an unreadable relay address
            self._show_notice(
                f"That relay address cannot be used: {exc}", theme.color("tint.worse")
            )
            return False
        # Not on the ``started`` signal: the server emits that from inside its own
        # ``start()``, before the bridge has taken ownership of it, so ``hosting``
        # is still False there and the history would attach to nothing. The
        # reconnect affordance reads ``can_reconnect``, which is the same question
        # asked of the same half-set bridge, so it comes through here too.
        self._refresh_rolls()
        self._refresh_trouble()
        return True

    def _fall_back_to_relay(self) -> None:
        """Second rung of the ladder: re-host through the relay and republish.

        Direct hosting is stopped first — the session can only be reached one way
        at a time, and the join code has to name the one that works. Nobody has
        joined yet at this point (the code is seconds old), so no player is being
        dropped in the swap.
        """
        self._relay_attempted = True
        relay = self._host_options.relay
        self._set_status("Trying the relay…", theme.color("accent"))
        self._bridge.stop()
        if not self._begin_hosting(relay) and not self._begin_hosting():
            self._refresh_idle_status()
            return
        self._bridge.publish()

    def reconnect_session(self) -> None:
        """Try to reach the table again — Session ▸ Reconnect, and the notice's button.

        Never a stop-and-host-again, whichever end this window holds. Hosting, the
        listener is reopened underneath a session that is still running, so nobody
        at the table is disturbed; ``stop`` would farewell every one of them with a
        reason their apps treat as final. Dialled in to a session on a server, the
        old client is dropped and a new one made — which costs the table nothing,
        because the session is not ours and is still there.
        """
        try:
            self._bridge.reconnect()
        except Exception as exc:  # noqa: BLE001 - every failure is one message
            self._show_notice(f"Could not reconnect: {exc}", theme.color("tint.worse"))
            self._refresh_trouble()
            return
        self._show_notice("Trying to reach the table again…", theme.color("accent"))
        self._refresh_trouble()

    def _on_trouble(self, *_args: object) -> None:
        """Any signal that could change whether the table is reachable."""
        self._refresh_trouble()

    def _refresh_trouble(self) -> None:
        """Recompute the whole thing from the bridge, like the corner read-out does.

        One state function rather than a signal per state: several signals arrive
        for one transition (a relisten that works raises both ``started`` and a
        host state), and whichever landed second would otherwise win.
        """
        text, colour = self._trouble_text()
        self._reconnect_action.setEnabled(self._bridge.can_reconnect)
        if not text:
            self._trouble.release()
            return
        self._trouble_label.setText(text)
        self._trouble_label.setStyleSheet(f"color: {colour};" if colour else "")
        self._trouble_button.setEnabled(self._bridge.can_reconnect)
        self._trouble.hold()

    def _trouble_text(self) -> tuple[str, str]:
        """What is wrong with reaching this table, or ``("", "")`` when nothing is."""
        if self._bridge.hosting:
            state = self._bridge.host_state
            if state == session_server.HOST_STATE_RELISTING:
                return (
                    "Nobody new can join — the session is trying to reopen itself. "
                    "Everyone already at the table is unaffected.",
                    theme.color("tint.warning"),
                )
            if state == session_server.HOST_STATE_UNREACHABLE:
                return (
                    "Nobody new can join: the session could not be reopened. "
                    "Everyone already at the table is unaffected, and the same "
                    "join code will work again once this succeeds.",
                    theme.color("tint.worse"),
                )
            return ("", "")
        if self._bridge.in_session:
            if self._bridge.connection_state == session_client.STATE_RECONNECTING:
                return (
                    "Lost the connection to the session — trying to get back in.",
                    theme.color("tint.warning"),
                )
            return ("", "")
        if self._bridge.can_reconnect:
            return (
                "This window is no longer connected to the session.",
                theme.color("tint.worse"),
            )
        return ("", "")

    def stop_hosting(self) -> None:
        """Leave the session, clear the join code and cards, and go back to idle.

        Symmetrical for both ways of being in one: this app's own server is shut
        down, while a session hosted on a server is only *disconnected from* — it
        goes on running with its players in it, which is the point of putting it
        there.
        """
        self._relay_attempted = False
        self._bridge.stop()
        self._join_code = ""
        self._copy_code_action.setEnabled(False)
        self._clear_advice()
        self._clear_cards()
        self._refresh_idle_status()
        # Leaving on purpose is not trouble, and ``stop`` also forgets what there
        # would be to go back to — so there is nothing left for the card to offer.
        self._refresh_trouble()

    def _refresh_rolls(self) -> None:
        """Point the history at the live session, or at the one on disk.

        In a session it follows the bridge, whether this app is hosting or dialled
        in to a session on a server. Between sessions there is nothing to follow,
        but the state loaded from the workspace still carries the log — so
        reopening GM Mode shows last night's rolls rather than a blank panel.
        """
        if self._bridge.hosting or self._bridge.joined:
            self._history.attach(self._bridge)
            return
        self._history.detach()
        self._history.set_rolls([roll.to_dict() for roll in self._state.rolls])

    def _show_offline_roll(self, roll: object) -> None:
        """Show a roll the GM made before hosting started.

        It is never persisted — there is no session running to record it in — but it
        gets a negative ``seq`` so the GM can still strike it from the panel. Starting
        to host re-seeds the panel from the real log, at which point these are gone.
        """
        if not isinstance(roll, dict):
            return
        result = roll.get("result")
        self._offline_seq -= 1
        record = {
            "seq": self._offline_seq,
            "player_name": self._name_of_gm(),
            "die": roll["die"],
            "bonus": roll["bonus"],
            "penalty": roll["penalty"],
            "dc": roll["dc"],
            "degree": None if result is None else result.degree,
            "critical": (
                bool(result.critical)
                if result is not None
                else (
                    not roll.get("routine")
                    and (roll["die"] == 1 or (roll["die"] == 20 and not roll.get("minion")))
                )
            ),
            "label": roll.get("label", ""),
            # Local-only, as everywhere: it carries the chain this card offers.
            "spec": roll.get("spec"),
            "routine": bool(roll.get("routine")),
            "minion": bool(roll.get("minion")),
        }
        self._history.add_roll(record)
        # A save an NPC answered off the history: what failing did goes on it.
        self._on_answer_back(record)
        # A recovery check an NPC rolled: what making it shed comes off it.
        self._on_recovery_back(record)
        # An NPC's attack against an area's primary, rolled from the area window: off
        # the air there is no rollAdded to bring its total back, so it is handed over.
        if getattr(self, "_area", None) is not None:
            self._area.take_roll(record)
        # And an aimed attack's die, for the same reason.
        if getattr(self, "_attack", None) is not None:
            self._attack.take_roll(record)
        # And a targeted action's check (Aid, Feint, Trick).
        if getattr(self, "_targeted", None) is not None:
            self._targeted.take_roll(record)

    def _on_local_roll_removed(self, seq: int) -> None:
        """Persist a roll the GM struck while not hosting.

        A live session removes it wherever the session lives (and this never
        fires). Off the air the panel shows the resumed session's persisted log,
        so drop the roll from the state and save it too; a pre-hosting offline
        roll is not in the state, so this is a no-op for those.
        """
        if self._bridge.hosting or self._bridge.joined:
            return
        if self._state.remove_roll(seq) is not None:
            try:
                store.save_session(self._state, write_rolls=True)
            except OSError:
                pass

    def _on_local_roll_revealed(self, seq: int) -> None:
        """Persist a hidden roll the GM showed while not hosting.

        The same shape as :meth:`_on_local_roll_removed`: off the air there is no
        table to show it to, but the resumed log is the one a later join replays, so
        the roll is stored visible and the next table sees it.
        """
        if self._bridge.hosting or self._bridge.joined:
            return
        if self._state.reveal_roll(seq) is not None:
            try:
                store.save_session(self._state, write_rolls=True)
            except OSError:
                pass

    def _name_of_gm(self) -> str:
        slot = next((s for s in self._state.players.values() if s.is_gm), None)
        return slot.display_name if slot is not None else "GM"

    def _rename_session(self) -> None:
        name = (self._state.name or "").strip() or "Session"
        self._state.name = name
        self._bridge.set_session_name(name)
        self.setWindowTitle(f"GM Mode — {name}")

    # -- bridge signals ----------------------------------------------------

    def _on_started(self, host: str, port: int) -> None:
        self._rename_session()
        self._notice.dismiss()
        self.publish_table_rules()

    def _on_stopped(self) -> None:
        self._copy_code_action.setEnabled(False)
        self._refresh_rolls()

    def _on_published(self, reachability: discovery.Reachability) -> None:
        if self._should_try_relay(reachability):
            self._fall_back_to_relay()
            return

        self._join_code = self._bridge.join_code()
        self._copy_code_action.setEnabled(bool(self._join_code))

        if reachability.method == discovery.METHOD_RELAY:
            self._set_status(
                "Players anywhere can join with this code — this session is going "
                "through the relay.",
                theme.color("tint.better"),
            )
        elif reachability.method == discovery.METHOD_MANUAL:
            self._set_status(
                f"This code points at {reachability.host}:{reachability.port}. "
                "Anyone who can reach that address can join.",
                theme.color("accent"),
            )
        elif reachability.internet_reachable:
            self._set_status(
                "Players anywhere can join with this code.",
                theme.color("tint.better"),
            )
        else:
            self._set_status(
                "Only players on this network can join with this code — "
                "nothing outside it can reach this machine yet.",
                theme.color("tint.worse"),
            )
        self._show_advice(reachability.advice)

    def _should_try_relay(self, reachability: discovery.Reachability) -> bool:
        """Is this the moment for the last rung — and has it not been tried already?

        Only after a *direct* attempt has come back unreachable. A tunnel address
        the GM typed is taken at their word (there is nothing to probe and nothing
        to improve on), and a relay is never tried twice for one hosting run.
        """
        return (
            not self._relay_attempted
            and self._host_options.use_relay
            and bool(self._host_options.relay)
            and reachability.method not in (discovery.METHOD_MANUAL, discovery.METHOD_RELAY)
            and not reachability.internet_reachable
        )

    def _on_player_joined(self, payload: dict) -> None:
        player = payload.get("player", {})
        name = str(player.get("display_name", "")) or "A player"
        # A returning player is not news the same way a new one is, and saying
        # "joined" for both is what made a reconnect look like a second arrival.
        if payload.get("adopted") or not payload.get("new", True):
            self._show_notice(f"{name} rejoined.", theme.color("tint.better"))
            return
        self._show_notice(f"{name} joined.", theme.color("tint.better"))

    def _on_refused(self, payload: dict) -> None:
        self._show_notice(
            f"A connection was refused: {payload.get('message') or payload.get('code', '')}",
            theme.color("tint.worse"),
        )

    def _on_error(self, code: str, message: str) -> None:
        self._show_notice(f"{message or code}", theme.color("tint.worse"))
