"""The Quick NPC wizard: a Power Level, a shape, and there is a creature.

The GM's side of :func:`mm_companion.core.npc.quick_npc`. It asks for the only
things a mook is ever actually consulted about — what it hits with, how hard, how
hard it is to hit and to hurt, and what it resists with — and hands back the
numbers for a :class:`~mm_companion.core.character.Character` that opens in the
ordinary NPC sheet. Everything past that is edited there; this is a starting point,
not a second kind of character.

The **Power Level** is the field that matters. A **preset** (Brute, Speed, Balance,
Random — ``system.json``'s ``quick_npc``) turns it into all six numbers at once, at
the caps that Power Level allows (:func:`~mm_companion.core.npc.preset_stats`), and
changing the level re-applies the preset. The six boxes stay editable: a preset is
how the numbers start, and a hand edit is how one creature differs from the next.
The level and the preset last used are remembered **for this run** — a GM making a
batch of PL 8 goons wants the fourth dialog where the third left off — but not saved,
since next session's fight is a different one.

Deliberately one page rather than a :class:`QWizard`: a handful of fields do not need
paging, and a wizard the GM has to click Next through is slower than the blank sheet
it exists to replace.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.npc import find_preset, pair_total, preset_stats
from mm_companion.ui import npc_icons, theme
from mm_companion.ui.npc_names import random_npc_name
from mm_companion.ui.widgets import make_spin_box, tinted_style

#: The range the six stat boxes accept. Wide enough for anything from a rat to a
#: cosmic horror; a hand edit is not held to the power level, so nothing narrower is
#: right.
STAT_MIN, STAT_MAX = 0, 40

#: The Power Levels the dialog offers.
PL_MIN, PL_MAX = 1, 20

#: The preset a first dialog starts on, when the data offers it.
DEFAULT_PRESET = "balance"

#: The six boxes, as rows of two: the stats each row's readout sums against a cap,
#: with the caption each box carries. The *pairing* is data (``quick_npc.pairs``);
#: this is only the order the dialog lays them out in.
STAT_ROWS = (
    (("attack", "Attack"), ("effect", "Effect")),
    (("defence", "Defence"), ("toughness", "Toughness")),
    (("fortitude", "Fortitude"), ("will", "Will")),
)

STAT_TIPS = {
    "attack": "The bonus both of its powers attack with.",
    "effect": "The rank of its Damage and Affliction — what sets the save DCs.",
    "defence": "How hard it is to hit. Dodge follows from it.",
    "toughness": "How hard it is to hurt.",
    "fortitude": "What it resists poison, disease and physical afflictions with.",
    "will": "What it resists mental effects with.",
}

HELP_TEXT = (
    "Pick a Power Level and a preset, and every number is set to what that level "
    "allows. Change any of them by hand afterwards. It comes out with a Damage and an "
    "Affliction power at its effect rank — open it afterwards to change anything."
)

#: The same portrait box the NPC card shows, so the wizard previews what the roster
#: will. Kept here rather than imported from ``npc_card`` to avoid a UI module
#: depending on a sibling purely for a number.
PORTRAIT_SIZE = 96

#: What the last accepted dialog of this run chose — the Power Level, the preset,
#: and an icon the GM picked by hand (absent when the icon simply followed the
#: preset). Process memory on purpose, not a setting (see the module docstring).
_memory: dict[str, object] = {}

#: What a preset's button says when its numbers have been edited by hand since it
#: was applied — the button is still lit, so it has to stop claiming they are its.
EDITED_MARK = " *"

#: Put after the name of a preset with a random share in it: clicking it again rolls
#: again, and a die says so where a tooltip only might.
RANDOM_MARK = " 🎲"


def reset_memory() -> None:
    """Forget the last Power Level and preset (a fresh run; tests)."""
    _memory.clear()


@dataclass(frozen=True)
class QuickNPC:
    """What the wizard collected — the arguments of :func:`~mm_companion.core.npc.quick_npc`.

    ``icon`` is an :mod:`~mm_companion.ui.npc_icons` id, not a path: the file it
    becomes is written when the creature is, not when the dialog closes.
    """

    name: str
    power_level: int
    attack: int
    effect: int
    defence: int
    toughness: int
    fortitude: int
    will: int
    icon: str


class QuickNPCDialog(QDialog):
    """Collect a quick NPC's numbers; :meth:`value` returns them after ``exec()``."""

    def __init__(self, parent: QWidget | None = None, *, data: GameData | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick NPC")
        self._data = data or load_game_data()
        rules = self._data.system.quick_npc
        presets = [p.id for p in rules.presets]
        remembered = _memory.get("preset")
        if remembered in presets:
            self._preset = str(remembered)
        elif DEFAULT_PRESET in presets:
            self._preset = DEFAULT_PRESET
        else:
            self._preset = presets[0] if presets else ""
        # An icon the GM chose with the arrows sticks: a preset clicked afterwards
        # changes the numbers, not the face — and the next dialog this run wears it too.
        picked = _memory.get("icon")
        self._icon_picked = isinstance(picked, str)
        self._icon = (
            npc_icons.icon(str(picked)).id
            if self._icon_picked
            else npc_icons.PRESET_ICONS.get(self._preset, npc_icons.NPC_ICONS[0].id)
        )
        self._spins: dict[str, QSpinBox] = {}
        self._readouts: list[tuple[QLabel, str, str, str]] = []
        # True while _apply_preset writes the boxes, so its writes are not hand edits.
        self._applying = False
        # Whether a box has been edited by hand since the preset was last applied.
        self._edited = False

        layout = QVBoxLayout(self)
        help_label = QLabel(HELP_TEXT)
        help_label.setWordWrap(True)
        help_label.setEnabled(False)
        layout.addWidget(help_label)

        top = QHBoxLayout()
        top.addWidget(self._build_portrait())
        form = QFormLayout()
        form.addRow("Name:", self._build_name_row())
        self._power_level = make_spin_box(
            PL_MIN, PL_MAX, value=int(_memory.get("power_level", rules.default_power_level))
        )
        self._power_level.setToolTip("Every number below is set from this.")
        font = self._power_level.font()
        font.setPointSizeF(theme.font_size("size.quick-npc-level"))
        font.setBold(True)
        self._power_level.setFont(font)
        pl_caption = QLabel("Power Level:")
        pl_caption.setFont(font)
        form.addRow(pl_caption, self._power_level)
        top.addLayout(form, stretch=1)
        layout.addLayout(top)

        layout.addWidget(self._build_presets())
        layout.addLayout(self._build_stats())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("Create NPC")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._name.textChanged.connect(self._sync_ok)
        self._power_level.valueChanged.connect(self._apply_preset)
        self._sync_ok()
        self._show_icon()
        self._apply_preset()
        self._set_tab_order()
        # Open where the GM starts: the suggested name, selected, so typing replaces it.
        self._name.setFocus()
        self._name.selectAll()

    # -- construction --------------------------------------------------------

    @staticmethod
    def _side_button(text: str, tooltip: str) -> QPushButton:
        """A button that does something *inside* the dialog, never the dialog's job.

        Not auto-default — in a dialog every push button is, so a clicked preset or
        arrow took the Enter key from Create NPC and Enter re-applied the preset (or,
        on Random, re-rolled it). And reached by Tab but not given focus by a click,
        so clicking a preset leaves the cursor in the field the GM was typing in.
        """
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def _set_tab_order(self) -> None:
        """Name, level, presets, the six numbers, then the icon — the order they matter."""
        chain: list[QWidget] = [self._name, self._reroll, self._power_level]
        chain += list(self._preset_buttons.values())
        chain += [self._spins[key] for row in STAT_ROWS for key, _caption in row]
        chain += [self._previous_icon, self._next_icon, self._ok_button]
        for before, after in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(before, after)

    def _build_name_row(self) -> QWidget:
        """The name box, pre-filled, beside the button that offers another one.

        Pre-filled rather than blank because naming a mook is the one part of this
        that is not a decision — the GM either takes what is offered or types over
        it, and both are one action.
        """
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(int(theme.metric("space.sm")))
        self._name = QLineEdit(random_npc_name())
        line.addWidget(self._name, stretch=1)
        reroll = self._reroll = self._side_button("🎲", "Suggest another name")
        reroll.setFixedWidth(int(theme.metric("column.roll-button")))
        reroll.clicked.connect(lambda: self._name.setText(random_npc_name(self._name.text())))
        line.addWidget(reroll)
        return row

    def _build_portrait(self) -> QWidget:
        """The icon preview between the two arrows that step through the icons."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(int(theme.metric("space.sm")))
        row = QHBoxLayout()
        row.setSpacing(int(theme.metric("space.sm")))
        width = int(theme.metric("column.roll-button"))
        previous = self._previous_icon = self._side_button("◀", "Previous icon")
        previous.setFixedWidth(width)
        previous.clicked.connect(lambda: self._pick_icon(npc_icons.step(self._icon, -1)))
        following = self._next_icon = self._side_button("▶", "Next icon")
        following.setFixedWidth(width)
        following.clicked.connect(lambda: self._pick_icon(npc_icons.step(self._icon, 1)))
        self._portrait = QLabel()
        self._portrait.setFixedSize(PORTRAIT_SIZE, PORTRAIT_SIZE)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(previous, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._portrait)
        row.addWidget(following, alignment=Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(row)
        self._icon_caption = QLabel()
        self._icon_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_caption.setEnabled(False)
        column.addWidget(self._icon_caption)
        return box

    def _build_presets(self) -> QWidget:
        """One checkable button per preset; clicking the checked one applies it again."""
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(int(theme.metric("space.sm")))
        line.addWidget(QLabel("Preset:"))
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_buttons: dict[str, QPushButton] = {}
        for preset in self._data.system.quick_npc.presets:
            button = self._side_button(self._preset_caption(preset.id), preset.description)
            button.setCheckable(True)
            button.setChecked(preset.id == self._preset)
            # `clicked`, not `toggled`: clicking the preset already chosen must still
            # apply it — that is how Random re-rolls and how hand edits are undone.
            button.clicked.connect(lambda _checked=False, p=preset.id: self._choose_preset(p))
            self._preset_group.addButton(button)
            self._preset_buttons[preset.id] = button
            line.addWidget(button)
        line.addStretch()
        return row

    def _build_stats(self) -> QGridLayout:
        """The six boxes in rows of two, each row with its sum against its cap."""
        grid = QGridLayout()
        grid.setHorizontalSpacing(int(theme.metric("space.sm")) * 2)
        for row, ((left, left_label), (right, right_label)) in enumerate(STAT_ROWS):
            for column, (key, caption) in ((0, (left, left_label)), (2, (right, right_label))):
                spin = make_spin_box(STAT_MIN, STAT_MAX)
                spin.setToolTip(STAT_TIPS[key])
                spin.valueChanged.connect(self._refresh_readouts)
                spin.valueChanged.connect(self._on_stat_changed)
                self._spins[key] = spin
                grid.addWidget(QLabel(f"{caption}:"), row, column)
                grid.addWidget(spin, row, column + 1)
            cap = next(
                (p.cap for p in self._data.system.quick_npc.pairs if set(p.stats) == {left, right}),
                None,
            )
            if cap is not None:
                readout = QLabel()
                readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(readout, row, 4)
                self._readouts.append((readout, cap, left, right))
        grid.setColumnStretch(4, 1)
        return grid

    # -- the preset ----------------------------------------------------------

    def _choose_preset(self, preset_id: str) -> None:
        """Make *preset_id* the preset, wear its icon, and fill the numbers from it.

        The icon only if the GM has not picked one: choosing a robot and then the
        Brute numbers makes a robot brute, not a fist.
        """
        self._preset = preset_id
        button = self._preset_buttons.get(preset_id)
        if button is not None:
            button.setChecked(True)
        icon_id = npc_icons.PRESET_ICONS.get(preset_id)
        if icon_id is not None and not self._icon_picked:
            self._set_icon(icon_id)
        self._apply_preset()

    def _preset_caption(self, preset_id: str) -> str:
        """A preset button's text: its name, a die if it rolls, a mark if edited since."""
        preset = find_preset(self._data, preset_id)
        if preset is None:
            return preset_id
        caption = preset.name
        if any(share == "random" for _stat, share in preset.shares):
            caption += RANDOM_MARK
        if self._edited and preset_id == self._preset:
            caption += EDITED_MARK
        return caption

    def _sync_preset_captions(self) -> None:
        for preset_id, button in self._preset_buttons.items():
            button.setText(self._preset_caption(preset_id))
            preset = find_preset(self._data, preset_id)
            tip = preset.description if preset is not None else ""
            if self._edited and preset_id == self._preset and preset is not None:
                tip += f"\n\nEdited by hand — click to set every number back to {preset.name}."
            button.setToolTip(tip)

    def _on_stat_changed(self) -> None:
        """A box moved: by the preset, nothing to say; by hand, the preset is now edited."""
        if self._applying or self._edited:
            return
        self._edited = True
        self._sync_preset_captions()

    def _apply_preset(self) -> None:
        """Set all six boxes from the preset at the current Power Level.

        Overwrites hand edits on purpose: the Power Level is the master field, and a
        number left over from another level would be a number at the wrong cap.
        """
        if find_preset(self._data, self._preset) is None:
            return
        stats = preset_stats(self._data, self._preset, self._power_level.value())
        self._applying = True
        try:
            for key, spin in self._spins.items():
                spin.setValue(getattr(stats, key))
        finally:
            self._applying = False
        self._edited = False
        self._sync_preset_captions()
        self._refresh_readouts()

    def _refresh_readouts(self) -> None:
        """Each row's sum against what its cap allows at this Power Level."""
        level = self._power_level.value()
        for label, cap, left, right in self._readouts:
            total = pair_total(self._data, cap, level)
            used = self._spins[left].value() + self._spins[right].value()
            label.setText(f"{used} / {total}")
            over = used > total
            label.setStyleSheet(tinted_style("tint.worse") if over else "")
            label.setToolTip(
                f"Over the Power Level {level} cap of {total}."
                if over
                else f"The Power Level {level} cap for these two is {total}."
            )

    # -- the icon ------------------------------------------------------------

    def _pick_icon(self, icon_id: str) -> None:
        """The GM chose an icon with the arrows: wear it, and keep it past a preset."""
        self._icon_picked = True
        self._set_icon(icon_id)

    def _set_icon(self, icon_id: str) -> None:
        self._icon = npc_icons.icon(icon_id).id
        self._show_icon()

    def _show_icon(self) -> None:
        self._portrait.setPixmap(
            npc_icons.icon_pixmap(self._icon, PORTRAIT_SIZE, self.devicePixelRatioF())
        )
        self._icon_caption.setText(npc_icons.icon(self._icon).label)

    # -- the result ----------------------------------------------------------

    def _sync_ok(self) -> None:
        """A nameless NPC cannot be saved — the file name comes from it."""
        self._ok_button.setEnabled(bool(self._name.text().strip()))

    def accept(self) -> None:
        """Remember this run's Power Level, preset and picked icon, then close."""
        _memory["power_level"] = self._power_level.value()
        _memory["preset"] = self._preset
        if self._icon_picked:
            _memory["icon"] = self._icon
        else:
            _memory.pop("icon", None)
        super().accept()

    def value(self) -> QuickNPC:
        """What was entered. Read after ``exec()`` returns ``Accepted``."""
        return QuickNPC(
            name=self._name.text().strip(),
            power_level=self._power_level.value(),
            icon=self._icon,
            **{key: spin.value() for key, spin in self._spins.items()},
        )
