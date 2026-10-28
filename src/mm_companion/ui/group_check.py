"""The GM's team check and group check for an NPC group (PDF p18).

Opened from an NPC group's ☰ menu (``ui/npc_group.py``). Both pick one trait — an
ability, a resistance, a skill or initiative, the same list the roller's Request row
offers (``rules.requested_roll_choices``) — and each member rolls it at their own
number (``localize_spec``), with one situational adjustment on every roll:

* **Team check.** A leader, and helpers: the helpers roll against ``helper_dc`` as one
  group, their degrees of success become the leader's bonus (``team_check_bonus``), and
  the leader rolls — against a DC when the GM sets one.
* **Group check.** Everyone rolls against the DC as one group, and
  ``group_check_result`` says where the whole group lands.

Every roll goes through the server like any NPC's (one table per stage in the history),
and the outcome is posted there as a line. Nothing lands on anyone: a check is not an
attack.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from mm_companion.core.data_loader import GameData
from mm_companion.core.dice import degrees
from mm_companion.core.rules import (
    DEFEAT_NONE,
    GroupCheckMember,
    MinionRules,
    RollSpec,
    answer_reading,
    character_defeat_state,
    group_check_result,
    localize_spec,
    requested_roll_choices,
    team_check_bonus,
)
from mm_companion.ui.action_run import ActionRun, group_line, total_of
from mm_companion.ui.action_ui import ADJUST_LIMIT
from mm_companion.ui.impress import ImpressParty
from mm_companion.ui.widgets import (
    grow_to_fit,
    guard_wheel,
    make_signed_spin_box,
    make_spin_box,
    muted_style,
    tinted_style,
)

MODE_TEAM = "team"
MODE_GROUP = "group"
#: The window's titles, by mode.
TITLES = {MODE_TEAM: "Team check", MODE_GROUP: "Group check"}
#: Where the DC box starts — an average-to-hard task.
DEFAULT_DC = 15
UNTRAINED_MARK = "⚠"


def _degree_text(degree: int) -> str:
    if degree > 0:
        return f"success, {degree} degree{'s' if degree > 1 else ''}"
    return f"failure, {-degree} degree{'s' if degree < -1 else ''}"


def _added(record: dict) -> int:
    """+1 for a natural 20 that counts (not a minion's), -1 for a natural 1."""
    die = int(record.get("die", 0))
    if not record.get("critical"):
        return 0
    return 1 if die == 20 else (-1 if die == 1 else 0)


class GroupCheckWindow(QDialog):
    """Set up and run one team check or group check for a group of NPCs."""

    def __init__(
        self,
        data: GameData,
        mode: str,
        group_name: str,
        members: list[ImpressParty],
        make_run: Callable[[str], ActionRun],
        *,
        minion_rules: Callable[[], MinionRules],
        post_note: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._mode = mode
        self._group_name = group_name
        self._members = list(members)
        self._make_run = make_run
        self._minion_rules = minion_rules
        self._post_note = post_note
        self._run: ActionRun | None = None
        self._summary = ""
        self._title = f"{TITLES[mode]}: {group_name}"
        #: The choices as they stood when Roll was pressed. The rolls come back later,
        #: maybe after the window has closed, so nothing past roll() reads a widget.
        self._adjust_value = 0
        self._leader_dc: int | None = None
        self.setWindowTitle(self._title)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._trait = QComboBox()
        guard_wheel(self._trait)
        self._specs: list[RollSpec | None] = []
        for group in requested_roll_choices(data):
            values = [v for v in group.values if v.spec is not None]
            if not values:
                continue
            self._trait.addItem(group.title)
            self._specs.append(None)
            self._trait.model().item(self._trait.count() - 1).setEnabled(False)
            for value in values:
                self._trait.addItem(f"  {value.label}")
                self._specs.append(value.spec)
        first = next((i for i, s in enumerate(self._specs) if s is not None), 0)
        self._trait.setCurrentIndex(first)
        form.addRow("Check:", self._trait)

        self._leader: QComboBox | None = None
        if mode == MODE_TEAM:
            self._leader = QComboBox()
            guard_wheel(self._leader)
            for member in self._members:
                self._leader.addItem(member.name, member.ref)
            form.addRow("Leader:", self._leader)

        dc_row = QHBoxLayout()
        self._dc_check = QCheckBox("DC")
        self._dc_check.setChecked(mode == MODE_GROUP)
        # A group check is judged against a DC by its nature; a team check's leader may
        # simply roll, the GM reading the total.
        self._dc_check.setVisible(mode == MODE_TEAM)
        self._dc: QSpinBox = make_spin_box(0, 60, value=DEFAULT_DC)
        dc_row.addWidget(self._dc_check)
        dc_row.addWidget(self._dc)
        dc_row.addStretch()
        form.addRow("Against:" if mode == MODE_GROUP else "Leader's DC:", dc_row)
        self._adjust = make_signed_spin_box(ADJUST_LIMIT)
        self._adjust.setToolTip("A bonus (+) or penalty (-) on every roll")
        form.addRow("Adjust:", self._adjust)
        layout.addLayout(form)

        if mode == MODE_TEAM:
            helper_dc = data.system.group_checks.helper_dc
            per = data.system.group_checks.bonus_per_degree
            hint = QLabel(
                f"Helpers roll against DC {helper_dc}; each degree of success they score "
                f"gives the leader +{per}."
            )
        else:
            share = data.system.group_checks.success_share
            hint = QLabel(
                f"If at least {share:.0%} succeed, everyone succeeds at the lowest degree "
                "among them; natural 20s and 1s then move the whole group."
            )
        hint.setWordWrap(True)
        hint.setStyleSheet(muted_style(italic=True))
        layout.addWidget(hint)

        box = QGroupBox("Members")
        rows = QVBoxLayout(box)
        self._checks: list[QCheckBox] = []
        self._results: list[QLabel] = []
        for member in self._members:
            line = QHBoxLayout()
            check = QCheckBox(member.name)
            down = (
                member.character is not None
                and character_defeat_state(member.character, data) != DEFEAT_NONE
            )
            check.setChecked(not down)
            check.toggled.connect(lambda _on: self._sync())
            line.addWidget(check)
            result = QLabel()
            result.setStyleSheet(muted_style())
            line.addWidget(result, stretch=1)
            rows.addLayout(line)
            self._checks.append(check)
            self._results.append(result)
        layout.addWidget(box)

        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._roll = QPushButton("Roll")
        self._roll.clicked.connect(self.roll)
        buttons.addWidget(self._roll)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._trait.currentIndexChanged.connect(lambda _i: self._sync())
        self._dc_check.toggled.connect(lambda _on: self._sync())
        if self._leader is not None:
            self._leader.currentIndexChanged.connect(lambda _i: self._sync())
        self._sync()

    # -- what is chosen --------------------------------------------------------------

    def spec(self) -> RollSpec | None:
        index = self._trait.currentIndex()
        return self._specs[index] if 0 <= index < len(self._specs) else None

    def set_trait(self, trait_key: str) -> None:
        for index, spec in enumerate(self._specs):
            if spec is not None and spec.trait_key == trait_key:
                self._trait.setCurrentIndex(index)
                return

    def set_dc(self, dc: int | None) -> None:
        self._dc_check.setChecked(dc is not None or self._mode == MODE_GROUP)
        if dc is not None:
            self._dc.setValue(dc)

    def set_member(self, index: int, on: bool) -> None:
        self._checks[index].setChecked(on)

    def leader(self) -> ImpressParty | None:
        if self._leader is None:
            return None
        ref = self._leader.currentData()
        return next((m for m in self._members if m.ref == ref), None)

    def ticked(self) -> list[ImpressParty]:
        return [m for m, c in zip(self._members, self._checks, strict=True) if c.isChecked()]

    def _own(self, member: ImpressParty, spec: RollSpec) -> RollSpec:
        """*spec* at *member*'s own number, plus the window's adjustment."""
        own = localize_spec(spec, member.character, self._data)
        return replace(
            own,
            modifier=own.modifier + self._adjust_value,
            trait_key="",
            title=self._title,
            label=f"{member.name}: {own.label}",
        )

    def _sync(self) -> None:
        """Each member's number for the chosen check, and whether it can roll yet."""
        spec = self.spec()
        leader = self.leader()
        for member, label in zip(self._members, self._results, strict=True):
            reading = (
                answer_reading(spec, member.character, self._data)
                if spec is not None and member.character is not None
                else None
            )
            if reading is None:
                label.setText("—")
                continue
            mark = f" {UNTRAINED_MARK} untrained" if not reading.usable else ""
            role = " (leader)" if leader is not None and member is leader else ""
            label.setText(f"{reading.modifier:+d}{mark}{role}")
        self._dc.setEnabled(self._dc_check.isChecked())
        reason = ""
        ticked = self.ticked()
        if spec is None:
            reason = "Pick a check."
        elif self._mode == MODE_TEAM:
            if leader is None or leader not in ticked:
                reason = "Tick the leader."
            elif len(ticked) < 2:
                reason = "A team check takes a leader and at least one helper."
        elif len(ticked) < 2:
            reason = "A group check takes two members or more."
        self._note.setText(html.escape(reason))
        self._note.setStyleSheet(tinted_style("tint.worse", bold=False) if reason else "")
        self._roll.setEnabled(not reason)

    # -- rolling ------------------------------------------------------------------

    def _inputs(self) -> list[QWidget]:
        widgets = [self._trait, self._dc, self._dc_check, self._adjust, *self._checks]
        return widgets + ([self._leader] if self._leader is not None else [])

    def roll(self) -> None:
        spec = self.spec()
        if spec is None:
            return
        self._note.setText("Rolling…")
        self._note.setStyleSheet(muted_style(italic=True))
        self._roll.setEnabled(False)
        for widget in self._inputs():
            widget.setEnabled(False)
        self._adjust_value = self._adjust.value()
        self._leader_dc = self._dc.value() if self._dc_check.isChecked() else None
        self._run = self._make_run(self._title)
        if self._mode == MODE_TEAM:
            went = self._roll_helpers(spec)
        else:
            went = self._roll_group(spec)
        if not went:
            self._unlock("The rolls did not reach the session — try again.")

    def _unlock(self, reason: str) -> None:
        """Nothing went out: hand the window back to the GM, saying why."""
        if not isValid(self):
            return
        for widget in self._inputs():
            widget.setEnabled(True)
        self._sync()
        self._note.setText(html.escape(reason))
        self._note.setStyleSheet(tinted_style("tint.worse", bold=False))
        grow_to_fit(self)

    def _line(self, member: ImpressParty, spec: RollSpec, dc: int | None, bonus: int = 0) -> dict:
        own = self._own(member, spec)
        if bonus:
            own = replace(own, modifier=own.modifier + bonus, label=f"{own.label} (team +{bonus})")
        rules = self._minion_rules()
        return group_line(
            member.name,
            own,
            title=self._title,
            dc=dc,
            minion=member.minion and rules.no_added_success,
        )

    def _roll_group(self, spec: RollSpec) -> bool:
        members = self.ticked()
        dc = self._dc.value()
        lines = [self._line(m, spec, dc) for m in members]

        def rolled(pairs) -> None:
            entries = []
            for member, record in pairs:
                added = _added(record)
                entries.append(GroupCheckMember(member.name, total_of(record), added))
                # Each member's own result *before* the natural 20s and 1s, which the
                # group counts afterwards — so the marks agree with "2 of 4 made".
                made = degrees(total_of(record) - dc) > 0
                natural = {1: " (nat 20: +1)", -1: " (nat 1: −1)"}.get(added, "")
                self._show(member, f"{total_of(record)} — {'✓' if made else '✗'}{natural}")
            result = group_check_result(entries, dc, self._data)
            if result is None:
                return
            text = (
                f"{result.successes} of {result.count} made DC {dc}: the group lands on "
                f"{_degree_text(result.base_degree)}"
            )
            if result.added:
                text += (
                    f"; natural 20s and 1s move it {result.added:+d}, "
                    f"to {_degree_text(result.degree)}"
                )
            self._done(f"{self._group_name}'s group check ({spec.label}): {text}.")

        return self._run.roll_group(lines, members, rolled)

    def _roll_helpers(self, spec: RollSpec) -> bool:
        leader = self.leader()
        helpers = [m for m in self.ticked() if m is not leader]
        helper_dc = self._data.system.group_checks.helper_dc
        lines = [self._line(m, spec, helper_dc) for m in helpers]

        def helped(pairs) -> None:
            helper_degrees = []
            for member, record in pairs:
                degree = int(record.get("degree") or 0)
                helper_degrees.append(degree)
                self._show(member, f"{total_of(record)} vs DC {helper_dc} — {_degree_text(degree)}")
            bonus = team_check_bonus(helper_degrees, self._data)
            self._summary = f"Helpers add +{bonus}."
            self._say(html.escape(self._summary + " The leader rolls…"))
            if not self._roll_leader(spec, leader, bonus):
                self._done(
                    f"{self._group_name}'s team check ({spec.label}): helpers +{bonus}; "
                    f"{leader.name}'s roll did not reach the session."
                )

        return self._run.roll_group(lines, helpers, helped)

    def _roll_leader(self, spec: RollSpec, leader: ImpressParty, bonus: int) -> bool:
        dc = self._leader_dc
        line = self._line(leader, spec, dc, bonus)

        def led(pairs) -> None:
            for _member, record in pairs:
                total = total_of(record)
                if dc is None:
                    said = f"{total}"
                    self._show(leader, f"{total} (with +{bonus})")
                else:
                    degree = int(record.get("degree") or 0)
                    said = f"{total} vs DC {dc} — {_degree_text(degree)}"
                    self._show(leader, said)
                self._done(
                    f"{self._group_name}'s team check ({spec.label}): helpers +{bonus}; "
                    f"{leader.name} rolls {said}."
                )

        if dc is None:
            line.pop("dc_offset", None)
            return self._run.roll_group([], [], led, lead=line, lead_about=leader)
        return self._run.roll_group([line], [leader], led)

    def _show(self, member: ImpressParty, text: str) -> None:
        if not isValid(self):
            return  # closed mid-roll: the outcome still reaches the history
        for each, label in zip(self._members, self._results, strict=True):
            if each is member:
                label.setText(text)

    def _say(self, rich: str) -> None:
        if isValid(self):
            self._note.setText(rich)
            self._note.setStyleSheet("")
            grow_to_fit(self)

    def _done(self, text: str) -> None:
        # The history first: it is what counts, window or no window.
        self._post_note(text)
        if self._run is not None:
            self._run.close()
        self._say(f"<b>{html.escape(text)}</b>")
