"""The area attack's windows: declaring one, and setting one up and resolving it.

Two seats, two windows, and neither computes a number — the area, its Dodge DC and
every creature's modifiers come from :mod:`mm_companion.core.rules.area_attack`:

* :class:`AreaDialog` — the **attacker** drops cards off the scene into it: one into
  the primary slot, if they aim the area at someone, and any number into the area.
  At a table that is a declaration for the GM, along with the attack check against
  the primary; off the air the attack is rolled here and the history is left a
  request to dodge. At a table a player only sees it when both they and the GM want
  them to choose (:func:`~mm_companion.ui.action_ui.sets_up_own_actions`); otherwise
  the area goes to the GM bare.
* :class:`AreaWindow` — the **GM** settles the power and its shape, who is in the area,
  whether the attack hit the primary, and each creature's cover and adjustments, then
  resolves it and watches each roll come back. What happens in between — the server
  rolling, players being asked, the conditions landing — is
  :mod:`mm_companion.ui.area_gm`'s.

A creature is put in either window the same way it is put on the board: by dragging
its card (:data:`~mm_companion.ui.card_chips.SCENE_MIME`). Each window also lists the
scene behind an **Add** button, since the board is not always where the window is.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
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
    find_area_power,
    power_area_use,
)
from mm_companion.ui.action_ui import (
    PHASE_SETUP,
    ActionRow,
    ActionWindow,
    BodyScroll,
    TargetDropField,
    TargetsBox,
    add_button,
    fit_to_screen,
)
from mm_companion.ui.impress import PARTY_PLAYER, ImpressParty
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import muted_style, tinted_style

__all__ = [
    "ACTION_NAME",
    "KIND_AREA_ATTACK",
    "AreaDialog",
    "AreaPlan",
    "AreaTarget",
    "AreaWindow",
    "TargetDropField",
    "add_button",
    "use_summary",
]

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
        self._fitted = False
        self._names: dict[str, str] = {}
        for entry in scene or []:
            ref = str(entry.get("ref", ""))
            if not ref or (own_player_id and entry.get("player_id") == own_player_id):
                continue
            self._names[ref] = str(entry.get("name", "")) or "Someone"
        self.setWindowTitle(f"{ACTION_NAME}: {power.name}" if power.name else ACTION_NAME)

        outer = QVBoxLayout(self)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = BodyScroll()
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

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
            primary_box = TargetsBox(
                "Primary target",
                "areaPrimary",
                resolve=self._resolve,
                choices=self._choices,
                single=True,
                placeholder="Drop a card here to aim the area at someone (optional)",
            )
            self.primary = primary_box.field
            layout.addWidget(primary_box)
            area_box = TargetsBox(
                "Caught in the area",
                "areaTargets",
                resolve=self._resolve,
                choices=self._choices,
                placeholder="Drop the cards of everyone else in the area here",
            )
            self.area = area_box.field
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
        outer.addWidget(buttons)
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

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            fit_to_screen(self)


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


class AreaRow(ActionRow):
    """A creature's row: what it rolls, its cover, and its numbers."""

    def __init__(self, party: ImpressParty, data: GameData) -> None:
        self.role = QLabel()
        self.cover = QComboBox()
        guard_wheel(self.cover)
        for option in data.area.cover:
            self.cover.addItem(
                f"{option.label} ({_signed(option.value)})" if option.value else option.label,
                option.value,
            )
        super().__init__(party, [self.role, self.cover])

    def inputs(self) -> list[QWidget]:
        return [self.include, self.cover, self.bonus, self.penalty]


class AreaWindow(ActionWindow):
    """The GM setting up and resolving an area attack — a player's, or an NPC's.

    The GM's controls are the power and its shape (when the attacker's sheet is to hand
    and the use is a free one), who the area was aimed at and whether that attack hit,
    who else is in the area (a card dropped on the table ticks them), and for each
    creature its cover and a bonus or penalty on its own checks. :attr:`resolveRequested`
    hands the finished :class:`AreaPlan` to whoever rolls it. NPCs are rolled for; a
    player in the area is asked to roll from their own sheet, and what each roll came to
    comes back to this window.

    *builds* are the attacker's builds the GM may switch between — an NPC's area-capable
    powers, when the GM started it from the NPC's card.
    """

    resolveRequested = Signal(object)  # AreaPlan
    #: The GM asked for the attack against the primary — rolled for an NPC, asked of a player.
    attackRequested = Signal(object)  # RollSpec

    def __init__(
        self,
        data: GameData,
        *,
        attempt: AreaAttempt,
        source: ImpressParty,
        targets: list[ImpressParty],
        builds: list[Power] | None = None,
        from_card: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            data,
            action_id=attempt.action_id,
            title=f"{ACTION_NAME}: {attempt.use.power_name}",
            targets=[t for t in targets if t.ref != source.ref],
            go_caption="🎲 Resolve",
            box_title="In the area",
            columns=(
                ("Rolls", "What the server rolls for this creature, or who rolls it"),
                ("Cover", "Cover from the area's origin: a bonus to the Dodge check"),
            ),
            from_card=from_card,
            parent=parent,
        )
        self.attempt = attempt
        self._source = source
        self._builds = list(builds or [])
        self._attack: tuple[int, int] | None = None
        self._asked_attack = False
        self._is_npc = source.kind != PARTY_PLAYER

        setup = QWidget()
        form = QFormLayout(setup)
        form.setContentsMargins(0, 0, 0, 0)
        self._power: QComboBox | None = None
        if len(self._builds) > 1:
            self._power = QComboBox()
            guard_wheel(self._power)
            for build in self._builds:
                self._power.addItem(build.name or "Power", build.id)
            self._power.setCurrentIndex(max(0, self._power.findData(attempt.use.power_id)))
            form.addRow("Power:", self._power)
        self._shape = QComboBox()
        guard_wheel(self._shape)
        for value, label in area_shape_options(data):
            self._shape.addItem(label, value)
        self._shape.setCurrentIndex(max(0, self._shape.findData(attempt.use.shape)))
        form.addRow("Shape:", self._shape)
        self._use_note = QLabel()
        self._use_note.setTextFormat(Qt.TextFormat.RichText)
        self._use_note.setWordWrap(True)
        form.addRow(self._use_note)
        self.add_section(setup)

        primary_box = QGroupBox("Primary target")
        primary_layout = QGridLayout(primary_box)
        self._primary = QComboBox()
        guard_wheel(self._primary)
        self._primary.addItem("None — aimed at a point", "")
        for party in self._targets:
            self._primary.addItem(party.name, party.ref)
        self._primary.setCurrentIndex(max(0, self._primary.findData(attempt.primary)))
        self._hit = QCheckBox("Hit")
        self._hit.setToolTip("A primary target that is hit makes no Dodge check: full rank.")
        self._hit.setChecked(attempt.use.attack is None)
        #: The GM ticked or unticked Hit themselves, so a fresher Defense must not undo it.
        self._hit_by_hand = False
        self._hit.clicked.connect(lambda _on: setattr(self, "_hit_by_hand", True))
        self._roll_attack = QPushButton(
            "🎲 Roll attack" if self._is_npc else "🎲 Ask for the attack"
        )
        self._roll_attack.setToolTip(
            "Roll the attack against the primary"
            if self._is_npc
            else f"Ask {source.name} to roll the attack against the primary"
        )
        self._attack_note = QLabel()
        self._attack_note.setWordWrap(True)
        primary_layout.addWidget(self._primary, 0, 0)
        primary_layout.addWidget(self._hit, 0, 1)
        primary_layout.addWidget(self._roll_attack, 0, 2)
        primary_layout.addWidget(self._attack_note, 1, 0, 1, 3)
        self.add_section(primary_box)

        self.build_rows(lambda _party: [], set(attempt.targets) | ({attempt.primary} - {""}))
        self.heading.setText(self._heading_text())

        self._shape.currentIndexChanged.connect(self._on_build)
        if self._power is not None:
            self._power.currentIndexChanged.connect(self._on_build)
        self._primary.currentIndexChanged.connect(self._on_primary)
        self._hit.toggled.connect(lambda _on: self.sync())
        self._roll_attack.clicked.connect(self._emit_attack)
        self.sync()

    def make_row(self, party, cells: list[QWidget]) -> ActionRow:
        row = AreaRow(party, self._data)
        row.bonus.setToolTip(f"A bonus to {party.name}'s checks against the area")
        row.penalty.setToolTip(f"A penalty to {party.name}'s checks against the area")
        row.cover.currentIndexChanged.connect(lambda _i: self.sync())
        return row

    def _heading_text(self) -> str:
        who = html.escape(self._source.name)
        return f"<b>{who}</b> uses <b>{html.escape(self.attempt.use.power_name)}</b> on an area."

    @property
    def use(self) -> AreaUse:
        return self.attempt.use

    # -- the power and its shape ----------------------------------------------------

    def _current_power(self) -> Power | None:
        character = self._source.character
        if self._power is not None:
            wanted = self._power.currentData()
            return next((b for b in self._builds if b.id == wanted), None)
        if self._builds:
            return self._builds[0]
        if character is None:
            return None
        return find_area_power(character, self._data, self.attempt.use.power_id)

    def _on_build(self) -> None:
        """A different power, or a different shape for a free use: work the area out again."""
        power = self._current_power()
        character = self._source.character
        if power is None or character is None:
            return
        use = power_area_use(
            power,
            character,
            self._data,
            shape=str(self._shape.currentData() or ""),
            npc=self._is_npc,
            free_use=True,
        )
        if use is None:
            return
        # Another power is another attack: one already rolled was not this one's.
        if self._attack is not None and use.power_id != self.attempt.use.power_id:
            self._attack = None
        self.attempt = replace(self.attempt, use=use)
        self.setWindowTitle(f"{ACTION_NAME}: {use.power_name}")
        self.heading.setText(self._heading_text())
        self.sync()

    def _shape_free(self) -> bool:
        """Whether the shape is the GM's to set: a free use, with the sheet to work it from."""
        return self.attempt.use.free and self._source.character is not None

    # -- the parties -----------------------------------------------------------

    def set_party_character(self, ref: str, character: Character | None) -> None:
        """A fresher model for one party (a player's new snapshot).

        The primary's Defense decides the Hit tick, so a new one re-reads it — unless the
        GM has set Hit by hand, which a changed sheet does not overrule.
        """
        if ref == self._source.ref:
            self._source.character = character
            self.sync()
        row = self._row(ref)
        if row is None:
            return
        row.party.character = character
        if ref == self.primary_ref() and self._attack is not None and not self._hit_by_hand:
            self.set_attack(*self._attack)
            return
        self.sync()

    # -- the primary and its attack ----------------------------------------------

    def primary_ref(self) -> str:
        return str(self._primary.currentData() or "")

    def primary_name(self) -> str:
        row = self._row(self.primary_ref())
        return row.party.name if row is not None else ""

    def set_primary(self, ref: str) -> None:
        self._primary.setCurrentIndex(max(0, self._primary.findData(ref)))

    def hit(self) -> bool:
        return bool(self.primary_ref()) and self._hit.isChecked()

    def set_hit(self, hit: bool) -> None:
        self._hit.setChecked(hit)

    def set_attack(self, total: int, die: int) -> None:
        """The attack check against the primary came in: say how it went, and tick Hit."""
        self._attack = (total, die)
        self._asked_attack = False
        dc = self._defense_dc()
        if dc is not None and self.phase == PHASE_SETUP:
            self._hit.setChecked(total >= dc)
        self.sync()

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
        self.sync()

    def attack_spec(self) -> RollSpec:
        return area_attack_spec(
            self.attempt.use,
            action_id=self.action_id,
            source=self._source.name,
            target=self.primary_name(),
        )

    def _emit_attack(self) -> None:
        if not self._is_npc:
            self._asked_attack = True
        self.attackRequested.emit(self.attack_spec())
        self.sync()

    def set_attack_asked(self, asked: bool) -> None:
        """Whether the player has been asked for the attack and not answered yet."""
        self._asked_attack = asked
        self.sync()

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

    def sync(self) -> None:
        if not hasattr(self, "_attack_note"):
            return  # still being built
        use = self.attempt.use
        self._use_note.setText(use_summary(use, self._data))
        self._shape.setEnabled(self.phase == PHASE_SETUP and self._shape_free())
        primary = self.primary_ref()
        can_roll = (
            self.phase == PHASE_SETUP
            and bool(primary)
            and use.attack is not None
            and self._attack is None
            and not self._asked_attack
        )
        self._roll_attack.setVisible(can_roll)
        self._hit.setEnabled(bool(primary) and self.phase == PHASE_SETUP)
        if not primary:
            self._attack_note.setText("Aimed at a point: everyone in the area dodges.")
            self._attack_note.setStyleSheet(muted_style(italic=True))
        elif use.attack is None:
            self._attack_note.setText("No attack check — the primary target is hit.")
            self._attack_note.setStyleSheet(muted_style(italic=True))
        elif self._attack is None:
            waiting = f" — waiting for {self._source.name}'s roll" if self._asked_attack else ""
            self._attack_note.setText(
                f"Attack {_signed(use.attack)} — not rolled yet{waiting}. Tick Hit to call it."
            )
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
        if self.phase != PHASE_SETUP:
            return
        for row in self._rows:
            on = row.include.isChecked()
            for widget in (row.cover, row.bonus, row.penalty):
                widget.setEnabled(on)
            # Said for every row, muted when it is out: what ticking it in would mean.
            is_primary = on and self.hit() and row.party.ref == primary
            if row.party.kind == PARTY_PLAYER:
                row.role.setText("Hit — asked for their save" if is_primary else "Asked to roll")
                row.role.setStyleSheet(muted_style(italic=True))
            elif is_primary:
                row.role.setText("Hit — no Dodge")
                row.role.setStyleSheet(tinted_style("tint.worse", bold=False))
            else:
                total = area_dodge_modifier(
                    row.party.character,
                    self._data,
                    cover=int(row.cover.currentData() or 0),
                    adjust=row.adjust,
                )
                row.role.setText(f"Dodge {_signed(total)} vs. DC {use.dodge_dc(self._data)}")
                row.role.setStyleSheet("" if on else muted_style())
        count = len(self.included())
        npcs = sum(1 for row in self.included() if row.party.kind != PARTY_PLAYER)
        players = count - npcs
        parts = [f"{count} in the area"]
        if npcs:
            parts.append(f"{npcs} rolled here")
        if players:
            parts.append(f"{players} asked to roll")
        self.summary.setText(" · ".join(parts))
        self.go.setEnabled(count > 0)

    def lock_setup(self) -> None:
        super().lock_setup()
        for widget in (self._shape, self._primary, self._hit, self._roll_attack):
            widget.setEnabled(False)
        if self._power is not None:
            self._power.setEnabled(False)
        self._roll_attack.hide()

    def _emit_go(self) -> None:
        plan = self.plan()
        self.start_running()
        self.resolveRequested.emit(plan)

    @property
    def _resolve(self) -> QPushButton:
        """The Resolve button, by the name it always had."""
        return self.go
