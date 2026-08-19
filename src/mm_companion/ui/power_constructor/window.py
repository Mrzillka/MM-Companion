from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.equipment import EquipmentItem
from mm_companion.core.powers import (
    PL_CAP_ATTACK,
    PL_CAP_EFFECT,
    Power,
    power_is_homerule,
)
from mm_companion.core.rules import (
    effective_size,
    effective_size_rank,
    improvised_effect_cost,
    improvised_plan,
    improvised_rolls,
    improvised_skills,
    item_ep_cost,
    modifier_label,
    pl_cap_note,
    power_allocation_violations,
    power_cost_formula,
    power_imposed_effect_violations,
    power_linked_range_violations,
    power_modifier_requirement_violations,
    power_pl_violations,
    power_strength_amount_violations,
    power_sub_build_violations,
    power_total_cost,
    power_trait_allocation_violations,
)
from mm_companion.ui import theme
from mm_companion.ui.attachment_dialog import AttachmentDialog
from mm_companion.ui.power_constructor.bricks import BrickList, BrickWidget, PaletteDropZone
from mm_companion.ui.power_constructor.canvas import PowerCanvas
from mm_companion.ui.power_constructor.common import (
    CONFIGURATION_MIME,
    EFFECT_MIME,
    MODIFIER_MIME,
    combat_focus_options,
)
from mm_companion.ui.power_constructor.terms_view import CostOverrideTarget, PowerTermsView
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import BOLD_STYLE, make_spin_box, muted_style, tinted_style


class PowerConstructorWindow(QMainWindow):
    """Standalone brick-builder window for assembling a single power — or one item.

    **Gear mode** (``gear=True``, or an ``item`` to edit) builds a piece of equipment
    instead. It is the same builder throughout — an
    :class:`~mm_companion.core.equipment.EquipmentItem` *wraps* a real
    :class:`~mm_companion.core.powers.Power`, so the palette, the canvas, the game-term
    table and every Dev-mode override work on gear untouched. Four things differ, and
    all four follow from equipment being a different kind of thing bought in a different
    currency:

    - the running total, and any hand-set price, read in **Equipment Points** (see
      :class:`~mm_companion.ui.power_constructor.terms_view.CostOverrideTarget` for why
      that override is not stored on the power);
    - a **group** combo, because a card's category is a rules fact the Equipment block
      sorts by and a custom item has no catalog entry to take one from;
    - the **"stacks with other bonuses"** check box — the per-item opt-out of the
      no-stacking rule (``docs/mm-equipment-design.md`` §3), which lives here because it
      is build state and a homerule;
    - an item is required to have a **name** rather than an effect. A power with no
      effects is nothing at all, but gear with none is ordinary: an accessory that only
      modifies its host weapon has no effects of its own, and a name is the only thing
      that makes an item identifiable on a card.
    """

    closed = Signal()
    powerSaved = Signal(object)  # carries the finished Power to the host section
    itemSaved = Signal(object)  # gear mode's counterpart: the finished EquipmentItem
    #: A roll the Improvise panel offers. This window is not on the sheet's block bus —
    #: it is a window, not a block — so it forwards rather than rolls, and the section
    #: that opened it hands the request on to the roller the way its own cards do.
    rollRequested = Signal(object)  # RollSpec

    def __init__(
        self,
        data: GameData | None = None,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        power: Power | None = None,
        item: EquipmentItem | None = None,
        gear: bool = False,
    ) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        # The wielding character, used to resolve stats that feed a power (Strength
        # for Strength-Based Damage, Attack for the PL cap) and to flag cap breaches.
        # None disables the check (a constructor opened without a character context).
        self._character = character
        # Combat focuses each effect card can offer as an attack-skill link.
        self._focus_options = combat_focus_options(character, self._data)
        # Editing works on a deep copy so closing the window without saving leaves
        # the character's stored power untouched; the copy is what `powerSaved` hands
        # back, and the host section swaps it in for the original on save.
        #
        # The copy is a real ``deepcopy``, *not* a ``to_dict``/``from_dict`` round-trip:
        # the save format deliberately omits runtime state (``activated``,
        # ``item_present``, ``array_active``, ``toggled_on``, ``suppressed``) so a loaded
        # character comes up all-active, which means a round-trip would quietly switch a
        # power the player had turned off back on the moment they edited its description.
        # The same is true of an item: ``worn`` is left out of its save format too.
        #
        # Gear mode is implied by being handed an item to edit — there is nothing else
        # an EquipmentItem could be doing here — and asked for outright by ``gear`` when
        # building a custom one from scratch. In it, ``self.power`` *is* the item's
        # build, so every method below goes on working on the power it always did.
        self._gear = gear or item is not None
        self._editing = (item if self._gear else power) is not None
        if self._gear:
            self.item: EquipmentItem | None = (
                deepcopy(item) if item is not None else EquipmentItem()
            )
            self.power = self.item.build
        else:
            self.item = None
            self.power = deepcopy(power) if self._editing else Power()
        titles = {
            (False, False): "Power Constructor",
            (False, True): "Edit Power",
            (True, False): "Equipment Constructor",
            (True, True): "Edit Equipment",
        }
        self.setWindowTitle(titles[(self._gear, self._editing)])
        self.resize(1240, 660)

        # Three columns: the brick palette, the build panel (the effect canvas the
        # player works in), and the read-only game-term summary. The summary lives in
        # its own column so it can grow with each added effect without ever shrinking
        # the construction canvas beside it.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_palette())
        splitter.addWidget(self._build_build_panel())
        splitter.addWidget(self._build_summary_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        # The palette opens wide enough for a brick's full cost subtitle, and can't be
        # dragged shut — it is the only way to add anything, so a collapsed palette
        # leaves the window with no visible way forward.
        splitter.setSizes([330, 560, 340])
        splitter.setCollapsible(0, False)
        self.setCentralWidget(splitter)

        if self._gear:
            self._terms.set_cost_override_target(self._cost_override_target())

        if self._editing:
            self._seed_from_power()

        # Read, never write: an edited power's own switches are the authority until the
        # user touches something (see :meth:`_apply_extended_settings`).
        self._seed_extended_settings()

        self._refresh_cost()
        self._refresh_improvised()
        self._refresh_game_terms()
        self._refresh_pl_warning()

        # An edited power that already carries overrides opens with Dev mode on, so its
        # homerule edits are visible straight away (this also builds the table). An
        # item's hand-set price is the same kind of edit and opens it the same way.
        if power_is_homerule(self.power) or (self.item is not None and self._item_priced_by_hand()):
            self._dev_mode.setChecked(True)

    @property
    def _noun(self) -> str:
        """What this window calls the thing it is building, in a sentence."""
        return "item" if self._gear else "power"

    @property
    def _currency(self) -> str:
        """What every cost in this window is denominated in.

        One property rather than a decision made at each readout: the effect cards, the
        running total and the Dev-mode override all have to agree, and a card reading
        "= 3 PP" under a total reading "3 EP" is two answers to the same question.
        """
        return self._data.equipment_rules.currency_abbreviation if self._gear else "PP"

    def _item_priced_by_hand(self) -> bool:
        return self.item is not None and self.item.ep_override is not None

    def _cost_override_target(self) -> CostOverrideTarget:
        """Point the Dev-mode price at the *item*, in Equipment Points.

        Not at ``Power.cost_override``: a number typed here is a price in the second
        currency, and storing it on the power would hand every function that asks a
        power what it costs an Equipment-Point answer labelled Power Points.
        """

        def write(value: int | None) -> None:
            if self.item is not None:
                self.item.ep_override = value

        return CostOverrideTarget(
            unit=self._currency,
            read=lambda: None if self.item is None else self.item.ep_override,
            write=write,
            derived=self._derived_ep_cost,
        )

    def _derived_ep_cost(self) -> int:
        """What the item costs with no hand-set price — 0 without an item."""
        if self.item is None:
            return 0
        stored, self.item.ep_override = self.item.ep_override, None
        try:
            return item_ep_cost(self.item, self._data, self._character)
        finally:
            self.item.ep_override = stored

    def _seed_from_power(self) -> None:
        """Populate the editor from the (copied) power — or item — being edited."""
        self._name.setText(self.power.name)
        self._description.setPlainText(self.power.description)
        self.canvas.load_power()
        # The item's own two fields seeded themselves as the panel was built; see
        # :meth:`_build_gear_row`.
        self._save_button.setText("Save Changes")
        self._save_button.setToolTip(f"Update this {self._noun} on the character sheet")

    # The effect palette is grouped by the effect's game-term type; the sections
    # read in a from-offense-to-utility order rather than the raw data order.
    _EFFECT_TYPE_ORDER = (
        "Attack",
        "Defense",
        "Control",
        "Alteration",
        "Movement",
        "Sensory",
        "General",
    )

    # -- left: the palette of bricks --------------------------------------
    def _build_palette(self) -> QWidget:
        from PySide6.QtWidgets import QTabWidget  # local: only used here

        tabs = QTabWidget()
        # ``hidden`` modifiers (the structural Linked / Alternate Effect records) are
        # applied automatically from a power's structure, so they never appear as
        # draggable palette bricks — they stay in the catalog only for cost lookups.
        extras = [
            BrickWidget(
                m.name, m.cost_formula, MODIFIER_MIME, m.id, flat=m.flat, description=m.description
            )
            for m in sorted(self._data.modifiers, key=lambda m: m.name)
            if m.category == "extra" and not m.hidden
        ]
        flaws = [
            BrickWidget(
                m.name, m.cost_formula, MODIFIER_MIME, m.id, flat=m.flat, description=m.description
            )
            for m in sorted(self._data.modifiers, key=lambda m: m.name)
            if m.category == "flaw" and not m.hidden
        ]
        # Keep each tab's search box + bricks addressable (also the test seam).
        self._search_tabs: dict[str, tuple[QLineEdit, list[BrickWidget]]] = {}
        tabs.addTab(
            self._build_search_tab(
                "effects", "Search effects", groups=self._effect_groups(), sortable=True
            ),
            "Effects",
        )
        tabs.addTab(self._build_search_tab("extras", "Search extras", bricks=extras), "Extras")
        tabs.addTab(self._build_search_tab("flaws", "Search flaws", bricks=flaws), "Flaws")
        # The rulebook's named ready-made powers. Only offered when the ruleset ships
        # any, so a mod that strips them loses the tab rather than showing an empty one.
        groups = self._configuration_groups()
        if groups:
            tabs.addTab(
                self._build_search_tab(
                    "configurations", "Search configurations", groups=groups, sortable=True
                ),
                "Configurations",
            )
        tabs.setMinimumWidth(300)
        # The palette is also where an attached chip is dragged to detach it.
        self.palette_zone = PaletteDropZone(tabs)
        return self.palette_zone

    def _effect_groups(self) -> list[tuple[str, list[BrickWidget]]]:
        """The effect bricks bucketed under their game-term type, in reading order."""
        by_type: dict[str, list[BrickWidget]] = {}
        for effect in sorted(self._data.effects, key=lambda e: e.name):
            brick = BrickWidget(
                effect.name,
                effect.base_cost,
                EFFECT_MIME,
                effect.id,
                description=effect.description,
            )
            by_type.setdefault(effect.effect_type, []).append(brick)
        ordered = [t for t in self._EFFECT_TYPE_ORDER if t in by_type]
        ordered += [t for t in by_type if t not in self._EFFECT_TYPE_ORDER]  # any stragglers
        return [(t, by_type[t]) for t in ordered]

    def _configuration_groups(self) -> list[tuple[str, list[BrickWidget]]]:
        """The standard-configuration bricks bucketed under the effect they are built on.

        Grouped the way the book's own table is (PDF p236, "by effect"), because that is
        how they are looked up: someone after Stun is looking under Affliction. The
        brick's subtitle is the cost the *book* prints, which is why it can differ by a
        point from what the built power costs — see ``configurations.json``.
        """

        effect_names = {e.id: e.name for e in self._data.effects}
        by_effect: dict[str, list[BrickWidget]] = {}
        for configuration in self._data.configurations:
            brick = BrickWidget(
                configuration.name,
                configuration.cost_note,
                CONFIGURATION_MIME,
                configuration.id,
                description=configuration.description,
            )
            group = effect_names.get(configuration.base_effect, "Other")
            by_effect.setdefault(group, []).append(brick)
        return [(name, by_effect[name]) for name in sorted(by_effect)]

    def _build_search_tab(
        self,
        key: str,
        placeholder: str,
        *,
        bricks: list[BrickWidget] | None = None,
        groups: list[tuple[str, list[BrickWidget]]] | None = None,
        sortable: bool = False,
    ) -> QWidget:
        """A scrollable :class:`BrickList` with a live search box pinned above it.

        Pass a flat ``bricks`` list or, for the Effects tab, ``groups`` of
        ``(section title, bricks)`` rendered under sticky-styled headers. Typing
        filters the bricks instantly to those whose name contains the query
        (case-insensitive substring), hiding any section left with no matches;
        clearing shows them all.

        A ``sortable`` grouped tab also gets a "Sort A–Z (no groups)" check box: when
        ticked it drops the section headers and lays every brick out in one flat,
        alphabetically-sorted list; unticking restores the grouped view.
        """
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        search = QLineEdit()
        search.setPlaceholderText(placeholder)
        search.setClearButtonEnabled(True)  # a one-click reset
        outer.addWidget(search)

        brick_list = BrickList(list(groups or [(None, bricks or [])]))

        if sortable and groups:
            sort_check = QCheckBox("Sort A–Z (no groups)")
            sort_check.setToolTip(
                "List every effect in one alphabetical list, ignoring type groups."
            )
            sort_check.toggled.connect(brick_list.set_alphabetical)
            outer.addWidget(sort_check)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(brick_list)
        outer.addWidget(scroll, stretch=1)

        search.textChanged.connect(brick_list.filter_to)
        self._search_tabs[key] = (search, brick_list.bricks)
        return tab

    # -- centre: the power being built ------------------------------------
    def _build_build_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self._name = QLineEdit()
        self._name.setPlaceholderText(
            "Item name (e.g. Combat Knife)" if self._gear else "Power name (e.g. Fire Blast)"
        )
        self._name.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name)

        self._description = QTextEdit()
        self._description.setPlaceholderText("Description / flavor text")
        # A compact two-ish line box: the flavor text is short, so keep it from
        # eating vertical room the canvas needs.
        self._description.setFixedHeight(50)
        self._description.textChanged.connect(self._on_description_changed)
        guard_wheel(self._description)  # don't let the box steal the page wheel
        layout.addWidget(self._description)

        if self._gear:
            layout.addWidget(self._build_gear_row())

        layout.addWidget(self._build_extended_row())
        layout.addWidget(self._build_improvised_row())

        # Cross-power relationships (Independent / Array / Linked *between* whole powers)
        # are no longer set here — they're built on the character sheet by dragging one
        # power card onto another to form a group (see ``ui/sections/powers.py``). This
        # constructor's mode bar still governs how a single power's own *effects* combine.

        # A prominent cost bar sits just above the canvas: the running total on the
        # left, the live Power Level / allocation warning on the right (hidden while
        # the power is within caps, naming the breach on its tooltip when it isn't).
        cost_row = QHBoxLayout()
        self._cost = QLabel()
        self._cost.setStyleSheet(BOLD_STYLE)
        # Size on the QFont, never in the stylesheet: a QSS font-size outranks the
        # widget font, which is the mechanism the sheet's power cards animate.
        cost_font = self._cost.font()
        cost_font.setPointSizeF(theme.font_size("size.cost-total"))
        self._cost.setFont(cost_font)
        cost_row.addWidget(self._cost)
        cost_row.addStretch()
        self._warning = QLabel()
        self._warning.setStyleSheet(tinted_style("tint.warning"))
        self._warning.setVisible(False)
        cost_row.addWidget(self._warning)
        layout.addLayout(cost_row)

        self.canvas = PowerCanvas(
            self.power,
            self._data,
            self._focus_options,
            self._character,
            unit=self._currency,
        )
        self.canvas.configurationDropped.connect(self._on_configuration_dropped)
        self.canvas.changed.connect(self._refresh_cost)
        self.canvas.changed.connect(self._refresh_game_terms)
        self.canvas.changed.connect(self._refresh_pl_warning)
        self.canvas.changed.connect(self._apply_extended_settings)
        self.canvas.changed.connect(self._refresh_improvised)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, stretch=1)

        # A save bar pinned below the canvas hands the finished power to the sheet.
        actions = QHBoxLayout()
        actions.addStretch()
        self._save_button = QPushButton("Save Item" if self._gear else "Save Power")
        self._save_button.setToolTip(
            "Add this item to the character sheet"
            if self._gear
            else "Add this power to the character sheet"
        )
        self._save_button.clicked.connect(self._save_power)
        actions.addWidget(self._save_button)
        layout.addLayout(actions)
        return panel

    # -- extended settings -------------------------------------------------

    #: Disclosure glyphs, matching the GM card's collapse control.
    _EXPANDED_GLYPH = "▾"
    _COLLAPSED_GLYPH = "▸"

    def _build_extended_row(self) -> QWidget:
        """The build's optional rules switches, folded away until they are wanted.

        Three settings, each a decision about the *power* rather than about one of its
        effects — whether the wielder's size raises its damage, whether it is held hard
        to the Power Level cap, and whether the sheet card carries a rank slider. A
        giant's fists scale and a giant's laser does not, and that is one answer however
        many effects carry it. (The flags themselves live on each effect, which is the
        level they apply at and the journey ``attack_skill`` already made; these
        checkboxes drive them together.)

        Each **row** hides itself whenever the power has nothing it could apply to, and
        the section hides when every row has — the way the structure bar appears only
        once there are two effects to structure.
        """

        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._extended_toggle = QToolButton()
        self._extended_toggle.setAutoRaise(True)
        self._extended_toggle.setCheckable(True)
        self._extended_toggle.setChecked(True)
        self._extended_toggle.setText(f"{self._EXPANDED_GLYPH} Extended settings")
        self._extended_toggle.toggled.connect(self._on_extended_toggled)
        outer.addWidget(self._extended_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self._extended_body = QWidget()
        body = QVBoxLayout(self._extended_body)
        body.setContentsMargins(16, 0, 0, 0)
        self._size_damage_row = self._build_size_damage_row()
        body.addWidget(self._size_damage_row)
        self._pl_cap_row = self._build_pl_cap_row()
        body.addWidget(self._pl_cap_row)
        self._rank_dial_row = self._build_rank_dial_row()
        body.addWidget(self._rank_dial_row)
        outer.addWidget(self._extended_body)

        self._extended_row = host
        host.setVisible(False)  # until an effect turns up that a row could apply to
        return host

    def _build_improvised_row(self) -> QWidget:
        """The Improvised Effect calculator: what rigging this up on the spot would take.

        Collapsed by default and hidden until the power actually costs something, because
        it answers a question most builds never ask. It belongs *here* rather than on the
        sheet card for the reason improvising exists at all: an improvised effect is one
        the character has **not bought**, and this window is the only place an unbought
        power is ever held (p101).

        The arithmetic is :func:`~mm_companion.core.rules.improvised_plan`; the two spin
        boxes are the trade the rules offer — shave time ranks off the preparation and pay
        for it on the DC, or spend extra ranks and gain a bonus on the check.
        """

        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._improvised_toggle = QToolButton()
        self._improvised_toggle.setAutoRaise(True)
        self._improvised_toggle.setCheckable(True)
        self._improvised_toggle.setChecked(False)
        self._improvised_toggle.setText(f"{self._COLLAPSED_GLYPH} Improvise this effect")
        self._improvised_toggle.setToolTip(
            "What it would take to rig this power up with a skill check instead of "
            "buying it, for a character with the Improvised Effect advantage."
        )
        self._improvised_toggle.toggled.connect(self._on_improvised_toggled)
        outer.addWidget(self._improvised_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self._improvised_body = QWidget()
        body = QVBoxLayout(self._improvised_body)
        body.setContentsMargins(16, 0, 0, 0)
        body.setSpacing(2)

        trades = QHBoxLayout()
        trades.setContentsMargins(0, 0, 0, 0)
        trades.addWidget(QLabel("Prepare faster by"))
        self._improvised_saved = make_spin_box(0, 30, value=0, buttons=False, max_width=44)
        self._improvised_saved.setToolTip(
            "Time ranks shaved off the preparation. Each one adds to the preparation DC, "
            "and the preparation can never drop below the ruleset's minimum."
        )
        self._improvised_saved.valueChanged.connect(lambda _v: self._refresh_improvised())
        trades.addWidget(self._improvised_saved)
        trades.addWidget(QLabel("ranks, or slower by"))
        self._improvised_spent = make_spin_box(0, 30, value=0, buttons=False, max_width=44)
        self._improvised_spent.setToolTip(
            "Extra time ranks spent preparing. Each one is a bonus on the preparation check."
        )
        self._improvised_spent.valueChanged.connect(lambda _v: self._refresh_improvised())
        trades.addWidget(self._improvised_spent)
        trades.addWidget(QLabel("ranks"))
        trades.addStretch()
        body.addLayout(trades)

        self._improvised_note = QLabel()
        self._improvised_note.setWordWrap(True)
        body.addWidget(self._improvised_note)

        # The two checks, as real rollable lines when the wielder can actually improvise.
        self._improvised_rolls_host = QWidget()
        rolls_layout = QVBoxLayout(self._improvised_rolls_host)
        rolls_layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self._improvised_rolls_host)

        self._improvised_body.setVisible(False)
        outer.addWidget(self._improvised_body)

        self._improvised_row = host
        host.setVisible(False)  # until the power costs something to improvise
        return host

    def _on_improvised_toggled(self, expanded: bool) -> None:
        glyph = self._EXPANDED_GLYPH if expanded else self._COLLAPSED_GLYPH
        self._improvised_toggle.setText(f"{glyph} Improvise this effect")
        self._improvised_body.setVisible(expanded)
        if expanded:
            self._refresh_improvised()

    def _refresh_improvised(self) -> None:
        """Restate the plan for the power as it currently stands.

        Cheap enough to run on every edit, but it only runs while the section is open —
        the numbers are read, not acted on, so nobody is served by keeping a collapsed
        panel current.
        """

        cost = improvised_effect_cost(self.power, self._data, self._character)
        self._improvised_row.setVisible(bool(cost))
        if not cost or not self._improvised_toggle.isChecked():
            return
        plan = improvised_plan(
            cost,
            self._data,
            ranks_saved=self._improvised_saved.value(),
            ranks_spent=self._improvised_spent.value(),
        )
        # The spin is not clamped in place — a player winding it up should see it stop
        # mattering rather than have the control fight them — so the sentence says what
        # was actually applied.
        asked = self._improvised_saved.value()
        capped = " (as far as it goes)" if asked > plan.ranks_saved else ""
        bonus = f", +{plan.check_bonus} on the check" if plan.check_bonus else ""
        self._improvised_note.setText(
            f"Reckoned from {plan.cost} PP (a Removable discount does not apply). "
            f"Preparing it takes <b>{plan.time_text}</b>{capped} and a "
            f"<b>DC {plan.prep_dc}</b> check{bonus}, made in secret by the GM. "
            f"Using it the first time is a <b>DC {plan.use_dc}</b> check, "
            "and it lasts one scene."
        )
        self._rebuild_improvised_rolls(plan)

    def _rebuild_improvised_rolls(self, plan) -> None:
        """A button per check, or a note saying what the character is missing.

        Deliberately **not** the sheet's :class:`~mm_companion.ui.cards.rolls.RollsFooter`:
        ``ui.cards`` reaches back into this package for the terms grid *and* sideways into
        ``ui.sections``, so importing it here closes an import loop — at module scope it
        fails outright, and deferred it fails for anyone who opens this window without the
        sheet. Two buttons need none of what that widget adds (pinning, hover chaining,
        target-rolled lines), so they are the honest answer rather than the fallback.
        """

        layout = self._improvised_rolls_host.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        skills = improvised_skills(self._character, self._data) if self._character else ()
        if not skills:
            note = QLabel(
                "Improvising needs the Improvised Effect advantage, taken for the skill "
                "the character improvises with."
            )
            note.setStyleSheet(muted_style())
            note.setWordWrap(True)
            layout.addWidget(note)
            return
        for spec in improvised_rolls(self._character, self._data, plan, skills[0]):
            sign = "+" if spec.modifier >= 0 else ""
            button = QPushButton(f"🎲  {spec.label}  {sign}{spec.modifier} vs DC {spec.dc}")
            button.setToolTip(spec.hint)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, s=spec: self.rollRequested.emit(s))
            layout.addWidget(button)

    def _build_size_damage_row(self) -> QWidget:
        """Size-scales-damage, with a note saying what it is worth to this wielder."""

        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._size_damage = QCheckBox("Size modifies this power's damage")
        # Checked *before* connecting: the handler recomputes the cost bar, which the
        # panel has not built yet at this point in the constructor.
        self._size_damage.setChecked(True)
        self._size_damage.toggled.connect(self._on_size_damage_toggled)
        layout.addWidget(self._size_damage)
        self._size_damage_note = QLabel()
        self._size_damage_note.setStyleSheet(muted_style())
        self._size_damage_note.setWordWrap(True)
        layout.addWidget(self._size_damage_note)
        return row

    def _build_pl_cap_row(self) -> QWidget:
        """The hard Power Level cap, and which side of the trade-off survives it.

        The priority buttons are built once and shown with the checkbox rather than
        created on demand, so a choice the player has made is never destroyed by
        un-ticking and re-ticking the box.
        """

        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._pl_cap = QCheckBox("Hold this power to the wielder's Power Level")
        self._pl_cap.setToolTip(
            "Never let this power resolve above the PL cap. Where the warning marker "
            "merely warns — and lets a large wielder past it, since the book raises the "
            "limit along with the size — a hard cap holds to a flat 2 x PL and lowers "
            "whichever side you choose to give up."
        )
        self._pl_cap.toggled.connect(self._on_pl_cap_toggled)
        layout.addWidget(self._pl_cap)

        self._pl_cap_priority = QWidget()
        priority = QHBoxLayout(self._pl_cap_priority)
        priority.setContentsMargins(16, 0, 0, 0)
        priority.setSpacing(8)
        priority.addWidget(QLabel("Keep the"))
        self._pl_cap_group = QButtonGroup(self)
        self._pl_cap_group.setExclusive(True)
        for value, text, hint in (
            (PL_CAP_EFFECT, "effect", "Hold the effect rank; the attack bonus falls instead."),
            (PL_CAP_ATTACK, "attack", "Hold the attack bonus; the effect rank falls instead."),
        ):
            button = QRadioButton(text)
            button.setToolTip(hint)
            button.setProperty("plCap", value)
            self._pl_cap_group.addButton(button)
            priority.addWidget(button)
        self._pl_cap_group.buttons()[0].setChecked(True)
        self._pl_cap_group.buttonToggled.connect(self._on_pl_cap_priority)
        priority.addStretch()
        self._pl_cap_priority.setVisible(False)
        layout.addWidget(self._pl_cap_priority)

        self._pl_cap_note = QLabel()
        self._pl_cap_note.setStyleSheet(muted_style())
        self._pl_cap_note.setWordWrap(True)
        layout.addWidget(self._pl_cap_note)
        return row

    def _build_rank_dial_row(self) -> QWidget:
        """The build half of the runtime rank dial: whether the card carries a slider."""

        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._rank_dial = QCheckBox("Add a rank slider to the card")
        self._rank_dial.setToolTip(
            "Let this power be used below its bought rank in play — a Damage 10 fired "
            "at 5. It costs the same either way: what a power is worth is what it was "
            "bought at, and dialling one down refunds nothing."
        )
        self._rank_dial.toggled.connect(self._on_rank_dial_toggled)
        layout.addWidget(self._rank_dial)
        return row

    def _chosen_pl_cap(self) -> str:
        """Which side of the Power Level trade-off the priority buttons protect."""

        button = self._pl_cap_group.checkedButton()
        return str(button.property("plCap")) if button is not None else PL_CAP_EFFECT

    def _resisted_effects(self) -> list:
        """The effects in this build that force a resistance.

        The set both the size switch and the hard Power Level cap apply to — the same
        question :func:`~mm_companion.core.rules.effect_size_rank_shift` and
        :func:`~mm_companion.core.rules.power_pl_violations` each ask, so neither row can
        offer a switch that does nothing.
        """

        by_id = {e.id: e for e in self._data.effects}
        return [
            effect
            for effect in self.power.effects
            if (base := by_id.get(effect.effect_id)) is not None
            and base.resistance_dc_base is not None
        ]

    def _dialable_effects(self) -> list:
        """The effects a rank slider could usefully be offered for.

        An effect bought at rank 1 has nothing to dial between, and one whose rank *is*
        its allocation (Enhanced Trait) has no rank of its own to turn down — its rows
        would say one thing and the slider another.
        """

        by_id = {e.id: e for e in self._data.effects}
        return [
            effect
            for effect in self.power.effects
            if effect.rank > 1
            and (base := by_id.get(effect.effect_id)) is not None
            and not base.rank_follows_allocation
        ]

    def _on_extended_toggled(self, expanded: bool) -> None:
        glyph = self._EXPANDED_GLYPH if expanded else self._COLLAPSED_GLYPH
        self._extended_toggle.setText(f"{glyph} Extended settings")
        self._extended_body.setVisible(expanded)

    def _on_size_damage_toggled(self, on: bool) -> None:
        for effect in self._resisted_effects():
            effect.size_scales_damage = on
        self._refresh_extended()

    def _on_pl_cap_toggled(self, on: bool) -> None:
        self._pl_cap_priority.setVisible(on)
        cap = self._chosen_pl_cap() if on else ""
        for effect in self._resisted_effects():
            effect.pl_cap = cap
        self._refresh_extended()

    def _on_pl_cap_priority(self, _button, checked: bool) -> None:
        if not checked:  # the group fires for the button losing the check too
            return
        if self._pl_cap.isChecked():
            self._on_pl_cap_toggled(True)

    def _on_rank_dial_toggled(self, on: bool) -> None:
        for effect in self._dialable_effects():
            effect.rank_dial = on

    def _refresh_extended(self) -> None:
        """Restate everything a switch here can move, plus the notes under them."""

        self._refresh_cost()
        self._refresh_game_terms()
        self._refresh_pl_warning()
        self._refresh_size_damage_note()
        self._refresh_pl_cap_note()

    def _apply_extended_settings(self) -> None:
        """Re-assert the section against a build whose effects have just changed.

        The checkboxes are authoritative once the window is open, so an effect dropped
        onto the canvas **inherits them** rather than its own defaults. Without that,
        adding an effect to a power whose switch is off would quietly turn it back on.
        """

        resisted = self._resisted_effects()
        dialable = self._dialable_effects()
        self._show_extended_rows(resisted, dialable)
        cap = self._chosen_pl_cap() if self._pl_cap.isChecked() else ""
        for effect in resisted:
            effect.size_scales_damage = self._size_damage.isChecked()
            effect.pl_cap = cap
        for effect in dialable:
            effect.rank_dial = self._rank_dial.isChecked()
        self._refresh_size_damage_note()
        self._refresh_pl_cap_note()

    def _seed_extended_settings(self) -> None:
        """Read the section back off a power being edited, without rewriting it."""

        resisted = self._resisted_effects()
        dialable = self._dialable_effects()
        self._show_extended_rows(resisted, dialable)
        if resisted:
            caps = {e.pl_cap for e in resisted}
            cap = caps.pop() if len(caps) == 1 else ""
            for widget, value in (
                (self._size_damage, all(e.size_scales_damage for e in resisted)),
                (self._pl_cap, bool(cap)),
            ):
                widget.blockSignals(True)
                widget.setChecked(value)
                widget.blockSignals(False)
            self._pl_cap_priority.setVisible(bool(cap))
            for button in self._pl_cap_group.buttons():
                if button.property("plCap") == (cap or PL_CAP_EFFECT):
                    button.blockSignals(True)
                    button.setChecked(True)
                    button.blockSignals(False)
        if dialable:
            self._rank_dial.blockSignals(True)
            self._rank_dial.setChecked(all(e.rank_dial for e in dialable))
            self._rank_dial.blockSignals(False)
        self._refresh_size_damage_note()
        self._refresh_pl_cap_note()

    def _show_extended_rows(self, resisted: list, dialable: list) -> None:
        """Show each row only where it applies, and the section only if a row does."""

        self._size_damage_row.setVisible(bool(resisted))
        self._pl_cap_row.setVisible(bool(resisted))
        self._rank_dial_row.setVisible(bool(dialable))
        self._extended_row.setVisible(bool(resisted or dialable))

    def _refresh_size_damage_note(self) -> None:
        """Say what the switch is worth *to this character*, right now."""

        if self._character is None:
            self._size_damage_note.setText(
                "A larger wielder hits harder: the Size Table's Damage column raises "
                "the rank of every effect here that forces a resistance."
            )
            return
        column = self._data.measurements.size_rank_column
        row = self._data.measurements.size_row(effective_size_rank(self._character, self._data))
        amount = row.modifier(column) if row is not None and column else 0
        size = effective_size(self._character, self._data)
        if not amount:
            self._size_damage_note.setText(
                f"{size} size adds nothing — this matters once the wielder grows or shrinks."
            )
            return
        self._size_damage_note.setText(
            f"{size} size is worth {amount:+d} rank to every effect here that forces a "
            "resistance, and shifts its Power Level cap by the same amount."
        )

    def _refresh_pl_cap_note(self) -> None:
        """Name what the cap is *actually* shaving right now, effect by effect.

        A cap that is not biting says so rather than going quiet: "within PL 10" is the
        reassurance a player ticking this box is after, and it is the only way to tell it
        apart from a cap that is switched off.
        """

        if not self._pl_cap.isChecked():
            self._pl_cap_note.setText("")
            return
        if self._character is None:
            self._pl_cap_note.setText(
                "Attack bonus plus effect rank will never exceed twice the wielder's "
                "Power Level; the side you did not keep gives way."
            )
            return
        by_id = {e.id: e for e in self._data.effects}
        notes = [
            f"{effect.label or by_id[effect.effect_id].name}: {note}"
            for effect in self._resisted_effects()
            if (note := pl_cap_note(effect, self._data, self._character))
        ]
        self._pl_cap_note.setText(
            "; ".join(notes)
            if notes
            else f"Within PL {self._character.power_level} — nothing is being held back."
        )

    # -- gear mode's extra build fields ------------------------------------
    def _build_gear_row(self) -> QWidget:
        """The facts about the *item* rather than about the build inside it.

        Which group it files under, whether it opts out of the no-stacking rule, and —
        the accessory pair — what it fits onto and what it lends whatever it is fitted
        to. All four sit up here beside the name rather than anywhere on the canvas,
        because none of them is a property of any effect.
        """
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        top = QWidget()
        row = QHBoxLayout(top)
        row.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(top)
        outer.addWidget(self._build_accessory_row())

        row.addWidget(QLabel("Group"))
        self._category = QComboBox()
        for category in self._data.equipment_categories:
            self._category.addItem(category.title or category.id, category.id)
        if self._category.count() == 0:  # a ruleset declaring no headings at all
            self._category.addItem("Other", "")
        self._category.setToolTip("Which group the Equipment block files this item under.")
        self._category.currentIndexChanged.connect(self._on_category_changed)
        guard_wheel(self._category)
        row.addWidget(self._category)
        row.addStretch()

        self._stacks = QCheckBox("Stacks with other bonuses")
        self._stacks.setToolTip(
            "Equipment bonuses normally do not stack — with each other or with powers, "
            "the best one applies. Tick this to add this item's bonus on top of the "
            "winner instead. It is a homerule, and the item's card is badged ⌂ for it."
        )
        self._stacks.toggled.connect(self._on_stacks_toggled)
        row.addWidget(self._stacks)

        # Seed *here* rather than in _seed_from_power: the build panel is constructed
        # first, so leaving it until then would have the combo's opening row write its
        # own category over the edited item's on the way past. A custom item, which has
        # none, takes whichever group the combo opened on — so an item saved without the
        # combo ever being touched still lands somewhere real.
        if self.item is not None and self.item.category:
            self._select_category(self.item.category)
        else:
            self._on_category_changed()
        if self.item is not None:
            self._stacks.setChecked(self.item.stacks)
        return host

    def _build_accessory_row(self) -> QWidget:
        """Where this fits, and what it lends when it is fitted there.

        A custom accessory could be priced and never attached to anything: both fields
        were written from the catalog at pick time and there was no control for either,
        so ``item_attaches_to`` fell back to a ``catalog_id`` a custom item does not
        have, and the block's fit button is gated on exactly that.

        A single combo rather than a multi-select: every accessory the ruleset ships
        fits one host category, and the model's tuple still admits more for a mod.
        """
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(QLabel("Fits onto"))
        self._attaches = QComboBox()
        self._attaches.addItem("— not an accessory —", "")
        for category in self._data.equipment_categories:
            self._attaches.addItem(category.title or category.id, category.id)
        self._attaches.setToolTip(
            "Gear this can be fitted to. An accessory lives on its host rather than "
            "loose on the sheet, and its price folds into the host's."
        )
        self._attaches.currentIndexChanged.connect(self._on_attaches_changed)
        guard_wheel(self._attaches)
        row.addWidget(self._attaches)

        self._lends = QPushButton()
        self._lends.setToolTip("Choose the modifiers this lends whatever it is fitted to")
        self._lends.clicked.connect(self._edit_attachment)
        row.addWidget(self._lends)
        row.addStretch()

        if self.item is not None and self.item.attaches_to:
            index = self._attaches.findData(self.item.attaches_to[0])
            if index >= 0:
                self._attaches.setCurrentIndex(index)
        self._sync_attachment_button()
        return host

    def _on_attaches_changed(self) -> None:
        if self.item is None:
            return
        chosen = self._attaches.currentData() or ""
        self.item.attaches_to = (chosen,) if chosen else ()
        self._sync_attachment_button()

    def _sync_attachment_button(self) -> None:
        """The button says what is currently lent, and goes dead when nothing can be.

        An item that fits nowhere lends nothing to anything, so offering the picker
        there would be offering a choice with no consequence.
        """
        selections = list(self.item.attachment) if self.item is not None else []
        catalog = self._data.modifier_catalog()
        names = [
            modifier_label(modifier, selection)
            for selection in selections
            if (modifier := catalog.get(selection.modifier_id)) is not None
        ]
        self._lends.setText(f"Lends: {', '.join(names)}" if names else "Lends: nothing")
        self._lends.setEnabled(bool(self.item is not None and self.item.attaches_to))

    def _edit_attachment(self) -> None:
        if self.item is None:
            return
        dialog = AttachmentDialog(self._data, list(self.item.attachment), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.item.attachment = dialog.selections()
            self._sync_attachment_button()
            # The lent modifiers are priced on *this* item, so its total moves.
            self._refresh_cost()

    def _select_category(self, category: str) -> None:
        """Show *category* on the combo, adding a row for one no heading names."""
        index = self._category.findData(category)
        if index < 0:
            self._category.addItem(category or "Other", category)
            index = self._category.count() - 1
        self._category.setCurrentIndex(index)

    def _on_category_changed(self) -> None:
        if self.item is not None:
            self.item.category = self._category.currentData() or ""

    def _on_stacks_toggled(self, on: bool) -> None:
        # A homerule, but not a re-pricing one: what an item costs is unchanged by
        # whether its bonus stacks, so the cost readout has nothing to restate.
        if self.item is not None:
            self.item.stacks = on

    # -- right: the game-term summary (editable in Dev mode) --------------
    def _build_summary_panel(self) -> QWidget:
        """The game-terms breakdown in its own scrolling column.

        Normally read-only, it tints each stat a modifier changed (green better, red
        worse). A **Dev mode (homerule)** check box pinned at its top turns the whole
        table editable in place: every game-term row becomes a combo you can pick or
        type into, with a before/after-modifiers order, plus derived-row and custom-row
        overrides and a whole-power cost override. Housed apart from the canvas so it
        can grow effect by effect without stealing the construction area's height.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)

        head_row = QHBoxLayout()
        heading = QLabel("Game terms")
        heading.setStyleSheet(BOLD_STYLE)
        head_row.addWidget(heading)
        head_row.addStretch()
        self._dev_mode = QCheckBox("Dev mode (homerule)")
        self._dev_mode.setToolTip(
            "Edit this power's derived game terms, readouts, and point cost by hand. "
            "A power with any override is flagged as homerule on its card."
        )
        self._dev_mode.toggled.connect(self._on_dev_mode_toggled)
        head_row.addWidget(self._dev_mode)
        layout.addLayout(head_row)

        self._terms = PowerTermsView()
        # An in-table override edit recomputes cost / PL warning, but must NOT rebuild
        # the table (that would destroy the widget being typed into).
        self._terms.edited.connect(self._on_terms_edited)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._terms)
        guard_wheel(scroll)
        layout.addWidget(scroll, stretch=1)
        return panel

    def _on_dev_mode_toggled(self, on: bool) -> None:
        """Flip the game-terms panel between read-only and the editable override table.
        Dev mode only changes the editor — stored overrides always apply — so the
        derived numbers don't shift on toggle."""
        self._terms.set_editable(on)

    def _on_terms_edited(self) -> None:
        """An override edit inside the table: recompute cost and warnings, but leave the
        table itself untouched so the widget the player is editing survives."""
        self._refresh_cost()
        self._refresh_pl_warning()

    def _on_name_changed(self, text: str) -> None:
        self.power.name = text

    def _on_configuration_dropped(self, name: str) -> None:
        """Title an *untitled* power after the configuration just dropped into it.

        Only when the name box is empty: a player who has already named their power has
        said what it is called, and "Blast" is not an improvement on "Sunfire Lance".
        """

        if not self._name.text().strip():
            self._name.setText(name)

    def _on_description_changed(self) -> None:
        self.power.description = self._description.toPlainText()

    def _refresh_cost(self) -> None:
        # The power's own full assembled cost. Whether it contributes only a flat point
        # as an array alternate is decided by its group on the character sheet, not here.
        #
        # Gear is priced in the other currency, and by `item_ep_cost` rather than
        # inline — which means an item still matching its catalog entry shows the book's
        # *printed* price, and drops to its derived one the moment the build is edited.
        # That is the same number the card shows, and the jump is the honest signal that
        # this is no longer the thing the book priced.
        if self.item is not None:
            total = item_ep_cost(self.item, self._data, self._character)
            suffix = " (homerule)" if self.item.ep_override is not None else ""
        else:
            total = power_total_cost(self.power, self._data, self._character)
            suffix = " (homerule)" if self.power.cost_override is not None else ""
            # A Removable power costs less than the sum of the cards above it, because
            # its discount is charged against the power's total rather than any one
            # effect. Show that arithmetic here — it is the only place it is visible.
            if working := power_cost_formula(self.power, self._data, self._character):
                suffix += f"  ({working})"
        self._cost.setText(f"Total cost: {total} {self._currency}{suffix}")

    def _refresh_game_terms(self) -> None:
        self._terms.set_power(self.power, self._data, self._character)

    def _pl_violations(self) -> list[str]:
        """Power Level cap breaches for the current power (empty without a character)."""
        if self._character is None:
            return []
        return power_pl_violations(self.power, self._character, self._data)

    def _alloc_violations(self) -> list[str]:
        """Tier-4 over-allocation breaches (an effect spending ranks it doesn't have)."""
        return power_allocation_violations(self.power, self._data)

    def _trait_cap_violations(self) -> list[str]:
        """Allocation rows holding more ranks of a trait than it can be taken at.

        Only advantages have a ceiling of their own — most are not ranked, a few cap at a
        fixed number — so in practice this is "three ranks put into a one-rank advantage".
        A warning, not a clamp: the row is the player's and on screen, and quietly
        charging for fewer ranks than it shows would leave the footer disagreeing with it.
        """

        return power_trait_allocation_violations(self.power, self._data, self._character)

    def _linked_violations(self) -> list[str]:
        """Linked effects that don't share a common Range (a build error)."""
        return power_linked_range_violations(self.power, self._data)

    def _strength_violations(self) -> list[str]:
        """Strength-Based amounts paying for more of an ability than the wielder has.

        Constructor-only: the character-sheet card never shows this warning.
        """
        if self._character is None:
            return []
        return power_strength_amount_violations(self.power, self._character, self._data)

    def _requirement_violations(self) -> list[str]:
        """Modifiers attached without a prerequisite they depend on (Increasing
        Difficulty without Cumulative/Progressive) — a house-rule warning."""
        return power_modifier_requirement_violations(self.power, self._data)

    def _imposed_violations(self) -> list[str]:
        """An Affliction's Transformed condition imposing an effect it cannot afford.

        The imposed effect may cost no more than the Affliction imposing it (p110), and
        that budget moves every time the Affliction's rank or modifiers do — which is
        why it is a live warning rather than something the picker could have prevented,
        the way the picker does prevent a too-slow or non-Personal effect.
        """
        return power_imposed_effect_violations(self.power, self._data, self._character)

    def _sub_build_violations(self) -> list[str]:
        """Nested characters over their budget, past their count, or carrying what they
        may not — a Summon's minion, a Metamorph's alternate forms (see
        :mod:`mm_companion.core.rules.subbuilds`)."""
        return power_sub_build_violations(self.power, self._data, self._character)

    def _refresh_pl_warning(self) -> None:
        """Show or hide the live warning from the current PL, allocation, and link breaches."""
        pl = self._pl_violations()
        alloc = self._alloc_violations()
        caps = self._trait_cap_violations()
        linked = self._linked_violations()
        strength = self._strength_violations()
        requirement = self._requirement_violations()
        imposed = self._imposed_violations()
        sub_builds = self._sub_build_violations()
        headlines = []
        if pl:
            headlines.append("over Power Level")
        if alloc:
            headlines.append("over-allocated")
        if caps:
            headlines.append("trait over its rank cap")
        if linked:
            headlines.append("mismatched linked Range")
        if strength:
            headlines.append("Strength shortfall")
        if requirement:
            headlines.append("missing required modifier")
        if imposed:
            headlines.append("imposed effect over budget")
        if sub_builds:
            headlines.append("sub-build over budget")
        headline = ("⚠ " + " & ".join(headlines).capitalize()) if headlines else ""
        if headline:
            self._warning.setText(headline)
            self._warning.setToolTip(
                "\n".join(
                    (
                        *pl,
                        *alloc,
                        *caps,
                        *linked,
                        *strength,
                        *requirement,
                        *imposed,
                        *sub_builds,
                    )
                )
            )
        self._warning.setVisible(bool(headline))

    def _save_power(self) -> None:
        """Hand the assembled power — or item — to the host section, then close.

        A power with no effects has nothing to cost or resolve, so it is rejected
        with a prompt rather than saved empty. An over-allocated Tier-4 effect (one
        spending more ranks than it has) is always rejected — that's a build error,
        not a house-rule choice. A power that breaks a PL cap is rejected only when
        enforcement is set to *block* — otherwise the live warning has already flagged
        it and the save is allowed to proceed.

        **An item is asked for a name instead of an effect.** Gear with no effects is
        ordinary — an accessory that only modifies its host weapon has none, and it is
        still a thing the character owns and pays for — but an unnamed item is a card
        reading "Equipment" that nothing can tell from the next one.
        """
        if self._gear:
            if not self.power.name.strip():
                QMessageBox.information(
                    self,
                    "Name this item",
                    "Give this item a name before saving it.",
                )
                return
        elif not self.power.effects:
            QMessageBox.information(
                self,
                "Nothing to save",
                "Add at least one effect before saving this power.",
            )
            return
        alloc = self._alloc_violations()
        if alloc:
            QMessageBox.warning(
                self,
                "Over-allocated",
                f"This {self._noun} can't be saved because an effect allocates more ranks "
                "than it has:\n\n• " + "\n• ".join(alloc),
            )
            return
        linked = self._linked_violations()
        if linked:
            QMessageBox.warning(
                self,
                "Mismatched linked Range",
                f"This {self._noun} can't be saved because its linked effects don't share "
                "the same Range:\n\n• " + "\n• ".join(linked),
            )
            return
        violations = self._pl_violations()
        if violations and storage.pl_enforcement() == storage.PL_ENFORCE_BLOCK:
            QMessageBox.warning(
                self,
                "Exceeds Power Level",
                f"This {self._noun} can't be saved because it breaks Power Level caps:"
                "\n\n• " + "\n• ".join(violations),
            )
            return
        if self.item is not None:
            self.itemSaved.emit(self.item)
        else:
            self.powerSaved.emit(self.power)
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.closed.emit()
        super().closeEvent(event)
