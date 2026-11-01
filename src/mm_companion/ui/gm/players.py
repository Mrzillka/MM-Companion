"""The player cards: the roster, a player's sheet, removing a player, and conditions
applied straight onto a player's live sheet.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QMessageBox,
)

from mm_companion.core import storage
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.session.model import (
    new_id,
)
from mm_companion.core.session.protocol import (
    DISPOSITION_PLAYER,
)
from mm_companion.ui import theme
from mm_companion.ui.gm.model import (
    SCENE_PLAYER,
    _player_key,
    _SceneEntry,
)
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.sections.conditions import condition_display_name
from mm_companion.ui.widgets import (
    discard_widget,
)


class GMPlayersMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    # -- player cards ------------------------------------------------------

    def _show_roster(self, players: list) -> None:
        """Reconcile the card grid against the roster, in place.

        Updated rather than rebuilt: the roster is re-broadcast on every join
        *and* every snapshot, so tearing every card down each time would make the
        grid flicker whenever anybody edited their sheet.
        """
        seen: set[str] = set()
        connected: set[str] = set()
        for entry in players:
            player_id = str(entry.get("player_id", ""))
            if not player_id:
                continue
            # The GM is not a player on their own board: conditions and rolls for
            # the GM happen on the GM's own sheet and roller, not through a card.
            if entry.get("is_gm"):
                continue
            seen.add(player_id)
            if entry.get("connected"):
                connected.add(player_id)
            card = self._cards.get(player_id)
            if card is None:
                card = PlayerCard(self._data)
                card.openSheetRequested.connect(self._open_player_sheet)
                card.applyConditionRequested.connect(self._apply_condition)
                card.removeConditionRequested.connect(self._remove_condition)
                card.recoveryRequested.connect(self._request_player_recovery)
                card.setHeroPointsRequested.connect(self._set_hero_points)
                card.removePlayerRequested.connect(self._remove_player)
                card.pinsChanged.connect(lambda pid, refs: self._store_pins(_player_key(pid), refs))
                card.loadRequested.connect(self._roller.load_spec)
                card.rollRequested.connect(self._roller.roll_spec)
                card.pinPickerRequested.connect(self._open_player_pin_picker)
                card.sceneToggled.connect(lambda pid, on: self._set_in_scene(SCENE_PLAYER, pid, on))
                card.pins.set_pins(self._pins_for(_player_key(player_id), "player"))
                card.pins.widthHintChanged.connect(self._sync_card_widths)
                self._cards[player_id] = card
                self._cards_flow.addWidget(card)
            card.set_roster(entry)
            snapshot = self._snapshots.get(player_id)
            if snapshot:
                card.set_character(snapshot)

        for player_id in [p for p in self._cards if p not in seen]:
            self._drop_card(player_id)
        self._no_players.setVisible(not self._cards)
        self._sync_card_widths()
        self._sync_scene_players(seen, connected)

    def _sync_scene_players(
        self, seated: set[str], connected: set[str] | None = None, *, all_connected: bool = False
    ) -> None:
        """Keep the board's player entries in step with the roster.

        A seat that has gone leaves the board whichever way the preference is set —
        there is nothing left to show. A seat that has merely **disconnected** stays:
        a dropped laptop is not a player leaving the fight, and their place in the
        turn order should be there when they redial.

        Whether a seat *arrives* on it is the preference: on, **connecting** is
        enough — the moment a seat comes online (a join, or a redial), not on every
        roster, so an offline seat is never seated for them and a player the GM took
        off the board stays off until they next connect. *all_connected* seats every
        connected player at once, which is what turning the preference on means. Off,
        the GM puts them there by hand and their card grows an eye to do it with.

        Turning the preference off does **not** clear whoever is already on the
        board. "Put every player in the Scene automatically", unticked, says *from
        now on I decide* — not *throw out the fight in progress*. What it changes
        at once is that the eyes appear, so the GM can decide.

        The preference is re-read here rather than once at startup. This runs on
        every roster — a join, a leave, and every snapshot a player pushes — and
        :meth:`changeEvent` catches the one case that would otherwise wait for one:
        a GM who changes it in Settings and comes straight back to a quiet table.
        """
        self._scene_auto_players = storage.gm_scene_auto_players()
        if connected is None:
            connected = {pid for pid, card in self._cards.items() if card.connected}
        arrived = set(connected) if all_connected else connected - self._connected_seats
        self._connected_seats = set(connected)
        before = list(self._scene)
        self._scene = [e for e in self._scene if e.kind != SCENE_PLAYER or e.source in seated]
        for player_id in [p for p in self._cards if p in seated and p in arrived]:
            if self._scene_auto_players and self._scene_entry_for(SCENE_PLAYER, player_id) is None:
                self._scene.append(
                    _SceneEntry(
                        ref=new_id(8),
                        kind=SCENE_PLAYER,
                        source=player_id,
                        disposition=DISPOSITION_PLAYER,
                    )
                )
        for entry in before:
            if entry.kind == SCENE_PLAYER and entry not in self._scene:
                self._player_initiative.pop(entry.source, None)
        self._push_scene()

    def _on_snapshot(self, player_id: str, character: object) -> None:
        """A player pushed their live sheet: remember it and restate their card."""
        if not isinstance(character, dict) or not character:
            return
        self._snapshots[player_id] = character
        card = self._cards.get(player_id)
        if card is not None:
            card.set_character(character)
        # An Impress being set up reads this player's resist options off the same
        # snapshot, so a sheet that just changed must not leave it on stale numbers.
        entry = self._scene_entry_for(SCENE_PLAYER, player_id)
        if entry is not None and getattr(self, "_impress", None) is not None:
            for window in self._impress.windows.values():
                window.set_party_character(entry.ref, Character.from_dict(character))
        # The area windows too: a primary player's Defense decides the Hit tick.
        if entry is not None and getattr(self, "_area", None) is not None:
            for window in self._area.windows.values():
                window.set_party_character(entry.ref, Character.from_dict(character))
        # Their conditions and their picture are on the board too, and a snapshot
        # is the only warning that either moved.
        if self._scene_entry_for(SCENE_PLAYER, player_id) is not None:
            self._push_scene()

    def _open_player_sheet(self, player_id: str) -> None:
        """Show a player's character in a read-only sheet.

        Built fresh from the newest snapshot every time, so clicking again on a
        card whose player has since changed something re-reads it — the sheet
        itself is a view of the moment it was opened, while the card stays live.
        """
        snapshot = self._snapshots.get(player_id)
        if not snapshot:
            return
        from mm_companion.ui.main_window import MainWindow

        previous = self._player_windows.pop(player_id, None)
        if previous is not None:
            previous.close()
        window = MainWindow(
            character=self._character_from_snapshot(snapshot),
            gm_view=True,
            pin_target=True,
        )
        card = self._cards.get(player_id)
        if card is not None:
            self._attach_pin_sheet(window, card)
        self._player_windows[player_id] = window
        window.show()
        window.raise_()

    @staticmethod
    def _character_from_snapshot(snapshot: dict) -> Character:
        """Rebuild a character from a snapshot, restoring its portrait.

        The portrait travels as a base64 thumbnail (image_path was stripped on the
        wire); decode it to a temp file so the read-only sheet's image block — which
        reads a path — shows the picture.
        """
        from mm_companion.ui.session_portrait import portrait_to_tempfile

        character = Character.from_dict(snapshot)
        path = portrait_to_tempfile(snapshot.get("portrait"))
        if path is not None:
            character.image_path = path
        return character

    # -- removing a player --------------------------------------------------

    def _remove_player(self, player_id: str) -> None:
        """Kick a player out of the session, once the GM confirms it.

        The kick drops the seat and closes the socket; the server broadcasts a new
        roster without it, so :meth:`_show_roster` reconciles the card away. Any
        read-only sheet the GM had open for that player is closed too.
        """
        card = self._cards.get(player_id)
        name = card.display_name() if card is not None else "this player"
        # Do not promise to disconnect someone who already is: a GM clearing a
        # seat after the session wants to be told what actually happens, and
        # "they will be disconnected" reads as a warning not to.
        online = card is not None and card.connected
        detail = (
            "They will be disconnected."
            if online
            else "They are already offline; this clears their seat."
        )
        confirm = QMessageBox.question(
            self,
            "Remove player" if online else "Remove seat",
            f"Remove {name} from the session? {detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if not self._bridge.kick(player_id):
            self._show_notice(f"{name} could not be removed.", theme.color("tint.worse"))
            return
        window = self._player_windows.pop(player_id, None)
        if window is not None:
            window.close()
        self._show_notice(f"{name} was removed from the session.", theme.color("accent"))

    # -- fast-apply conditions ---------------------------------------------

    def _apply_condition(self, player_id: str, condition_id: str, parameter: object) -> None:
        self._send_condition("apply", player_id, condition_id, parameter)

    def _remove_condition(self, player_id: str, condition_id: str, parameter: object) -> None:
        self._send_condition("remove", player_id, condition_id, parameter)

    def _set_hero_points(self, player_id: str, value: int) -> None:
        """Order a player's hero-point total changed on their live sheet.

        Like a condition, the GM only asks: the command goes down the player's
        connection, their app writes the value, and the card's pips move only once
        the snapshot comes back — so a command that did not land stays visible.
        """
        who = self._player_name(player_id)
        if not self._bridge.set_hero_points(player_id, value):
            self._show_notice(
                f"{who} is not connected, so their hero points were not changed.",
                theme.color("tint.worse"),
            )

    def _send_condition(
        self, action: str, player_id: str, condition_id: str, parameter: object
    ) -> None:
        """Order one condition change on a player's live sheet.

        The GM never edits the player's character here — the command goes down
        their connection and their app applies it through
        :func:`~mm_companion.core.rules.apply_condition`, exactly as their own "+"
        would. The chips on this card only move once the snapshot comes back, so
        a command that quietly failed is visible rather than assumed.
        """
        subject = str(parameter) if parameter else None
        name = self._condition_name(condition_id, subject)
        who = self._player_name(player_id)
        send = self._bridge.apply_condition if action == "apply" else self._bridge.remove_condition
        if not send(player_id, condition_id, subject):
            self._show_notice(
                f"{who} is not connected, so “{name}” was not sent.", theme.color("tint.worse")
            )
            return
        verb = "Applied" if action == "apply" else "Removed"
        self._show_notice(f"{verb} “{name}” on {who}.", theme.color("accent"))

    def _condition_name(self, condition_id: str, parameter: str | None) -> str:
        """The condition as the GM picked it, named the way a chip names it."""
        record = next((c for c in self._data.conditions if c.id == condition_id), None)
        return condition_display_name(
            AppliedCondition(condition_id=condition_id, parameter=parameter), record
        )

    def _player_name(self, player_id: str) -> str:
        """What to call one seat.

        The **card** first: it is fed from the live roster, while ``_state`` is
        this window's own copy of the session — authoritative when hosting, and a
        snapshot from join time when the session lives on a server. Asking the
        state first put a stale name (or "That player") on the Scene for a remote
        GM, which is the one place a wrong name is read by the whole table.
        """
        card = self._cards.get(player_id)
        if card is not None and card.display_name():
            return card.display_name()
        slot = self._state.players.get(player_id)
        return slot.display_name if slot is not None else "That player"

    def _drop_card(self, player_id: str) -> None:
        """Remove one card from the grid (the seat itself is gone, not just offline)."""
        card = self._cards.pop(player_id, None)
        if card is None:
            return
        for index in range(self._cards_flow.count()):
            item = self._cards_flow.itemAt(index)
            if item is not None and item.widget() is card:
                self._cards_flow.takeAt(index)
                break
        discard_widget(card)
        self._forget_pins(_player_key(player_id))

    def _clear_cards(self) -> None:
        for player_id in list(self._cards):
            self._drop_card(player_id)
        self._snapshots.clear()
        self._no_players.setVisible(True)
