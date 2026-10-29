"""Drop-target feedback: idle, accept, and — new — reject.

Driven through the state seams rather than synthetic drag events, which Qt makes
awkward to fabricate and which would test Qt more than this module.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from mm_companion.ui import theme
from mm_companion.ui.drop_feedback import DropFeedback, clear_all


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def target(qapp) -> DropFeedback:
    feedback = DropFeedback(QWidget(), "PowerCanvas")
    feedback.set_idle("border: 1px dashed grey;")
    yield feedback
    clear_all()


def test_it_starts_idle_wearing_the_resting_style(target) -> None:
    assert target.state == DropFeedback.IDLE
    assert "dashed grey" in target._widget.styleSheet()


def test_accept_and_reject_are_visibly_different(target) -> None:
    target.show_accept()
    accepted = target._widget.styleSheet()
    assert target.state == DropFeedback.ACCEPT
    assert theme.color("accent") in accepted

    target.show_reject()
    rejected = target._widget.styleSheet()
    assert target.state == DropFeedback.REJECT
    assert theme.color("drop.reject") in rejected
    assert rejected != accepted


def test_clearing_restores_the_resting_style(target) -> None:
    target.show_reject()
    target.clear()

    assert target.state == DropFeedback.IDLE
    assert "dashed grey" in target._widget.styleSheet()


def test_the_rules_are_scoped_to_the_selector(qapp) -> None:
    """An unscoped rule would repaint every label and separator inside the target."""
    feedback = DropFeedback(QWidget(), "EffectCard")
    feedback.show_accept()

    assert feedback._widget.styleSheet().startswith("EffectCard {")
    clear_all()


def test_only_one_target_is_ever_dressed(qapp) -> None:
    """Entering one target stands the others down, so a stale outline can't linger."""
    first = DropFeedback(QWidget(), "PowerCanvas")
    second = DropFeedback(QWidget(), "EffectCard")

    first.show_reject()
    assert first.state == DropFeedback.REJECT

    second.show_accept()
    assert second.state == DropFeedback.ACCEPT
    assert first.state == DropFeedback.IDLE
    clear_all()


def test_a_borderless_target_washes_without_an_outline(qapp) -> None:
    """A group nested inside a card that draws its own border: no stacked outlines."""
    feedback = DropFeedback(QWidget(), "ModifierGroup", radius="radius.chip", border=False)
    feedback.show_accept()
    rules = feedback._widget.styleSheet()

    assert "background:" in rules
    assert "border:" not in rules
    clear_all()


def test_setting_a_new_resting_style_does_not_disturb_a_live_highlight(target) -> None:
    """The canvas re-declares its idle border when it gains its first card."""
    target.show_accept()
    target.set_idle("border: 1px solid black;", apply_now=False)

    assert target.state == DropFeedback.ACCEPT
    assert theme.color("accent") in target._widget.styleSheet()

    target.clear()
    assert "solid black" in target._widget.styleSheet()


def test_a_plain_widget_target_actually_paints_its_wash(qapp) -> None:
    """ModifierGroup is a QWidget, which ignores a stylesheet background by default.

    It paints itself before the style engine is consulted, so the rule is applied
    and simply never drawn — which is how the extras/flaws group's drop highlight
    was silently flat. WA_StyledBackground routes the background through the style.
    """
    from PySide6.QtCore import Qt

    from mm_companion.ui.power_constructor.modifier_chip import ModifierGroup

    group = ModifierGroup("Extras")
    assert group.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
