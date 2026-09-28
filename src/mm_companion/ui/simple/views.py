"""The simple sheet's own views of the blocks a player only *reads* at the table.

Each is a small widget built from ``(data, character)`` plus, on a live sheet, the
block's section — the funnel anything a player *does* here spends through, so a hero
point, a condition or a roll has one way onto the character whichever view it came
from. Every view exposes ``refresh()``, which redraws from the model; the simple sheet
calls it whenever the sheet's bus says anything changed (see
:mod:`mm_companion.ui.simple.sheet`).

What each one leaves out is the point. No ranks where there is a total, no point
costs, no Power Level limits, no pickers: a trait's *number* is big, what qualifies it
is small print (:mod:`mm_companion.ui.simple.style`), and nothing that is only ever
changed while building is on the page at all.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library
from mm_companion.core.character import AppliedCondition
from mm_companion.core.components import MECH_RANDOM_ACTION
from mm_companion.core.rules import (
    ability_roll,
    advantage_by_name,
    all_advantage_selections,
    character_reach,
    condition_check_penalty,
    condition_speed_lines,
    debilitated_traits,
    effective_size,
    estimated_power_level,
    granted_advantage_selections,
    granted_skill_rows,
    initiative_ability,
    initiative_modifier,
    initiative_roll,
    movement_mode_lines,
    pushed_effects,
    pushed_trait_labels,
    reach_is_altered,
    reach_text,
    resistance_condition_effect,
    resistance_roll,
    skill_bonus,
    skill_modifiers,
    skill_roll,
    speed_columns,
    split_trait_key,
    trait_bonuses,
    trait_display_name,
)
from mm_companion.ui import theme
from mm_companion.ui.advantage_parameters import parameter_display
from mm_companion.ui.damage_row import DamageRow
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.sections.character_image import ScalingImageLabel
from mm_companion.ui.sections.system_info import HeroPointsWidget
from mm_companion.ui.simple.registry import SimpleContext, register_simple_view
from mm_companion.ui.simple.style import (
    name_label,
    set_font,
    signed,
    term_label,
)
from mm_companion.ui.simple.widgets import (
    CaptionBox,
    ColumnGrid,
    SimpleLine,
    StatBox,
    clear_layout,
    make_rollable,
)
from mm_companion.ui.widgets import attach_context_removal, rebuilding


def _vbox(widget: QWidget, spacing: str = "simple.spacing") -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(int(theme.metric(spacing)))
    return layout


def _flow(parent_layout, spacing: int | None = None) -> tuple[FlowContainer, FlowLayout]:
    container = FlowContainer()
    flow = FlowLayout(container, spacing=spacing if spacing is not None else 4)
    flow.setContentsMargins(0, 0, 0, 0)
    parent_layout.addWidget(container)
    return container, flow


def _join(parts) -> str:
    return " · ".join(part for part in parts if part)


class _View(QWidget):
    """The shared shape: a context, and ``refresh()`` redrawing from the model."""

    def __init__(self, context: SimpleContext) -> None:
        super().__init__()
        self.context = context
        self._data = context.data
        self._character = context.character

    def refresh(self) -> None:  # pragma: no cover - every view overrides it
        raise NotImplementedError


# -- Name & Details ------------------------------------------------------------


class ProfileView(_View):
    """The character's name, large, over the rest of who they are in small print.

    The one box with no heading of its own: its name *is* the sheet's heading.
    """

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        self.name = QLabel()
        self.name.setWordWrap(True)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_font(self.name, "size.simple-name", bold=True)
        self.subtitle = name_label(wrap=True)
        self.details = term_label()
        layout.addWidget(self.name)
        layout.addWidget(self.subtitle)
        layout.addWidget(self.details)
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        profile = self._character.profile
        name = library.display_name(self._character)
        self.name.setText(name)
        primary = []
        details = []
        for field in self._data.profile_fields:
            value = str(profile.get(field.key, "")).strip()
            if not value or value == name:
                continue
            (primary if field.primary else details).append(f"{field.label}: {value}")
        self.subtitle.setText(_join(primary))
        self.subtitle.setVisible(bool(primary))
        self.details.setText(_join(details))
        self.details.setVisible(bool(details))


# -- Character Image -----------------------------------------------------------


class _Portrait(ScalingImageLabel):
    """The portrait, asking for a height of its own rather than none at all."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        height = int(theme.metric("simple.portrait"))
        return QSize(int(height * 0.8), height)


class PortraitView(_View):
    """Just the picture, letterboxed into whatever room the row gives it."""

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        self.image = _Portrait()
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.image, stretch=1)
        self._shown: str | None = "unset"
        self.refresh()

    def refresh(self) -> None:
        path = library.resolve_image_path(self._character.image_path)
        if path == self._shown:
            return
        self._shown = path
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self.image.clear_source("No portrait")
        else:
            self.image.set_source(pixmap)


# -- Power Level & System ------------------------------------------------------


class SystemView(_View):
    """What the character *is* at the table: level, initiative, speed, hero points.

    Power Level and Initiative are stat boxes (Initiative rolls like one); the hero
    points are the same pips the edit sheet uses, spent through the System block's
    one funnel so a moved point writes its note into the roll history exactly as a
    click there would. Extra Effort is here for the same reason it is on the edit
    sheet: both of its currencies — the fatigue, and the hero point that shrugs it
    off — belong to this block. The point pool, the cost notice and the Power Level
    limits are build facts and are not drawn at all.
    """

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        _container, self._boxes = _flow(layout)
        self.level = StatBox("PL")
        self.initiative = StatBox("Init")
        self._boxes.addWidget(self.level)
        self._boxes.addWidget(self.initiative)
        make_rollable(
            self.initiative, context, lambda: initiative_roll(self._character, self._data)
        )

        self.hero_points = HeroPointsWidget()
        self.hero_points.valueChanged.connect(self._on_hero_points)
        self._hero_row = CaptionBox("Hero Points", self.hero_points)
        self._boxes.addWidget(self._hero_row)

        self.speed = name_label(wrap=True)
        self.movement = term_label()
        self.facts = term_label()
        layout.addWidget(self.speed)
        layout.addWidget(self.movement)
        layout.addWidget(self.facts)

        self._effort_row = QWidget()
        effort = QHBoxLayout(self._effort_row)
        effort.setContentsMargins(0, 0, 0, 0)
        effort.setSpacing(int(theme.metric("space.sm")))
        self.effort_button = QPushButton("Extra Effort…")
        self.effort_button.setToolTip(
            "Push past your limits (p20). The benefit is immediate; at the start of your "
            "next turn you gain the next rung of the fatigue ladder."
        )
        self.effort_button.clicked.connect(self._show_effort_menu)
        effort.addWidget(self.effort_button)
        self.effort_note = QLabel()
        self.effort_note.setWordWrap(True)
        self.effort_note.setStyleSheet(f"color: {theme.color('tint.warning')};")
        effort.addWidget(self.effort_note, stretch=1)
        layout.addWidget(self._effort_row)
        # Nothing to spend through on paper, so the button is not drawn there.
        self._effort_row.setVisible(context.live and hasattr(context.section, "extra_effort_menu"))
        layout.addStretch()
        self.refresh()

    def _on_hero_points(self, value: int) -> None:
        section = self.context.section
        if section is not None and hasattr(section, "set_hero_points"):
            section.set_hero_points(value)
        else:
            self._character.characteristics["hero_points"] = value

    def _show_effort_menu(self) -> None:
        section = self.context.section
        if section is None:
            return
        menu = section.extra_effort_menu()
        menu.exec(self.effort_button.mapToGlobal(self.effort_button.rect().bottomLeft()))

    def refresh(self) -> None:
        character, data = self._character, self._data
        if self.context.npc:
            self.level.set_value(
                f"~{estimated_power_level(character, data)}",
                tooltip="Estimated from the traits — an NPC is not built to a level.",
            )
        else:
            self.level.set_value(str(character.power_level), tooltip="Power Level")
        modifier = initiative_modifier(character, data)
        penalty = condition_check_penalty(character, data)
        ability = initiative_ability(character, data)
        self.initiative.set_value(
            signed(modifier + penalty),
            moved=-1 if penalty else 0,
            note=ability,
            tooltip=f"Initiative ({ability})",
        )

        self._hero_row.setVisible(not self.context.npc)
        points = int(character.characteristics.get("hero_points", 0) or 0)
        if self.hero_points.value() != points:
            self.hero_points.set_value(points)

        lines = condition_speed_lines(character, data)
        speed_parts = []
        for index, line in enumerate(lines):
            if line.immobilised:
                speed_parts.append(f"{line.label}: immobilised")
                continue
            columns = speed_columns(line.rank, data, ground=index == 0)
            text = " / ".join(c.replace(" feet", " ft").replace(" foot", " ft") for c in columns)
            speed_parts.append(f"{line.label} {text}")
        self.speed.setText("; ".join(speed_parts))
        slowed = any(line.rank_mod or line.immobilised for line in lines)
        self.speed.setStyleSheet(f"color: {theme.color('tint.worse')};" if slowed else "")

        modes = []
        for line in movement_mode_lines(character, data):
            columns = speed_columns(line.rank, data, ground=False)
            modes.append(f"{line.label} " + " / ".join(c.replace(" feet", " ft") for c in columns))
        self.movement.setText(_join(modes))
        self.movement.setVisible(bool(modes))

        facts = [f"Size {effective_size(character, data)}"]
        if reach_is_altered(character, data):
            facts.append(f"Reach {reach_text(character_reach(character, data))}")
        self.facts.setText(_join(facts))

        pushed = [
            f"{label} +{ranks}" for label, ranks in pushed_trait_labels(character, data).items()
        ]
        effects = len(pushed_effects(character))
        if effects:
            pushed.append(f"{effects} effect{'' if effects == 1 else 's'}")
        self.effort_note.setText(("⚡ " + ", ".join(pushed)) if pushed else "")


# -- Abilities / Resistances -----------------------------------------------------


class AbilitiesView(_View):
    """One box per ability: its short code over its effective value.

    The effective value is what a roll uses — a power's boost and a condition's
    penalty already folded in — and it is tinted by which way it moved, since "why is
    my Strength 12" has an answer on the edit sheet and a colour here.
    """

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        _container, flow = _flow(layout)
        self.boxes: dict[str, StatBox] = {}
        for ability in self._data.abilities:
            box = StatBox(ability.abbr or ability.name[:3].upper())
            box.setToolTip(ability.name)
            make_rollable(
                box, context, lambda k=ability.key: ability_roll(self._character, self._data, k)
            )
            flow.addWidget(box)
            self.boxes[ability.key] = box
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        for ability in self._data.abilities:
            spec = ability_roll(self._character, self._data, ability.key)
            bought = int(self._character.abilities.get(ability.key, 0))
            self.boxes[ability.key].set_value(
                signed(spec.modifier) if spec.modifier < 0 else str(spec.modifier),
                moved=spec.modifier - bought,
                tooltip=_hint(ability.name, spec.hint),
            )


def _hint(name: str, hint: str) -> str:
    return f"{name}\n{hint}" if hint else name


class ResistancesView(_View):
    """One box per defence, the same shape as the abilities beside it."""

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        _container, flow = _flow(layout)
        self.boxes: dict[str, StatBox] = {}
        for resistance in self._data.resistances:
            box = StatBox(resistance.abbr or resistance.name)
            box.setToolTip(resistance.name)
            make_rollable(
                box,
                context,
                lambda k=resistance.key: resistance_roll(self._character, self._data, k),
            )
            flow.addWidget(box)
            self.boxes[resistance.key] = box
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        bonuses = trait_bonuses(self._character, self._data).get("resistance", {})
        for resistance in self._data.resistances:
            spec = resistance_roll(self._character, self._data, resistance.key)
            condition = resistance_condition_effect(self._character, self._data, resistance.key)
            bonus = bonuses.get(resistance.key)
            moved = -1 if condition.active else (bonus.amount if bonus else 0)
            self.boxes[resistance.key].set_value(
                str(spec.modifier), moved=moved, tooltip=_hint(resistance.name, spec.hint)
            )


# -- Skills ----------------------------------------------------------------------


def skill_rows(context: SimpleContext) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(trained, untrained)`` skill rows, each ``(row_id, display name)``, in sheet order.

    *Trained* is anything the character has put something into: ranks bought, a focus
    or a specialization, a row a power grants, or a standing bonus from an advantage.
    *Untrained* is every other skill that may be used untrained — a player still rolls
    Perception without a rank in it, so it is on the sheet, in small print.
    """
    character, data = context.character, context.data
    catalog = {skill.name: skill for skill in data.skills}
    ordered = list(character.skill_order) + [
        s.name for s in data.skills if s.name not in character.skill_order
    ]
    hidden = set(character.hidden_skills)
    granted = granted_skill_rows(character, data)
    trained: list[tuple[str, str]] = []
    untrained: list[tuple[str, str]] = []
    for name in ordered:
        skill = catalog.get(name)
        if skill is None or name in hidden:
            continue
        rows: list[tuple[str, str]] = []
        if skill.focused:
            for focus in character.focuses.get(name, []):
                rows.append((f"{name}::{focus}", f"{name}: {focus}"))
        else:
            rows.append((name, name))
        for spec in character.specializations.get(name, []):
            rows.append((f"{name}::spec::{spec}", f"{name}: {spec}"))
        for row_id in granted:
            if split_trait_key(row_id)[0] == name and all(r[0] != row_id for r in rows):
                rows.append((row_id, trait_display_name(data, row_id)))
        for row_id, display in rows:
            has_ranks = int(character.skill_ranks.get(row_id, 0)) > 0
            if has_ranks or row_id in granted or skill_bonus(character, data, row_id) is not None:
                trained.append((row_id, display))
            elif row_id == name and not getattr(skill, "trained_only", False):
                untrained.append((row_id, display))
    return trained, untrained


class SkillsView(_View):
    """The skills a character has trained, name and total, in as many columns as fit.

    The rest — every skill that may still be used untrained — follow as one line of
    small print, each still rollable, because a Perception check nobody bought ranks
    in is still the most-rolled check at most tables.
    """

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        self.grid = ColumnGrid(int(theme.metric("simple.line-column")))
        layout.addWidget(self.grid)
        self.untrained_caption = term_label("Untrained")
        layout.addWidget(self.untrained_caption)
        self._untrained_host, self._untrained = _flow(layout, spacing=6)
        layout.addStretch()
        self.refresh()

    def _line(self, row_id: str, display: str) -> SimpleLine:
        spec = skill_roll(self._character, self._data, row_id, label=display)
        mods = skill_modifiers(self._character, self._data, row_id)
        bonus = skill_bonus(self._character, self._data, row_id)
        moved = -1 if mods.condition.active else (bonus.amount if bonus else 0)
        line = SimpleLine(display, signed(spec.modifier), moved=moved)
        if spec.hint:
            line.setToolTip(spec.hint)
        make_rollable(
            line,
            self.context,
            lambda r=row_id, d=display: skill_roll(self._character, self._data, r, label=d),
        )
        return line

    def refresh(self) -> None:
        with rebuilding(self):
            trained, untrained = skill_rows(self.context)
            self.grid.set_items([self._line(row_id, display) for row_id, display in trained])
            self.grid.setVisible(bool(trained))
            clear_layout(self._untrained)
            for row_id, display in untrained:
                spec = skill_roll(self._character, self._data, row_id, label=display)
                chip = term_label(f"{display} {signed(spec.modifier)}", wrap=False)
                chip.setObjectName("simpleChip")
                make_rollable(
                    chip,
                    self.context,
                    lambda r=row_id, d=display: skill_roll(self._character, self._data, r, label=d),
                )
                self._untrained.addWidget(chip)
            self.untrained_caption.setVisible(bool(untrained))
            self._untrained_host.setVisible(bool(untrained))
            self._untrained_host.refresh_height()


# -- Advantages ------------------------------------------------------------------


class AdvantagesView(_View):
    """Every advantage standing on the character, with its one-line summary under it."""

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        self.grid = ColumnGrid(int(theme.metric("simple.line-column")))
        layout.addWidget(self.grid)
        self.empty = term_label("No advantages")
        layout.addWidget(self.empty)
        layout.addStretch()
        self.refresh()

    def _text(self, selection) -> str:
        advantage = advantage_by_name(self._data, selection.name)
        ranked = bool(advantage and advantage.ranked)
        text = f"{selection.name} {selection.rank}" if ranked else selection.name
        spec = advantage.parameter if advantage else None
        subject = parameter_display(spec, selection.parameter, self._data)
        return f"{text} ({subject})" if subject else text

    def refresh(self) -> None:
        with rebuilding(self):
            lost = debilitated_traits(self._character, self._data)
            sources = {
                (s.name, s.parameter): source
                for s, source in granted_advantage_selections(self._character, self._data)
            }
            lines = []
            for selection in all_advantage_selections(self._character, self._data):
                advantage = advantage_by_name(self._data, selection.name)
                summary = advantage.description if advantage else ""
                source = sources.get((selection.name, selection.parameter))
                if source and selection not in self._character.advantages:
                    summary = _join([f"from {source}", summary])
                line = SimpleLine(
                    self._text(selection), terms=summary, strike=selection.name in lost
                )
                if selection.name in lost:
                    line.setToolTip("Debilitated — this advantage is effectively lost")
                lines.append(line)
            self.grid.set_items(lines)
            self.grid.setVisible(bool(lines))
            self.empty.setVisible(not lines)


# -- Complications -----------------------------------------------------------------


class ComplicationsView(_View):
    """Each complication's name, and its story in small type under it."""

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        self._layout = _vbox(self)
        self.refresh()

    def refresh(self) -> None:
        with rebuilding(self):
            clear_layout(self._layout)
            shown = [c for c in self._character.complications if c.name or c.description]
            for complication in shown:
                name = name_label(complication.name or "Complication", bold=True, wrap=True)
                self._layout.addWidget(name)
                if complication.description:
                    body = QLabel(complication.description)
                    body.setWordWrap(True)
                    body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                    set_font(body, "size.simple-term")
                    self._layout.addWidget(body)
            if not shown:
                self._layout.addWidget(term_label("No complications"))
            self._layout.addStretch()


# -- Conditions --------------------------------------------------------------------


class ConditionsView(_View):
    """The conditions on the character as chips, a "+" to add one, and the damage ladder.

    Conditions are the most-changed thing on a sheet in play, so all of it stays live:
    the "+" opens the same menu the edit sheet's does, a right-click on a chip sheds
    it, and the round buttons apply a failed Toughness save's rung through the core
    resolver — the control a GM's card has had since cards existed, and one a player
    tracking their own hero's injuries wants just as much.
    """

    def __init__(self, context: SimpleContext) -> None:
        super().__init__(context)
        layout = _vbox(self)
        live = context.live and hasattr(context.section, "choose_condition")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(int(theme.metric("space.sm")))
        self.add_button = QToolButton()
        self.add_button.setText("+")
        self.add_button.setToolTip("Add a condition")
        self.add_button.clicked.connect(self._show_menu)
        top.addWidget(self.add_button)
        self.damage = DamageRow(self._data)
        self.damage.set_character(self._character)
        self.damage.setToolTip("A Toughness save against damage: made, or failed by 1, 2, 3")
        self.damage.stepChosen.connect(self._on_damage)
        top.addWidget(self.damage)
        top.addStretch()
        self._top = QWidget()
        self._top.setLayout(top)
        layout.addWidget(self._top)
        self._top.setVisible(live)
        self._chips_host, self._chips = _flow(layout)
        self.empty = term_label("No conditions")
        layout.addWidget(self.empty)
        layout.addStretch()
        self.refresh()

    def _show_menu(self) -> None:
        from mm_companion.ui.sections.conditions import build_condition_menu

        section = self.context.section
        if section is None:
            return
        menu = build_condition_menu(self, self._data, section.choose_condition)
        menu.exec(self.add_button.mapToGlobal(self.add_button.rect().bottomLeft()))

    def _on_damage(self, index: int) -> None:
        section = self.context.section
        if section is not None:
            section.apply_damage_step(index)

    def _chip(self, applied: AppliedCondition) -> QWidget:
        from mm_companion.ui.sections.conditions import condition_display_name, condition_tooltip

        catalog = {c.id: c for c in self._data.conditions}
        record = catalog.get(applied.condition_id)
        name = condition_display_name(applied, record)
        chip = QWidget()
        chip.setObjectName("simpleCondition")
        chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        worse = theme.color("tint.worse")
        chip.setStyleSheet(
            f"#simpleCondition {{ border: {int(theme.metric('border.width'))}px solid {worse};"
            f" border-radius: {int(theme.metric('radius.chip'))}px;"
            f" background: {theme.wash('tint.worse', 0.10)}; }}"
        )
        row = QHBoxLayout(chip)
        pad = int(theme.metric("space.xs"))
        row.setContentsMargins(pad * 2, pad, pad * 2, pad)
        row.setSpacing(pad)
        label = name_label(name, bold=applied.provenance is None)
        if applied.provenance is not None:
            font = label.font()
            font.setItalic(True)
            label.setFont(font)
        row.addWidget(label)
        chip.setToolTip(condition_tooltip(applied, record, catalog))
        section = self.context.section
        if record is not None and MECH_RANDOM_ACTION in record.mechanisms and section is not None:
            rolled = section.confused_roll(applied)
            if rolled:
                row.addWidget(term_label(f"— {rolled}", wrap=False))
            die = QToolButton()
            die.setText("🎲")
            die.setAutoRaise(True)
            die.setToolTip("Roll this turn's random action")
            die.clicked.connect(lambda _c=False, a=applied: self._roll_confused(a))
            row.addWidget(die)
        if section is not None and hasattr(section, "shed_condition"):
            attach_context_removal(chip, lambda a=applied: section.shed_condition(a), what=name)
        return chip

    def _roll_confused(self, applied: AppliedCondition) -> None:
        section = self.context.section
        if section is not None:
            section.roll_confused(applied)
            self.refresh()

    def refresh(self) -> None:
        with rebuilding(self):
            clear_layout(self._chips)
            for applied in self._character.conditions:
                self._chips.addWidget(self._chip(applied))
            has_any = bool(self._character.conditions)
            self._chips_host.setVisible(has_any)
            self._chips_host.refresh_height()
            self.empty.setVisible(not has_any)
            self.damage.refresh()


# -- Notes, on paper ----------------------------------------------------------------


class NotesPrintView(_View):
    """The notes a Notes block has open, as text — what a note is on paper.

    On screen the block is borrowed as it is (an editor, so a player can write in it
    mid-session); an editor is a scrolling box, though, and a print of one is its first
    screenful. Markdown, since that is what a note is written in.
    """

    def __init__(self, context: SimpleContext, key: str = "notes") -> None:
        super().__init__(context)
        self._key = key
        self._layout = _vbox(self, "space.sm")
        self.refresh()

    def refresh(self) -> None:
        from mm_companion.core import notes

        clear_layout(self._layout)
        state = self._character.notes.get(self._key)
        refs = list(state.files) if state is not None else []
        for ref in refs:
            title = name_label(notes.note_title(ref), bold=True, wrap=True)
            self._layout.addWidget(title)
            body = QLabel()
            body.setTextFormat(Qt.TextFormat.MarkdownText)
            body.setText(notes.read_note(ref))
            body.setWordWrap(True)
            body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            set_font(body, "size.simple-label")
            self._layout.addWidget(body)
        if not refs:
            self._layout.addWidget(term_label("No notes open"))


def _notes_print_view(context: SimpleContext) -> QWidget:
    return NotesPrintView(context, context.key or "notes")


def register_base_views() -> None:
    """Register the simple sheet's own views of the base blocks (once, at import)."""
    for key, factory, kwargs in (
        ("base_info", ProfileView, {"heading": False}),
        ("character_image", PortraitView, {"heading": False}),
        ("system_info", SystemView, {}),
        ("abilities", AbilitiesView, {}),
        ("resistances", ResistancesView, {}),
        ("conditions", ConditionsView, {}),
        ("advantages", AdvantagesView, {}),
        ("complications", ComplicationsView, {}),
        ("skills", SkillsView, {}),
    ):
        register_simple_view(key, factory, replace=True, **kwargs)
    # Borrowed: their play-time controls are the point, and too intricate to draw
    # twice. The two below are also no use on paper.
    register_simple_view("dice", printable=False, heading=False, replace=True)
    register_simple_view("scene", printable=False, replace=True)
    register_simple_view("notes", print_factory=_notes_print_view, replace=True)


register_base_views()
