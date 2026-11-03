"""The rank dial a power runs at below its full rank, and the selector that picks an
array's live effect.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget,
)

from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import (
    active_array_child,
    active_array_effect_index,
    effect_current_rank,
    effect_has_rank_dial,
    effect_stands,
    live_array_effects,
    live_powers,
    power_effects_are_array,
    size_steps,
)
from mm_companion.ui.cards import (
    effect_title,
)
from mm_companion.ui.sections.power_parts.widgets import _EffectSelector, _RankDial


class PowersDialsMixin:
    """Part of :class:`~mm_companion.ui.sections.powers.PowersSection`; see the module docstring."""

    # -- the array's live effect ------------------------------------------
    def _effect_selector(self, power: Power, interactive: bool) -> QWidget | None:
        """The picker for which of an array power's own effects is in use; ``None``
        for every power that is not one, which is nearly all of them.

        Also ``None`` **once the power's points are split**: every effect holding a share
        is running at the same time, so the selection has stopped deciding anything and
        a picker that still moved ``active_effect`` would be a control with nothing
        visible behind it. That is the same bargain ``_activation_role`` strikes one
        level up, where a split array's member cards stop being clickable. The way back
        is the same too — slide every share to nothing.
        """

        if not power_effects_are_array(power) or live_array_effects(power):
            return None
        titles = [effect_title(effect, self._character, self._data) for effect in power.effects]
        current = active_array_effect_index(power, self._data, self._character)
        selector = _EffectSelector(titles, current, interactive)
        selector.effectPicked.connect(lambda index, p=power: self._on_effect_picked(p, index))
        return selector

    def _on_effect_picked(self, power: Power, index: int) -> None:
        """Put an array power onto one of its own effects.

        Runtime, so it emits ``runtimeChanged`` rather than ``changed`` — the same
        bargain the rank dial and the array-member click strike. The rebuild is what
        redraws every other effect's summary as no longer contributing.
        """

        power.active_effect = index
        self._rebuild_list()
        self.runtimeChanged.emit()

    # -- the rank dial ----------------------------------------------------
    @staticmethod
    def _rank_is_shared(power: Power, parent: PowerGroup | None) -> bool:
        """Whether this whole card's ranks are decided by a share of an array's pool.

        A Dynamic member of an array group is rationed as a unit — every effect under it
        is held down by the same proportion — so none of them has a rank of its own to
        dial. It is asked of the *parent*, which is the half :meth:`_rank_dials` used to
        miss: ``live_array_effects`` answers about a power's own effects and is empty for
        a leaf card, so a Growth that was a Dynamic member kept its Size dial and fought
        its Share dial for the same number.

        Necessary but **not sufficient**: whether the share dial actually gets built is
        :meth:`_share_dial`'s answer, and :meth:`_rank_dials` takes it as *shared*. This
        alone stood a card's rank dial down for every Dynamic member, including the ones
        the share dial then declined to draw for (a pool of nothing, a member costing
        nothing, a ladder with one rung) — which left the card with neither control and
        no way to turn the power up at all.
        """

        return parent is not None and parent.mode == STRUCTURE_ARRAY and power.dynamic

    def _rank_dials(
        self,
        power: Power,
        parent: PowerGroup | None,
        interactive: bool,
        shared: bool | None = None,
    ) -> list[QWidget]:
        """One :class:`_RankDial` per effect that has ranks worth choosing between.

        The rule is :func:`~mm_companion.core.rules.effect_has_rank_dial`: the
        constructor's *Add a rank dial* checkbox when the player has touched it, the
        ruleset's answer when not (yes for anything with a size readout, so a mod's
        size effect gets a ladder too). The block names neither Growth nor Damage.

        A single-rank effect gets nothing (that dial would just be the card's switch);
        a size effect is measured by its *rungs* there, since a Growth 1 still climbs.

        **An effect whose rank a share decides gets nothing here**, whether the share
        is its own power's or the card's: the share dial is the only control. Two
        dials claiming one rank made a Growth's ladder snap back, this one writing a
        rank the share clamped away and the clamp staying as a floor.

        The caption names the *effect* only when the power has more than one.
        """
        # *shared* is whether a share dial was really drawn for this card. Defaulted from
        # the flag alone so a caller that has not built one can still ask, but the card
        # passes the truth: a member whose share dial was declined keeps its rank dial.
        if self._rank_is_shared(power, parent) and (shared is not False):
            return []  # the whole card is rationed; see _share_dial
        dials: list[QWidget] = []
        for effect in power.effects:
            if any(e is effect for e in live_array_effects(power)):
                continue  # its rank is its share; see _share_dial
            steps = size_steps(power, effect, self._character, self._data)
            sized = len(steps) >= 2
            if not effect_has_rank_dial(effect, self._data) or not (sized or effect.rank > 1):
                continue
            caption = "Size" if sized else "Rank"
            if len(power.effects) > 1:
                caption = effect_title(effect, self._character, self._data)
            # Where the dial stands. A *size* effect is positioned by whether it is
            # standing on the sheet — a Growth that is switched off is nowhere, not at
            # rank 1. An instant effect (a Damage) never "stands" at all, so asking the
            # same question would peg every blast card at Off; what matters there is
            # simply whether the card is switched on.
            standing = (
                effect_stands(power, effect, self._data, self._character)
                if sized
                else self._power_is_active(power)
                and self._character is not None
                and any(p is power for p in live_powers(self._character.powers))
            )
            dial = _RankDial(
                caption,
                effect.rank,
                effect_current_rank(effect, self._data, self._character) if standing else 0,
                self._dial_labels(steps),
                interactive,
            )
            dial.rankPicked.connect(
                lambda rank, p=power, e=effect, g=parent: self._on_rank_dialled(p, e, g, rank)
            )
            dials.append(dial)
        return dials

    @staticmethod
    def _dial_labels(steps) -> dict[int, str]:
        """What each notch of the dial *is*, where a plain rank number won't do.

        A size effect's notches are named by the size the wielder becomes, one entry per
        rank rather than per rung: the Size Table clamps several ranks into one category
        near its ends, and a dial that skipped them would refuse to stop where the
        player put it. Zero always reads "Off"; anything the map doesn't cover falls back
        to its bare rank.
        """
        labels = {0: "Off"}
        for step in steps:
            for rank in range(step.rank, step.last_rank + 1):
                labels[rank] = step.category
        return labels

    def _on_rank_dialled(
        self, power: Power, effect: PowerEffectInstance, parent: PowerGroup | None, rank: int
    ) -> None:
        """Hold an effect at *rank* — or, at zero, switch the power off.

        The dial does what a click on the card would, then lands on the notch asked
        for: on a dormant power it wakes it at that rank, and zero switches it off.
        The exception is an array's live member: an array always keeps one member
        live, so dialling it to zero must not switch the whole array off.

        The rank is written before any activation path, so whichever one runs rebuilds
        the cards with it already in place.
        """
        role = self._activation_role(power, parent)
        if rank <= 0:
            if role != "select":
                self._set_power_active(power, False)  # rebuilds and emits
            else:
                self._rebuild_list()  # put the dial back where the array left it
            return
        effect.current_rank = rank
        if role == "select" and isinstance(parent, PowerGroup):
            if active_array_child(parent) is not power:
                self._set_array_active(parent, power.id)  # rebuilds and emits
                return
        if not self._power_is_active(power):
            self._set_power_active(power, True)  # rebuilds and emits
            return
        self._rebuild_list()
        self.runtimeChanged.emit()
