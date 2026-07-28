from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.powers import (
    Power,
    power_is_homerule,
)
from mm_companion.core.rules import (
    power_allocation_violations,
    power_linked_range_violations,
    power_modifier_requirement_violations,
    power_pl_violations,
    power_strength_amount_violations,
    power_total_cost,
)
from mm_companion.ui.power_constructor.bricks import BrickList, BrickWidget, PaletteDropZone
from mm_companion.ui.power_constructor.canvas import PowerCanvas
from mm_companion.ui.power_constructor.common import (
    EFFECT_MIME,
    MODIFIER_MIME,
    combat_focus_options,
)
from mm_companion.ui.power_constructor.terms_view import PowerTermsView
from mm_companion.ui.wheel_guard import guard_wheel


class PowerConstructorWindow(QMainWindow):
    """Standalone brick-builder window for assembling a single power."""

    closed = Signal()
    powerSaved = Signal(object)  # carries the finished Power to the host section

    def __init__(
        self,
        data: GameData | None = None,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        power: Power | None = None,
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
        self._editing = power is not None
        self.power = deepcopy(power) if self._editing else Power()
        self.setWindowTitle("Edit Power" if self._editing else "Power Constructor")
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

        if self._editing:
            self._seed_from_power()

        self._refresh_cost()
        self._refresh_game_terms()
        self._refresh_pl_warning()

        # An edited power that already carries overrides opens with Dev mode on, so its
        # homerule edits are visible straight away (this also builds the table).
        if power_is_homerule(self.power):
            self._dev_mode.setChecked(True)

    def _seed_from_power(self) -> None:
        """Populate the editor from the (copied) power being edited."""
        self._name.setText(self.power.name)
        self._description.setPlainText(self.power.description)
        self.canvas.load_power()
        self._save_button.setText("Save Changes")
        self._save_button.setToolTip("Update this power on the character sheet")

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
        self._name.setPlaceholderText("Power name (e.g. Fire Blast)")
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

        # Cross-power relationships (Independent / Array / Linked *between* whole powers)
        # are no longer set here — they're built on the character sheet by dragging one
        # power card onto another to form a group (see ``ui/sections/powers.py``). This
        # constructor's mode bar still governs how a single power's own *effects* combine.

        # A prominent cost bar sits just above the canvas: the running total on the
        # left, the live Power Level / allocation warning on the right (hidden while
        # the power is within caps, naming the breach on its tooltip when it isn't).
        cost_row = QHBoxLayout()
        self._cost = QLabel()
        self._cost.setStyleSheet("font-size: 12pt; font-weight: bold;")
        cost_row.addWidget(self._cost)
        cost_row.addStretch()
        self._warning = QLabel()
        self._warning.setStyleSheet("color: #d1a01e; font-weight: bold;")
        self._warning.setVisible(False)
        cost_row.addWidget(self._warning)
        layout.addLayout(cost_row)

        self.canvas = PowerCanvas(self.power, self._data, self._focus_options, self._character)
        self.canvas.changed.connect(self._refresh_cost)
        self.canvas.changed.connect(self._refresh_game_terms)
        self.canvas.changed.connect(self._refresh_pl_warning)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, stretch=1)

        # A save bar pinned below the canvas hands the finished power to the sheet.
        actions = QHBoxLayout()
        actions.addStretch()
        self._save_button = QPushButton("Save Power")
        self._save_button.setToolTip("Add this power to the character sheet")
        self._save_button.clicked.connect(self._save_power)
        actions.addWidget(self._save_button)
        layout.addLayout(actions)
        return panel

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
        heading.setStyleSheet("font-weight: bold;")
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

    def _on_description_changed(self) -> None:
        self.power.description = self._description.toPlainText()

    def _refresh_cost(self) -> None:
        # The power's own full assembled cost. Whether it contributes only a flat point
        # as an array alternate is decided by its group on the character sheet, not here.
        total = power_total_cost(self.power, self._data, self._character)
        suffix = " (homerule)" if self.power.cost_override is not None else ""
        self._cost.setText(f"Total cost: {total} PP{suffix}")

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

    def _refresh_pl_warning(self) -> None:
        """Show or hide the live warning from the current PL, allocation, and link breaches."""
        pl = self._pl_violations()
        alloc = self._alloc_violations()
        linked = self._linked_violations()
        strength = self._strength_violations()
        requirement = self._requirement_violations()
        headlines = []
        if pl:
            headlines.append("over Power Level")
        if alloc:
            headlines.append("over-allocated")
        if linked:
            headlines.append("mismatched linked Range")
        if strength:
            headlines.append("Strength shortfall")
        if requirement:
            headlines.append("missing required modifier")
        headline = ("⚠ " + " & ".join(headlines).capitalize()) if headlines else ""
        if headline:
            self._warning.setText(headline)
            self._warning.setToolTip("\n".join((*pl, *alloc, *linked, *strength, *requirement)))
        self._warning.setVisible(bool(headline))

    def _save_power(self) -> None:
        """Hand the assembled power to the host section, then close.

        A power with no effects has nothing to cost or resolve, so it is rejected
        with a prompt rather than saved empty. An over-allocated Tier-4 effect (one
        spending more ranks than it has) is always rejected — that's a build error,
        not a house-rule choice. A power that breaks a PL cap is rejected only when
        enforcement is set to *block* — otherwise the live warning has already flagged
        it and the save is allowed to proceed.
        """
        if not self.power.effects:
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
                "This power can't be saved because an effect allocates more ranks "
                "than it has:\n\n• " + "\n• ".join(alloc),
            )
            return
        linked = self._linked_violations()
        if linked:
            QMessageBox.warning(
                self,
                "Mismatched linked Range",
                "This power can't be saved because its linked effects don't share "
                "the same Range:\n\n• " + "\n• ".join(linked),
            )
            return
        violations = self._pl_violations()
        if violations and storage.pl_enforcement() == storage.PL_ENFORCE_BLOCK:
            QMessageBox.warning(
                self,
                "Exceeds Power Level",
                "This power can't be saved because it breaks Power Level caps:\n\n• "
                + "\n• ".join(violations),
            )
            return
        self.powerSaved.emit(self.power)
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.closed.emit()
        super().closeEvent(event)
