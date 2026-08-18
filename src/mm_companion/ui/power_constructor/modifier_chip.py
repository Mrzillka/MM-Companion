from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import Modifier
from mm_companion.core.powers import (
    ModifierSelection,
)
from mm_companion.core.rules import (
    effective_ability,
    modifier_is_banded,
    modifier_is_per_rank,
    modifier_rank_cap,
    selection_band,
)
from mm_companion.ui import theme
from mm_companion.ui.drop_feedback import DropFeedback, DropIndicator
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.power_constructor.common import (
    CHIP_MIME,
    STRENGTH_AMOUNT_MAX,
    TRAIT_SOURCES,
    CellContext,
    _move_item,
    brick_tooltip,
    fill_trait_combo,
    is_trait_allocation,
    link_trait_row,
    repeatable_cell_kind,
)
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box


class ModifierChip(QFrame):
    """An attached extra/flaw shown on an effect card, with a remove button.

    A ``ranked`` modifier (bought in its own ranks, e.g. Accurate) also carries a
    rank spin box; changing it writes back to the :class:`ModifierSelection` and
    emits :attr:`changed` so the card can recompute its cost.

    A modifier that folds an ability into the effect (``adds_ability``, e.g.
    Strength-Based) carries an "amount used" spin box: the fixed number of ability
    ranks the power *pays* for, folded in every rank. Its ceiling is
    :data:`STRENGTH_AMOUNT_MAX`, independent of the wielder's current ability, so the
    cost stays stable when that ability changes; buying more than the wielder actually
    has is flagged as a warning, not repriced.

    A **per-rank** modifier also carries an "Only some ranks" checkbox revealing a
    from/to pair — the rules let one apply to part of an effect (a Blast 12 whose top
    four ranks alone are Tiring). It is offered only there: a flat modifier is charged
    once whatever it covers, so a band on one would be a control that changes no number.
    The spin ceilings track the host effect's rank through :meth:`sync_effect_rank`.
    """

    removeRequested = Signal(object)
    changed = Signal()

    def __init__(
        self,
        modifier: Modifier,
        selection: ModifierSelection,
        game_data=None,
        character: Character | None = None,
        effect_rank: int = 1,
    ) -> None:
        super().__init__()
        self.selection = selection
        self._modifier = modifier
        self._data = game_data
        self._character = character
        self._effect_rank = max(1, effect_rank)
        self._press_pos = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # hints the chip is draggable
        tint = "badge.extra" if modifier.category == "extra" else "badge.flaw"
        self.setStyleSheet(
            f"ModifierChip {{ background: {theme.color(tint)};"
            f" border-radius: {int(theme.metric('radius.chip'))}px; }}"
        )
        # The same hover text the palette brick carries — a chip is a cramped label, so
        # what the modifier does still has to be one hover away once it is attached.
        # Set on the frame only; Qt walks up to it for children with no tooltip.
        self.setToolTip(brick_tooltip(modifier.name, modifier.cost_formula, modifier.description))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 2, 3, 2)
        outer.setSpacing(2)
        header = QHBoxLayout()
        header.setSpacing(4)
        # A blank Custom modifier titles itself with the player's typed name (kept in
        # sync from its name config field); a normal modifier uses its record name.
        self._title = QLabel(self._title_text())
        header.addWidget(self._title)
        if modifier.ranked:
            # The ceiling is the ruleset's when it states one (Striding's 5), never the
            # widget's own number — see `modifier_rank_cap`.
            cap = modifier_rank_cap(modifier)
            rank = make_spin_box(1, cap, value=selection.rank, buttons=False, max_width=44)
            rank.setPrefix("×")
            rank.valueChanged.connect(self._on_rank_changed)
            header.addWidget(rank)
        remove = QPushButton("✕")
        remove.setFlat(True)
        remove.setFixedWidth(18)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        header.addWidget(remove)
        outer.addLayout(header)

        # A few modifiers carry their own choices (Removable tier, Side Effect
        # backfire, a Triggered/Limited condition — see docs/mm-powers-ui-design.md §4).
        # A choice with a cost (the tier, the always/on-failure toggle) feeds the cost
        # engine straight from the selection's config.
        if modifier.config_fields:
            outer.addLayout(self._build_config(modifier))

        # A "how much of the ability to pay for" spin box for an ability-folding
        # modifier (Strength-Based). Fixed ceiling, independent of the wielder's ability.
        if modifier.adds_ability:
            outer.addLayout(self._build_amount(modifier.adds_ability))

        # The rank band, for a per-rank modifier only — see the class docstring.
        self._band_check = None
        self._band_from = None
        self._band_to = None
        if modifier_is_per_rank(modifier, selection):
            outer.addLayout(self._build_band())

    # -- the rank band -----------------------------------------------------
    def _build_band(self) -> QHBoxLayout:
        """An "Only some ranks" checkbox revealing the from/to pair it gates.

        The same shape the effect card's "Use attack skill" row strikes: the control is
        built once and shown or hidden, never rebuilt, so nothing the player is halfway
        through editing is destroyed under them.
        """

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        low, high = selection_band(self.selection, self._effect_rank)
        banded = modifier_is_banded(self._modifier, self.selection, self._effect_rank)

        self._band_check = QCheckBox("Only some ranks")
        self._band_check.setToolTip(
            "Apply this modifier to part of the effect rather than all of it — a Blast "
            "12 whose top four ranks alone are Tiring pays for eight plain ranks and "
            "four discounted ones."
        )
        self._band_check.setChecked(banded)
        self._band_check.toggled.connect(self._on_band_toggled)
        row.addWidget(self._band_check)

        self._band_from = make_spin_box(
            1, self._effect_rank, value=low, buttons=False, max_width=44
        )
        self._band_to = make_spin_box(1, self._effect_rank, value=high, buttons=False, max_width=44)
        for spin in (self._band_from, self._band_to):
            spin.valueChanged.connect(self._on_band_changed)
            spin.setVisible(banded)
            row.addWidget(spin)
        self._band_dash = QLabel("–")
        self._band_dash.setVisible(banded)
        row.insertWidget(row.indexOf(self._band_to), self._band_dash)
        row.addStretch()
        return row

    def sync_effect_rank(self, rank: int) -> None:
        """Restate the band against a host effect whose rank has just changed.

        The band's ceiling is the effect's rank, so lowering the rank has to bring the
        spins down with it — the same restatement the effect card already makes to its
        allocation widgets. The stored band is left alone when the checkbox is off:
        :func:`~mm_companion.core.rules.selection_band` clamps on read, so a band the
        player has not asked for cannot leak into the cost.
        """

        self._effect_rank = max(1, rank)
        if self._band_from is None or self._band_to is None:
            return
        for spin in (self._band_from, self._band_to):
            spin.blockSignals(True)
            spin.setMaximum(self._effect_rank)
            spin.blockSignals(False)
        if self._band_check is not None and self._band_check.isChecked():
            self._commit_band()

    def _on_band_toggled(self, on: bool) -> None:
        for widget in (self._band_from, self._band_dash, self._band_to):
            if widget is not None:
                widget.setVisible(on)
        if on:
            self._commit_band()
        else:
            self.selection.applies_from = 0
            self.selection.applies_to = 0
            self.changed.emit()

    def _on_band_changed(self, _value: int) -> None:
        self._commit_band()

    def _commit_band(self) -> None:
        """Write the band down, keeping ``from`` at or below ``to``."""

        low = self._band_from.value()
        high = max(low, self._band_to.value())
        if high != self._band_to.value():
            self._band_to.blockSignals(True)
            self._band_to.setValue(high)
            self._band_to.blockSignals(False)
        self.selection.applies_from = low
        self.selection.applies_to = high
        self.changed.emit()

    def _current_ability(self, ability_key: str) -> int:
        """The wielder's current effective ability rank (0 without a character)."""
        if self._character is None:
            return 0
        return max(0, effective_ability(self._character, self._data, ability_key))

    def _build_amount(self, ability_key: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        abbr = next(
            (a.abbr for a in getattr(self._data, "abilities", []) if a.key == ability_key),
            ability_key,
        )
        row.addWidget(QLabel(f"{abbr} paid for"))
        # A stored amount is the fixed cost basis; without one (a legacy selection that
        # tracked the ability) seed the spin box at the wielder's current ability.
        current = self.selection.config.get("amount")
        value = self._current_ability(ability_key) if current is None else max(0, int(current))
        value = min(value, STRENGTH_AMOUNT_MAX)
        spin = make_spin_box(0, STRENGTH_AMOUNT_MAX, value=value, buttons=False, max_width=48)
        spin.setToolTip(
            f"Ranks of {abbr} this effect pays for (max {STRENGTH_AMOUNT_MAX}). "
            f"The effect folds in your current {abbr}, capped at this amount."
        )
        spin.valueChanged.connect(self._on_amount_changed)
        row.addWidget(spin)
        row.addStretch()
        return row

    def _on_amount_changed(self, value: int) -> None:
        # Always pinned — the amount is the fixed cost basis, so the cost doesn't drift
        # when the wielder's ability changes.
        self.selection.config["amount"] = value
        self.changed.emit()

    def _repeatable_rows(self, cfg) -> QWidget:
        """A variable-length row list for a modifier's ``repeatable`` config field.

        Reduced Trait's rows: which traits are lowered to pay for the raised ones, and
        by how many ranks. Cells come from the same
        :func:`~mm_companion.ui.power_constructor.common.repeatable_cell_kind` registry
        the effect card's rows use, so a trait row reads and stores identically whether
        it hangs off the effect or off one of its flaws — which is what lets
        :func:`~mm_companion.core.rules.config_trait_allocation` read both with one
        function. Stacked vertically rather than inline: a chip is a narrow thing and a
        row is two controls wide.
        """

        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        rows_host = QWidget()
        rows_layout = QVBoxLayout(rows_host)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        outer.addWidget(rows_host)

        existing = self.selection.config.get(cfg.key)
        if not isinstance(existing, list):
            existing = []
        row_widgets: list[tuple[QWidget, dict]] = []

        def commit() -> None:
            rows = []
            for _widget, cells in row_widgets:
                stored = {c.key: repeatable_cell_kind(c).read(cells[c.key]) for c in cfg.columns}
                if any(str(v).strip() for v in stored.values()):
                    rows.append(stored)
            self.selection.config[cfg.key] = rows
            self.changed.emit()

        def add_row(initial: dict | None = None) -> None:
            initial = initial or {}
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(3)
            cells: dict = {}
            for column in cfg.columns:
                kind = repeatable_cell_kind(column)
                cell = kind.build(CellContext(self._data, self._character), column, initial, commit)
                row_layout.addWidget(cell, kind.stretch)
                cells[column.key] = cell
            remove = QPushButton("✕")
            remove.setFlat(True)
            remove.setFixedWidth(20)
            row_layout.addWidget(remove)
            rows_layout.addWidget(row_widget)
            entry = (row_widget, cells)
            row_widgets.append(entry)
            link_trait_row(CellContext(self._data, self._character), cfg.columns, cells)

            def do_remove(_checked: bool = False) -> None:
                if entry in row_widgets:
                    row_widgets.remove(entry)
                row_widget.setParent(None)
                row_widget.deleteLater()
                commit()

            remove.clicked.connect(do_remove)

        for row_data in existing:
            if isinstance(row_data, dict):
                add_row(row_data)
        if not row_widgets and is_trait_allocation(cfg):
            add_row()  # the picker is the question; an empty one reads as a missing control

        add_button = QPushButton("＋ Add")
        add_button.clicked.connect(lambda: add_row())
        outer.addWidget(add_button)
        return host

    def _build_config(self, modifier: Modifier) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        points_spin = None  # the single points spin, if any, that gates other fields
        gated: list[tuple[QWidget, int]] = []  # (widget, show_when_points value)
        for cfg in modifier.config_fields:
            if cfg.type == "points":
                # A small spin box whose value *is* the modifier's flat cost (Subtle's
                # 1 or 2 points). Seed and persist the default so the cost is right
                # before the player touches it.
                stored = self.selection.config.get(cfg.key)
                value = cfg.default_value if stored is None else int(stored)
                self.selection.config[cfg.key] = value
                spin = make_spin_box(
                    cfg.min_value, cfg.max_value, value=value, buttons=False, max_width=44
                )
                spin.setSuffix(" pt")
                if cfg.hint:
                    spin.setToolTip(cfg.hint)
                spin.valueChanged.connect(lambda v, k=cfg.key: self._on_config(k, v))
                points_spin = spin
                row.addWidget(spin)
            elif cfg.type == "select":
                combo = QComboBox()
                if cfg.source in TRAIT_SOURCES and self._data is not None:
                    # Data-driven trait list (Reduced Trait's "which trait goes down";
                    # Check Required's "which check", which also offers the derived
                    # stats a player can roll but not buy).
                    fill_trait_combo(
                        combo, self._data, self.selection.config.get(cfg.key, ""), cfg.source
                    )
                else:
                    for option in cfg.options:
                        combo.addItem(option.label, option.value)
                    index = combo.findData(self.selection.config.get(cfg.key))
                    combo.setCurrentIndex(index if index >= 0 else 0)
                # Persist the shown default so downstream logic (cost, Limited Degree
                # field-hiding) reflects what the closed combo already displays.
                if not self.selection.config.get(cfg.key) and combo.currentData():
                    self.selection.config[cfg.key] = combo.currentData()
                guard_wheel(combo)
                if cfg.hint:
                    combo.setToolTip(cfg.hint)
                combo.currentIndexChanged.connect(
                    lambda _i, c=combo, k=cfg.key: self._on_config(k, c.currentData())
                )
                if cfg.show_when_points:
                    gated.append((combo, cfg.show_when_points))
                row.addWidget(combo)
            elif cfg.type == "repeatable":
                rows_widget = self._repeatable_rows(cfg)
                if cfg.hint:
                    rows_widget.setToolTip(cfg.hint)
                if cfg.show_when_points:
                    gated.append((rows_widget, cfg.show_when_points))
                row.addWidget(rows_widget)
            else:  # text
                edit = QLineEdit(self.selection.config.get(cfg.key, ""))
                edit.setPlaceholderText(cfg.label)
                if cfg.hint:
                    edit.setToolTip(cfg.hint)
                edit.textChanged.connect(lambda text, k=cfg.key: self._on_config(k, text))
                row.addWidget(edit)

        # A field gated on the points spin (Variable Conditions' "which degree") shows
        # only when the spin reads its trigger value; keep it in sync as the spin moves.
        if gated and points_spin is not None:

            def _sync_gated(value: int) -> None:
                for widget, when in gated:
                    widget.setVisible(value == when)

            _sync_gated(points_spin.value())
            points_spin.valueChanged.connect(_sync_gated)
        return row

    def _title_text(self) -> str:
        """The chip's header label — a Custom modifier's typed name, else its record name."""
        if self._modifier.custom:
            name = str(self.selection.config.get("name", "")).strip()
            if name:
                return name
        return self._modifier.name

    def _on_config(self, key: str, value) -> None:
        if value:
            self.selection.config[key] = value
        else:
            self.selection.config.pop(key, None)
        if self._modifier.custom:
            self._title.setText(self._title_text())
        self.changed.emit()

    def _on_rank_changed(self, value: int) -> None:
        self.selection.rank = value
        self.changed.emit()

    # -- drag to reorder within the group ---------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # The left button has to still be down, or a later right-/middle-drag across
        # the chip would start a left-button drag from a stale press point.
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(CHIP_MIME, b"1")  # the source chip is read from drag.source()
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())  # drag a ghost of the chip itself
        drag.exec(Qt.DropAction.MoveAction)
        self._press_pos = None

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Clear the press point, so a click that never became a drag leaves none behind."""
        self._press_pos = None
        super().mouseReleaseEvent(event)


class ModifierGroup(QWidget):
    """A titled, vertically-stacked run of modifier chips, hidden while empty.

    An :class:`EffectCard` keeps one of these for extras and one for flaws; each
    reveals itself only once its first chip is added and hides again when its last
    chip is removed. Chips can be **dragged within the group to reorder** them — the
    card mirrors the new order onto its backing selection list, which matters when two
    modifiers touch the same stat (later ones win). Reorder drops arrive as
    :attr:`reordered` ``(from_index, to_index)`` where ``to_index`` is an insertion
    point in the pre-move list.

    A drag shows **where the chip will land**: an accent insertion bar tracks the drop
    index down the gap the chip would fall into, so the order after the drop is legible
    before letting go rather than a guess. Dropping anywhere else — most usefully back
    on the palette — is a removal, handled by :class:`~.bricks.PaletteDropZone`.
    """

    reordered = Signal(int, int)

    #: Width of the insertion bar, in pixels.
    INDICATOR_WIDTH = 3

    def __init__(self, title: str) -> None:
        super().__init__()
        self._chips: list[QWidget] = []
        self.setAcceptDrops(True)
        # A plain QWidget ignores a stylesheet background entirely — it paints
        # itself before the style engine gets a look in, so the rule is applied and
        # simply never drawn. (The sibling drop targets are QFrames, which honour
        # it, which is why only this one was silently flat.) This attribute routes
        # the widget's background through the style, so its drop wash shows up.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # A wash with no outline: this group sits inside an effect card that draws
        # its own drop border, and two nested outlines would stack up.
        self._drops = DropFeedback(
            self, "ModifierGroup", radius="radius.chip", wash=0.12, border=False
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header = QLabel(title)
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)
        # A FlowContainer (not a bare QWidget) so a second, wrapped row of chips grows
        # the group instead of painting over the config form below it.
        self._chip_area = FlowContainer()
        self._chip_layout = FlowLayout(self._chip_area)
        layout.addWidget(self._chip_area)
        self._indicator = DropIndicator(self)
        self.setVisible(False)

    def add_chip(self, chip: QWidget) -> None:
        self._chips.append(chip)
        self._chip_layout.addWidget(chip)
        self.setVisible(True)

    def remove_chip(self, chip: QWidget) -> None:
        if chip in self._chips:
            self._chips.remove(chip)
        self._chip_layout.removeWidget(chip)
        chip.setParent(None)
        chip.deleteLater()
        self.setVisible(bool(self._chips))

    # -- reordering (drop handlers delegate to move_chip, the test seam) ---
    def move_chip(self, from_index: int, to_index: int) -> None:
        """Reorder the chip at ``from_index`` to insertion point ``to_index``.

        A drop that settles in place is a no-op (no relayout, no signal); otherwise
        the chip widgets are re-laid-out in the new order and :attr:`reordered` fires
        so the card can move the matching selection.
        """
        if not 0 <= from_index < len(self._chips):
            return
        if not _move_item(self._chips, from_index, to_index):
            return
        for chip in self._chips:  # re-add every chip in the new order
            self._chip_layout.removeWidget(chip)
        for chip in self._chips:
            self._chip_layout.addWidget(chip)
        self._chip_layout.invalidate()
        self.reordered.emit(from_index, to_index)

    def _drop_index(self, pos) -> int:
        """The insertion index for a drop at ``pos`` — before the nearest chip, or
        after it when the drop lands on its right half.

        ``pos`` is in this group's coordinates; chip geometries are in the chip area's,
        which sits below the group's header, so the point is mapped across first —
        otherwise the header's height skews which chip reads as nearest once the chips
        wrap onto a second row.
        """
        if not self._chips:
            return 0
        local = self._chip_area.mapFrom(self, pos)
        nearest = min(
            range(len(self._chips)),
            key=lambda i: (self._chips[i].geometry().center() - local).manhattanLength(),
        )
        center = self._chips[nearest].geometry().center()
        return nearest + (1 if local.x() > center.x() else 0)

    def _indicator_rect(self, index: int) -> QRect:
        """The insertion bar's geometry, in this group's coordinates, for a drop at
        ``index`` — in the gap before that chip, or past the right edge of the last one
        when the drop lands at the end. Empty while the group holds no chips."""
        if not self._chips:
            return QRect()
        gap = self._chip_layout.spacing() // 2
        if index < len(self._chips):
            geometry = self._chips[index].geometry()
            x = geometry.left() - gap
        else:
            geometry = self._chips[-1].geometry()
            x = geometry.right() + gap
        top_left = self._chip_area.mapTo(self, QPoint(x, geometry.top()))
        return QRect(top_left.x(), top_left.y(), self.INDICATOR_WIDTH, geometry.height())

    def _show_indicator(self, pos) -> None:
        """Track the insertion bar to the drop index for a drag at ``pos``."""
        rect = self._indicator_rect(self._drop_index(pos))
        if rect.isEmpty():
            self._indicator.hide_indicator()
        else:
            self._indicator.move_to(rect)

    def _end_drag(self) -> None:
        self._indicator.hide_indicator()
        self._drops.clear()

    def _accepts(self, event) -> bool:
        return event.mimeData().hasFormat(CHIP_MIME) and event.source() in self._chips

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Only accept a chip dragged from this same group — a reorder, never a move
        # between the Extras and Flaws groups (that would change its category). A chip
        # dragged anywhere else falls through to the palette, which reads it as a removal.
        if self._accepts(event):
            self._drops.show_accept()
            self._show_indicator(event.position().toPoint())
            event.acceptProposedAction()
        else:
            # Most often a flaw dragged onto Extras (or the reverse): a move that
            # would change the modifier's category, which is never allowed. Saying
            # so beats looking identical to a drag the group simply missed.
            self._drops.show_reject()
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._accepts(event):
            self._show_indicator(event.position().toPoint())
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._end_drag()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._end_drag()
        source = event.source()
        if source not in self._chips:
            event.ignore()
            return
        self.move_chip(self._chips.index(source), self._drop_index(event.position().toPoint()))
        event.acceptProposedAction()
