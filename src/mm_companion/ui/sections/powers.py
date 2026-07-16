"""Section 4: powers.

The most complex part of a character. An "Add Power" button opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` brick-builder in
its own window; saving there hands the finished
:class:`~mm_companion.core.powers.Power` back through
:attr:`~mm_companion.ui.power_constructor.PowerConstructorWindow.powerSaved`, which
this section appends to the shared :class:`~mm_companion.core.character.Character`
and shows as a *card*. Each card reads top-to-bottom like a stat-block entry: a
header (name, assembled point cost, a ⚠ marker when the power breaks a Power Level
cap, and — for a runtime-gated power — an on/off switch), the free-text description,
a per-effect summary listing each effect's extras and flaws, and a bottom line
dedicated to roll information (attack bonus, save DC). Hovering the card reveals the
full auto-generated game-term breakdown as a tooltip, the same data the Power
Constructor shows while building. Each card carries an edit button that reopens the
constructor pre-loaded with that power — editing a deep copy that replaces the
original in place on save — and a remove button.

Powers can be **grouped**. A character's ``powers`` is a tree of
:data:`~mm_companion.core.powers.PowerNode` — leaf powers and
:class:`~mm_companion.core.powers.PowerGroup` containers, which can nest arbitrarily.
Dragging a card into a *gap* reorders (or moves it into/out of a group); dropping it
*onto* another card, or onto a group's title bar, combines the two into a group with
a distinct highlight. A group's title bar carries an Independent / Array / Linked
mode toggle (the same three choices the Constructor offers for a single power's own
effects) that decides how its members' costs combine.

It follows the standard section contract (``data`` + ``character`` constructor,
``changed`` signal, ``set_locked``) so it slots into the sheet like the others, and —
because saved powers live on the model — a loaded character repopulates its list at
construction.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    ModifierSelection,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
    power_is_homerule,
)
from mm_companion.core.rules import (
    HOMERULE_TINT,
    active_array_child,
    array_alternate_cost,
    array_base_index,
    debilitated_traits,
    effect_attack_skill_bonus,
    effect_effective_rank,
    effect_stat_rows,
    modifier_label,
    node_display_cost,
    power_display_name,
    power_has_standing_effect,
    power_pl_violations,
    power_runtime_gates,
    powers_points_spent,
)
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.widgets import hline_separator, title_with_cost

# Tints for a stat a modifier changed, matching the Power Constructor's
# PowerTermsView: an extra improved it (green), a flaw limited it (red).
_TINT_BETTER = "#2e9e4f"
_TINT_WORSE = "#d15b5b"
# A homerule (Dev-mode) override reads in a distinct blue, apart from better/worse.
_TINT_HOMERULE = "#4a90d9"
_TINTS = {"better": _TINT_BETTER, "worse": _TINT_WORSE, HOMERULE_TINT: _TINT_HOMERULE}

# A calm blue reused for drag affordances and dice info.
_ACCENT = "#6a86c0"

# Drag-and-drop payload: the dragged node's stable id (a Power.id or PowerGroup.id).
# A tree position needs parent context, not a bare index, so drops resolve the id.
_POWER_MIME = "application/x-mm-power-node"

# What each group mode is called on its title bar.
_MODE_LABELS = {
    STRUCTURE_INDEPENDENT: "Group of powers",
    STRUCTURE_ARRAY: "Group of alternate effects",
    STRUCTURE_LINKED: "Group of linked powers",
}


class _DragHandle(QLabel):
    """The ``⠿`` grip at the head of a card; a press-drag on it starts the drag.

    It only *detects* the gesture (a left-press moved past the platform drag
    threshold) and emits :attr:`dragStarted`; the owning card builds and runs the
    actual :class:`QDrag`, so the grip stays a dumb, reusable handle.
    """

    dragStarted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("⠿", parent)
        self._press: QPoint | None = None
        self.setToolTip("Drag to reorder, or drop onto another power to group them")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet("color: gray;")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        moved = (event.position().toPoint() - self._press).manhattanLength()
        if moved >= QApplication.startDragDistance():
            self._press = None
            self.dragStarted.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: ARG002
        self._press = None


class _ModeToggle(QWidget):
    """A segmented Independent / Array / Linked switch for a group's title bar.

    Mirrors the Power Constructor's mode bar (the same three choices for how parts
    combine), but scoped to whole cards in a group rather than one power's effects.
    Emits :attr:`modeChanged` with a structure id when the user picks a segment.
    """

    modeChanged = Signal(str)

    _MODES = (
        (STRUCTURE_INDEPENDENT, "Independent", "Members act on their own; their costs add up."),
        (
            STRUCTURE_ARRAY,
            "Array",
            "One member active at a time; the costliest is paid in "
            "full and each other is a flat-cost alternate.",
        ),
        (STRUCTURE_LINKED, "Linked", "Members always activate together as one; costs add up."),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for mode, label, tip in self._MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setFixedHeight(22)
            self._group.addButton(button)
            self._buttons[mode] = button
            row.addWidget(button)
        self._group.buttonClicked.connect(self._on_clicked)

    def _on_clicked(self, button: QPushButton) -> None:
        for mode, candidate in self._buttons.items():
            if candidate is button:
                self.modeChanged.emit(mode)
                return

    def set_mode(self, mode: str) -> None:
        """Reflect a mode into the buttons without emitting :attr:`modeChanged`."""
        (self._buttons.get(mode) or self._buttons[STRUCTURE_INDEPENDENT]).setChecked(True)

    def set_toggle_enabled(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(enabled)


class _DraggableCard(QFrame):
    """A stat-block card (leaf power or group) that can be picked up by its grip.

    It carries the id of the tree node it renders; when its grip fires, it launches a
    :class:`QDrag` carrying that id (with a snapshot of the card as the drag cursor)
    so the enclosing :class:`_NodeList` — or a group title bar — can drop it.
    """

    def __init__(self, node_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.node_id = node_id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # Never shrink below the height its content needs — a card always shows all of
        # its rows (the block, not the card, grows and the page scrolls).
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_POWER_MIME, self.node_id.encode("ascii"))
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, 12))
        drag.exec(Qt.DropAction.MoveAction)


class _GroupHeader(QWidget):
    """A group's title bar, which also acts as a drop target that *wraps* the group.

    Dropping a card onto a group's bar groups the whole group with the dragged node
    into a new parent group (the way to nest a group beside a peer). Joining a group
    as another member is done by dropping into its body instead (handled by the
    group's inner :class:`_NodeList`).
    """

    powerDropped = Signal(str)  # the dropped node's id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(_POWER_MIME):
            event.acceptProposedAction()
            self.setStyleSheet("background: rgba(106, 134, 192, 0.25); border-radius: 4px;")

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(_POWER_MIME):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: object) -> None:  # noqa: ARG002
        self.setStyleSheet("")

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet("")
        if not event.mimeData().hasFormat(_POWER_MIME):
            return
        source = bytes(event.mimeData().data(_POWER_MIME)).decode("ascii")
        event.acceptProposedAction()
        self.powerDropped.emit(source)


class _NodeList(QWidget):
    """A vertical stack of cards for one level of the tree; a drop target for both.

    Renders the ordered nodes of one list — the character's top-level ``powers``
    (``parent_id`` empty) or a group's children (``parent_id`` = the group id). A drag
    over a card's *body* offers to **combine** (the target card is highlighted); a drag
    near a gap offers to **reorder/move** (a thin insertion line). Dropping emits the
    matching request for the section to apply against the model.
    """

    combineRequested = Signal(str, str)  # source id, target (drop-on) id
    moveRequested = Signal(str, str, int)  # source id, parent id (""=top), gap index

    def __init__(self, parent_id: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parent_id = parent_id
        self.setAcceptDrops(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._entries: list[tuple[str, QWidget]] = []
        self._indicator = QFrame(self)
        self._indicator.setFrameShape(QFrame.Shape.HLine)
        self._indicator.setFixedHeight(2)
        self._indicator.setStyleSheet(f"background: {_ACCENT}; border: none;")
        self._indicator.hide()
        # A translucent outline laid over a card to mark it as the combine target.
        self._highlight = QFrame(self)
        self._highlight.setStyleSheet(
            f"border: 2px solid {_ACCENT}; border-radius: 6px; "
            "background: rgba(106, 134, 192, 0.15);"
        )
        self._highlight.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._highlight.hide()

    def clear(self) -> None:
        """Remove every card (keeping the reusable indicator/highlight overlays)."""
        for index in reversed(range(self._layout.count())):
            widget = self._layout.itemAt(index).widget()
            if widget is not None and widget is not self._indicator:
                self._layout.takeAt(index)
                widget.setParent(None)
                widget.deleteLater()
        self._entries = []
        self._clear_hints()

    def add_entry(self, node_id: str, widget: QWidget) -> None:
        self._entries.append((node_id, widget))
        self._layout.addWidget(widget)

    # -- drop handling ----------------------------------------------------
    def _target(self, y: int) -> tuple[str, int, str, QWidget | None]:
        """Resolve a drop at vertical position *y* to a combine or reorder target.

        Returns ``("combine", pos, node_id, widget)`` for the body of an entry, or
        ``("reorder", gap_index, "", None)`` near a boundary / below all entries.
        """
        for pos, (node_id, widget) in enumerate(self._entries):
            top = widget.y()
            height = widget.height()
            if y < top + height * 0.25:
                return ("reorder", pos, "", None)
            if y < top + height * 0.75:
                return ("combine", pos, node_id, widget)
        return ("reorder", len(self._entries), "", None)

    def _show_reorder(self, index: int) -> None:
        self._highlight.hide()
        self._layout.removeWidget(self._indicator)
        self._layout.insertWidget(index, self._indicator)
        self._indicator.show()

    def _show_combine(self, widget: QWidget) -> None:
        self._indicator.hide()
        self._layout.removeWidget(self._indicator)
        self._highlight.setGeometry(widget.geometry())
        self._highlight.show()
        self._highlight.raise_()

    def _clear_hints(self) -> None:
        self._indicator.hide()
        self._layout.removeWidget(self._indicator)
        self._highlight.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(_POWER_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not event.mimeData().hasFormat(_POWER_MIME):
            return
        event.acceptProposedAction()
        kind, index, _node_id, widget = self._target(event.position().toPoint().y())
        if kind == "combine" and widget is not None:
            self._show_combine(widget)
        else:
            self._show_reorder(index)

    def dragLeaveEvent(self, event: object) -> None:  # noqa: ARG002
        self._clear_hints()

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasFormat(_POWER_MIME):
            return
        source = bytes(event.mimeData().data(_POWER_MIME)).decode("ascii")
        kind, index, node_id, _widget = self._target(event.position().toPoint().y())
        self._clear_hints()
        event.acceptProposedAction()
        if kind == "combine":
            self.combineRequested.emit(source, node_id)
        else:
            self.moveRequested.emit(source, self.parent_id, index)


class PowersSection(TitledSection):
    """Powers section: launches the Power Constructor and lists saved powers as a tree."""

    # A build change (add/remove/edit a power, group, re-cost) — marks the sheet dirty.
    changed = Signal()
    # A runtime on/off toggle. It updates the live sheet numbers (a trait boost drops
    # in or out) but is *not* part of the point build and is not persisted, so it must
    # not mark the character dirty — the sheet wires this to the same refreshes as
    # ``changed`` minus the unsaved-changes flag.
    runtimeChanged = Signal()

    def __init__(
        self,
        data: GameData,
        character: Character,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._locked = False
        # Keep constructor windows referenced so Qt doesn't garbage-collect them the
        # moment the click handler returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)
        self._empty = QLabel("No powers yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        # The saved powers stack above the Add button, one card each; the top-level
        # list is the root of the drag-and-drop tree.
        self._list_host = _NodeList("")
        self._list_host.combineRequested.connect(self._on_combine)
        self._list_host.moveRequested.connect(self._on_move)
        layout.addWidget(self._list_host)

        self._add_button = QPushButton("Add Power")
        self._add_button.clicked.connect(self._open_constructor)
        layout.addWidget(self._add_button)

        # Seed from the (possibly loaded) model.
        self._rebuild_list()

    # -- constructor lifecycle --------------------------------------------
    def _open_constructor(self) -> None:
        window = PowerConstructorWindow(self._data, character=self._character)
        window.powerSaved.connect(self._on_power_saved)
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_saved(self, power: Power) -> None:
        self._character.powers.append(power)
        self._rebuild_list()
        self.changed.emit()

    def _edit_power(self, power: Power) -> None:
        """Reopen the constructor pre-loaded with an existing power for editing.

        The constructor edits a deep copy and hands it back on save; the copy then
        replaces the original in place (identity match), so an unsaved close is a
        no-op and a save swaps in exactly the power that was opened.
        """
        window = PowerConstructorWindow(self._data, character=self._character, power=power)
        window.powerSaved.connect(
            lambda edited, original=power: self._on_power_edited(original, edited)
        )
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_edited(self, original: Power, edited: Power) -> None:
        located = self._locate(original.id)
        if located is not None:
            _node, siblings, index, _parent = located
            siblings[index] = edited
        else:  # the original was removed while the editor was open — treat as an add
            self._character.powers.append(edited)
        self._rebuild_list()
        self.changed.emit()

    def _on_window_closed(self, window: PowerConstructorWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    # -- tree lookup / mutation seams (headless-testable) -----------------
    def _locate(
        self,
        node_id: str,
        nodes: list[PowerNode] | None = None,
        parent: PowerGroup | None = None,
    ) -> tuple[PowerNode, list[PowerNode], int, PowerGroup | None] | None:
        """Find a node by id, returning ``(node, its list, index, parent group)``.

        The list is the actual mutable container (top-level ``powers`` or a group's
        ``children``), so callers can splice in place. ``None`` when the id is absent.
        """
        nodes = self._character.powers if nodes is None else nodes
        for index, node in enumerate(nodes):
            if node.id == node_id:
                return node, nodes, index, parent
            if isinstance(node, PowerGroup):
                found = self._locate(node_id, node.children, node)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _subtree_ids(node: PowerNode) -> set[str]:
        ids = {node.id}
        if isinstance(node, PowerGroup):
            for child in node.children:
                ids |= PowersSection._subtree_ids(child)
        return ids

    def _on_combine(self, source_id: str, target_id: str) -> None:
        """Group the dragged node with a drop target into a new Independent group.

        Wraps the target (a card, or a whole group when dropped on its title bar) and
        the source into a fresh :class:`PowerGroup`, replacing the target in place —
        nesting naturally when the target already sits inside a group. Rejected when
        the two are the same node or the target lives inside the source's own subtree.
        """
        if source_id == target_id:
            return
        source = self._locate(source_id)
        target = self._locate(target_id)
        if source is None or target is None:
            return
        source_node, _src_list, _src_index, _src_parent = source
        if target_id in self._subtree_ids(source_node):
            return  # can't group a node with its own descendant
        # Remove the source, then re-find the target (its index may have shifted).
        source_node, src_list, src_index, _ = source
        src_list.pop(src_index)
        target = self._locate(target_id)
        if target is None:  # source and target were the same list and collapsed away
            src_list.append(source_node)  # put it back; nothing to do
            return
        target_node, tgt_list, tgt_index, _ = target
        group = PowerGroup(
            mode=STRUCTURE_INDEPENDENT,
            children=[target_node, source_node],
            active_child_id=target_node.id,
        )
        tgt_list[tgt_index] = group
        self._after_structural_change()

    def _on_move(self, source_id: str, parent_id: str, index: int) -> None:
        """Move the dragged node into a list (top-level or a group) at *index*.

        This is how a card is reordered, pulled out of a group (dropped in a higher
        list), or added to a group as another member (dropped in the group's body).
        Rejected when the destination lives inside the moved node's own subtree.
        """
        source = self._locate(source_id)
        if source is None:
            return
        source_node, src_list, src_index, _ = source
        if parent_id == "":
            dest_list: list[PowerNode] = self._character.powers
        else:
            if parent_id in self._subtree_ids(source_node):
                return  # can't move a node into itself
            dest = self._locate(parent_id)
            if dest is None or not isinstance(dest[0], PowerGroup):
                return
            dest_list = dest[0].children
        src_list.pop(src_index)
        if dest_list is src_list and src_index < index:
            index -= 1  # the pop shifted everything after the source down one
        index = max(0, min(index, len(dest_list)))
        dest_list.insert(index, source_node)
        self._after_structural_change()

    def _after_structural_change(self) -> None:
        """Tidy the tree after a combine/move, then rebuild and signal the change."""
        self._collapse_singletons()
        self._normalize_arrays()
        self._rebuild_list()
        self.changed.emit()

    def _collapse_singletons(self) -> None:
        """Dissolve groups left trivial by a move: one child unwraps, zero drops out."""

        def collapse(nodes: list[PowerNode]) -> list[PowerNode]:
            result: list[PowerNode] = []
            for node in nodes:
                if isinstance(node, PowerGroup):
                    node.children[:] = collapse(node.children)
                    if len(node.children) == 1:
                        result.append(node.children[0])
                    elif node.children:
                        result.append(node)
                    # an emptied group is dropped
                else:
                    result.append(node)
            return result

        self._character.powers[:] = collapse(self._character.powers)

    def _normalize_arrays(self) -> None:
        """Point every array group's ``active_child_id`` at a real child (else the first)."""

        def normalize(nodes: list[PowerNode]) -> None:
            for node in nodes:
                if isinstance(node, PowerGroup):
                    if node.mode == STRUCTURE_ARRAY and node.children:
                        ids = {child.id for child in node.children}
                        if node.active_child_id not in ids:
                            node.active_child_id = node.children[0].id
                    normalize(node.children)

        normalize(self._character.powers)

    # -- rendering --------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the cards from the current character state.

        The public seam the sheet calls when a fact *outside* this section changes a
        power's displayed numbers — an ability (a Strength-Based Damage folds in
        Strength; an attack power's PL cap tracks Attack) or the character's Power
        Level (which sets every attack cap). It only reads the model, so it never
        emits :attr:`changed` (no signal loop back to the triggering section).
        """
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the whole card tree from the model, toggling the empty label."""
        self._normalize_arrays()  # a valid active member per array before drawing
        self._list_host.clear()
        for node in self._character.powers:
            self._list_host.add_entry(node.id, self._render_node(node, None))
        self._empty.setVisible(not self._character.powers)
        self.set_block_title(
            title_with_cost("Powers", powers_points_spent(self._character, self._data))
        )

    def _render_node(
        self, node: PowerNode, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget:
        """A widget for one tree node — a group container or a leaf power card.

        ``interactive`` is ``False`` when an enclosing group is currently switched off
        (a Linked group's one toggle turns its whole subtree off); it greys out the
        node's runtime-activation controls so a member can't be re-activated while its
        group is inactive. Structural chrome (drag/edit/remove) is unaffected.
        """
        if isinstance(node, PowerGroup):
            return self._make_group_card(node, parent, interactive)
        return self._make_card(node, parent, interactive)

    # -- group card -------------------------------------------------------
    def _make_group_card(
        self, group: PowerGroup, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget:
        """A framed container: a mode title bar over its members, rendered indented."""
        card = _DraggableCard(group.id)
        card.setObjectName("groupCard")
        card.setStyleSheet("#groupCard { border: 1px solid #8894b0; border-radius: 6px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self._group_header(group, card, parent, interactive))

        # A Linked group that is off forces its whole subtree off, so its members'
        # activation controls are disabled; other modes just pass interactivity down.
        child_interactive = interactive and (
            self._group_is_active(group) if group.mode == STRUCTURE_LINKED else True
        )
        inner = _NodeList(group.id)
        inner.combineRequested.connect(self._on_combine)
        inner.moveRequested.connect(self._on_move)
        for child in group.children:
            inner.add_entry(child.id, self._render_node(child, group, child_interactive))
        indent = QWidget()
        indent_layout = QHBoxLayout(indent)
        indent_layout.setContentsMargins(14, 0, 0, 0)
        indent_layout.addWidget(inner)
        layout.addWidget(indent)
        return card

    def _group_header(
        self,
        group: PowerGroup,
        card: _DraggableCard,
        parent: PowerGroup | None,
        interactive: bool = True,
    ) -> QWidget:
        """The group's title bar: grip, name + rename, mode toggle, cost, ungroup —
        plus an Active checkbox when this group is itself a member of an array."""
        header = _GroupHeader()
        header.powerDropped.connect(lambda src, gid=group.id: self._on_combine(src, gid))
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)

        grip = _DragHandle()
        grip.setToolTip("Drag to move this group, or drop a power here to group it with this one")
        grip.dragStarted.connect(card.start_drag)
        row.addWidget(grip)
        grip.setVisible(not self._locked)

        mode_label = _MODE_LABELS.get(group.mode, _MODE_LABELS[STRUCTURE_INDEPENDENT])
        label = QLabel(group.name or mode_label)
        label.setStyleSheet("font-weight: bold;")
        row.addWidget(label)

        rename = QPushButton("✎")
        rename.setFixedWidth(24)
        rename.setToolTip("Rename this group")
        rename.clicked.connect(lambda _checked=False, g=group: self._rename_group(g))
        row.addWidget(rename)
        rename.setVisible(not self._locked)

        toggle = _ModeToggle()
        toggle.set_mode(group.mode)
        toggle.modeChanged.connect(lambda mode, g=group: self._set_group_mode(g, mode))
        toggle.set_toggle_enabled(not self._locked)
        row.addWidget(toggle)

        row.addStretch()

        # When this whole group is a member of an *array* parent, it gets the same
        # select control a leaf member gets. Otherwise a Linked group that can be
        # turned off (some member is gateable) and carries a standing bonus gets one
        # Active toggle for the whole group — every member switches together.
        control = self._array_member_control(group, parent, interactive)
        if control is not None:
            row.addWidget(control)
        elif (
            group.mode == STRUCTURE_LINKED
            and self._node_is_gateable(group)
            and self._node_has_standing(group)
        ):
            toggle = QCheckBox("Active")
            toggle.setChecked(self._group_is_active(group))
            toggle.setToolTip(
                "Switch this linked group on/off — every power in it toggles together."
            )
            # Runtime activation stays usable in the locked read-only view — turning a
            # power on/off is a mid-play action, not an edit to the build.
            toggle.setEnabled(interactive)
            toggle.toggled.connect(lambda on, g=group: self._set_group_active(g, on))
            row.addWidget(toggle)

        cost = QLabel(f"{node_display_cost(group, parent, self._data, self._character)} PP")
        cost.setEnabled(False)
        row.addWidget(cost)

        ungroup = QPushButton("✕")
        ungroup.setFixedWidth(24)
        ungroup.setToolTip("Ungroup — dissolve this group, keeping its powers")
        ungroup.clicked.connect(lambda _checked=False, g=group: self._ungroup(g))
        row.addWidget(ungroup)
        ungroup.setVisible(not self._locked)
        return header

    def _rename_group(self, group: PowerGroup) -> None:
        """Prompt for a new group name; blank clears it back to the mode label."""
        placeholder = _MODE_LABELS.get(group.mode, _MODE_LABELS[STRUCTURE_INDEPENDENT])
        name, ok = QInputDialog.getText(
            self,
            "Rename group",
            "Group name:",
            QLineEdit.EchoMode.Normal,
            group.name or placeholder,
        )
        if not ok:
            return
        group.name = name.strip()
        self._rebuild_list()
        self.changed.emit()

    def _set_group_mode(self, group: PowerGroup, mode: str) -> None:
        group.mode = mode
        self._normalize_arrays()
        self._rebuild_list()
        self.changed.emit()

    def _node_has_standing(self, node: PowerNode) -> bool:
        """Whether any leaf under *node* contributes a standing (non-instant) bonus."""
        return any(power_has_standing_effect(p, self._data) for p in self._leaf_powers(node))

    def _node_is_gateable(self, node: PowerNode) -> bool:
        """Whether any leaf under *node* carries a runtime gate (so it can be turned off)."""
        return any(power_runtime_gates(p, self._data) for p in self._leaf_powers(node))

    def _array_member_control(
        self, node: PowerNode, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget | None:
        """The select control a node gets by virtue of being an *array* member.

        A standing member (one with a bonus that stays on the sheet) gets the "Active"
        radio; an instant member of an otherwise-mixed array gets a momentary "Use"
        button (an attack isn't kept "active" — using it just drops the continuous
        sibling). An all-instant array has nothing to keep active, so no control is
        shown. ``None`` when the node isn't a member of a multi-member array.
        """
        if (
            not isinstance(parent, PowerGroup)
            or parent.mode != STRUCTURE_ARRAY
            or len(parent.children) < 2
        ):
            return None
        if not any(self._node_has_standing(child) for child in parent.children):
            return None  # all-instant array — nothing stands to be switched off
        if self._node_has_standing(node):
            return self._array_active_checkbox(node, parent, interactive)
        return self._array_use_button(node, parent, interactive)

    def _array_use_button(
        self, node: PowerNode, parent: PowerGroup, interactive: bool = True
    ) -> QPushButton:
        """A momentary "Use" for an instant member of a mixed array.

        An instant effect has no standing bonus to keep "Active"; using it just makes
        it the array's live alternate, which drops the continuous sibling. Disabled and
        labelled "In use" while it is the current selection.
        """
        in_use = active_array_child(parent) is node
        button = QPushButton("In use" if in_use else "Use")
        button.setToolTip(
            "Use this alternate — it becomes the array's live member, dropping any "
            "continuous sibling. An instant effect isn't kept 'active'."
        )
        # Usable while locked — using an alternate is a mid-play action, not an edit.
        button.setEnabled(interactive and not in_use)
        button.clicked.connect(
            lambda _checked=False, g=parent, nid=node.id: self._set_array_active(g, nid)
        )
        return button

    def _array_active_checkbox(
        self, node: PowerNode, parent: PowerGroup | None, interactive: bool = True
    ) -> QCheckBox | None:
        """An "Active" switch for a node that is a member of an *array* parent.

        Replaces the group's old active-member combo box: each array member carries its
        own checkbox, and checking one selects it as the live alternate (its siblings
        switch off). ``None`` when the node isn't inside a multi-member array.
        """
        if parent is None or parent.mode != STRUCTURE_ARRAY or len(parent.children) < 2:
            return None
        box = QCheckBox("Active")
        box.setChecked(active_array_child(parent) is node)
        box.setToolTip(
            "Only one array member is active at a time — check to make this the "
            "active one; its siblings switch off."
        )
        # Usable while locked — selecting the live alternate is a mid-play action.
        box.setEnabled(interactive)
        box.clicked.connect(
            lambda checked, g=parent, nid=node.id, cb=box: self._on_array_active_clicked(
                g, nid, cb, checked
            )
        )
        return box

    def _on_array_active_clicked(
        self, group: PowerGroup, child_id: str, checkbox: QCheckBox, checked: bool
    ) -> None:
        """Handle a click on an array member's Active switch.

        Checking a member selects it; an array always keeps exactly one live member, so
        un-checking the current one is refused (it just snaps back on).
        """
        if not checked:
            checkbox.setChecked(True)
            return
        self._set_array_active(group, child_id)

    def _set_array_active(self, group: PowerGroup, child_id: str) -> None:
        """Select an array member as the live alternate and switch it on.

        Only the selected member contributes to the sheet (:func:`live_powers` descends
        into it alone), so its siblings' bonuses drop off automatically. The newly-live
        member also has its runtime gates flipped on so its effect actually applies.
        """
        group.active_child_id = child_id
        located = self._locate(child_id)
        if located is not None:
            for member in self._leaf_powers(located[0]):
                member.activated = True
                member.item_present = True
                for effect in member.effects:
                    effect.toggled_on = True
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _ungroup(self, group: PowerGroup) -> None:
        """Dissolve a group, splicing its members back into the group's own slot."""
        located = self._locate(group.id)
        if located is None:
            return
        _node, siblings, index, _parent = located
        siblings[index : index + 1] = group.children
        self._after_structural_change()

    # -- leaf power card --------------------------------------------------
    def _make_card(
        self, power: Power, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget:
        """A stat-block card for one power: header, description, effects, roll line.

        The whole card carries the full game-term breakdown on its tooltip (the same
        data the Power Constructor shows while building), so hovering reveals every
        derived system value.
        """
        card = _DraggableCard(power.id)
        card.setToolTip(self._system_tooltip(power))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        layout.addWidget(self._header_row(power, card, parent, interactive))

        if power.description:
            desc = QLabel(power.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: gray; font-style: italic;")
            layout.addWidget(desc)

        effects = self._effects_block(power)
        if effects is not None:
            layout.addWidget(effects)

        # A dedicated bottom line for the numbers that come up mid-play: the attack
        # bonus and the save DC each effect imposes.
        layout.addWidget(hline_separator())
        layout.addWidget(self._rolls_label(power))
        return card

    def _header_row(
        self,
        power: Power,
        card: _DraggableCard,
        parent: PowerGroup | None,
        interactive: bool = True,
    ) -> QWidget:
        """Name + PL warning on the left; the on/off switch, cost, and edit/remove
        chrome on the right, led by a drag grip (hidden when locked).

        Returns a host widget (not a bare layout) so every child has a parent the
        moment it is created. Calling ``setVisible(True)`` on a *parentless* widget
        shows it as a momentary top-level window — on Windows that flashes a small
        window on screen and is slow to realize; the edit/remove buttons hit exactly
        that path, so the header must own them before their visibility is set.
        """
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        grip = _DragHandle()
        grip.dragStarted.connect(card.start_drag)
        layout.addWidget(grip)
        grip.setVisible(not self._locked)

        name = QLabel(power_display_name(power, self._data))
        # A Debilitated condition naming this power loses it — strike the header through
        # and redden it (display-only; the power's point cost is untouched).
        if power.name and power.name in debilitated_traits(self._character, self._data):
            name.setStyleSheet("font-weight: bold; font-size: 14px; color: #d15b5b;")
            font = name.font()
            font.setStrikeOut(True)
            name.setFont(font)
            name.setToolTip("Debilitated — this power is effectively lost")
        else:
            name.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(name)

        # A power that breaks a PL cap carries a warning marker naming the breach;
        # enforcement is a warning for now (see storage.pl_enforcement).
        violations = power_pl_violations(power, self._character, self._data)
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet("color: #d1a01e; font-weight: bold;")
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)

        # A homerule power (one carrying any Dev-mode override) is badged so a bent
        # value on the sheet is never mistaken for a by-the-book one.
        if power_is_homerule(power):
            homerule = QLabel("⌂")
            homerule.setStyleSheet(f"color: {_TINT_HOMERULE}; font-weight: bold;")
            homerule.setToolTip("Homerule power — carries manual (Dev-mode) overrides.")
            layout.addWidget(homerule)
        layout.addStretch()

        # An array member gets a select control (an "Active" radio for a standing
        # member, a momentary "Use" for an instant one, nothing for an all-instant
        # array). A member of a Linked group has no per-card switch — the group's
        # single toggle drives it. Otherwise a standalone power that carries a runtime
        # gate *and* a standing bonus gets its own on/off switch.
        control = self._array_member_control(power, parent, interactive)
        if control is not None:
            layout.addWidget(control)
        elif isinstance(parent, PowerGroup) and parent.mode == STRUCTURE_LINKED:
            pass  # the linked group's one Active toggle lives on the group header
        elif power_runtime_gates(power, self._data) and power_has_standing_effect(
            power, self._data
        ):
            active = QCheckBox("Active")
            active.setChecked(self._power_is_active(power))
            active.setToolTip("Switch this power on/off — its bonuses apply only while active.")
            # Usable while locked — turning a power on/off is a mid-play action.
            active.setEnabled(interactive)
            active.toggled.connect(lambda on, p=power: self._set_power_active(p, on))
            layout.addWidget(active)

        # Inside an array group a non-base member contributes only its flat pooled cost;
        # every other card shows its full assembled cost (node_display_cost decides).
        cost = QLabel(f"{node_display_cost(power, parent, self._data, self._character)} PP")
        cost.setEnabled(False)
        layout.addWidget(cost)

        # Add each button to the (host-owned) layout *before* setting visibility:
        # addWidget reparents it to `host`, so setVisible acts on a parented child.
        edit = QPushButton("✎")
        edit.setFixedWidth(24)
        edit.setToolTip("Edit this power")
        edit.clicked.connect(lambda _checked=False, p=power: self._edit_power(p))
        layout.addWidget(edit)
        edit.setVisible(not self._locked)

        remove = QPushButton("✕")
        remove.setFixedWidth(24)
        remove.setToolTip("Remove this power")
        remove.clicked.connect(lambda _checked=False, p=power: self._remove_power(p))
        layout.addWidget(remove)
        remove.setVisible(not self._locked)
        return host

    # -- effect summary (name + extras/flaws) -----------------------------
    def _effects_block(self, power: Power) -> QWidget | None:
        """A stacked, per-effect summary; ``None`` for a power with no effects."""
        if not power.effects:
            return None
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(3)
        for index, effect in enumerate(power.effects):
            layout.addWidget(self._effect_summary(power, effect, index))
        return host

    def _effect_summary(self, power: Power, effect: PowerEffectInstance, index: int) -> QWidget:
        """One effect: its name and effective rank, a composite role note, and its
        attached extras (green) and flaws (red)."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel(self._effect_title(effect))
        title.setStyleSheet("font-weight: bold;")
        header.addWidget(title)
        note = self._role_note(power, index)
        if note:
            role = QLabel(note)
            role.setStyleSheet("color: gray; font-style: italic;")
            header.addWidget(role)
        header.addStretch()
        layout.addLayout(header)

        extras = self._modifier_names(effect.extras)
        if extras:
            label = QLabel("Extras: " + ", ".join(extras))
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_TINT_BETTER};")
            layout.addWidget(label)
        flaws = self._modifier_names(effect.flaws)
        if flaws:
            label = QLabel("Flaws: " + ", ".join(flaws))
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_TINT_WORSE};")
            layout.addWidget(label)
        return box

    def _effect_title(self, effect: PowerEffectInstance) -> str:
        """``"Damage 8"`` — the effect's name at its effective rank (a Strength-Based
        Damage folds in the wielder's Strength, matching the constructor)."""
        base = next((e for e in self._data.effects if e.id == effect.effect_id), None)
        rank = effect_effective_rank(effect, self._data, self._character)
        return f"{base.name if base else effect.effect_id} {rank}"

    def _modifier_names(self, selections: list[ModifierSelection]) -> list[str]:
        """Resolve each selection to its modifier name, tagging a ranked one taken
        above rank 1 with its rank (e.g. ``"Accurate ×2"``) and a modifier with a
        typed free-text detail with it (e.g. ``"Limited (only at night)"``)."""
        catalog = self._data.modifier_catalog()
        names: list[str] = []
        for selection in selections:
            modifier = catalog.get(selection.modifier_id)
            if modifier is None:
                continue
            names.append(modifier_label(modifier, selection, rank_sep=" ×"))
        return names

    def _role_note(self, power: Power, index: int) -> str:
        """A composite effect's part: ``"base"``/``"alternate …"`` for an array or
        ``"linked"``; empty for a single or independent-multi effect."""
        if len(power.effects) < 2:
            return ""
        if power.structure == STRUCTURE_LINKED:
            return "linked"
        if power.structure == STRUCTURE_ARRAY:
            if index == array_base_index(power, self._data, self._character):
                return "base"
            return f"alternate ({array_alternate_cost(self._data)} pt)"
        return ""

    # -- roll line --------------------------------------------------------
    def _rolls_label(self, power: Power) -> QLabel:
        """The bottom roll line; a muted placeholder when the power rolls nothing."""
        text = self._rolls_text(power)
        label = QLabel(f"🎲 {text}" if text else "No attack or resistance roll")
        label.setWordWrap(True)
        if text:
            label.setStyleSheet(f"color: {_ACCENT};")  # a calm blue reserved for dice info
        else:
            label.setEnabled(False)
        return label

    def _rolls_text(self, power: Power) -> str:
        """The attack bonus and save DC each effect imposes, read from the same
        game-term rows the constructor shows; effect-prefixed for a multi-effect power."""
        multi = len(power.effects) > 1
        parts: list[str] = []
        for effect in power.effects:
            attack_bonus = effect_attack_skill_bonus(effect, self._character, self._data)
            rows = {
                r.key: r
                for r in effect_stat_rows(effect, self._data, self._character, attack_bonus)
            }
            segments = []
            if "check" in rows:
                segments.append(rows["check"].value)
            if "resistance" in rows:
                segments.append(rows["resistance"].value)
            elif "effect_dc" in rows:  # a save DC with no shown check/resistance phrase
                segments.append(rows["effect_dc"].value)
            if not segments:
                continue
            line = " · ".join(segments)
            if multi:
                base = next((e for e in self._data.effects if e.id == effect.effect_id), None)
                line = f"{base.name if base else effect.effect_id}: {line}"
            parts.append(line)
        return "    ".join(parts)

    # -- hover tooltip: the full game-term breakdown ----------------------
    def _system_tooltip(self, power: Power) -> str:
        """Rich-text breakdown of every effect's game-term stats for the card tooltip.

        Mirrors the Power Constructor's PowerTermsView: a structure header for a
        composite power, then each effect at its effective rank with its stat rows,
        each modifier-changed value tinted green (better) or red (worse)."""
        if not power.effects:
            return ""
        blocks: list[str] = []
        header = self._structure_header(power)
        if header:
            blocks.append(f"<b>{html.escape(header)}</b>")
        for index, effect in enumerate(power.effects):
            attack_bonus = effect_attack_skill_bonus(effect, self._character, self._data)
            title = html.escape(self._effect_title(effect))
            note = self._role_note(power, index)
            if note:
                title += f" <i>{html.escape(note)}</i>"
            rows = []
            for stat in effect_stat_rows(effect, self._data, self._character, attack_bonus):
                value = html.escape(stat.value)
                tint = _TINTS.get(stat.change)
                if tint:
                    value = f"<span style='color:{tint}'>{value}</span>"
                rows.append(f"{html.escape(stat.label)}: {value}")
            body = "<br>".join(rows)
            blocks.append(f"<p style='margin:4px 0 0 0'><b>{title}</b><br>{body}</p>")
        return "".join(blocks)

    @staticmethod
    def _structure_header(power: Power) -> str:
        if len(power.effects) < 2:
            return ""
        if power.structure == STRUCTURE_LINKED:
            return "Linked (all effects activate together)"
        if power.structure == STRUCTURE_ARRAY:
            return "Array (one effect active at a time)"
        return ""

    def _remove_power(self, power: Power) -> None:
        located = self._locate(power.id)
        if located is None:
            return
        _node, siblings, index, _parent = located
        siblings.pop(index)
        self._after_structural_change()

    # -- runtime on/off ---------------------------------------------------
    @staticmethod
    def _power_is_active(power: Power) -> bool:
        """Whether every runtime switch on the power is currently in its 'on' state."""
        return power.activated and power.item_present and all(e.toggled_on for e in power.effects)

    def _set_power_active(self, power: Power, active: bool) -> None:
        """Flip the power's runtime switches — and its whole linked group — together.

        A single "Active" control drives whichever gate the power carries (Activation,
        Removable, or a Sustained toggle); ``rules.effect_is_active`` reads only the
        flags the power's gates make relevant. Members of a Linked group switch on/off
        as one, so if this power sits directly in a Linked group every leaf under that
        group is flipped too. The ``changed`` signal is already wired to refresh the
        stats/skills sections, so the boosted totals update live.
        """
        for member in self._linked_activation_set(power):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _group_is_active(self, group: PowerGroup) -> bool:
        """Whether every leaf power under a linked group is currently switched on."""
        return all(self._power_is_active(p) for p in self._leaf_powers(group))

    def _set_group_active(self, group: PowerGroup, active: bool) -> None:
        """Flip every power under a linked group on/off as one (Decision 3).

        A Linked group presents a single Active toggle rather than a per-card switch,
        so a permanent member drops off with its sustained sibling. Mirrors
        :meth:`_set_power_active` but spans the whole group's leaves.
        """
        for member in self._leaf_powers(group):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _linked_activation_set(self, power: Power) -> list[Power]:
        """Every leaf power that switches on/off together with *power*.

        Just ``[power]`` unless it sits directly inside a Linked group, in which case
        all leaf powers under that group activate as one.
        """
        located = self._locate(power.id)
        if located is not None:
            parent = located[3]
            if isinstance(parent, PowerGroup) and parent.mode == STRUCTURE_LINKED:
                return self._leaf_powers(parent)
        return [power]

    @staticmethod
    def _leaf_powers(node: PowerNode) -> list[Power]:
        if isinstance(node, PowerGroup):
            leaves: list[Power] = []
            for child in node.children:
                leaves.extend(PowersSection._leaf_powers(child))
            return leaves
        return [node]

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points (Add / Remove / group chrome)."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        self._rebuild_list()
