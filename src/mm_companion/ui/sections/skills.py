"""Section 3: the skills table.

Each skill row lays out its bonus as a sum of columns: the linked ability's
short code and current rank, the skill's own ranks, and the modifiers imposed
from outside — bonuses granted by powers and advantages, penalties imposed by
conditions. The total bonus is their sum. Only the ranks are bought: the "+"
column is a derived read-out (:func:`skill_modifiers`), never an input, and the
whole column hides while nothing modifies any row.
Focused skills (Close Combat, Expertise, Ranged Combat) have no ranks of their
own — the character instead adds focused instances, each of which becomes its
own rankable row. Any skill can also carry *specialized* rows: narrow, half-cost
rank pools rendered as extra indented rows under the skill.

To save vertical space the skills are laid out across several side-by-side
tables. The number of panels adapts to the block's width (see
:mod:`mm_companion.ui.sections.column_flow`): a wide block shows more columns, a
narrow one fewer, and a long skill/focus name raises the minimum panel width so
the count drops before anything clips. Neither table scrolls — each is sized to
show all of its rows and grows as focuses are added, so the whole section scrolls
with the page. The split is dynamic: skills are grouped into blocks (a plain
skill is one block; a focused skill with its focus rows, plus any skill's
specialization rows, form a single block), and the blocks are divided across the
panels so their heights are as even as possible without ever splitting a block.

**Which rows, and in what order, is the player's** — the ruleset supplies the
catalogue, not the sheet's layout. Any row is **dragged** to a new place and
**right-clicked** to remove, the shared table-block gestures out of
:mod:`mm_companion.ui.sections.row_table`, and a sort dropdown reorders the lot at
once. Two levels, because the rows are two things: a skill moves among the skills
and carries its focus/specialization rows with it, while a focus moves among its
own skill's focuses and nowhere else (a drop outside is visibly refused). The
order lives on the character as ``skill_order`` and a removed row as
``hidden_skills``, both resolved against ``GameData.skills`` — so a ruleset that
gains a skill still shows it, and the ``↺`` button puts the whole block back to
the ruleset's own order and set. Removing a skill drops what was bought on it,
which is why it asks first when there is anything to lose; ``↺`` restores the
rows, and cannot restore the ranks.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, Skill
from mm_companion.core.rules import (
    PIN_SKILL,
    PinRef,
    SkillModifiers,
    effective_ability,
    granted_skill_rows,
    skill_modifiers,
    skill_points_spent,
    skill_roll,
    skill_total,
    split_trait_key,
    trait_display_name,
)
from mm_companion.ui import theme
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.column_flow import ColumnFlowPanels, even_split
from mm_companion.ui.sections.row_table import (
    SORT_MANUAL,
    AutoHeightTable,
    RowEntry,
    RowIndex,
    RowReorder,
    SortControl,
    install_row_menu,
    move_within,
    remove_contributor,
    wrapping_column_width,
)
from mm_companion.ui.sections.stat_table import (
    CONDITION_TINT,
    ROLL_ROLE,
    PinMenuState,
    bonus_tint,
    pin_menu_contributor,
    tint_item,
)
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, readonly_item

RANK_MIN, RANK_MAX = 0, 20
COL_NAME, COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL = range(6)
HEADERS = ["Skill", "Ability", "ABL", "Rank", "+", "Total"]

# Sort modes for the skills list (UI-only state, not persisted; the *order* they
# write is). SORT_MANUAL is the shared one — see row_table, which only lets rows be
# dragged while it is in force.
SORT_ALPHA, SORT_ABILITY, SORT_TOTAL, SORT_RANK = "alpha", "ability", "total", "rank"

#: This block's drag payload. Its own format, so no other block's rows can land here.
ROW_MIME = "application/x-mm-rows-skills"
# Rough widths used to decide how many panels fit without clipping a name. The
# numeric columns are near-fixed; the name column wants room for the widest
# skill/focus/specialization label, up to a cap past which it *wraps* rather than
# claiming the whole panel. Kept lean so a second column appears before a lone one
# stretches wide and leaves a big gap between names and their numbers. The four that
# set the block's density are theme metrics — a denser preset wants narrower ones —
# read through spin_width()/name_min_width()/name_max_width()/mod_width().
NAME_PADDING = 16
FRAME_PADDING = 16
# The fixed share of the ABL and Total columns, either side of the rank spin box.
NUMERIC_PADDING = 40 + 24
#: Cell padding around the Ability column's short code. The column itself is
#: *measured* (see ``_ability_col_width``) rather than being another constant here:
#: it is the one near-fixed column whose content comes from the ruleset, so a mod
#: with longer ability codes must widen the panel rather than squeeze the names.
ABILITY_PADDING = 16


def spin_width() -> int:
    """Width cap for the numeric spin-box columns, so they don't hog the row."""
    return int(theme.metric("column.skill.spin"))


def name_min_width() -> int:
    """Floor for the skill-name column, before the widest label widens it."""
    return int(theme.metric("column.skill.name"))


def name_max_width() -> int:
    """Widest that column grows before a long focus name wraps instead."""
    return int(theme.metric("column.skill.name-max"))


def mod_width() -> int:
    """The derived "+" column's share, added only while that column is shown."""
    return int(theme.metric("column.skill.mod"))


class SkillRowKey(NamedTuple):
    """What one rendered row *is*, for the row menu and the drag.

    Three kinds, and the difference is what may be done to them: a ``"skill"`` row
    moves among the skills and takes its sub-rows with it, while a ``"focus"`` or
    ``"spec"`` row moves only within its own skill. ``row_id`` is the
    ``Character.skill_ranks`` key the row buys ranks under — empty for a focused
    skill's header row, which is a skill with no pool of its own.
    """

    kind: str
    skill: str
    name: str = ""
    row_id: str = ""


class SkillRow(NamedTuple):
    """The cells of one rendered skill row that the refresh pass writes into.

    ``name_item`` is ``None`` when the name cell is a widget rather than a plain item
    (a row carrying an inline add/remove button), so nothing there can be restyled.
    """

    ability_key: str
    row_id: str
    ability_item: QTableWidgetItem
    mod_item: QTableWidgetItem
    total_item: QTableWidgetItem
    name_item: QTableWidgetItem | None


class SkillsSection(ColumnFlowPanels, TitledSection):
    """A table of skills whose total bonuses track the shared character model.

    Ranks and focuses are read from and written to the :class:`Character`; the "+"
    and total columns are computed by :func:`skill_modifiers` / :func:`skill_total`
    rather than in the view. Emits :attr:`changed` when the build changes so the
    sheet can recompute spent power points.
    """

    changed = Signal()

    #: A row was double-clicked — roll that skill (a focus and a specialized pool
    #: each roll their own row). Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`; rolling is not a build edit.
    rollRequested = Signal(object)

    #: A row was clicked once — show that skill in the roller's chip, ready to roll.
    loadRequested = Signal(object)

    #: A row was right-clicked and pinned — carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`. Only ever raised on a sheet a
    #: GM opened from a card (see :meth:`set_pin_target`).
    pinRequested = Signal(object)
    #: The same, for a row that was already on the card.
    unpinRequested = Signal(object)

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._data = data
        self._character = character
        self._skills = data.skills
        self._ability_abbrs: dict[str, str] = {a.key: a.abbr or a.key for a in data.abilities}
        # Ranks and focuses live on the model; ensure every focused skill has a
        # (possibly empty) focus list to render from.
        self._ranks = character.skill_ranks
        self._focuses = character.focuses
        # Specializations (narrow, half-cost pools) can hang off any skill; the model
        # only carries non-empty entries, so read with .get rather than seeding all.
        self._specializations = character.specializations
        for skill in data.skills:
            if skill.focused:
                self._focuses.setdefault(skill.name, [])
        # One SkillRow per rankable row, so the derived cells can be recomputed when
        # abilities or powers change and a condition overlay can restyle the total/name.
        self._rows: list[SkillRow] = []
        # Rank spin boxes rebuilt on every layout pass, tracked so the lock state can
        # be re-applied to them.
        self._editable_spins: list[QSpinBox] = []
        self._locked = False
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        # Whether any row currently carries an outside bonus; drives the "+" column's
        # visibility (and, through _min_col_width, how many panels fit).
        self._show_mods = False
        # Rows a power or item granted that the character never bought — an Enhanced
        # Trait naming a focus they have no row for. Re-read on every rebuild, and
        # remembered so refresh_granted can tell a real change from a redundant signal.
        self._granted_signature: tuple[tuple[str, int], ...] = ()
        # Which model item each rendered row stands for, and the ordering gestures
        # over it. One controller for every panel: they are one ordered list split
        # for display, so a row has to be draggable from one into another.
        self._row_refs = RowIndex()
        self._sort_mode = SORT_MANUAL
        self._reorder = RowReorder(
            ROW_MIME,
            self._row_refs,
            self._on_row_moved,
            enabled=lambda: not self._locked and self._sort.reorder_enabled(),
            accepts=self._accepts_drop,
        )

        layout = QVBoxLayout(self)

        # Sort control plus the button that puts the whole block back to the
        # ruleset's own order and set (hidden while locked).
        self._controls = QWidget()
        controls = QHBoxLayout(self._controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self._sort = SortControl(
            [
                (SORT_MANUAL, "Manual"),
                (SORT_ALPHA, "Name (A–Z)"),
                (SORT_ABILITY, "Ability"),
                (SORT_TOTAL, "Total (high→low)"),
                (SORT_RANK, "Rank (high→low)"),
            ]
        )
        self._sort.sortChanged.connect(self._on_sort_changed)
        # The combo itself, for anything that drives the block by picking a mode.
        self._sort_combo = self._sort.combo
        controls.addWidget(self._sort)
        self._reset_button = QToolButton()
        self._reset_button.setText("↺")
        self._reset_button.setAutoRaise(True)
        self._reset_button.setToolTip(
            "Put every skill back in the ruleset's own order, including any that were "
            "removed. Ranks a removal dropped do not come back."
        )
        self._reset_button.clicked.connect(self.reset_skills)
        controls.addWidget(self._reset_button)
        controls.addStretch()
        layout.addWidget(self._controls)

        # The skills fan out across a variable number of side-by-side panels; the
        # count adapts to the block's width (see ColumnFlowPanels).
        self._init_flow_panels(layout)
        self._rebuild()

    def _make_table(self) -> AutoHeightTable:
        # The table never scrolls itself; it reports its rows as its size, and shares
        # its width equally with the sibling panels while keeping that fitted height,
        # so panels of different heights top-align rather than stretch. No fit_width:
        # a panel is one of several, and the section caps its own minimum at one of
        # them (see ColumnFlowPanels.minimumSizeHint).
        #
        # word_wrap: the name column stretches, so a label longer than its share has
        # to break. Without it the row stayed one line tall and Qt painted "…"
        # instead — "    Expertise: Interstellar Xenobiology (specialized)" cut off
        # mid-word, with nothing on screen saying so.
        table = AutoHeightTable(0, len(HEADERS), word_wrap=True)
        table.setWordWrap(True)
        table.setHorizontalHeaderLabels(HEADERS)
        table.verticalHeader().setVisible(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        # The panels fit their content and never scroll, so keep them out of the
        # focus chain; the wheel then always falls through to the page scroll.
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Connected once per table, which outlives the frequent _rebuild: the tables
        # themselves are reused, only their items are rebuilt.
        table.cellDoubleClicked.connect(
            lambda row, _col, t=table: self._emit_row_spec(t, row, self.rollRequested)
        )
        # A single click loads the skill into the roller's chip without throwing the
        # die. It fires first on a double-click too, which is harmless: rolling loads
        # the same spec anyway. See build_stat_table for the same bargain.
        table.cellClicked.connect(
            lambda row, _col, t=table: self._emit_row_spec(t, row, self.loadRequested)
        )
        # A row menu with two independent entries: the same "Pin to GM card" the two
        # stat tables offer, off the same stashed payload (this table builds itself
        # rather than going through build_stat_table), and this block's own Remove.
        install_row_menu(
            table,
            pin_menu_contributor(
                self._pin_ref,
                self.pinRequested.emit,
                self.unpinRequested.emit,
                self._pins,
            ),
            remove_contributor(self._remove_label, self._remove_row),
        )
        # A panel built later by _ensure_tables is wired here too, so every panel is
        # both a source of dragged rows and a target for them.
        self._reorder.attach(table)
        guard_wheel(table)
        return table

    def _emit_row_spec(self, table: QTableWidget, row: int, signal: Signal) -> None:
        """Send this row's roll to *signal*, if the row is one that rolls.

        Every column but Rank arrives here — that one is a spin box cell widget,
        which eats the clicks itself (and unlocked would want them for editing
        anyway).
        """
        item = table.item(row, COL_TOTAL)
        payload = None if item is None else item.data(ROLL_ROLE)
        if not payload:
            return
        row_id, display = payload
        signal.emit(skill_roll(self._character, self._data, row_id, label=display))

    @staticmethod
    def _pin_ref(payload: object) -> PinRef | None:
        """The pin that names this row. Its ``(row_id, display)`` payload is the
        roll's, so a pinned skill is exactly the row that was right-clicked — a
        focus and its parent skill are different rows and stay different pins."""
        if not isinstance(payload, tuple) or not payload:
            return None
        return PinRef(PIN_SKILL, str(payload[0]))

    def set_pin_target(self, enabled: bool) -> None:
        """Whether this block's rows offer to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so a row can offer Unpin."""
        self._pins.set_pinned(refs)

    # -- which skills, in what order -----------------------------------------

    def _ordered_skill_names(self) -> list[str]:
        """Every skill name in the player's order, hidden ones included.

        The same three-part rule the Equipment block's group order follows: the
        stored order wins as far as it goes, names it doesn't mention trail in the
        ruleset's own order, and a stored name the ruleset no longer has is kept
        rather than dropped — so a skill from a mod that is off today comes back
        where the player left it rather than at the end.
        """

        catalog = [skill.name for skill in self._skills]
        stored = self._character.skill_order
        ordered = list(stored)
        ordered.extend(name for name in catalog if name not in stored)
        return ordered

    def _visible_skills(self) -> list[Skill]:
        """The skills this block draws: the player's order, minus what they removed."""

        by_name = {skill.name: skill for skill in self._skills}
        hidden = set(self._character.hidden_skills)
        return [
            by_name[name]
            for name in self._ordered_skill_names()
            if name in by_name and name not in hidden
        ]

    def reseed(self) -> None:
        """Re-render the rows from the model — the sheet put an earlier state back.

        Deliberately *only* the render: the constructor also seeds an empty focus list
        per focused skill, and a reseed that writes the model would make the undo
        controller see a change it did not make and record a step for it.
        """
        self._rebuild()

    # -- data-driven rebuild -------------------------------------------------

    def _rebuild(self) -> None:
        self._rows.clear()
        self._editable_spins.clear()
        self._row_refs.clear()
        skills = self._visible_skills()
        count = self._flow_column_count()
        self._column_count = count
        self._ensure_tables(count)
        for table, bucket in zip(self._tables, self._split_blocks(skills, count), strict=True):
            specs = self._expand(bucket)
            table.setRowCount(0)
            table.clearSpans()
            table.setRowCount(len(specs))
            self._render_side(table, specs)
            table.setColumnHidden(COL_MODS, not self._show_mods)
            table.updateGeometry()

        self._apply_lock()
        self._refresh_totals()
        # Last, and that order matters: _refresh_totals is what finally fills the
        # ABL/+/Total cells, so it is what settles those ResizeToContents columns and
        # therefore what the stretching name column is left with. Measuring the
        # wrapped rows any earlier fits them to a column wider than they get.
        for table in self._tables:
            table.remeasure_wrapped_rows()

    def _split_blocks(self, skills: list[Skill], count: int) -> list[list[Skill]]:
        """Divide *skills* into *count* ordered groups of near-equal height.

        Each skill is a block whose height is one row, plus one row per focus for
        focused skills and one per specialization for any skill; blocks are never
        split across groups.
        """

        granted = self._granted_rows()
        sizes = []
        for skill in skills:
            size = 1 + len(self._focuses.get(skill.name, [])) if skill.focused else 1
            size += len(self._specializations.get(skill.name, []))
            size += sum(1 for row_id in granted if split_trait_key(row_id)[0] == skill.name)
            sizes.append(size)
        return [[skills[i] for i in bucket] for bucket in even_split(sizes, count)]

    # -- responsive panel count ---------------------------------------------

    def _flow_item_count(self) -> int:
        return len(self._visible_skills())

    def _name_labels(self) -> Iterator[str]:
        """Every label the name column has to hold, in no particular order.

        The same strings :meth:`_expand` renders, so what the panel is sized for
        and what is drawn in it cannot drift. A focus or specialization row carries
        its skill's name as well as its own and is always the longest of them.
        """

        granted = self._granted_rows()
        for skill in self._visible_skills():
            yield skill.name
            for focus in self._focuses.get(skill.name, []):
                yield f"    {skill.name}: {focus}"
            for spec in self._specializations.get(skill.name, []):
                yield f"    {skill.name}: {spec} (specialized)"
            for row_id in granted:
                if split_trait_key(row_id)[0] == skill.name:
                    yield f"    {trait_display_name(self._data, row_id)}"

    def _min_col_width(self) -> int:
        """Narrowest a panel may get before a skill name would clip.

        The name column's share is the widest label actually present, floored at
        the block's density metric and *capped*: past the cap a long focus name
        wraps onto a second line rather than pushing the whole block wide enough to
        print it on one, which used to collapse the flow to a single panel. Plus
        the near-fixed numeric columns — every one of them, which is what the
        docstring on :meth:`_ability_col_width` is about.
        """

        name_width = wrapping_column_width(
            self.fontMetrics(),
            self._name_labels(),
            padding=NAME_PADDING,
            cap=name_max_width(),
            floor=name_min_width(),
        )
        mods = mod_width() if self._show_mods else 0
        numeric = NUMERIC_PADDING + spin_width() + self._ability_col_width()
        return name_width + numeric + mods + FRAME_PADDING

    def _ability_col_width(self) -> int:
        """The Ability column's share: its own header, or the widest short code.

        Budgeted here because it was not, and a column left out of this sum is a
        column the *name* column silently pays for: the flow fitted one panel too
        many and the stretching name column absorbed the whole shortfall, which is
        what clipped a long focus name.
        """

        fm = self.fontMetrics()
        codes = max(
            (fm.horizontalAdvance(abbr) for abbr in self._ability_abbrs.values()), default=0
        )
        return max(fm.horizontalAdvance(HEADERS[COL_ABILITY]), codes) + ABILITY_PADDING

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_column_count()

    def _granted_rows(self) -> dict[str, str]:
        """Granted skill rows the character has no row of their own for, ``row_id -> source``.

        An Enhanced Trait may raise *Expertise: Stealth* on a hero who bought no
        Expertise. The boost is real and paid for, and without a row here it would be
        invisible — so the block grows one, muted and un-editable, the way the Advantages
        block shows a granted advantage.
        """

        return {
            row_id: ", ".join(bonus.sources)
            for row_id, bonus in granted_skill_rows(self._character, self._data).items()
        }

    def refresh_granted(self) -> None:
        """Re-render when the granted rows have changed, and restate the totals either way.

        Wired to the enhancements topic, which fires whenever a power is toggled, edited
        or re-costed. Most of those move numbers only, and a rebuild would cost the block
        its selection and its scroll — so the rows are rebuilt only when the granted
        *set* actually moved, and every other signal is the ordinary totals refresh this
        replaced.
        """

        signature = tuple(sorted((row_id, 0) for row_id in self._granted_rows()))
        if signature == self._granted_signature:
            self.refresh_totals()
            return
        self._granted_signature = signature
        self._rebuild()

    def _expand(self, skills: list[Skill]) -> list[tuple]:
        """Flatten skills into per-row specs.

        A focused skill yields a header row followed by one row per focus; a plain
        skill yields a single row. Either way, any specialized pools follow as extra
        indented ``"spec"`` rows, and any rows a power *granted* (:meth:`_granted_rows`)
        follow those.
        """

        granted = self._granted_rows()
        specs: list[tuple] = []
        for skill in skills:
            if skill.focused:
                specs.append(("header", skill))
                for focus in self._focuses.get(skill.name, []):
                    display = f"{skill.name}: {focus}"
                    row_id = f"{skill.name}::{focus}"
                    specs.append(("focus", skill, display, row_id, focus))
            else:
                specs.append(("skill", skill, skill.name, skill.name))
            for spec in self._specializations.get(skill.name, []):
                display = f"{skill.name}: {spec} (specialized)"
                row_id = f"{skill.name}::spec::{spec}"
                specs.append(("spec", skill, display, row_id, spec))
            for row_id, source in granted.items():
                if split_trait_key(row_id)[0] == skill.name:
                    display = trait_display_name(self._data, row_id)
                    specs.append(("granted", skill, display, row_id, source))
        return specs

    def _render_side(self, table: QTableWidget, specs: list[tuple]) -> None:
        for row, spec in enumerate(specs):
            kind = spec[0]
            if kind == "header":
                self._render_group_header(table, row, spec[1])
            elif kind == "spec":
                _, skill, display, row_id, spec_name = spec
                self._render_skill_row(
                    table, row, skill, display, row_id, indent=True, spec_name=spec_name
                )
            elif kind == "focus":
                _, skill, display, row_id, focus_name = spec
                self._render_skill_row(
                    table, row, skill, display, row_id, indent=True, focus_name=focus_name
                )
            elif kind == "granted":
                _, skill, display, row_id, source = spec
                self._render_skill_row(
                    table, row, skill, display, row_id, indent=True, granted_source=source
                )
            else:
                _, skill, display, row_id = spec
                self._render_skill_row(table, row, skill, display, row_id, can_specialize=True)

    def _render_group_header(self, table: QTableWidget, row: int, skill: Skill) -> None:
        """Header cell block with 'Add focus' / 'Add specialization' for a focused skill."""

        # A focused skill's header row *is* that skill's row: it is what is dragged
        # to move the skill and what is right-clicked to remove it. It buys no ranks
        # of its own, hence the empty row id.
        self._row_refs.add(table, row, SkillRowKey("skill", skill.name))

        # In the locked (read-only) view there's nothing to add, so the header
        # is just the skill name in its own cell, with no buttons.
        if self._locked:
            table.setItem(row, COL_NAME, readonly_item(skill.name))
            return

        add_focus = QPushButton("Add focus…")
        add_focus.clicked.connect(lambda _=False, s=skill: self._add_focus(s))
        add_spec = QPushButton("Add specialization…")
        add_spec.clicked.connect(lambda _=False, s=skill: self._add_specialization(s))
        host = QWidget()
        hbox = QHBoxLayout(host)
        hbox.setContentsMargins(4, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(QLabel(skill.name))
        hbox.addWidget(add_focus)
        hbox.addWidget(add_spec)
        hbox.addStretch()
        # Spanned across **every** column, the skill's name included, and that is the
        # point rather than a tidiness: a ``ResizeToContents`` column measures a
        # spanned cell widget as if it were that column's own content, so parking the
        # buttons on the Ability column made it as wide as they are — half again what
        # a three-letter code needs — and the *name* column, the one that stretches,
        # paid for it out of the room a focus name was relying on. Column 0 stretches,
        # so it is the one column a wide widget cannot distort.
        table.setSpan(row, COL_NAME, 1, len(HEADERS))
        table.setCellWidget(row, COL_NAME, host)

    def _render_skill_row(
        self,
        table: QTableWidget,
        row: int,
        skill: Skill,
        display: str,
        row_id: str,
        indent: bool = False,
        can_specialize: bool = False,
        spec_name: str | None = None,
        focus_name: str | None = None,
        granted_source: str = "",
    ) -> None:
        """Render one rankable row.

        ``granted_source`` names the power or item that granted a row the character never
        bought (see :meth:`_granted_rows`). Such a row buys nothing, so it gets a
        read-only ranks cell in place of a spin and is muted throughout; it is kept out of
        ``_row_refs`` too, since reordering or removing it would promise an edit the block
        cannot make. It keeps its Total cell and its roll payload — a granted focus is a
        real skill and is rolled like one.
        """

        name_item = self._render_name_cell(
            table, row, skill, display, indent, can_specialize and not granted_source
        )
        if granted_source:
            pass  # not a model row: nothing addresses it, so it names no SkillRowKey
        elif spec_name is not None:
            self._row_refs.add(table, row, SkillRowKey("spec", skill.name, spec_name, row_id))
        elif focus_name is not None:
            self._row_refs.add(table, row, SkillRowKey("focus", skill.name, focus_name, row_id))
        else:
            self._row_refs.add(table, row, SkillRowKey("skill", skill.name, "", row_id))

        abbr = self._ability_abbrs.get(skill.ability, skill.ability)
        table.setItem(row, COL_ABILITY, readonly_item(abbr, center=True))

        ability_rank_item = readonly_item("", center=True)
        table.setItem(row, COL_ABILITY_RANK, ability_rank_item)

        if granted_source:
            table.setItem(row, COL_RANKS, readonly_item("—", center=True))
        else:
            ranks_spin = make_spin_box(
                RANK_MIN,
                RANK_MAX,
                value=self._ranks.get(row_id, 0),
                buttons=False,
                max_width=spin_width(),
            )
            ranks_spin.valueChanged.connect(
                lambda value, rid=row_id: self._on_rank_changed(rid, value)
            )
            table.setCellWidget(row, COL_RANKS, ranks_spin)
            self._editable_spins.append(ranks_spin)

        # Imposed from outside, never typed in — a read-only cell whose text, tint and
        # tooltip are all filled by _refresh_totals.
        mod_item = readonly_item("", center=True)
        table.setItem(row, COL_MODS, mod_item)

        total_item = readonly_item("", center=True)
        # What this table row rolls, carried on the Total cell. A double-click reads
        # it back from there whichever column was hit (see _on_cell_double_clicked);
        # a group header row has no Total cell at all, which is exactly how it says
        # "nothing to roll here".
        total_item.setData(ROLL_ROLE, (row_id, display))
        table.setItem(row, COL_TOTAL, total_item)
        if granted_source:
            muted = QBrush(QColor(theme.color("text.muted.rich")))
            for column in range(len(HEADERS)):
                item = table.item(row, column)
                if item is not None:
                    item.setForeground(muted)
            if name_item is not None:
                name_item.setToolTip(f"{display} — granted by {granted_source}")
        self._rows.append(
            SkillRow(skill.ability, row_id, ability_rank_item, mod_item, total_item, name_item)
        )

    def _render_name_cell(
        self,
        table: QTableWidget,
        row: int,
        skill: Skill,
        display: str,
        indent: bool,
        can_specialize: bool,
    ) -> QTableWidgetItem | None:
        """The skill's name cell, with an inline ``＋`` where one belongs.

        A plain read-only label unless (and only while unlocked) the row can grow a
        specialized (half-cost) pool, which a non-focused skill's main row can: that
        is an *add* affordance with nowhere else discoverable to live. Removing is
        not here at all — it is the row's right-click menu, which is why a focus and
        a specialization row are now plain cells and can be struck through by the
        condition overlay like every other row. Returns the name
        :class:`QTableWidgetItem` for a plain cell, or ``None`` for a widget cell.
        """

        name = ("    " if indent else "") + display
        if self._locked or not can_specialize:
            item = readonly_item(name)
            # Every plain name cell, not just the indented ones it started on. A
            # focus or pool carries its skill's name as well as its own, so it is
            # still the longest label here and the one _min_col_width is really
            # sizing the panel for — but that sum is a heuristic and the column is
            # capped, so any name can end up wrapped or, if it is one long word Qt
            # will not break, elided. This is the one place to read it whole.
            item.setToolTip(display)
            table.setItem(row, COL_NAME, item)
            return item

        # A plain QLabel, not a wrapping one: this cell only ever holds a bare
        # skill name, which fits under name_max_width() comfortably, and a widget
        # inside a cell is not reliably height-negotiated by resizeRowToContents.
        # The long labels are the indented focus/specialization rows above, which
        # are plain items and do wrap.
        add = QPushButton("＋")
        add.setFlat(True)
        add.setFixedWidth(20)
        add.setToolTip("Add a specialized (half-cost) rank pool for this skill")
        add.clicked.connect(lambda _=False, s=skill: self._add_specialization(s))
        host = QWidget()
        hbox = QHBoxLayout(host)
        hbox.setContentsMargins(4, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(QLabel(name))
        # Beside the name rather than pushed to the far side of the column: it means
        # "add one of these to *this* skill", and the name is what it is pointing at.
        # It used to be right-aligned, which read as attached only because the column
        # was too narrow for the gap to show.
        hbox.addWidget(add)
        hbox.addStretch()
        table.setCellWidget(row, COL_NAME, host)
        return None

    # -- interaction ---------------------------------------------------------

    def _add_focus(self, skill: Skill) -> None:
        """Ask for a focus, offering the catalog's suggestions but accepting anything.

        ``getItem`` rather than ``getText`` so a skill with an enumerable set of focuses
        (Close Combat's weapon groups, Performance's arts) offers them; it stays editable,
        because the list is a suggestion and never a closed one. A skill whose focuses
        cannot be listed (Expertise, Languages) carries a ``focus_note`` instead, which
        becomes the prompt — the same split the Enhanced Trait picker reads.
        """

        taken = self._focuses[skill.name]
        options = [f for f in skill.focuses if f not in taken]
        prompt = skill.focus_note or "Focus:"
        focus, ok = QInputDialog.getItem(self, f"Add {skill.name} focus", prompt, options, 0, True)
        focus = focus.strip()
        if ok and focus and focus not in self._focuses[skill.name]:
            self._focuses[skill.name].append(focus)
            self._rebuild()
            self.changed.emit()

    def _remove_focus(self, skill: Skill, focus: str) -> None:
        focuses = self._focuses.get(skill.name, [])
        if focus not in focuses:
            return
        focuses.remove(focus)
        self._ranks.pop(f"{skill.name}::{focus}", None)
        self._rebuild()
        self.changed.emit()

    def _add_specialization(self, skill: Skill) -> None:
        """Ask for a specialized (half-cost) rank pool, the way :meth:`_add_focus` asks
        for a focus: the catalog's common uses of the skill as ready choices, its
        ``specialization_note`` as the prompt where those cannot be listed ("By specific
        sense"), and free text past both — a pool is whatever the player narrowed to,
        and the list was never a closed one.
        """

        taken = self._specializations.get(skill.name, [])
        options = [name for name in skill.specializations if name not in taken]
        prompt = skill.specialization_note or "Specialization:"
        name, ok = QInputDialog.getItem(
            self, f"Add {skill.name} specialization", prompt, options, 0, True
        )
        name = name.strip()
        if not ok or not name:
            return
        # Only reach for the list once the dialog was actually accepted: a bare
        # ``setdefault`` on the cancel path leaves an empty entry on the model, which
        # ``_remove_specialization`` goes out of its way to avoid and which would then
        # ride along into the saved JSON.
        specs = self._specializations.setdefault(skill.name, [])
        if name not in specs:
            specs.append(name)
            self._rebuild()
            self.changed.emit()

    def _remove_specialization(self, skill: Skill, spec_name: str) -> None:
        specs = self._specializations.get(skill.name, [])
        if spec_name not in specs:
            return
        specs.remove(spec_name)
        if not specs:  # keep the model tidy — drop the now-empty entry
            self._specializations.pop(skill.name, None)
        self._ranks.pop(f"{skill.name}::spec::{spec_name}", None)
        self._rebuild()
        self.changed.emit()

    # -- removing a row ------------------------------------------------------

    def _skill_by_name(self, name: str) -> Skill | None:
        return next((skill for skill in self._skills if skill.name == name), None)

    def _remove_label(self, table: QTableWidget, row: int) -> str | None:
        """How the row menu words its Remove entry — ``None`` while locked."""

        if self._locked:
            return None
        key = self._row_refs.key_at(table, row)
        if not isinstance(key, SkillRowKey):
            return None
        if key.kind == "focus":
            return f"Remove {key.skill}: {key.name}"
        if key.kind == "spec":
            return f"Remove {key.skill}: {key.name} (specialized)"
        return f"Remove {key.skill}"

    def _remove_row(self, table: QTableWidget, row: int) -> None:
        key = self._row_refs.key_at(table, row)
        if not isinstance(key, SkillRowKey):
            return
        skill = self._skill_by_name(key.skill)
        if skill is None:
            return
        if key.kind == "focus":
            self._remove_focus(skill, key.name)
        elif key.kind == "spec":
            self._remove_specialization(skill, key.name)
        else:
            self.remove_skill(key.skill)

    def _skill_has_anything_bought(self, name: str) -> bool:
        """Whether removing *name* would cost the player something they bought."""

        if self._focuses.get(name) or self._specializations.get(name):
            return True
        prefix = f"{name}::"
        return any(
            rank
            for row_id, rank in self._ranks.items()
            if row_id == name or row_id.startswith(prefix)
        )

    def remove_skill(self, name: str, *, confirm: bool = True) -> None:
        """Take a whole skill off the sheet, and what was bought on it with it.

        The seam the row menu lands on. Unlike a focus — one narrow pool, cheaply
        rebought — a skill can carry ranks, focuses *and* specializations, so this
        **asks first** whenever there is anything to lose. It is the one gesture on
        this block that cannot be undone: :meth:`reset_skills` brings the row back,
        never the ranks.

        ``skill_order`` deliberately keeps the name, so a restored skill returns to
        where the player had put it rather than to the end.
        """

        if name in self._character.hidden_skills:
            return
        if confirm and self._skill_has_anything_bought(name):
            answer = QMessageBox.question(
                self,
                f"Remove {name}",
                f"{name} has ranks, focuses or specializations bought on it. "
                "Removing it drops them, and they cannot be brought back.\n\n"
                "Remove it anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._character.hidden_skills.append(name)
        self._focuses.pop(name, None)
        self._specializations.pop(name, None)
        prefix = f"{name}::"
        for row_id in [k for k in self._ranks if k == name or k.startswith(prefix)]:
            del self._ranks[row_id]
        self._rebuild()
        self.changed.emit()

    def reset_skills(self) -> None:
        """Put every skill back, in the ruleset's own order.

        Both halves of the block's display state go at once — the hand order and
        what was removed — because they are one question ("show me the sheet the
        ruleset ships") and answering half of it leaves a puzzle. Ranks a removal
        dropped stay dropped; the tooltip on the button says so.
        """

        if not self._character.skill_order and not self._character.hidden_skills:
            self._sort.set_mode(SORT_MANUAL)
            self._sort_mode = SORT_MANUAL
            return
        self._character.skill_order = []
        self._character.hidden_skills = []
        for skill in self._skills:
            if skill.focused:
                self._focuses.setdefault(skill.name, [])
        self._sort.set_mode(SORT_MANUAL)
        self._sort_mode = SORT_MANUAL
        self._rebuild()
        self.changed.emit()

    # -- ordering ------------------------------------------------------------

    def _on_sort_changed(self, mode: str) -> None:
        self._sort_mode = mode
        # Only Manual mode offers hand reordering, which RowReorder asks about at
        # gesture time (see the `enabled` predicate it was built with).
        if mode == SORT_MANUAL:
            return  # nothing to reorder; the current order stands
        self._apply_sort()
        self._rebuild()
        self.changed.emit()  # a preset rewrites the saved order — mark it an edit

    def _apply_sort(self) -> None:
        """Rewrite ``skill_order`` for the current preset mode.

        Permanent, like the Advantages block's: the new order is the one that
        saves. Total and Rank are therefore a *snapshot* — they order the block by
        what those numbers are now and do not follow later edits, which is the price
        of the order being a stored fact rather than a live view.
        """

        keys = {
            SORT_ALPHA: lambda name: (name.lower(),),
            SORT_ABILITY: lambda name: (self._ability_of(name), name.lower()),
            SORT_TOTAL: lambda name: (-self._best_of(name, self._row_total), name.lower()),
            SORT_RANK: lambda name: (-self._best_of(name, self._row_rank), name.lower()),
        }
        key = keys.get(self._sort_mode)
        if key is None:
            return
        names = self._ordered_skill_names()
        names.sort(key=key)
        self._character.skill_order = names

    def _ability_of(self, name: str) -> str:
        skill = self._skill_by_name(name)
        return "" if skill is None else self._ability_abbrs.get(skill.ability, skill.ability)

    def _row_ids_of(self, name: str) -> list[str]:
        """Every rank-buying row this skill owns — its own, its focuses, its pools."""

        skill = self._skill_by_name(name)
        rows = [] if skill is not None and skill.focused else [name]
        rows += [f"{name}::{focus}" for focus in self._focuses.get(name, [])]
        rows += [f"{name}::spec::{spec}" for spec in self._specializations.get(name, [])]
        return rows

    def _best_of(self, name: str, of_row) -> int:
        """The highest reading over the skill's rows — a focused skill has no own row."""

        return max((of_row(row_id) for row_id in self._row_ids_of(name)), default=0)

    def _row_rank(self, row_id: str) -> int:
        return self._ranks.get(row_id, 0)

    def _row_total(self, row_id: str) -> int:
        return skill_total(self._character, self._data, row_id)

    def _accepts_drop(self, source: RowEntry, target: RowEntry, before: bool) -> bool:
        """Whether this pairing is a move at all (see :meth:`_on_row_moved`)."""

        held, over = source.key, target.key
        if not isinstance(held, SkillRowKey) or not isinstance(over, SkillRowKey):
            return False
        if held.kind == "skill":
            # A skill lands beside another skill; dropping it on a sub-row means
            # "beside that sub-row's skill", so only its own block is no move.
            return over.skill != held.skill
        # A focus or a pool belongs to its skill and moves only among its own kind.
        return over.kind == held.kind and over.skill == held.skill and over.name != held.name

    def _on_row_moved(self, source: RowEntry, target: RowEntry, before: bool) -> None:
        held, over = source.key, target.key
        if held.kind == "skill":
            # Dropped on a sub-row: that names the enclosing skill, and there is no
            # "before" half of a row that isn't the skill's own.
            self.move_skill(held.skill, over.skill, before and over.kind == "skill")
            return
        self.move_sub_row(held, over.name, before)

    def move_skill(self, name: str, target: str, before: bool) -> None:
        """Move the skill *name* so it sits either side of the skill *target*.

        The seam a dragged skill row lands on, and the headless-testable one: it
        takes two names rather than a drop position. Its focus and specialization
        rows travel with it, because they are rendered *from* it and were never
        separately ordered.
        """

        order = self._ordered_skill_names()
        if name == target or name not in order or target not in order:
            return
        move_within(order, order.index(name), order.index(target) + (0 if before else 1))
        self._character.skill_order = order
        self._rebuild()
        self.changed.emit()

    def move_sub_row(self, held: SkillRowKey, target: str, before: bool) -> None:
        """Move a focus or specialized pool among its own skill's, in place."""

        items = (
            self._focuses.get(held.skill)
            if held.kind == "focus"
            else self._specializations.get(held.skill)
        )
        if not items or held.name not in items or target not in items or held.name == target:
            return
        move_within(items, items.index(held.name), items.index(target) + (0 if before else 1))
        self._rebuild()
        self.changed.emit()

    def set_locked(self, locked: bool) -> None:
        """Make the rank spin boxes read-only labels and drop the 'Add focus'
        buttons while locked.

        Rebuilds the tables so the focus buttons are omitted entirely: they live
        in table cells, where toggling visibility isn't reliable. The two row
        gestures need no rebuild — the drag asks ``_locked`` through the predicate
        :class:`RowReorder` was built with, and the Remove entry through
        :meth:`_remove_label`, which words nothing while locked.
        """
        self._locked = locked
        self._controls.setVisible(not locked)
        self._rebuild()

    def _apply_lock(self) -> None:
        """Apply the current lock state to the spin boxes built by the last
        rebuild. (Focus buttons are omitted at build time when locked.)"""
        for spin in self._editable_spins:
            set_widget_locked(spin, self._locked)

    def _on_rank_changed(self, row_id: str, value: int) -> None:
        self._ranks[row_id] = value
        self._refresh_totals()
        self.changed.emit()

    # -- totals --------------------------------------------------------------

    def refresh_totals(self) -> None:
        """Recompute every skill total — the sheet calls this when powers change,
        since an Enhanced-Trait boost to a linked ability or the skill itself moves
        the total."""

        self._refresh_totals()

    def _refresh_totals(self) -> None:
        mods = {
            row.row_id: skill_modifiers(self._character, self._data, row.row_id)
            for row in self._rows
        }
        if any(m.has_flat_modifier for m in mods.values()) != self._show_mods:
            # The "+" column just appeared or emptied out. It changes how wide a panel
            # needs to be, so rebuild rather than only toggling the column; the rebuild
            # ends by calling back here, now with the two in agreement.
            self._show_mods = not self._show_mods
            self._rebuild()
            return

        for row in self._rows:
            mod = mods[row.row_id]
            # The ABL column shows the *effective* ability (with any power boost) so
            # the row's columns still sum to the total.
            ability = effective_ability(self._character, self._data, row.ability_key)
            total = skill_total(self._character, self._data, row.row_id)
            row.ability_item.setText(str(ability))
            self._fill_modifier_cell(row.mod_item, mod)
            # A scoped Impaired/Disabled (or a global one) overlays the total in red,
            # struck through for a lost-trait condition. This is display-only — the
            # build math above (skill_total) is untouched.
            effect = mod.condition
            row.total_item.setText(str(effect.apply(total) if effect.active else total))
            self._style_condition(row.total_item, row.name_item, effect, total)
        # Keep the section title's running point cost current.
        self.set_priced_title("Skills", skill_points_spent(self._character, self._data))

    @staticmethod
    def _fill_modifier_cell(mod_item: QTableWidgetItem, mod: SkillModifiers) -> None:
        """Show a row's net outside modifier as a signed number, explained on hover.

        Green while what grants it raises the row, red once a condition takes part or
        the modifier itself is a penalty (a large creature's Stealth) — matching the
        stat grids — and struck through for a lost-trait condition. Blank for a row
        nothing modifies; the column as a whole hides only when *every* row is blank,
        so a shown column still has empty cells.
        """

        if not mod.has_flat_modifier:
            mod_item.setText("")
            mod_item.setToolTip("")
            tint_item(mod_item, None)
            return

        penalised = mod.condition.active
        mod_item.setText(f"{mod.amount:+d}")

        tips = []
        if mod.grants:
            tips.append(f"{mod.grants.amount:+d} from {', '.join(mod.grants.sources)}")
        if penalised and mod.condition.tooltip:
            tips.append(mod.condition.tooltip)
        mod_item.setToolTip("\n".join(tips))

        tint_item(
            mod_item,
            CONDITION_TINT if penalised else bonus_tint(mod.amount),
            struck=mod.condition.trait_lost,
        )

    @staticmethod
    def _style_condition(total_item, name_item, effect, base_total: int) -> None:
        """Tint the total red (and strike the row) while a condition scopes to it."""

        struck = effect.active and effect.trait_lost
        for item in (total_item, name_item):
            tint_item(item, CONDITION_TINT if effect.active else None, struck=struck)
        if effect.active:
            total_item.setToolTip(f"{base_total} {effect.tooltip}")
        else:
            total_item.setToolTip("")
