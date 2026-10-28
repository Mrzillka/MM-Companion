"""The GM's quick-damage control: a Toughness save's result, as four buttons.

A Damage attack lands, the target rolls Toughness, and what that costs them is a
table — the ladder in ``effects.json`` that
:mod:`mm_companion.core.rules.damage` reads. Before this the GM read the outcome
off the roll history as a *sentence* ("Incapacitated!") and then went hunting for
three conditions in a menu of thirty-nine, per creature, per round.

So: one round button per rung. The tick is a **made** save, which is not nothing —
the target still takes a Hit — and 1/2/3 are degrees of failure. What each one will
actually do is resolved against *this* creature and written into its tooltip, so an
escalation ("already Dazed, so Stunned") is visible before the click rather than a
surprise after it.

The buttons are round, and their shape and colour come from a **widget-level**
stylesheet rather than the theme's application sheet — the same bargain
:class:`~mm_companion.ui.compact.CompactOverlayButton` and
:class:`~mm_companion.ui.pin_panel.PinChip` strike, and here it is forced: Classic
emits no ``QToolButton`` rules at all, so an app-level rule would exist under some
presets and not others.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    BOOK_MINION_RULES,
    DamageStep,
    MinionRules,
    damage_step_summary,
    damage_steps,
    minion_step,
)
from mm_companion.ui import theme

#: What the row is captioned. The ladder is the *damage* ladder, and "Hit" is the
#: one thing every rung on it has in common.
ROW_LABEL = "Hit:"
#: What the one failure button of a **minion**'s row reads: any failed save is its
#: worst (PDF p25), so the degrees collapse into one.
MINION_FAIL_LABEL = "✗"


#: How strongly the deepest rung of the ladder is filled, and how strongly the
#: shallowest; every rung between is interpolated, so the bad end of the row is
#: obvious without reading the numbers on it. Washed rather than filled at all:
#: four saturated buttons on every card would shout over the pinned numbers, which
#: are what the GM is actually reading.
FILL_MIN = 0.12
FILL_MAX = 0.38


def _button_style(diameter: int, tint: str, fill: float) -> str:
    """One round button's own stylesheet, in tokens every preset defines.

    It states no ``color``, which is deliberate rather than an omission: the text
    colours (``text.primary`` and friends) belong to the *styled* presets, and
    Classic has none — a rule naming one would raise inside a card's construction
    under the default theme. The glyph inherits the palette, as
    :class:`~mm_companion.ui.pin_panel.PinChip` lets its own label do.
    """
    border = int(theme.metric("border.width"))
    return (
        "QToolButton {"
        f" background: {theme.wash(tint, fill)};"
        f" border: {border}px solid {theme.color(tint)};"
        f" border-radius: {diameter // 2}px; }}\n"
        f"QToolButton:hover {{ background: {theme.wash(tint, min(1.0, fill + 0.28))}; }}\n"
        f"QToolButton:pressed {{ background: {theme.wash(tint, min(1.0, fill + 0.45))}; }}"
    )


class DamageRow(QWidget):
    """The ladder as a caption and a row of round buttons.

    Holds no character state of its own beyond the one it points at for tooltips —
    the click only *announces* a step, and applying it is the GM window's job, since
    that is what owns the model, the open sheet and the save.
    """

    #: The chosen step's :attr:`~mm_companion.core.rules.damage.DamageStep.index` —
    #: ``0`` for a made save, ``1..n`` for degrees of failure.
    stepChosen = Signal(int)

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self._character: Character | None = None
        self._steps = damage_steps(data)
        self._buttons: list[QToolButton] = []
        #: The session's minion rules — read when the creature is a minion.
        self._rules = BOOK_MINION_RULES

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xs")))

        self._caption = QLabel(ROW_LABEL)
        caption_font = self._caption.font()
        caption_font.setPointSizeF(theme.font_size("size.terms"))
        self._caption.setFont(caption_font)
        layout.addWidget(self._caption)

        diameter = int(theme.metric("gm.damage-button"))
        for step in self._steps:
            button = QToolButton()
            button.setText(step.label)
            button.setFixedSize(diameter, diameter)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # Chrome, reached with the mouse. A focusable button on a card whose
            # neighbours shed their focus chrome reads as a lit "on" state.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            # The glyph's size goes on the font, never in the sheet below: a
            # stylesheet ``font-size`` outranks a widget's QFont everywhere here.
            font = button.font()
            font.setPointSizeF(theme.font_size("size.terms"))
            button.setFont(font)
            button.setStyleSheet(
                _button_style(diameter, _tint_for(step), _fill_for(step, self._steps))
            )
            button.clicked.connect(lambda _checked=False, i=step.index: self.stepChosen.emit(i))
            layout.addWidget(button)
            self._buttons.append(button)
        layout.addStretch()

        # An empty row would be a caption and nothing else. A ruleset whose damage
        # effect carries no ladder simply has no control.
        #
        # ``hide()`` only, never ``setVisible(True)``: the row is built parentless
        # (``npc_card`` adds it to a layout afterwards) and showing a parentless
        # widget realizes it as a momentary top-level window — a small window
        # flashing on screen on Windows, on every NPC card build, which is every
        # condition the GM applies. The same trap ``2536db9`` fixed for the power
        # card's header buttons, and the mirror of the one
        # :func:`~mm_companion.ui.widgets.discard_widget` closes on the way out.
        # Showing it is not ours to do anyway: a fresh widget is made visible by the
        # parent it is added to.
        if not self._steps:
            self.hide()
        self.refresh()

    @property
    def steps(self) -> tuple[DamageStep, ...]:
        """The ladder this row is offering."""
        return self._steps

    def button_tooltips(self) -> list[str]:
        """Each button's current tooltip, in order — what the tests assert on."""
        return [button.toolTip() for button in self._buttons]

    def set_character(self, character: Character | None) -> None:
        """Point the row at the creature its buttons will act on, and restate them."""
        self._character = character
        self.refresh()

    def set_minion_rules(self, rules: MinionRules) -> None:
        """The session's minion rules changed: restate the row for them."""
        self._rules = rules
        self.refresh()

    def collapsed_to_worst(self) -> bool:
        """Whether the row shows a minion's two buttons: the made save and one failure.

        A minion that fails takes the worst degree whatever it rolled, so 1/2/3 would
        be three buttons doing the same thing. The last one stays — it *is* the worst
        rung, and the index it emits is the one the window applies.
        """
        return bool(
            self._character is not None and self._character.minion and self._rules.worst_degree
        )

    def visible_labels(self) -> list[str]:
        """The buttons a GM would see, in order — what the tests assert on."""
        return [b.text() for b in self._buttons if not b.isHidden()]

    def refresh(self) -> None:
        """Re-derive every tooltip from the character's *current* conditions.

        Cheap and called on every card redraw, because the answer changes as the
        creature does: the same button reads "Hit + Dazed" on a fresh goon and
        "Hit + Stunned — escalated" on one that has already been dazed.

        """
        worst_only = self.collapsed_to_worst()
        deepest = max((s.index for s in self._steps), default=0)
        for button, step in zip(self._buttons, self._steps, strict=True):
            # Hidden and shown on a child of this row, never on a parentless widget,
            # so neither call can flash a window (see the note above).
            button.setHidden(worst_only and 0 < step.index < deepest)
            failure = worst_only and step.index == deepest and step.index > 0
            button.setText(MINION_FAIL_LABEL if failure else step.label)
            button.setToolTip(self._tooltip(step, minion_failure=failure))

    def _tooltip(self, step: DamageStep, *, minion_failure: bool = False) -> str:
        if minion_failure:
            head = "Save failed (a minion takes the worst degree)"
        else:
            head = "Save made" if step.index == 0 else f"{step.index} degree(s) of failure"
        if self._character is None:
            return f"{head} — {step.caption}"
        if self._character.minion:
            step = minion_step(step, self._data, self._rules)
        return f"{head} — {damage_step_summary(self._character, step, self._data)}"


def _tint_for(step: DamageStep) -> str:
    """Which semantic tint a step wears.

    A made save is a *warning* rather than a loss — the target came through it and
    still took a Hit; every degree of failure is the loss tint, which the deepening
    wash then separates by degree.
    """
    return "tint.warning" if step.index == 0 else "tint.worse"


def _fill_for(step: DamageStep, steps: tuple[DamageStep, ...]) -> float:
    """How strongly a step is washed: the deepest rung of the ladder is the fullest.

    Derived from the ladder's own length rather than a table of four numbers, so a
    ruleset with five rungs spreads across the same range instead of running off the
    end of one.
    """
    deepest = max((s.index for s in steps), default=0)
    if deepest <= 0:
        return FILL_MIN
    return FILL_MIN + (FILL_MAX - FILL_MIN) * (step.index / deepest)
