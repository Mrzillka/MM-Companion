"""The area attack's windows: declaring one, and resolving one.

Two seats, two windows, and neither computes a number — the area, its Dodge DC and
every creature's modifiers come from :mod:`mm_companion.core.rules.area_attack`:

* :class:`AreaDialog` — the **attacker** drops cards off the scene into it: one into
  the primary slot, if they aim the area at someone, and any number into the area.
  At a table that is a declaration for the GM, along with the attack check against
  the primary; off the air the attack is rolled here and the history is left a
  request to dodge.
* :class:`AreaWindow` — the **GM** settles who is in the area, whether the attack hit
  the primary, and each creature's cover and adjustments, then resolves it. What
  happens next — the server rolling, the conditions landing — is
  :mod:`mm_companion.ui.area_gm`'s.

A creature is put in either window the same way it is put on the board: by dragging
its card (:data:`~mm_companion.ui.card_chips.SCENE_MIME`). Each window also lists the
scene behind an **Add** button, since the board is not always where the window is.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    AreaAttempt,
    AreaUse,
    RollSpec,
    area_attack_spec,
    area_dodge_modifier,
    area_request_specs,
    area_shape_options,
    defense_class,
    power_area_use,
)
from mm_companion.ui import theme
from mm_companion.ui.card_chips import SCENE_MIME
from mm_companion.ui.card_drop import CardDropFlow
from mm_companion.ui.drop_feedback import DropFeedback
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, muted_style, tinted_style

#: What the history and the windows call the action.
ACTION_NAME = "Area Attack"

#: The ``ActionRequest`` kind a declared area attack travels as.
KIND_AREA_ATTACK = "area"


def _signed(value: int) -> str:
    return f"{value:+d}"


def use_summary(use: AreaUse, data: GameData) -> str:
    """The area in one rich-text paragraph: its size, its Dodge DC, what it does."""
    effects = ", ".join(
        f"{html.escape(e.name)} {e.rank}"
        + (f" ({html.escape(e.resistance_label)})" if e.resistance_label else "")
        for e in use.effects
    )
    attack = (
        f"Attack {_signed(use.attack)} against a primary target's Defense."
        if use.attack is not None
        else "No attack check: a primary target is hit without one."
    )
    free = " (free use, rank capped at the Power Level)" if use.free else ""
    return (
        f"<b>{html.escape(use.size_text(data))}</b>{free}<br>"
        f"Dodge DC <b>{use.dodge_dc(data)}</b> for half · {effects}<br>{attack}"
    )


# -- the drop field -----------------------------------------------------------------


class TargetDropField(CardDropFlow):
    """A flow of target chips a scene card is dropped into — one, or any number.

    *resolve* turns a dragged reference into ``(ref, name)``, or ``None`` for one this
    window cannot target (the attacker's own card); the field never interprets a ref
    itself. A chip's ✕ takes the creature back out.
    """

    changed = Signal()

    def __init__(
        self,
        name: str,
        *,
        resolve: Callable[[str], tuple[str, str] | None],
        single: bool = False,
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(name, parent, accepts=lambda ref: resolve(ref) is not None)
        self._resolve = resolve
        self._single = single
        self._chips: dict[str, QToolButton] = {}
        self.set_placeholder(placeholder)
        self.dropped.connect(lambda ref, _index: self.add_ref(ref))

    def add_ref(self, raw_ref: str) -> bool:
        """Put the creature *raw_ref* names in the field; ``False`` if it cannot go."""
        found = self._resolve(raw_ref)
        if found is None:
            return False
        ref, name = found
        if ref in self._chips:
            return False
        if self._single:
            self.clear_refs(notify=False)
        chip = QToolButton()
        chip.setText(f"{name}  ✕")
        chip.setToolTip(f"Take {name} back out")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(lambda _=False, r=ref: self.remove_ref(r))
        self._chips[ref] = chip
        self.add_card(chip)
        self.changed.emit()
        return True

    def remove_ref(self, ref: str, *, notify: bool = True) -> None:
        chip = self._chips.pop(ref, None)
        if chip is None:
            return
        self.set_cards([c for c in self._chips.values()])
        if notify:
            self.changed.emit()

    def clear_refs(self, *, notify: bool = True) -> None:
        self._chips.clear()
        self.clear()
        if notify:
            self.changed.emit()

    def refs(self) -> list[str]:
        return list(self._chips)


def _add_button(
    choices: Callable[[], list[tuple[str, str]]], add: Callable[[str], object]
) -> QPushButton:
    """An "Add" button listing the scene, for when the board is not to hand."""
    button = QPushButton("Add")
    button.setToolTip("Pick a creature off the scene — or drag its card in")
    menu = QMenu(button)

    def fill() -> None:
        menu.clear()
        entries = choices()
        if not entries:
            menu.addAction("Nobody else is on the scene").setEnabled(False)
        for ref, name in entries:
            menu.addAction(name, lambda r=ref: add(r))

    menu.aboutToShow.connect(fill)
    button.setMenu(menu)
    return button


# -- the attacker's dialog -------------------------------------------------------------


class AreaDialog(QDialog):
    """Declare an area attack: where the area is aimed, and who is caught in it.

    *scene* is the board as every seat sees it (``None`` off the air). In a session the
    dialog only declares — :meth:`attempt` goes to the GM and :meth:`attack_spec` is
    rolled into the history. Off the air there is nobody to resolve it, so the attack
    is rolled here and :meth:`offline_requests` are left for whoever is caught.
    """

    def __init__(
        self,
        data: GameData,
        character: Character,
        power: Power,
        *,
        scene: list[dict] | None = None,
        own_player_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._power = power
        self._online = scene is not None
        self._names: dict[str, str] = {}
        for entry in scene or []:
            ref = str(entry.get("ref", ""))
            if not ref or (own_player_id and entry.get("player_id") == own_player_id):
                continue
            self._names[ref] = str(entry.get("name", "")) or "Someone"
        self.setWindowTitle(f"{ACTION_NAME}: {power.name}" if power.name else ACTION_NAME)

        layout = QVBoxLayout(self)
        self._shape: QComboBox | None = None
        self._use = power_area_use(power, character, data)
        if self._use is not None and self._use.free:
            form = QFormLayout()
            self._shape = QComboBox()
            guard_wheel(self._shape)
            for value, label in area_shape_options(data):
                self._shape.addItem(label, value)
            self._shape.setCurrentIndex(max(0, self._shape.findData(self._use.shape)))
            self._shape.currentIndexChanged.connect(self._on_shape)
            form.addRow("Shape:", self._shape)
            layout.addLayout(form)

        self._summary = QLabel()
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self.primary: TargetDropField | None = None
        self.area: TargetDropField | None = None
        if self._online:
            primary_box = QGroupBox("Primary target")
            primary_layout = QHBoxLayout(primary_box)
            self.primary = TargetDropField(
                "areaPrimary",
                resolve=self._resolve,
                single=True,
                placeholder="Drop a card here to aim the area at someone (optional)",
            )
            primary_layout.addWidget(self.primary, stretch=1)
            primary_layout.addWidget(_add_button(self._choices, self.primary.add_ref))
            layout.addWidget(primary_box)

            area_box = QGroupBox("Caught in the area")
            area_layout = QHBoxLayout(area_box)
            self.area = TargetDropField(
                "areaTargets",
                resolve=self._resolve,
                placeholder="Drop the cards of everyone else in the area here",
            )
            area_layout.addWidget(self.area, stretch=1)
            area_layout.addWidget(_add_button(self._choices, self.area.add_ref))
            layout.addWidget(area_box, stretch=1)
            self.primary.changed.connect(lambda: self._dedupe(self.primary, self.area))
            self.area.changed.connect(lambda: self._dedupe(self.area, self.primary))
            hint = QLabel("The GM checks the attack, sets each creature's cover, and resolves it.")
        else:
            hint = QLabel(
                "Not in a session: the attack is rolled here, and the history is left a "
                "request to dodge for whoever is caught."
            )
        hint.setWordWrap(True)
        hint.setStyleSheet(muted_style(italic=True))
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._ok = QPushButton("Send to GM" if self._online else "Roll")
        self._ok.setDefault(True)
        buttons.addButton(self._ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync()

    # -- reading -------------------------------------------------------------

    def use(self) -> AreaUse | None:
        return self._use

    def primary_ref(self) -> str:
        refs = self.primary.refs() if self.primary is not None else []
        return refs[0] if refs else ""

    def target_refs(self) -> list[str]:
        return self.area.refs() if self.area is not None else []

    def attempt(self, action_id: str, *, source_name: str = "") -> AreaAttempt | None:
        """What goes to the GM, or ``None`` until there is something to send."""
        if self._use is None:
            return None
        return AreaAttempt(
            action_id=action_id,
            use=self._use,
            primary=self.primary_ref(),
            targets=tuple(self.target_refs()),
            source_name=source_name,
        )

    def attack_spec(self, action_id: str, *, source_name: str = "") -> RollSpec | None:
        """The attack check against the primary — ``None`` with no primary or no roll."""
        use = self._use
        primary = self.primary_ref()
        if use is None or use.attack is None or (self._online and not primary):
            return None
        return area_attack_spec(
            use, action_id=action_id, source=source_name, target=self._names.get(primary, "")
        )

    def note_text(self, *, source_name: str = "") -> str:
        """The line the history gets while the GM resolves it."""
        use = self._use
        if use is None:
            return ""
        caught = [self._names.get(ref, "") for ref in self.target_refs()]
        primary = self._names.get(self.primary_ref(), "")
        who = source_name or "Someone"
        parts = [f"{who} uses {use.power_name} on an area ({use.size_text(self._data)})"]
        if primary:
            parts.append(f"aimed at {primary}")
        if caught:
            parts.append("catching " + ", ".join(n for n in caught if n))
        return " — ".join(parts) + ". The GM resolves it."

    def offline_requests(self, *, source_name: str = "") -> list[RollSpec]:
        """Off the air: the Dodge request for anyone caught, chained to the save."""
        if self._use is None:
            return []
        return area_request_specs(
            self._use, self._data, source=source_name, caught=("Everyone in the area",)
        )

    # -- reacting ----------------------------------------------------------

    def _choices(self) -> list[tuple[str, str]]:
        return list(self._names.items())

    def _resolve(self, ref: str) -> tuple[str, str] | None:
        name = self._names.get(ref)
        return None if name is None else (ref, name)

    def _dedupe(self, changed: TargetDropField | None, other: TargetDropField | None) -> None:
        """A creature is the primary or merely in the area — never both."""
        if changed is not None and other is not None:
            for ref in changed.refs():
                if ref in other.refs():
                    other.remove_ref(ref, notify=False)
        self._sync()

    def _on_shape(self) -> None:
        shape = str(self._shape.currentData() or "") if self._shape is not None else ""
        self._use = power_area_use(self._power, self._character, self._data, shape=shape)
        self._sync()

    def _sync(self) -> None:
        use = self._use
        if use is None:
            self._summary.setText("This power has nothing to use on an area.")
            self._ok.setEnabled(False)
            return
        self._summary.setText(use_summary(use, self._data))
        ready = True
        if self._online:
            ready = bool(self.primary_ref() or self.target_refs())
        self._ok.setEnabled(ready)


# -- the GM's window ---------------------------------------------------------------


@dataclass(frozen=True)
class AreaTarget:
    """One creature in the area as the GM settled it."""

    party: ImpressParty
    #: Hit directly by the attack check: no Dodge, full rank.
    primary: bool = False
    cover: int = 0
    bonus: int = 0
    penalty: int = 0

    @property
    def adjust(self) -> int:
        """The GM's own adjustment to this creature's checks."""
        return self.bonus - self.penalty


@dataclass(frozen=True)
class AreaPlan:
    """Everything the GM decided, ready to roll — see :class:`AreaWindow`."""

    action_id: str
    use: AreaUse
    source: ImpressParty
    targets: tuple[AreaTarget, ...] = field(default=())

    @property
    def title(self) -> str:
        return f"{self.source.name}: {self.use.power_name} (area)"


@dataclass
class _Row:
    party: ImpressParty
    include: QCheckBox
    role: QLabel
    cover: QComboBox
    bonus: object
    penalty: object


class _DropBox(QGroupBox):
    """A group box a scene card can be dropped on, to put that creature in the area."""

    dropped = Signal(str)

    def __init__(self, title: str, accepts: Callable[[str], bool]) -> None:
        super().__init__(title)
        self._accepts = accepts
        self.setObjectName("areaDropBox")
        self.setAcceptDrops(True)
        self._feedback = DropFeedback(self, "#areaDropBox", border=False)

    def _ref(self, event) -> str:
        if not event.mimeData().hasFormat(SCENE_MIME):
            return ""
        ref = bytes(event.mimeData().data(SCENE_MIME)).decode("utf-8")
        return ref if ref and self._accepts(ref) else ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._ref(event):
            event.acceptProposedAction()
            self._feedback.show_accept()
        else:
            self._feedback.show_reject()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._ref(event):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._feedback.clear()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        ref = self._ref(event)
        self._feedback.clear()
        if ref:
            event.acceptProposedAction()
            self.dropped.emit(ref)


class AreaWindow(QDialog):
    """The GM resolving an area attack — a player's (from their attempt) or an NPC's.

    The GM's controls are who the area was aimed at and whether that attack hit, who
    else is in the area (a card dropped on the list ticks them), and for each creature
    its cover and a bonus or penalty on its own checks. :attr:`resolveRequested` hands
    the finished :class:`AreaPlan` to whoever rolls it. NPCs are rolled for; a player
    in the area is sent a request to roll from their own sheet.
    """

    resolveRequested = Signal(object)  # AreaPlan
    #: The GM asked to roll the attack against the primary (an NPC's own attack).
    attackRequested = Signal(object)  # RollSpec
    cancelled = Signal()

    def __init__(
        self,
        data: GameData,
        *,
        attempt: AreaAttempt,
        source: ImpressParty,
        targets: list[ImpressParty],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self.attempt = attempt
        self.action_id = attempt.action_id
        self._source = source
        self._targets = [t for t in targets if t.ref != source.ref]
        self._rows: list[_Row] = []
        self._attack: tuple[int, int] | None = None
        self._done = False
        use = attempt.use
        self.setWindowTitle(f"{ACTION_NAME}: {use.power_name}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        heading = QLabel(f"<b>{html.escape(source.name)}</b> — {use_summary(use, data)}")
        heading.setTextFormat(Qt.TextFormat.RichText)
        heading.setWordWrap(True)
        layout.addWidget(heading)

        primary_box = QGroupBox("Primary target")
        primary_layout = QGridLayout(primary_box)
        self._primary = QComboBox()
        guard_wheel(self._primary)
        self._primary.addItem("None — aimed at a point", "")
        for party in self._targets:
            self._primary.addItem(party.name, party.ref)
        self._primary.setCurrentIndex(max(0, self._primary.findData(attempt.primary)))
        self._primary.currentIndexChanged.connect(self._on_primary)
        self._hit = QCheckBox("Hit")
        self._hit.setToolTip("A primary target that is hit makes no Dodge check: full rank.")
        self._hit.setChecked(use.attack is None)
        self._hit.toggled.connect(lambda _on: self._sync())
        #: The GM ticked or unticked Hit themselves, so a fresher Defense must not undo it.
        self._hit_by_hand = False
        self._hit.clicked.connect(lambda _on: setattr(self, "_hit_by_hand", True))
        self._attack_note = QLabel()
        self._attack_note.setWordWrap(True)
        self._roll_attack = QPushButton("🎲 Roll attack")
        self._roll_attack.clicked.connect(self._emit_attack)
        primary_layout.addWidget(self._primary, 0, 0)
        primary_layout.addWidget(self._hit, 0, 1)
        primary_layout.addWidget(self._roll_attack, 0, 2)
        primary_layout.addWidget(self._attack_note, 1, 0, 1, 3)
        layout.addWidget(primary_box)

        self._box = _DropBox("In the area", lambda ref: self._party(ref) is not None)
        self._box.dropped.connect(self.include_ref)
        box_layout = QVBoxLayout(self._box)
        grid_host = QWidget()
        stack = QVBoxLayout(grid_host)
        stack.setContentsMargins(0, 0, 0, 0)
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(int(theme.metric("space.sm")))
        headers = (
            ("", ""),
            ("Rolls", "What the server rolls for this creature, or who rolls it"),
            ("Cover", "Cover from the area's origin: a bonus to the Dodge check"),
            ("Bonus", "A bonus to this creature's own checks against the area"),
            ("Penalty", "A penalty to this creature's own checks against the area"),
        )
        for column, (caption, tip) in enumerate(headers):
            header = QLabel(caption)
            header.setStyleSheet(muted_style())
            header.setToolTip(tip)
            self._grid.addWidget(header, 0, column)
        self._grid.setColumnStretch(1, 1)
        stack.addLayout(self._grid)
        stack.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_host)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        box_layout.addWidget(scroll)
        hint = QLabel("Drop a card here to put that creature in the area.")
        hint.setStyleSheet(muted_style(italic=True))
        box_layout.addWidget(hint)
        layout.addWidget(self._box, stretch=1)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._resolve = QPushButton("🎲 Resolve")
        self._resolve.setDefault(True)
        buttons.addButton(self._resolve, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._emit_resolve)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self._build_rows(set(attempt.targets) | ({attempt.primary} - {""}))
        self._sync()

    # -- the parties -----------------------------------------------------------

    def _party(self, ref: str) -> ImpressParty | None:
        """The creature a scene ref — or a roster card's ``kind:source`` — names."""
        for party in self._targets:
            if ref == party.ref or ref == f"{party.kind}:{party.source}":
                return party
        return None

    def _build_rows(self, wanted: set[str]) -> None:
        for index, party in enumerate(self._targets, start=1):
            include = QCheckBox(party.name)
            role = QLabel()
            role.setWordWrap(True)
            cover = QComboBox()
            guard_wheel(cover)
            for option in self._data.area.cover:
                cover.addItem(
                    f"{option.label} ({_signed(option.value)})" if option.value else option.label,
                    option.value,
                )
            bonus = make_spin_box(0, 20, value=0)
            bonus.setToolTip(f"A bonus to {party.name}'s checks against the area")
            penalty = make_spin_box(0, 20, value=0)
            penalty.setToolTip(f"A penalty to {party.name}'s checks against the area")
            row = _Row(party, include, role, cover, bonus, penalty)
            self._rows.append(row)
            self._grid.addWidget(include, index, 0)
            self._grid.addWidget(role, index, 1)
            self._grid.addWidget(cover, index, 2)
            self._grid.addWidget(bonus, index, 3)
            self._grid.addWidget(penalty, index, 4)
            include.setChecked(party.ref in wanted)
            include.toggled.connect(lambda _on: self._sync())
            cover.currentIndexChanged.connect(lambda _i: self._sync())
            bonus.valueChanged.connect(lambda _v: self._sync())
            penalty.valueChanged.connect(lambda _v: self._sync())
        if not self._rows:
            empty = QLabel("Nobody is on the scene.")
            empty.setStyleSheet(muted_style(italic=True))
            self._grid.addWidget(empty, 1, 0, 1, 5)

    def _row(self, ref: str) -> _Row | None:
        return next((r for r in self._rows if r.party.ref == ref), None)

    def include_ref(self, ref: str) -> bool:
        """Put the creature *ref* names in the area (a card dropped on the list)."""
        party = self._party(ref)
        row = self._row(party.ref) if party is not None else None
        if row is None:
            return False
        row.include.setChecked(True)
        return True

    def included(self) -> list[_Row]:
        return [row for row in self._rows if row.include.isChecked()]

    def set_party_character(self, ref: str, character: Character | None) -> None:
        """A fresher model for one party (a player's new snapshot).

        The primary's Defense decides the Hit tick, so a new one re-reads it — unless the
        GM has set Hit by hand, which a changed sheet does not overrule.
        """
        row = self._row(ref)
        if row is None:
            return
        row.party.character = character
        if ref == self.primary_ref() and self._attack is not None and not self._hit_by_hand:
            self.set_attack(*self._attack)
            return
        self._sync()

    # -- the primary and its attack ----------------------------------------------

    def primary_ref(self) -> str:
        return str(self._primary.currentData() or "")

    def set_primary(self, ref: str) -> None:
        self._primary.setCurrentIndex(max(0, self._primary.findData(ref)))

    def hit(self) -> bool:
        return bool(self.primary_ref()) and self._hit.isChecked()

    def set_hit(self, hit: bool) -> None:
        self._hit.setChecked(hit)

    def set_attack(self, total: int, die: int) -> None:
        """The attack check against the primary came in: say how it went, and tick Hit."""
        self._attack = (total, die)
        dc = self._defense_dc()
        if dc is not None:
            self._hit.setChecked(total >= dc)
        self._sync()

    def _defense_dc(self) -> int | None:
        row = self._row(self.primary_ref())
        character = row.party.character if row is not None else None
        if character is None:
            return None
        return defense_class(character, self._data)

    def _on_primary(self) -> None:
        row = self._row(self.primary_ref())
        if row is not None:
            row.include.setChecked(True)
        # A new primary has a Defense of its own; the attack is judged afresh against it.
        self._hit_by_hand = False
        if self._attack is not None:
            self.set_attack(*self._attack)
        self._sync()

    def _emit_attack(self) -> None:
        primary = self._row(self.primary_ref())
        spec = area_attack_spec(
            self.attempt.use,
            action_id=self.action_id,
            source=self._source.name,
            target=primary.party.name if primary is not None else "",
        )
        self.attackRequested.emit(spec)

    # -- the plan --------------------------------------------------------------

    def plan(self) -> AreaPlan:
        primary = self.primary_ref()
        hit = self.hit()
        return AreaPlan(
            action_id=self.action_id,
            use=self.attempt.use,
            source=self._source,
            targets=tuple(
                AreaTarget(
                    party=row.party,
                    primary=hit and row.party.ref == primary,
                    cover=int(row.cover.currentData() or 0),
                    bonus=row.bonus.value(),
                    penalty=row.penalty.value(),
                )
                for row in self.included()
            ),
        )

    def _sync(self) -> None:
        use = self.attempt.use
        primary = self.primary_ref()
        can_roll = (
            bool(primary)
            and use.attack is not None
            and self._source.kind == PARTY_NPC
            and self._attack is None
        )
        self._roll_attack.setVisible(can_roll)
        self._hit.setEnabled(bool(primary))
        if not primary:
            self._attack_note.setText("Aimed at a point: everyone in the area dodges.")
            self._attack_note.setStyleSheet(muted_style(italic=True))
        elif use.attack is None:
            self._attack_note.setText("No attack check — the primary target is hit.")
            self._attack_note.setStyleSheet(muted_style(italic=True))
        elif self._attack is None:
            self._attack_note.setText(f"Attack {_signed(use.attack)} — not rolled yet.")
            self._attack_note.setStyleSheet(muted_style(italic=True))
        else:
            total, die = self._attack
            dc = self._defense_dc()
            against = f" vs. Defense DC {dc}" if dc is not None else ""
            natural = f" (natural {die})" if die in (1, 20) else ""
            self._attack_note.setText(f"Attack rolled {total}{natural}{against}.")
            self._attack_note.setStyleSheet(
                tinted_style("tint.better" if self.hit() else "tint.worse", bold=False)
            )
        for row in self._rows:
            on = row.include.isChecked()
            for widget in (row.cover, row.bonus, row.penalty):
                widget.setEnabled(on)
            if not on:
                row.role.setText("")
                continue
            is_primary = self.hit() and row.party.ref == primary
            if row.party.kind == PARTY_PLAYER:
                row.role.setText("Hit — rolls their save" if is_primary else "Rolls from a request")
                row.role.setStyleSheet(muted_style(italic=True))
            elif is_primary:
                row.role.setText("Hit — no Dodge")
                row.role.setStyleSheet(tinted_style("tint.worse", bold=False))
            else:
                total = area_dodge_modifier(
                    row.party.character,
                    self._data,
                    cover=int(row.cover.currentData() or 0),
                    adjust=row.bonus.value() - row.penalty.value(),
                )
                row.role.setText(f"Dodge {_signed(total)} vs. DC {use.dodge_dc(self._data)}")
                row.role.setStyleSheet("")
        count = len(self.included())
        npcs = sum(1 for row in self.included() if row.party.kind != PARTY_PLAYER)
        players = count - npcs
        parts = [f"{count} in the area"]
        if npcs:
            parts.append(f"{npcs} rolled here")
        if players:
            parts.append(f"{players} asked to roll")
        self._summary.setText(" · ".join(parts))
        self._resolve.setEnabled(count > 0)

    def _emit_resolve(self) -> None:
        self._done = True
        self.resolveRequested.emit(self.plan())
        self.close()

    def done(self, result: int) -> None:
        """Every way the window goes away — see :meth:`ImpressWindow.done`."""
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().done(result)
