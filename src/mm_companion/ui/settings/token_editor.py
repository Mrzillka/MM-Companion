"""A form over *every* token in a theme, generated from the theme itself.

There is no list of settings here, and deliberately so. The editor walks the
preset's own token maps and picks a widget from the **shape of each value** — a
``#rrggbb`` gets a swatch, a four-number list gets four spin boxes, a fraction
gets a fractional spin box — so a token added in a later release, or by a mod,
appears on its own with no change to this file. The dispatch is total: an
unrecognised shape still gets a row, editing its JSON.

The editor owns the draft. It loads a :class:`~mm_companion.ui.theme.Theme`, hands
back an edited one from :meth:`TokenEditor.draft`, and emits ``changed`` — which
is what lets it be driven and asserted on with no window around it.

Two values it refuses rather than passes on:

* a non-literal in a token a translucent wash is derived from, because
  :func:`mm_companion.ui.theme.wash` raises on one, deep inside a card's paint
  path where nobody could connect it back to the colour they picked;
* nothing else — a tint that fails the contrast floor is *warned* about and
  accepted, the same bargain the rules layer strikes with a Power Level breach.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import block_sizes, theme
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.settings.color_row import ColorRow
from mm_companion.ui.theme import token_meta as meta_module
from mm_companion.ui.theme.tokens import (
    CHROME_MODES,
    CHROME_STYLED,
    MIN_CONTRAST,
    Theme,
    contrast_ratio,
    is_literal_color,
    measurement_backdrops,
)
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import BOLD_STYLE, make_double_spin_box, make_spin_box, muted_style

#: The token groups the form walks, in order.
TOKEN_GROUPS = ("colors", "metrics", "typography")

#: Chrome modes, in the order the combo box offers them, with what they mean.
_CHROME_LABELS = {
    "system": "System (follow the OS)",
    "styled": "Styled (dress the window)",
}

#: The colours ``qss._chrome_rules`` reads with no fallback, and which Classic
#: therefore does not have. A theme cannot go ``styled`` until it states them.
STYLED_REQUIRED = (
    "surface.window",
    "surface.block",
    "surface.titlebar",
    "surface.field",
    "surface.card",
    "text.primary",
    "border.block",
)

#: Sizes a styled preset usually restates as well. Copied alongside the colours
#: when seeding, but their absence is survivable — the stylesheet falls back to
#: ``radius.card`` for both.
STYLED_OPTIONAL_METRICS = ("radius.block", "radius.field")


def seed_styled_surfaces(draft: Theme, source: Theme) -> Theme:
    """*draft* with the tokens a styled stylesheet needs taken from *source*.

    Only the ones it is missing, and only those the stylesheet actually requires —
    the point is to make ``styled`` legal, not to import somebody else's look.
    Taking them from a shipped preset rather than writing literals here is also
    what keeps this file free of hardcoded colours.
    """
    colors = dict(draft.colors)
    for name in STYLED_REQUIRED:
        if not is_literal_color(str(colors.get(name, ""))) and name in source.colors:
            colors[name] = source.colors[name]
    metrics = dict(draft.metrics)
    for name in STYLED_OPTIONAL_METRICS:
        if name not in metrics and name in source.metrics:
            metrics[name] = source.metrics[name]
    return replace(
        draft,
        colors=colors,
        metrics=metrics,
        chrome=replace(draft.chrome, mode=CHROME_STYLED),
    )


#: Sensible bounds per kind of metric, chosen by the token's own name. Generous
#: rather than exact — the point is to stop a typo becoming a 90,000px margin, not
#: to have an opinion about taste.
_METRIC_RANGES: tuple[tuple[tuple[str, ...], int, int], ...] = (
    (("radius.", "border.", "focus.", "space.", "card.spacing", "group."), 0, 64),
    (("column.", "canvas."), 0, 2000),
)
_METRIC_DEFAULT_RANGE = (0, 4096)

#: The four numbers of a box metric, in the order Qt's ``setContentsMargins`` wants.
_BOX_EDGES = ("left", "top", "right", "bottom")

#: The bounds a block-size override can set.
_BLOCK_BOUNDS = ("min_width", "min_height", "max_width", "max_height")


def _compact_width() -> int:
    """Width for a spin box that shares its row with three others.

    A quarter of the value column: four of them then take about as much room as
    one ordinary field, which is what keeps a margins row from being the widest
    thing on the page and dragging a horizontal scrollbar in behind it.
    """
    return max(48, int(theme.metric("column.settings.value")) // 4)


def _metric_range(name: str) -> tuple[int, int]:
    for prefixes, low, high in _METRIC_RANGES:
        if name.startswith(prefixes):
            return low, high
    return _METRIC_DEFAULT_RANGE


class TokenEditor(QWidget):
    """The generated form. Load a theme, read the draft back, listen for changes."""

    #: The draft changed. Read :meth:`draft` for the new one.
    changed = Signal()
    #: The user asked for ``styled`` chrome on a theme that declares no surfaces.
    #: Policy for that belongs to the page, which can offer to seed them.
    styledSurfacesNeeded = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._draft: Theme | None = None
        self._locked = False
        self._loading = False
        self._color_rows: dict[str, ColorRow] = {}
        self._inputs: list[QWidget] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(int(theme.metric("space.lg")))

    # -- the draft --------------------------------------------------------------

    def draft(self) -> Theme:
        """The theme as edited. Only valid after :meth:`load`."""
        if self._draft is None:  # pragma: no cover - the window always loads first
            raise RuntimeError("TokenEditor.draft() before load()")
        return self._draft

    def load(self, preset: Theme) -> None:
        """Show *preset* and edit from there, discarding any previous draft."""
        self._draft = preset
        self._rebuild()

    def update_draft(self, **changes: Any) -> None:
        """Change something about the draft that has no row of its own — its name.

        Announces the change like any other, so the page's dirty flag and preview
        do not need a second path for the header fields.
        """
        self._apply(**changes)

    def set_locked(self, locked: bool) -> None:
        """Read-only view: a bundled preset is shown, not edited.

        Text and numeric fields shed their input chrome the way the rest of the
        app's locked widgets do; a swatch or a checkbox has no value to keep
        showing once it cannot be pressed, so those are simply disabled.
        """
        self._locked = locked
        for widget in self._inputs:
            if isinstance(widget, (QCheckBox, QPushButton)):
                widget.setEnabled(not locked)
            else:
                set_widget_locked(widget, locked)

    # -- building ---------------------------------------------------------------

    def _rebuild(self) -> None:
        self._loading = True
        try:
            _clear(self._layout)
            self._color_rows.clear()
            self._inputs.clear()
            self._layout.addWidget(self._build_chrome())
            for group in TOKEN_GROUPS:
                self._layout.addWidget(self._build_group(group))
            self._layout.addWidget(self._build_blocks())
            self._layout.addStretch()
        finally:
            self._loading = False
        self._revalidate_colors()
        self.set_locked(self._locked)

    def _build_chrome(self) -> QWidget:
        draft = self.draft()
        box = QGroupBox("Chrome")
        form = QFormLayout(box)

        combo = QComboBox()
        for mode in CHROME_MODES:
            combo.addItem(_CHROME_LABELS.get(mode, mode), mode)
        combo.setCurrentIndex(max(0, combo.findData(draft.chrome.mode)))
        combo.setMaximumWidth(int(theme.metric("column.settings.value")))
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: self._set_chrome_mode(c.currentData())
        )
        guard_wheel(combo)
        self._inputs.append(combo)
        form.addRow(
            _field_label("Mode", "Whether the theme dresses the widgets, or the OS does."),
            combo,
        )

        ring = QCheckBox("Draw one")
        ring.setChecked(draft.chrome.focus_ring)
        ring.toggled.connect(self._set_focus_ring)
        self._inputs.append(ring)
        form.addRow(
            _field_label(
                "Focus ring",
                "An accent outline on the focused control — the only visible sign "
                "that it has taken the scroll wheel from the page.",
            ),
            ring,
        )
        return box

    def _build_group(self, group: str) -> QWidget:
        meta = meta_module.token_meta()
        tokens = getattr(self.draft(), group)
        box = QGroupBox(meta.group_label(group))
        column = QVBoxLayout(box)
        names = [name for name in tokens if not name.startswith("_")]
        for bucket, members in meta_module.grouped(group, names):
            column.addWidget(self._build_bucket(group, bucket, members))
        if not names:  # pragma: no cover - every bundled preset states some tokens
            column.addWidget(_note("This theme states nothing in this group."))
        return box

    def _build_bucket(self, group: str, bucket: str, names: list[str]) -> QWidget:
        meta = meta_module.token_meta()
        box = QGroupBox(meta.bucket_label(bucket))
        form = QFormLayout(box)
        for name in names:
            value = getattr(self.draft(), group)[name]
            form.addRow(
                _field_label(meta.label(name), meta.hint(name)),
                self._build_editor(group, name, value),
            )
        return box

    def _build_editor(self, group: str, name: str, value: Any) -> QWidget:
        """One widget for one token, chosen by the shape of its value."""
        if group == "colors":
            return self._build_color(name, str(value))
        if group == "typography" and name == "family":
            return self._build_family(value)
        if isinstance(value, Sequence) and not isinstance(value, str) and len(value) == 4:
            return self._build_box(group, name, value)
        if isinstance(value, bool):  # before int: a bool is one
            return self._build_flag(group, name, value)
        if isinstance(value, int):
            low, high = _metric_range(name)
            spin = make_spin_box(
                low, high, value=value, max_width=int(theme.metric("column.settings.value"))
            )
            spin.valueChanged.connect(lambda v, g=group, n=name: self._set_token(g, n, v))
            self._inputs.append(spin)
            return spin
        if isinstance(value, float):
            spin = self._build_fraction(group, name, value)
            return spin
        return self._build_json(group, name, value)

    def _build_color(self, name: str, value: str) -> QWidget:
        row = ColorRow(value)
        row.valueChanged.connect(lambda v, n=name: self._set_color(n, v))
        self._color_rows[name] = row
        self._inputs.extend(row.editable_widgets())
        return row

    def _build_fraction(self, group: str, name: str, value: float) -> QWidget:
        width = int(theme.metric("column.settings.value"))
        if "opacity" in name:
            spin = make_double_spin_box(
                0.0, 1.0, value=value, decimals=2, step=0.05, max_width=width
            )
        elif "scale" in name:
            spin = make_double_spin_box(
                0.1, 3.0, value=value, decimals=2, step=0.05, max_width=width
            )
        elif group == "typography":
            spin = make_double_spin_box(
                4.0, 96.0, value=value, decimals=1, step=0.5, max_width=width
            )
        else:
            low, high = _metric_range(name)
            spin = make_double_spin_box(
                low, high, value=value, decimals=1, step=0.5, max_width=width
            )
        spin.valueChanged.connect(lambda v, g=group, n=name: self._set_token(g, n, v))
        self._inputs.append(spin)
        return spin

    def _build_box(self, group: str, name: str, value: Sequence[Any]) -> QWidget:
        """Four spin boxes for a ``(left, top, right, bottom)`` margin token."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))
        low, high = _metric_range(name)
        spins = []
        for index, edge in enumerate(_BOX_EDGES):
            spin = make_spin_box(low, high, value=int(value[index]), max_width=_compact_width())
            spin.setToolTip(edge.title())
            spin.valueChanged.connect(
                lambda _v, g=group, n=name: self._set_token(g, n, [s.value() for s in spins])
            )
            spins.append(spin)
            self._inputs.append(spin)
            row.addWidget(spin)
        row.addStretch()
        return holder

    def _build_flag(self, group: str, name: str, value: bool) -> QWidget:
        check = QCheckBox()
        check.setChecked(value)
        check.toggled.connect(lambda v, g=group, n=name: self._set_token(g, n, v))
        self._inputs.append(check)
        return check

    def _build_family(self, value: Any) -> QWidget:
        """The font family, whose ``null`` means "leave the platform default alone"."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        combo = QFontComboBox()
        guard_wheel(combo)
        if value:
            combo.setCurrentFont(QFont(str(value)))
        default = QCheckBox("Platform default")
        default.setChecked(not value)
        combo.setEnabled(bool(value))

        def commit() -> None:
            family = None if default.isChecked() else combo.currentFont().family()
            combo.setEnabled(not default.isChecked())
            self._set_token("typography", "family", family)

        default.toggled.connect(lambda _v: commit())
        combo.currentFontChanged.connect(lambda _f: commit())
        self._inputs.extend([combo, default])
        row.addWidget(combo, stretch=1)
        row.addWidget(default)
        return holder

    def _build_json(self, group: str, name: str, value: Any) -> QWidget:
        """The catch-all: a shape nothing above recognised, edited as JSON."""
        field = QLineEdit(json.dumps(value))
        field.setToolTip("This token's value has an unfamiliar shape; edit it as JSON.")

        def commit() -> None:
            try:
                parsed = json.loads(field.text())
            except json.JSONDecodeError:
                field.setText(json.dumps(getattr(self.draft(), group)[name]))
                return
            self._set_token(group, name, parsed)

        field.editingFinished.connect(commit)
        self._inputs.append(field)
        return field

    def _build_blocks(self) -> QWidget:
        """Per-block size overrides — a theme parameter, but a sharp one.

        Every bundled preset leaves this empty, so iterating it would render an
        empty box and offer no way in. Instead every block the app knows about gets
        a row, unchecked, showing the bounds it currently has. Checking one writes
        all four: an omitted bound and a zero mean different things to
        ``block_sizes`` (inherit vs. unbounded) and a snapshot should not have to
        rely on the reader knowing which.
        """
        draft = self.draft()
        box = QGroupBox("Block sizes")
        column = QVBoxLayout(box)
        column.addWidget(
            _note(
                "Advanced. These override how much room each sheet block takes. "
                "Setting a maximum equal to a minimum pins that block's size."
            )
        )
        effective = block_sizes.load_block_sizes()
        form = QFormLayout()
        for key in sorted(set(effective) | set(draft.blocks)):
            form.addRow(_field_label(key.replace("_", " ").title(), ""), self._build_block_row(key))
        column.addLayout(form)
        return box

    def _build_block_row(self, key: str) -> QWidget:
        current = self.draft().blocks.get(key) or {}
        size = block_sizes.load_block_sizes().get(key, block_sizes.BlockSize())
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        toggle = QCheckBox("Override")
        toggle.setChecked(bool(current))
        spins = {}
        for bound in _BLOCK_BOUNDS:
            spin = make_spin_box(
                0,
                block_sizes.UNBOUNDED,
                value=int(getattr(size, bound)),
                max_width=_compact_width(),
            )
            spin.setToolTip(bound.replace("_", " ").title())
            spin.setEnabled(toggle.isChecked())
            spins[bound] = spin

        def commit() -> None:
            for spin in spins.values():
                spin.setEnabled(toggle.isChecked())
            blocks = {k: dict(v) for k, v in self.draft().blocks.items()}
            if toggle.isChecked():
                blocks[key] = {bound: spins[bound].value() for bound in _BLOCK_BOUNDS}
            else:
                blocks.pop(key, None)
            self._apply(blocks=blocks)

        toggle.toggled.connect(lambda _v: commit())
        for spin in spins.values():
            spin.valueChanged.connect(lambda _v: commit())
        self._inputs.append(toggle)
        self._inputs.extend(spins.values())

        row.addWidget(toggle)
        for spin in spins.values():
            row.addWidget(spin)
        row.addStretch()
        return holder

    # -- edits ------------------------------------------------------------------

    def _set_chrome_mode(self, mode: str) -> None:
        if self._loading or mode == self.draft().chrome.mode:
            return
        if mode == CHROME_STYLED and not self._declares_surfaces():
            # A styled preset states its own surfaces and the stylesheet requires
            # them; flipping Classic would raise inside apply(). The page decides
            # what to offer, then reloads us.
            self.styledSurfacesNeeded.emit()
            return
        self._apply(chrome=replace(self.draft().chrome, mode=mode))

    def _set_focus_ring(self, enabled: bool) -> None:
        if self._loading:
            return
        self._apply(chrome=replace(self.draft().chrome, focus_ring=enabled))

    def _set_token(self, group: str, name: str, value: Any) -> None:
        if self._loading:
            return
        tokens = {**getattr(self.draft(), group), name: value}
        self._apply(**{group: tokens})

    def _set_color(self, name: str, value: str) -> None:
        if self._loading:
            return
        row = self._color_rows[name]
        if meta_module.token_meta().is_washed(name) and not is_literal_color(value):
            # A wash decomposes the colour into channels; a palette() expression
            # has none until Qt paints it. Refuse rather than let it reach a card.
            row.set_warning(
                "A translucent fill is derived from this colour, so it must be a #rrggbb.",
                "tint.worse",
            )
            row.set_value(str(self.draft().colors[name]))
            return
        self._set_token("colors", name, value)
        self._revalidate_colors()

    def _apply(self, **changes: Any) -> None:
        self._draft = replace(self.draft(), **changes)
        self.changed.emit()

    def _declares_surfaces(self) -> bool:
        colors = self.draft().colors
        return all(is_literal_color(str(colors.get(name, ""))) for name in STYLED_REQUIRED)

    # -- validation -------------------------------------------------------------

    def _revalidate_colors(self) -> None:
        """Re-warn every contrast-checked tint against the *draft's* own backdrops.

        Every one, not just the edited token: changing a surface moves the ground
        under all of them at once.
        """
        meta = meta_module.token_meta()
        backdrops = measurement_backdrops(self.draft())
        for name, row in self._color_rows.items():
            if not meta.styles_text(name):
                continue
            value = str(self.draft().colors[name])
            row.set_warning(_contrast_warning(value, backdrops))


def _contrast_warning(value: str, backdrops: Sequence[str]) -> str:
    """The worst backdrop this colour reads against, if it fails the floor there.

    Empty when it clears everywhere, or when either side is a ``palette()``
    expression — those have no channels here to measure.
    """
    if not is_literal_color(value):
        return ""
    ratios = [(contrast_ratio(value, back), back) for back in backdrops if is_literal_color(back)]
    if not ratios:
        return ""
    worst, against = min(ratios)
    if worst >= MIN_CONTRAST:
        return ""
    return f"⚠ {worst:.2f}:1 on {against} — below the {MIN_CONTRAST:.1f}:1 floor for text."


def _field_label(text: str, hint: str) -> QWidget:
    """A form label, with its help text wrapped underneath when there is any.

    The column is pinned to a fixed width and the wrapped height then measured
    outright. Left to negotiate, a word-wrapped label inside a composite widget
    gets sized for one line by the enclosing form and the rest of the sentence is
    clipped by the row below — ``heightForWidth`` only reaches it when every
    layout in between agrees to ask, and the form does not reliably ask.
    """
    width = int(theme.metric("column.settings.label"))
    holder = QWidget()
    holder.setFixedWidth(width)
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    name = QLabel(text)
    name.setStyleSheet(BOLD_STYLE)
    name.setWordWrap(True)
    name.setMinimumHeight(name.heightForWidth(width))
    column.addWidget(name)
    if hint:
        note = _note(hint)
        note.setMinimumHeight(note.heightForWidth(width))
        column.addWidget(note)
    return holder


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(muted_style(italic=True))
    return label


def _clear(layout: QVBoxLayout) -> None:
    """Empty a layout, deleting whatever was in it."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


__all__ = [
    "STYLED_OPTIONAL_METRICS",
    "STYLED_REQUIRED",
    "TOKEN_GROUPS",
    "TokenEditor",
    "seed_styled_surfaces",
]
