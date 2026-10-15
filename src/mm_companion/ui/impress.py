"""The Impress action's windows: declaring one, resisting one, and resolving one.

Three seats see three different windows, and none of them computes a number — every
total, option and ladder comes from :mod:`mm_companion.core.rules.impress`:

* :class:`ImpressDialog` — the **impresser** picks an impression, a skill and their
  targets off the scene, and sends that to the GM. Off the air there is no GM, so it
  carries the modifiers itself and rolls the check straight into the history, leaving
  a button to resist with (the "request only" way of running it).
* :class:`ResistPromptDialog` — a **targeted player** says how they resist: their
  highest option by default, or one they pick.
* :class:`ImpressWindow` — the **GM** sets the situational modifiers (always theirs to
  set, whoever is impressing), sees and overrides each target's resistance, adjusts
  the check per target, and rolls. What happens next — the server rolling the group,
  the conditions landing — is :mod:`mm_companion.ui.impress_gm`'s, which owns the
  session plumbing this module stays out of.
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
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
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
    ResistAnswer,
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
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, muted_style, tinted_style

#: What the history and the windows call the action.
ACTION_NAME = "Impress"

#: The ``ActionRequest`` / ``ActionPrompt`` kinds this action travels as: a player's
#: declared attempt, a target's resist choice, and the GM's "how do you resist?" and
#: "that is settled now".
KIND_IMPRESS = "impress"
KIND_RESIST = "resist"
KIND_RESIST_CLOSED = "resist-closed"


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
        self.setWindowTitle(ACTION_NAME)

        layout = QVBoxLayout(self)
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

        self._targets = QListWidget()
        self._group_note = QLabel()
        self._group_note.setStyleSheet(tinted_style("tint.warning"))
        self._mode_buttons: dict[str, QRadioButton] = {}
        self._modifiers: ModifiersPanel | None = None
        if self._online:
            targets_box = QGroupBox("Targets")
            targets_layout = QVBoxLayout(targets_box)
            for entry in scene or []:
                ref = str(entry.get("ref", ""))
                if not ref or (own_player_id and entry.get("player_id") == own_player_id):
                    continue
                item = QListWidgetItem(str(entry.get("name", "")) or "Someone")
                item.setData(Qt.ItemDataRole.UserRole, ref)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self._targets.addItem(item)
            if self._targets.count() == 0:
                empty = QLabel("Nobody else is on the scene yet.")
                empty.setStyleSheet(muted_style(italic=True))
                targets_layout.addWidget(empty)
            self._targets.itemChanged.connect(lambda _item: self._sync())
            targets_layout.addWidget(self._targets)
            targets_layout.addWidget(self._group_note)
            layout.addWidget(targets_box)

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
        layout.addWidget(self._total)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._ok = QPushButton("Send to GM" if self._online else "Roll")
        self._ok.setDefault(True)
        buttons.addButton(self._ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        _preselect_skill(self._skill, self.impression(), data)
        self._sync()

    # -- reading -----------------------------------------------------------

    def impression(self) -> Impression | None:
        return self._data.impress.impression(str(self._impression.currentData() or ""))

    def skill(self) -> TraitChoice | None:
        ref = self._skill.currentData()
        return next((c for c in self._skills if c.ref == ref), None)

    def target_refs(self) -> list[str]:
        refs = []
        for row in range(self._targets.count()):
            item = self._targets.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                refs.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return refs

    def set_targets(self, refs: list[str]) -> None:
        for row in range(self._targets.count()):
            item = self._targets.item(row)
            wanted = item.data(Qt.ItemDataRole.UserRole) in refs
            item.setCheckState(Qt.CheckState.Checked if wanted else Qt.CheckState.Unchecked)

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


# -- the target's prompt -----------------------------------------------------------


class ResistPromptDialog(QDialog):
    """A targeted player saying how they resist an impression.

    Non-modal — the game goes on around it — and answered with one click: "Use
    highest" is on by default, and turning it off lets the player pick. The GM
    can still override whatever comes back.
    """

    answered = Signal(object)  # ResistAnswer

    def __init__(
        self,
        data: GameData,
        character: Character,
        *,
        action_id: str,
        payload: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self.action_id = action_id
        self.setWindowTitle(f"Resist {ACTION_NAME}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        self._what = QLabel()
        self._what.setWordWrap(True)
        layout.addWidget(self._what)

        self._highest = QCheckBox()
        self._highest.setChecked(True)
        self._highest.toggled.connect(self._sync)
        layout.addWidget(self._highest)
        self._choice = QComboBox()
        guard_wheel(self._choice)
        layout.addWidget(self._choice)

        buttons = QDialogButtonBox()
        send = QPushButton("Send to GM")
        send.setDefault(True)
        buttons.addButton(send, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self._send)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self._choices: list[TraitChoice] = []
        self.update_prompt(payload)

    def update_prompt(self, payload: dict) -> None:
        """Show what is being resisted — again, if the GM changed the skill."""
        source = str(payload.get("source_name", "")) or "Someone"
        impression = str(payload.get("impression_name", "")) or ACTION_NAME
        skill_label = str(payload.get("skill_label", ""))
        skill_row = str(payload.get("skill_row", ""))
        with_skill = f" with {skill_label}" if skill_label else ""
        self._what.setText(
            f"<b>{html.escape(source)}</b> is trying to impress you"
            f"{html.escape(with_skill)}: <b>{html.escape(impression)}</b>."
        )
        current = str(self._choice.currentData() or "")
        self._choices = resist_choices(self._character, self._data, skill_row)
        _fill_choices(self._choice, self._choices, current)
        best = best_choice(self._choices)
        self._highest.setText(
            f"Use highest ({choice_caption(best)})" if best is not None else "Use highest"
        )
        self._sync()

    def _sync(self) -> None:
        self._choice.setEnabled(not self._highest.isChecked())

    def answer(self) -> ResistAnswer:
        best = best_choice(self._choices)
        chosen = (
            best
            if self._highest.isChecked()
            else next((c for c in self._choices if c.ref == self._choice.currentData()), best)
        )
        return ResistAnswer(
            action_id=self.action_id,
            use_highest=self._highest.isChecked(),
            choice="" if chosen is None else chosen.ref,
            label="" if chosen is None else chosen.label,
            modifier=0 if chosen is None else chosen.modifier,
            options=tuple(self._choices),
        )

    def _send(self) -> None:
        self.answered.emit(self.answer())
        self.close()


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


@dataclass
class _TargetRow:
    party: ImpressParty
    include: QCheckBox
    highest: QCheckBox
    choice: QComboBox
    status: QLabel
    bonus: object
    penalty: object
    choices: list[TraitChoice] = field(default_factory=list)
    answered: bool = False
    prompted: bool = False
    #: Asked, but the prompt could not be delivered — they resist off their sheet.
    unreachable: bool = False


class ImpressWindow(QDialog):
    """The GM resolving an Impress — a player's (from *attempt*) or one of their NPCs'.

    The GM's controls are the situational modifiers, each target's resistance ("Use
    highest" on by default, or a pick — a player's own answer fills this in), and a
    bonus and penalty on the check against each target alone. :attr:`rollRequested`
    hands the finished :class:`ImpressPlan` to whoever rolls it.
    """

    rollRequested = Signal(object)  # ImpressPlan
    #: A player target was ticked and has not been asked how they resist yet.
    promptNeeded = Signal(object)  # ImpressParty
    #: The skill changed under prompts already sent; they need re-sending.
    promptsChanged = Signal()
    #: A player who was asked is no longer being impressed (untargeted).
    promptWithdrawn = Signal(object)  # ImpressParty
    cancelled = Signal()

    def __init__(
        self,
        data: GameData,
        *,
        action_id: str,
        sources: list[ImpressParty],
        targets: list[ImpressParty],
        attempt: ImpressAttempt | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self.action_id = action_id
        self._attempt = attempt
        self._sources = sources
        self._rows: list[_TargetRow] = []
        self._done = False
        #: The skill every player's answer so far was given against.
        self._asked_skill = ""
        self.setWindowTitle(ACTION_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._source = QComboBox()
        guard_wheel(self._source)
        for party in sources:
            self._source.addItem(party.name, party.ref)
        self._source.currentIndexChanged.connect(self._on_source)
        self._impression = QComboBox()
        guard_wheel(self._impression)
        self._impression.currentIndexChanged.connect(self._on_impression)
        self._skill = QComboBox()
        guard_wheel(self._skill)
        self._skill.currentIndexChanged.connect(self._on_skill)
        self._skills: list[TraitChoice] = []
        if attempt is not None:
            # A player's declaration: what they chose is theirs, and is read-only here.
            self._source.setEnabled(False)
            self._impression.setEnabled(False)
            self._skill.setEnabled(False)
        form.addRow("Impresser:", self._source)
        form.addRow("Impression:", self._impression)
        form.addRow("Skill:", self._skill)
        layout.addLayout(form)

        self._outcome = QLabel()
        self._outcome.setWordWrap(True)
        self._outcome.setStyleSheet(muted_style())
        layout.addWidget(self._outcome)

        targets_box = QGroupBox("Targets")
        targets_layout = QVBoxLayout(targets_box)
        grid_host = QWidget()
        stack = QVBoxLayout(grid_host)
        stack.setContentsMargins(0, 0, 0, 0)
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(int(theme.metric("space.sm")))
        headers = (
            ("", ""),
            ("Resists with", "What each target rolls against the impress check"),
            ("", ""),
            ("Bonus", "A bonus to the impress check against this target alone"),
            ("Penalty", "A penalty to the impress check against this target alone"),
        )
        for column, (caption, tip) in enumerate(headers):
            header = QLabel(caption)
            header.setStyleSheet(muted_style())
            header.setToolTip(tip)
            self._grid.addWidget(header, 0, column)
        self._grid.setColumnStretch(1, 1)
        # The rows stack from the top; the scroll area's spare height goes below them.
        stack.addLayout(self._grid)
        stack.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        targets_layout.addWidget(scroll)
        self._group_note = QLabel()
        self._group_note.setStyleSheet(tinted_style("tint.warning"))
        targets_layout.addWidget(self._group_note)
        layout.addWidget(targets_box, stretch=1)
        self._all_targets = targets

        self._modifiers = ModifiersPanel(data)
        self._modifiers.changed.connect(self._sync)
        layout.addWidget(self._modifiers)

        mode_row = QHBoxLayout()
        self._mode_buttons: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)
        for mode, caption in MODE_CAPTIONS.items():
            button = QRadioButton(caption)
            group.addButton(button)
            mode_row.addWidget(button)
            self._mode_buttons[mode] = button
        mode = attempt.mode if attempt is not None else MODE_RESOLVE
        self._mode_buttons.get(mode, self._mode_buttons[MODE_RESOLVE]).setChecked(True)
        group.buttonToggled.connect(lambda _button, on: on and self._on_mode())
        layout.addLayout(mode_row)

        self._total = QLabel()
        self._total.setWordWrap(True)
        layout.addWidget(self._total)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._roll = QPushButton("🎲 Roll")
        self._roll.setDefault(True)
        buttons.addButton(self._roll, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._emit_roll)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self._build_targets()
        self._on_source()

    # -- the impresser -------------------------------------------------------

    def source(self) -> ImpressParty | None:
        ref = self._source.currentData()
        return next((p for p in self._sources if p.ref == ref), None)

    def impression(self) -> Impression | None:
        return self._data.impress.impression(str(self._impression.currentData() or ""))

    def skill(self) -> TraitChoice | None:
        ref = self._skill.currentData()
        return next((c for c in self._skills if c.ref == ref), None)

    def _on_source(self) -> None:
        source = self.source()
        character = source.character if source is not None else None
        attempt = self._attempt
        if attempt is not None:
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
            row.include.setEnabled(not mine)
            if mine:
                row.include.setChecked(False)
        self._on_skill()

    def _on_impression(self) -> None:
        if self._attempt is None:
            _preselect_skill(self._skill, self.impression(), self._data)
        self._sync()

    def _on_skill(self) -> None:
        skill = self.skill()
        ref = skill.ref if skill is not None else ""
        changed = ref != self._asked_skill
        self._asked_skill = ref
        for row in self._rows:
            if changed:
                # The impresser's skill is one of the ways to resist, so an answer
                # given against the old one is no answer to the new one.
                row.answered = False
            self._refresh_choices(row, skill)
        if changed and any(row.prompted for row in self._rows):
            self.promptsChanged.emit()
        self._sync()

    def _on_mode(self) -> None:
        # Asking a player how they resist only means anything when the GM rolls it;
        # in request mode each target rolls from the card instead.
        for party in self.players_to_prompt():
            self.promptNeeded.emit(party)
        for row in self._rows:
            self._sync_row(row)

    def players_to_prompt(self) -> list[ImpressParty]:
        """The ticked players who should be asked how they resist and have not been."""
        if self.mode() != MODE_RESOLVE:
            return []
        return [
            row.party
            for row in self.included()
            if row.party.kind == PARTY_PLAYER and not row.prompted
        ]

    # -- the targets -----------------------------------------------------------

    def _build_targets(self) -> None:
        wanted = set(self._attempt.targets) if self._attempt is not None else set()
        for index, party in enumerate(self._all_targets, start=1):
            include = QCheckBox(party.name)
            highest = QCheckBox("Use highest")
            highest.setChecked(True)
            choice = QComboBox()
            guard_wheel(choice)
            status = QLabel()
            status.setWordWrap(True)
            bonus = make_spin_box(0, 20, value=0)
            bonus.setToolTip(f"A bonus to the impress check against {party.name} alone")
            penalty = make_spin_box(0, 20, value=0)
            penalty.setToolTip(f"A penalty to the impress check against {party.name} alone")
            row = _TargetRow(party, include, highest, choice, status, bonus, penalty)
            self._rows.append(row)
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.addWidget(highest)
            cell_layout.addWidget(choice, stretch=1)
            self._grid.addWidget(include, index, 0)
            self._grid.addWidget(cell, index, 1)
            self._grid.addWidget(status, index, 2)
            self._grid.addWidget(bonus, index, 3)
            self._grid.addWidget(penalty, index, 4)
            include.setChecked(party.ref in wanted)
            include.toggled.connect(lambda on, r=row: self._on_include(r, on))
            highest.toggled.connect(lambda _on, r=row: self._sync_row(r))
            choice.currentIndexChanged.connect(lambda _i: self._sync())
            bonus.valueChanged.connect(lambda _v: self._sync())
            penalty.valueChanged.connect(lambda _v: self._sync())
            self._sync_row(row)
        if not self._rows:
            empty = QLabel("Nobody is on the scene to impress.")
            empty.setStyleSheet(muted_style(italic=True))
            self._grid.addWidget(empty, 1, 0, 1, 5)

    def _refresh_choices(self, row: _TargetRow, skill: TraitChoice | None) -> None:
        if row.answered and row.choices:
            # A player's own numbers, from their own sheet, are the ones to trust.
            _fill_choices(row.choice, row.choices, str(row.choice.currentData() or ""))
            return
        character = row.party.character
        row.choices = (
            resist_choices(character, self._data, skill.key)
            if character is not None and skill is not None
            else []
        )
        _fill_choices(row.choice, row.choices, str(row.choice.currentData() or ""))
        self._sync_row(row)

    def _on_include(self, row: _TargetRow, on: bool) -> None:
        if row.party.kind == PARTY_PLAYER:
            if on and not row.prompted and self.mode() == MODE_RESOLVE:
                self.promptNeeded.emit(row.party)
            elif not on and row.prompted:
                # Untargeted: their prompt is asking about nothing now.
                row.prompted = row.answered = row.unreachable = False
                self.promptWithdrawn.emit(row.party)
        self._sync_row(row)

    def _sync_row(self, row: _TargetRow) -> None:
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
        if not on:
            row.status.setText("")
        elif row.party.kind != PARTY_PLAYER:
            row.status.setText("")
        elif row.answered:
            row.status.setText("Answered")
            row.status.setStyleSheet(tinted_style("tint.better", bold=False))
        elif row.unreachable:
            row.status.setText("Not connected — using their sheet")
            row.status.setStyleSheet(muted_style(italic=True))
        elif row.prompted:
            row.status.setText("Waiting…")
            row.status.setStyleSheet(muted_style(italic=True))
        else:
            row.status.setText("")
        self._sync()

    def mark_prompted(self, ref: str, sent: bool) -> None:
        """Record that a player target was (or could not be) asked."""
        row = self._row(ref)
        if row is None:
            return
        row.prompted = sent
        row.unreachable = not sent
        self._sync_row(row)

    def prompted_parties(self) -> list[ImpressParty]:
        return [row.party for row in self._rows if row.prompted]

    def set_answer(self, player_id: str, answer: ResistAnswer) -> None:
        """A player's own answer: their pick, on their own numbers. The GM may still override."""
        row = next(
            (r for r in self._rows if r.party.kind == PARTY_PLAYER and r.party.source == player_id),
            None,
        )
        if row is None or not row.prompted:
            # Untargeted since they were asked: a late answer is to nothing.
            return
        row.answered = True
        if answer.options:
            row.choices = list(answer.options)
        _fill_choices(row.choice, row.choices, answer.choice)
        row.highest.blockSignals(True)
        row.highest.setChecked(answer.use_highest)
        row.highest.blockSignals(False)
        self._sync_row(row)

    def set_party_character(self, ref: str, character: Character | None) -> None:
        """A fresher model for one party (a player's new snapshot)."""
        row = self._row(ref)
        if row is not None:
            row.party.character = character
            self._refresh_choices(row, self.skill())

    def _row(self, ref: str) -> _TargetRow | None:
        return next((r for r in self._rows if r.party.ref == ref), None)

    def included(self) -> list[_TargetRow]:
        return [row for row in self._rows if row.include.isChecked()]

    def set_included(self, refs: list[str]) -> None:
        for row in self._rows:
            row.include.setChecked(row.party.ref in refs)

    @property
    def modifiers(self) -> ModifiersPanel:
        return self._modifiers

    def mode(self) -> str:
        return MODE_REQUEST if self._mode_buttons[MODE_REQUEST].isChecked() else MODE_RESOLVE

    def set_mode(self, mode: str) -> None:
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    # -- the total ----------------------------------------------------------------

    def check_modifier(self) -> int:
        skill = self.skill()
        base = skill.modifier if skill is not None else 0
        return base + self._modifiers.total() + group_penalty(self._data, len(self.included()))

    def _resist_for(self, row: _TargetRow) -> TraitChoice | None:
        if row.highest.isChecked():
            return best_choice(row.choices)
        return next((c for c in row.choices if c.ref == row.choice.currentData()), None)

    def _sync(self) -> None:
        impression, skill = self.impression(), self.skill()
        outcomes = impression_outcomes(self._data, impression) if impression else ()
        self._outcome.setText(("Success: " + " → ".join(outcomes)) if outcomes else "")
        count = len(self.included())
        penalty = group_penalty(self._data, count)
        self._group_note.setText(
            f"Group impression: {_signed(penalty)} to the check." if penalty else ""
        )
        self._group_note.setVisible(bool(penalty))
        if skill is None:
            self._total.setText("No interaction skill to impress with.")
            self._roll.setEnabled(False)
            return
        parts = [f"{html.escape(skill.label)} {_signed(skill.modifier)}"]
        if self._modifiers.total():
            parts.append(f"modifiers {_signed(self._modifiers.total())}")
        if penalty:
            parts.append(f"group {_signed(penalty)}")
        self._total.setText(
            f"Impress check: <b>{_signed(self.check_modifier())}</b> ({', '.join(parts)})"
        )
        needs_targets = self.mode() == MODE_RESOLVE
        self._roll.setEnabled(impression is not None and (count > 0 or not needs_targets))

    # -- rolling ------------------------------------------------------------------

    def plan(self) -> ImpressPlan | None:
        impression, skill, source = self.impression(), self.skill(), self.source()
        if impression is None or skill is None or source is None:
            return None
        targets = tuple(
            PlannedTarget(
                party=row.party,
                resist=self._resist_for(row),
                dc_offset=row.bonus.value() - row.penalty.value(),
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

    def _emit_roll(self) -> None:
        plan = self.plan()
        if plan is None:
            return
        self._done = True
        self.rollRequested.emit(plan)
        self.close()

    def done(self, result: int) -> None:
        """Every way the window goes away — Roll, Cancel, Esc, the title bar's ✕.

        Overridden here rather than ``closeEvent``: Esc rejects the dialog without
        any close event, which left the players it had asked staring at a prompt
        for an impression nobody would ever roll.
        """
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().done(result)
