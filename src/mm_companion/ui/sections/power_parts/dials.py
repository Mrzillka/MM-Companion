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

        One way in, and the block names neither Growth nor Damage:
        :func:`~mm_companion.core.rules.effect_has_rank_dial`, which is the *Add a rank
        dial* checkbox in the constructor's Extended settings when the player has
        touched it and the ruleset's own answer when they have not — and the ruleset
        says yes to anything carrying a size readout, so a mod's own size effect gets a
        ladder without touching this file. That is the whole of the rule now: a Growth's
        checkbox used to be a control that changed nothing, because a size effect got its
        dial whatever the box said.

        A single-rank effect gets nothing: dialling a Growth 1 is exactly the card's own
        on/off switch, and a second way to press it is not a choice. A size effect is
        measured by its *rungs* rather than its rank there, since the ladder a Growth 1
        climbs is real even though its rank is one.

        **An effect whose rank a share decides gets nothing here** — whether the share
        is held by its own power (a Dynamic array of effects) or by the card it sits on
        (a Dynamic member of an array group). Its rank is not the player's to set
        directly; the share dial is the control that moves it, and it is deliberately the
        only one. Two dials claiming one rank is what made a Growth's ladder snap back:
        this one wrote a rank the share then clamped away, and the clamped value stayed
        behind as a floor the share could not lift.

        The caption names the *effect* only when the power has more than one, which is
        the same bargain the dice footer's labels strike: on the ordinary single-effect
        Growth card "Size" is the whole story, while a Growth linked to a Shrinking needs
        to say which dial is which.
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

        The dial does exactly what a click on the card would have done, and then lands on
        the notch asked for. So moving it on a dormant power is a request to *be* that
        rank: it wakes the power (flipping its switches, or becoming its array's live
        alternate) at the chosen notch rather than at full rank. And sliding back to zero
        is the card's own click again — off it goes, which is what makes the dial a whole
        control rather than one that can only ever turn a power on. The one exception is
        an array's live member, where clicking the card is deliberately a no-op: an array
        always keeps exactly one member live, and dialling one to zero must not switch
        the whole array off.

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
