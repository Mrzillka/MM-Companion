"""Section 4: powers.

The most complex part of a character. An "Add Power" button opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` brick-builder in
its own window; saving there hands the finished
:class:`~mm_companion.core.powers.Power` back through
:attr:`~mm_companion.ui.power_constructor.PowerConstructorWindow.powerSaved`, which
this section appends to the shared :class:`~mm_companion.core.character.Character`
and shows as a *card*. Each card reads top-to-bottom like a stat-block entry: a
header (name, assembled point cost, a ⚠ marker when the power breaks a Power Level
cap), the free-text description, and a per-effect summary pairing each effect's extras
and flaws with *its full game-term table* alongside them (Type / Range / Action /
Duration / checks / measures — the same data the Power Constructor shows while
building, rendered small and muted so it informs without shouting). A power that calls
for dice closes with a footer listing one roll per line (the attack check, then the
save it forces); a power that rolls nothing simply ends. Each card carries an edit
button that reopens the constructor pre-loaded with that power — editing a deep copy
that replaces the original in place on save — and a remove button.

**The card is the on/off switch.** There is no small "Active" checkbox: clicking a
card's body toggles a runtime-gated power (or, for a member of an array, makes it the
live alternate), and the card's own appearance carries the state — a switched-off
power stays fully readable but recedes, dimmed and a notch smaller, so a glance at the
block tells you what is running. Flipping one *eases* between those two looks rather
than cutting, and a card you can click says so before you touch it: an accent edge down
its left side, and an accent border under the pointer — on exactly one card at a time,
the innermost one a click would reach. Clicking the grip, ✎ or ✕ never toggles: those
widgets consume the click themselves.

Powers can be **grouped**. A character's ``powers`` is a tree of
:data:`~mm_companion.core.powers.PowerNode` — leaf powers and
:class:`~mm_companion.core.powers.PowerGroup` containers, which can nest arbitrarily.
Dragging a card into a *gap* reorders (or moves it into/out of a group); dropping it
*onto* another card, or onto a group's title bar, combines the two into a group with
a distinct highlight. A group's title bar carries an Independent / Array / Linked
mode toggle (the same three choices the Constructor offers for a single power's own
effects) that decides how its members' costs combine.

The **cards themselves** live in :mod:`mm_companion.ui.cards` — the frame and its
on/off look, the grip, the drop-target lists, the dice footer, the per-effect summary.
This section is the part that knows about *powers*: the tree, what a click on a card
means, what a group's mode does to its members' costs. The Equipment block draws its
items from the same pieces (an item wraps a real ``Power``), so a change to how a card
looks or behaves reaches both.

It follows the standard section contract (``data`` + ``character`` constructor,
``changed`` signal, ``set_locked``) so it slots into the sheet like the others, and —
because saved powers live on the model — a loaded character repopulates its list at
construction.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
    power_is_homerule,
)
from mm_companion.core.rules import (
    PIN_POWER,
    PinRef,
    active_array_child,
    array_alternate_cost,
    array_dynamic_primary_cost,
    debilitated_traits,
    effect_current_rank,
    effect_stands,
    group_array_base_index,
    leaf_powers,
    live_powers,
    node_display_cost,
    power_display_name,
    power_has_custom_modifier,
    power_has_standing_effect,
    power_pl_violations,
    power_roll_lines,
    power_rolls,
    power_runtime_gates,
    powers_points_spent,
    size_steps,
)
from mm_companion.ui import theme
from mm_companion.ui.cards import (
    DraggableCard,
    DragHandle,
    GroupHeader,
    NodeList,
    RollLine,
    RollsFooter,
    effect_title,
    effects_block,
)
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.sections.stat_table import PinMenuState
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    BOLD_STYLE,
    hline_separator,
    muted_style,
    preserved_scroll,
    tinted_style,
)

# The card machinery moved to :mod:`mm_companion.ui.cards` so the Equipment block could
# draw the same cards. These are the spellings anything that reached into this module
# already imports; the classes themselves are the shared ones.
_DraggableCard = DraggableCard
_DragHandle = DragHandle
_GroupHeader = GroupHeader
_NodeList = NodeList
_RollLine = RollLine

# What a click on a card does, by activation role (see _activation_role).
_CLICK_HINTS = {
    "toggle": "Click this card to switch the power on or off — its bonuses apply "
    "only while it is active.",
    "select": "Click this card to make it the array's live alternate; its siblings switch off.",
}

# What each group mode is called on its title bar.
_MODE_LABELS = {
    STRUCTURE_INDEPENDENT: "Group of powers",
    STRUCTURE_ARRAY: "Group of alternate effects",
    STRUCTURE_LINKED: "Group of linked powers",
}


def roll_lines(power: Power, character: Character, data: GameData) -> list[str]:
    """One entry per die roll a power calls for; effect-prefixed for a multi-effect power.

    Module-level so both the power card's dice footer and the GM's NPC hover summary
    read the same lines. The lines are the labels of :func:`~mm_companion.core.rules.
    power_rolls`, which is also what the card's 🎲 buttons roll — so what is written
    and what is rolled can never drift apart.
    """

    return power_roll_lines(power, character, data)


def _mode_toggle_style(locked: bool) -> str:
    """The mode switch's own stylesheet, in tokens every preset defines.

    It is stated **here, on the widget**, and not as a rule in the theme's
    application sheet — the same bargain :mod:`~mm_companion.ui.lock` and the
    compact roller's overlay button strike, and here it is forced twice over.
    Classic emits no widget chrome at all, so an app-level rule would exist under
    some presets and not others; and a *styled* preset states ``QPushButton``'s box
    (border, radius, padding), which makes ``QStyleSheetStyle`` take the whole box
    over and stop painting the platform's sunken/checked panel. That is the bug this
    fixes: with no ``:checked`` rule anywhere, the lit segment painted exactly like
    its two neighbours, and a group card gave no sign of being Independent, Array or
    Linked. Only tokens *every* preset defines are used — Classic has no
    ``surface.*`` group.

    The resting border is drawn rather than dropped so a segment does not jump
    sideways as it lights up, which is the lesson the theme's tool-button rules and
    ``QuickRollStar`` already carry. No ``font-size`` here: weight only.

    Used by the group card's mode switch alone now that the size ladder has become a
    slider; it stays a shared helper because a segmented strip is the shape any future
    one-of-N card control wants, and two of them agreeing about nothing is the bug this
    docstring exists to prevent.
    """
    accent = theme.color("accent")
    rest = (
        "QPushButton {"
        " background: transparent;"
        f" color: {theme.color('text.muted')};"
        f" border: {int(theme.metric('border.width'))}px solid {theme.color('border.card')};"
        f" border-radius: {int(theme.metric('radius.chip'))}px;"
        f" padding: {int(theme.metric('space.xs'))}px {int(theme.metric('space.sm'))}px; }}"
    )
    lit = (
        "QPushButton:checked {"
        f" background: {accent};"
        f" color: {theme.color('text.on-badge')};"
        f" border-color: {accent};"
        " font-weight: bold; }"
    )
    if locked:
        # A locked switch is a read-out, so it sheds the hover cue along with the
        # rest of its input chrome; set_locked hides the unlit segments entirely.
        return f"{rest}\n{lit}"
    hover = f"QPushButton:hover {{ border-color: {accent}; color: {accent}; }}"
    return f"{rest}\n{hover}\n{lit}"


def _lit_width(button: QPushButton) -> int:
    """How wide *button* has to be to hold its label once that label lights up.

    Only the checked segment is bold, and a size hint measured from the resting
    font is a few pixels short of the bold one — which showed up as a clipped
    "Independen" the moment that segment was the mode in force. Every segment gets
    the same allowance, so lighting one up never re-widths the strip either.

    The bold *delta* is added to the hint rather than the bold advance replacing
    it: the hint already carries the stylesheet's padding, the border and Qt's own
    margins, and none of those are worth re-deriving here.
    """
    font = button.font()
    bold = QFont(font)
    bold.setBold(True)
    grew = QFontMetrics(bold).horizontalAdvance(button.text()) - QFontMetrics(
        font
    ).horizontalAdvance(button.text())
    return button.sizeHint().width() + max(0, grew)


class _ModeToggle(QWidget):
    """A segmented Independent / Array / Linked switch for a group's title bar.

    Mirrors the Power Constructor's mode bar (the same three choices for how parts
    combine), but scoped to whole cards in a group rather than one power's effects.
    Emits :attr:`modeChanged` with a structure id when the user picks a segment.

    The lit segment *is* how the card reports its group's mode, so it states its own
    look rather than trusting the platform to paint a checked button — see
    :func:`_mode_toggle_style`. Locked, it goes on saying which mode is in force and
    stops being a control: :meth:`set_locked`.
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
        row.setSpacing(int(theme.metric("space.xs")))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for mode, label, tip in self._MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setFixedHeight(22)
            button.setMinimumWidth(_lit_width(button))
            self._group.addButton(button)
            self._buttons[mode] = button
            row.addWidget(button)
        self._group.buttonClicked.connect(self._on_clicked)
        self.set_locked(False)

    def _on_clicked(self, button: QPushButton) -> None:
        for mode, candidate in self._buttons.items():
            if candidate is button:
                self.modeChanged.emit(mode)
                return

    def set_mode(self, mode: str) -> None:
        """Reflect a mode into the buttons without emitting :attr:`modeChanged`."""
        (self._buttons.get(mode) or self._buttons[STRUCTURE_INDEPENDENT]).setChecked(True)

    def set_locked(self, locked: bool) -> None:
        """Read-only view: keep the mode legible, drop the switch.

        Locking is *not* ``setEnabled(False)`` anywhere in this app, and a greyed
        strip of three segments is exactly how a group's mode became unreadable — so
        the two unlit segments go away instead and the lit one stays on as a static
        chip naming the mode. It is left transparent to the mouse rather than
        disabled, so a click on it falls through to the group card the way a click
        anywhere else on the title bar does; the card is the switch. Call this
        *after* :meth:`set_mode`, or there is no lit segment yet to keep.
        """
        self.setStyleSheet(_mode_toggle_style(locked))
        for button in self._buttons.values():
            button.setVisible(not locked or button.isChecked())
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus if locked else Qt.FocusPolicy.StrongFocus)
            button.setCursor(
                Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor
            )


class _RankDial(QWidget):
    """A slider for the rank an effect is currently *held at*, and what that means.

    Two kinds of power want one and they want the same control. A Growth 3 is not one
    leap to Gargantuan — it is Large, then Huge, then Gargantuan, and which of the three
    you are standing at is a mid-fight decision. A Damage 10 is not all-or-nothing
    either: a hero pulling their punches fires it at 5. So the card carries one slider
    from ``0`` to the effect's bought rank, with a label beside it saying what the
    current notch *is*: the **size the character becomes** for a size effect (read
    against the wielder, so a Small character's dial starts at Medium) and the plain
    rank otherwise. A rank is an accounting fact the card already prints; "Huge" is the
    thing being chosen.

    Ranks the Size Table clamps together simply repeat their category, which is honest —
    a Growth 8 really does spend several of its ranks at Gargantuan.

    Four behaviours carried over from the strip of buttons this replaces:

    * **Zero is off.** Nothing is held while the power is switched off — the dial
      reports where the power *is*, and off is nowhere — and sliding back to 0 switches
      it off, exactly as clicking the card would, so the dial is a whole control rather
      than one that can only turn a power on. Sliding up from 0 wakes the power at the
      notch asked for, so going from dormant to Huge is one gesture.
    * **It commits on release.** Every runtime setter ends in a rebuild, so a slider
      that wrote on each tick would delete itself under the player's thumb. The label
      tracks the drag; only ``sliderReleased`` (and a keyboard or groove step, which
      leaves the handle up) reaches the section.
    * **NoFocus**, because committing destroys the whole card: focus would land on
      whatever the tab order offers next — a table in some other block — and a
      ``QScrollArea`` scrolls to show a child that has just taken focus. That was the
      page jumping away from the card under the cursor.
    * **Live in the locked sheet**, like every other runtime control on a card: how far
      a power is turned up is a play action, not a build edit. Inside a switched-off
      Linked group it goes transparent to the mouse instead, so a click falls through to
      the card exactly as it does off the group's own chrome — never
      ``setEnabled(False)``, which nothing in this app does.
    """

    rankPicked = Signal(int)  #: the effect rank the player settled on (0 = switch off)

    def __init__(
        self,
        caption: str,
        maximum: int,
        current: int,
        labels: dict[int, str],
        interactive: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._labels = labels
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        caption_label = QLabel(caption)
        caption_label.setStyleSheet(muted_style())
        row.addWidget(caption_label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, maximum))
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(1)
        self._slider.setValue(max(0, min(maximum, current)))
        self._slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
        self._slider.setCursor(
            Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor
        )
        guard_wheel(self._slider)  # don't let a card's slider steal the page wheel
        # *After* the wheel guard, which asks for StrongFocus so a focused widget keeps
        # its own wheel. Focus is the one thing this slider must never take: committing
        # destroys the card, and a QScrollArea chases whatever takes focus next.
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self._slider, 1)

        self._value = QLabel(self._label_for(self._slider.value()))
        row.addWidget(self._value)

        # Connected *after* the initial value, so seeding the dial never reads as the
        # player having moved it.
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.sliderReleased.connect(self._commit)

    def _label_for(self, rank: int) -> str:
        return self._labels.get(rank, f"Rank {rank}")

    def _on_value_changed(self, value: int) -> None:
        self._value.setText(self._label_for(value))
        # A drag reports every notch it passes; only the one it stops on is a decision.
        # A keyboard or groove step leaves the handle up, and *is* one.
        if not self._slider.isSliderDown():
            self._commit()

    def _commit(self) -> None:
        self.rankPicked.emit(self._slider.value())


class PowersSection(TitledSection):
    """Powers section: launches the Power Constructor and lists saved powers as a tree."""

    # A build change (add/remove/edit a power, group, re-cost) — marks the sheet dirty.
    changed = Signal()
    # A runtime on/off toggle. It updates the live sheet numbers (a trait boost drops
    # in or out) but is *not* part of the point build, so it costs nothing and does not
    # re-derive the block itself — the sheet wires it to the same refreshes as
    # ``changed`` minus FACTS_CHANGED. It *is* saved with the character, though (a
    # Growth held at Large reopens at Large), so it does carry the dirty flag.
    runtimeChanged = Signal()
    #: A card's roll line was right-clicked and pinned — carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`. Only ever raised on a sheet a
    #: GM opened from a card (see :meth:`set_pin_target`).
    pinRequested = Signal(object)
    #: The same, for a line that was already on the card.
    unpinRequested = Signal(object)

    #: A card's 🎲 was pressed — roll that line. Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`. Neither a build change nor a
    #: runtime one: rolling a power changes nothing about the power.
    rollRequested = Signal(object)

    #: How long a card takes to ease between its live and switched-off looks. A class
    #: attribute so tests can zero it and assert on the resting state without waiting
    #: on a timer.
    TRANSITION_MS = 180

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
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        # Per node id, how switched-off that node's card currently *looks* (0 live, 1
        # fully off). Survives the card teardown a toggle triggers, so the replacement
        # card can ease on from where its predecessor was — see _show_activation.
        self._card_off: dict[str, float] = {}
        self._card_off_prev: dict[str, float] = {}
        # Keep constructor windows referenced so Qt doesn't garbage-collect them the
        # moment the click handler returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)
        self._empty = QLabel("No powers yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        # The saved powers stack above the Add button, one card each; the top-level
        # list is the root of the drag-and-drop tree.
        self._list_host = NodeList("")
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
        if target is None:  # defensive: the target should still be findable here
            src_list.insert(src_index, source_node)  # put it back where it was
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
        """Rebuild the whole card tree from the model, toggling the empty label.

        Every runtime setter ends here — flipping one power can restate another card's
        numbers — so this runs on a plain mid-play click, and the block is momentarily
        empty while it does. :func:`~mm_companion.ui.widgets.preserved_scroll` is what
        stops that shrinking the page out from under the card just clicked.
        """
        with preserved_scroll(self):
            self._normalize_arrays()  # a valid active member per array before drawing
            # Hand the on-screen progress over to the cards about to be built, and start
            # a fresh map — so a power that was removed or ungrouped leaves nothing
            # behind for a later node to inherit.
            self._card_off_prev, self._card_off = self._card_off, {}
            self._list_host.clear()
            for node in self._character.powers:
                self._list_host.add_entry(node.id, self._render_node(node, None))
            self._empty.setVisible(not self._character.powers)
            self.set_priced_title("Powers", powers_points_spent(self._character, self._data))

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
        """A framed container: a mode title bar over its members, rendered indented.

        A group that owns a switch (a Linked group, or any group sitting inside an
        array) is clicked on its title bar the same way a leaf card is clicked on its
        body — and dims as a whole, members included, when it is switched off.
        """
        card = DraggableCard(group.id, group=True)
        self._arm_activation(card, group, parent, interactive)
        layout = QVBoxLayout(card)
        layout.addWidget(self._group_header(group, card, parent))

        # A Linked group that is off forces its whole subtree off, so its members'
        # activation controls are disabled; other modes just pass interactivity down.
        child_interactive = interactive and (
            self._group_is_active(group) if group.mode == STRUCTURE_LINKED else True
        )
        inner = NodeList(group.id)
        inner.combineRequested.connect(self._on_combine)
        inner.moveRequested.connect(self._on_move)
        for child in group.children:
            inner.add_entry(child.id, self._render_node(child, group, child_interactive))
        indent = QWidget()
        indent_layout = QHBoxLayout(indent)
        indent_layout.setContentsMargins(14, 0, 0, 0)
        indent_layout.addWidget(inner)
        layout.addWidget(indent)
        self._show_activation(card, group, parent)
        return card

    def _group_header(
        self,
        group: PowerGroup,
        card: DraggableCard,
        parent: PowerGroup | None,
    ) -> QWidget:
        """The group's title bar: grip, name + rename, mode toggle, cost, ungroup.

        The bar carries no activation control of its own — it is a plain widget, so a
        click on it falls through to the group card, which is the switch.
        """
        header = GroupHeader()
        header.powerDropped.connect(lambda src, gid=group.id: self._on_combine(src, gid))
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)

        grip = DragHandle()
        grip.setToolTip("Drag to move this group, or drop a power here to group it with this one")
        grip.dragStarted.connect(card.start_drag)
        row.addWidget(grip)
        grip.setVisible(not self._locked)

        mode_label = _MODE_LABELS.get(group.mode, _MODE_LABELS[STRUCTURE_INDEPENDENT])
        label = QLabel(group.name or mode_label)
        label.setStyleSheet(BOLD_STYLE)
        row.addWidget(label)

        rename = QPushButton("✎")
        rename.setFixedWidth(24)
        rename.setToolTip("Rename this group")
        rename.clicked.connect(lambda _checked=False, g=group: self._rename_group(g))
        row.addWidget(rename)
        rename.setVisible(not self._locked)

        # Order matters: the lock keeps whichever segment is lit, so the mode has to
        # be set before it — see _ModeToggle.set_locked.
        toggle = _ModeToggle()
        toggle.set_mode(group.mode)
        toggle.modeChanged.connect(lambda mode, g=group: self._set_group_mode(g, mode))
        toggle.set_locked(self._locked)
        row.addWidget(toggle)

        row.addStretch()

        dynamic = self._dynamic_toggle(group, parent)
        if dynamic is not None:
            row.addWidget(dynamic)

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

    def _dynamic_toggle(self, node: PowerNode, parent: PowerGroup | None) -> QWidget | None:
        """A Dynamic switch for a member of an ``array`` group, or ``None``.

        The whole-card twin of the Power Constructor's per-effect switch, and the same
        question at the other level an array exists at: a Dynamic member shares the
        array's point pool and runs alongside the array's other Dynamic members instead
        of switching them off, and pays a dearer Alternate Effect for it (p101).

        Offered only inside a real array — a group of one has nothing to be an alternate
        *of* — and to leaf powers and nested groups alike, since either can be a member.
        Locked, it follows :class:`_ModeToggle` rather than the buttons around it: the
        flag stays readable and stops being a control, and the click falls through to
        the card, which is the array's member selector.
        """

        if parent is None or parent.mode != STRUCTURE_ARRAY or len(parent.children) < 2:
            return None
        box = QCheckBox("Dynamic")
        box.setChecked(node.dynamic)
        base = group_array_base_index(parent, self._data, self._character)
        if parent.children[base] is node:
            price = f"one Alternate Effect ({array_dynamic_primary_cost(self._data)} PP)"
        else:
            dear = array_alternate_cost(self._data, dynamic=True)
            price = f"{dear} PP instead of {array_alternate_cost(self._data)}"
        box.setToolTip(
            "Share this array's point pool with its other Dynamic members and run "
            f"alongside them at reduced effectiveness, rather than switching them off. "
            f"Costs {price}."
        )
        box.toggled.connect(lambda on, n=node: self._set_dynamic(n, on))
        if self._locked:
            box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return box

    def _set_dynamic(self, node: PowerNode, on: bool) -> None:
        """Mark an array member Dynamic (or not) and reprice the tree."""
        node.dynamic = on
        self._rebuild_list()
        self.changed.emit()

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

    # -- what a click on a card does --------------------------------------
    def _activation_role(self, node: PowerNode, parent: PowerGroup | None) -> str:
        """What clicking this node's card does: ``"select"``, ``"toggle"``, or ``""``.

        ``"select"`` — the node is a member of a multi-member *array* in which something
        stands on the sheet, so clicking it makes it the live alternate (its siblings
        drop). Both a standing member and an instant one behave this way; an all-instant
        array has nothing to keep active, so its members aren't clickable.

        ``"toggle"`` — the node carries its own on/off switch: a Linked *group* that is
        gateable and standing (its members switch as one), or a standalone power that
        has a runtime gate *and* a standing bonus.

        ``""`` — nothing to switch (a permanent ungated power, a pure-instant attack),
        or the switch belongs to an ancestor: anything under a Linked group is driven by
        that group's card, so its own card stays inert and lets the click bubble up.

        The Linked test looks at the whole ancestor chain, not just the immediate
        parent: a member may sit inside a sub-group (dragging one card onto another
        always makes an *Independent* group) that is itself inside the Linked group, and
        it still has to switch with its linked siblings rather than sprout its own switch.
        """
        if (
            isinstance(parent, PowerGroup)
            and parent.mode == STRUCTURE_ARRAY
            and len(parent.children) >= 2
            and any(self._node_has_standing(child) for child in parent.children)
        ):
            return "select"
        if self._linked_ancestor(node) is not None:
            return ""
        if isinstance(node, PowerGroup):
            if (
                node.mode == STRUCTURE_LINKED
                and self._node_is_gateable(node)
                and self._node_has_standing(node)
            ):
                return "toggle"
            return ""
        if power_runtime_gates(node, self._data) and power_has_standing_effect(node, self._data):
            return "toggle"
        return ""

    def _node_is_inactive(self, node: PowerNode, parent: PowerGroup | None, role: str) -> bool:
        """Whether the card should be drawn in its dimmed, switched-off state."""
        if role == "select":
            return active_array_child(parent) is not node
        if role == "toggle":
            if isinstance(node, PowerGroup):
                return not self._group_is_active(node)
            return not self._power_is_active(node)
        return False

    def _arm_activation(
        self,
        card: DraggableCard,
        node: PowerNode,
        parent: PowerGroup | None,
        interactive: bool,
    ) -> None:
        """Make the card its power's switch, if the power has one to offer.

        The card *is* the control — there is no separate checkbox — so the whole frame
        becomes the click target, and says so (see :meth:`DraggableCard.set_clickable`).
        ``interactive`` is ``False`` for a card inside a switched-off Linked group, which
        still shows its state but can't be clicked back on past its group.
        """
        role = self._activation_role(node, parent)
        if not (role and interactive):
            return
        card.set_clickable(True)
        card.setToolTip(_CLICK_HINTS[role])
        card.clicked.connect(lambda n=node, p=parent, r=role: self._on_card_clicked(n, p, r))

    def _show_activation(
        self, card: DraggableCard, node: PowerNode, parent: PowerGroup | None
    ) -> None:
        """Put the card into its on/off look, easing there when the state just changed.

        Every runtime setter ends in :meth:`_rebuild_list`, because flipping one power
        can change the numbers printed on *another* card (switching off an Enhanced
        Trait restates a Strength-Based Damage). So no card survives a toggle and there
        is nothing to animate across it. Instead the section remembers, per node id, the
        progress each card was last *showing* — and a freshly built card picks up from
        exactly there and eases to its new target. Because the running animation writes
        that progress back on every frame, a second click mid-transition resumes from
        where the eye left off rather than snapping.

        Called once the card's children exist, so the type scaling can see them.
        """
        role = self._activation_role(node, parent)
        target = 1.0 if self._node_is_inactive(node, parent, role) else 0.0
        previous = self._card_off_prev.get(node.id)
        self._card_off[node.id] = target
        if previous is None or previous == target or self.TRANSITION_MS <= 0:
            card.set_off_progress(target)
            return
        card.set_off_progress(previous)
        ease = QVariantAnimation(card)
        ease.setStartValue(previous)
        ease.setEndValue(target)
        ease.setDuration(self.TRANSITION_MS)
        ease.setEasingCurve(QEasingCurve.Type.OutCubic)
        ease.valueChanged.connect(lambda value, c=card, i=node.id: self._on_ease(c, i, value))
        ease.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_ease(self, card: DraggableCard, node_id: str, progress: float) -> None:
        card.set_off_progress(progress)
        self._card_off[node_id] = progress

    def _on_card_clicked(self, node: PowerNode, parent: PowerGroup | None, role: str) -> None:
        """Apply a card click: select an array's live alternate, or flip a switch."""
        if role == "select":
            if isinstance(parent, PowerGroup) and active_array_child(parent) is not node:
                # An array always keeps exactly one live member, so clicking the current
                # one is a no-op rather than switching the whole array off.
                self._set_array_active(parent, node.id)
        elif role == "toggle":
            if isinstance(node, PowerGroup):
                self._set_group_active(node, not self._group_is_active(node))
            else:
                self._set_power_active(node, not self._power_is_active(node))

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

        Each effect carries its full game-term breakdown inline (the same data the Power
        Constructor shows while building), rendered quietly under the effect's name so
        the derived system values are always on the page, never behind a hover.

        The card body doubles as the power's on/off switch — see
        :meth:`_arm_activation` and :meth:`_show_activation`.
        """
        card = DraggableCard(power.id)
        self._arm_activation(card, power, parent, interactive)
        layout = QVBoxLayout(card)
        layout.addWidget(self._header_row(power, card, parent))

        if power.description:
            desc = QLabel(power.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(muted_style(italic=True))
            layout.addWidget(desc)

        effects = self._effects_block(power)
        if effects is not None:
            layout.addWidget(effects)

        # A dialled effect is a range, not a switch: a slider over the ranks the wielder
        # can hold it at, under the effect breakdown that explains what each notch is
        # worth and above the dice, with the rest of the mid-play controls.
        for dial in self._rank_dials(power, parent, interactive):
            layout.addWidget(dial)

        # A dedicated footer for the numbers that come up mid-play — one line per roll.
        # A power that rolls nothing gets neither the footer nor its rule.
        rolls = self._rolls_block(power)
        if rolls is not None:
            layout.addWidget(hline_separator())
            layout.addWidget(rolls)
        self._show_activation(card, power, parent)
        return card

    def _header_row(
        self,
        power: Power,
        card: DraggableCard,
        parent: PowerGroup | None,
    ) -> QWidget:
        """Name + PL warning on the left; the cost and edit/remove chrome on the right,
        led by a drag grip (hidden when locked).

        Returns a host widget (not a bare layout) so every child has a parent the
        moment it is created. Calling ``setVisible(True)`` on a *parentless* widget
        shows it as a momentary top-level window — on Windows that flashes a small
        window on screen and is slow to realize; the edit/remove buttons hit exactly
        that path, so the header must own them before their visibility is set.
        """
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        grip = DragHandle()
        grip.dragStarted.connect(card.start_drag)
        layout.addWidget(grip)
        grip.setVisible(not self._locked)

        name = QLabel(power_display_name(power, self._data))
        # The size goes on the QFont, not into the stylesheet: a stylesheet font-size
        # would outrank the card's own font and so sit out the switched-off transition.
        font = name.font()
        font.setPointSizeF(theme.font_size("size.card-name"))
        # A Debilitated condition naming this power loses it — strike the header through
        # and redden it (display-only; the power's point cost is untouched).
        if power.name and power.name in debilitated_traits(self._character, self._data):
            name.setStyleSheet(tinted_style("tint.worse"))
            font.setStrikeOut(True)
            name.setToolTip("Debilitated — this power is effectively lost")
        else:
            name.setStyleSheet(BOLD_STYLE)
        name.setFont(font)
        layout.addWidget(name)

        # A power that breaks a PL cap carries a warning marker naming the breach;
        # enforcement is a warning for now (see storage.pl_enforcement).
        violations = power_pl_violations(power, self._character, self._data)
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet(tinted_style("tint.warning"))
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)

        # A homerule power (one carrying a Dev-mode override or a blank Custom modifier)
        # is badged so a bent value on the sheet is never mistaken for a by-the-book one.
        if power_is_homerule(power) or power_has_custom_modifier(power, self._data):
            homerule = QLabel("⌂")
            homerule.setStyleSheet(tinted_style("tint.homerule"))
            homerule.setToolTip(
                "Homerule power — carries manual (Dev-mode) overrides or a custom modifier."
            )
            layout.addWidget(homerule)
        layout.addStretch()

        dynamic = self._dynamic_toggle(power, parent)
        if dynamic is not None:
            layout.addWidget(dynamic)

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

    # -- the rank dial ----------------------------------------------------
    def _rank_dials(
        self, power: Power, parent: PowerGroup | None, interactive: bool
    ) -> list[QWidget]:
        """One :class:`_RankDial` per effect that has ranks worth choosing between.

        Two ways in, and the block names neither Growth nor Damage. A **size** effect
        earns one because the ruleset gave it a size readout
        (:func:`~mm_companion.core.rules.size_steps`), so a mod's own size effect gets a
        dial without touching this file; any other effect earns one because the player
        ticked *Add a rank slider* in the constructor's Extended settings.

        A single-rank effect gets nothing either way: dialling a Growth 1 is exactly the
        card's own on/off switch, and a second way to press it is not a choice.

        The caption names the *effect* only when the power has more than one, which is
        the same bargain the dice footer's labels strike: on the ordinary single-effect
        Growth card "Size" is the whole story, while a Growth linked to a Shrinking needs
        to say which dial is which.
        """
        dials: list[QWidget] = []
        for effect in power.effects:
            steps = size_steps(power, effect, self._character, self._data)
            sized = len(steps) >= 2
            if not sized and not (effect.rank_dial and effect.rank > 1):
                continue
            caption = "Size" if sized else "Rank"
            if len(power.effects) > 1:
                caption = effect_title(effect, self._character, self._data)
            # Where the handle sits. A *size* effect is positioned by whether it is
            # standing on the sheet — a Growth that is switched off is nowhere, not at
            # rank 1. An instant effect (a Damage) never "stands" at all, so asking the
            # same question would peg every blast card at Off; what matters there is
            # simply whether the card is switched on.
            standing = (
                effect_stands(power, effect, self._data, self._character)
                if sized
                else self._power_is_active(power)
                and self._character is not None
                and any(p is power for p in live_powers(self._character.powers))
            )
            dial = _RankDial(
                caption,
                effect.rank,
                effect_current_rank(effect) if standing else 0,
                self._dial_labels(steps),
                interactive,
            )
            dial.rankPicked.connect(
                lambda rank, p=power, e=effect, g=parent: self._on_rank_dialled(p, e, g, rank)
            )
            dials.append(dial)
        return dials

    @staticmethod
    def _dial_labels(steps) -> dict[int, str]:
        """What each notch of the dial *is*, where a plain rank number won't do.

        A size effect's notches are named by the size the wielder becomes, one entry per
        rank rather than per rung: the Size Table clamps several ranks into one category
        near its ends, and a dial that skipped them would refuse to stop where the
        player put it. Zero always reads "Off"; anything the map doesn't cover falls back
        to its bare rank.
        """
        labels = {0: "Off"}
        for step in steps:
            for rank in range(step.rank, step.last_rank + 1):
                labels[rank] = step.category
        return labels

    def _on_rank_dialled(
        self, power: Power, effect: PowerEffectInstance, parent: PowerGroup | None, rank: int
    ) -> None:
        """Hold an effect at *rank* — or, at zero, switch the power off.

        The dial does exactly what a click on the card would have done, and then lands on
        the notch asked for. So moving it on a dormant power is a request to *be* that
        rank: it wakes the power (flipping its switches, or becoming its array's live
        alternate) at the chosen notch rather than at full rank. And sliding back to zero
        is the card's own click again — off it goes, which is what makes the dial a whole
        control rather than one that can only ever turn a power on. The one exception is
        an array's live member, where clicking the card is deliberately a no-op: an array
        always keeps exactly one member live, and dialling one to zero must not switch
        the whole array off.

        The rank is written before any activation path, so whichever one runs rebuilds
        the cards with it already in place.
        """
        role = self._activation_role(power, parent)
        if rank <= 0:
            if role != "select":
                self._set_power_active(power, False)  # rebuilds and emits
            else:
                self._rebuild_list()  # put the handle back where the array left it
            return
        effect.current_rank = rank
        if role == "select" and isinstance(parent, PowerGroup):
            if active_array_child(parent) is not power:
                self._set_array_active(parent, power.id)  # rebuilds and emits
                return
        if not self._power_is_active(power):
            self._set_power_active(power, True)  # rebuilds and emits
            return
        self._rebuild_list()
        self.runtimeChanged.emit()

    # -- effect summary and dice footer -----------------------------------
    def _effects_block(self, power: Power) -> QWidget | None:
        """The per-effect summary — each effect's extras/flaws beside its game terms.

        Drawn by :mod:`mm_companion.ui.cards.effects`, which the Equipment block
        renders its items with too: an item wraps a real :class:`Power`, so both
        cards show the same breakdown from the same code.
        """
        return effects_block(power, self._character, self._data)

    def _rolls_block(self, power: Power) -> QWidget | None:
        """The card's dice footer, one line per roll; ``None`` when nothing is rolled.

        A power that resolves without dice — a Protection, a permanent Enhanced Trait —
        gets no footer at all rather than a line saying so: the absence *is* the answer,
        and a placeholder on every passive power is pure noise.

        A pin names a roll by *which entry of* :func:`~mm_companion.core.rules.
        power_rolls` it is, so the footer is handed the power's id and turns a line
        index into a :class:`~mm_companion.core.rules.pins.PinRef` — including for
        the resistance line, which the wielder never rolls but which is the number a
        GM most wants on a mook's card.
        """
        specs = self._rolls(power)
        if not specs:
            return None
        footer = RollsFooter(
            specs,
            pins=self._pins,
            pin_ref=lambda index, pid=power.id: PinRef(PIN_POWER, pid, index),
        )
        footer.rollRequested.connect(self.rollRequested)
        footer.pinRequested.connect(self.pinRequested)
        footer.unpinRequested.connect(self.unpinRequested)
        return footer

    def set_pin_target(self, enabled: bool) -> None:
        """Whether a card's roll lines offer to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so a line can offer Unpin."""
        self._pins.set_pinned(refs)

    def _rolls(self, power: Power):
        """Every roll the power calls for, as specs; see :func:`~mm_companion.core.
        rules.power_rolls`."""
        return power_rolls(power, self._character, self._data)

    def _rolls_lines(self, power: Power) -> list[str]:
        """One entry per die roll the power calls for; see module-level
        :func:`roll_lines`."""
        return roll_lines(power, self._character, self._data)

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

        Just ``[power]`` unless it sits somewhere under a Linked group, in which case
        all leaf powers under that group activate as one — including through any
        intervening sub-group, and out to the *outermost* Linked group when they nest.
        """
        linked = self._linked_ancestor(power)
        return self._leaf_powers(linked) if linked is not None else [power]

    def _ancestor_groups(self, node_id: str) -> list[PowerGroup]:
        """Every group enclosing *node_id*, outermost first (empty at the top level)."""

        def walk(nodes: list[PowerNode], trail: list[PowerGroup]) -> list[PowerGroup] | None:
            for node in nodes:
                if node.id == node_id:
                    return trail
                if isinstance(node, PowerGroup):
                    found = walk(node.children, [*trail, node])
                    if found is not None:
                        return found
            return None

        return walk(self._character.powers, []) or []

    def _linked_ancestor(self, node: PowerNode) -> PowerGroup | None:
        """The outermost Linked group enclosing *node*, or ``None``.

        Outermost rather than nearest: nested Linked groups all switch as one, so the
        activation set is the widest of them.
        """
        return next((g for g in self._ancestor_groups(node.id) if g.mode == STRUCTURE_LINKED), None)

    @staticmethod
    def _leaf_powers(node: PowerNode) -> list[Power]:
        """This node's leaves — one node's worth of :func:`leaf_powers`."""
        return list(leaf_powers([node]))

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points (Add / Remove / group chrome)."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        self._rebuild_list()
