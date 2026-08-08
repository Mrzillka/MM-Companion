"""The stat-block **card** machinery, shared by the Powers and Equipment blocks.

A card is how this app draws an assembled :class:`~mm_companion.core.powers.Power`:
a framed panel that reads top-to-bottom like a stat-block entry, is dragged by a grip
to reorder or group it, dims when its subject is switched off, and closes with a dice
footer on anything that rolls. An equipment item wraps a real ``Power``, so the
Equipment block draws the same card from the same pieces rather than a lookalike.

This package holds the pieces and nothing about *what* is being rendered — no power
tree, no equipment catalog. The owning section supplies the model, decides what a
click means, and connects the signals:

- :class:`~mm_companion.ui.cards.card.DraggableCard` — the frame, its on/off look
  (:meth:`~mm_companion.ui.cards.card.DraggableCard.set_off_progress`) and the
  one-card-lit-at-a-time hover rules.
- :class:`~mm_companion.ui.cards.drag.DragHandle` — the ``⠿`` grip, and
  :data:`~mm_companion.ui.cards.drag.NODE_MIME`, the drag payload's format.
- :class:`~mm_companion.ui.cards.node_list.NodeList` /
  :class:`~mm_companion.ui.cards.node_list.GroupHeader` — one level of the tree as a
  drop target, and a group's title bar as the other.
- :class:`~mm_companion.ui.cards.rolls.RollsFooter` /
  :class:`~mm_companion.ui.cards.rolls.RollLine` — the dice footer, where the whole
  line is the button.
- :mod:`~mm_companion.ui.cards.effects` — the per-effect summary and its game-terms
  grid.

Every widget that reads or writes the drag payload takes its format as a keyword
argument defaulting to ``NODE_MIME``, so a second board built on these cards names its
own and the two cannot accept each other's drags.
"""

from mm_companion.ui.cards.card import (
    DraggableCard,
    card_ancestors_of,
    hand_back_highlight,
    lerp,
)
from mm_companion.ui.cards.drag import NODE_MIME, DragHandle
from mm_companion.ui.cards.effects import (
    MODIFIER_STRETCH,
    TERMS_STRETCH,
    effect_summary,
    effect_title,
    effects_block,
    modifier_names,
    modifiers_column,
    role_note,
    structure_header,
    terms_grid,
    terms_style,
)
from mm_companion.ui.cards.node_list import GroupHeader, NodeList
from mm_companion.ui.cards.rolls import RollLine, RollsFooter

__all__ = [
    "MODIFIER_STRETCH",
    "NODE_MIME",
    "TERMS_STRETCH",
    "DragHandle",
    "DraggableCard",
    "GroupHeader",
    "NodeList",
    "RollLine",
    "RollsFooter",
    "card_ancestors_of",
    "effect_summary",
    "effect_title",
    "effects_block",
    "hand_back_highlight",
    "lerp",
    "modifier_names",
    "modifiers_column",
    "role_note",
    "structure_header",
    "terms_grid",
    "terms_style",
]
