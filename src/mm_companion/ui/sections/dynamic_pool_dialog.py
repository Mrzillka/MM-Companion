"""The Dynamic pool dialog: split an array's points across its Dynamic members.

An ordinary array's members are mutually exclusive — one is live and the rest are off.
**Dynamic** members instead "share the power points of the array, allowing them to
operate at the same time, but at reduced effectiveness", and the split is decided "once
per turn as a free action" (p101). This dialog is that free action: one spin box per
Dynamic member, bounded so the split can never exceed the pool, with the rank each share
buys shown beside it as it is dialled.

It edits *runtime* state (:attr:`~mm_companion.core.powers.Power.dynamic_points`), not
the build, so it stays available while the sheet is locked — the same bargain the card
clicks that select an array's live alternate strike. The arithmetic is entirely
:mod:`mm_companion.core.rules`'s: this only renders it.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import Power, PowerGroup, PowerNode
from mm_companion.core.rules import (
    array_pool_points,
    dynamic_rank_share,
    node_cost,
    power_display_name,
)
from mm_companion.ui.widgets import BOLD_STYLE, make_spin_box, muted_style

_INTRO = (
    "Dynamic Alternate Effects share this array's points and run at the same time, at "
    "reduced effectiveness — deciding the split is a free action, once per turn. A "
    "member holding too few points for even one rank is simply off."
)

#: Said under the rows when the array also holds members that are *not* Dynamic. They
#: cannot hold a share (only a Dynamic alternate may), so while anything is split the
#: array's ordinary alternates are not running — which is what the rules mean by them
#: being mutually exclusive with everything else.
_ORDINARY_NOTE = (
    "This array's other {count} member{plural} are not Dynamic, so they cannot hold a "
    "share: while any points are split they are switched off. Clear the split to go "
    "back to selecting one live alternate."
)

_NOTHING_DYNAMIC = (
    "No member of this array is Dynamic yet. Tick a member's Dynamic box to let it "
    "share the array's points with the others."
)


def member_label(node: PowerNode, game_data: GameData) -> str:
    """What one array member is called in the split — its card's name, or the group's.

    A member can be a whole sub-group as easily as a single card, and an unnamed group
    has only its mode to go by; a leaf power falls back to the names of its effects the
    way its card does (:func:`~mm_companion.core.rules.power_display_name`).
    """

    if isinstance(node, PowerGroup):
        return node.name or "Group of powers"
    return power_display_name(node, game_data)


class _MemberRow:
    """One Dynamic member's row: its spin box, and the rank its share currently buys."""

    __slots__ = ("node", "full_cost", "spin", "effect")

    def __init__(self, node: PowerNode, full_cost: int, spin, effect: QLabel) -> None:
        self.node = node
        self.full_cost = full_cost
        self.spin = spin
        self.effect = effect

    @property
    def points(self) -> int:
        return int(self.spin.value())


class DynamicPoolDialog(QDialog):
    """Split one ``array`` group's point pool across its Dynamic members."""

    def __init__(
        self,
        group: PowerGroup,
        game_data: GameData,
        character: Character | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Split the array's points")
        self._group = group
        self._data = game_data
        self._pool = array_pool_points(group, game_data, character)
        self._rows: list[_MemberRow] = []

        layout = QVBoxLayout(self)
        intro = QLabel(_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        # The rank readout absorbs the slack, so the names and their spin boxes stay
        # packed together and a long member name does not push the numbers off-screen.
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        dynamic = [child for child in group.children if child.dynamic]
        for index, child in enumerate(dynamic):
            grid.addWidget(QLabel(member_label(child, game_data)), index, 0)
            full = node_cost(child, game_data, character)
            # The maximum is the whole pool; what is *left* of it is enforced live in
            # :meth:`_refresh`, so a row can always be dialled down to make room.
            spin = make_spin_box(0, max(0, self._pool), value=max(0, child.dynamic_points or 0))
            spin.setSuffix(" PP")
            spin.setToolTip(f"This member costs {full} PP at full rank.")
            grid.addWidget(spin, index, 1)
            effect = QLabel()
            grid.addWidget(effect, index, 2)
            row = _MemberRow(child, full, spin, effect)
            spin.valueChanged.connect(self._refresh)
            self._rows.append(row)

        self._total = QLabel()
        self._total.setStyleSheet(BOLD_STYLE)
        layout.addWidget(self._total)

        ordinary = len(group.children) - len(dynamic)
        if not dynamic:
            note = QLabel(_NOTHING_DYNAMIC)
        elif ordinary:
            plural = "" if ordinary == 1 else "s"
            note = QLabel(_ORDINARY_NOTE.format(count=ordinary, plural=plural))
        else:
            note = QLabel("")
        note.setWordWrap(True)
        note.setStyleSheet(muted_style())
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        clear = QPushButton("Clear the split")
        clear.setToolTip(
            "Hand the whole pool back — the array goes back to one selected live "
            "alternate at full rank."
        )
        clear.clicked.connect(self._clear)
        buttons.addButton(clear, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    # -- state ----------------------------------------------------------------
    def assigned(self) -> int:
        """How many of the pool's points are currently spread across the rows."""
        return sum(row.points for row in self._rows)

    def _refresh(self) -> None:
        """Restate each row's rank and the running total, and re-bound the spin boxes.

        The bound is what enforces "the split may not exceed the pool": every row's
        maximum is what is left over once the *other* rows are paid, so the total can
        be walked up to the pool and never past it, and a row can always be turned down
        to free points for another.
        """

        assigned = self.assigned()
        for row in self._rows:
            spare = self._pool - (assigned - row.points)
            if row.spin.maximum() != spare:
                row.spin.blockSignals(True)
                row.spin.setMaximum(max(0, spare))
                row.spin.blockSignals(False)
            row.effect.setText(self._effect_text(row))
            row.effect.setStyleSheet(muted_style())
        # No tint either way: the bound above makes going over impossible, and leaving
        # part of the pool unassigned is a legal thing to do rather than a mistake.
        self._total.setText(f"{assigned} of {self._pool} PP assigned")

    def _effect_text(self, row: _MemberRow) -> str:
        """What one row's share currently buys, named effect by effect.

        The rank *is* the answer the rules give — the book's own example is a Flight 5
        costing 10 points held to "1 rank of Flight" by the 2 assigned to it — so the
        row prints "Flight 1 of 5" rather than a fraction the reader has to convert.
        A member every one of whose effects falls to zero is below its minimum and says
        so; anything longer than a couple of effects is elided rather than wrapped.
        """

        if not row.points:
            return "off"
        parts = []
        for effect in _member_effects(row.node):
            base = next((e for e in self._data.effects if e.id == effect.effect_id), None)
            name = effect.label or (base.name if base else effect.effect_id)
            share = dynamic_rank_share(effect.rank, row.points, row.full_cost)
            parts.append(f"{name} {share} of {effect.rank}")
        if not parts:
            return ""
        if not any(
            dynamic_rank_share(e.rank, row.points, row.full_cost) for e in _member_effects(row.node)
        ):
            return "too few points to run"
        return ", ".join(parts[:2]) + (", …" if len(parts) > 2 else "")

    def _clear(self) -> None:
        for row in self._rows:
            row.spin.setValue(0)
        self._refresh()

    def apply_to(self) -> None:
        """Write the split onto the group's members.

        A row left at zero is stored as *no share at all* rather than a zero: the two
        behave identically (a member holding nothing is not running), and the model
        writes ``dynamic_points`` only when it is set — so clearing the split leaves a
        saved character byte-for-byte what it was before anyone opened this.
        """

        for row in self._rows:
            row.node.dynamic_points = row.points or None


def _member_effects(node: PowerNode) -> list:
    """Every effect under one member, so a row can say what its share buys."""

    if isinstance(node, PowerGroup):
        return [e for child in node.children for e in _member_effects(child)]
    if isinstance(node, Power):
        return list(node.effects)
    return []
