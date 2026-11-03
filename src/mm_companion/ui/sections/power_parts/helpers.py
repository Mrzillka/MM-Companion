"""What the power section's parts share: the click hints, and the small readings of a group
(its mode, whether its pool is split, what a share holds down).
"""

from __future__ import annotations

from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import (
    member_effects,
)
from mm_companion.ui.power_constructor.canvas import MODE_ARRAY_DYNAMIC

# What a click on a card does, by activation role (see _activation_role).
_CLICK_HINTS = {
    "toggle": "Click the card to switch this power on or off. Its bonuses apply only while "
    "it is on.",
    "select": "Click the card to make this the array's active effect. The others switch off.",
}


# The same click while the array's points are split across its Dynamic members, when
# "its siblings switch off" has stopped being true: the split decides who is running,
# so selecting one member no longer switches anything off (see live_array_children).
_SPLIT_SELECT_HINT = (
    "The array's points are split across its Dynamic effects, so they all run at once. "
    "Picking one matters again after ↺ on the group's title bar hands the points back."
)


def _group_mode(group: PowerGroup) -> str:
    """Which of the toggle's four segments a group is currently on.

    :data:`~mm_companion.ui.power_constructor.canvas.MODE_ARRAY_DYNAMIC` is a *view*
    over the model rather than a fourth stored structure — an array whose members carry
    the ``dynamic`` flag — so this is the one place the view is derived and the switch,
    the title and the rename placeholder cannot disagree about it. **Any** Dynamic
    member counts, so a mixed array saved while Dynamic was a per-member checkbox reads
    as what it is instead of as a plain array that quietly costs more.
    """

    if group.mode == STRUCTURE_ARRAY and any(child.dynamic for child in group.children):
        return MODE_ARRAY_DYNAMIC
    return group.mode


def _held_effects(node) -> list[PowerEffectInstance]:
    """The effects one share holds down — a whole member's, or a single effect's own.

    A share is held by a member of an array, and an array exists at two levels: its
    members are whole cards at the group level and single effects inside one power. The
    dial is the same either way, so this is where the two shapes meet.
    """

    if isinstance(node, PowerEffectInstance):
        return [node]
    return member_effects(node)


def _pool_is_split(group: PowerGroup) -> bool:
    """Whether any of this array's Dynamic members is currently holding a share.

    The one question that decides whether an array behaves as a set of mutually
    exclusive alternates or as a pool running several at once, so the hint, the dimming
    and the header button all ask it here rather than each spelling it out.
    """

    return group.mode == STRUCTURE_ARRAY and any(
        child.dynamic and (child.dynamic_points or 0) > 0 for child in group.children
    )


def _share_index(steps: list[int], budget: int) -> int:
    """The highest notch *budget* points can pay for."""

    best = 0
    for index, points in enumerate(steps):
        if points <= budget:
            best = index
    return best
