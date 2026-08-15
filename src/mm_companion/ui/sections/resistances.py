"""The resistances block: a table of the derived resistance defenses.

Resistances are *derived* from a base trait (Toughness and Fortitude from Stamina,
Will from Awareness, Dodge from the Defense combat trait): a resistance's spin box
holds the **total** value, which starts equal to that base. Only the difference
from the base costs power points — raising the spin box above the base spends
points, lowering it below refunds them (:func:`~mm_companion.core.rules.resistance_base`
gives the base, and the model stores just the bought delta). So when the base trait
changes, an unmodified resistance follows it (:meth:`ResistancesSection.refresh_bases`
re-seeds the spin boxes) while a bought difference is preserved. The sheet calls
:meth:`follow_ability_change` when an ability moves.

A resistance a power raises (Protection) shows its enhanced total in green in the
Total column; :meth:`refresh_enhancements` recomputes that column and the sheet
calls it whenever a power changes.
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QSpinBox, QTableWidgetItem, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    PIN_RESISTANCE,
    PinRef,
    RollSpec,
    resistance_base,
    resistance_condition_effect,
    resistance_points_spent,
    resistance_roll,
    trait_bonuses,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.stat_table import (
    PinMenuState,
    apply_stat_effects,
    build_stat_table,
    set_stat_value,
)
from mm_companion.ui.sections.titled_section import TitledSection


class ResistancesSection(TitledSection):
    """Spin boxes for the derived resistances, backed by the shared :class:`Character`.

    Emits :attr:`changed` whenever the point build changes, so the sheet can
    recompute spent power points.
    """

    changed = Signal()

    #: A row was double-clicked — roll this resistance check. Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`; rolling is not a build edit.
    rollRequested = Signal(object)

    #: A row was clicked once — show this check in the roller's chip, ready to roll.
    loadRequested = Signal(object)

    #: A row was right-clicked and pinned — carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`. Only ever raised on a sheet a
    #: GM opened from a card (see ``set_pin_target``); the same non-build promise as
    #: the two roll signals.
    pinRequested = Signal(object)
    #: The same, for a row that was already on the card.
    unpinRequested = Signal(object)

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._data = data
        self._character = character
        self._locked = False
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        self._resistances: dict[str, QSpinBox] = {}
        self._resistance_enh: dict[str, QTableWidgetItem] = {}

        layout = QVBoxLayout(self)
        self.table = build_stat_table(
            data.resistances,
            self._resistances,
            self._resistance_enh,
            character.resistances,
            self._on_resistance_changed,
            data.costs.trait_range("resistance"),
            roll_spec=self._roll_spec,
            roll_sink=self.rollRequested.emit,
            load_sink=self.loadRequested.emit,
            pin_ref=self._pin_ref,
            pin_sink=self.pinRequested.emit,
            unpin_sink=self.unpinRequested.emit,
            pins=self._pins,
        )
        layout.addWidget(self.table)
        # Abilities and Resistances are one fixed size, sized to the taller of the
        # two, so the shorter one has room left over; give it to the bottom rather
        # than let the layout centre the table and leave the two blocks' headers on
        # different lines.
        layout.addStretch()

        # The spin boxes hold the *total* (base + bought), so display the base on
        # top of the stored delta now that the table exists.
        self.refresh_bases()
        self.refresh_enhancements()
        self.refresh_cost()

    def _on_resistance_changed(self, key: str, value: int) -> None:
        # The spin box holds the total; only the difference from the derived base is
        # bought (and costs/refunds points), so store that delta on the model.
        base = resistance_base(self._character, self._data, key)
        self._character.resistances[key] = value - base
        # Dodge derives from the Defense trait, so changing one resistance can move
        # another; re-seed them all (guarded, so this doesn't re-enter).
        self.refresh_bases()
        self.refresh_enhancements()
        self.refresh_cost()
        self.changed.emit()

    def follow_ability_change(self) -> None:
        """Re-seed the bases and enhancement labels after an ability moved.

        A resistance derived from the changed ability follows it (its bought delta
        is kept), and its "→ total" moves with the new base.
        """
        self.refresh_bases()
        self.refresh_enhancements()

    def reseed(self) -> None:
        """Restate the whole block from the model — the sheet put an earlier state back.

        Strictly this is already covered: ``ability-changed`` reaches
        :meth:`follow_ability_change`, which re-seeds the spins. Spelling it out keeps
        that from being an accident of which topics a restore happens to publish.
        """
        self.refresh_bases()
        self.refresh_enhancements()
        self.refresh_cost()

    def refresh_bases(self) -> None:
        """Show each resistance's total (derived base + bought delta) in its spin box.

        The model stores only the bought delta, so the displayed total is
        :func:`~mm_companion.core.rules.resistance_base` plus that delta. Signals are
        blocked while re-seeding so following the base doesn't count as a fresh edit,
        and :func:`~mm_companion.ui.sections.stat_table.set_stat_value` stretches the
        spin box rather than let a total past its ceiling clamp — a clamped display
        would make the next edit recompute the delta from the wrong number.
        """
        for res in self._data.resistances:
            spin = self._resistances.get(res.key)
            if spin is None:
                continue
            base = resistance_base(self._character, self._data, res.key)
            bought = self._character.resistances.get(res.key, 0)
            blocker = QSignalBlocker(spin)
            set_stat_value(spin, base + bought)
            del blocker

    def refresh_enhancements(self) -> None:
        """Recompute each resistance's Total cell from standing boosts and conditions.

        Conditions overlay Hit's penalty on Toughness and Vulnerable/Defenseless
        halving/zeroing on the active defenses (Dodge, Defence).
        """
        bonuses = trait_bonuses(self._character, self._data).get("resistance", {})
        cond_effects = {
            res.key: resistance_condition_effect(self._character, self._data, res.key)
            for res in self._data.resistances
        }
        apply_stat_effects(self._resistances, self._resistance_enh, bonuses, cond_effects)

    def refresh_cost(self) -> None:
        """Re-title the block with its current PP subtotal (also driven by a homebrew
        cost-rate change, via ``cost-rates-changed``)."""
        self.set_priced_title("Resistances", resistance_points_spent(self._character, self._data))

    def _roll_spec(self, key: str) -> RollSpec:
        """This resistance's check, built fresh at click time so it is never stale."""
        return resistance_roll(self._character, self._data, key)

    def _pin_ref(self, key: object) -> PinRef:
        """The pin that names this row — the same key the roll is built from."""
        return PinRef(PIN_RESISTANCE, str(key))

    def set_pin_target(self, enabled: bool) -> None:
        """Whether this block's rows offer to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so a row can offer Unpin."""
        self._pins.set_pinned(refs)

    def set_locked(self, locked: bool) -> None:
        """Make the resistance spin boxes read-only labels (locked) or editable."""
        self._locked = locked
        for spin in self._resistances.values():
            set_widget_locked(spin, locked)
