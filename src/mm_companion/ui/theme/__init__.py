"""The look of the app, as named tokens rather than literals at the point of use.

Every colour, radius, border width, column minimum and font size the interface
uses is a **token** — a dotted name like ``"tint.worse"`` or ``"radius.card"`` —
whose value comes from the active theme preset. Widget code asks for the name::

    from mm_companion.ui import theme

    label.setStyleSheet(f"color: {theme.color('tint.worse')};")
    frame.setStyleSheet(
        f"border: {theme.metric('border.width')}px solid {theme.color('border.card')};"
        f" border-radius: {theme.metric('radius.card')}px;"
    )

Before this, each of those was a literal in whichever section needed it — 78
``setStyleSheet`` calls across 24 files, with the same warning amber written out
twice and the modifier-chip tints reachable only by grep. Retuning the look meant
hunting them all down, and "make the borders rounder" wasn't expressible at all.

Presets
-------
``classic`` is the default and reproduces the historical look exactly: the
native platform widget style, with only the handful of semantic tints the app
has always drawn itself. Designed presets (``slate-dark``, ``parchment-light``)
set ``chrome.mode: "styled"`` and dress the window themselves. See
:mod:`~mm_companion.ui.theme.loader` for where preset files are found and
:mod:`~mm_companion.ui.theme.qss` for what ``styled`` actually emits.

Two rules for anything reading from here
----------------------------------------
1. **Font sizes go on the** ``QFont``, **never into a stylesheet.** A stylesheet
   ``font-size`` outranks the widget font, which would make the powers card's
   switched-off transition (it interpolates point size) silently stop working.
   Use :func:`font_size` with ``QFont.setPointSizeF``.
2. **Don't cache a token in a module constant.** Values change when the user
   switches preset; read them where they are used so a rebuilt widget picks the
   new value up.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from mm_companion.core import storage
from mm_companion.ui.theme.loader import (
    DEFAULT_THEME_ID,
    available_themes,
    clear_theme_cache,
    resolve_id,
)
from mm_companion.ui.theme.tokens import (
    Chrome,
    Theme,
    UnknownToken,
    contrast_ratio,
    is_literal_color,
    rgba,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PySide6.QtWidgets import QApplication

__all__ = [
    "Chrome",
    "DEFAULT_THEME_ID",
    "Theme",
    "UnknownToken",
    "active_theme",
    "available_themes",
    "apply",
    "box",
    "color",
    "contrast_ratio",
    "font_family",
    "font_size",
    "is_literal_color",
    "metric",
    "reset",
    "set_active_theme",
    "tint_rgba",
    "wash",
]

# The resolved active preset. Cached because token lookups happen once per widget
# per style change — resolving one would otherwise re-read settings.json from disk
# every time a label picks its colour. Cleared by reset().
_active: Theme | None = None


def active_theme() -> Theme:
    """The preset currently in force, from the ``theme`` setting."""
    global _active
    if _active is None:
        _active = available_themes()[resolve_id(storage.theme_name())]
    return _active


def reset() -> None:
    """Forget the cached preset and the on-disk scan.

    Call after the ``theme`` setting changes, after a preset file is edited, or
    (in tests) after the workspace is pointed somewhere else.
    """
    # Imported here, not at module scope: block_sizes reads the active theme, so
    # importing it up top would close the loop.
    from mm_companion.ui.block_sizes import clear_block_size_cache

    global _active
    _active = None
    clear_theme_cache()
    clear_block_size_cache()


def set_active_theme(theme_id: str, app: QApplication | None = None) -> Theme:
    """Persist *theme_id* as the active preset and re-dress *app* if given.

    Re-applying the stylesheet re-dresses everything the theme styles through
    QSS. Widgets that build their own styling in their constructors (a power
    card's border, say) only pick the new tokens up when they are next rebuilt,
    which is why the UI offers a relaunch after a switch.
    """
    storage.update_settings(theme=theme_id)
    reset()
    theme = active_theme()
    if app is not None:
        apply(app)
    return theme


def apply(app: QApplication) -> None:
    """Install the active preset's global stylesheet (and font family) on *app*.

    The family goes on the application *font* rather than into the stylesheet, for
    the same reason point sizes do: a QSS ``font-family`` would outrank each
    widget's own ``QFont``, and the powers cards animate through that font.
    """
    from mm_companion.ui.theme import qss

    family = font_family()
    if family:
        font = app.font()
        font.setFamily(family)
        app.setFont(font)
    app.setStyleSheet(qss.build(active_theme()))


def _lookup(group: str, name: str) -> object:
    theme = active_theme()
    tokens = getattr(theme, group)
    try:
        return tokens[name]
    except KeyError:
        raise UnknownToken(group, name, tokens) from None


def color(name: str) -> str:
    """The colour token *name*, e.g. ``"tint.worse"`` or ``"border.card"``.

    The returned string is whatever the theme declared: usually ``#rrggbb``, but
    a preset that wants to follow the OS may give a Qt stylesheet expression such
    as ``palette(mid)``. Both are valid in a stylesheet; only the former can have
    a wash derived from it (see :func:`wash`).
    """
    return str(_lookup("colors", name))


def metric(name: str) -> int | float:
    """A numeric token — a radius, border width, spacing step or column minimum."""
    value = _lookup("metrics", name)
    if not isinstance(value, (int, float)):
        raise UnknownToken("metrics", name, active_theme().metrics)
    return value


def box(name: str) -> tuple[int, int, int, int]:
    """A four-number token as ``(left, top, right, bottom)``, for content margins."""
    value = _lookup("metrics", name)
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 4:
        raise ValueError(f"Metric token {name!r} is not a four-number box: {value!r}")
    left, top, right, bottom = (int(v) for v in value)
    return left, top, right, bottom


def font_size(name: str) -> float:
    """A point size, for ``QFont.setPointSizeF`` — never for a stylesheet."""
    value = _lookup("typography", name)
    if not isinstance(value, (int, float)):
        raise UnknownToken("typography", name, active_theme().typography)
    return float(value)


def font_family() -> str:
    """The theme's font family, or ``""`` to keep the platform default."""
    value = active_theme().typography.get("family")
    return str(value) if value else ""


def wash(name: str, alpha: float) -> str:
    """A translucent ``rgba(...)`` of colour token *name*, for a fill behind content.

    Pairs with a solid border of the same token so the two never drift apart.
    """
    return rgba(color(name), alpha)


def tint_rgba(hex_color: str, alpha: float) -> str:
    """A translucent ``rgba(...)`` of an already-resolved literal colour.

    Prefer :func:`wash`, which takes the token name. This one is for the few
    places holding a colour that did not come from a token lookup.
    """
    return rgba(hex_color, alpha)
