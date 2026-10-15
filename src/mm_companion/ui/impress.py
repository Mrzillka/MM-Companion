"""The Impress action's windows: declaring one, and setting one up and resolving it.

Two seats see two different windows, and neither computes a number — every total,
option and ladder comes from :mod:`mm_companion.core.rules.impress`:

* :class:`ImpressDialog` — the **impresser** picks an impression, a skill and their
  targets off the scene, and sends that to the GM. Off the air there is no GM, so it
  carries the modifiers itself and rolls the check straight into the history, leaving
  a button to resist with (the "request only" way of running it). At a table a player
  only sees it when both they and the GM want them to choose
  (:func:`~mm_companion.ui.action_ui.sets_up_own_actions`); otherwise the Impress goes
  to the GM bare.
* :class:`ImpressWindow` — the **GM** sets the situational modifiers (always theirs to
  set, whoever is impressing) and a bonus or penalty on the impresser's check, sees and
  overrides each target's resistance, adjusts the check per target, and rolls; then
  watches each roll come back in it. What happens in between — the server rolling,
  players being asked, the conditions landing — is
  :mod:`mm_companion.ui.impress_gm`'s, which owns the session plumbing this module stays
  out of.

A targeted player is not asked anything before the roll: once the check is in, they are
asked to roll their resistance (:class:`~mm_companion.ui.action_ui.RollPromptDialog`),
picking how they resist there.
"""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import ActionModifier, GameData, Impression
from mm_companion.core.rules import (
    MODE_REQUEST,
    MODE_RESOLVE,
    ImpressAttempt,
    RollSpec,
    TraitChoice,
    best_choice,
    group_penalty,
    impress_skill_choices,
    impress_spec,
    impression_allowed,
    impression_outcomes,
    modifier_lines,
    modifier_total,
    modifier_value,
    resist_choices,
)
from mm_companion.ui import theme
from mm_companion.ui.action_ui import (
    PHASE_SETUP,
    ActionRow,
    ActionWindow,
    BodyScroll,
    TargetsBox,
    fit_to_screen,
)
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, muted_style, tinted_style

#: What the history and the windows call the action.
ACTION_NAME = "Impress"

#: The ``ActionRequest`` kind a declared Impress travels as.
KIND_IMPRESS = "impress"


#: The roller's Action-row button, as the sheet and the GM window both offer it:
#: ``(key, caption, tooltip)`` — see
#: :meth:`~mm_companion.ui.dice_roller.DiceRollerPanel.set_actions`.
IMPRESS_ACTION = (
    KIND_IMPRESS,
    "Impress…",
    "Use an interaction skill to impose a condition on someone",
)


def new_action_id() -> str:
    """A fresh id tying one attempt's messages together."""
    return uuid.uuid4().hex[:12]


#: The two modes, as the impresser and the GM are offered them.
MODE_CAPTIONS = {
    MODE_RESOLVE: "The GM rolls every resistance and applies the result",
    MODE_REQUEST: "Request only — each target rolls their own resistance",
}


def _signed(value: int) -> str:
    return f"{value:+d}"


def choice_caption(choice: TraitChoice) -> str:
    """A trait choice as a combo shows it: "Deception +9"."""
    return f"{choice.label} {_signed(choice.modifier)}"


def _fill_impressions(
    combo: QComboBox, data: GameData, character: Character | None, current: str = ""
) -> None:
    """List every impression, greying out the ones *character* may not make.

    Shown rather than hidden, so a player can see that Dazed exists and what it
    would take — the tooltip names the advantage.
    """
    combo.blockSignals(True)
    combo.clear()
    model = combo.model()
    for impression in data.impress.impressions:
        combo.addItem(impression.name, impression.id)
        index = combo.count() - 1
        outcomes = " → ".join(impression_outcomes(data, impression))
        tip = f"Success: {outcomes}" if outcomes else impression.name
        if not impression_allowed(character, data, impression):
            advantage = next(
                (a.name for a in data.advantages if a.id == impression.advantage),
                impression.advantage,
            )
            tip = f"Needs the {advantage} advantage. {tip}"
            if isinstance(model, QStandardItemModel):
                item = model.item(index)
                if item is not None:
                    item.setEnabled(False)
        combo.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
    wanted = combo.findData(current) if current else -1
    if wanted >= 0 and not impression_allowed(character, data, data.impress.impressions[wanted]):
        # Kept from another impresser who could make it; this one cannot.
        wanted = -1
    if wanted < 0:
        wanted = next(
            (
                i
                for i, imp in enumerate(data.impress.impressions)
                if impression_allowed(character, data, imp)
            ),
            0,
        )
    combo.setCurrentIndex(wanted)
    combo.blockSignals(False)


def _fill_choices(combo: QComboBox, choices: list[TraitChoice], current: str = "") -> None:
    combo.blockSignals(True)
    combo.clear()
    for choice in choices:
        combo.addItem(choice_caption(choice), choice.ref)
    index = combo.findData(current) if current else -1
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def _preselect_skill(combo: QComboBox, impression: Impression | None, data: GameData) -> None:
    """Put the impression's suggested skill in the combo, if the sheet has a row for it."""
    if impression is None or not impression.skill:
        return
    skill = next((s for s in data.skills if s.id == impression.skill), None)
    if skill is None:
        return
    index = combo.findData(f"skill:{skill.name}")
    if index >= 0:
        combo.setCurrentIndex(index)


# -- the GM's modifiers ---------------------------------------------------------


class ModifiersPanel(QGroupBox):
    """The situational modifiers on an impress check, one control each — from data.

    A toggle is a checkbox, a range is a slider with its value beside it; toggles
    sharing an ``exclusive`` group switch each other off. :meth:`values` is what
    :func:`~mm_companion.core.rules.modifier_total` reads.
    """

    changed = Signal()

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__("Situational modifiers", parent)
        self._data = data
        self._toggles: dict[str, QCheckBox] = {}
        self._sliders: dict[str, tuple[QSlider, QLabel, ActionModifier]] = {}
        grid = QGridLayout(self)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(int(theme.metric("space.xs")))
        for row, modifier in enumerate(data.impress.modifiers):
            if modifier.kind == "toggle":
                box = QCheckBox(f"{modifier.label} ({_signed(modifier.value)})")
                box.toggled.connect(lambda on, m=modifier: self._on_toggle(m, on))
                grid.addWidget(box, row, 0, 1, 3)
                self._toggles[modifier.id] = box
                continue
            label = QLabel(modifier.label)
            label.setWordWrap(True)
            slider = QSlider(Qt.Orientation.Horizontal)
            # Counted in steps up from the minimum, so every position the slider can
            # stop at is a value the modifier may take (an earlier attempt is −5 each,
            # not −1), whatever the range — the same snapping modifier_value does.
            low, high = sorted((modifier.minimum, modifier.maximum))
            slider.setRange(0, (high - low) // modifier.step)
            slider.setValue(self._position(modifier, 0))
            slider.setPageStep(1)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            guard_wheel(slider)
            readout = QLabel("0")
            readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            readout.setMinimumWidth(readout.fontMetrics().horizontalAdvance("+20 "))
            slider.valueChanged.connect(lambda _v, m=modifier: self._on_slide(m))
            grid.addWidget(label, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(readout, row, 2)
            self._sliders[modifier.id] = (slider, readout, modifier)

    @staticmethod
    def _position(modifier: ActionModifier, raw: object) -> int:
        """The slider position standing for *raw*, snapped onto the scale."""
        low = min(modifier.minimum, modifier.maximum)
        return (modifier_value(modifier, raw) - low) // modifier.step

    @staticmethod
    def _value(modifier: ActionModifier, position: int) -> int:
        return min(modifier.minimum, modifier.maximum) + position * modifier.step

    def _on_toggle(self, modifier: ActionModifier, on: bool) -> None:
        if on and modifier.exclusive:
            for other in self._data.impress.modifiers:
                if other.id != modifier.id and other.exclusive == modifier.exclusive:
                    box = self._toggles.get(other.id)
                    if box is not None and box.isChecked():
                        box.blockSignals(True)
                        box.setChecked(False)
                        box.blockSignals(False)
        self.changed.emit()

    def _on_slide(self, modifier: ActionModifier) -> None:
        slider, readout, _ = self._sliders[modifier.id]
        value = self._value(modifier, slider.value())
        readout.setText(_signed(value) if value else "0")
        readout.setStyleSheet(
            "" if not value else tinted_style("tint.better" if value > 0 else "tint.worse")
        )
        self.changed.emit()

    def values(self) -> dict[str, object]:
        """Each modifier's position: a toggle's state, a slider's value."""
        values: dict[str, object] = {mid: box.isChecked() for mid, box in self._toggles.items()}
        for mid, (slider, _readout, modifier) in self._sliders.items():
            values[mid] = self._value(modifier, slider.value())
        return values

    def set_values(self, values: dict[str, object]) -> None:
        for mid, box in self._toggles.items():
            box.setChecked(bool(values.get(mid)))
        for mid, (slider, _readout, modifier) in self._sliders.items():
            slider.setValue(self._position(modifier, values.get(mid, 0)))

    def total(self) -> int:
        return modifier_total(self._data, self.values())

    def lines(self) -> list[str]:
        return modifier_lines(self._data, self.values())


# -- the impresser's dialog -----------------------------------------------------


class ImpressDialog(QDialog):
    """Declare an Impress: what impression, with which skill, at whom.

    *scene* is the board as every seat sees it (``None`` off the air). In a session
    the dialog only declares — :meth:`attempt` is what goes to the GM, who sets the
    modifiers and rolls. Off the air there is nobody to send it to, so the dialog
    grows the modifiers itself and :meth:`offline_spec` is the check to roll, carrying
    the resistance as a button for whoever is on the receiving end.

    Targets are picked the way an area's are: scene cards dragged into the field, or
    picked off its Add button. Modeless, for the reason the area's dialog is — a modal
    one swallows the press that starts dragging a card off the board behind it.
    """

    def __init__(
        self,
        data: GameData,
        character: Character,
        *,
        scene: list[dict] | None = None,
        own_player_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._online = scene is not None
        self._fitted = False
        self._names: dict[str, str] = {}
        for entry in scene or []:
            ref = str(entry.get("ref", ""))
            if not ref or (own_player_id and entry.get("player_id") == own_player_id):
                continue
            self._names[ref] = str(entry.get("name", "")) or "Someone"
        self.setWindowTitle(ACTION_NAME)

        outer = QVBoxLayout(self)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = BodyScroll()
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        form = QFormLayout()
        self._impression = QComboBox()
        guard_wheel(self._impression)
        _fill_impressions(self._impression, data, character)
        self._impression.currentIndexChanged.connect(self._on_impression)
        form.addRow("Impression:", self._impression)

        self._skills = impress_skill_choices(character, data)
        self._skill = QComboBox()
        guard_wheel(self._skill)
        _fill_choices(self._skill, self._skills)
        self._skill.currentIndexChanged.connect(self._sync)
        form.addRow("Skill:", self._skill)
        layout.addLayout(form)

        self._outcome = QLabel()
        self._outcome.setWordWrap(True)
        self._outcome.setStyleSheet(muted_style())
        layout.addWidget(self._outcome)

        self._group_note = QLabel()
        self._group_note.setStyleSheet(tinted_style("tint.warning"))
        self._mode_buttons: dict[str, QRadioButton] = {}
        self._modifiers: ModifiersPanel | None = None
        self.targets: TargetsBox | None = None
        if self._online:
            self.targets = TargetsBox(
                "Targets",
                "impressTargets",
                resolve=self._resolve,
                choices=lambda: list(self._names.items()),
                placeholder="Drop the cards of everyone you are impressing here",
            )
            self.targets.field.changed.connect(self._sync)
            layout.addWidget(self.targets, stretch=1)
            layout.addWidget(self._group_note)

            mode_box = QGroupBox("Resolve")
            mode_layout = QVBoxLayout(mode_box)
            group = QButtonGroup(self)
            for mode, caption in MODE_CAPTIONS.items():
                button = QRadioButton(caption)
                group.addButton(button)
                mode_layout.addWidget(button)
                self._mode_buttons[mode] = button
            self._mode_buttons[MODE_RESOLVE].setChecked(True)
            group.buttonToggled.connect(lambda *_: self._sync())
            layout.addWidget(mode_box)
            hint = QLabel("The GM sets the situational modifiers and rolls.")
            hint.setStyleSheet(muted_style(italic=True))
            layout.addWidget(hint)
        else:
            self._modifiers = ModifiersPanel(data)
            self._modifiers.changed.connect(self._sync)
            layout.addWidget(self._modifiers)
            hint = QLabel(
                "Not in a session: the check is rolled here, and its card offers the "
                "resistance as a button."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet(muted_style(italic=True))
            layout.addWidget(hint)

        self._total = QLabel()
        outer.addWidget(self._total)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._ok = QPushButton("Send to GM" if self._online else "Roll")
        self._ok.setDefault(True)
        buttons.addButton(self._ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        _preselect_skill(self._skill, self.impression(), data)
        self._sync()

    # -- reading -----------------------------------------------------------

    def impression(self) -> Impression | None:
        return self._data.impress.impression(str(self._impression.currentData() or ""))

    def skill(self) -> TraitChoice | None:
        ref = self._skill.currentData()
        return next((c for c in self._skills if c.ref == ref), None)

    def target_refs(self) -> list[str]:
        return self.targets.field.refs() if self.targets is not None else []

    def set_targets(self, refs: list[str]) -> None:
        if self.targets is None:
            return
        self.targets.field.clear_refs(notify=False)
        for ref in refs:
            self.targets.field.add_ref(ref)
        self._sync()

    def mode(self) -> str:
        if self._mode_buttons.get(MODE_REQUEST) and self._mode_buttons[MODE_REQUEST].isChecked():
            return MODE_REQUEST
        return MODE_RESOLVE if self._online else MODE_REQUEST

    def set_mode(self, mode: str) -> None:
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    @property
    def modifiers(self) -> ModifiersPanel | None:
        return self._modifiers

    def attempt(self, action_id: str, *, source_name: str = "") -> ImpressAttempt | None:
        """What goes to the GM, or ``None`` until there is something to send."""
        impression, skill = self.impression(), self.skill()
        if impression is None or skill is None:
            return None
        return ImpressAttempt(
            action_id=action_id,
            impression=impression.id,
            skill_row=skill.key,
            skill_label=skill.label,
            skill_modifier=skill.modifier,
            targets=tuple(self.target_refs()),
            mode=self.mode(),
            source_name=source_name,
        )

    def offline_spec(self, *, source_name: str = "") -> RollSpec | None:
        """The check to roll off the air: skill plus modifiers, resistance attached."""
        impression, skill = self.impression(), self.skill()
        if impression is None or skill is None:
            return None
        modifiers = self._modifiers.total() if self._modifiers is not None else 0
        lines = self._modifiers.lines() if self._modifiers is not None else []
        return impress_spec(
            self._data,
            impression,
            skill,
            skill.modifier + modifiers,
            source=source_name,
            request=True,
            hint="\n".join(lines),
        )

    # -- reacting ----------------------------------------------------------

    def _resolve(self, ref: str) -> tuple[str, str] | None:
        name = self._names.get(ref)
        return None if name is None else (ref, name)

    def _on_impression(self) -> None:
        _preselect_skill(self._skill, self.impression(), self._data)
        self._sync()

    def _sync(self) -> None:
        impression, skill = self.impression(), self.skill()
        outcomes = impression_outcomes(self._data, impression) if impression else ()
        self._outcome.setText(
            "Success: "
            + " → ".join(
                f"{n} degree{'s' if n > 1 else ''}: {o}" for n, o in enumerate(outcomes, start=1)
            )
            if outcomes
            else ""
        )
        count = len(self.target_refs())
        penalty = group_penalty(self._data, count) if self._online else 0
        self._group_note.setText(
            f"Group impression: {_signed(penalty)} to the check." if penalty else ""
        )
        self._group_note.setVisible(bool(penalty))
        ready = impression is not None and skill is not None
        if self._online and self.mode() == MODE_RESOLVE and count == 0:
            ready = False
        self._ok.setEnabled(ready)
        if skill is None:
            self._total.setText("No interaction skill to impress with.")
            return
        modifiers = self._modifiers.total() if self._modifiers is not None else 0
        total = skill.modifier + modifiers + penalty
        extra = " before the GM's modifiers" if self._online else ""
        self._total.setText(f"Impress check: <b>{_signed(total)}</b>{extra}")

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            fit_to_screen(self)


# -- the GM's window --------------------------------------------------------------

#: Who a party is to the table.
PARTY_PLAYER = "player"
PARTY_NPC = "npc"


@dataclass
class ImpressParty:
    """Someone who can impress or be impressed, as the GM window knows them.

    ``ref`` is the scene ref (what a player's attempt names), ``source`` the player
    id or the NPC's file name, and ``character`` the model the numbers come from — an
    NPC's own, or a player's last snapshot (``None`` before one has arrived).
    """

    ref: str
    name: str
    kind: str
    source: str
    character: Character | None = None


@dataclass(frozen=True)
class PlannedTarget:
    party: ImpressParty
    resist: TraitChoice | None
    dc_offset: int
    #: The GM left it at "Use highest": a player then picks when asked to roll.
    highest: bool = True


@dataclass(frozen=True)
class ImpressPlan:
    """Everything the GM decided, ready to roll — see :class:`ImpressWindow`."""

    action_id: str
    impression: Impression
    skill: TraitChoice
    source: ImpressParty
    mode: str
    check_modifier: int
    hint: str
    targets: tuple[PlannedTarget, ...] = field(default=())

    @property
    def title(self) -> str:
        """What the action's table in the history is headed by."""
        return f"{self.source.name}: {self.impression.name}"


class ImpressRow(ActionRow):
    """A target's row: how it resists ("Use highest", or a pick), and its numbers."""

    def __init__(self, party: ImpressParty) -> None:
        self.highest = QCheckBox("Use highest")
        self.highest.setChecked(True)
        self.choice = QComboBox()
        guard_wheel(self.choice)
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.addWidget(self.highest)
        cell_layout.addWidget(self.choice, stretch=1)
        super().__init__(party, [cell])
        self.choices: list[TraitChoice] = []

    def inputs(self) -> list[QWidget]:
        return [self.include, self.highest, self.choice, self.bonus, self.penalty]


class ImpressWindow(ActionWindow):
    """The GM setting up and resolving an Impress — a player's, or one of their NPCs'.

    The GM's controls are the impresser (an NPC of theirs), the impression and the skill
    (a player's own when they chose them, the GM's when they sent it bare), a bonus and a
    penalty on the impresser's check, the situational modifiers, each target's resistance
    ("Use highest" by default, or a pick), and a bonus and penalty on the check against
    each target alone. :attr:`rollRequested` hands the finished :class:`ImpressPlan` to
    whoever rolls it; the window then shows each roll as it comes back.
    """

    rollRequested = Signal(object)  # ImpressPlan

    def __init__(
        self,
        data: GameData,
        *,
        action_id: str,
        sources: list[ImpressParty],
        targets: list[ImpressParty],
        attempt: ImpressAttempt | None = None,
        from_card: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            data,
            action_id=action_id,
            title=ACTION_NAME,
            targets=targets,
            go_caption="🎲 Roll",
            box_title="Targets",
            columns=(("Resists with", "What each target rolls against the impress check"),),
            from_card=from_card,
            parent=parent,
        )
        self._attempt = attempt
        self._sources = sources
        #: The player chose the impression and skill; they are theirs, read-only here.
        self._declared = attempt is not None and not attempt.bare

        setup = QWidget()
        form = QFormLayout(setup)
        form.setContentsMargins(0, 0, 0, 0)
        self._source = QComboBox()
        guard_wheel(self._source)
        for party in sources:
            self._source.addItem(party.name, party.ref)
        spin = int(theme.metric("column.stat.spin"))
        self._check_bonus = make_spin_box(0, 20, value=0, buttons=False, max_width=spin)
        self._check_bonus.setToolTip("A bonus to the impresser's check")
        self._check_penalty = make_spin_box(0, 20, value=0, buttons=False, max_width=spin)
        self._check_penalty.setToolTip("A penalty to the impresser's check")
        source_row = QWidget()
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self._source, stretch=1)
        source_layout.addWidget(QLabel("Bonus"))
        source_layout.addWidget(self._check_bonus)
        source_layout.addWidget(QLabel("Penalty"))
        source_layout.addWidget(self._check_penalty)
        source_layout.addStretch(1)
        self._impression = QComboBox()
        guard_wheel(self._impression)
        self._skill = QComboBox()
        guard_wheel(self._skill)
        self._skills: list[TraitChoice] = []
        if attempt is not None:
            # A player's declaration: they impress, nobody else.
            self._source.setEnabled(False)
        if self._declared:
            self._impression.setEnabled(False)
            self._skill.setEnabled(False)
        form.addRow("Impresser:", source_row)
        form.addRow("Impression:", self._impression)
        form.addRow("Skill:", self._skill)
        self._outcome = QLabel()
        self._outcome.setWordWrap(True)
        self._outcome.setStyleSheet(muted_style())
        form.addRow(self._outcome)
        self.add_section(setup)
        self._check_note = QLabel()
        self._check_note.setTextFormat(Qt.TextFormat.RichText)
        self._check_note.setWordWrap(True)
        self._check_note.hide()
        self.add_section(self._check_note)

        self._group_note = QLabel()
        self._group_note.setStyleSheet(tinted_style("tint.warning"))
        self.add_section(self._group_note, after_targets=True)
        self._modifiers = ModifiersPanel(data)
        self.add_section(self._modifiers, after_targets=True)

        mode_host = QWidget()
        mode_row = QHBoxLayout(mode_host)
        mode_row.setContentsMargins(0, 0, 0, 0)
        self._mode_buttons: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)
        for mode, caption in MODE_CAPTIONS.items():
            button = QRadioButton(caption)
            group.addButton(button)
            mode_row.addWidget(button)
            self._mode_buttons[mode] = button
        mode = attempt.mode if attempt is not None else MODE_RESOLVE
        self._mode_buttons.get(mode, self._mode_buttons[MODE_RESOLVE]).setChecked(True)
        self.add_section(mode_host, after_targets=True)

        self._total = QLabel()
        self._total.setWordWrap(True)
        self.add_section(self._total, after_targets=True)

        wanted = set(attempt.targets) if attempt is not None else set()
        self.build_rows(lambda _party: [], wanted)
        self.heading.setText(self._heading_text())

        # Wired once everything they reach exists.
        self._source.currentIndexChanged.connect(self._on_source)
        self._check_bonus.valueChanged.connect(lambda _v: self.sync())
        self._check_penalty.valueChanged.connect(lambda _v: self.sync())
        self._impression.currentIndexChanged.connect(self._on_impression)
        self._skill.currentIndexChanged.connect(self._on_skill)
        self._modifiers.changed.connect(self.sync)
        group.buttonToggled.connect(lambda _button, on: on and self._on_mode())
        self._on_source()

    def make_row(self, party, cells: list[QWidget]) -> ActionRow:
        row = ImpressRow(party)
        row.bonus.setToolTip(f"A bonus to the impress check against {party.name} alone")
        row.penalty.setToolTip(f"A penalty to the impress check against {party.name} alone")
        row.highest.toggled.connect(lambda _on, r=row: self._sync_row(r))
        row.choice.currentIndexChanged.connect(lambda _i: self.sync())
        return row

    def _heading_text(self) -> str:
        attempt = self._attempt
        if attempt is None:
            return "One of your NPCs impresses somebody."
        who = html.escape(attempt.source_name or "A player")
        if attempt.bare:
            return f"<b>{who}</b> is impressing — you choose how, and whom."
        return f"<b>{who}</b> declared an impression — set the modifiers and roll."

    # -- the impresser -------------------------------------------------------

    def source(self) -> ImpressParty | None:
        ref = self._source.currentData()
        return next((p for p in self._sources if p.ref == ref), None)

    def set_source(self, ref: str) -> None:
        index = self._source.findData(ref)
        if index >= 0:
            self._source.setCurrentIndex(index)

    def impression(self) -> Impression | None:
        return self._data.impress.impression(str(self._impression.currentData() or ""))

    def skill(self) -> TraitChoice | None:
        ref = self._skill.currentData()
        return next((c for c in self._skills if c.ref == ref), None)

    def _on_source(self) -> None:
        source = self.source()
        character = source.character if source is not None else None
        attempt = self._attempt
        if self._declared and attempt is not None:
            _fill_impressions(self._impression, self._data, None, attempt.impression)
            self._skills = [
                TraitChoice("skill", attempt.skill_row, attempt.skill_label, attempt.skill_modifier)
            ]
            _fill_choices(self._skill, self._skills)
        else:
            current = str(self._impression.currentData() or "")
            _fill_impressions(self._impression, self._data, character, current)
            self._skills = impress_skill_choices(character, self._data) if character else []
            _fill_choices(self._skill, self._skills)
            _preselect_skill(self._skill, self.impression(), self._data)
        # The impresser cannot be their own target.
        for row in self._rows:
            mine = source is not None and row.party.ref == source.ref
            row.include.setEnabled(not mine and self.phase == PHASE_SETUP)
            if mine:
                row.include.setChecked(False)
        self._on_skill()

    def _on_impression(self) -> None:
        if not self._declared:
            _preselect_skill(self._skill, self.impression(), self._data)
        self.sync()

    def _on_skill(self) -> None:
        skill = self.skill()
        for row in self._rows:
            self._refresh_choices(row, skill)
        self.sync()

    def _on_mode(self) -> None:
        for row in self._rows:
            self._sync_row(row)

    # -- the targets -----------------------------------------------------------

    def _refresh_choices(self, row: ImpressRow, skill: TraitChoice | None) -> None:
        character = row.party.character
        row.choices = (
            resist_choices(character, self._data, skill.key)
            if character is not None and skill is not None
            else []
        )
        _fill_choices(row.choice, row.choices, str(row.choice.currentData() or ""))
        self._sync_row(row)

    def on_include(self, row: ActionRow, on: bool) -> None:
        self._sync_row(row)

    def _sync_row(self, row: ImpressRow) -> None:
        if self.phase != PHASE_SETUP:
            return
        on = row.include.isChecked()
        # Request mode rolls the check alone: nobody's resistance or per-target
        # adjustment is rolled here, so there is nothing for these to set.
        rolled = on and self.mode() == MODE_RESOLVE
        for widget in (row.highest, row.bonus, row.penalty):
            widget.setEnabled(rolled)
        row.choice.setEnabled(rolled and not row.highest.isChecked())
        best = best_choice(row.choices)
        row.highest.setText(
            f"Use highest ({choice_caption(best)})" if best is not None else "Use highest"
        )
        if on and rolled and row.party.kind == PARTY_PLAYER:
            row.show_result(
                "Picks how to resist when asked" if row.highest.isChecked() else "Asked to roll",
                muted_style(italic=True),
            )
        else:
            row.show_result("")
        self.sync()

    def set_party_character(self, ref: str, character: Character | None) -> None:
        """A fresher model for one party (a player's new snapshot)."""
        row = self._row(ref)
        if row is not None:
            row.party.character = character
            if self.phase == PHASE_SETUP:
                self._refresh_choices(row, self.skill())
        source = self.source()
        if source is not None and source.ref == ref and not self._declared:
            source.character = character
            if self.phase == PHASE_SETUP:
                self._on_source()

    @property
    def modifiers(self) -> ModifiersPanel:
        return self._modifiers

    def mode(self) -> str:
        return MODE_REQUEST if self._mode_buttons[MODE_REQUEST].isChecked() else MODE_RESOLVE

    def set_mode(self, mode: str) -> None:
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    def set_check_bonus(self, bonus: int, penalty: int = 0) -> None:
        """The GM's own bonus and penalty on the impresser's check."""
        self._check_bonus.setValue(bonus)
        self._check_penalty.setValue(penalty)

    # -- the total ----------------------------------------------------------------

    def check_modifier(self) -> int:
        skill = self.skill()
        base = skill.modifier if skill is not None else 0
        own = self._check_bonus.value() - self._check_penalty.value()
        return (
            base + own + self._modifiers.total() + group_penalty(self._data, len(self.included()))
        )

    def _resist_for(self, row: ImpressRow) -> TraitChoice | None:
        if row.highest.isChecked():
            return best_choice(row.choices)
        return next((c for c in row.choices if c.ref == row.choice.currentData()), None)

    def sync(self) -> None:
        if not hasattr(self, "_total"):
            return  # still being built
        impression, skill = self.impression(), self.skill()
        outcomes = impression_outcomes(self._data, impression) if impression else ()
        self._outcome.setText(("Success: " + " → ".join(outcomes)) if outcomes else "")
        count = len(self.included())
        penalty = group_penalty(self._data, count)
        self._group_note.setText(
            f"Group impression: {_signed(penalty)} to the check." if penalty else ""
        )
        self._group_note.setVisible(bool(penalty))
        if self.phase != PHASE_SETUP:
            return
        if skill is None:
            self._total.setText("No interaction skill to impress with.")
            self.go.setEnabled(False)
            return
        parts = [f"{html.escape(skill.label)} {_signed(skill.modifier)}"]
        own = self._check_bonus.value() - self._check_penalty.value()
        if own:
            parts.append(f"GM {_signed(own)}")
        if self._modifiers.total():
            parts.append(f"modifiers {_signed(self._modifiers.total())}")
        if penalty:
            parts.append(f"group {_signed(penalty)}")
        self._total.setText(
            f"Impress check: <b>{_signed(self.check_modifier())}</b> ({', '.join(parts)})"
        )
        needs_targets = self.mode() == MODE_RESOLVE
        self.go.setEnabled(impression is not None and (count > 0 or not needs_targets))
        source = self.source()
        asked = sum(1 for row in self.included() if row.party.kind == PARTY_PLAYER)
        if not needs_targets:
            asked = 0
        if source is not None and source.kind == PARTY_PLAYER:
            asked += 1
        said = [f"{count} targeted"] if count else []
        if asked:
            said.append(f"{asked} asked to roll")
        self.summary.setText(" · ".join(said))

    # -- rolling ------------------------------------------------------------------

    def lock_setup(self) -> None:
        super().lock_setup()
        for widget in (
            self._source,
            self._impression,
            self._skill,
            self._check_bonus,
            self._check_penalty,
            self._modifiers,
            *self._mode_buttons.values(),
        ):
            widget.setEnabled(False)

    def set_check(self, text: str, token: str = "") -> None:
        """What the impress check came to, shown above the table."""
        self._check_note.setText(text)
        self._check_note.setStyleSheet(tinted_style(token, bold=False) if token else "")
        self._check_note.show()

    def check_text(self) -> str:
        return self._check_note.text()

    def plan(self) -> ImpressPlan | None:
        impression, skill, source = self.impression(), self.skill(), self.source()
        if impression is None or skill is None or source is None:
            return None
        targets = tuple(
            PlannedTarget(
                party=row.party,
                resist=self._resist_for(row),
                dc_offset=row.adjust,
                highest=row.highest.isChecked(),
            )
            for row in self.included()
        )
        return ImpressPlan(
            action_id=self.action_id,
            impression=impression,
            skill=skill,
            source=source,
            mode=self.mode(),
            check_modifier=self.check_modifier(),
            hint="\n".join(self._modifiers.lines()),
            targets=targets,
        )

    def _emit_go(self) -> None:
        plan = self.plan()
        if plan is None:
            return
        self.start_running()
        self.rollRequested.emit(plan)

    @property
    def _roll(self) -> QPushButton:
        """The Roll button, by the name it always had."""
        return self.go
