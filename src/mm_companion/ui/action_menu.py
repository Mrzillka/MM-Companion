"""The roller's Actions popup, as sections built from ``actions.json``.

``combat.menu`` in the game data says which actions the popup shows and in what order;
``combat.actions`` names, draws and explains each one. What it does *not* say is which
of them the window hosting the roller can run — the sheet's Dice block runs Impress,
the GM's Rolls block runs Impress, Area and the team actions — so the host says that,
as *offered*: an action id mapped to ``None`` (it runs) or to the reason it cannot run
right now (no NPC group to act with). An action the host does not mention at all is not
built yet, and its tile is shown disabled with :data:`NOT_BUILT`.

**Reroll** and **Ask to Roll** are the roller's own: the roller hands the host's builder
their entries in *offered* (``DiceRollerPanel.own_actions``) — Reroll lit or greyed from its
own last roll, Ask from whether the host gave it any traits to ask for.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from PySide6.QtCore import QPoint
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QWidget

from mm_companion.core.data_loader import GameData
from mm_companion.ui.action_grid import GridItem, GridSection
from mm_companion.ui.widgets import help_markup

#: The action the roller runs itself (it throws its own last roll again).
ACTION_REROLL = "reroll"

#: The action that asks the table to roll a trait (the roller's own small form).
ACTION_REQUEST = "request"

#: Why Ask to Roll is greyed out: the host gave the roller no traits to ask for.
NO_REQUEST_TRAITS = "There are no traits to ask for here."

#: Why a tile for an action this version cannot run yet is greyed out.
NOT_BUILT = "Not available yet."

#: Why Reroll is greyed out before there is a roll to throw again.
NOTHING_TO_REROLL = "Roll something first: there is nothing to reroll yet."


#: The first line of the Attack tile's target menu: roll the attack at nobody in
#: particular, the DC box standing for the Defense (decision 12 of the plan).
SKIP_TARGET = "Skip (no target)"

#: Why the Attack and Area tiles are greyed on a sheet with nothing to use them with.
NO_ATTACKS = "This character has no attack to make."
#: Why the targeted actions (Aid, Feint, Grab …) are greyed: they are taken against
#: someone on the Scene.
NEEDS_A_TABLE = "Join a session first: the GM resolves it against someone on the Scene."
NO_ONE_TO_TARGET = "Put someone on the Scene first: the action is taken against them."
NO_ALLY_AND_OPPONENT = "Aid needs both an ally and an opponent on the Scene."
#: Said when the attack picked cannot make an action's opposed check (Disarm's Damage
#: rank, with an attack that deals none). ``{}`` is the action's name.
NO_OPPOSED_CHECK = "That attack cannot make the {} check: it deals no Damage. Pick another."

NO_AREAS = (
    "No area to use: buy an area power, or switch an attack's area use on (right-click "
    "its card)."
)


def choose_from_menu(
    parent: QWidget,
    captions: Sequence[str],
    at: QPoint,
    *,
    disabled: Sequence[int] = (),
    icons: Sequence[QIcon | None] = (),
) -> int | None:
    """Offer *captions* on a plain ``QMenu`` at *at* and return the index picked, or
    ``None`` if the menu was dismissed. Indices in *disabled* are shown greyed; *icons*
    (by index, ``None`` for none) go in front of their lines — the GM's NPC chooser
    shows each creature's picture.

    The one place the roller's choosers (which attack, which target, which area) build
    their menus — and so the one name a test stands in for (``action_menu.QMenu``).
    """
    menu = QMenu(parent)
    for index, caption in enumerate(captions):
        action = menu.addAction(caption)
        action.setData(index)
        if index in disabled:
            action.setEnabled(False)
        icon = icons[index] if index < len(icons) else None
        if icon is not None:
            action.setIcon(icon)
    chosen = menu.exec(at)
    if chosen is None:
        return None
    index = chosen.data()
    return index if isinstance(index, int) else None


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
        if ACTION_REQUEST in offered:
            request = offered[ACTION_REQUEST]
            items.append(
                GridItem(
                    ACTION_REQUEST,
                    "Ask",
                    ACTION_REQUEST,
                    action_tooltip("Ask to Roll", "Ask the table to roll a trait.", request),
                    enabled=request is None,
                )
            )
        return [GridSection("Actions", tuple(items))]
    sections: list[GridSection] = []
    for section in combat.menu:
        items = []
        reasons: list[str | None] = []
        for action_id in section.items:
            action = combat.action(action_id)
            if action is None or (action.gm_only and not gm):
                continue
            reason = offered.get(action_id, NOT_BUILT)
            reasons.append(reason)
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
            sections.append(GridSection(section.title, tuple(items), note=section_note(reasons)))
    return sections


def section_note(reasons: Sequence[str | None]) -> str:
    """The line under a section's title when **every** tile in it is greyed: the reason
    most of them share, said once — rather than a row of dead tiles to be hovered one by
    one to find out why. ``""`` while any tile is live, or no reason is shared.

    Most, not all: Surprise Attack is greyed for a reason of its own (the GM grants it)
    beside five maneuvers waiting on an attack, and the five are what the line is for.
    """
    if not reasons or any(reason is None for reason in reasons):
        return ""
    reason, count = Counter(reasons).most_common(1)[0]
    return str(reason) if count > 1 else ""
