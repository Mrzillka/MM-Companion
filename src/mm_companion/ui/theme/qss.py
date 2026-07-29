"""Build the application-wide stylesheet for a theme.

:func:`build` turns a :class:`~mm_companion.ui.theme.tokens.Theme` into the one
string handed to ``QApplication.setStyleSheet``. What it emits depends on the
preset's ``chrome.mode``:

``system``
    Nothing but the focus ring. Every widget keeps the native platform style, so
    the app looks exactly as it did before there was a theme layer at all, and
    it still follows the OS light/dark setting through Qt's ``palette()``
    expressions in the tokens.

``styled``
    The theme dresses the window itself: surfaces, block frames and title bars,
    cards, canvases, chips and input chrome, all from tokens.

Three rules constrain everything below, each learned from a real breakage:

1. **Never select a bare widget class that appears inside content.** A rule on
   plain ``QFrame`` or ``QLabel`` is inherited by every separator and every label
   nested in a card. Selectors here are object names (``#blockFrame``) or classes
   that only ever name a whole component (``EffectCard``, ``PowerCanvas``).
2. **Never set** ``font-size``. A stylesheet font size outranks the widget font,
   and the powers section animates a card's point size through ``QFont`` — a QSS
   size would make a switched-off card stop shrinking. Sizes come from
   :func:`mm_companion.ui.theme.font_size` instead.
3. **Keep backgrounds off the ancestors of an animated card.** Several widgets
   carry a ``QGraphicsOpacityEffect`` (the powers cards, the canvas drop fade, the
   GM notice). A stylesheet background on an ancestor paints behind the whole
   subtree and fights the effect, so surfaces stop at the block frame.
"""

from __future__ import annotations

from mm_companion.ui.theme.tokens import Theme, UnknownToken

#: Widget classes that take a focus ring. Every one of them is either wheel-guarded
#: (see :mod:`mm_companion.ui.wheel_guard`, where a control ignores the wheel until
#: it has focus) or a button — so the ring is what makes that modality visible.
_FOCUSABLE = (
    "QSpinBox",
    "QDoubleSpinBox",
    "QComboBox",
    "QLineEdit",
    "QTextEdit",
    "QPlainTextEdit",
    "QAbstractItemView",
    "QPushButton",
    "QToolButton",
)


def _color(theme: Theme, name: str, fallback: str | None = None) -> str:
    try:
        return str(theme.colors[name])
    except KeyError:
        if fallback is not None:
            return fallback
        raise UnknownToken("colors", name, theme.colors) from None


def _metric(theme: Theme, name: str, fallback: float | None = None) -> float:
    value = theme.metrics.get(name)
    if isinstance(value, (int, float)):
        return value
    if fallback is not None:
        return fallback
    raise UnknownToken("metrics", name, theme.metrics)


def build(theme: Theme) -> str:
    """The global stylesheet for *theme*."""
    blocks = [_focus_rules(theme)] if theme.chrome.focus_ring else []
    if theme.styled:
        blocks.append(_chrome_rules(theme))
    return "\n\n".join(block for block in blocks if block)


def _focus_rules(theme: Theme) -> str:
    """An accent ring on the focused control.

    The only visible sign of the wheel guard's rule that a spin box or combo box
    ignores the scroll wheel until it holds keyboard focus — without it, "why did
    my scroll go to the page?" has no answer on screen.
    """
    ring = _color(theme, "focus.ring")
    width = int(_metric(theme, "focus.width", 2))
    radius = int(_metric(theme, "radius.card", 4))
    selector = ", ".join(f"{name}:focus" for name in _FOCUSABLE)
    return (
        f"/* focus ring */\n"
        f"{selector} {{\n"
        f"    border: {width}px solid {ring};\n"
        f"    border-radius: {radius}px;\n"
        f"}}"
    )


def _chrome_rules(theme: Theme) -> str:
    """Surfaces, frames and input chrome for a preset that dresses the window itself."""
    c = lambda name, fallback=None: _color(theme, name, fallback)  # noqa: E731 - local alias
    m = lambda name, fallback=None: int(_metric(theme, name, fallback))  # noqa: E731

    window = c("surface.window")
    block = c("surface.block")
    titlebar = c("surface.titlebar")
    field = c("surface.field")
    text = c("text.primary")
    muted = c("text.muted")
    border = c("border.block")
    width = m("border.width")
    radius_block = m("radius.block", m("radius.card"))
    radius_field = m("radius.field", m("radius.card"))
    pad = m("space.sm")

    return "\n\n".join(
        (
            # The menu bar. Stated despite the palette because the native Windows
            # style paints its menu chrome from the system theme and ignores the
            # application palette, which leaves a light preset with a dark menu bar
            # bolted to the top of it. These are top-level chrome classes, never
            # nested inside a card, so they are not the container selectors the
            # module docstring rules out.
            "/* menus */\n"
            f"QMenuBar {{ background: {window}; color: {text}; }}\n"
            f"QMenuBar::item:selected {{ background: {titlebar}; }}\n"
            f"QMenu {{ background: {block}; color: {text};"
            f" border: {width}px solid {border}; }}\n"
            f"QMenu::item:selected {{ background: {c('accent')};"
            f" color: {c('text.on-badge')}; }}",
            # One block: a titled frame on the block surface. Surfaces stop here —
            # see rule 3 in the module docstring. Colours the palette already
            # carries (window, text, selection) are deliberately absent: this
            # sheet only says what a palette cannot, which is geometry.
            "/* sheet blocks */\n"
            f"#blockFrame {{\n"
            f"    background: {block};\n"
            f"    border: {width}px solid {border};\n"
            f"    border-radius: {radius_block}px;\n"
            f"}}\n"
            f"#blockTitleBar {{\n"
            f"    background: {titlebar};\n"
            f"    border-top-left-radius: {radius_block}px;\n"
            f"    border-top-right-radius: {radius_block}px;\n"
            f"    border-bottom: {width}px solid {border};\n"
            f"}}\n"
            f"#blockTitleLabel {{ color: {text}; font-weight: bold; }}\n"
            f"#blockCanvas {{ background: {window}; }}\n"
            f"#dropIndicator {{ background-color: {c('drop.indicator')}; }}\n"
            # The pinned strip: the same window surface as the page it sits
            # beside, divided from it by one border along its inner edge, and a
            # title-bar-coloured grip to drag it by.
            f"#pinnedPanel {{ background: {window}; }}\n"
            f"#pinnedGrip {{ background: {titlebar};"
            f" border: {width}px solid {border}; border-radius: {radius_field}px; }}",
            # Input chrome. Scoped to the concrete input classes, never to their
            # containers, so nothing leaks into a card's labels. The fill and text
            # come from the palette's Base/Text; only the frame is stated here.
            "/* inputs */\n"
            f"QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{\n"
            f"    background: {field};\n"
            f"    border: {width}px solid {border};\n"
            f"    border-radius: {radius_field}px;\n"
            f"    padding: 0 {pad}px;\n"
            f"}}\n"
            f"QHeaderView::section {{\n"
            f"    background: {titlebar};\n"
            f"    color: {muted};\n"
            f"    border: none;\n"
            f"    padding: {m('space.xs')}px {pad}px;\n"
            f"    font-weight: bold;\n"
            f"}}",
            # Tabs (the Power Constructor's Effects / Extras / Flaws palette).
            # Stated for the same reason as the menus: the native style paints a
            # tab from the system theme, which on a light preset over a dark OS
            # leaves the *unselected* tabs' text near-invisible.
            "/* tabs */\n"
            f"QTabWidget::pane {{ background: {block};"
            f" border: {width}px solid {border}; }}\n"
            f"QTabBar::tab {{\n"
            f"    background: {titlebar};\n"
            f"    color: {muted};\n"
            f"    border: {width}px solid {border};\n"
            f"    border-bottom: none;\n"
            f"    border-top-left-radius: {radius_field}px;\n"
            f"    border-top-right-radius: {radius_field}px;\n"
            f"    padding: {m('space.sm')}px {m('space.lg')}px;\n"
            f"}}\n"
            f"QTabBar::tab:selected {{ background: {block}; color: {text};"
            f" font-weight: bold; }}\n"
            f"QTabBar::tab:hover {{ color: {text}; }}",
            # Buttons. autoRaise tool buttons (the block title bar's ↗ and ✕) stay
            # flat until hovered, so the title bar keeps reading as one strip.
            "/* buttons */\n"
            f"QPushButton {{\n"
            f"    border: {width}px solid {border};\n"
            f"    border-radius: {radius_field}px;\n"
            f"    padding: {m('space.xs')}px {m('space.lg')}px;\n"
            f"}}\n"
            f"QPushButton:hover {{ border-color: {c('accent')}; }}\n"
            f"QToolButton {{ background: transparent; border: none; }}\n"
            f"QToolButton:hover {{ background: {c('surface.card', block)}; "
            f"border-radius: {radius_field}px; }}",
        )
    )
