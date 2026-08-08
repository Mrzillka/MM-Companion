"""The parts a GM's player card and NPC card have in common.

The two cards show the same creature from different sides — a player's is a
*remote* snapshot the GM can only ask to change, an NPC's is the GM's own model —
but what a GM *reads* off them is identical: a picture, and the numbers behind it.
So the picture and the numbers live here.

:func:`character_summary_html` is the card's hover summary. It began on the NPC
card alone, which left a GM able to glance at a mook's Toughness but obliged to
open a whole window for a player's. Same creature, same question, so: same answer.

:class:`PortraitButton` is the picture, and the **only** way into a sheet from
either card. An NPC card used to open on a click anywhere, which fought its own
drag-to-reorder gesture; a player card had a button instead. One target that
looks clickable beats two that disagree.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    effective_ability,
    item_effective_build,
    leaf_powers,
    resistance_total,
)

#: The portrait's side, in pixels. Matches the launcher's cards.
PORTRAIT_SIZE = 96
#: Shown on a card whose character has no picture (or has not sent one yet).
NO_IMAGE = "No image"


def character_summary_html(character: Character, data: GameData, title: str) -> str:
    """A compact abilities / resistances / powers / equipment summary, as tooltip HTML.

    *title* is the name to head it with, which the two cards know differently — an
    NPC's comes off its file summary, a player's off the snapshot — so it is
    passed in rather than dug out of the character.

    Deliberately every ability and every resistance rather than a chosen few: this
    is the *whole* picture, and the pinned-parameter strip beside it is where a GM
    says which handful they want without hovering.
    """
    rows = [f"<b>{escape(title)}</b>"]

    abilities = ", ".join(
        f"{escape(a.name)} {effective_ability(character, data, a.key):+d}" for a in data.abilities
    )
    if abilities:
        rows.append(f"<b>Abilities</b><br>{abilities}")

    resistances = ", ".join(
        f"{escape(r.name)} {resistance_total(character, data, r.key)}" for r in data.resistances
    )
    if resistances:
        rows.append(f"<b>Resistances</b><br>{resistances}")

    power_lines = []
    for power in leaf_powers(character.powers):
        name = escape(power.name or "Power")
        rolls = _roll_lines(power, character, data)
        power_lines.append(f"{name}: {escape(', '.join(rolls))}" if rolls else name)
    if power_lines:
        rows.append("<b>Powers</b><br>" + "<br>".join(power_lines))

    # Gear reads like a power here because it *is* one underneath, and a mook whose
    # whole threat is the rifle it carries hovered blank without this. Each item as
    # its effective build, so a fitted laser sight's +2 shows in the number the GM is
    # about to be attacked with.
    #
    # Every item, worn or not — the same argument the pinned strip beside this makes:
    # wearing is runtime state a GM flips constantly, and a summary that emptied when a
    # sword was sheathed would be noise.
    gear_lines = []
    for item in character.equipment:
        name = escape(item.name or "Equipment")
        rolls = _roll_lines(item_effective_build(item, data), character, data)
        gear_lines.append(f"{name}: {escape(', '.join(rolls))}" if rolls else name)
    if gear_lines:
        rows.append("<b>Equipment</b><br>" + "<br>".join(gear_lines))

    return "<br><br>".join(rows)


def _roll_lines(power, character: Character, data: GameData) -> list[str]:
    """The power's dice-footer lines, imported late to dodge a UI import cycle."""
    from mm_companion.ui.sections.powers import roll_lines

    return roll_lines(power, character, data)


class PortraitButton(QLabel):
    """A card's picture, clickable to open that character's sheet.

    A ``QLabel`` rather than a ``QPushButton`` because it must show a scaled
    pixmap edge to edge with a plain box around it, and a button would wrap that
    in its own chrome. It carries the affordance instead: a pointing-hand cursor
    and a tooltip saying what a click does.

    :meth:`set_clickable` is what a player card gates on — before the first
    snapshot arrives there is no sheet to open, so the cursor and the tooltip stay
    off rather than promising something that would do nothing.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(NO_IMAGE, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(PORTRAIT_SIZE, PORTRAIT_SIZE)
        self.setFrameShape(QLabel.Shape.Box)
        self._clickable = False
        self.set_clickable(True)

    def set_clickable(self, clickable: bool) -> None:
        """Whether a click opens a sheet, and whether the portrait says so."""
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip("Open sheet" if clickable else "")

    def set_image(self, pixmap: QPixmap | None) -> None:
        """Show *pixmap* scaled into the box, or the placeholder for ``None``."""
        if pixmap is None or pixmap.isNull():
            self.setText(NO_IMAGE)
            self.setPixmap(QPixmap())
            return
        self.setText("")
        self.setPixmap(
            pixmap.scaled(
                PORTRAIT_SIZE,
                PORTRAIT_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Open on a click that both started and ended on the portrait.

        The press is swallowed (not forwarded to the card) so that a press here
        can never be mistaken for the start of the card's drag-to-reorder — which
        is the whole reason opening moved off the card body in the first place.
        """
        if (
            self._clickable
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        event.accept()
