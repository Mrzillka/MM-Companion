"""NPC groups: merging, leaving and renaming, and the group's team attack and group check."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QInputDialog,
    QWidget,
)

from mm_companion.core import npc_groups, storage
from mm_companion.ui.action_run import ActionRun
from mm_companion.ui.gm.model import (
    SCENE_NPC,
)
from mm_companion.ui.group_check import GroupCheckWindow
from mm_companion.ui.impress import (
    new_action_id,
)
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_group import GROUP_KIND, NPCGroupCard
from mm_companion.ui.team_attack import TeamAttackWindow


class GMNpcGroupsMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    # -- groups --------------------------------------------------------------

    def _persist_groups(self) -> None:
        storage.set_gm_npc_groups(npc_groups.groups_to_raw(self._npc_groups))

    def _can_merge_npcs(self, ref: str, widget: object) -> bool:
        """Whether dropping *ref* onto *widget* combines them: an NPC card or a group's
        onto another NPC card or group."""
        kind = ref.partition(":")[0]
        return kind in (SCENE_NPC, GROUP_KIND) and isinstance(widget, (NPCCard, NPCGroupCard))

    def _merge_npcs(self, ref: str, widget: object) -> None:
        """A card (or a whole group) was dropped onto another: they become one group.

        Onto a loose card, the two make a new group named after the target ("Thug 2"
        and "Thug 5" make "Thug"); onto a grouped creature or a group, the movers join
        it. Dropping something onto itself, or onto its own group, does nothing.
        """
        kind, _, source = ref.partition(":")
        if kind == GROUP_KIND:
            card = self._group_cards.get(source)
            moving = card.members if card is not None else []
        else:
            moving = [source] if source in self._npc_state else []
        if not moving:
            return
        if isinstance(widget, NPCGroupCard):
            groups = npc_groups.join(
                self._npc_groups, moving, widget.group_id, target_is_group=True
            )
            anchor = widget.members[0]
        elif isinstance(widget, NPCCard):
            target = widget.name_key
            entry = self._npc_state.get(target)
            name = npc_groups.base_name(entry.summary.name) if entry is not None else ""
            groups = npc_groups.join(self._npc_groups, moving, target, name=name)
            anchor = target
        else:
            return
        if groups == self._npc_groups:
            return
        self._npc_groups = groups
        self._persist_groups()
        # The movers stand with the group in the board's order, so it draws as one.
        order = [n for n in self._ordered_npcs() if n not in moving]
        at = order.index(anchor) + 1 if anchor in order else len(order)
        self._manual_order = order[:at] + moving + order[at:]
        self._refresh_npcs()

    def _leave_group(self, name: str) -> None:
        """A row's "Leave the group": a loose card again, right after its old group."""
        if npc_groups.group_of(self._npc_groups, name) is None:
            return
        self._npc_groups = npc_groups.leave(self._npc_groups, name)
        self._persist_groups()
        self._refresh_npcs()

    def _ungroup(self, group_id: str) -> None:
        """The group's "Ungroup": every member a loose card again, where they stood."""
        self._npc_groups = npc_groups.dissolve(self._npc_groups, group_id)
        self._persist_groups()
        self._refresh_npcs()

    def _rename_group(self, group_id: str) -> None:
        """The group's "Rename group…"."""
        group = next((g for g in self._npc_groups if g.id == group_id), None)
        if group is None:
            return
        name, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=group.name)
        if not ok or not name.strip():
            return
        self._npc_groups = npc_groups.rename(self._npc_groups, group_id, name)
        self._persist_groups()
        self._refresh_npcs()

    def _group_to_scene(self, group_id: str, on: bool) -> None:
        """Put every member of a group on the Scene, or take every one off."""
        card = self._group_cards.get(group_id)
        if card is None:
            return
        for member in card.members:
            self._set_in_scene(SCENE_NPC, member, on)

    def _team_attack(self, group_id: str) -> TeamAttackWindow | None:
        """A group's "Team attack…": its members combine their attacks on one target."""
        card = self._group_cards.get(group_id)
        group = next((g for g in self._npc_groups if g.id == group_id), None)
        if card is None or group is None:
            return None
        members = [self._npc_party(name) for name in card.members if name in self._npc_state]
        skip = {
            entry.ref
            for name in card.members
            if (entry := self._scene_entry_for(SCENE_NPC, name)) is not None
        }
        targets = [t for t in self._aim_targets() if t.ref not in skip]
        window: TeamAttackWindow | None = None

        def run(chosen, target_ref: str, adjust: int) -> str:
            return self._attack.team_attack(
                chosen,
                target_ref,
                adjust=adjust,
                title=f"Team attack: {group.name}",
                on_rolled=lambda lines, summary: window.show_rolls(lines, summary),
                on_done=lambda text: window.show_done(text),
            )

        window = TeamAttackWindow(self._data, group.name, members, targets, run, parent=self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._team_window = window
        # Deleted on close: let go of it then, rather than hold a dead handle.
        window.destroyed.connect(lambda _=None, w=window: self._forget_window("_team_window", w))
        window.show()
        return window

    def _group_check(self, group_id: str, mode: str) -> GroupCheckWindow | None:
        """A group's "Team check…" or "Group check…" (PDF p18)."""
        card = self._group_cards.get(group_id)
        group = next((g for g in self._npc_groups if g.id == group_id), None)
        if card is None or group is None:
            return None
        members = [self._npc_party(name) for name in card.members if name in self._npc_state]

        def make_run(title: str) -> ActionRun:
            return ActionRun(
                self._data,
                self._bridge,
                self,
                new_action_id(),
                title=title,
                asker="The GM",
                parent=self,
            )

        window = GroupCheckWindow(
            self._data,
            mode,
            group.name,
            members,
            make_run,
            minion_rules=self.minion_rules,
            post_note=self._post_note,
            parent=self,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._check_window = window
        window.destroyed.connect(lambda _=None, w=window: self._forget_window("_check_window", w))
        window.show()
        return window

    def _forget_window(self, attribute: str, window: QWidget) -> None:
        """Drop the handle on a closed group window — unless a newer one holds it."""
        if getattr(self, attribute, None) is window:
            setattr(self, attribute, None)

    def _group_npcs(self, members: list[str], name: str) -> None:
        """Record *members* (two or more, just made) as a new group called *name*."""
        if len(members) < 2:
            return
        groups = self._npc_groups
        for member in members[1:]:
            groups = npc_groups.join(groups, [member], members[0], name=name)
        self._npc_groups = groups
        self._persist_groups()

    def _reorder_npc(self, name: str, target_index: int) -> None:
        """Move an NPC to a dropped slot in the GM's arrangement of the cast.

        Purely an arrangement now, and it must stay that way: the grid no longer
        sorts by initiative and no longer shows one, so a drag here has no business
        clearing a number it does not display. It used to clear both the dragged
        NPC's roll and that of a rolled neighbour it was dropped in front of, which
        was the right rule while this grid *was* the turn order. That rule lives on
        the Scene, in :meth:`_drop_on_scene`, where the ordering it protects is.
        """
        self._reorder_npcs([name], target_index)
