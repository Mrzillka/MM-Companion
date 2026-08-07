"""Section 5: equipment.

The Powers block's sibling, and deliberately built out of the same pieces — an
:class:`~mm_companion.core.equipment.EquipmentItem` wraps a real
:class:`~mm_companion.core.powers.Power`, so a suit of armour is drawn by the same
:mod:`mm_companion.ui.cards` machinery that draws a Protection power, from the same
game-term tables. What differs is everything *around* the card:

**Gear is chosen, not assembled.** "Add Equipment" opens the
:class:`~mm_companion.ui.sections.equipment_picker.EquipmentPickerDialog` — the
catalog, filtered and priced — rather than a brick-builder. But the builder is still
there for the two cases the catalog cannot answer: **"Create Custom Item"** and a
card's **✎** open the
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` in gear mode, since
an item's build *is* a power and there was never a second builder to write. Editing
works on a deep copy the constructor hands back, which then replaces the original in
place — the same contract :class:`~mm_companion.ui.sections.powers.PowersSection`
honours, so closing the editor without saving is a no-op.

**The groups are automatic.** A card's group is its item's ``category``, a rules fact
about the thing (a rifle is not armour), so a drag reorders items *within* a group and
reorders the groups against each other, and a cross-group drop is refused — visibly,
with :meth:`~mm_companion.ui.drop_feedback.DropFeedback.show_reject`, since a bare
``ignore()`` reads exactly like a target that simply did not notice. Group order lives
on the character (``equipment_group_order``) and falls back to the ruleset's declared
order.

**Every worn item applies at once.** Clicking a card wears or stows it, with the same
eased dim a power's on/off switch uses — but there is no array-style exclusivity here,
so nothing switches off because something else switched on. What gear *does* have is
the no-stacking rule (``docs/mm-equipment-design.md`` §3): two pieces of armour do not
add up, and neither does armour plus a power. The loser is still on the sheet, so its
card says what beat it rather than showing a number nothing is reading.

**Some gear goes on other gear.** An accessory (a laser sight, a suppressor) is fitted
to a weapon from its own card and then lives *on* that weapon: it leaves the loose list,
its price folds into the host's, and its modifiers are lent to the host's rolls by
:func:`~mm_companion.core.rules.item_effective_build`. That is what keeps a scope's
Improved Aim honest — it is a trait of the rifle, not of the character, and a loose card
sitting in its own group could only imply otherwise.

**There are two currencies**, and the budget bar at the top is the second one: ranks of
the Equipment advantage buy Equipment Points, and those buy the items. Overspending
warns (a red bar and a ``⚠``) and never blocks — see
:func:`mm_companion.core.storage.equipment_enforcement` for the seam that could change
that.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QVariantAnimation, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import EquipmentEntry, GameData
from mm_companion.core.equipment import PER_RANK_COST_KINDS, EquipmentItem
from mm_companion.core.powers import power_is_homerule
from mm_companion.core.rules import (
    PIN_EQUIPMENT,
    GrantedAdvantage,
    PinRef,
    SupersededItemBonus,
    accessory_hosts,
    attach_accessory,
    build_item_from_entry,
    debilitated_traits,
    detach_accessory,
    equipment_budget,
    equipment_points_spent,
    equipment_violations,
    item_attaches_to,
    item_breakage_warnings,
    item_effective_build,
    item_ep_cost,
    item_granted_advantages,
    item_superseded,
    power_has_custom_modifier,
    power_pl_violations,
    power_rolls,
)
from mm_companion.ui import theme
from mm_companion.ui.cards import DraggableCard, DragHandle, NodeList, RollsFooter, effects_block
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.sections.equipment_picker import EquipmentPickerDialog
from mm_companion.ui.sections.stat_table import PinMenuState
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.widgets import BOLD_STYLE, hline_separator, muted_style, tinted_style

#: The two drag payloads this board uses. They are *not* the powers block's
#: :data:`~mm_companion.ui.cards.NODE_MIME`, and they are not each other's: a format is
#: what stops one board accepting a drag it would then resolve against ids it has never
#: heard of. Items and groups are told apart the same way, which is what lets a group's
#: card and its members share one drop stack without a drop having to guess which it was.
EQUIPMENT_MIME = "application/x-mm-equipment-item"
EQUIPMENT_GROUP_MIME = "application/x-mm-equipment-group"

#: What clicking an equipment card does. Unlike a power's, every item has a switch:
#: wearing is a fact about the character whether or not the item grants a bonus.
WEAR_HINT = (
    "Click this card to wear or stow this item — a stowed item keeps its "
    "Equipment Point price but grants nothing."
)

#: Why an item-granted advantage is not in the Advantages block, on the line's tooltip.
GRANT_HINT = (
    "Equipment grants its wielder this advantage only while the item is in use — it "
    "is a trait of the item, not of the character, which is why it is written here "
    "rather than in the Advantages block."
)

#: Why an item's bonus is not on the sheet, on the annotation's tooltip.
STACKING_HINT = (
    "Equipment bonuses do not stack with each other or with powers — the best one "
    "applies. Tick “stacks with other bonuses” on the item to opt it out."
)


class BudgetBar(QWidget):
    """The Equipment Point budget as a bar: how much is spent, out of how much.

    The second currency's whole readout. It is a *warning*, never a gate — a build
    over its budget is shown in red with a ``⚠`` naming the breach and is still
    perfectly saveable, the same bargain the Power Level markers strike.

    Styled from a widget-level stylesheet rather than the theme sheet, for the reason
    :mod:`mm_companion.ui.lock` gives: the Classic preset emits almost no application
    sheet, so a theme rule for this bar would exist under some presets and not others.
    Every token it names is one every preset defines.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(int(theme.metric("space.md")))
        row.addWidget(self._bar, stretch=1)

        self._value = QLabel()
        row.addWidget(self._value)

        self._warning = QLabel("⚠")
        self._warning.setStyleSheet(tinted_style("tint.warning"))
        row.addWidget(self._warning)
        self._warning.setVisible(False)

    def set_budget(self, spent: int, budget: int, unit: str, violations: list[str]) -> None:
        """Show *spent* of *budget*, reddening and warning once it is over."""
        over = spent > budget
        # The range tops out at whichever is larger, so an overspend fills the bar
        # rather than silently clamping and reading as exactly on budget.
        self._bar.setRange(0, max(budget, spent, 1))
        self._bar.setValue(spent)
        radius = int(theme.metric("radius.chip"))
        width = int(theme.metric("border.width"))
        fill = theme.color("tint.worse" if over else "accent")
        self._bar.setStyleSheet(
            f"QProgressBar {{ border: {width}px solid {theme.color('border.card')};"
            f" border-radius: {radius}px; background: transparent; }}"
            f"QProgressBar::chunk {{ background: {fill}; border-radius: {radius}px; }}"
        )
        self._value.setText(f"{spent} / {budget} {unit}")
        self._value.setStyleSheet(tinted_style("tint.worse") if over else BOLD_STYLE)
        self._warning.setVisible(bool(violations))
        self._warning.setToolTip("\n".join(violations))
        self.setToolTip(
            "\n".join(violations)
            if violations
            else f"{budget - spent} {unit} left of the {budget} the Equipment advantage grants."
        )


class EquipmentSection(TitledSection):
    """Equipment section: a budget bar, a catalog picker, and auto-grouped item cards."""

    #: A build change (add/remove an item, reorder, re-price) — marks the sheet dirty.
    changed = Signal()
    #: Wearing or stowing an item. It moves the live sheet numbers but is not part of
    #: the build and is not persisted, so — exactly like a power's on/off toggle — it
    #: must not mark the character dirty.
    runtimeChanged = Signal()
    #: A line of an item's dice footer was clicked, carrying its
    #: :class:`~mm_companion.core.rules.RollSpec` to whichever block answers rolls.
    #: Rolling a weapon is a play action like wearing one, so it marks nothing dirty.
    rollRequested = Signal(object)
    #: A GM asked for one of an item's rolls on (or off) the card beside this sheet.
    pinRequested = Signal(object)  # PinRef
    unpinRequested = Signal(object)  # PinRef

    #: How long a card takes to ease between its worn and stowed looks. A class
    #: attribute so tests can zero it, the same way ``PowersSection.TRANSITION_MS`` is.
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
        self._picker: EquipmentPickerDialog | None = None
        # Whether there is a GM card beside this sheet, and what is already on it.
        # Live state read at menu time, so set_pin_target/set_pinned need no rebuild.
        self._pins = PinMenuState()
        # Per item id, how stowed that item's card currently *looks* (0 worn, 1 fully
        # stowed). Survives the card teardown a toggle triggers, so the replacement
        # card eases on from where its predecessor was — see _show_worn.
        self._card_off: dict[str, float] = {}
        self._card_off_prev: dict[str, float] = {}
        # Open constructor windows, kept referenced so Qt does not collect one the
        # moment the click handler that opened it returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)

        self._budget = BudgetBar()
        layout.addWidget(self._budget)

        self._empty = QLabel("No equipment yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        # The groups stack above the Add button. This outer list carries *group*
        # drags only (its own mime), so an item dragged past its group's edge finds
        # nothing here that will take it.
        self._groups_host = NodeList("", mime=EQUIPMENT_GROUP_MIME, combinable=False)
        self._groups_host.moveRequested.connect(self._on_group_moved)
        layout.addWidget(self._groups_host)

        # Two ways to acquire gear, side by side because they answer different
        # questions: "what is there?" and "what do I have in mind?".
        self._buttons = QWidget()
        button_row = QHBoxLayout(self._buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        self._add_button = QPushButton("Add Equipment")
        self._add_button.setToolTip("Browse the catalog and pick something off it")
        self._add_button.clicked.connect(self._open_picker)
        button_row.addWidget(self._add_button)
        self._custom_button = QPushButton("Create Custom Item")
        self._custom_button.setToolTip("Build a piece of gear the catalog does not have")
        self._custom_button.clicked.connect(self._create_custom_item)
        button_row.addWidget(self._custom_button)
        layout.addWidget(self._buttons)

        self.set_block_title("Equipment")
        self._rebuild_list()

    # -- the catalog ------------------------------------------------------
    def _open_picker(self) -> None:
        """Open (or re-raise) the modeless catalog picker.

        One instance, kept and re-shown: someone kitting a character out picks several
        things, and a fresh dialog each time would lose the filter they just typed.
        """
        if self._picker is None:
            self._picker = EquipmentPickerDialog(self._data, self)
            self._picker.entryChosen.connect(self._add_entry)
        self._picker.show()
        self._picker.raise_()
        self._picker.activateWindow()

    def _add_entry(self, entry: EquipmentEntry) -> None:
        """Put a catalog entry on the character, asking for a rank when it needs one.

        Most gear has one printed price; the handful whose ``cost_kind`` prices it *per
        rank* (an Armored Costume's Protection) needs to know how many. No upper bound
        is imposed here — how high a rank may go is a Power Level question, and
        :func:`~mm_companion.core.rules.power_pl_violations` already marks the card
        when it is breached.
        """
        rank = 1
        if entry.cost_kind in PER_RANK_COST_KINDS:
            unit = self._data.equipment_rules.currency_abbreviation
            chosen, ok = QInputDialog.getInt(
                self,
                f"Add {entry.name}",
                f"Rank ({entry.cost} {unit} per rank):",
                1,
                1,
                99,
            )
            if not ok:
                return
            rank = chosen
        self._character.equipment.append(build_item_from_entry(entry, self._data, rank=rank))
        self._after_change()

    def _remove_item(self, item: EquipmentItem) -> None:
        """Take one item off the character — loose, or fitted to something else.

        An attached accessory is not in the flat list at all (that is what stops the
        budget counting it twice), so removing one has to reach into its host.
        """
        self._character.equipment[:] = [i for i in self._character.equipment if i is not item]
        for host in self._character.equipment:
            host.accessories[:] = [a for a in host.accessories if a is not item]
        self._after_change()

    # -- accessories ------------------------------------------------------
    def _attach_menu(self, accessory: EquipmentItem, anchor: QPushButton) -> None:
        """Offer the items this accessory fits, and fit it to the one chosen.

        A menu rather than a drag: the two boards a drag would have to cross are the
        accessory's own group and the weapon's, and a cross-group drop is exactly what
        this block refuses everywhere else (a rifle may not be dragged into Armor). So
        fitting says what it is instead of overloading the gesture that reorders.
        """
        hosts = accessory_hosts(self._character, accessory, self._data)
        menu = QMenu(anchor)
        for host in hosts:
            action = menu.addAction(host.name or "Equipment")
            action.triggered.connect(lambda _checked=False, h=host: self._attach(h, accessory))
        if not hosts:
            action = menu.addAction("Nothing this fits")
            action.setEnabled(False)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _attach(self, host: EquipmentItem, accessory: EquipmentItem) -> None:
        if attach_accessory(self._character, host, accessory, self._data):
            self._after_change()

    def _detach(self, host: EquipmentItem, accessory: EquipmentItem) -> None:
        if detach_accessory(self._character, host, accessory):
            self._after_change()

    # -- the builder ------------------------------------------------------
    def _create_custom_item(self) -> None:
        """Open the constructor on a blank item, for gear the catalog does not carry."""
        window = PowerConstructorWindow(self._data, character=self._character, gear=True)
        window.itemSaved.connect(self._on_item_saved)
        self._track(window)

    def _edit_item(self, item: EquipmentItem) -> None:
        """Reopen the constructor on an existing item.

        The constructor edits a **deep copy** and hands it back on save, and that copy
        replaces the original in place — so an editor closed without saving leaves the
        character's item exactly as it was. A stock item edited here stops matching its
        catalog entry and is priced from its effects instead
        (:func:`~mm_companion.core.rules.item_is_stock`), which is the card's price
        moving off the book's printed number the moment the build stops being the
        book's.
        """
        window = PowerConstructorWindow(self._data, character=self._character, item=item)
        window.itemSaved.connect(
            lambda edited, original=item: self._on_item_edited(original, edited)
        )
        self._track(window)

    def _track(self, window: PowerConstructorWindow) -> None:
        """Hold a constructor window open and show it."""
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_window_closed(self, window: PowerConstructorWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def _on_item_saved(self, item: EquipmentItem) -> None:
        self._character.equipment.append(item)
        self._after_change()

    def _on_item_edited(self, original: EquipmentItem, edited: EquipmentItem) -> None:
        """Swap the edited copy in for the item it was opened on.

        Matched by id rather than by identity, since the copy carries the original's
        id and nothing else about it can be relied on to have stayed the same. An
        original removed while the editor was open is treated as an add, the way
        :meth:`PowersSection._on_power_edited` treats the same race.
        """
        index = next(
            (
                i
                for i, existing in enumerate(self._character.equipment)
                if existing.id == original.id
            ),
            None,
        )
        if index is None:
            self._character.equipment.append(edited)
        else:
            self._character.equipment[index] = edited
        self._after_change()

    # -- grouping ---------------------------------------------------------
    def _grouped_items(self) -> dict[str, list[EquipmentItem]]:
        """The character's gear bucketed by category, each bucket in sheet order."""
        grouped: dict[str, list[EquipmentItem]] = {}
        for item in self._character.equipment:
            grouped.setdefault(item.category, []).append(item)
        return grouped

    def _ordered_categories(self, grouped: dict[str, list[EquipmentItem]]) -> list[str]:
        """Which groups to draw, in order: the character's, then the ruleset's.

        ``equipment_group_order`` is the user's own arrangement and wins as far as it
        goes; a category it does not name trails, in the order
        ``GameData.equipment_categories`` declares — and a category *that* does not name
        either (a mod's item ahead of its heading) trails those, alphabetically, rather
        than being dropped.
        """
        declared = [category.id for category in self._data.equipment_categories]
        rank = {category: index for index, category in enumerate(declared)}
        named: list[str] = []
        for category in self._character.equipment_group_order:
            if category in grouped and category not in named:
                named.append(category)
        rest = sorted(
            (category for category in grouped if category not in named),
            key=lambda category: (rank.get(category, len(declared)), category),
        )
        return named + rest

    def _category_title(self, category: str) -> str:
        for record in self._data.equipment_categories:
            if record.id == category:
                return record.title or record.id
        return category or "Other"

    def _item(self, item_id: str) -> EquipmentItem | None:
        return next((i for i in self._character.equipment if i.id == item_id), None)

    def _item_category(self, item_id: str) -> str:
        item = self._item(item_id)
        return item.category if item is not None else ""

    def _reflow(self, grouped: dict[str, list[EquipmentItem]], order: list[str]) -> None:
        """Write the grouped buckets back over the flat model list.

        ``Character.equipment`` is flat, and the groups are derived from it — so the
        one way to persist an arrangement is to lay the list out group by group. That
        also keeps a category's items contiguous, which is what makes a within-group
        reorder a plain list splice.
        """
        self._character.equipment[:] = [item for category in order for item in grouped[category]]

    @staticmethod
    def _splice(items: list, item: object, index: int) -> None:
        """Move *item* to *index* within its own list, correcting for its removal."""
        old = items.index(item)
        items.pop(old)
        if old < index:
            index -= 1
        items.insert(max(0, min(index, len(items))), item)

    def _on_item_moved(self, source_id: str, category: str, index: int) -> None:
        """Reorder an item inside its own group.

        A drop from another group never reaches here — the group's list refuses it
        (see :meth:`_make_group_card`) — but the category is re-checked anyway, because
        the model is the thing being spliced and a mismatch would silently move an item
        into a bucket it does not belong to.
        """
        item = self._item(source_id)
        grouped = self._grouped_items()
        if item is None or item.category != category or category not in grouped:
            return
        self._splice(grouped[category], item, index)
        self._reflow(grouped, self._ordered_categories(grouped))
        self._after_change()

    def _on_group_moved(self, source: str, _parent_id: str, index: int) -> None:
        """Reorder a whole group against its siblings, and remember that arrangement.

        Categories the character owns nothing in are kept on the end of
        ``equipment_group_order`` rather than dropped, so emptying a group and refilling
        it later puts it back where its owner had left it.
        """
        grouped = self._grouped_items()
        order = self._ordered_categories(grouped)
        if source not in order:
            return
        self._splice(order, source, index)
        remembered = [c for c in self._character.equipment_group_order if c not in order]
        self._character.equipment_group_order = order + remembered
        self._reflow(grouped, order)
        self._after_change()

    def _after_change(self) -> None:
        # Normalise the flat list back into group order first. The drag handlers do
        # their own reflow before calling here (this repeats it harmlessly), but an
        # *added* item lands on the end of the list and an *edited* one can have
        # changed category outright — both would otherwise leave a group's items
        # scattered through the model, which is the one thing the splice in
        # :meth:`_on_item_moved` assumes is never true.
        grouped = self._grouped_items()
        self._reflow(grouped, self._ordered_categories(grouped))
        self._rebuild_list()
        self.changed.emit()

    # -- rendering --------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the cards from the current character state.

        The seam the sheet calls when something *outside* this block changes what an
        item's card says — an ability (a Strength-Based weapon folds in Strength), the
        Power Level (which sets every attack cap), or the Equipment advantage's rank
        (which is the whole budget). It only reads the model, so it never emits
        :attr:`changed` and there is no signal loop back to the block that triggered it.
        """
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        grouped = self._grouped_items()
        # Hand the on-screen progress over to the cards about to be built, and start a
        # fresh map, so a removed item leaves nothing behind for a later one to inherit.
        self._card_off_prev, self._card_off = self._card_off, {}
        self._groups_host.clear()
        for category in self._ordered_categories(grouped):
            card = self._make_group_card(category, grouped[category])
            self._groups_host.add_entry(category, card)
        self._empty.setVisible(not self._character.equipment)
        self._refresh_budget()

    def _refresh_budget(self) -> None:
        unit = self._data.equipment_rules.currency_abbreviation
        self._budget.set_budget(
            equipment_points_spent(self._character, self._data),
            equipment_budget(self._character, self._data),
            unit,
            equipment_violations(self._character, self._data),
        )

    # -- group card -------------------------------------------------------
    def _make_group_card(self, category: str, items: list[EquipmentItem]) -> QWidget:
        """A framed container for one category: its title bar over its item cards.

        The group is never a switch and never dims — wearing is per item — so its card
        is armed for dragging only. Its inner list is where the category rule lives:
        it admits an item whose own category matches and visibly refuses one that does
        not, which is what makes "a weapon cannot be dragged into Armor" a thing the
        user is *told* rather than a drop that quietly does nothing.
        """
        card = DraggableCard(category, group=True, mime=EQUIPMENT_GROUP_MIME)
        layout = QVBoxLayout(card)
        layout.addWidget(self._group_header(category, items, card))

        inner = NodeList(
            category,
            mime=EQUIPMENT_MIME,
            combinable=False,
            accepts=lambda item_id, home=category: self._item_category(item_id) == home,
        )
        inner.moveRequested.connect(self._on_item_moved)
        for item in items:
            inner.add_entry(item.id, self._make_card(item))

        indent = QWidget()
        indent_layout = QHBoxLayout(indent)
        indent_layout.setContentsMargins(14, 0, 0, 0)
        indent_layout.addWidget(inner)
        layout.addWidget(indent)
        # Not an on/off state — just the resting look, which is where the card's
        # margins and spacing come from.
        card.set_off_progress(0.0)
        return card

    def _group_header(
        self, category: str, items: list[EquipmentItem], card: DraggableCard
    ) -> QWidget:
        """The group's title bar: grip, heading, and the group's own point subtotal."""
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)

        grip = DragHandle()
        grip.setToolTip("Drag to move this whole group")
        grip.dragStarted.connect(card.start_drag)
        row.addWidget(grip)
        grip.setVisible(not self._locked)

        title = QLabel(self._category_title(category))
        title.setStyleSheet(BOLD_STYLE)
        row.addWidget(title)
        row.addStretch()

        unit = self._data.equipment_rules.currency_abbreviation
        spent = sum(item_ep_cost(item, self._data, self._character) for item in items)
        cost = QLabel(f"{spent} {unit}")
        cost.setEnabled(False)
        row.addWidget(cost)
        return host

    # -- item card --------------------------------------------------------
    def _make_card(self, item: EquipmentItem) -> QWidget:
        """A stat-block card for one item, which is also its wear/stow switch."""
        card = DraggableCard(item.id, mime=EQUIPMENT_MIME)
        card.set_clickable(True)
        card.setToolTip(WEAR_HINT)
        card.clicked.connect(lambda i=item: self._toggle_worn(i))

        # The *effective* build throughout: what the item does is its own build plus
        # whatever is fitted to it, and every reader below (the header's PL markers, the
        # terms table, the dice footer) wants one and the same build.
        build = item_effective_build(item, self._data)

        layout = QVBoxLayout(card)
        layout.addWidget(self._header_row(item, card, build))

        if item.build.description:
            desc = QLabel(item.build.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(muted_style(italic=True))
            layout.addWidget(desc)

        effects = effects_block(build, self._character, self._data)
        if effects is not None:
            layout.addWidget(effects)

        fitted = self._accessories_block(item)
        if fitted is not None:
            layout.addWidget(fitted)

        grants = self._grants_block(item)
        if grants is not None:
            layout.addWidget(grants)

        superseded = self._superseded_block(item)
        if superseded is not None:
            layout.addWidget(superseded)

        breakage = self._breakage_block(item)
        if breakage is not None:
            layout.addWidget(breakage)

        # The numbers that come up mid-play, one line per roll. An item that rolls
        # nothing — a first-aid kit, a suit of armour — gets neither the footer nor its
        # rule, the same bargain a passive power's card strikes.
        rolls = self._rolls_block(item, build)
        if rolls is not None:
            layout.addWidget(hline_separator())
            layout.addWidget(rolls)

        self._show_worn(card, item)
        return card

    def _rolls_block(self, item: EquipmentItem, build) -> QWidget | None:
        """An item's dice footer — a weapon's attack, and the save it forces.

        Drawn by the powers block's own :class:`~mm_companion.ui.cards.RollsFooter` from
        the specs :func:`~mm_companion.core.rules.power_rolls` derives off
        ``item.build``, so a sword and a Damage power roll the same way: the attack line
        carries a ``🎲``, the resistance line is written down but unbuttoned (the wielder
        never makes their own target's save — it reaches the person who does as the
        follow-up chip on the attack's history card), and the whole line is the target.

        A **stowed** item keeps its footer. Its card is dimmed because it grants nothing
        while stowed, but drawing a sheathed sword is one motion and a roll is not a
        build fact — refusing the die here would only teach people to click the card
        twice first.
        """
        specs = power_rolls(build, self._character, self._data)
        if not specs:
            return None
        footer = RollsFooter(
            specs,
            pins=self._pins,
            pin_ref=lambda index, iid=item.id: PinRef(PIN_EQUIPMENT, iid, index),
        )
        footer.rollRequested.connect(self.rollRequested)
        footer.pinRequested.connect(self.pinRequested)
        footer.unpinRequested.connect(self.unpinRequested)
        return footer

    def set_pin_target(self, enabled: bool) -> None:
        """Whether an item's roll lines offer to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so a line can offer Unpin."""
        self._pins.set_pinned(refs)

    def _header_row(self, item: EquipmentItem, card: DraggableCard, build=None) -> QWidget:
        """Name and markers on the left; the item's price and its ``✕`` on the right.

        A host widget rather than a bare layout, for the reason the powers header gives:
        ``setVisible`` on a *parentless* widget shows it as a momentary top-level window,
        so every child has to be owned before its visibility is set.
        """
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        grip = DragHandle()
        grip.setToolTip("Drag to reorder this item within its group")
        grip.dragStarted.connect(card.start_drag)
        layout.addWidget(grip)
        grip.setVisible(not self._locked)

        name = QLabel(item.name or "Equipment")
        # On the QFont, never in a stylesheet: a stylesheet font-size outranks the
        # card's own font and would sit out the stowed transition.
        font = name.font()
        font.setPointSizeF(theme.font_size("size.card-name"))
        if item.name and item.name in debilitated_traits(self._character, self._data):
            name.setStyleSheet(tinted_style("tint.worse"))
            font.setStrikeOut(True)
            name.setToolTip("Debilitated — this item is effectively lost")
        else:
            name.setStyleSheet(BOLD_STYLE)
        name.setFont(font)
        layout.addWidget(name)

        violations = power_pl_violations(
            build if build is not None else item.build, self._character, self._data
        )
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet(tinted_style("tint.warning"))
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)

        if self._is_homerule(item):
            homerule = QLabel("⌂")
            homerule.setStyleSheet(tinted_style("tint.homerule"))
            homerule.setToolTip(
                "Homerule item — carries a manual price, a custom modifier, or the "
                "per-item opt-out of the no-stacking rule."
            )
            layout.addWidget(homerule)
        layout.addStretch()

        unit = self._data.equipment_rules.currency_abbreviation
        cost = QLabel(f"{item_ep_cost(item, self._data, self._character)} {unit}")
        cost.setEnabled(False)
        layout.addWidget(cost)

        # Parented by addWidget *before* the visibility is set, for the reason the
        # powers header gives: setVisible on a parentless widget flashes a top-level
        # window.
        # An accessory offers somewhere to go. Only on a *loose* one: a fitted accessory
        # is drawn as a row of its host's card and is taken off from there.
        if item_attaches_to(item, self._data):
            fit = QPushButton("🔗")
            fit.setFixedWidth(24)
            fit.setToolTip("Fit this accessory to a weapon")
            fit.clicked.connect(lambda _checked=False, i=item, b=fit: self._attach_menu(i, b))
            layout.addWidget(fit)
            fit.setVisible(not self._locked)

        edit = QPushButton("✎")
        edit.setFixedWidth(24)
        edit.setToolTip("Edit this item")
        edit.clicked.connect(lambda _checked=False, i=item: self._edit_item(i))
        layout.addWidget(edit)
        edit.setVisible(not self._locked)

        remove = QPushButton("✕")
        remove.setFixedWidth(24)
        remove.setToolTip("Remove this item")
        remove.clicked.connect(lambda _checked=False, i=item: self._remove_item(i))
        layout.addWidget(remove)
        remove.setVisible(not self._locked)
        return host

    def _is_homerule(self, item: EquipmentItem) -> bool:
        """Whether the item's numbers have been bent, and so want the ``⌂`` badge.

        Three ways an item leaves the book: a Dev-mode override or a blank Custom
        modifier on its build (the same two a power is badged for), a hand-set
        Equipment Point price, and the per-item opt-out of the no-stacking rule —
        which is a homerule by construction (``docs/mm-equipment-design.md`` §3).
        """
        return (
            item.stacks
            or item.ep_override is not None
            or power_is_homerule(item.build)
            or power_has_custom_modifier(item.build, self._data)
        )

    def _accessories_block(self, item: EquipmentItem) -> QWidget | None:
        """The items fitted to this one, each with the ``✕`` that takes it off again.

        A row rather than a card of its own: an accessory that is fitted has stopped
        being a thing you own and become part of the thing you own, which is exactly
        what its price folding into the host's says too. Detaching is lossless — the
        stored builds were never rewritten — so the row is a switch, not a decision.
        """
        if not item.accessories:
            return None
        unit = self._data.equipment_rules.currency_abbreviation
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(0)
        for accessory in item.accessories:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            name = QLabel(f"⚭ {accessory.name or 'Accessory'}")
            name.setStyleSheet(muted_style())
            name.setToolTip("Fitted to this item — its price is counted in the total above.")
            row_layout.addWidget(name)
            row_layout.addStretch()
            price = QLabel(f"{item_ep_cost(accessory, self._data, self._character)} {unit}")
            price.setStyleSheet(muted_style())
            row_layout.addWidget(price)
            take_off = QPushButton("✕")
            take_off.setFixedWidth(24)
            take_off.setToolTip("Take this accessory off")
            take_off.clicked.connect(lambda _checked=False, h=item, a=accessory: self._detach(h, a))
            row_layout.addWidget(take_off)
            take_off.setVisible(not self._locked)
            layout.addWidget(row)
        return host

    def _grants_block(self, item: EquipmentItem) -> QWidget | None:
        """ "Grants Improved Aim (Targeting Scope)" — advantages that come with the item.

        Written on the card and **never** added to the Advantages block, because that is
        what the rule says they are: an advantage a weapon grants applies while using
        that weapon and nowhere else. An accessory's is named after the accessory, so a
        scope's Improved Aim reads as the scope's even though it is drawn on the rifle.
        """
        granted = item_granted_advantages(item, self._data)
        if not granted:
            return None
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(0)
        for grant in granted:
            label = QLabel(self._grant_text(item, grant))
            label.setWordWrap(True)
            label.setStyleSheet(muted_style())
            label.setToolTip(GRANT_HINT)
            layout.addWidget(label)
        return host

    @staticmethod
    def _grant_text(item: EquipmentItem, grant: GrantedAdvantage) -> str:
        """The advantage, plus which accessory brought it when it was not the item."""
        if grant.source and grant.source != item.name:
            return f"Grants {grant.name} ({grant.source})"
        return f"Grants {grant.name}"

    def _breakage_block(self, item: EquipmentItem) -> QWidget | None:
        """ "Strength 10 exceeds this Sword's Toughness 7" — the thing is going to break.

        A warning and never a clamp: the Damage bonus stays exactly what the sheet says
        it is, because the weapon breaking is a real event at the table rather than an
        arithmetic cap (``docs/mm-equipment-design.md`` §4). Silence otherwise — an item
        the wielder is not over-straining says nothing rather than printing a
        reassurance.
        """
        warnings = item_breakage_warnings(item, self._character, self._data)
        if not warnings:
            return None
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(0)
        for warning in warnings:
            label = QLabel(f"⚠ {warning}")
            label.setWordWrap(True)
            label.setStyleSheet(tinted_style("tint.warning", bold=False))
            label.setToolTip(self._data.equipment_rules.breakage_rule)
            layout.addWidget(label)
        return host

    def _superseded_block(self, item: EquipmentItem) -> QWidget | None:
        """ "Superseded by *X*" for each bonus this item is not currently granting.

        ``None`` when everything it offers is on the sheet — which is the usual case,
        and an item with nothing to say says nothing rather than printing a reassurance.
        """
        beaten = item_superseded(item, self._character, self._data)
        if not beaten:
            return None
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(0)
        for note in beaten:
            label = QLabel(self._superseded_text(note))
            label.setWordWrap(True)
            label.setStyleSheet(tinted_style("tint.warning", bold=False))
            label.setToolTip(STACKING_HINT)
            layout.addWidget(label)
        return host

    def _superseded_text(self, note: SupersededItemBonus) -> str:
        return f"+{note.amount} {self._trait_label(note)} superseded by {note.beaten_by}"

    def _trait_label(self, note: SupersededItemBonus) -> str:
        """The trait's own display name, falling back to the key it was stored under."""
        for ability in self._data.abilities:
            if ability.key == note.stat:
                return ability.name
        for resistance in self._data.resistances:
            if resistance.key == note.stat:
                return resistance.name
        return note.stat

    # -- worn / stowed ----------------------------------------------------
    def _toggle_worn(self, item: EquipmentItem) -> None:
        """Wear or stow one item.

        Every worn item applies — there is no exclusivity between cards, so nothing
        else changes state here. The whole list is still rebuilt, because wearing one
        item can restate *another* card (the no-stacking rule means a better piece of
        armour supersedes a worse one, and the loser's card has to say so).
        """
        item.worn = not item.worn
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _show_worn(self, card: DraggableCard, item: EquipmentItem) -> None:
        """Put the card into its worn/stowed look, easing when the state just changed.

        The same bargain :meth:`PowersSection._show_activation` strikes and for the
        same reason: no card survives a toggle, so the progress each card was last
        *showing* is remembered per item id and a freshly built card picks up from
        exactly there. The running animation writes it back on every frame, so a
        second click mid-transition resumes rather than snapping.
        """
        target = 0.0 if item.worn else 1.0
        previous = self._card_off_prev.get(item.id)
        self._card_off[item.id] = target
        if previous is None or previous == target or self.TRANSITION_MS <= 0:
            card.set_off_progress(target)
            return
        card.set_off_progress(previous)
        ease = QVariantAnimation(card)
        ease.setStartValue(previous)
        ease.setEndValue(target)
        ease.setDuration(self.TRANSITION_MS)
        ease.setEasingCurve(QEasingCurve.Type.OutCubic)
        ease.valueChanged.connect(lambda value, c=card, i=item.id: self._on_ease(c, i, value))
        ease.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_ease(self, card: DraggableCard, item_id: str, progress: float) -> None:
        card.set_off_progress(progress)
        self._card_off[item_id] = progress

    # -- view mode --------------------------------------------------------
    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points.

        Wearing an item stays available: it is a mid-play action like a power's on/off
        switch, not a build edit, which is why it emits :attr:`runtimeChanged`.
        """
        self._locked = locked
        self._buttons.setVisible(not locked)
        if locked and self._picker is not None:
            self._picker.close()
        if locked:
            for window in list(self._windows):
                window.close()
        self._rebuild_list()
