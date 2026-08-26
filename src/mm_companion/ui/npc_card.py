"""One NPC, as a card on the GM's window.

Unlike a :class:`~mm_companion.ui.player_card.PlayerCard` — which shows a *remote*
player's live sheet and can only *ask* their app to change it — an NPC is the
GM's own, held locally as an ordinary :class:`~mm_companion.core.character.Character`
saved in the workspace ``gm_characters/`` dir. So this card acts on the model
directly: conditions apply straight onto it (persisted like any sheet edit),
initiative is rolled here, and the whole thing can be copied. The card is keyed
by its file name, which is the stable identity a session's cast
(:attr:`~mm_companion.core.session.model.SessionState.npc_paths`) records.

The card body's press-drag reorders it in the initiative list; the **portrait** is
what opens its sheet (see :mod:`~mm_companion.ui.card_summary`). Those were once
the same gesture, distinguished only by how far the pointer had moved, which meant
a reorder that fell short of the threshold opened a window instead.

That reorder is a real :class:`~PySide6.QtGui.QDrag` now, and had to become one the
day a card could be dragged onto the **Scene**. It used to track the pointer
itself and emit a preview for the window to draw, which is a fine gesture inside
one container and cannot leave it — nothing crosses a widget boundary, so there is
no way for another block to know a drag is happening at all. The drop target does
the work now (:class:`~mm_companion.ui.card_drop.CardDropFlow`), and this card only
says what is being dragged.

A card **collapses**. Expanded it is a good roster entry and a bad combat readout:
a 96px portrait, a PL, and two buttons, times a dozen mooks, is three cards on
screen at a time. Collapsed it keeps only what a GM reads mid-round — who it is,
whose turn it is, the pinned numbers, what conditions it is under — and the damage
row below. Everything shed is still one click away, and *which* state a card is in
is the GM's, remembered per creature by the window that owns them.

Two things the collapsed state must not lose, and so neither state has them where
they used to be: the **"+"** that applies a condition now sits beside the damage
row, the two of them being the two ways to put something on a creature; and
**initiative** is rolled by its own badge, the explicit button having gone rather
than exist on one state only. Both **swallow their press** — the badge by hand, the
"+" by being a button — because a press that reaches the card starts its
drag-to-reorder, which is the same reason
:class:`~mm_companion.ui.card_summary.PortraitButton` swallows its own.

**Right-click means "take that away"** wherever it lands on this card: on a
condition chip it sheds the condition, on the initiative badge it clears the roll,
and on the card itself it offers to remove or delete the NPC. Each of the first two
consumes the event, so the general answer never fires over a specific one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.core.dice import roll_d20
from mm_companion.core.library import CharacterSummary
from mm_companion.core.rules import initiative_modifier
from mm_companion.ui import theme
from mm_companion.ui.card_chips import _ConditionChip, _SceneEye, start_card_drag
from mm_companion.ui.card_summary import PortraitButton, character_summary_html
from mm_companion.ui.damage_row import DamageRow
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.pin_panel import PIN_PANEL_WIDTH, install_pin_panel
from mm_companion.ui.sections.conditions import (
    build_condition_menu,
    condition_display_name,
    condition_tooltip,
)
from mm_companion.ui.widgets import ElidingLabel

#: How wide the card's own column is. Fixed, so a row of them lines up in the
#: flow layout; the pinned-parameter strip adds its own width beside it. The same
#: in both states on purpose: collapsing wins its room in *height*, and a card that
#: also narrowed would break the columns its neighbours line up in.
CARD_WIDTH = 210
#: How far the pointer must move with the button down to count as a drag rather
#: than a click, in pixels.
DRAG_THRESHOLD = 8
#: How many pinned chips a collapsed card shows before the strip scrolls. The strip
#: is most of what is left of a collapsed card, so a creature with eight pins must
#: not make its card twice the height of its neighbour's.
COLLAPSED_PIN_ROWS = 4
#: What the collapse toggle reads: the caret points the way the card will go.
EXPANDED_GLYPH = "▾"
COLLAPSED_GLYPH = "▸"
#: The initiative badge before anything has been rolled. Something to click, and a
#: dash rather than a zero — an unrolled NPC has no initiative, not one of nought.
NO_INITIATIVE = "init —"
#: How this card names itself in a drag. The kind matters: a file name and a
#: player id are not distinguishable by looking at them, so the drop target
#: would not know which roster a bare one came from.
SCENE_KIND = "npc"


class _InitiativeBadge(QLabel):
    """The NPC's initiative, and the only thing that rolls or clears it.

    A ``QLabel`` for the same reason
    :class:`~mm_companion.ui.card_summary.PortraitButton` is one: a ``QToolButton``
    wraps its text in some forty pixels of its own chrome, and four of those across
    a 210px card leave the name a stub. It carries the affordance instead — a
    pointing hand, a tooltip, an accent — and, like the portrait, **swallows its
    press**, so clicking it can never be read as the start of the card's
    drag-to-reorder.

    Left-click rolls, right-click clears. The pair belongs on the one widget: the
    number *is* the thing being set, there is nowhere else on a collapsed card to
    put a second control, and a GM who has mis-rolled an NPC's place in the order
    otherwise has to drag the card out of the rolled zone to be rid of it.
    """

    clicked = Signal()
    #: Right-clicked — take this NPC back out of the initiative order.
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        font = self.font()
        font.setBold(True)
        font.setPointSizeF(theme.font_size("size.terms"))
        self.setFont(font)
        self.setStyleSheet(f"color: {theme.color('accent')};")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Roll initiative for this NPC.\n\nRight-click to clear it.")

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Consumed either way: propagating would reach the card's own
        # "Remove from this session / Delete" menu, which is not what someone
        # aiming at the initiative number asked for.
        self.cleared.emit()
        event.accept()


def _small_button(text: str = "") -> QToolButton:
    """A square tool button the size of one damage button.

    Left to itself a ``QToolButton`` claims some forty pixels for a single glyph,
    and four of those across a 210px card leave the name a stub. Sized to
    ``gm.damage-button`` so every small control on a GM card is the same square,
    with the glyph's size on the font — never in a stylesheet, which would outrank
    it.
    """
    button = QToolButton()
    button.setText(text)
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    side = int(theme.metric("gm.damage-button"))
    button.setFixedSize(side, side)
    font = button.font()
    font.setPointSizeF(theme.font_size("size.terms"))
    button.setFont(font)
    return button


class NPCCard(QFrame):
    """A GM's NPC: portrait, name, and estimated Power Level, acted on locally."""

    #: The NPC's file name — the GM asked to open its sheet.
    openRequested = Signal(str)
    #: The NPC's file name — take it out of this session (the file stays).
    removeRequested = Signal(str)
    #: The NPC's file name — delete the file for good.
    deleteRequested = Signal(str)
    #: ``(file_name, condition_id, parameter)`` — put this condition on the NPC.
    #: The parameter rides as ``object`` (it is ``str | None``).
    applyConditionRequested = Signal(str, str, object)
    #: ``(file_name, condition_id, parameter)`` — take it off again.
    removeConditionRequested = Signal(str, str, object)
    #: ``(file_name, total)`` — this NPC just rolled initiative.
    #: ``(file name, on)`` — the GM put this NPC on the shared board, or took it
    #: off. The window owns what that means; the card only says it was asked.
    sceneToggled = Signal(str, bool)
    initiativeRolled = Signal(str, int)
    #: The NPC's file name — its initiative was cleared, so it leaves the order.
    initiativeCleared = Signal(str)
    #: The NPC's file name — duplicate it into a new NPC (Goon → Goon-2).
    copyRequested = Signal(str)
    #: ``(file_name, refs)`` — this card's pinned-parameter strip changed.
    pinsChanged = Signal(str, object)
    #: A ``RollSpec`` — the GM clicked a pinned chip and wants it in the roller.
    loadRequested = Signal(object)
    #: The same, double-clicked — throw it.
    rollRequested = Signal(object)
    #: The NPC's file name — the GM asked to pin something to this card.
    pinPickerRequested = Signal(str)
    #: ``(file_name, collapsed)`` — the GM shrank or reopened this card. Emitted only
    #: for a click on the caret; :meth:`set_collapsed` is silent, being a *load*.
    collapsedChanged = Signal(str, bool)
    #: ``(file_name, step_index)`` — a rung of the damage ladder was clicked. The
    #: index, not the conditions: what a degree of failure *means* is the rules
    #: layer's answer, and it depends on what the creature already carries.
    damageRequested = Signal(str, int)

    def __init__(
        self,
        character: Character,
        summary: CharacterSummary,
        data: GameData,
        parent: QWidget | None = None,
        *,
        initiative: int | None = None,
        collapsed: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # The card's own column is fixed; the pinned strip adds its width beside it.
        self.setFixedWidth(CARD_WIDTH + PIN_PANEL_WIDTH + int(theme.metric("space.sm")) * 3)

        self._data = data
        self._character = character
        self._summary = summary
        self._conditions_by_id: dict[str, Condition] = {c.id: c for c in data.conditions}
        #: The file name, this card's stable identity across a refresh.
        self.name_key = summary.path.name if summary.path is not None else ""
        #: Where a left-button press landed, to tell a drag from a click.
        self._press_pos = None

        layout = QVBoxLayout()

        # The header is the whole card when it is collapsed, so everything a GM
        # reaches for mid-round lives here: who it is, whose turn it is, the "+",
        # and the way back to the full card.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(int(theme.metric("space.xs")))

        thumb_size = int(theme.metric("gm.thumb"))
        self._thumb = PortraitButton(size=thumb_size, placeholder="?")
        self._thumb.clicked.connect(lambda: self.openRequested.emit(self.name_key))
        header.addWidget(self._thumb)

        # Two labels for one name, in the same slot, exactly one of them showing.
        # The two states want different things of a name: collapsed it shares its
        # row with a thumbnail, a badge and a caret, so it *elides* — a wrapped name
        # would take a second line the row's height (the thumbnail's) does not have,
        # and clip. Expanded there is no thumbnail and the row can grow, so it wraps
        # as it always has. Both live here rather than the wrapped one keeping a row
        # of its own, which left the expanded card with a near-empty strip above its
        # own name.
        self._header_name = ElidingLabel(summary.name)
        header_name_font = self._header_name.font()
        header_name_font.setBold(True)
        self._header_name.setFont(header_name_font)
        header.addWidget(self._header_name, stretch=1)

        self._name_label = QLabel(summary.name)
        name_font = self._name_label.font()
        name_font.setBold(True)
        self._name_label.setFont(name_font)
        self._name_label.setWordWrap(True)
        header.addWidget(self._name_label, stretch=1)

        # In the header, so it survives a collapse: the two things a GM reaches
        # for mid-round on a shrunk card are whose turn it is and whether the
        # table can see this creature at all.
        self._scene_eye = _SceneEye()
        self._scene_eye.toggled.connect(lambda on: self.sceneToggled.emit(self.name_key, bool(on)))
        header.addWidget(self._scene_eye)

        # The badge *is* the roll affordance once the explicit button is hidden.
        self._initiative_badge = _InitiativeBadge()
        self._initiative_badge.clicked.connect(self.roll_initiative)
        self._initiative_badge.cleared.connect(self.clear_initiative)
        header.addWidget(self._initiative_badge)

        self._collapse_button = _small_button()
        self._collapse_button.clicked.connect(self._toggle_collapsed)
        header.addWidget(self._collapse_button)
        layout.addLayout(header)

        self._portrait = PortraitButton()
        self._portrait.clicked.connect(lambda: self.openRequested.emit(self.name_key))
        layout.addWidget(self._portrait, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._set_portrait(summary.image_path)

        # The rows a collapsed card sheds are hosted in widgets rather than added as
        # bare layouts, for the plain reason that a layout cannot be hidden.
        pl_host = QWidget()
        pl_row = QHBoxLayout(pl_host)
        pl_row.setContentsMargins(0, 0, 0, 0)
        self._pl_label = QLabel(f"PL {summary.power_level}")
        pl_row.addWidget(self._pl_label)
        pl_row.addStretch()
        layout.addWidget(pl_host)

        # No "Initiative" button here any more: the badge above rolls, in both
        # states, so this was a second way to do one thing — and the one that cost
        # a row of the expanded card and did not exist on the collapsed one.
        buttons_host = QWidget()
        buttons_row = QHBoxLayout(buttons_host)
        buttons_row.setContentsMargins(0, 0, 0, 0)
        self._copy_button = QToolButton()
        self._copy_button.setText("Copy")
        self._copy_button.setToolTip("Duplicate this NPC (Goon → Goon-2)")
        self._copy_button.clicked.connect(lambda: self.copyRequested.emit(self.name_key))
        buttons_row.addWidget(self._copy_button)
        buttons_row.addStretch()
        layout.addWidget(buttons_host)

        self.set_initiative(initiative)

        # The two ways to put something on this creature, side by side and on both
        # states: pick a condition by hand, or take a rung of the damage ladder. A
        # GM who never collapses a card still wants a failed Toughness save to cost
        # one click rather than three trips through a menu of thirty-nine.
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(int(theme.metric("space.xs")))
        self._condition_button = _small_button("+")
        self._condition_button.setToolTip("Apply a condition to this NPC")
        self._condition_button.clicked.connect(self._show_condition_menu)
        action_row.addWidget(self._condition_button)
        self._damage = DamageRow(data)
        self._damage.set_character(character)
        self._damage.stepChosen.connect(
            lambda index: self.damageRequested.emit(self.name_key, index)
        )
        action_row.addWidget(self._damage, stretch=1)
        layout.addLayout(action_row)

        self._chips = FlowContainer()
        self._chip_flow = FlowLayout(self._chips)
        layout.addWidget(self._chips)
        layout.addStretch()

        #: The widgets the collapsed card sheds — everything that describes the
        #: creature rather than tracking it through a fight.
        self._expanded_only = (self._name_label, self._portrait, pl_host, buttons_host)

        self.pins = install_pin_panel(self, layout, data)
        self.pins.set_character(character)
        self.pins.pinsChanged.connect(lambda refs: self.pinsChanged.emit(self.name_key, refs))
        self.pins.loadRequested.connect(self.loadRequested)
        self.pins.rollRequested.connect(self.rollRequested)
        self.pins.pickRequested.connect(lambda: self.pinPickerRequested.emit(self.name_key))

        self.set_collapsed(collapsed)
        self._refresh_tooltip()

    # -- what the card is showing -----------------------------------------

    @property
    def character(self) -> Character:
        """The NPC's model — the GM edits this directly."""
        return self._character

    def set_in_scene(self, on: bool) -> None:
        """Show whether this NPC is on the shared board. Silent, like
        :meth:`set_collapsed`: the window telling a card what it already decided
        must not bounce back as a fresh request."""
        self._scene_eye.set_in_scene(on)

    @property
    def in_scene(self) -> bool:
        """Whether the eye says this creature is on the board."""
        return self._scene_eye.isChecked()

    def display_name(self) -> str:
        """The NPC's name, as its summary gives it."""
        return self._summary.name

    @property
    def initiative(self) -> int | None:
        """The NPC's rolled initiative this session, or ``None`` if unrolled."""
        return self._initiative

    @property
    def collapsed(self) -> bool:
        """Whether the card is showing its short form."""
        return self._collapsed

    # -- collapsing --------------------------------------------------------

    def set_collapsed(self, collapsed: bool) -> None:
        """Show the short form (or the full card) — without announcing it.

        Silent, like :meth:`~mm_companion.ui.pin_panel.PinPanel.set_pins`: this is
        the owner telling the card what it already decided, and echoing that back
        would have the window save what it just read. The caret emits instead.
        """
        self._collapsed = collapsed
        for widget in self._expanded_only:
            widget.setVisible(not collapsed)
        self._thumb.setVisible(collapsed)
        self._header_name.setVisible(collapsed)
        self._collapse_button.setText(COLLAPSED_GLYPH if collapsed else EXPANDED_GLYPH)
        self._collapse_button.setToolTip("Show the whole card" if collapsed else "Shrink this card")
        self.pins.set_max_visible(COLLAPSED_PIN_ROWS if collapsed else None)
        # The chips are built at one size or the other, so they are rebuilt rather
        # than restyled — which the card does on every condition change anyway.
        self.refresh_conditions()
        self.updateGeometry()

    def _toggle_collapsed(self) -> None:
        """The caret: flip the state *and* say so, so the window can remember it."""
        self.set_collapsed(not self._collapsed)
        self.collapsedChanged.emit(self.name_key, self._collapsed)

    # -- initiative --------------------------------------------------------

    def set_initiative(self, total: int | None) -> None:
        """Show (or clear) the NPC's initiative badge."""
        self._initiative = total
        self._initiative_badge.setText(NO_INITIATIVE if total is None else f"init {total}")

    def roll_initiative(self) -> int:
        """Roll d20 + this NPC's initiative modifier, show it, and announce it.

        Local by design — an NPC is the GM's own and never on the wire, so this
        is not routed through the session server the way a shared roll is.
        """
        total = roll_d20() + initiative_modifier(self._character, self._data)
        self.set_initiative(total)
        self.initiativeRolled.emit(self.name_key, total)
        return total

    def clear_initiative(self) -> None:
        """Take this NPC back out of the initiative order.

        The undo for a roll made on the wrong creature, or for a round that is
        over. It drops the card into the un-rolled zone, which is also where a drag
        would have put it — this is just the way to say so without one.
        """
        self.set_initiative(None)
        self.initiativeCleared.emit(self.name_key)

    def _set_portrait(self, image_path: str | None) -> None:
        """Show the NPC's picture (a local file, so it resolves normally)."""
        resolved = library.resolve_image_path(image_path)
        self._portrait.set_image(QPixmap(resolved) if resolved else None)

    # -- hover summary -----------------------------------------------------

    def _refresh_tooltip(self) -> None:
        """Put the hover summary on the **name**, from the current model.

        Not on the card, which is where it used to be: a tooltip on the card fires
        wherever the pointer rests, so a GM lining up a pinned chip or a degree
        button got a wall of stats over the thing they were aiming at. The name is
        the part of the card that *is* the creature, so it is the part that
        describes it. The rest of the card keeps its own tooltips, which are about
        what each control does.
        """
        summary = self.summary_html()
        self._name_label.setToolTip(summary)
        # Through the label's own seam: it owns its tooltip (it shows the full name
        # there when the name is clipped) and would wipe a plain setToolTip on the
        # next resize.
        self._header_name.set_hover_text(summary)

    def summary_html(self) -> str:
        """A compact abilities / resistances / powers summary, as tooltip HTML."""
        return character_summary_html(self._character, self._data, self._summary.name)

    # -- conditions --------------------------------------------------------

    def refresh_conditions(self) -> None:
        """Rebuild the condition chips from the (possibly just-changed) model."""
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for applied in self._character.conditions:
            record = self._conditions_by_id.get(applied.condition_id)
            chip = _ConditionChip(
                condition_display_name(applied, record),
                tooltip=condition_tooltip(applied, record, self._conditions_by_id),
                compact=self._collapsed,
            )
            chip.arm_removal(
                lambda cid=applied.condition_id, param=applied.parameter: (
                    self.removeConditionRequested.emit(self.name_key, cid, param)
                )
            )
            self._chip_flow.addWidget(chip)
        self._chips.setVisible(bool(self._character.conditions))
        # What a damage button will do depends on what is already on the creature,
        # so the row's tooltips are re-derived whenever the conditions move.
        self._damage.refresh()
        # Re-derived whenever the card redraws, not kept from construction. The
        # summary reads the *build* numbers, which no condition currently moves —
        # so this changes nothing today. It is here because "computed once in
        # __init__ over a mutable model" is the shape of a bug waiting for the
        # first summary line that does depend on runtime state.
        self._refresh_tooltip()

    def condition_names(self) -> list[str]:
        """The chip captions, in order — the readable form of the NPC's conditions."""
        return [
            self._chip_flow.itemAt(i).widget().text()  # type: ignore[union-attr]
            for i in range(self._chip_flow.count())
        ]

    def _show_condition_menu(self) -> None:
        """The same catalog, split the same way, the sheet's own "+" offers."""
        menu = build_condition_menu(self, self._data, self._choose_condition)
        button = self._condition_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _choose_condition(self, condition: Condition) -> None:
        """Ask for the condition's subject if it needs one, then request it.

        The dialog reads the NPC's own character for its "a specific advantage /
        power" choices, so the GM picks from what the NPC actually has.
        """
        from mm_companion.ui.sections.condition_dialog import prompt_condition_parameter

        go_ahead, parameter = prompt_condition_parameter(
            condition, self._data, self._character, self
        )
        if go_ahead:
            self.applyConditionRequested.emit(self.name_key, condition.id, parameter)

    # -- interaction -------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Past the threshold, hand the gesture to Qt as a real drag."""
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            super().mouseMoveEvent(event)
            return
        self._press_pos = None
        start_card_drag(self, f"{SCENE_KIND}:{self.name_key}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        menu = QMenu(self)
        menu.addAction(
            "Remove from this session",
            lambda: self.removeRequested.emit(self.name_key),
        )
        menu.addAction(
            f"Delete {self._summary.name}",
            lambda: self.deleteRequested.emit(self.name_key),
        )
        menu.exec(event.globalPos())
