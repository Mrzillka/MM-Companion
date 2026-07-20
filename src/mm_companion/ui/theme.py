"""Shared semantic colours for the sheet UI.

The single source of truth for the handful of "meaning" colours the interface
reuses — the green a boosted trait reads in, the red a penalty or breach reads
in, the blue an interactive accent reads in. Before this module each was
re-declared as a private constant in every section that needed it (``stat_grid``,
``powers``, ``power_constructor``, ``system_info``), so retuning one meant hunting
them all down. Import from here instead.

These are opaque hex strings suitable for ``color:``/``background:`` in a
stylesheet or for :class:`~PySide6.QtGui.QColor`. Where a colour sits behind
content (a drop target, a card fill) prefer pairing an accent with ``palette()``
roles and a translucent fill, as :mod:`power_constructor` does, so it reads on
light and dark themes alike.

Contrast: each shade below is tuned so its worst-case WCAG contrast ratio
against *both* a light window (~``#f0f0f0``) and a dark window (~``#2d2d2d``)
clears **3.2:1** — comfortably above the 3.0 AA threshold for the bold/large
text these tints mostly style. A single fixed hue can't also clear the 4.5 AA
threshold for small text on both themes at once (light wants a darker shade,
dark a lighter one), so the tints are balanced for the best achievable
worst-case instead. Keep any retune inside that both-themes envelope rather than
optimising for one theme.
"""

from __future__ import annotations

#: A boosted / improved / better-than-baseline value (enhanced traits, "better"
#: modifier tints, the stat-grid "→ total" when a power raises a trait).
TINT_BETTER = "#30964e"

#: A penalty / flaw / applied condition / PL breach — anything worse than
#: baseline, and the marker on an over-cap power.
TINT_WORSE = "#d15b5b"

#: The primary interactive accent: drop-target highlights in the Power
#: Constructor, cost notices, and the home-rule (Dev-mode) override tint.
ACCENT = "#4488cf"

#: A muted blue reserved for dice / roll information and the powers-section drag
#: indicator, kept distinct from the brighter :data:`ACCENT`.
DICE_ACCENT = "#6784bf"


def tint_rgba(hex_color: str, alpha: float) -> str:
    """A ``rgba(r, g, b, a)`` string for one of the tints above at a given opacity.

    For the translucent *fills* behind a drop target or a highlighted card, which
    pair a solid accent border with a faint wash of the same hue. Deriving the wash
    from the tint here (rather than hardcoding its rgb) keeps the fill in step with
    the border whenever a tint is retuned — otherwise the two drift apart, as they
    silently did after the contrast pass nudged every accent.
    """
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"
