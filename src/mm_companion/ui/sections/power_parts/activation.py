"""What a click on a power card does, and switching powers and groups on and off, linked
ones together.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QVariantAnimation,
)

from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    Power,
    PowerGroup,
    PowerNode,
)
from mm_companion.core.rules import (
    active_array_child,
    leaf_powers,
    live_array_children,
    power_has_standing_effect,
    power_runtime_gates,
)
from mm_companion.ui.cards import (
    DraggableCard,
)
from mm_companion.ui.sections.power_parts.helpers import (
    _CLICK_HINTS,
    _SPLIT_SELECT_HINT,
    _pool_is_split,
)


class PowersActivationMixin:
    """Part of :class:`~mm_companion.ui.sections.powers.PowersSection`; see the module docstring."""

    # -- what a click on a card does --------------------------------------
    def _activation_role(self, node: PowerNode, parent: PowerGroup | None) -> str:
        """What clicking this node's card does: ``"select"``, ``"toggle"``, or ``""``.

        ``"select"`` — the node is a member of a multi-member *array* in which something
        stands on the sheet, so clicking it makes it the live alternate (its siblings
        drop). Both a standing member and an instant one behave this way; an all-instant
        array has nothing to keep active, so its members aren't clickable.

        ``"toggle"`` — the node carries its own on/off switch: a Linked *group* that is
        gateable and standing (its members switch as one), or a standalone power that
        has a runtime gate *and* a standing bonus.

        ``""`` — nothing to switch (a permanent ungated power, a pure-instant attack),
        or the switch belongs to an ancestor: anything under a Linked group is driven by
        that group's card, so its own card stays inert and lets the click bubble up.

        The Linked test looks at the whole ancestor chain, not just the immediate
        parent: a member may sit inside a sub-group (dragging one card onto another
        always makes an *Independent* group) that is itself inside the Linked group, and
        it still has to switch with its linked siblings rather than sprout its own switch.
        """
        if self._selectable_array_member(parent):
            # ...unless the array's points are split, when the pool decides who is
            # running. (The click used to stay armed: `active_child_id` quietly moved
            # and nothing on screen changed.) `_arm_activation` still tooltips why.
            return "" if _pool_is_split(parent) else "select"
        if self._linked_ancestor(node) is not None:
            return ""
        if isinstance(node, PowerGroup):
            if (
                node.mode == STRUCTURE_LINKED
                and self._node_is_gateable(node)
                and self._node_has_standing(node)
            ):
                return "toggle"
            return ""
        if power_runtime_gates(node, self._data) and power_has_standing_effect(node, self._data):
            return "toggle"
        return ""

    def _selectable_array_member(self, parent: PowerGroup | None) -> bool:
        """Whether a card in *parent* is one of a live array's mutually exclusive members.

        An array only has something to select between if it has two of them and at least
        one *stands* on the sheet — an all-instant array keeps nothing active, so its
        members are not switches. Asked by :meth:`_activation_role` (does a click do
        anything) and :meth:`_node_is_inactive` (is this one running), which have to
        agree about what an array is even when the pool has taken the click away.
        """

        return (
            isinstance(parent, PowerGroup)
            and parent.mode == STRUCTURE_ARRAY
            and len(parent.children) >= 2
            and any(self._node_has_standing(child) for child in parent.children)
        )

    def _node_is_inactive(self, node: PowerNode, parent: PowerGroup | None, role: str) -> bool:
        """Whether the card should be drawn in its dimmed, switched-off state."""
        if self._selectable_array_member(parent):
            # Which member is *running* rather than which is selected: once the pool is
            # split every Dynamic member holding a share is live at once, so dimming all
            # but the selected one would contradict the numbers on the sheet. Asked of
            # the array rather than of the card's role, because a split takes the role
            # away and the dimming has to outlive it.
            if not any(child is node for child in live_array_children(parent)):
                return True
            # ...and a Dynamic member the fallback woke up while its own share dial sits
            # on "Off" is switched off, whatever the array did with it (see
            # :meth:`_on_share_dialled`). Only a Dynamic member can be parked that way,
            # so an ordinary alternate is left reading exactly as it always did.
            return bool(node.dynamic) and not self._member_is_running(node)
        if role == "toggle":
            if isinstance(node, PowerGroup):
                return not self._group_is_active(node)
            return not self._power_is_active(node)
        return False

    def _arm_activation(
        self,
        card: DraggableCard,
        node: PowerNode,
        parent: PowerGroup | None,
        interactive: bool,
    ) -> None:
        """Make the card its power's switch, if the power has one to offer.

        The card *is* the control — there is no separate checkbox — so the whole frame
        becomes the click target, and says so (see :meth:`DraggableCard.set_clickable`).
        ``interactive`` is ``False`` for a card inside a switched-off Linked group, which
        still shows its state but can't be clicked back on past its group.

        A member of a *split* array is the one card that gets the hint without the
        click: the pool has taken the decision over, so there is nothing to arm, but a
        card that has quietly stopped being a control needs to say so more than one that
        still is.
        """
        role = self._activation_role(node, parent)
        if self._selectable_array_member(parent) and _pool_is_split(parent):
            card.set_click_hint(_SPLIT_SELECT_HINT)
            return
        if not (role and interactive):
            return
        card.set_clickable(True)
        card.set_click_hint(_CLICK_HINTS[role])
        card.clicked.connect(lambda n=node, p=parent, r=role: self._on_card_clicked(n, p, r))

    def _show_activation(
        self, card: DraggableCard, node: PowerNode, parent: PowerGroup | None
    ) -> None:
        """Put the card into its on/off look, easing there when the state just changed.

        Every runtime setter ends in :meth:`_rebuild_list`, since flipping one power can
        change numbers on *another* card, so no card survives a toggle. The section
        remembers, per node id, the progress each card was last showing; a fresh card
        picks up there and eases to its new target. The animation writes progress back
        every frame, so a second click mid-transition does not snap.

        Called once the card's children exist, so the type scaling can see them.
        """
        role = self._activation_role(node, parent)
        target = 1.0 if self._node_is_inactive(node, parent, role) else 0.0
        previous = self._card_off_prev.get(node.id)
        self._card_off[node.id] = target
        if previous is None or previous == target or self.TRANSITION_MS <= 0:
            card.set_off_progress(target)
            return
        card.set_off_progress(previous)
        ease = QVariantAnimation(card)
        ease.setStartValue(previous)
        ease.setEndValue(target)
        ease.setDuration(self.TRANSITION_MS)
        ease.setEasingCurve(QEasingCurve.Type.OutCubic)
        ease.valueChanged.connect(lambda value, c=card, i=node.id: self._on_ease(c, i, value))
        ease.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_ease(self, card: DraggableCard, node_id: str, progress: float) -> None:
        card.set_off_progress(progress)
        self._card_off[node_id] = progress

    def _on_card_clicked(self, node: PowerNode, parent: PowerGroup | None, role: str) -> None:
        """Apply a card click: select an array's live alternate, or flip a switch."""
        if role == "select":
            if isinstance(parent, PowerGroup) and active_array_child(parent) is not node:
                # An array always keeps exactly one live member, so clicking the current
                # one is a no-op rather than switching the whole array off.
                self._set_array_active(parent, node.id)
        elif role == "toggle":
            if isinstance(node, PowerGroup):
                self._set_group_active(node, not self._group_is_active(node))
            else:
                self._set_power_active(node, not self._power_is_active(node))

    def _set_array_active(self, group: PowerGroup, child_id: str) -> None:
        """Select an array member as the live alternate and switch it on.

        Only the selected member contributes to the sheet (:func:`live_powers` descends
        into it alone), so its siblings' bonuses drop off automatically. The newly-live
        member also has its runtime gates flipped on so its effect actually applies.
        """
        group.active_child_id = child_id
        located = self._locate(child_id)
        if located is not None:
            for member in self._leaf_powers(located[0]):
                member.activated = True
                member.item_present = True
                for effect in member.effects:
                    effect.toggled_on = True
        self._rebuild_list()
        self.runtimeChanged.emit()

    # -- runtime on/off ---------------------------------------------------
    @staticmethod
    def _power_is_active(power: Power) -> bool:
        """Whether every runtime switch on the power is currently in its 'on' state."""
        return power.activated and power.item_present and all(e.toggled_on for e in power.effects)

    def _set_power_active(self, power: Power, active: bool) -> None:
        """Flip the power's runtime switches — and its whole linked group — together.

        A single "Active" control drives whichever gate the power carries (Activation,
        Removable, or a Sustained toggle); ``rules.effect_is_active`` reads only the
        flags the power's gates make relevant. Members of a Linked group switch on/off
        as one, so if this power sits directly in a Linked group every leaf under that
        group is flipped too. The ``changed`` signal is already wired to refresh the
        stats/skills sections, so the boosted totals update live.
        """
        for member in self._linked_activation_set(power):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _group_is_active(self, group: PowerGroup) -> bool:
        """Whether every leaf power under a linked group is currently switched on."""
        return all(self._power_is_active(p) for p in self._leaf_powers(group))

    def _set_group_active(self, group: PowerGroup, active: bool) -> None:
        """Flip every power under a linked group on/off as one (Decision 3).

        A Linked group presents a single Active toggle rather than a per-card switch,
        so a permanent member drops off with its sustained sibling. Mirrors
        :meth:`_set_power_active` but spans the whole group's leaves.
        """
        for member in self._leaf_powers(group):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _linked_activation_set(self, power: Power) -> list[Power]:
        """Every leaf power that switches on/off together with *power*.

        Just ``[power]`` unless it sits somewhere under a Linked group, in which case
        all leaf powers under that group activate as one — including through any
        intervening sub-group, and out to the *outermost* Linked group when they nest.
        """
        linked = self._linked_ancestor(power)
        return self._leaf_powers(linked) if linked is not None else [power]

    def _ancestor_groups(self, node_id: str) -> list[PowerGroup]:
        """Every group enclosing *node_id*, outermost first (empty at the top level)."""

        def walk(nodes: list[PowerNode], trail: list[PowerGroup]) -> list[PowerGroup] | None:
            for node in nodes:
                if node.id == node_id:
                    return trail
                if isinstance(node, PowerGroup):
                    found = walk(node.children, [*trail, node])
                    if found is not None:
                        return found
            return None

        return walk(self._character.powers, []) or []

    def _linked_ancestor(self, node: PowerNode) -> PowerGroup | None:
        """The outermost Linked group enclosing *node*, or ``None``.

        Outermost rather than nearest: nested Linked groups all switch as one, so the
        activation set is the widest of them.
        """
        return next((g for g in self._ancestor_groups(node.id) if g.mode == STRUCTURE_LINKED), None)

    @staticmethod
    def _leaf_powers(node: PowerNode) -> list[Power]:
        """This node's leaves — one node's worth of :func:`leaf_powers`."""
        return list(leaf_powers([node]))
