"""Spending a power in play: extra effort, power stunts, limited uses and their counters,
and the pools a Variable or a Dynamic array draws on.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
)

from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import (
    USE_POWER_STUNT,
    array_pool_points,
    clear_power_extra_effort,
    effect_counts_uses,
    effect_is_spent,
    leaf_powers,
    live_array_effects,
    power_pool_points,
    spend_extra_effort,
)
from mm_companion.ui import theme
from mm_companion.ui.extra_effort import ExtraEffortDialog
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.sections.power_parts.helpers import _pool_is_split
from mm_companion.ui.sections.power_parts.widgets import _SplitGroup
from mm_companion.ui.widgets import (
    help_markup,
    muted_style,
)


class PowersUsesMixin:
    """Part of :class:`~mm_companion.ui.sections.powers.PowersSection`; see the module docstring."""

    def use_extra_effort(
        self, use, power: Power, effect: PowerEffectInstance, effect_name: str
    ) -> bool:
        """Confirm one use of Extra Effort against this effect, and charge it.

        The push itself is *runtime* state on the effect, so it rides the same signal a
        card toggle does; the fatigue is applied to the shared model by
        :func:`~mm_companion.core.rules.spend_extra_effort`, through the very condition
        resolver the Conditions block applies with. Returns ``False`` when the dialog was
        cancelled, so nothing was spent and nothing was gained.

        A **power stunt** is the one use that cannot be confirmed in a single dialog: it
        is a whole alternate effect the player has yet to build, so it opens the
        constructor first and charges the effort when something comes back
        (:meth:`_open_stunt`). ``True`` there means "the constructor is open", not "it
        was spent" — nothing is until the build is confirmed.
        """

        if use.id == USE_POWER_STUNT:
            self._open_stunt(use, power, effect, effect_name)
            return True

        dialog = ExtraEffortDialog(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        outcome = spend_extra_effort(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            doubled=dialog.doubled,
            determination=dialog.determination,
        )
        if dialog.spend_hero_point:
            self.heroPointRequested.emit(-1)
        self.noteRequested.emit(outcome.note)
        self._rebuild_list()
        self.runtimeChanged.emit()
        self.conditionsChanged.emit()
        return True

    def _open_stunt(self, use, power: Power, effect: PowerEffectInstance, effect_name: str) -> None:
        """Open the constructor to build a stunt of ``power``.

        The build comes **first** and the effort is charged on the way back
        (:meth:`_on_stunt_saved`): a player who closes the constructor without saving has
        changed their mind, and charging them a rung of the fatigue ladder for a stunt
        that does not exist would be the app inventing a rule.
        """

        window = PowerConstructorWindow(self._data, character=self._character)
        window.rollRequested.connect(self.rollRequested)
        window.powerSaved.connect(
            lambda built, u=use, p=power, e=effect, n=effect_name: self._on_stunt_saved(
                built, u, p, e, n
            )
        )
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_stunt_saved(
        self, built: Power, use, power: Power, effect: PowerEffectInstance, effect_name: str
    ) -> bool:
        """Charge the Extra Effort, then put the finished stunt on the sheet as its own card.

        Cancelling the cost dialog here drops the build rather than adding it free: the
        dialog is the "yes, spend it" step, and a stunt nobody paid for is not a stunt.

        The stunt is appended at the top level even when its source sits inside a group —
        it is a card of its own, marked with what it came from, rather than a member of
        anybody's array. It costs no points and is not saved (see ``Power.stunt_of``), so
        this rides ``runtimeChanged`` rather than ``changed``.
        """

        dialog = ExtraEffortDialog(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        built.stunt_of = power.id
        self._character.powers.append(built)
        outcome = spend_extra_effort(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            doubled=dialog.doubled,
            determination=dialog.determination,
        )
        if dialog.spend_hero_point:
            self.heroPointRequested.emit(-1)
        self.noteRequested.emit(outcome.note)
        self._rebuild_list()
        self.runtimeChanged.emit()
        self.conditionsChanged.emit()
        return True

    def count_use(self, spec: object) -> None:
        """A card's die was thrown and it was a use — the ``use-spent`` handler.

        Counted *after* the roll, so it was made at the rank the effect had when it was
        used: a Fades Blast 6 fires at 6 and then reads 5. Raised by the roller rather
        than counted here at the click, because only the roller knows the die really was
        thrown — a click while it still tumbles is refused, and must not count.
        """

        by_id = {power.id: power for power in leaf_powers(self._character.powers)}
        for power_id, index in getattr(spec, "spends", ()):
            power = by_id.get(power_id)
            if power is not None:
                self.spend_uses(power, (index,))

    def spend_uses(self, power: Power, indices) -> bool:
        """Count one use of each effect of *power* at *indices*; ``False`` if none counts."""

        counted = False
        for index in indices:
            if 0 <= index < len(power.effects):
                effect = power.effects[index]
                if effect_counts_uses(effect, self._data) and not effect_is_spent(
                    effect, self._data
                ):
                    effect.uses += 1
                    counted = True
        if counted:
            self._rebuild_list()
            self.runtimeChanged.emit()
        return counted

    def recharge(self, power: Power) -> bool:
        """Give every Short-Term and Fades effect of *power* its uses back; ``False`` if none."""

        recharged = False
        for effect in power.effects:
            if effect.uses:
                effect.uses = 0
                recharged = True
        if recharged:
            self._rebuild_list()
            self.runtimeChanged.emit()
        return recharged

    def clear_extra_effort(self, power: Power) -> bool:
        """Take back every rank Extra Effort pushed into this power; ``False`` if none."""

        if not clear_power_extra_effort(power):
            return False
        self._rebuild_list()
        self.runtimeChanged.emit()
        return True

    def fill_counter_menu(self, menu: QMenu, specs: list) -> None:
        """Add one entry per counter roll to ``menu``, each asking the roller for it."""

        for spec in specs:
            action = menu.addAction(f"{spec.label}  +{spec.modifier}")
            action.setToolTip(spec.hint)
            action.triggered.connect(lambda _checked=False, s=spec: self.rollRequested.emit(s))

    def _pool_readout(self, group: PowerGroup) -> QWidget | None:
        """How much of a Dynamic array's pool is currently spread, or ``None``.

        A readout, not a control: the split is made on each member's dial, and the
        header says the one number no dial can, how much of the pool is spoken for.
        Unassigned points are legal and not tinted; a split *over* the pool (which a
        rebuild can cause) is flagged.

        **It is there before the first split, too**: it used to appear only once
        something was assigned, hiding the pool from the gesture that spends it. It is
        also where a Dynamic array says which regime it is in (one alternate, or a
        split).

        It is handed to the array's :class:`_SplitGroup`, which restates it whenever a
        member's dial is set.
        """

        if group.mode != STRUCTURE_ARRAY or not any(c.dynamic for c in group.children):
            return None
        pool = array_pool_points(group, self._data)
        if pool <= 0:
            return None
        label = QLabel()
        label.setToolTip(
            help_markup(
                "**Dynamic:** this array's points are shared by its members, which all run at "
                "once at reduced strength.\n\nClick a pip on a member's rank scale to give it a "
                "share. The button beside this hands the whole pool back."
            )
        )
        # A group's header is built before its members' cards, so their dials have not
        # registered yet and the coordinator does not exist. Say the truth now and let
        # :meth:`_make_group_card` hand the label over once they have.
        assigned = sum(c.dynamic_points or 0 for c in group.children if c.dynamic)
        label.setText(_SplitGroup.readout_text(assigned, pool))
        label.setStyleSheet(muted_style())
        self._pool_labels[group.id] = label
        return label

    def _pool_release(self, group: PowerGroup) -> QWidget | None:
        """The one gesture that hands a split pool back, or ``None`` for an array with none.

        Otherwise the way back is sliding every member to nothing, one gesture each,
        and a split array's cards are not clickable.

        It writes what sliding every dial to zero writes: the shares go, and the
        selected alternate is switched back on through :meth:`_set_array_active`, since
        with the shares gone it is what the array runs
        (:func:`~mm_companion.core.rules.live_array_children`).

        Runtime, so one undo step and ``runtimeChanged``. Shown only for an array that
        has a split.
        """

        if not _pool_is_split(group):
            return None
        button = QPushButton("↺")
        button.setFixedWidth(int(theme.metric("column.chip-button")))
        button.setToolTip(
            "Hand the pool back — clear every member's share and return the array to "
            "one alternate at a time."
        )
        button.clicked.connect(lambda _checked=False, g=group: self._release_pool(g))
        return button

    def _release_pool(self, group: PowerGroup) -> None:
        """Clear every share in *group* and put its selected alternate back on."""

        for child in group.children:
            child.dynamic_points = None
            self._hold_member(child, None)  # the notch each was held at goes with it
        child_id = group.active_child_id or (group.children[0].id if group.children else "")
        if child_id:
            self._set_array_active(group, child_id)  # rebuilds and signals for us
            return
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _effect_pool_readout(self, power: Power) -> QWidget | None:
        """What an ``array`` power's own effects have spread of its pool, or ``None``.

        Same sentence as the group header's (:meth:`_pool_readout`, and the same
        :meth:`_SplitGroup.readout_text` writes both), one level down: the members are
        this power's effects rather than sibling cards, and the pool is what its base
        effect costs. It lands on the card's own header because that is the only place a
        power-wide fact can go — there is no group bar above a leaf card.
        """

        if power.structure != STRUCTURE_ARRAY or len(power.effects) < 2:
            return None
        if not any(effect.dynamic for effect in power.effects):
            return None
        pool = power_pool_points(power, self._data)
        if pool <= 0:
            return None
        assigned = sum(e.dynamic_points or 0 for e in power.effects if e.dynamic)
        label = QLabel(_SplitGroup.readout_text(assigned, pool))
        label.setStyleSheet(muted_style())
        label.setToolTip(
            help_markup(
                "**Dynamic:** this power's points are shared by its effects, which all run at "
                "once at reduced strength.\n\nClick a pip on an effect's rank scale to give it a "
                "share. The button beside this hands the whole pool back."
            )
        )
        self._pool_labels[power.id] = label
        return label

    def _effect_pool_release(self, power: Power) -> QWidget | None:
        """The effect-level hand-back — :meth:`_pool_release` one level down."""

        if not live_array_effects(power):
            return None
        button = QPushButton("↺")
        button.setFixedWidth(int(theme.metric("column.chip-button")))
        button.setToolTip(
            "Hand the pool back — clear every effect's share and return this power to "
            "one effect at a time."
        )
        button.clicked.connect(lambda _checked=False, p=power: self._release_effect_pool(p))
        return button

    def _release_effect_pool(self, power: Power) -> None:
        """Clear every effect's share; the *Using* picker decides again."""

        for effect in power.effects:
            effect.dynamic_points = None
            effect.current_rank = None  # the notch it was held at goes with the share
        self._rebuild_list()
        self.runtimeChanged.emit()
