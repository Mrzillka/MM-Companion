"""The resistances block: a table of the derived resistance defenses.

Resistances are *derived* from a base trait (Toughness and Fortitude from Stamina,
Will from Awareness, Dodge from the Defense combat trait), and the block shows a row
as the **three numbers** that make it, because three different things move them and a
single total says nothing about which:

* **Ability** — the base, :func:`~mm_companion.core.rules.resistance_base`. An
  Enhanced Trait on Stamina raises *this*, and the cell tints and names the power.
* **Rank** — the spin box, and the only number the player owns: what was bought on
  top of the base, starting at 0. It is exactly what the model stores, so an edit is
  a write and nothing else. Each rank costs a power point and a negative rank refunds
  one (a hero can be flimsier than their Stamina suggests).
* **Total** — :func:`~mm_companion.core.rules.resistance_total`: ability + rank +
  every standing bonus (Protection, worn armour), with a condition's overlay painted
  on top. This is the number the game asks for, so unlike the Abilities block's it is
  never blank.

The spin box used to hold the *total* instead, with the model storing the difference
from a base the widget had to subtract back out on every edit. Three numbers cost
that trick nothing and end it: the base is a column now, so it can move — a power
raising Stamina, or Dodge's own base moving when Defence is bought — without ever
re-seeding a spin box the player is typing in, and without a clamped display being
able to rewrite the ranks underneath.

Neither read-out column is ever computed here (see the standing rule: widgets do not
do game maths). :meth:`refresh_readouts` asks ``core.rules`` for both and hands them
to the shared painter; the sheet calls it whenever an ability, a power, a piece of
gear or a condition changes.
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QSpinBox, QTableWidgetItem, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, Resistance
from mm_companion.core.rules import (
    PIN_RESISTANCE,
    PinRef,
    RollSpec,
    resistance_base,
    resistance_base_bonus,
    resistance_condition_effect,
    resistance_points_spent,
    resistance_roll,
    resistance_total,
    trait_bonuses,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.stat_table import (
    PinMenuState,
    apply_value_column,
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
        # The two read-out columns: the base this resistance derives from, and the
        # total everything nets to. Both are filled from core, never from the spins.
        self._resistance_base: dict[str, QTableWidgetItem] = {}
        self._resistance_total: dict[str, QTableWidgetItem] = {}

        layout = QVBoxLayout(self)
        # No margin round the table — the section's border is the table's border.
        # See :class:`~mm_companion.ui.sections.abilities.AbilitiesSection`.
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = build_stat_table(
            data.resistances,
            self._resistances,
            self._resistance_total,
            character.resistances,
            self._on_resistance_changed,
            data.costs.trait_range("resistance"),
            base_store=self._resistance_base,
            roll_spec=self._roll_spec,
            roll_sink=self.rollRequested.emit,
            load_sink=self.loadRequested.emit,
            pin_ref=self._pin_ref,
            pin_sink=self.pinRequested.emit,
            unpin_sink=self.unpinRequested.emit,
            pins=self._pins,
        )
        layout.addWidget(self.table)
        # Abilities and Resistances share a row and so a height, and this is the
        # shorter of the two: the room left over goes into this table's own rows
        # (:meth:`~mm_companion.ui.sections.row_table.AutoHeightTable.sync_row_stretch`)
        # rather than into a stretch under it, so the two read as one pair of tables
        # rather than as a full block beside a half-empty one.

        self.refresh_ranks()
        self.refresh_readouts()
        self.refresh_cost()

    def _on_resistance_changed(self, key: str, value: int) -> None:
        # The spin box holds the bought ranks and the model stores the bought ranks,
        # so the edit is the write. Dodge derives from the Defence trait, though, so
        # buying a rank of one resistance can still move another's readout.
        self._character.resistances[key] = value
        self.refresh_readouts()
        self.refresh_cost()
        self.changed.emit()

    def follow_ability_change(self) -> None:
        """Restate the readouts after an ability moved.

        A resistance derived from the changed ability follows it — in the Ability
        column and the Total, never in the ranks, which are the player's and are not
        touched here.
        """
        self.refresh_readouts()

    def reseed(self) -> None:
        """Restate the whole block from the model — the sheet put an earlier state back.

        The readouts are strictly already covered: ``ability-changed`` reaches
        :meth:`follow_ability_change`. The *ranks* are not — nothing else republishes
        them — so a restore has to re-seed the spins, and spelling the rest out keeps
        the readouts from being an accident of which topics a restore happens to
        publish.
        """
        self.refresh_ranks()
        self.refresh_readouts()
        self.refresh_cost()

    def refresh_ranks(self) -> None:
        """Re-seed the spin boxes from the model — the bought ranks, and nothing else.

        Only a load or an undo needs this: nothing derived reaches the ranks any more,
        so an ability moving no longer re-seeds a control the player may be typing in.
        Signals are blocked so a restored value doesn't count as a fresh edit, and
        :func:`~mm_companion.ui.sections.stat_table.set_stat_value` stretches the spin
        box rather than let a rank past its ceiling clamp — a clamp here would silently
        refund the points a narrower ruleset happens to disagree with.
        """
        for res in self._data.resistances:
            spin = self._resistances.get(res.key)
            if spin is None:
                continue
            blocker = QSignalBlocker(spin)
            set_stat_value(spin, int(self._character.resistances.get(res.key, 0)))
            del blocker

    def refresh_readouts(self) -> None:
        """Restate both read-out columns from ``core.rules``.

        The **Ability** column is the derived base, tinted by whatever is raising the
        trait it comes from (:func:`~mm_companion.core.rules.resistance_base_bonus` —
        an Enhanced Stamina, or Defence's own boost reaching Dodge). The **Total** is
        :func:`~mm_companion.core.rules.resistance_total`, so it carries the ranks, the
        base and every standing bonus at once, with the conditions painted over it —
        Hit's penalty on Toughness, Vulnerable/Defenseless halving or zeroing an active
        defense. The condition overlay is deliberately on the Total alone: it is
        display-only and never part of the build, and a base a condition had rewritten
        would look like something the character *has*.
        """
        bonuses = trait_bonuses(self._character, self._data).get("resistance", {})
        cond_effects = {
            res.key: resistance_condition_effect(self._character, self._data, res.key)
            for res in self._data.resistances
        }
        apply_value_column(
            self._resistance_base,
            {res.key: self._base_value(res) for res in self._data.resistances},
            {
                res.key: resistance_base_bonus(self._character, self._data, res.key)
                for res in self._data.resistances
            },
        )
        apply_value_column(
            self._resistance_total,
            {
                res.key: resistance_total(self._character, self._data, res.key)
                for res in self._data.resistances
            },
            bonuses,
            cond_effects,
        )

    def _base_value(self, res: Resistance) -> int | None:
        """This resistance's base, or ``None`` where there is no trait to derive from.

        Defence is bought outright rather than derived, so its Ability cell reads as a
        dash instead of the 0 :func:`~mm_companion.core.rules.resistance_base` returns
        for it — a 0 there claims a base that is merely low.
        """

        if not res.ability:
            return None
        return resistance_base(self._character, self._data, res.key)

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
