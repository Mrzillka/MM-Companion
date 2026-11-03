"""Section 4: powers.

The most complex part of a character. An "Add Power" button opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` brick-builder in
its own window; saving there hands the finished
:class:`~mm_companion.core.powers.Power` back through
:attr:`~mm_companion.ui.power_constructor.PowerConstructorWindow.powerSaved`, which
this section appends to the shared :class:`~mm_companion.core.character.Character`
and shows as a *card*. Each card reads like a stat-block entry: a header (name,
cost, a ⚠ marker when the power breaks a build rule), the description, a per-effect
summary with each effect's modifiers and game terms, and, for a power that rolls, a
dice footer with one roll per line. ✎ reopens the constructor on a deep copy that
replaces the original on save; ✕ removes it.

**The card is the on/off switch.** Clicking a card's body toggles a runtime-gated
power (or makes an array member the live alternate). A switched-off power stays
readable but recedes, dimmed and a notch smaller, and flipping one *eases* between the
two looks. A clickable card says so before you touch it: an accent edge down its left
side, and an accent border under the pointer on the innermost card a click would
reach. The grip, ✎ and ✕ consume their own clicks.

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
``changed`` signal, ``set_locked``). Its parts are mixins in
:mod:`mm_companion.ui.sections.power_parts`.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
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
    PowerGroup,
    PowerNode,
    power_is_homerule,
    power_is_stunt,
)
from mm_companion.core.rules import (
    PIN_POWER,
    PinRef,
    counter_rolls,
    debilitated_traits,
    effect_counts_uses,
    group_scope_note,
    invalidate_build_cache,
    node_cost_formula,
    node_display_cost,
    power_display_name,
    power_has_custom_modifier,
    power_has_standing_effect,
    power_offers_area,
    power_roll_lines,
    power_rolls_with_area,
    power_runtime_gates,
    power_total_cost,
    power_violations,
    powers_points_spent,
    pushable_effects,
    stunt_source,
)
from mm_companion.ui import theme
from mm_companion.ui.cards import (
    DraggableCard,
    DragHandle,
    GroupHeader,
    NodeList,
    RollLine,
    RollsFooter,
    effects_block,
    simple_effects_block,
)
from mm_companion.ui.extra_effort import add_power_effort_actions
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.power_constructor.canvas import MODE_ARRAY_DYNAMIC
from mm_companion.ui.sections.power_parts.activation import PowersActivationMixin
from mm_companion.ui.sections.power_parts.dials import PowersDialsMixin
from mm_companion.ui.sections.power_parts.helpers import _group_mode
from mm_companion.ui.sections.power_parts.shares import PowersSharesMixin
from mm_companion.ui.sections.power_parts.tree import PowersTreeMixin
from mm_companion.ui.sections.power_parts.uses import PowersUsesMixin
from mm_companion.ui.sections.power_parts.widgets import _ModeToggle, _SplitGroup
from mm_companion.ui.sections.stat_table import PinMenuState
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.simple.style import term_label
from mm_companion.ui.widgets import (
    BOLD_STYLE,
    ElidingLabel,
    HeldRebuild,
    help_markup,
    hline_separator,
    muted_style,
    rebuilding,
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


# What each group mode is called on its title bar.
_MODE_LABELS = {
    STRUCTURE_INDEPENDENT: "Group of powers",
    STRUCTURE_ARRAY: "Group of alternate effects",
    MODE_ARRAY_DYNAMIC: "Group of dynamic alternate effects",
    STRUCTURE_LINKED: "Group of linked powers",
}

#: The same four modes as one word each — the simple sheet's small print beside a
#: group's name, where the mode is a fact to read rather than a switch to throw.
_SIMPLE_MODE_NAMES = {
    STRUCTURE_INDEPENDENT: "",
    STRUCTURE_ARRAY: "array",
    MODE_ARRAY_DYNAMIC: "dynamic array",
    STRUCTURE_LINKED: "linked",
}


def roll_lines(power: Power, character: Character, data: GameData) -> list[str]:
    """One entry per die roll a power calls for; effect-prefixed for a multi-effect power.

    Module-level so both the power card's dice footer and the GM's NPC hover summary
    read the same lines. The lines are the labels of :func:`~mm_companion.core.rules.
    power_rolls`, which is also what the card's 🎲 buttons roll — so what is written
    and what is rolled can never drift apart.
    """

    return power_roll_lines(power, character, data)


class PowersSection(
    PowersTreeMixin,
    PowersUsesMixin,
    PowersActivationMixin,
    PowersSharesMixin,
    PowersDialsMixin,
    HeldRebuild,
    TitledSection,
):
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
    #: A card's area line was pressed — use that power on an area. Carries the power's
    #: id; it is no roll, so it never travels as one (see ``bus.AREA_REQUESTED``).
    areaRequested = Signal(str)
    #: ``(RollSpec, SceneTarget)`` — an attack line aimed at a creature on the Scene
    #: (see ``ui/attack_targets.py``); served by whoever rolls (``bus.ATTACK_REQUESTED``).
    targetRequested = Signal(object)

    #: A sentence for the roll history — what a use of Extra Effort bought and what it
    #: cost. Carries the text, like the System block's own note.
    noteRequested = Signal(str)
    #: Extra Effort was shrugged off with a Determination heroic feat, which costs a Hero
    #: Point (p22). Carries the delta; the System block owns the pips and moves them.
    heroPointRequested = Signal(int)
    #: Extra Effort's fatigue was applied to the character. The same fan-out the
    #: Conditions block's own signal drives — the model changed, and every view over a
    #: condition restates itself.
    conditionsChanged = Signal()

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
        # On the simple sheet (see set_simple): the same cards, told more quietly.
        self._simple = False
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        # Per node id, how switched-off that node's card currently *looks* (0 live, 1
        # fully off). Survives the card teardown a toggle triggers, so the replacement
        # card can ease on from where its predecessor was — see _show_activation.
        self._card_off: dict[str, float] = {}
        # Per-array share-dial coordinators, keyed by group id; see :class:`_SplitGroup`,
        # and the header labels waiting to be handed to them.
        self._splits: dict[str, _SplitGroup] = {}
        self._pool_labels: dict[str, QLabel] = {}
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
        # The constructor is a window, not a block, so it cannot reach the roller
        # itself; its Improvise panel asks and this section hands the request on the
        # same way its own cards do.
        window.rollRequested.connect(self.rollRequested)
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
        window.rollRequested.connect(self.rollRequested)
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
        empty while it does. :func:`~mm_companion.ui.widgets.rebuilding` is what
        stops that shrinking the page out from under the card just clicked.
        """
        if self.rebuild_held():
            return
        with rebuilding(self):
            self._normalize_arrays()  # a valid active member per array before drawing
            # The one refresh handler that *writes* the model (an array's active
            # member decides whose boosts stand), so it owes the build scope a fresh
            # gather (see :mod:`mm_companion.core.rules.build_cache`). Insurance: today
            # nothing is memoized yet when this runs, but that depends on how the bus
            # scopes a handler and on this staying first.
            invalidate_build_cache()
            # Hand the on-screen progress over to the cards about to be built, and start
            # a fresh map — so a power that was removed or ungrouped leaves nothing
            # behind for a later node to inherit.
            self._card_off_prev, self._card_off = self._card_off, {}
            # One coordinator per array holding a split, rebuilt with the cards it
            # joins: a dial from a torn-down card must never be restated.
            self._splits = {}
            self._pool_labels = {}
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
        card.set_compact(self._simple)
        self._arm_activation(card, group, parent, interactive)
        layout = QVBoxLayout(card)
        layout.addWidget(self._group_header(group, card, parent))

        # A Linked group that is off forces its whole subtree off, so its members'
        # activation controls are disabled; other modes just pass interactivity down.
        child_interactive = interactive and (
            self._group_is_active(group) if group.mode == STRUCTURE_LINKED else True
        )
        # A group refuses a stunt outright, and shows the refusal rather than accepting
        # the drop and quietly doing nothing (see :meth:`_groupable`).
        inner = NodeList(group.id, accepts=self._groupable)
        inner.combineRequested.connect(self._on_combine)
        inner.moveRequested.connect(self._on_move)
        for child in group.children:
            inner.add_entry(child.id, self._render_node(child, group, child_interactive))
        # Now that the members' dials exist, give the header's readout to the thing
        # that counts it down while one of them is moving.
        split = self._splits.get(group.id)
        readout = self._pool_labels.get(group.id)
        if split is not None and readout is not None:
            split.set_readout(readout)
            split.restate()
        indent = QWidget()
        indent_layout = QHBoxLayout(indent)
        indent_layout.setContentsMargins(int(theme.metric("space.indent")), 0, 0, 0)
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

        mode_label = _MODE_LABELS.get(_group_mode(group), _MODE_LABELS[STRUCTURE_INDEPENDENT])
        label = QLabel(group.name or mode_label)
        label.setStyleSheet(BOLD_STYLE)
        row.addWidget(label)

        rename = QPushButton("✎")
        rename.setFixedWidth(int(theme.metric("column.chip-button")))
        rename.setToolTip("Rename this group")
        rename.clicked.connect(lambda _checked=False, g=group: self._rename_group(g))
        row.addWidget(rename)
        rename.setVisible(not self._locked)

        if self._simple:
            # The mode is a fact about the build; on the simple sheet it is small print
            # beside the name rather than a strip of segments — and an unnamed group's
            # "Group of dynamic alternate effects" would only say it twice.
            mode = _SIMPLE_MODE_NAMES.get(_group_mode(group), "")
            if mode and not group.name:
                label.setText(mode.capitalize())
            else:
                row.addWidget(term_label(mode, wrap=False))
        else:
            # Order matters: the lock keeps whichever segment is lit, so the mode has to
            # be set before it — see _ModeToggle.set_locked.
            toggle = _ModeToggle()
            toggle.set_mode(_group_mode(group))
            toggle.modeChanged.connect(lambda mode, g=group: self._set_group_mode(g, mode))
            toggle.set_locked(self._locked)
            row.addWidget(toggle)

        row.addStretch()

        split = self._pool_readout(group)
        if split is not None:
            row.addWidget(split)
        # Stays in a locked sheet, unlike the rename and ungroup buttons either side of
        # it: handing the pool back is the same free action the share dials are, and
        # those are live locked too (:class:`_RankDial`).
        release = self._pool_release(group)
        if release is not None:
            row.addWidget(release)
        if split is not None or release is not None:
            # The pool is a sentence and the cost beside it is a number; without a gap
            # they read as one phrase ("not split 15 PP").
            row.addSpacing(int(theme.metric("space.lg")))

        cost = QLabel(f"{node_display_cost(group, parent, self._data, self._character)} PP")
        cost.setEnabled(False)
        self._explain_cost(cost, group)
        row.addWidget(cost)
        cost.setVisible(not self._simple)

        ungroup = QPushButton("✕")
        ungroup.setFixedWidth(int(theme.metric("column.chip-button")))
        ungroup.setToolTip("Ungroup — dissolve this group, keeping its powers")
        ungroup.clicked.connect(lambda _checked=False, g=group: self._ungroup(g))
        row.addWidget(ungroup)
        ungroup.setVisible(not self._locked)
        return header

    def _explain_cost(self, label: QLabel, node: PowerNode) -> None:
        """Put the working behind a node's price on its cost label, when there is any.

        The constructor prints the same working beside its total, in full — there is
        room on a line of its own. Here there is not: a card's header already carries a
        name, up to three badges, a pool readout and three buttons, and a group's carries
        a mode toggle and a hand-back button as well. So the arithmetic goes on the
        tooltip of the one thing it explains, which is where a player asking "why does
        this say 23 when the cards say 10, 16 and 20" is already pointing.
        """

        parts = [node_cost_formula(node, self._data, self._character)]
        if isinstance(node, PowerGroup):
            # Not a warning: three genuinely separate removable devices really are
            # charged three times, and nothing can tell that build from one device split
            # across three cards. It states the arithmetic and lets the player decide.
            parts.append(group_scope_note(node, self._data, self._character))
        tooltip = "\n".join(part for part in parts if part)
        if tooltip:
            label.setToolTip(tooltip)

    def _arm_card_menu(self, card: DraggableCard, power: Power) -> None:
        """Arm the card's right-click menu — Extra Effort, then this power's counters.

        Both are things a power can be *used for*, so neither is in the dice footer:
        there they gave every attack and weapon a die button for a case the GM has to
        approve first. A card with nothing to offer gets no menu.
        """

        if (
            not counter_rolls(power, self._character, self._data)
            and not pushable_effects(power, self._data)
            and not self._offers_area_switch(power)
            and not self._counted(power)
        ):
            return
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos, c=card, p=power: self._show_card_menu(c, p, pos)
        )

    def card_menu(self, card: DraggableCard, power: Power) -> QMenu:
        """The card's whole menu, built but not shown.

        Split from :meth:`_show_card_menu` so the wiring can be checked without ``exec``
        — a modal menu headless is a test that hangs rather than a test that passes.
        """

        menu = QMenu(card)
        offered = add_power_effort_actions(
            menu, power, self._character, self._data, self.use_extra_effort
        )
        if any(effect.extra_effort for effect in power.effects):
            clear = menu.addAction("Clear Extra Effort")
            clear.setToolTip(
                "Extra Effort lasts until the end of your turn, and the app doesn't track turns. "
                "Take the ranks back when it is over."
            )
            clear.triggered.connect(lambda _checked=False, p=power: self.clear_extra_effort(p))
            offered = True
        counted = self._counted(power)
        if counted:
            if offered:
                menu.addSeparator()
            use = menu.addAction("Use once")
            use.setToolTip(
                "Count one use of its Short-Term or Fades effect without rolling (an always-on "
                "Protection that fades as it is hit, say). Rolling from the card counts a use by "
                "itself."
            )
            use.triggered.connect(
                lambda _checked=False, p=power, i=tuple(counted): self.spend_uses(p, i)
            )
            recharge = menu.addAction("Recharge")
            recharge.setEnabled(any(power.effects[i].uses for i in counted))
            recharge.setToolTip(
                "Restore the uses: recharging, resting, repairing or reloading. That usually "
                "takes at least an hour, outside action time."
            )
            recharge.triggered.connect(lambda _checked=False, p=power: self.recharge(p))
            offered = True
        specs = counter_rolls(power, self._character, self._data)
        if specs and offered:
            menu.addSeparator()
        self.fill_counter_menu(menu, specs)
        offered = offered or bool(specs)
        if self._offers_area_switch(power):
            if offered:
                menu.addSeparator()
            area = menu.addAction("Use as an area attack")
            area.setCheckable(True)
            area.setChecked(power.area_use)
            area.setToolTip(
                help_markup(
                    "**Area use:** use this attack on an area without buying the Area extra. It's "
                    "the GM's call (an attacker much larger than its target, say). The area's "
                    "rank is capped at your Power Level."
                )
            )
            area.toggled.connect(lambda on, p=power: self.set_area_use(p, on))
        return menu

    def set_npc_mode(self, npc: bool) -> None:
        """As for any priced block, and redraw the cards: an NPC's free area use is
        capped at its *estimated* Power Level, so the area line's rank may move."""
        changed = npc != self._npc
        super().set_npc_mode(npc)
        if changed:
            self._rebuild_list()

    def _counted(self, power: Power) -> list[int]:
        """The indices of *power*'s effects that count their uses (Short-Term, Fades)."""

        return [
            index
            for index, effect in enumerate(power.effects)
            if effect_counts_uses(effect, self._data)
        ]

    def _offers_area_switch(self, power: Power) -> bool:
        """Whether the card offers the free area use — an attack, or one already switched on."""
        return power.area_use or power_offers_area(power, self._data)

    def set_area_use(self, power: Power, on: bool) -> None:
        """Switch the free area use on or off — runtime state, priced at nothing.

        It rides the runtime signal a card toggle does, so it is saved with the sheet and
        is an undoable step, and the card is redrawn with (or without) its area line.
        """
        if power.area_use == on:
            return
        power.area_use = on
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _show_card_menu(self, card: DraggableCard, power: Power, pos) -> None:
        self.card_menu(card, power).exec(card.mapToGlobal(pos))

    def _rename_group(self, group: PowerGroup) -> None:
        """Prompt for a new group name; blank clears it back to the mode label."""
        placeholder = _MODE_LABELS.get(_group_mode(group), _MODE_LABELS[STRUCTURE_INDEPENDENT])
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
        """Put a group on one of the toggle's four segments.

        *Dynamic array* is a view rather than a stored mode (:func:`_group_mode`), so it
        is written as an array whose every member carries the flag, and picking plain
        *Array* is how it is taken back off. The cost math is untouched by that: it
        still prices Dynamic per member, which is what the rules do.
        """

        dynamic = mode == MODE_ARRAY_DYNAMIC
        group.mode = STRUCTURE_ARRAY if dynamic else mode
        for child in group.children:
            child.dynamic = dynamic
            if not dynamic:
                child.dynamic_points = None
        self._normalize_arrays()
        self._rebuild_list()
        self.changed.emit()

    def _node_has_standing(self, node: PowerNode) -> bool:
        """Whether any leaf under *node* contributes a standing (non-instant) bonus."""
        return any(power_has_standing_effect(p, self._data) for p in self._leaf_powers(node))

    def _node_is_gateable(self, node: PowerNode) -> bool:
        """Whether any leaf under *node* carries a runtime gate (so it can be turned off)."""
        return any(power_runtime_gates(p, self._data) for p in self._leaf_powers(node))

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
        card.set_compact(self._simple)
        self._arm_activation(card, power, parent, interactive)
        self._arm_card_menu(card, power)
        layout = QVBoxLayout(card)
        layout.addWidget(self._header_row(power, card, parent))

        if power.description:
            if self._simple:
                desc = term_label(power.description)
            else:
                desc = QLabel(power.description)
                desc.setWordWrap(True)
                desc.setStyleSheet(muted_style(italic=True))
            layout.addWidget(desc)

        effects = self._effects_block(power)
        if effects is not None:
            layout.addWidget(effects)

        # Under the breakdown that names the effects, above the dials that turn one of
        # them up: which of an array's effects is running is the choice you make first.
        selector = self._effect_selector(power, interactive)
        if selector is not None:
            layout.addWidget(selector)

        # A Dynamic member's share of its array's pool, made on the same dial a rank
        # is: the share and the rank it buys are one gesture rather than a number typed
        # into a dialog and a rank worked out afterwards.
        share = self._share_dial(power, parent, interactive)
        if share is not None:
            layout.addWidget(share)
            if interactive:
                card.keep_lit(share)
        for dial in self._effect_share_dials(power, interactive):
            layout.addWidget(dial)
            if interactive:
                card.keep_lit(dial)
        # ...and now that they have registered, let the coordinator drive the header's
        # readout, the same handover :meth:`_make_group_card` makes one level up.
        effect_split = self._splits.get(power.id)
        effect_readout = self._pool_labels.get(power.id)
        if effect_split is not None and effect_readout is not None:
            effect_split.set_readout(effect_readout)
            effect_split.restate()

        # A dial over the ranks the wielder can hold an effect at, under the effect
        # breakdown and above the dice. It stays at full strength while the card
        # recedes, since picking a pip wakes the power (:meth:`DraggableCard.keep_lit`)
        # — unless it sits inside a switched-off Linked group, where it is inert.
        for dial in self._rank_dials(power, parent, interactive, shared=share is not None):
            layout.addWidget(dial)
            if interactive:
                card.keep_lit(dial)

        # A dedicated footer for the numbers that come up mid-play — one line per roll.
        # A power that rolls nothing gets neither the footer nor its rule.
        rolls = self._rolls_block(power)
        if rolls is not None:
            if not self._simple:
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

        # Eliding: a plain QLabel reports its whole text as a minimum, which held the
        # card open at the power's name and clipped its buttons off in a narrow
        # block. The full name is on hover.
        name = ElidingLabel(power_display_name(power, self._data))
        # The size goes on the QFont, not into the stylesheet: a stylesheet font-size
        # would outrank the card's own font and so sit out the switched-off transition.
        font = name.font()
        font.setPointSizeF(theme.font_size("size.card-name"))
        # A Debilitated condition naming this power loses it — strike the header through
        # and redden it (display-only; the power's point cost is untouched).
        if power.name and power.name in debilitated_traits(self._character, self._data):
            name.setStyleSheet(tinted_style("tint.worse"))
            font.setStrikeOut(True)
            name.set_hover_text("Debilitated — this power is effectively lost")
        else:
            name.setStyleSheet(BOLD_STYLE)
        name.setFont(font)
        layout.addWidget(name)
        card.set_hint_target(name)

        # A stunt says so, and says what it is a stunt *of*: it is a card of its own
        # rather than a member of the source power's array, so the relationship is only
        # ever visible here. Muted, because it is provenance rather than a number.
        if power_is_stunt(power):
            source = stunt_source(power, self._character)
            of = power_display_name(source, self._data) if source else "a power now gone"
            badge = QLabel(f"✦ stunt of {of}")
            badge.setStyleSheet(muted_style(italic=True))
            badge.setToolTip(
                help_markup(
                    "**Power stunt:** a temporary alternate effect paid for with Extra Effort "
                    "instead of Power Points. It costs nothing, isn't saved with the character, "
                    "and lasts until the end of the scene."
                )
            )
            layout.addWidget(badge)

        # A power that breaks any build rule carries a marker naming every breach
        # (a warning for now: storage.pl_enforcement). The same walk over POWER_CHECKS
        # the Power Constructor's warning band makes, so the two cannot disagree.
        # Both markers are about the build, so the simple sheet leaves them out.
        violations = [] if self._simple else power_violations(power, self._character, self._data)
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet(tinted_style("tint.warning"))
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)

        # A homerule power (one carrying a Dev-mode override or a blank Custom modifier)
        # is badged so a bent value on the sheet is never mistaken for a by-the-book one.
        if not self._simple and (
            power_is_homerule(power) or power_has_custom_modifier(power, self._data)
        ):
            homerule = QLabel("⌂")
            homerule.setStyleSheet(tinted_style("tint.homerule"))
            homerule.setToolTip(
                "Homerule power — carries manual (Edit by hand) overrides or a custom modifier."
            )
            layout.addWidget(homerule)
        layout.addStretch()

        # The effect-level twin of a group header's pool line. An array exists at two
        # levels and so does its pool, but only the group level ever said so: a power
        # whose own effects were split coordinated their dials live and stated the
        # pool nowhere, which is the one number no single dial can show.
        pool = self._effect_pool_readout(power)
        if pool is not None:
            layout.addWidget(pool)
        release = self._effect_pool_release(power)
        if release is not None:
            layout.addWidget(release)
        if pool is not None or release is not None:
            layout.addSpacing(int(theme.metric("space.lg")))

        # Inside an array group a non-base member contributes only its flat pooled cost;
        # every other card shows its full assembled cost (node_display_cost decides). A
        # stunt contributes nothing, and "0 PP" beside a real build reads as a bug — so it
        # says what it is instead, and keeps the number it *would* have cost in the
        # tooltip, since that is the number its ceiling is measured against.
        if power_is_stunt(power):
            cost = QLabel("Stunt")
            cost.setToolTip(
                f"Bought with Extra Effort, not with points — it would cost "
                f"{power_total_cost(power, self._data, self._character)} PP."
            )
        else:
            cost = QLabel(f"{node_display_cost(power, parent, self._data, self._character)} PP")
            self._explain_cost(cost, power)
        cost.setEnabled(False)
        layout.addWidget(cost)
        cost.setVisible(not self._simple)

        # Add each button to the (host-owned) layout *before* setting visibility:
        # addWidget reparents it to `host`, so setVisible acts on a parented child.
        edit = QPushButton("✎")
        edit.setFixedWidth(int(theme.metric("column.chip-button")))
        edit.setToolTip("Edit this power")
        edit.clicked.connect(lambda _checked=False, p=power: self._edit_power(p))
        layout.addWidget(edit)
        edit.setVisible(not self._locked)

        remove = QPushButton("✕")
        remove.setFixedWidth(int(theme.metric("column.chip-button")))
        remove.setToolTip("Remove this power")
        remove.clicked.connect(lambda _checked=False, p=power: self._remove_power(p))
        layout.addWidget(remove)
        remove.setVisible(not self._locked)
        return host

    # -- effect summary and dice footer -----------------------------------
    def _effects_block(self, power: Power) -> QWidget | None:
        """The per-effect summary — each effect's extras/flaws beside its game terms.

        Drawn by :mod:`mm_companion.ui.cards.effects`, which the Equipment block
        renders its items with too: an item wraps a real :class:`Power`, so both
        cards show the same breakdown from the same code. The simple sheet asks for
        that module's shorter version — the terms as small print, the numbers left to
        the roll chips.
        """
        if self._simple:
            return simple_effects_block(power, self._character, self._data)
        return effects_block(power, self._character, self._data)

    def _rolls_block(self, power: Power) -> QWidget | None:
        """The card's dice footer, one line per roll; ``None`` when nothing is rolled.

        A power that resolves without dice gets no footer at all.

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
            simple=self._simple,
        )
        footer.rollRequested.connect(self.rollRequested)
        footer.targetRequested.connect(self.targetRequested)
        footer.areaRequested.connect(self.areaRequested)
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
        return power_rolls_with_area(power, self._character, self._data, npc=self._npc)

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

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points (Add / Remove / group chrome)."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        self._rebuild_list()

    def is_empty(self) -> bool:
        """No powers: the simple sheet leaves this block off rather than print "none"."""
        return not self._character.powers

    def set_simple(self, simple: bool) -> None:
        """Draw the cards for the simple sheet (see :mod:`mm_companion.ui.simple`).

        The same cards and every control on them — a power's switch, an array's live
        member, a Dynamic array's share dials, Extra Effort on the right-click — with
        the build facts taken off: no point costs, no Power Level or homerule
        markers, no mode strip, and each effect's game terms told as small print
        with its attack and save as big numbers on the roll chips.
        """
        simple = bool(simple)
        if simple == self._simple:
            return
        self._simple = simple
        self.set_simple_frame(simple)
        self._rebuild_list()
