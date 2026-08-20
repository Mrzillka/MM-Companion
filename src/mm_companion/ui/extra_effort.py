"""The Extra Effort controls: the menu of what it buys, and the dialog that charges it.

Shared UI rather than a block's own, because Extra Effort is asked for in two places and
must behave identically in both (the way ``build_condition_menu`` is shared by the three
"+" buttons that apply a condition):

* the **Powers** block's card menu, for the two uses that name one of your own effects —
  the rank increase and the power stunt — since the effect is what you right-clicked;
* the **System** block, beside the hero points, for the four that name nothing. That is
  where the other resource spent at the table already lives, and where the sentence for
  the roll history is already raised from.

Both routes end in :class:`ExtraEffortDialog`, which states the benefit, states the
price — the next rung of the fatigue ladder — and offers the two advantages that change
either: **Determination** shrugs the fatigue off (as a use of the advantage, or as a
Heroic Feat bought with a Hero Point, p22) and **Extraordinary Effort** takes two
benefits for two rungs (p86). The arithmetic is all
:mod:`mm_companion.core.rules.extra_effort`'s; this only renders it and reports back what
the player chose.

The controls are *play*, not build, so they stay available while the sheet is locked —
the same bargain the array's Split points button and the card clicks strike.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMenu,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import ExtraEffortUse, GameData
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    TARGET_EFFECT,
    USE_RANK_INCREASE,
    determination_ranks,
    effect_current_rank,
    effect_display_name,
    extra_effort_rank_increase,
    extra_effort_uses,
    fatigue_label,
    has_extraordinary_effort,
    next_fatigue,
    pushable_effects,
    pushed_effects,
)
from mm_companion.ui.widgets import BOLD_STYLE

#: Said beside an effect-naming use in the character-wide menu. It is offered there and
#: disabled rather than left out, because "you can push a power's rank" is exactly what a
#: player looks for in this menu, and a list that quietly omitted two of the book's six
#: uses would read as a shorter rule.
_ON_THE_CARD = "on the power's card"

#: The character-wide menu's title line, so the menu says what it costs before it is used.
_MENU_TITLE = "Extra Effort — {cost} at the start of your next turn"


def _hero_points(character: Character) -> int:
    """How many hero points the sheet says this character is holding."""

    try:
        return int(character.characteristics.get("hero_points", 0) or 0)
    except (TypeError, ValueError):
        return 0


def character_effort_menu(
    parent: QWidget,
    character: Character,
    game_data: GameData,
    on_chosen: Callable[[ExtraEffortUse], None],
    on_clear: Callable[[], None] | None = None,
) -> QMenu:
    """The whole list of uses, for the block that owns no effect in particular.

    Built from the ruleset's list, so a mod adding a seventh use is offered it here with
    no Python; the two that have to name an effect are shown disabled, pointing at the
    card where they can actually be taken.
    """

    menu = QMenu(parent)
    cost = fatigue_label(next_fatigue(character, game_data), game_data)
    header = menu.addAction(_MENU_TITLE.format(cost=cost))
    header.setEnabled(False)
    menu.addSeparator()
    for use in extra_effort_uses(game_data):
        if use.target == TARGET_EFFECT:
            action = menu.addAction(f"{use.label} — {_ON_THE_CARD}")
            action.setEnabled(False)
            continue
        action = menu.addAction(use.label)
        action.setToolTip(use.description)
        action.triggered.connect(lambda _checked=False, u=use: on_chosen(u))
    # The way back. Extra Effort lasts "until the end of your turn" and nothing here
    # tracks turns, so the end of one is a button — offered only while there is
    # something to take back, and character-wide because that is what a turn ending is.
    pushed = pushed_effects(character)
    if pushed and on_clear is not None:
        menu.addSeparator()
        plural = "" if len(pushed) == 1 else "s"
        clear = menu.addAction(f"Clear the ranks pushed into {len(pushed)} effect{plural}")
        clear.setToolTip("Your turn ended: every effect goes back to the rank it was bought at.")
        clear.triggered.connect(lambda _checked=False: on_clear())
    return menu


def add_power_effort_actions(
    menu: QMenu,
    power: Power,
    character: Character,
    game_data: GameData,
    on_chosen: Callable[[ExtraEffortUse, PowerEffectInstance, str], None],
) -> bool:
    """Add this power's Extra Effort entries to ``menu``; ``True`` when any were added.

    One entry per effect-naming use for a single-effect power, and a submenu per use for a
    power with several — a Blast/Dazzle array's card should not offer "Rank increase"
    twice with nothing to tell the two apart.

    A power whose every effect is Permanent gets nothing at all, which is the rule rather
    than an omission: a Permanent effect cannot be improved with Extra Effort (p104).
    """

    effects = pushable_effects(power, game_data)
    if not effects:
        return False
    uses = [use for use in extra_effort_uses(game_data) if use.target == TARGET_EFFECT]
    if not uses:
        return False
    ranks = extra_effort_rank_increase(character, game_data)
    for use in uses:
        label = use.label
        if use.id == USE_RANK_INCREASE:
            label = f"{use.label} (+{ranks})"
        if len(effects) == 1:
            action = menu.addAction(label)
            action.setToolTip(use.description)
            name = effect_display_name(effects[0], game_data)
            action.triggered.connect(
                lambda _checked=False, u=use, e=effects[0], n=name: on_chosen(u, e, n)
            )
            continue
        # addMenu(title) hands ownership back to the caller, so the submenu is parented
        # on the menu or it is collected out from under it while it is open.
        submenu = QMenu(label, menu)
        menu.addMenu(submenu)
        for effect in effects:
            name = effect_display_name(effect, game_data)
            entry = submenu.addAction(f"{name} {effect.rank}")
            entry.setToolTip(use.description)
            entry.triggered.connect(
                lambda _checked=False, u=use, e=effect, n=name: on_chosen(u, e, n)
            )
    return True


class ExtraEffortDialog(QDialog):
    """Confirm one use of Extra Effort: what it grants, and which way it is paid for.

    Reads back through :attr:`determination`, :attr:`spend_hero_point` and
    :attr:`doubled`. The caller does the spending — this only asks — so the same dialog
    serves the block that owns the hero points and the block that owns the power.
    """

    def __init__(
        self,
        character: Character,
        game_data: GameData,
        use: ExtraEffortUse,
        *,
        effect: PowerEffectInstance | None = None,
        effect_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Extra Effort")
        self._character = character
        self._data = game_data
        self._use = use
        self._effect = effect
        self._effect_name = effect_name

        layout = QVBoxLayout(self)
        headline = QLabel(use.label)
        headline.setStyleSheet(BOLD_STYLE)
        layout.addWidget(headline)
        if use.description:
            description = QLabel(use.description)
            description.setWordWrap(True)
            layout.addWidget(description)

        # Each of the three optional controls below is built either way and hidden when
        # this character cannot use it, rather than skipped: an unbuilt widget is one the
        # property readers would have to guard, and a parentless one is a stray top-level
        # window. Hidden radios stay in the dialog's auto-exclusive group, which is what
        # keeps the three ways of paying mutually exclusive.
        self._twice = QCheckBox(self)
        self._twice.hide()
        if has_extraordinary_effort(character, game_data):
            self._twice.setText("Take it twice — Extraordinary Effort")
            self._twice.setToolTip(
                "Two of the listed benefits, even two of the same one, for two rungs of "
                "the fatigue ladder (p86)."
            )
            self._twice.toggled.connect(self._refresh)
            self._twice.show()

        # Built above but laid out here, so the dialog reads in the order it happens:
        # what this use is, what it will do to the effect, then the two dials that
        # change either half of the bargain.
        benefit = self._benefit_text()
        if benefit:
            self._benefit = QLabel(benefit)
            self._benefit.setWordWrap(True)
            layout.addWidget(self._benefit)
        else:
            self._benefit = None
        if not self._twice.isHidden():
            layout.addWidget(self._twice)

        self._take_it = QRadioButton(self)
        self._take_it.setChecked(True)
        layout.addWidget(self._take_it)

        self._advantage = QRadioButton(self)
        self._advantage.hide()
        ranks = determination_ranks(character, game_data)
        if ranks:
            plural = "" if ranks == 1 else "s"
            self._advantage.setText(
                f"Shrug it off with Determination ({ranks} use{plural} per adventure)"
            )
            self._advantage.setToolTip(
                "Determination removes the Fatigue as it arrives. The app tracks no "
                "adventure, so how many uses are left is yours to keep."
            )
            self._advantage.show()
            layout.addWidget(self._advantage)

        self._hero_point = QRadioButton(self)
        points = _hero_points(character)
        self._hero_point.setText(f"Shrug it off — spend a Hero Point ({points} left)")
        self._hero_point.setToolTip(
            "A Heroic Feat of Determination: spending a Hero Point removes the Fatigue "
            "of using Extra Effort (p22)."
        )
        self._hero_point.setEnabled(points > 0)
        layout.addWidget(self._hero_point)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    # -- what the player chose ------------------------------------------------
    @property
    def doubled(self) -> bool:
        """Whether Extraordinary Effort was asked for — two benefits, two rungs."""

        return self._twice.isChecked()

    @property
    def determination(self) -> bool:
        """Whether the fatigue is shrugged off, by either route."""

        return self._advantage.isChecked() or self._hero_point.isChecked()

    @property
    def spend_hero_point(self) -> bool:
        """Whether that shrug is the Heroic Feat, and so costs a hero point."""

        return self._hero_point.isChecked()

    # -- rendering ------------------------------------------------------------
    def _benefit_text(self) -> str:
        """What the rank increase will do to the named effect, or ``""``.

        Only the rank increase changes a number on the sheet, so it is the only use with
        something to promise here; the rest are read off their own description.
        """

        if self._effect is None or self._use.id != USE_RANK_INCREASE:
            return ""
        ranks = extra_effort_rank_increase(self._character, self._data) * (2 if self.doubled else 1)
        name = self._effect_name or "This effect"
        # Where it stands now, dial and Dynamic share included — the push adds to that.
        held = effect_current_rank(self._effect, self._data, self._character)
        return f"{name} runs at rank {held + ranks} while the effort lasts (+{ranks})."

    def _refresh(self) -> None:
        """Restate the benefit and the price — the doubling moves both."""

        if self._benefit is not None:
            self._benefit.setText(self._benefit_text())
        rungs = 2 if self.doubled else 1
        landed = next_fatigue(self._character, self._data, rungs)
        self._take_it.setText(
            f"Take the fatigue — {fatigue_label(landed, self._data)} at the start of "
            "your next turn"
        )
