"""A card's body: the per-effect summary, pairing what was bought with what it costs.

Everything here is a free function over ``(power/effect, character, data)`` rather than
a method, because the two blocks that render cards hold their character and game data
differently and neither owns this rendering. What it draws is the same either way: a
power's effects, or an equipment item's — an item *is* a :class:`Power` under the skin.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    ModifierSelection,
    Power,
    PowerEffectInstance,
)
from mm_companion.core.rules import (
    array_alternate_cost,
    array_base_index,
    effect_attack_skill_bonus,
    effect_effective_rank,
    effect_is_selected,
    effect_stat_rows,
    modifier_label,
    structure_header,
)
from mm_companion.ui import theme
from mm_companion.ui.power_constructor.terms_grid import (
    TermsGridBox,
    TermsGridStyle,
    build_terms_grid,
)
from mm_companion.ui.reflow import ShedBox
from mm_companion.ui.widgets import BOLD_STYLE, muted_style, tinted_style

# How an effect's row divides between what was bought (extras/flaws) and what it costs
# at the table (the game terms).
MODIFIER_STRETCH = 1
TERMS_STRETCH = 2


def terms_style() -> TermsGridStyle:
    """The card's copy of the terms grid: small, unbolded type.

    The numbers read as reference rather than as the point of the card. The size
    is set on the QFont (see :class:`TermsGridStyle`) so it scales with a
    switched-off card's transition instead of being pinned by a stylesheet.
    """
    return TermsGridStyle(point_size=theme.font_size("size.terms"), bold_changed=False)


def effects_block(power: Power, character: Character, data: GameData) -> QWidget | None:
    """A stacked, per-effect summary; ``None`` for a power with no effects.

    A composite power leads with its structure line (what Linked or Array means for
    the effects below), which used to live only in the card's hover tooltip.
    """
    if not power.effects:
        return None
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(6, 0, 0, 0)
    layout.setSpacing(3)
    header = structure_header(power)
    if header:
        line = QLabel(header)
        line.setStyleSheet(muted_style(italic=True))
        layout.addWidget(line)
    for index, effect in enumerate(power.effects):
        layout.addWidget(effect_summary(power, effect, index, character, data))
    return host


def effect_summary(
    power: Power,
    effect: PowerEffectInstance,
    index: int,
    character: Character,
    data: GameData,
) -> QWidget:
    """One effect: its name and effective rank, a composite role note, then its
    attached extras (green) and flaws (red) *beside* its game-term table.

    The two sit side by side rather than stacked: what a player bought (the
    modifiers) and what it costs them at the table (the terms) are read together,
    and a card is a wide, shallow thing, so the width is there for the taking.
    """
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    # Explicit, because a layout with zeroed margins can be given zero spacing too by
    # the platform style — which ran the role note straight into the rank before it
    # ("Protection 10alternate (1 pt)").
    header.setSpacing(int(theme.metric("space.sm")))
    title = QLabel(effect_title(effect, character, data))
    title.setStyleSheet(BOLD_STYLE)
    header.addWidget(title)
    note = role_note(power, index, character, data)
    if note:
        role = QLabel(note)
        role.setStyleSheet(muted_style(italic=True))
        header.addWidget(role)
    header.addStretch()
    layout.addLayout(header)

    layout.addWidget(effect_body(effect, character, data))

    # An array's other effects are not running — only one is at a time, which is what
    # the array paid for. Their numbers stay on the card (a player choosing between them
    # needs to read them) but recede, so the summary never looks like a list of bonuses
    # that all apply.
    #
    # Opacity, not ``setEnabled(False)``: every label here carries an explicit stylesheet
    # colour (bold, muted, green/red tints), and a stylesheet colour outranks the
    # disabled palette — so disabling the block greyed almost nothing. The card's own
    # switched-off look reaches for the same effect and the same token
    # (:meth:`~mm_companion.ui.cards.card.DraggableCard._apply_opacity`), and an effect
    # is only attached while it is wanted, since one forces its whole subtree to paint
    # through an offscreen buffer.
    if not effect_is_selected(power, effect, data, character):
        faded = QGraphicsOpacityEffect(box)
        faded.setOpacity(theme.metric("opacity.inactive"))
        box.setGraphicsEffect(faded)
    return box


def effect_body(effect: PowerEffectInstance, character: Character, data: GameData) -> ShedBox:
    """What a player bought, beside what it costs them at the table — until it
    cannot be both.

    The two sit side by side rather than stacked: the modifiers and the game terms
    are read together, and a card is a wide, shallow thing. A card on a page the
    user drags is not always wide, though, and the terms grid re-dealing itself
    into a single column only gets so far: past that the card was simply cut off
    down its right-hand edge.

    So it is a :class:`~mm_companion.ui.reflow.ShedBox`, and the order says which
    of the two the card would rather keep. **The terms go first**: they are the
    same numbers the Power Constructor prints, they can be read there and on the
    card's own roll footer, and a rank that has been bought with three extras and a
    flaw is a fact about the build that appears nowhere else. Past both, the card
    clips — which is where the sheet's bargain has always ended.
    """
    body = QHBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(10)
    parts: list[QWidget] = []
    modifiers = modifiers_column(effect, data)
    if modifiers is not None:
        body.addWidget(modifiers, MODIFIER_STRETCH)
        parts.append(modifiers)
    # An effect with nothing bought onto it has no column to sit beside, so the
    # terms take the whole width rather than leaving a third of the card blank.
    terms = terms_grid(effect, character, data)
    body.addWidget(terms, TERMS_STRETCH)
    parts.append(terms)
    # Indices into *parts*, which is why the terms are found by identity rather
    # than by a constant: an effect with no modifiers has them at index 0.
    order = [parts.index(terms)]
    if modifiers is not None:
        order.append(parts.index(modifiers))
    return ShedBox(parts, order, body)


def modifiers_column(effect: PowerEffectInstance, data: GameData) -> QWidget | None:
    """The effect's extras (green) over its flaws (red); ``None`` when it has neither."""
    extras = modifier_names(effect.extras, data, effect.rank)
    flaws = modifier_names(effect.flaws, data, effect.rank)
    if not extras and not flaws:
        return None
    column = QWidget()
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for caption, names, token in (
        ("Extras: ", extras, "tint.better"),
        ("Flaws: ", flaws, "tint.worse"),
    ):
        if not names:
            continue
        label = QLabel(caption + ", ".join(names))
        label.setWordWrap(True)
        label.setStyleSheet(tinted_style(token, bold=False))
        layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignTop)
    layout.addStretch()
    return column


def terms_grid(effect: PowerEffectInstance, character: Character, data: GameData) -> TermsGridBox:
    """The effect's game terms as a compact, always-visible label/value table.

    The same rows the Power Constructor's ``PowerTermsView`` shows — Type, Range,
    Action, Duration, checks, measures, derived readouts — but typeset to recede:
    small text, muted labels, two pairs per line so the table stays short. A value a
    modifier changed keeps its green/red tint (with the base value on its tooltip),
    because that is the part worth noticing at a glance.
    """
    attack_bonus = effect_attack_skill_bonus(effect, character, data)
    rows = effect_stat_rows(effect, data, character, attack_bonus)
    grid = build_terms_grid(rows, terms_style())
    grid.setContentsMargins(0, 1, 0, 0)
    grid.setVerticalSpacing(0)
    # In a box that re-deals them into one column when the card gets narrow.
    return TermsGridBox(grid)


def effect_title(effect: PowerEffectInstance, character: Character, data: GameData) -> str:
    """``"Damage 8"`` — the effect's name at its effective rank (a Strength-Based
    Damage folds in the wielder's Strength, matching the constructor)."""
    base = next((e for e in data.effects if e.id == effect.effect_id), None)
    rank = effect_effective_rank(effect, data, character)
    return f"{base.name if base else effect.effect_id} {rank}"


def modifier_names(
    selections: list[ModifierSelection], data: GameData, effect_rank: int = 0
) -> list[str]:
    """Resolve each selection to its modifier name, tagging a ranked one taken
    above rank 1 with its rank (e.g. ``"Accurate ×2"``) and a modifier with a
    typed free-text detail with it (e.g. ``"Limited (only at night)"``).

    ``effect_rank`` lets a modifier applied to only part of its effect name the band it
    covers; without one it is simply left off."""
    catalog = data.modifier_catalog()
    names: list[str] = []
    for selection in selections:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None:
            continue
        names.append(modifier_label(modifier, selection, rank_sep=" ×", effect_rank=effect_rank))
    return names


def role_note(power: Power, index: int, character: Character, data: GameData) -> str:
    """A composite effect's part: ``"base"``/``"alternate …"`` for an array or
    ``"linked"``; empty for a single or independent-multi effect."""
    if len(power.effects) < 2:
        return ""
    if power.structure == STRUCTURE_LINKED:
        return "linked"
    if power.structure == STRUCTURE_ARRAY:
        effect = power.effects[index]
        if index == array_base_index(power, data, character):
            return "base, dynamic" if effect.dynamic else "base"
        kind = "dynamic alternate" if effect.dynamic else "alternate"
        return f"{kind} ({array_alternate_cost(data, dynamic=effect.dynamic)} pt)"
    return ""
