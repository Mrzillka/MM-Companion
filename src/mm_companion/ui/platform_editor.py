"""The platform editor: buying a vehicle or an installation off its own trait table.

The one place in the app where gear is bought as **traits** rather than as effects. A
vehicle is Size, Strength, Speed, Defense and Toughness off the §5 table; an
installation is Size and Toughness off the §6 one, plus Features. Both are
``docs/mm-equipment-design.md``, and both come out as a
:class:`~mm_companion.core.equipment.PlatformSpec` written onto the item by
:func:`~mm_companion.core.rules.apply_platform`.

Three things shape the dialog, and all three are the rules showing through:

**Size is chosen first**, because it sets the baselines for three of a vehicle's other
traits. So the Size box is at the top, every trait below it carries its baseline in
plain sight, and a trait cannot be taken *below* the baseline its size confers — the
size already paid for it, and a "cheaper" jumbo jet with a motorcycle's Strength is not
a thing the table can price. Changing Size therefore carries the three traits it sets
along with it, keeping whatever was bought above each baseline; a platform *opened*
under its baseline (a printed sailboat is) is left exactly as it was found.

**Nothing here computes a price.** Every number on the readout comes from
:mod:`mm_companion.core.rules.platforms` via a working copy of the item the caller
handed in: the spec is applied to that copy on every keystroke and the copy is asked
what it costs (:func:`~mm_companion.core.rules.item_ep_cost`). That is also what makes
Speed's price appear at all — it is a movement effect on the build rather than a trait,
so only the item itself knows the whole number.

**The two currencies stay apart, visibly.** Durable / Minion / Summonable are the rare
modifiers that price the ranks of the Equipment *advantage* funding a vehicle, in Power
Points. They are shown on their own line, in their own currency, next to the Equipment
Point price and never inside it.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, VehicleSizeRow
from mm_companion.core.equipment import (
    PLATFORM_INSTALLATION,
    PLATFORM_VEHICLE,
    EquipmentItem,
    PlatformSpec,
)
from mm_companion.core.powers import ModifierSelection
from mm_companion.core.rules import (
    apply_platform,
    equipment_advantage_rank,
    installation_size_cost,
    item_ep_cost,
    item_platform,
    item_platform_violations,
    new_platform,
    platform_feature_catalog,
    platform_rules_category,
    vehicle_modifier_advantage_cost,
    vehicle_size_row,
)
from mm_companion.ui import theme
from mm_companion.ui.widgets import BOLD_STYLE, make_spin_box, muted_style, tinted_style

#: How far past the printed tables the boxes let a platform go. The size tables stop at
#: rank 5 (vehicles) and 11 (installations) and are extended arithmetically by the rules
#: layer, so the ceiling here is a sanity bound on a spin box rather than a rule — a GM
#: wanting a continent-sized fortress is not this dialog's argument to have.
MAX_SIZE_RANK = 20
MAX_TRAIT_RANK = 40
MAX_SPEED_RANK = 20
#: How many ranks of one repeatable Feature a platform may buy. The escalations that
#: are defined (Concealed's +10 then +5 to a maximum of +30) top out well inside this.
MAX_FEATURE_RANKS = 5


class PlatformEditorDialog(QDialog):
    """Buy or re-buy one platform's traits, Features and — for a vehicle — its modifiers.

    Edits the :class:`~mm_companion.core.equipment.EquipmentItem` it is given **in
    place** on accept, so the caller hands in a copy and swaps it back over the original
    the way :class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` does. A
    dialog closed with Cancel has written nothing.
    """

    def __init__(
        self,
        data: GameData,
        character: Character,
        item: EquipmentItem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._item = item
        spec = item_platform(item, data)
        self._kind = spec.kind if spec is not None else PLATFORM_VEHICLE
        self._spec = spec if spec is not None else new_platform(self._kind, data)
        #: Feature id -> its rank box. Built once; a spec is read out of them, never
        #: back into them, so nothing here has to guard against a feedback loop.
        self._feature_boxes: dict[str, object] = {}
        self._modifier_boxes: dict[str, QCheckBox] = {}
        self._loading = True

        self.setWindowTitle(f"Edit {item.name}" if item.name else "New Platform")
        layout = QVBoxLayout(self)

        self._name = QLineEdit(item.name)
        self._name.textChanged.connect(self._sync)
        form = QFormLayout()
        form.addRow("Name", self._name)
        self._build_traits(form)
        layout.addLayout(form)

        layout.addWidget(self._features_panel())
        if self._kind == PLATFORM_VEHICLE:
            modifiers = self._modifiers_panel()
            if modifiers is not None:
                layout.addWidget(modifiers)

        self._cost = QLabel()
        self._cost.setStyleSheet(BOLD_STYLE)
        layout.addWidget(self._cost)
        self._advantage_cost = QLabel()
        self._advantage_cost.setStyleSheet(muted_style())
        self._advantage_cost.setWordWrap(True)
        layout.addWidget(self._advantage_cost)
        self._warnings = QLabel()
        self._warnings.setStyleSheet(tinted_style("tint.warning"))
        self._warnings.setWordWrap(True)
        layout.addWidget(self._warnings)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._loading = False
        self._sync()

    # -- the trait rows ---------------------------------------------------
    def _build_traits(self, form: QFormLayout) -> None:
        """The kind's own trait boxes, Size first because Size sets the rest."""
        spec = self._spec
        if self._kind == PLATFORM_INSTALLATION:
            rules = self._data.installation_rules
            table = rules.size_table
            floor = min((row.size_rank for row in table), default=1)
            ceiling = max((row.size_rank for row in table), default=MAX_SIZE_RANK)
            self._size = make_spin_box(floor, max(ceiling, MAX_SIZE_RANK), value=spec.size)
            self._size.valueChanged.connect(self._sync)
            self._size_note = QLabel()
            self._size_note.setStyleSheet(muted_style())
            form.addRow("Size", self._row(self._size, self._size_note))

            self._toughness = make_spin_box(0, MAX_TRAIT_RANK, value=spec.toughness)
            self._toughness.valueChanged.connect(self._sync)
            self._toughness_note = QLabel()
            self._toughness_note.setStyleSheet(muted_style())
            form.addRow("Toughness", self._row(self._toughness, self._toughness_note))

            self._impervious = make_spin_box(0, MAX_TRAIT_RANK, value=self._impervious_rank())
            self._impervious.valueChanged.connect(self._sync)
            self._impervious.setToolTip(
                "Impervious Toughness stops damage below its rank outright. An "
                "installation's Toughness may reach twice the series Power Level, but "
                "its Impervious may not."
            )
            form.addRow("Impervious", self._impervious)
            return

        classes = self._data.vehicle_rules.classes
        self._class = QComboBox()
        for record in classes:
            self._class.addItem(record.title or record.id, record.id)
        index = self._class.findData(spec.vehicle_class)
        if index >= 0:
            self._class.setCurrentIndex(index)
        self._class.currentIndexChanged.connect(self._sync)
        self._class.setToolTip(
            "The medium it travels through — and so which movement effect its Speed "
            "rank is a rank of. An Exotic vehicle names none, and its Speed reaches "
            "the sheet only if its build carries a movement effect of its own."
        )
        form.addRow("Class", self._class)

        self._size = make_spin_box(0, MAX_SIZE_RANK, value=spec.size)
        self._size.valueChanged.connect(self._on_size_changed)
        self._size_note = QLabel()
        self._size_note.setStyleSheet(muted_style())
        self._size.setToolTip(
            "Chosen first: Size sets the Strength, Toughness and Defense a vehicle "
            "gets for nothing, and none of the three can be taken below it."
        )
        form.addRow("Size", self._row(self._size, self._size_note))

        self._strength = make_spin_box(0, MAX_TRAIT_RANK, value=spec.strength)
        self._strength.valueChanged.connect(self._sync)
        self._strength_note = QLabel()
        self._strength_note.setStyleSheet(muted_style())
        form.addRow("Strength", self._row(self._strength, self._strength_note))

        self._toughness = make_spin_box(0, MAX_TRAIT_RANK, value=spec.toughness)
        self._toughness.valueChanged.connect(self._sync)
        self._toughness_note = QLabel()
        self._toughness_note.setStyleSheet(muted_style())
        form.addRow("Toughness", self._row(self._toughness, self._toughness_note))

        self._defense = make_spin_box(-MAX_TRAIT_RANK, 0, value=spec.defense_modifier)
        self._defense.valueChanged.connect(self._sync)
        self._defense_note = QLabel()
        self._defense_note.setStyleSheet(muted_style())
        self._defense.setToolTip(
            "A vehicle's size makes it easier to hit. Each −1 bought back off that "
            "penalty costs a point; it never rises above zero."
        )
        form.addRow("Defense", self._row(self._defense, self._defense_note))

        self._speed = make_spin_box(0, MAX_SPEED_RANK, value=spec.speed or 0)
        self._speed.valueChanged.connect(self._sync)
        self._speed_note = QLabel()
        self._speed_note.setStyleSheet(muted_style())
        self._speed.setToolTip(
            "Speed is not a trait off the table — it is a movement effect, and it is "
            "priced as one through the build. Zero means the vehicle has no movement "
            "rank of its own."
        )
        form.addRow("Speed", self._row(self._speed, self._speed_note))
        # The baselines the boxes currently reflect, so a size change can move each
        # trait by the same amount its baseline moved rather than clamping it.
        self._baseline = vehicle_size_row(spec.size, self._data) or VehicleSizeRow(
            size_rank=spec.size, strength=0, toughness=0, defense=0
        )
        self._apply_baseline_floors(follow=False)

    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        """A form field and the note that annotates it, side by side."""
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch()
        return host

    def _impervious_rank(self) -> int:
        """The Impervious rank the spec's Toughness annotations already carry."""
        wanted = self._data.installation_rules.impervious_modifier
        return next((s.rank for s in self._spec.defenses if s.modifier_id == wanted), 0)

    def _on_size_changed(self) -> None:
        """Move the three size-derived traits with their new baselines, then re-price."""
        self._apply_baseline_floors()
        self._sync()

    def _apply_baseline_floors(self, *, follow: bool = True) -> None:
        """Carry the three size-derived boxes to the current size's baselines.

        **You keep what you bought.** Each of the three moves by the same amount its
        baseline did, so a jet upgraded from size 3 to 4 keeps its two points of bought
        Strength and its unbought Defense penalty rather than quietly acquiring three
        points of Defense it never asked for. That is "Size is chosen first" honoured in
        a dialog where every box is on screen at once.

        The minimum moves too, so the floor holds against a number typed straight into
        the box and not only against the arrows: a trait below its baseline is not a
        cheaper vehicle, it is a build the table has no price for
        (:func:`~mm_companion.core.rules.vehicle_trait_cost` reads it as zero).

        ``follow=False`` — how the dialog *opens* — moves nothing and floors at
        whichever is lower, the baseline or what the platform already has. A printed
        sailboat's Toughness is one under its size's, and a dialog that silently
        corrected it would re-price a boat someone opened to add a Feature to.
        """
        baseline = vehicle_size_row(self._size.value(), self._data)
        if baseline is None:
            return
        previous = self._baseline
        for box, floor, was in (
            (self._strength, baseline.strength, previous.strength),
            (self._toughness, baseline.toughness, previous.toughness),
            (self._defense, baseline.defense, previous.defense),
        ):
            blocked = box.blockSignals(True)
            value = box.value() + (floor - was) if follow else box.value()
            box.setMinimum(min(floor, value))
            box.setValue(value)
            box.blockSignals(blocked)
        self._baseline = baseline

    # -- Features and modifiers -------------------------------------------
    def _features_panel(self) -> QWidget:
        """Every Feature this kind of platform may carry, each with its rank box.

        A rank box rather than a checkbox because a repeatable Feature is bought more
        than once and each rank is another point — and because the escalating ones
        (Concealed's +10 then +5 a rank) read their step count off exactly that number.
        A Feature the ruleset says is not repeatable is capped at one, which is the same
        control saying so.
        """
        catalog = platform_feature_catalog(self._kind, self._data)
        counts: dict[str, int] = {}
        for feature in self._spec.features:
            counts[feature] = counts.get(feature, 0) + 1

        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("Features")
        heading.setStyleSheet(BOLD_STYLE)
        column.addWidget(heading)

        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(0, 0, 0, 0)
        for feature in catalog.values():
            box = make_spin_box(
                0,
                MAX_FEATURE_RANKS if feature.repeatable else 1,
                value=counts.get(feature.id, 0),
            )
            box.valueChanged.connect(self._sync)
            if feature.description:
                box.setToolTip(feature.description)
            label = QLabel(feature.name)
            if feature.description:
                label.setToolTip(feature.description)
            form.addRow(label, box)
            self._feature_boxes[feature.id] = box

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(theme.metric("platform.features.height"))
        column.addWidget(scroll)
        return host

    def _modifiers_panel(self) -> QWidget | None:
        """Durable / Minion / Summonable — priced in the *other* currency, and said so.

        ``None`` when the ruleset declares none, so a stripped-down ruleset simply has
        no such row rather than an empty box.
        """
        if not self._data.vehicle_modifiers:
            return None
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("Vehicle modifiers")
        heading.setStyleSheet(BOLD_STYLE)
        column.addWidget(heading)
        for modifier in self._data.vehicle_modifiers:
            box = QCheckBox(f"{modifier.name} ({modifier.cost:+d} PP per Equipment rank)")
            box.setChecked(modifier.id in self._spec.modifiers)
            box.setToolTip(
                f"{modifier.description}\n\nThis changes what the ranks of the Equipment "
                "advantage funding this vehicle cost in Power Points — not what the "
                "vehicle costs in Equipment Points."
            )
            box.toggled.connect(self._sync)
            column.addWidget(box)
            self._modifier_boxes[modifier.id] = box
        return host

    # -- reading the widgets back -----------------------------------------
    def _read_spec(self) -> PlatformSpec:
        """The spec the boxes currently describe.

        Built fresh from the original rather than mutated in place, so the fields this
        dialog does not show — a printed vehicle's own movement effect, its notes —
        survive an edit untouched.
        """
        spec = PlatformSpec(
            kind=self._kind,
            vehicle_class=self._spec.vehicle_class,
            size=self._size.value(),
            strength=self._spec.strength,
            speed=self._spec.speed,
            defense_modifier=self._spec.defense_modifier,
            toughness=self._toughness.value(),
            modifiers=[
                modifier_id for modifier_id, box in self._modifier_boxes.items() if box.isChecked()
            ],
            defenses=list(self._spec.defenses),
            movement_effect=self._spec.movement_effect,
            movement_rank=self._spec.movement_rank,
            notes=self._spec.notes,
        )
        features: list[str] = []
        for feature_id, box in self._feature_boxes.items():
            features.extend([feature_id] * box.value())
        spec.features = features

        if self._kind == PLATFORM_INSTALLATION:
            wanted = self._data.installation_rules.impervious_modifier
            spec.defenses = [s for s in spec.defenses if s.modifier_id != wanted]
            if self._impervious.value():
                spec.defenses.append(
                    ModifierSelection(modifier_id=wanted, rank=self._impervious.value())
                )
            return spec

        spec.vehicle_class = self._class.currentData() or ""
        spec.strength = self._strength.value()
        spec.defense_modifier = self._defense.value()
        spec.speed = self._speed.value() or None
        # A platform whose movement effect is named outright (a mole machine burrows)
        # keeps its own rank in step with the Speed box, or the box would say one thing
        # and the sheet's Speed readout another.
        if spec.movement_effect:
            spec.movement_rank = spec.speed
        return spec

    def _sync(self) -> None:
        """Re-apply the spec to the working item and restate the price and the warnings.

        Applying it on every keystroke is what lets the readout be the *item's* real
        price — traits, Features and the movement effect the build pays for — rather
        than a second sum computed here that could drift from the one the card shows.
        """
        if self._loading:
            return
        spec = self._read_spec()
        self._item.build.name = self._name.text()
        apply_platform(self._item, spec, self._data)
        self._describe_traits(spec)

        unit = self._data.equipment_rules.currency_abbreviation
        self._cost.setText(f"{item_ep_cost(self._item, self._data, self._character)} {unit}")

        ranks = equipment_advantage_rank(self._character, self._data)
        advantage = vehicle_modifier_advantage_cost(spec.modifiers, ranks, self._data)
        self._advantage_cost.setText(
            f"Vehicle modifiers: {advantage:+d} PP on the {ranks} ranks of the "
            "Equipment advantage funding this vehicle."
            if spec.modifiers
            else ""
        )
        self._advantage_cost.setVisible(bool(spec.modifiers))

        violations = item_platform_violations(self._item, self._character, self._data)
        self._warnings.setText("\n".join(f"⚠ {message}" for message in violations))
        self._warnings.setVisible(bool(violations))

    def _describe_traits(self, spec: PlatformSpec) -> None:
        """The plain-sight annotations: what each box's baseline is, and what it costs."""
        if self._kind == PLATFORM_INSTALLATION:
            rules = self._data.installation_rules
            row = rules.size_row(spec.size)
            examples = ", ".join(row.examples) if row is not None else ""
            cost = installation_size_cost(spec.size, self._data)
            self._size_note.setText(f"{examples} ({cost:+d})" if examples else f"({cost:+d})")
            self._toughness_note.setText(f"free to {rules.free_toughness}")
            return

        baseline = vehicle_size_row(spec.size, self._data)
        if baseline is None:
            return
        self._size_note.setText(", ".join(baseline.examples))
        self._strength_note.setText(f"size gives {baseline.strength}")
        self._toughness_note.setText(f"size gives {baseline.toughness}")
        self._defense_note.setText(f"size gives {baseline.defense:+d}")
        vehicle_class = self._data.vehicle_rules.vehicle_class(spec.vehicle_class)
        effect_id = spec.movement_effect or (vehicle_class.effect if vehicle_class else "")
        base = next((e for e in self._data.effects if e.id == effect_id), None)
        self._speed_note.setText(
            f"as {base.name}" if base is not None and spec.speed else "no movement effect"
        )

    def accept(self) -> None:
        """Commit the spec to the item and close. ``_sync`` has already written it."""
        self._sync()
        super().accept()

    @property
    def item(self) -> EquipmentItem:
        """The item this dialog was editing, with the accepted spec on it."""
        return self._item


def platform_kind_title(kind: str, data: GameData) -> str:
    """What this ruleset calls one platform of *kind*, for a button or a menu entry.

    Read off the equipment category the kind files under rather than from the ``kind``
    string, so a ruleset that renames Vehicles gets its own word here too. Falls back to
    the id, which is at least true.
    """

    category = platform_rules_category(kind, data)
    for record in data.equipment_categories:
        if record.id == category:
            title = record.title or record.id
            # The categories are plural headings ("Vehicles"); one of them is not.
            return title[:-1] if title.endswith("s") else title
    return category or kind
