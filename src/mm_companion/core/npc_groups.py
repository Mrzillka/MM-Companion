"""NPC groups: the GM's way of keeping a squad of creatures together on the board.

A group is a name and an ordered list of NPC **file names** (an NPC's identity across
the GM window: its cast entry, its pins, its collapsed state). It is the GM's own
arrangement, not table state — stored in the workspace settings beside the collapsed
cards (``core.storage.gm_npc_groups``), so it survives a restart and follows the NPCs
into any session they are cast in.

Pure data and pure functions: the GM window decides when to call them and draws the
result (``ui/npc_group.py``). Nothing here is a game rule — a group does not roll or
resist as one (yet); it is how the cards are laid out.

The invariants every function keeps:

* an NPC is in at most one group;
* a stored group has two members or more (one that falls to one dissolves);
* a group shown on a board needs two of its members *in that cast* (:func:`shown_groups`)
  — the stored group is left alone, since the others may be cast in another session.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = [
    "NPCGroup",
    "base_name",
    "dissolve",
    "group_of",
    "groups_from_raw",
    "groups_to_raw",
    "join",
    "leave",
    "numbered_names",
    "rename",
    "shown_groups",
]

#: A trailing number on a name, " 3" or "-3", which the squad's name drops.
_TRAILING_NUMBER = re.compile(r"^(.*?)(?:[ -])(\d+)$")


@dataclass
class NPCGroup:
    """A named squad of NPCs, by file name, in the order the board shows them."""

    id: str
    name: str
    members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "members": list(self.members)}


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def groups_from_raw(raw: object) -> list[NPCGroup]:
    """Read the stored groups, dropping anything malformed and keeping the invariants."""

    if not isinstance(raw, list):
        return []
    groups: list[NPCGroup] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_members = item.get("members")
        if not isinstance(raw_members, list):
            continue  # null, a string, a number: nothing to read a squad from
        members = [m for m in raw_members if isinstance(m, str) and m and m not in seen]
        members = list(dict.fromkeys(members))
        if len(members) < 2:
            continue
        seen.update(members)
        group_id = item.get("id")
        name = item.get("name")
        groups.append(
            NPCGroup(
                id=group_id if isinstance(group_id, str) and group_id else _new_id(),
                name=name if isinstance(name, str) and name.strip() else "Group",
                members=members,
            )
        )
    return groups


def groups_to_raw(groups: Iterable[NPCGroup]) -> list[dict]:
    return [group.to_dict() for group in groups if len(group.members) >= 2]


def group_of(groups: Iterable[NPCGroup], member: str) -> NPCGroup | None:
    """The group *member* is in, or ``None``."""

    return next((g for g in groups if member in g.members), None)


def base_name(name: str) -> str:
    """*name* without a trailing number: "Thug 3" and "Thug-3" are both "Thug"."""

    match = _TRAILING_NUMBER.match(name.strip())
    return match.group(1).strip() if match else name.strip()


def numbered_names(name: str, count: int, existing: Iterable[str]) -> list[str]:
    """*count* names "<base> 1…N" for a squad, skipping any already in *existing*."""

    taken = set(existing)
    base = base_name(name) or name
    names: list[str] = []
    n = 1
    while len(names) < count:
        candidate = f"{base} {n}"
        if candidate not in taken:
            names.append(candidate)
            taken.add(candidate)
        n += 1
    return names


def _without(groups: list[NPCGroup], members: Iterable[str]) -> list[NPCGroup]:
    """*groups* with *members* taken out of every one, and the groups left with one gone."""

    leaving = set(members)
    kept: list[NPCGroup] = []
    for group in groups:
        group.members = [m for m in group.members if m not in leaving]
        if len(group.members) >= 2:
            kept.append(group)
    return kept


def join(
    groups: list[NPCGroup],
    moving: list[str],
    target: str,
    *,
    target_is_group: bool = False,
    name: str = "",
) -> list[NPCGroup]:
    """Put *moving* (file names) with *target* — an NPC's file name, or a group's id.

    Into the target's group when it has one (or *is* one); otherwise a new group of
    the target and the movers, called *name* (or "Group"). The movers leave whatever
    group they were in. Moving a creature onto itself, or onto its own group, changes
    nothing.
    """

    groups = [NPCGroup(g.id, g.name, list(g.members)) for g in groups]
    if target_is_group:
        destination = next((g for g in groups if g.id == target), None)
    else:
        destination = group_of(groups, target)
    moving = [m for m in dict.fromkeys(moving) if m != target]
    if destination is not None:
        moving = [m for m in moving if m not in destination.members]
    if not moving:
        return groups
    if destination is None:
        if target_is_group:
            return groups
        destination = NPCGroup(_new_id(), name.strip() or "Group", [target])
        groups = _without(groups, moving)
        groups.append(destination)
    else:
        keep_id = destination.id
        groups = _without(groups, moving)
        # The destination may have lost nobody (the movers were never in it), so it
        # survives _without; find it again by id.
        destination = next(g for g in groups if g.id == keep_id)
    destination.members.extend(moving)
    return groups


def leave(groups: list[NPCGroup], member: str) -> list[NPCGroup]:
    """*member* out of its group; a group left with one member dissolves."""

    groups = [NPCGroup(g.id, g.name, list(g.members)) for g in groups]
    return _without(groups, [member])


def dissolve(groups: list[NPCGroup], group_id: str) -> list[NPCGroup]:
    """The group *group_id* gone; its members are loose cards again."""

    return [NPCGroup(g.id, g.name, list(g.members)) for g in groups if g.id != group_id]


def rename(groups: list[NPCGroup], group_id: str, name: str) -> list[NPCGroup]:
    """The group *group_id* called *name* (blank keeps the old name)."""

    renamed = [NPCGroup(g.id, g.name, list(g.members)) for g in groups]
    for group in renamed:
        if group.id == group_id and name.strip():
            group.name = name.strip()
    return renamed


def shown_groups(groups: Iterable[NPCGroup], cast: Iterable[str]) -> list[NPCGroup]:
    """The groups as one board shows them: only members in *cast*, and only groups
    with two of them there. The stored groups are not changed."""

    present = set(cast)
    shown: list[NPCGroup] = []
    for group in groups:
        members = [m for m in group.members if m in present]
        if len(members) >= 2:
            shown.append(NPCGroup(group.id, group.name, members))
    return shown
