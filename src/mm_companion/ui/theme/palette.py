"""The Qt palette a styled preset installs on the application.

A stylesheet is the wrong tool for text colour. Qt only applies a QSS rule to
widgets its selector actually matches — ``QMainWindow { color: … }`` dresses the
window and nothing inside it — so carrying a theme's foreground down to every
label would mean selecting bare ``QLabel``, and a bare-container selector is
exactly what :mod:`mm_companion.ui.theme.qss` forbids: it is inherited by every
nested widget and repaints things the rule was never meant to touch.

A :class:`QPalette` is the mechanism Qt provides for this. It reaches every
widget through normal palette inheritance, covers the chrome a stylesheet can't
reasonably reach (the menu bar, tooltips, selection colours, the greyed look of
a disabled control), and — the part that matters most here — it is what the
``palette(role)`` expressions in the tokens themselves resolve against. Without
it, a styled preset's remaining ``palette(mid)`` would quietly resolve to the
*operating system's* mid grey rather than the theme's.

Only a ``styled`` preset gets one. ``classic`` deliberately installs nothing, so
the app keeps following the OS light/dark setting exactly as it always has.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from mm_companion.ui.theme.tokens import Theme, is_literal_color

#: Palette roles fed from colour tokens. Each entry is ``role -> (token, fallback
#: token)``; the fallback covers a preset that declares a surface but not one of
#: its finer variants.
_ROLES: tuple[tuple[QPalette.ColorRole, str, str | None], ...] = (
    (QPalette.ColorRole.Window, "surface.window", None),
    (QPalette.ColorRole.WindowText, "text.primary", None),
    (QPalette.ColorRole.Base, "surface.field", "surface.block"),
    (QPalette.ColorRole.AlternateBase, "surface.card", "surface.block"),
    (QPalette.ColorRole.Text, "text.primary", None),
    (QPalette.ColorRole.Button, "surface.card", "surface.block"),
    (QPalette.ColorRole.ButtonText, "text.primary", None),
    (QPalette.ColorRole.ToolTipBase, "surface.card", "surface.block"),
    (QPalette.ColorRole.ToolTipText, "text.primary", None),
    (QPalette.ColorRole.PlaceholderText, "text.muted", None),
    (QPalette.ColorRole.Highlight, "accent", None),
    (QPalette.ColorRole.HighlightedText, "text.on-badge", None),
    (QPalette.ColorRole.Link, "accent", None),
    # Qt's structural greys, which the sunken separators and frame edges use.
    (QPalette.ColorRole.Mid, "border.card", "border.block"),
    (QPalette.ColorRole.Midlight, "surface.block", None),
    (QPalette.ColorRole.Dark, "border.group", "border.block"),
    (QPalette.ColorRole.Shadow, "border.block", None),
)

#: Roles that go muted rather than absent when a control is disabled. Qt's own
#: default here is a washed-out grey computed from the palette, which lands
#: badly on a deliberately-toned surface.
_DISABLED_ROLES = (
    QPalette.ColorRole.WindowText,
    QPalette.ColorRole.Text,
    QPalette.ColorRole.ButtonText,
)


def build_palette(theme: Theme) -> QPalette | None:
    """The palette for *theme*, or ``None`` when it should keep the OS palette.

    Returns ``None`` for a ``system`` preset, and for a ``styled`` one that has
    not declared the surfaces a palette needs — better to leave the OS palette
    alone than to install a half-filled one that leaves text unreadable.
    """
    if not theme.styled:
        return None
    if not _resolve(theme, "surface.window") or not _resolve(theme, "text.primary"):
        return None

    palette = QPalette()
    for role, token, fallback in _ROLES:
        value = _resolve(theme, token) or (_resolve(theme, fallback) if fallback else None)
        if value:
            palette.setColor(role, QColor(value))

    muted = _resolve(theme, "text.muted")
    if muted:
        for role in _DISABLED_ROLES:
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(muted))
    return palette


def _resolve(theme: Theme, token: str) -> str | None:
    """A token's value, but only when it is a colour Qt can construct.

    A ``palette(role)`` value is meaningless here — it is the very thing this
    palette defines — so it is treated as absent rather than handed to QColor,
    which would silently produce an invalid black.
    """
    value = theme.colors.get(token)
    if isinstance(value, str) and is_literal_color(value):
        return value
    return None
