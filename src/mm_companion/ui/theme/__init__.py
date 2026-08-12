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
    bundled_ids,
    clear_theme_cache,
    delete_workspace_theme,
    is_workspace_theme,
    resolve_id,
    save_workspace_theme,
    shadows_bundled,
    theme_to_dict,
    unique_theme_id,
    workspace_theme_path,
)
from mm_companion.ui.theme.qss import ARROWLESS_PROPERTY
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
    "ARROWLESS_PROPERTY",
    "Chrome",
    "DEFAULT_THEME_ID",
    "Theme",
    "UnknownToken",
    "active_theme",
    "arrow_columns",
    "asset",
    "available_themes",
    "apply",
    "box",
    "bundled_ids",
    "color",
    "contrast_ratio",
    "delete_workspace_theme",
    "font_family",
    "font_family_mono",
    "font_size",
    "is_literal_color",
    "is_workspace_theme",
    "metric",
    "preview_theme",
    "reset",
    "save_workspace_theme",
    "set_active_theme",
    "set_preview",
    "shadows_bundled",
    "theme_to_dict",
    "tint_rgba",
    "unique_theme_id",
    "wash",
    "workspace_theme_path",
]

# The resolved active preset. Cached because token lookups happen once per widget
# per style change — resolving one would otherwise re-read settings.json from disk
# every time a label picks its colour. Cleared by reset().
_active: Theme | None = None

# An unsaved draft being previewed live, from the Settings window. While set it
# stands in front of _active for every lookup — deliberately without touching the
# `theme` setting or _active itself, so reverting is set_preview(None) and costs
# neither a disk read nor a re-resolve. Cleared by reset() too: that call means
# "forget everything", and a preview outliving it would be a lie.
_preview: Theme | None = None


def active_theme() -> Theme:
    """The preset currently in force: a live preview if one is set, else the setting."""
    global _active
    if _preview is not None:
        return _preview
    if _active is None:
        _active = available_themes()[resolve_id(storage.theme_name())]
    return _active


def preview_theme() -> Theme | None:
    """The unsaved draft currently being previewed, or ``None``."""
    return _preview


def set_preview(draft: Theme | None, app: QApplication | None = None) -> None:
    """Dress *app* in an unsaved *draft*, or hand it back to the saved preset.

    The one seam the Settings window's live preview goes through. Whoever opens a
    preview owns closing it: a window that leaves one set has the app wearing a
    theme that exists nowhere on disk until it restarts.
    """
    from mm_companion.ui.block_sizes import clear_block_size_cache

    global _preview
    _preview = draft
    # Block bounds are cached per theme *id*, and a draft carries the id of the
    # preset it is editing — so the cached entry is the wrong one both on the way
    # in and on the way back out.
    clear_block_size_cache()
    if app is not None:
        apply(app)


def reset() -> None:
    """Forget the cached preset, any live preview, and the on-disk scan.

    Call after the ``theme`` setting changes, after a preset file is written or
    deleted, or (in tests) after the workspace is pointed somewhere else.
    """
    # Imported here, not at module scope: block_sizes reads the active theme, so
    # importing it up top would close the loop.
    from mm_companion.ui.block_sizes import clear_block_size_cache

    global _active, _preview, _arrow_columns
    _active = None
    _preview = None
    # The arrow column belongs to the base style, not to the preset — but a test
    # that swaps styles has no other seam to invalidate it through.
    _arrow_columns = None
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
    """Dress *app* in the active preset: palette, font, then stylesheet.

    Three mechanisms, each doing what only it can:

    * The **palette** carries colour to every widget through Qt's own
      inheritance, and is what the ``palette(role)`` expressions in the tokens
      resolve against. A ``system`` preset installs none, keeping the OS palette
      — and with it the OS light/dark setting. See
      :mod:`mm_companion.ui.theme.palette`.
    * The **font family** goes on the application font rather than into the
      stylesheet, for the same reason point sizes do: a QSS ``font-family``
      outranks each widget's own ``QFont``, which the powers cards animate.
    * The **stylesheet** states only what the other two cannot — geometry, and
      the object-named chrome of the blocks.
    """
    from mm_companion.ui.theme import palette as palette_module
    from mm_companion.ui.theme import qss

    theme = active_theme()

    palette = palette_module.build_palette(theme)
    if palette is not None:
        _remember_os_palette(app)
        app.setPalette(palette)
    elif _palette_installed:
        # Switching back to a system preset: hand back the palette the app actually
        # had, or the previous preset's colours would linger with nothing left to
        # state them. It has to be the *remembered* one, not
        # ``style().standardPalette()``, which fabricates a fresh light palette on
        # Windows however the OS is set — leaving the window light while the native
        # style went on painting its buttons for dark mode, which is how a message
        # box ended up with unreadable "Discard"/"Cancel" text after a theme change.
        app.setPalette(_os_palette if _os_palette is not None else app.style().standardPalette())
    _remember_palette(palette is not None)

    family = font_family()
    if family:
        font = app.font()
        font.setFamily(family)
        app.setFont(font)

    app.setStyleSheet(qss.build(theme, arrow_columns(app)))


# The platform style's own arrow column, measured once. Cached because measuring
# means clearing the stylesheet, and apply() runs on every keystroke of the theme
# editor's live preview.
_arrow_columns = None


def arrow_columns(app: QApplication):
    """How much room the platform style keeps for a spin box's / combo box's arrows.

    A stylesheet that states any box on those widgets makes Qt lay the edit field
    out from the box alone, over the top of the arrows — so the sheet has to give
    that column back as padding, and how wide it is is a fact about the *style*
    (50px under ``windows11``, 15px under ``Fusion``), not about the theme. Hence a
    measurement rather than a token; :func:`mm_companion.ui.theme.qss.build` takes
    the answer.

    Measured with no sheet installed, because our own would be what distorts it.
    That costs one extra assignment, which is why the result is remembered — and
    why :func:`reset` drops it, so a test that swaps the base style measures again.
    """
    from mm_companion.ui.theme import qss

    global _arrow_columns
    if _arrow_columns is not None:
        return _arrow_columns

    from PySide6.QtWidgets import (
        QComboBox,
        QSpinBox,
        QStyle,
        QStyleOptionComboBox,
        QStyleOptionSpinBox,
    )

    previous = app.styleSheet()
    app.setStyleSheet("")
    try:
        spin = QSpinBox()
        spin.setRange(0, 999)
        spin.ensurePolished()
        spin.resize(150, spin.sizeHint().height())
        spin_option = QStyleOptionSpinBox()
        spin_option.initFrom(spin)
        spin_option.subControls = QStyle.SubControl.SC_All
        spin_option.buttonSymbols = spin.buttonSymbols()
        spin_option.frame = spin.hasFrame()
        buttons = [
            spin.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox, spin_option, sub_control, spin
            )
            for sub_control in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown)
        ]

        combo = QComboBox()
        combo.addItem("Measured")
        combo.ensurePolished()
        combo.resize(150, combo.sizeHint().height())
        combo_option = QStyleOptionComboBox()
        combo_option.initFrom(combo)
        combo_option.subControls = QStyle.SubControl.SC_All
        arrow = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            combo_option,
            QStyle.SubControl.SC_ComboBoxArrow,
            combo,
        )

        spin_column = 150 - min(rect.x() for rect in buttons)
        combo_column = 150 - arrow.x()
        spin.deleteLater()
        combo.deleteLater()
    except Exception:  # pragma: no cover - a style that will not be measured
        # Never let a paint-path fallback take the app down; the constants are a
        # perfectly serviceable answer, just not a tailored one.
        app.setStyleSheet(previous)
        return qss.FALLBACK_ARROW_COLUMNS

    fallback_spin, fallback_combo = qss.FALLBACK_ARROW_COLUMNS
    # A degenerate measurement (a style that reports nothing) would silently remove
    # the padding and put the bug back, so the fallback is the floor.
    _arrow_columns = (max(spin_column, fallback_spin), max(combo_column, fallback_combo))
    return _arrow_columns


# Whether the last apply() installed a palette of our own, so switching back to a
# system preset knows it has one to undo.
_palette_installed = False

# The palette the app was wearing before we ever replaced it — the OS one, and the
# only faithful thing to hand back when a system preset is chosen again. Captured
# once, on the first install, because after that ``app.palette()`` is ours.
_os_palette = None


def _remember_palette(installed: bool) -> None:
    global _palette_installed
    _palette_installed = installed


def _remember_os_palette(app: QApplication) -> None:
    """Keep a copy of the OS palette, the first time we are about to replace it."""
    from PySide6.QtGui import QPalette

    global _os_palette
    if _os_palette is None and not _palette_installed:
        _os_palette = QPalette(app.palette())


def _lookup(group: str, name: str) -> object:
    """The value of token *name* in *group*, from the active preset or the default.

    The fallback is what makes a hand-written or Settings-saved preset survive an
    upgrade. Those are written as full snapshots with no ``extends``, so a token
    the app grows *after* one was saved is simply absent from it — and without a
    fallback the first widget to ask for it would raise, on the user's machine,
    because of a file they wrote months earlier. Take Classic's value instead: the
    look is slightly off in one place rather than the app being unusable.

    A genuine typo in widget code still raises, since Classic won't have it either.
    """
    theme = active_theme()
    tokens = getattr(theme, group)
    if name in tokens:
        return tokens[name]
    if theme.id != DEFAULT_THEME_ID:
        backing = getattr(available_themes().get(DEFAULT_THEME_ID), group, {})
        if name in backing:
            return backing[name]
    raise UnknownToken(group, name, tokens)


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


def font_family_mono() -> str:
    """The theme's monospace family, or ``""`` to keep the platform's fixed font.

    Read off the preset directly rather than through :func:`_lookup`, like
    :func:`font_family`: a preset saved before this token existed answers
    ``None``, and "" is already the right answer for that — the platform's own
    fixed-width font is a better guess than any theme file's.
    """
    value = active_theme().typography.get("family.mono")
    return str(value) if value else ""


def asset(name: str) -> str:
    """Which artwork variant the theme wants for *name* — ``"die"``, ``"hero-point"``.

    The value is a variant id from :mod:`mm_companion.ui.svg_assets`, not a path.
    Resolve it there, at the point of use, and let that module fall back if the
    preset names something it doesn't have — a hand-edited theme file must not be
    able to raise inside a paint path.
    """
    return str(_lookup("assets", name))


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
