"""The Dynamic share dial: how a Dynamic array divides its points between its members,
notch by notch.
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
    PowerNode,
)
from mm_companion.core.rules import (
    array_pool_points,
    dynamic_held_rank,
    dynamic_member_cost,
    dynamic_rank_share,
    dynamic_share_points,
    dynamic_share_steps,
    effect_current_rank,
    effect_display_name,
    effect_is_selected,
    live_array_children,
    power_pool_points,
    size_steps,
)
from mm_companion.ui.sections.power_parts.activation import PowersActivationMixin
from mm_companion.ui.sections.power_parts.helpers import _held_effects, _share_index
from mm_companion.ui.sections.power_parts.widgets import _RankDial, _SplitGroup


class PowersSharesMixin:
    """Part of :class:`~mm_companion.ui.sections.powers.PowersSection`; see the module docstring."""

    # -- the Dynamic share dial -------------------------------------------
    def _share_dial(
        self, node: PowerNode, parent: PowerGroup | None, interactive: bool
    ) -> QWidget | None:
        """The dial a Dynamic member's share is made on — and its **only** dial.

        A share is only interesting for the rank it buys, so the notches *are* the
        ranks and the points are what each spends
        (:func:`~mm_companion.core.rules.dynamic_share_steps`).

        It **replaces** the rank dial (:meth:`_rank_dials` stands down): two dials
        claiming one rank deadlocked, the share clamping away what the rank dial wrote
        and the clamp surviving as a floor.

        The reachable pips **end where the pool does**; one dotted pip turns hollow
        for every point a sibling hands back. The dial is always drawn, even at one
        notch: it used to vanish once siblings spent the pool, with no way back.
        """

        if parent is None or not node.dynamic or parent.mode != STRUCTURE_ARRAY:
            return None
        pool = array_pool_points(parent, self._data)
        full = dynamic_member_cost(node, self._data)
        if pool <= 0 or full <= 0:
            return None
        held = max(0, node.dynamic_points or 0)
        others = sum(c.dynamic_points or 0 for c in parent.children if c.dynamic) - held
        return self._build_share_dial(
            node,
            full,
            held,
            max(0, pool - others),
            interactive,
            split=self._split_for(parent, pool),
            fallback=self._fallback_share(node, parent, full),
        )

    def _effect_share_dials(self, power: Power, interactive: bool) -> list[QWidget]:
        """The same dial one level down: a power's *own* Dynamic effects, sharing its pool.

        An array exists at two levels and so does its pool, so the control does too. The
        arithmetic is identical — only what holds the share differs, an effect rather
        than a whole card — which is why both go through :meth:`_build_share_dial` and
        cannot price the same rank two ways.
        """

        if power.structure != STRUCTURE_ARRAY or len(power.effects) < 2:
            return []
        pool = power_pool_points(power, self._data)
        if pool <= 0:
            return []
        assigned = sum(e.dynamic_points or 0 for e in power.effects if e.dynamic)
        dials: list[QWidget] = []
        for effect in power.effects:
            if not effect.dynamic:
                continue
            full = dynamic_member_cost(effect, self._data)
            if full <= 0:
                continue
            held = max(0, effect.dynamic_points or 0)
            dial = self._build_share_dial(
                effect,
                full,
                held,
                max(0, pool - (assigned - held)),
                interactive,
                power=power,
                split=self._split_for(power, pool),
                fallback=self._fallback_share(effect, power, full),
            )
            if dial is not None:
                dials.append(dial)
        return dials

    def _build_share_dial(
        self,
        node,
        full: int,
        held: int,
        affordable: int,
        interactive: bool,
        power: Power | None = None,
        split: _SplitGroup | None = None,
        fallback: tuple[int, int | None] = (0, None),
    ) -> QWidget | None:
        """One share dial, whatever holds the share.

        *affordable* is the most this member could pay for — what is unassigned plus what
        it already holds — and it is where the reachable pips end, so the last hollow pip
        is always the most the player can ask for. A share stored above it (a rebuild
        moved the pool under a split already made) still seats the dial at its
        true notch rather than quietly reading low, so the number on the card and the
        number charged against the pool are the same one.

        *fallback* is the notch a member running on **no** share at all is standing on —
        an unsplit array's selected alternate (:meth:`_fallback_share`). It is a reading
        rather than a claim, so it is what the dial is drawn on and what a commit
        landing back on it counts as *no change*, while the pool goes on being counted
        without it until the player actually picks another notch.

        **Its label names the rank without a price**: that notch spends nothing, and
        ``10 PP · Growth 6`` under an unsplit header read as this member holding the
        whole pool. :class:`_SplitGroup` counts it as zero too (:meth:`_SplitGroup._held`).
        The price appears once another notch is picked.
        """

        notches = self._share_notches(node, full, held)
        if len(notches) < 2:
            return None
        steps = [points for points, _rank in notches]
        seat = fallback if fallback[0] else (held, self._held_rank(node, held, full))
        index = self._seat(notches, *seat)
        labels = {
            i: self._share_label(node, points, rank, full, power)
            for i, (points, rank) in enumerate(notches)
        }
        if fallback[0]:
            labels[index] = self._share_label(
                node, notches[index][0], notches[index][1], full, power, priced=False
            )
        dial = _RankDial(
            self._share_caption(node, power),
            len(steps) - 1,  # brought in to what is affordable by set_ceiling below
            index,
            labels,
            interactive,
        )
        dial.set_ceiling(max(_share_index(steps, affordable), index))
        if split is not None:
            split.add(dial, steps, phantom=bool(fallback[0]))
        dial.rankPicked.connect(
            lambda picked, n=node, nt=notches, seated=index: self._on_share_dialled(
                n, nt, picked, seated
            )
        )
        return dial

    @staticmethod
    def _seat(notches: list[tuple[int, int | None]], points: int, rank: int | None) -> int:
        """Which notch a member sitting on *points*, standing at *rank*, is drawn on.

        The last notch at the share it holds — the most that share buys, which is what a
        member nobody has held below its ceiling is running at — unless one of the
        notches at that price is the rank it is actually standing at, which is the whole
        reason two of them can share a price (:meth:`_share_notches`).
        """

        seat = 0
        for index, (price, hold) in enumerate(notches):
            if price != points:
                continue
            seat = index
            if rank is not None and hold == rank:
                return index
        return seat

    def _held_rank(self, node, points: int, full: int) -> int | None:
        """The rank this member is deliberately held at, below what its share buys.

        The model's side of the notch the player stopped on, and read by exactly the rule
        the sheet reads it by (:func:`~mm_companion.core.rules.dynamic_held_rank`), so the
        handle and the rank on the card can never come from two different answers.
        """

        effects = _held_effects(node)
        if len(effects) != 1:
            return None
        return dynamic_held_rank(effects[0], points, full)

    def _hold_member(self, node, hold: int | None) -> None:
        """Pin a member to the rank its notch names, or let its share decide again.

        Written only where it is doing work — a notch that stands exactly where its share
        already reaches stores nothing — so a file gains a ``current_rank`` only for a
        member deliberately held below its ceiling, and a value left behind by a rank dial
        the effect had before it joined the pool is cleared the first time the dial is
        touched.
        """

        effects = _held_effects(node)
        if len(effects) != 1:
            return
        effect = effects[0]
        if hold is None or hold <= 0:
            effect.current_rank = None
            return
        full = dynamic_member_cost(node, self._data)
        buys = dynamic_rank_share(effect.rank, node.dynamic_points or 0, full)
        effect.current_rank = hold if hold < buys else None

    def _fallback_share(self, node, host, full: int) -> tuple[int, int | None]:
        """The notch a member holding *no* share is running on anyway — share and rank.

        Zero for every member the pool is rationing. But an array whose shares are
        all handed back is not split, and
        :func:`~mm_companion.core.rules.live_array_children` runs its **selected
        alternate** at its rank (:func:`~mm_companion.core.rules.effect_is_selected`
        for a power's own effects). That member is running, so its dial must not say
        "Off" (it used to, for every member of an untouched Dynamic array).

        Priced through the inverse of what a share buys
        (:func:`~mm_companion.core.rules.dynamic_share_points`), so a member at full
        rank lands on its whole cost; one holding several effects is priced whole.

        A reading, not a claim: nothing is written, and :class:`_SplitGroup` leaves
        these points out of the pool until the handle moves. The rank comes back too,
        to pick between two notches that share a price (:meth:`_seat`).
        """

        if node.dynamic_points is not None or not self._member_is_running(node):
            return (0, None)
        if isinstance(node, PowerEffectInstance):
            if not effect_is_selected(host, node, self._data, self._character):
                return (0, None)
            effects = [node]
        else:
            if not any(child is node for child in live_array_children(host)):
                return (0, None)
            effects = _held_effects(node)
        if len(effects) != 1:
            return (full, None)
        rank = effect_current_rank(effects[0], self._data, self._character)
        return (dynamic_share_points(effects[0].rank, rank, full), rank)

    def _split_for(self, host, pool: int) -> _SplitGroup:
        """The coordinator joining one array's share dials, made on first ask.

        Keyed by the host's id, so a group of cards and a power's own effects each get
        their own — an array exists at two levels and the two pools are separate.
        """

        split = self._splits.get(host.id)
        if split is None:
            split = _SplitGroup(pool)
            self._splits[host.id] = split
        return split

    def _share_caption(self, node, power: Power | None) -> str:
        """What the share dial calls itself — the same word the rank dial would use.

        A Dynamic member's dial *is* its rank dial, so it answers to the same name: a
        size effect's ladder is captioned "Size" whether or not a pool is rationing it,
        and a member holding more than one effect is captioned "Share" because its label
        already names them all.
        """

        effects = _held_effects(node)
        if len(effects) != 1:
            return "Share"
        host = power if power is not None else (node if isinstance(node, Power) else None)
        if host is not None and size_steps(host, effects[0], self._character, self._data):
            return "Size"
        return "Rank"

    def _share_notches(self, node, full: int, held: int) -> list[tuple[int, int | None]]:
        """Every ``(share, rank)`` this member's dial can stop on, cheapest first.

        **One notch per rank, not per price.** A share buys a ceiling, not a rank, and
        where a member's cost per rank is not round some ranks have no price of their
        own: notches by price let a Growth be Large or Gargantuan but never Huge. So
        every rank gets a notch, priced at the cheapest share that *reaches* it, and
        carries the rank it stands at, which
        :func:`~mm_companion.core.rules.dynamic_held_rank` reads back.

        The rank is ``None`` for a member holding **several** effects, whose share
        rations them all together and which therefore has no single rank to stand at;
        those notches are the prices its effects can stop on, as they always were.

        A stored share that is not a notch (a hand-edited file, or a ladder that changed
        under it) is folded in the same way: rounding it down for display while the pool
        went on charging the full amount lost the difference the moment the dial was
        touched.
        """

        effects = _held_effects(node)
        notches: list[tuple[int, int | None]] = [(0, 0)]
        if len(effects) == 1:
            rank = effects[0].rank
            notches += [(dynamic_share_points(rank, r, full), r) for r in range(1, rank + 1)]
        else:
            prices: set[int] = set()
            for effect in effects:
                prices.update(p for p, _rank in dynamic_share_steps(effect.rank, full))
            notches += [(p, None) for p in sorted(prices) if p > 0]
        if held > 0 and all(points != held for points, _rank in notches):
            notches.append((held, None))
            notches.sort(key=lambda notch: notch[0])  # stable: the ranks keep their order
        return notches

    def _share_steps(self, node, full: int, held: int) -> list[int]:
        """What each notch of :meth:`_share_notches` **spends**, cheapest first.

        The ladder as the pool sees it, which is what bounds it: how much of it is
        reachable is :meth:`_RankDial.set_ceiling`'s business, and because the affordable
        notches are always a *prefix* of this list (they are the ones under a budget, and
        the list is sorted) bringing the end in never changes what a notch means. Two
        notches at one price are two ranks the same share can stop at, so the prefix
        holds and the ceiling lands on the higher of them.
        """

        return [points for points, _rank in self._share_notches(node, full, held)]

    def _share_label(
        self,
        node,
        points: int,
        hold: int | None,
        full: int,
        power: Power | None = None,
        priced: bool = True,
    ) -> str:
        """What one notch of the share dial costs and buys, named effect by effect.

        The rank *is* the answer the rules give — the book's own example is a Flight 5
        costing 10 points held to "1 rank of Flight" by the 2 assigned to it (p101) — so
        a notch reads "6 PP · Flight 3" rather than a fraction the reader has to convert.
        A share too small for even one rank of anything says so instead of reading as
        rank 0.

        *hold* is the rank the notch **stands** at where that is the player's to choose
        (:meth:`_share_notches`), and it is the only thing separating the two notches
        that share a price: "5 PP · Huge" is the one that stops there, and
        "5 PP · Gargantuan" beside it spends the same points on the next rung.

        **A size effect is named by the size it becomes**, as on the rank dial
        (:meth:`_dial_labels`); it used to show bare rank numbers here.

        *priced* is off for the notch a member was merely **found** on rather than paid
        for (see :meth:`_build_share_dial`): the rank is true, the price is not yet.
        """

        if points <= 0:
            return "Off"
        effects = _held_effects(node)
        shares = [
            dynamic_rank_share(e.rank, points, full) if hold is None else hold for e in effects
        ]
        if not shares:
            return f"{points} PP" if priced else ""
        if not any(shares):
            return f"{points} PP · too few points to run"
        parts = [
            self._notch_name(node, effect, share, power)
            for effect, share in zip(effects, shares, strict=True)
        ]
        prefix = f"{points} PP · " if priced else ""
        return prefix + ", ".join(parts[:2]) + (", …" if len(parts) > 2 else "")

    def _notch_name(
        self, node, effect: PowerEffectInstance, share: int, power: Power | None
    ) -> str:
        """What one effect *is* at *share* ranks — its size category, or "Name rank".

        The same choice :meth:`_dial_labels` makes for the plain rank dial, made in the
        one other place a rank is put into words, so a size effect reads the same under a
        share as it does without one. A member holding several effects keeps the effect's
        name in front of the rung, since the label has to say which of them it means.
        """

        host = power if power is not None else (node if isinstance(node, Power) else None)
        steps = size_steps(host, effect, self._character, self._data) if host is not None else ()
        category = self._dial_labels(steps).get(share) if steps else None
        name = effect_display_name(effect, self._data)
        if category is None:
            return f"{name} {share}"
        return category if len(_held_effects(node)) == 1 else f"{name} {category}"

    def _on_share_dialled(
        self,
        node,
        notches: list[tuple[int, int | None]],
        index: int,
        seated: int | None = None,
    ) -> None:
        """Hand a member the share its dial was left on — and at zero, switch it off.

        Runtime, so it emits ``runtimeChanged``. Zero is stored as *no share at all*,
        so sliding every member back to nothing leaves a saved character as it was
        before the split: the way back to an ordinary array.

        **Zero is off**, said out loud here: handing back the *last* share makes the
        array fall back to its selected alternate at full rank, so a Growth parked on
        "Off" came straight back on. So the notch flips the member's own switches as a
        card click would (:meth:`_set_member_running`), and only on its own leaves, so
        a share never switches off a Linked group it sits inside.

        A commit that lands where it started rebuilds nothing. *Where it started* is
        the notch the dial was **drawn** on (:meth:`_build_share_dial` passes it back),
        including one the fallback woke (:meth:`_fallback_share`), so leaving that dial
        keeps the array unsplit while stepping it to zero puts the member down.

        A notch is a **share and a rank**, and both are written: two notches can spend
        the same points and stop at different ranks (:meth:`_share_notches`), so the
        index is compared, and :meth:`_hold_member` stores the rank when it is below
        what the points buy.
        """

        points, hold = notches[max(0, min(index, len(notches) - 1))]
        running = points > 0
        if seated is None:
            full = dynamic_member_cost(node, self._data)
            share = node.dynamic_points or 0
            seated = self._seat(notches, share, self._held_rank(node, share, full))
        if index == seated and self._member_is_running(node) == running:
            return
        node.dynamic_points = points or None
        self._hold_member(node, hold if running else None)
        self._set_member_running(node, running)
        self._rebuild_list()
        self.runtimeChanged.emit()

    @staticmethod
    def _member_is_running(node) -> bool:
        """Whether a Dynamic array member's own switches are on.

        Not whether it is *contributing* — that is the pool's question
        (:func:`~mm_companion.core.rules.live_array_children`) and the gates' — only
        whether the player has left this member switched on. A member is a whole card or
        sub-group at the group level and a single effect at the power's own level, and
        each answers with the flag its level switches.
        """

        if isinstance(node, PowerEffectInstance):
            return node.toggled_on
        return all(
            PowersActivationMixin._power_is_active(p)
            for p in PowersActivationMixin._leaf_powers(node)
        )

    @staticmethod
    def _set_member_running(node, running: bool) -> None:
        """Switch one Dynamic member on or off, leaving its siblings alone.

        **Every** switch on the member's own leaves, not just ``activated``: a card
        click clears ``activated``, ``item_present`` and each ``toggled_on`` together
        (:meth:`_set_power_active`), and raising only ``activated`` left a Speed parked
        by a card click impossible to buy back on. Which flags matter is
        :func:`~mm_companion.core.rules.effect_is_active`'s business.

        *Its own leaves* is still the whole of the scope: reaching wider, as
        :meth:`_set_power_active` does for a Linked group, would let one member's share
        switch off a Linked group the array happens to sit inside.
        """

        if isinstance(node, PowerEffectInstance):
            node.toggled_on = running
            return
        for power in PowersActivationMixin._leaf_powers(node):
            power.activated = running
            power.item_present = running
            for effect in power.effects:
                effect.toggled_on = running
