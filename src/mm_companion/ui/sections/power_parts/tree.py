"""Editing the power tree: finding a node, combining two into a group, moving one, and
tidying the tree after a structural change.
"""

from __future__ import annotations

from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    PowerGroup,
    PowerNode,
    power_is_stunt,
)
from mm_companion.ui.power_constructor.canvas import MODE_ARRAY_DYNAMIC
from mm_companion.ui.sections.power_parts.helpers import _group_mode


class PowersTreeMixin:
    """Part of :class:`~mm_companion.ui.sections.powers.PowersSection`; see the module docstring."""

    # -- tree lookup / mutation seams (headless-testable) -----------------
    def _locate(
        self,
        node_id: str,
        nodes: list[PowerNode] | None = None,
        parent: PowerGroup | None = None,
    ) -> tuple[PowerNode, list[PowerNode], int, PowerGroup | None] | None:
        """Find a node by id, returning ``(node, its list, index, parent group)``.

        The list is the actual mutable container (top-level ``powers`` or a group's
        ``children``), so callers can splice in place. ``None`` when the id is absent.
        """
        nodes = self._character.powers if nodes is None else nodes
        for index, node in enumerate(nodes):
            if node.id == node_id:
                return node, nodes, index, parent
            if isinstance(node, PowerGroup):
                found = self._locate(node_id, node.children, node)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _subtree_ids(node: PowerNode) -> set[str]:
        ids = {node.id}
        if isinstance(node, PowerGroup):
            for child in node.children:
                ids |= PowersTreeMixin._subtree_ids(child)
        return ids

    def _groupable(self, node_id: str) -> bool:
        """Whether this node may join a group at all — false for a **power stunt**.

        A stunt is bought with Extra Effort and a Hero Point rather than with points, so
        it costs 0 (see :func:`~mm_companion.core.rules.node_cost`). Inside an array that
        makes it the cheapest member by definition, which moves the base, the pool and
        every other member's flat price — a temporary card silently repricing the build
        it was taken from. It is not saved either (``strip_stunts``), so the group it
        left would come back a member short.

        The tree is drag-and-drop and nothing else refuses a drop, so this is asked in
        three places: ``NodeList``'s admission rule (which *shows* the refusal), and both
        mutation seams, which are the ones that hold the invariant.

        A node this section cannot find is refused too. It cannot arrive from a drag
        within the tree, and the mutation seams would drop it on the floor anyway — so
        accepting it would mean lighting the target up for a move that never happens,
        which is the failure this whole guard exists to avoid.
        """

        node = self._locate(node_id)
        return node is not None and not power_is_stunt(node[0])

    def _on_combine(self, source_id: str, target_id: str) -> None:
        """Group the dragged node with a drop target into a new Independent group.

        Wraps the target (a card, or a whole group when dropped on its title bar) and
        the source into a fresh :class:`PowerGroup`, replacing the target in place —
        nesting naturally when the target already sits inside a group. Rejected when
        the two are the same node, when the target lives inside the source's own
        subtree, or when either of them is a stunt (:meth:`_groupable`).
        """
        if source_id == target_id:
            return
        if not (self._groupable(source_id) and self._groupable(target_id)):
            return
        source = self._locate(source_id)
        target = self._locate(target_id)
        if source is None or target is None:
            return
        source_node, _src_list, _src_index, _src_parent = source
        if target_id in self._subtree_ids(source_node):
            return  # can't group a node with its own descendant
        # Remove the source, then re-find the target (its index may have shifted).
        source_node, src_list, src_index, _ = source
        src_list.pop(src_index)
        target = self._locate(target_id)
        if target is None:  # defensive: the target should still be findable here
            src_list.insert(src_index, source_node)  # put it back where it was
            return
        target_node, tgt_list, tgt_index, _ = target
        group = PowerGroup(
            mode=STRUCTURE_INDEPENDENT,
            children=[target_node, source_node],
            active_child_id=target_node.id,
        )
        tgt_list[tgt_index] = group
        # The two members are inside a fresh Independent group now and neither is an
        # array member any more; the group itself may have landed in one.
        self._after_structural_change(source_node.id, target_node.id, group.id)

    def _on_move(self, source_id: str, parent_id: str, index: int) -> None:
        """Move the dragged node into a list (top-level or a group) at *index*.

        This is how a card is reordered, pulled out of a group (dropped in a higher
        list), or added to a group as another member (dropped in the group's body).
        Rejected when the destination lives inside the moved node's own subtree, or when
        a stunt is being moved *into* a group (:meth:`_groupable`) — reordering one at
        the top level is fine, since that is where it belongs.
        """
        source = self._locate(source_id)
        if source is None:
            return
        source_node, src_list, src_index, _ = source
        if parent_id == "":
            dest_list: list[PowerNode] = self._character.powers
        else:
            if not self._groupable(source_id):
                return
            if parent_id in self._subtree_ids(source_node):
                return  # can't move a node into itself
            dest = self._locate(parent_id)
            if dest is None or not isinstance(dest[0], PowerGroup):
                return
            dest_list = dest[0].children
        src_list.pop(src_index)
        if dest_list is src_list and src_index < index:
            index -= 1  # the pop shifted everything after the source down one
        index = max(0, min(index, len(dest_list)))
        dest_list.insert(index, source_node)
        self._after_structural_change(source_node.id)

    def _after_structural_change(self, *moved_ids: str) -> None:
        """Tidy the tree after a combine/move, then rebuild and signal the change.

        *moved_ids* are the nodes whose **parent** just changed; each is re-seated
        against its new one (:meth:`_reseat_dynamic`). After the tidying, not before: a
        singleton group collapsing re-parents its child a second time, and a node seated
        against the parent it had in between would be seated against a group that no
        longer exists.
        """

        self._collapse_singletons()
        self._normalize_arrays()
        for node_id in moved_ids:
            self._reseat_dynamic(node_id)
        self._rebuild_list()
        self.changed.emit()

    def _reseat_dynamic(self, node_id: str) -> None:
        """Make a node's Dynamic flag agree with the group it now sits in.

        ``dynamic`` is a fact about a node's *place*, and a drag used to leave it
        alone: a card dropped into a Dynamic array joined as a plain, mispriced
        alternate, and one dragged out took the flag along and turned its next array
        Dynamic (:func:`_group_mode` lights on **any** member).

        So a node landing inside a Dynamic array joins it, and one landing elsewhere
        stops claiming to. Its **share** is only cleared, never invented.

        Scoped to the node that moved: applied on every rebuild it would migrate a
        mixed array saved while Dynamic was a per-member checkbox (see
        ``docs/notes/powers.md``).
        """

        located = self._locate(node_id)
        if located is None:
            return
        node, _siblings, _index, parent = located
        joined = (
            parent is not None
            and parent.mode == STRUCTURE_ARRAY
            and _group_mode(parent) == MODE_ARRAY_DYNAMIC
        )
        node.dynamic = joined
        if not joined:
            node.dynamic_points = None

    def _collapse_singletons(self) -> None:
        """Dissolve groups left trivial by a move: one child unwraps, zero drops out."""

        def collapse(nodes: list[PowerNode]) -> list[PowerNode]:
            result: list[PowerNode] = []
            for node in nodes:
                if isinstance(node, PowerGroup):
                    node.children[:] = collapse(node.children)
                    if len(node.children) == 1:
                        result.append(node.children[0])
                    elif node.children:
                        result.append(node)
                    # an emptied group is dropped
                else:
                    result.append(node)
            return result

        self._character.powers[:] = collapse(self._character.powers)

    def _normalize_arrays(self) -> None:
        """Point every array group's ``active_child_id`` at a real child (else the first)."""

        def normalize(nodes: list[PowerNode]) -> None:
            for node in nodes:
                if isinstance(node, PowerGroup):
                    if node.mode == STRUCTURE_ARRAY and node.children:
                        ids = {child.id for child in node.children}
                        if node.active_child_id not in ids:
                            node.active_child_id = node.children[0].id
                    normalize(node.children)

        normalize(self._character.powers)
