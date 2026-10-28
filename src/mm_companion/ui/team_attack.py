"""The GM's Team Attack window: a group of NPCs combines its attacks on one target.

Opened from an NPC group's menu (``ui/npc_group.py``). The GM picks the target off the
Scene, the attack the group brings (one every chosen member has — a squad's copies all
do), and who joins in; a member who is down starts left out. **Roll attacks** hands it to
the aimed-attack controller (``AttackController.team_attack``), which rolls every check
as one group, combines the hits (``rules.combine_team_attack``) and resolves the
target's single save; the window shows each attacker's roll and what they came to.
"""

from __future__ import annotations

import html
from collections.abc import Callable

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
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    DEFEAT_NONE,
    TeamOption,
    character_defeat_state,
    team_attack_options,
    team_incompatibility,
)
from mm_companion.ui.action_ui import ADJUST_LIMIT
from mm_companion.ui.attack_targets import SceneTarget
from mm_companion.ui.impress import ImpressParty
from mm_companion.ui.widgets import guard_wheel, make_signed_spin_box, muted_style, tinted_style

#: ``(members, target ref, adjust) -> why it could not start ("" when it did)``.
Runner = Callable[[list[tuple[ImpressParty, TeamOption]], str, int], str]


class TeamAttackWindow(QDialog):
    """Set up and watch one team attack by a group of NPCs."""

    def __init__(
        self,
        data: GameData,
        group_name: str,
        members: list[ImpressParty],
        targets: list[SceneTarget],
        run: Runner,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Team attack: {group_name}")
        self._data = data
        self._members = list(members)
        self._run = run
        #: Each member's attacks, by option key.
        self._options: list[dict[str, TeamOption]] = [
            {o.key: o for o in team_attack_options(m.character, data)} if m.character else {}
            for m in self._members
        ]

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._target = QComboBox()
        guard_wheel(self._target)
        for target in targets:
            text = f"{target.name} (minion)" if target.minion else target.name
            self._target.addItem(text, target.ref)
        form.addRow("Target:", self._target)
        self._attack = QComboBox()
        guard_wheel(self._attack)
        for key, label in self._shared_attacks():
            self._attack.addItem(label, key)
        form.addRow("Attack:", self._attack)
        self._adjust = make_signed_spin_box(ADJUST_LIMIT)
        self._adjust.setToolTip("A bonus (+) or penalty (-) on every attacker's check")
        form.addRow("Adjust:", self._adjust)
        layout.addLayout(form)

        box = QGroupBox("Attackers")
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
            if down:
                check.setToolTip("Down — left out unless you tick it")
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
        self._roll = QPushButton("Roll attacks")
        self._roll.clicked.connect(self.roll)
        buttons.addWidget(self._roll)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._attack.currentIndexChanged.connect(lambda _i: self._sync())
        self._target.currentIndexChanged.connect(lambda _i: self._sync())
        self._sync()

    # -- what is chosen ------------------------------------------------------------

    def _shared_attacks(self) -> list[tuple[str, str]]:
        """The attacks at least two members have, in the first one's order."""
        seen: dict[str, str] = {}
        counts: dict[str, int] = {}
        for options in self._options:
            for key, option in options.items():
                seen.setdefault(key, option.label)
                counts[key] = counts.get(key, 0) + 1
        return [(key, label) for key, label in seen.items() if counts[key] >= 2]

    def chosen(self) -> list[tuple[ImpressParty, TeamOption]]:
        """The ticked members that have the chosen attack, each with theirs."""
        key = self._attack.currentData()
        return [
            (member, options[key])
            for member, options, check in zip(
                self._members, self._options, self._checks, strict=True
            )
            if check.isChecked() and key in options
        ]

    def _sync(self) -> None:
        """Grey out who lacks the attack, and say why the roll cannot go yet."""
        key = self._attack.currentData()
        for options, check in zip(self._options, self._checks, strict=True):
            has = key in options
            check.setEnabled(has)
            if not has:
                check.setChecked(False)
        reason = ""
        if self._target.count() == 0:
            reason = "Nobody else is on the Scene to attack."
        elif key is None:
            reason = "These creatures share no attack to combine."
        else:
            reason = team_incompatibility([o for _m, o in self.chosen()], self._data)
        self._note.setText(html.escape(reason))
        self._note.setStyleSheet(tinted_style("tint.worse", bold=False) if reason else "")
        self._roll.setEnabled(not reason)

    # -- running it ------------------------------------------------------------------

    def set_target(self, ref: str) -> None:
        self._target.setCurrentIndex(max(0, self._target.findData(ref)))

    def set_member(self, index: int, on: bool) -> None:
        self._checks[index].setChecked(on)

    def roll(self) -> None:
        chosen = self.chosen()
        target = str(self._target.currentData() or "")
        adjust = self._adjust.value()
        # Said *before* the run: off the air (and on a hosting server) the whole attack
        # can resolve inside this call, and its result must not be overwritten.
        self._note.setText("Rolling…")
        self._note.setStyleSheet(muted_style(italic=True))
        reason = self._run(chosen, target, adjust)
        if reason:
            self._note.setText(html.escape(reason))
            self._note.setStyleSheet(tinted_style("tint.worse", bold=False))
            return
        self._roll.setEnabled(False)
        for widget in (self._target, self._attack, self._adjust, *self._checks):
            widget.setEnabled(False)

    def show_rolls(self, lines: list[tuple[str, str]], summary: str) -> None:
        """Each attacker's roll beside their name, and what the team came to."""
        said = dict(lines)
        for member, label in zip(self._members, self._results, strict=True):
            label.setText(said.get(member.name, ""))
        self._summary = summary
        self._note.setText(html.escape(summary))
        self._note.setStyleSheet("")

    def show_done(self, text: str) -> None:
        """What the attack did, under what the team came to."""
        summary = getattr(self, "_summary", "")
        target = self._target.currentText()
        outcome = f"<b>{html.escape(target)}: {html.escape(text)}</b>"
        self._note.setText(f"{html.escape(summary)}<br>{outcome}" if summary else outcome)
        self._note.setStyleSheet("")
