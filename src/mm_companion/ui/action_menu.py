"""The roller's Actions popup, as sections built from ``actions.json``.

``combat.menu`` in the game data says which actions the popup shows and in what order;
``combat.actions`` names, draws and explains each one. What it does *not* say is which
of them the window hosting the roller can run — the sheet's Dice block runs Impress,
the GM's Rolls block runs Impress, Area and the team actions — so the host says that,
as *offered*: an action id mapped to ``None`` (it runs) or to the reason it cannot run
right now (no NPC group to act with). An action the host does not mention at all is not
built yet, and its tile is shown disabled with :data:`NOT_BUILT`.

**Reroll** is the roller's own: the roller hands the host's builder its own entry in
*offered* (``DiceRollerPanel.open_actions``), lit or greyed from its own last roll.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mm_companion.core.data_loader import GameData
from mm_companion.ui.action_grid import GridItem, GridSection
from mm_companion.ui.widgets import help_markup

#: The action the roller runs itself (it throws its own last roll again).
ACTION_REROLL = "reroll"

#: Why a tile for an action this version cannot run yet is greyed out.
NOT_BUILT = "Not available yet."

#: Why Reroll is greyed out before there is a roll to throw again.
NOTHING_TO_REROLL = "Roll something first: there is nothing to reroll yet."


def action_tooltip(name: str, text: str, reason: str | None = None) -> str:
    """A tile's tooltip: the action's name in bold, what it does, and why it is off."""
    body = f"**{name}:** {text}" if text else f"**{name}**"
    if reason:
        body = f"{body}\n\n*{reason}*"
    return help_markup(body)


def combat_menu(
    data: GameData,
    offered: Mapping[str, str | None],
    *,
    gm: bool = False,
    fallback: Sequence[tuple[str, str, str]] = (),
) -> list[GridSection]:
    """The popup's sections for a host that runs *offered* (see the module docstring).

    ``gmOnly`` actions are left out of a player's menu altogether rather than greyed:
    a player can never run them. A ruleset with no ``combat.menu`` falls back to one
    section of *fallback* ``(key, caption, tooltip)`` triples — the host's own actions
    — plus Reroll, so the button still does what the old Action row did.
    """
    combat = data.combat
    if not combat.menu:
        items = [
            GridItem(key, caption.rstrip("…"), key, action_tooltip(caption.rstrip("…"), tip))
            for key, caption, tip in fallback
            if key in offered and offered[key] is None
        ]
        reroll = offered.get(ACTION_REROLL)
        items.append(
            GridItem(
                ACTION_REROLL,
                "Reroll",
                ACTION_REROLL,
                action_tooltip("Reroll", "Reroll your last roll.", reroll),
                enabled=reroll is None,
            )
        )
        return [GridSection("Actions", tuple(items))]
    sections: list[GridSection] = []
    for section in combat.menu:
        items = []
        for action_id in section.items:
            action = combat.action(action_id)
            if action is None or (action.gm_only and not gm):
                continue
            reason = offered.get(action_id, NOT_BUILT)
            items.append(
                GridItem(
                    key=action.id,
                    caption=action.tile_caption,
                    icon=action.icon,
                    tooltip=action_tooltip(action.name, action.tooltip, reason),
                    enabled=reason is None,
                )
            )
        if items:
            sections.append(GridSection(section.title, tuple(items)))
    return sections
