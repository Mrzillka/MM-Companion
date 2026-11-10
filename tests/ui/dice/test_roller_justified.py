"""The second roller pass (2026-11-10): a halo for a ring, a short result line with its
breakdown on hover, controls justified on one label column, pills that share their line,
Reroll under the result, and an Actions popup whose shared rows keep to the tile grid.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QSize
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from mm_companion.core.rules import RollSpec
from mm_companion.ui import dice_roller, theme
from mm_companion.ui.action_grid import ActionGridPopup, GridItem, GridSection
from mm_companion.ui.action_menu import section_note
from mm_companion.ui.dice_roller import CHIP_PREFIX, spec_caption
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.roll_history import (
    CIRCUMSTANCE_PART,
    MODIFIER_PART,
    breakdown_lines,
    roll_breakdown,
    roll_headline,
)
from mm_companion.ui.roller.view import DiceRollerView

pytestmark = pytest.mark.usefixtures("workspace")


def _settled(qapp: QApplication, view: QWidget, width: int, height: int) -> None:
    view.resize(width, height)
    for _ in range(8):
        qapp.processEvents()


def _left(widget: QWidget, to: QWidget) -> int:
    return widget.mapTo(to, widget.rect().topLeft()).x()


def _right(widget: QWidget, to: QWidget) -> int:
    return widget.mapTo(to, widget.rect().topRight()).x()


# -- the result line and its breakdown --------------------------------------------------


def test_the_line_is_the_die_the_net_bonus_and_the_total() -> None:
    assert "19 + 4 =" in roll_headline({"die": 19, "bonus": 4, "penalty": 0})
    assert "<b>23</b>" in roll_headline({"die": 19, "bonus": 4, "penalty": 0})
    assert "7 − 2 =" in roll_headline({"die": 7, "bonus": 0, "penalty": 2})
    # Nothing added: the die is the total, said once.
    assert roll_headline({"die": 18, "bonus": 0, "penalty": 0}) == "<b>18</b>"
    assert "take 10 + 3 =" in roll_headline({"die": 10, "bonus": 3, "routine": True})


def test_the_breakdown_names_every_part() -> None:
    spec = RollSpec(label="Athletics", modifier=9, parts=(("Strength", 4), ("Ranks", 5)))
    roll = {"die": 13, "bonus": 11, "penalty": 0, "spec": spec.to_dict(), "label": "Athletics"}
    assert breakdown_lines(roll) == [
        ("d20", "13"),
        ("Strength", "+4"),
        ("Ranks", "+5"),
        (CIRCUMSTANCE_PART, "+2"),
        ("Total", "24"),
    ]
    # Nothing loaded: the box is the whole modifier.
    plain = breakdown_lines({"die": 5, "bonus": 0, "penalty": 3})
    assert plain == [("d20", "5"), (MODIFIER_PART, "−3"), ("Total", "2")]
    assert "Success" in roll_breakdown({**roll, "dc": 15, "degree": 2})


def test_the_readout_and_the_card_carry_the_breakdown(qapp: QApplication, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.load_spec(RollSpec(label="Athletics", modifier=9, parts=(("Strength", 4), ("R", 5))))
    panel._modifier_spin.setValue(2)
    panel._start_roll()
    assert "Strength" in panel._readout.toolTip() and "Circumstance" in panel._readout.toolTip()
    (card,) = view._local_history.cards()
    tips = [label.toolTip() for label in card.findChildren(dice_roller.QLabel)]
    assert any("Strength" in tip for tip in tips)


def test_a_plain_roll_card_has_no_title(qapp: QApplication, instant_die) -> None:
    view = DiceRollerView()
    view.panel._start_roll()
    (card,) = view._local_history.cards()
    text = " ".join(label.text() for label in card.findChildren(dice_roller.QLabel))
    assert "d20" not in text and "<b>11</b>" in text


def test_the_chip_names_the_roll_without_rolling_colon() -> None:
    caption = spec_caption(RollSpec(label="Blast: Attack +7 vs. Defense", modifier=7))
    assert caption.startswith(CHIP_PREFIX) and "Rolling" not in caption


# -- the halo ----------------------------------------------------------------------------


def test_the_halo_follows_hover_and_a_loaded_trait(qapp: QApplication) -> None:
    view = DiceRollerView()
    panel = view.panel
    die = panel._die_button
    assert panel.halo_token() is None
    qapp.sendEvent(die, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert panel.halo_token() == "accent.dice"
    qapp.sendEvent(die, QEvent(QEvent.Type.Leave))
    assert panel.halo_token() is None
    panel.load_spec(RollSpec(label="Athletics", modifier=9))
    assert panel.halo_token() == "accent.dice"
    # Never a box: the button is bare, the glow is the drawing's.
    assert "border: none" in die.styleSheet() and "transparent" in die.styleSheet()


# -- justified controls -----------------------------------------------------------------


def test_the_controls_share_one_label_column_and_the_whole_width(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.show()
    panel = view.panel
    panel.set_modifier_steps((2, 5))
    _settled(qapp, view, 380, 800)
    part = panel._settings_part
    first_step = min(panel._step_buttons, key=lambda b: _left(b, part))
    last_step = max(panel._step_buttons, key=lambda b: _left(b, part))
    # The two rows start their controls at the same x...
    assert _left(first_step, part) == _left(panel._dc_spin, part)
    # ...and both reach the right edge rather than stopping short.
    assert part.width() - _right(last_step, part) <= 2
    assert part.width() - _right(panel._actions_button, part) <= 2


def test_the_dc_capsule_stays_inside_the_panel(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.show()
    panel = view.panel
    _settled(qapp, view, 380, 800)
    panel.set_dc(15)
    qapp.processEvents()
    capsule = panel._dc_pair.geometry()
    assert capsule.left() >= 0
    assert capsule.contains(panel._dc_label.geometry().center())


# -- pills ------------------------------------------------------------------------------


def test_pills_share_their_line_and_float_their_x(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.show()
    panel = view.panel
    for bonus in (2, 3):
        panel._add_quick_roll({"bonus": bonus, "penalty": 0})
    _settled(qapp, view, 380, 800)
    strip = panel._quick_container
    flow = panel._quick_flow
    chips = [flow.itemAt(i).widget() for i in range(flow.count())]
    assert len(chips) == 2
    right = max(_right(chip, strip) for chip in chips)
    assert strip.width() - right <= 2  # the line runs edge to edge
    (remove,) = [b for b in chips[0].findChildren(QPushButton) if b.text() == "×"]
    assert chips[0].layout().indexOf(remove) == -1  # it floats, taking no room
    assert remove.isHidden()
    chips[0].enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert not remove.isHidden()


def test_a_justified_flow_widens_but_never_rewraps(qapp: QApplication) -> None:
    host = FlowContainer()
    flow = FlowLayout(host, spacing=4)
    flow.setContentsMargins(0, 0, 0, 0)
    buttons = [QPushButton(f"b{i}") for i in range(3)]
    for button in buttons:
        flow.addWidget(button)
    plain = flow.heightForWidth(400)
    flow.set_justify(True)
    assert flow.heightForWidth(400) == plain
    host.resize(400, plain)
    host.show()
    qapp.processEvents()
    assert host.width() - buttons[-1].geometry().right() <= 2
    widths = [button.width() for button in buttons]
    assert max(widths) - min(widths) <= 1  # the spare width is shared, not dumped


# -- Reroll under the result ------------------------------------------------------------


def test_reroll_shows_under_a_result_it_can_throw_again(qapp: QApplication, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    button = panel._reroll_button
    assert button.isHidden()
    panel._start_roll()
    assert not button.isHidden()
    button.click()
    assert panel.can_reroll and not button.isHidden()
    # Loading a trait puts the prompt back, and Reroll with it.
    panel.load_spec(RollSpec(label="Athletics", modifier=9))
    assert button.isHidden()
    # Take 10 rolled nothing to be lucky with.
    panel.take_ten()
    assert button.isHidden()


# -- the Actions popup ------------------------------------------------------------------


def test_a_section_says_once_why_all_its_tiles_are_greyed() -> None:
    assert section_note(["no attack", "no attack", "the GM grants it"]) == "no attack"
    assert section_note(["no attack", None]) == ""  # a live tile: no note
    assert section_note(["one", "two"]) == ""  # nothing shared
    assert section_note([]) == ""


def test_a_shared_row_keeps_to_the_tile_columns(qapp: QApplication) -> None:
    anchor = QWidget()
    tile = GridItem("t", "Tile")
    sections = [
        GridSection("Row", tuple(GridItem(f"r{i}", f"R{i}") for i in range(6))),
        GridSection("Area & Group", (GridItem("area", "Area"),)),
        GridSection("Dice", (GridItem("reroll", "Reroll"), tile)),
    ]
    popup = ActionGridPopup(sections, anchor)
    popup.resize(QSize(600, 400))
    popup.show()
    qapp.processEvents()
    side = int(theme.metric("action-grid.tile"))
    gap = int(theme.metric("action-grid.gap"))
    content = popup._scroll.widget()
    reroll = popup.tile("reroll")
    first = popup.tile("r0")
    offset = _left(reroll, content) - _left(first, content)
    assert offset % (side + gap) == 0  # on a column of the full row above
    (dice_title,) = [t for t in popup._titles if t.text() == "Dice"]
    assert _left(dice_title, content) == _left(reroll, content)
    popup.close()
