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
from mm_companion.ui.sections.pool_allocator import make_allocator
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
    "This array's other {subject} not Dynamic, so {pronoun} cannot hold a share: while "
    "any points are split {pronoun} are switched off. Clear the split to go back to "
    "selecting one live alternate."
)

#: How :data:`_ORDINARY_NOTE` names the members it is about. An array can hold exactly
#: one non-Dynamic member as easily as five, and "other 1 member are not Dynamic" read
#: like a bug in the sentence rather than a fact about the build.
_ORDINARY_SUBJECT = {True: ("member is", "it"), False: ("{count} members are", "they")}

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

        # Set while the allocator and the spin boxes are writing each other's values, so
        # neither one's ``valueChanged`` bounces back as a fresh edit.
        self._syncing = False

        layout = QVBoxLayout(self)
        intro = QLabel(_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Filled in below, once the rows the allocator drives exist.
        self._allocator: QWidget | None = None
        self._allocator_slot = QVBoxLayout()
        self._allocator_slot.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._allocator_slot)

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

        # The direct control, above the exact one. It spreads the whole pool in a single
        # gesture, which is what "a free action, once per turn" wants; the spin boxes
        # below stay authoritative and remain the only way to hold points back.
        self._allocator = make_allocator(
            [member_label(row.node, game_data) for row in self._rows],
            self._pool,
            self._allocator_detail,
        )
        if self._allocator is not None:
            self._allocator_slot.addWidget(self._allocator)
            self._allocator.set_shares([row.points for row in self._rows])
            self._allocator.sharesChanged.connect(self._on_allocated)

        self._total = QLabel()
        self._total.setStyleSheet(BOLD_STYLE)
        layout.addWidget(self._total)

        ordinary = len(group.children) - len(dynamic)
        if not dynamic:
            note = QLabel(_NOTHING_DYNAMIC)
        elif ordinary:
            subject, pronoun = _ORDINARY_SUBJECT[ordinary == 1]
            note = QLabel(
                _ORDINARY_NOTE.format(subject=subject.format(count=ordinary), pronoun=pronoun)
            )
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

    def _allocator_detail(self, index: int, points: int) -> str:
        """What one side of the allocator's split currently buys.

        The allocator holds no rules of its own, so it asks for this — the same sentence
        the row below it prints, from the same :func:`dynamic_rank_share`.
        """

        row = self._rows[index]
        return self._effect_text(row, points)

    def _on_allocated(self, shares: list[int]) -> None:
        """Write a split made on the allocator through to the spin boxes.

        The spins remain the state: everything else in this dialog reads them, and
        :meth:`apply_to` writes from them. Their bounds are lifted to the whole pool
        first, because :meth:`_refresh` leaves each one capped at what the *other* rows
        left over — so raising the row that is receiving points before lowering the one
        giving them away would clamp the new split back into the old one's shape.
        :meth:`_refresh` puts the real bounds back straight afterwards.
        """

        self._syncing = True
        try:
            for row, share in zip(self._rows, shares, strict=False):
                row.spin.setMaximum(self._pool)
                row.spin.setValue(share)
        finally:
            self._syncing = False
        self._refresh()

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
        # Follow a split typed into the spins, unless the spins are currently echoing
        # one the allocator itself just made.
        if self._allocator is not None and not self._syncing:
            self._allocator.set_shares([row.points for row in self._rows])

    def _effect_text(self, row: _MemberRow, points: int | None = None) -> str:
        """What one row's share currently buys, named effect by effect.

        The rank *is* the answer the rules give — the book's own example is a Flight 5
        costing 10 points held to "1 rank of Flight" by the 2 assigned to it — so the
        row prints "Flight 1 of 5" rather than a fraction the reader has to convert.
        A member every one of whose effects falls to zero is below its minimum and says
        so; anything longer than a couple of effects is elided rather than wrapped.

        ``points`` asks the same question of a share the row is not holding *yet* — the
        allocator describing where its handle is before the spin boxes have caught up.
        """

        held = row.points if points is None else points
        if not held:
            return "off"
        parts = []
        for effect in _member_effects(row.node):
            base = next((e for e in self._data.effects if e.id == effect.effect_id), None)
            name = effect.label or (base.name if base else effect.effect_id)
            share = dynamic_rank_share(effect.rank, held, row.full_cost)
            parts.append(f"{name} {share} of {effect.rank}")
        if not parts:
            return ""
        if not any(
            dynamic_rank_share(e.rank, held, row.full_cost) for e in _member_effects(row.node)
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
