"""The parameters pinned to each card, and the picker that chooses them."""

from __future__ import annotations

from PySide6.QtCore import Qt

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.rules import (
    PinRef,
    default_pins,
)
from mm_companion.ui.gm.model import (
    _npc_key,
    _player_key,
)
from mm_companion.ui.pin_picker import PinPickerDialog
from mm_companion.ui.widgets import (
    resurface,
)


class GMPinsMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    # -- pinned parameters ---------------------------------------------------

    def _pins_for(self, key: str, kind: str) -> list[PinRef]:
        """This card's strip, seeding it from the defaults the first time.

        Seeded and *stored* on first sight rather than left to fall through to the
        defaults on every read: once a card exists, its strip is the GM's, and a
        later change to ``gm_default_pins`` must not silently rearrange the cards
        already on the board.
        """
        if key not in self._pins:
            self._pins[key] = default_pins(kind, storage.gm_default_pins())
            self._persist_pins()
        return self._pins[key]

    def reseed_pins_from_defaults(self) -> None:
        """Throw every card's own strip away and seed them all from the defaults.

        The deliberate exception to the rule :meth:`_pins_for` keeps — a card's
        strip is the GM's once the card exists — so nothing calls this but the GM
        Mode settings page, on an explicit, confirmed ask.

        Cleared first, then re-seeded card by card: what that drops is the entries
        for cards *not* on the board (a player who left, an NPC file not loaded),
        which is the point. Those are gone from the settings file, so they seed
        from the new defaults the next time they are seen.
        """
        defaults = storage.gm_default_pins()
        self._pins.clear()
        cards = [(_player_key(pid), "player", card) for pid, card in self._cards.items()]
        cards += [
            (_npc_key(name), "npc", entry.card)
            for name, entry in self._npc_state.items()
            if entry.card is not None
        ]
        for card_key, kind, card in cards:
            refs = default_pins(kind, defaults)
            # set_pins is silent by design (a load, not an edit), so the strip is
            # stored explicitly — which is also what restates an open picker or sheet.
            card.pins.set_pins(refs)
            self._store_pins(card_key, refs)
        self._persist_pins()

    def _store_pins(self, card_key: str, refs: object) -> None:
        """A card's strip was edited — remember it, and restate it everywhere.

        Both a picker and an opened sheet offer to pin *and* to unpin, and each
        picks which from what it was last told is on the card. So a change made in
        any of the three has to reach the other two, or a menu ends up offering to
        pin something that is already there.
        """
        self._pins[card_key] = list(refs) if isinstance(refs, list) else []
        self._persist_pins()
        picker = self._pin_pickers.get(card_key)
        if picker is not None:
            picker.set_pinned(self._pins[card_key])
        for window in self._pin_sheets.get(card_key, ()):
            window.sheet.set_pinned(self._pins[card_key])

    def _persist_pins(self) -> None:
        """Write every card's strip, **including the empty ones**.

        An empty list is not the absence of an answer: it is a GM who took every
        chip off that card on purpose, and :meth:`_pins_for` seeds the defaults
        for a key it has never seen. Dropping the empty ones to keep the settings
        file tidy meant a cleared strip was back at four chips after a restart.
        """
        storage.update_settings(
            gm_pins={key: [ref.to_dict() for ref in refs] for key, refs in self._pins.items()}
        )

    def _open_player_pin_picker(self, player_id: str) -> None:
        card = self._cards.get(player_id)
        if card is None or card.character is None:
            return
        self._open_pin_picker(_player_key(player_id), card, card.character, card.display_name())

    def _open_npc_pin_picker(self, name: str) -> None:
        entry = self._npc_state.get(name)
        if entry is None or entry.card is None:
            return
        self._open_pin_picker(_npc_key(name), entry.card, entry.character, entry.summary.name)

    def _open_pin_picker(self, card_key: str, card, character: Character, title: str) -> None:
        """Open (or raise) the picker for one card, wired to that card's strip.

        Modeless, so the GM can pin several things in a row and watch the card
        fill in — which means the dialog outlives this call and has to be kept
        referenced, and dropped again when it closes.
        """
        existing = self._pin_pickers.get(card_key)
        if existing is not None:
            resurface(existing)
            return
        picker = PinPickerDialog(character, self._data, card.pins.pins, self, title=title)
        picker.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        picker.pinRequested.connect(card.pins.add_pin)
        picker.unpinRequested.connect(card.pins.remove_ref)
        picker.destroyed.connect(lambda *_: self._pin_pickers.pop(card_key, None))
        self._pin_pickers[card_key] = picker
        picker.show()

    def _attach_pin_sheet(self, window, card) -> None:
        """Wire a sheet opened from *card* to that card's strip, both directions.

        The sheet's rows pin and unpin onto the card; the card tells the sheet what
        is on it, now and on every later change, so a sheet left open beside the
        card never goes stale.
        """
        card_key = self._card_key(card)
        window.pinRequested.connect(card.pins.add_pin)
        window.unpinRequested.connect(card.pins.remove_ref)
        window.sheet.set_pinned(card.pins.pins)
        sheets = self._pin_sheets.setdefault(card_key, [])
        sheets.append(window)
        window.destroyed.connect(lambda *_: self._drop_pin_sheet(card_key, window))

    def _drop_pin_sheet(self, card_key: str, window) -> None:
        sheets = self._pin_sheets.get(card_key)
        if sheets is None:
            return
        self._pin_sheets[card_key] = [w for w in sheets if w is not window]

    @staticmethod
    def _card_key(card) -> str:
        """A card's pin-store key, from whichever identity that card carries."""
        name_key = getattr(card, "name_key", "")
        return _npc_key(name_key) if name_key else _player_key(card.player_id)

    def _forget_pins(self, card_key: str) -> None:
        """Drop a card's strip for good — the seat or the NPC is gone."""
        picker = self._pin_pickers.pop(card_key, None)
        if picker is not None:
            picker.close()
        self._pin_sheets.pop(card_key, None)
        if self._pins.pop(card_key, None) is not None:
            self._persist_pins()
