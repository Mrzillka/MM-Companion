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
"""

from __future__ import annotations

#: A boosted / improved / better-than-baseline value (enhanced traits, "better"
#: modifier tints, the stat-grid "→ total" when a power raises a trait).
TINT_BETTER = "#2e9e4f"

#: A penalty / flaw / applied condition / PL breach — anything worse than
#: baseline, and the marker on an over-cap power.
TINT_WORSE = "#d15b5b"

#: The primary interactive accent: drop-target highlights in the Power
#: Constructor, cost notices, and the home-rule (Dev-mode) override tint.
ACCENT = "#4a90d9"

#: A muted blue reserved for dice / roll information and the powers-section drag
#: indicator, kept distinct from the brighter :data:`ACCENT`.
DICE_ACCENT = "#6a86c0"
