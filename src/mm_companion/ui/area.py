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
* :class:`AreaWindow` — the **GM** settles the power, who is in the area,
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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    AREA_MODE_REQUEST,
    AREA_MODE_RESOLVE,
    BOOK_MINION_RULES,
    AreaAttempt,
    AreaUse,
    MinionRules,
    RollSpec,
    area_attack_spec,
    area_capable_builds,
    area_dodge_modifier,
    area_heal_spec,
    area_key_index,
    area_key_power_id,
    area_request_specs,
    area_title,
    area_use_adjusted,
    area_use_heals,
    defense_class,
    find_area_power,
    power_area_use,
    power_area_uses,
    routine_total,
)
from mm_companion.ui import theme
from mm_companion.ui.action_ui import (
    ADJUST_LIMIT,
    DIALOG_CHARS,
    PHASE_SETUP,
    ActionRow,
    ActionWindow,
    BodyScroll,
    TargetDropField,
    TargetsBox,
    add_button,
    fit_to_screen,
)
from mm_companion.ui.impress import PARTY_PLAYER, ImpressParty, mode_row
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    HelpButton,
    dialog_buttons,
    make_signed_spin_box,
    muted_style,
    tinted_style,
)

__all__ = [
    "ACTION_NAME",
    "AREA_ACTION",
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

#: The GM roller's Action-row button: somebody uses an attack on an area.
AREA_ACTION = (
    KIND_AREA_ATTACK,
    "Area…",
    "Use somebody's attack on an area — an NPC's, or a player's",
)


def _signed(value: int) -> str:
    return f"{value:+d}"


def use_summary(use: AreaUse, data: GameData) -> str:
    """The area in two rich-text lines: its size, then its Dodge DC, effects and attack.

    Only the numbers: how an area works is :data:`AREA_HELP`, behind the window's "?".
    """
    effects = ", ".join(
        f"{html.escape(e.name)} {e.rank}"
        + (f" (primary target: {e.primary_rank})" if e.primary_rank is not None else "")
        + (f" ({html.escape(e.resistance_label)})" if e.resistance_label else "")
        for e in use.effects
    )
    free = " (free use)" if use.free else ""
    if area_use_heals(use, data):
        return (
            f"<b>{html.escape(use.size_text(data))}</b>{free}<br>{effects} · "
            "one check helps everyone in it"
        )
    parts = [f"Dodge DC <b>{use.dodge_dc(data)}</b>", effects]
    parts.append(f"Attack {_signed(use.attack)}" if use.attack is not None else "No attack check")
    return f"<b>{html.escape(use.size_text(data))}</b>{free}<br>" + " · ".join(parts)


#: How an area attack works, behind the "?" of both windows.
AREA_HELP = (
    "Everyone in the area makes a Dodge check against the Dodge DC. Making it halves "
    "the rank they resist the effect at, rounded up; Evasion adds to that Dodge.\n\n"
    "The area may be aimed at one creature, the primary target, with an attack check "
    "against its Defense. A hit means no Dodge for it: it resists at full rank. An area "
    "with no attack check hits its primary target without one.\n\n"
    "A free use (any attack used as an area) has its rank capped at the Power Level."
)
AREA_HELP += (
    "\n\nAn area Healing has no Dodge and no primary target: one check is made, and each "
    "degree of success takes a condition off everyone in the area."
)
AREA_MODE_HELP = (
    "GM resolves: the GM rolls every NPC's Dodge and save, each player caught is asked to "
    "roll theirs, and the conditions land on their own.\n\n"
    "Request only: the history gets a Dodge card and a save card — the save at full rank "
    "or at half — and everyone caught rolls from them and applies what it does."
)
HEAL_HELP = (
    "Everyone the area reaches. One check is made, and each degree of success takes a "
    "condition off each of them, least severe first."
)
DIALOG_HELP = (
    "Drop cards off the Scene into the boxes, or pick them from Add. The GM checks "
    "the attack, sets each creature's cover, and resolves it.\n\n" + AREA_HELP
)
DIALOG_HELP_OFFLINE = (
    "Not in a session: the attack is rolled here, and the history is left a request to "
    "dodge for whoever is caught.\n\n" + AREA_HELP
)
PRIMARY_HELP = (
    "The creature the area is aimed at, if any. Roll the attack against its Defense, "
    "and Hit is ticked from the result, or tick Hit to call it yourself. A primary "
    "target that is hit makes no Dodge check and resists at full rank.\n\n"
    "With no primary target the area is aimed at a point, and everyone in it dodges."
)
CAUGHT_HELP = (
    "Everyone else the area catches. Each makes a Dodge check against the Dodge DC, and "
    "resists the effect at half rank if they make it."
)
GM_BOX_HELP = (
    "Tick a creature to put it in the area, or drop its card here from the Scene. Cover "
    "adds to its Dodge check; Adjust (a bonus or a penalty) counts on both its Dodge "
    "and its save.\n\n"
    "NPCs are rolled for. A player in the area is asked to roll from their own sheet."
)


# -- the attacker's dialog -------------------------------------------------------------


class AreaDialog(QDialog):
    """Declare an area attack: where the area is aimed, and who is caught in it.

    *scene* is the board as every seat sees it (``None`` off the air). In a session the
    dialog only declares — :meth:`attempt` goes to the GM and :meth:`attack_spec` is
    rolled into the history. Off the air there is nobody to resolve it, so the attack
    is rolled here and :meth:`offline_requests` are left for whoever is caught.

    *effect_index* is which of the power's area effects this is (a build of several
    areas runs each as an area of its own). An area **Healing** has no primary target
    and nobody dodges it: its one box is who is in it, and its one roll the wielder's
    check (:meth:`heal_spec`).
    """

    def __init__(
        self,
        data: GameData,
        character: Character,
        power: Power,
        *,
        effect_index: int = 0,
        scene: list[dict] | None = None,
        own_player_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._power = power
        self._effect_index = effect_index
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

        # A free use takes the default shape: which shape an area is changes nothing the
        # table resolves, so it is not a choice worth a row of the window.
        self._use = power_area_use(power, character, data, effect_index=effect_index)
        heals = self._use is not None and area_use_heals(self._use, data)

        self._summary = QLabel()
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self.primary: TargetDropField | None = None
        self.area: TargetDropField | None = None
        self._mode_buttons: dict[str, QRadioButton] = {}
        if self._online:
            if not heals:
                primary_box = TargetsBox(
                    "Primary target",
                    "areaPrimary",
                    resolve=self._resolve,
                    choices=self._choices,
                    single=True,
                    placeholder="Drop a card here to aim the area at someone (optional)",
                    help_text=PRIMARY_HELP,
                )
                self.primary = primary_box.field
                layout.addWidget(primary_box)
            area_box = TargetsBox(
                "In the area" if heals else "Caught in the area",
                "areaTargets",
                resolve=self._resolve,
                choices=self._choices,
                placeholder=(
                    "Drop the cards of everyone in the area here"
                    if heals
                    else "Drop the cards of everyone else in the area here"
                ),
                help_text=HEAL_HELP if heals else CAUGHT_HELP,
            )
            self.area = area_box.field
            layout.addWidget(area_box, stretch=1)
            if self.primary is not None:
                self.primary.changed.connect(lambda: self._dedupe(self.primary, self.area))
                self.area.changed.connect(lambda: self._dedupe(self.area, self.primary))
            else:
                self.area.changed.connect(self._sync)
            if not heals:
                mode_host, self._mode_buttons, group = mode_row(self, AREA_MODE_HELP)
                self._mode_buttons[AREA_MODE_RESOLVE].setChecked(True)
                layout.addWidget(mode_host)
        else:
            # Off the air the summary is all there is: slack goes under it, not around it
            # (a lone label is otherwise centred in a dialog its buttons make taller).
            layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._ok = QPushButton("Send to GM" if self._online else "Roll")
        self._ok.setDefault(True)
        buttons.addButton(self._ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addLayout(
            dialog_buttons(buttons, DIALOG_HELP if self._online else DIALOG_HELP_OFFLINE)
        )
        self._sync()

    # -- reading -------------------------------------------------------------

    def use(self) -> AreaUse | None:
        return self._use

    def heals(self) -> bool:
        return self._use is not None and area_use_heals(self._use, self._data)

    def primary_ref(self) -> str:
        refs = self.primary.refs() if self.primary is not None else []
        return refs[0] if refs else ""

    def target_refs(self) -> list[str]:
        return self.area.refs() if self.area is not None else []

    def mode(self) -> str:
        button = self._mode_buttons.get(AREA_MODE_REQUEST)
        return AREA_MODE_REQUEST if button is not None and button.isChecked() else AREA_MODE_RESOLVE

    def set_mode(self, mode: str) -> None:
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

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
            mode=self.mode(),
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

    def heal_spec(self, *, source_name: str = "") -> RollSpec | None:
        """Off the air: an area Healing's one check, rolled here."""
        if self._use is None:
            return None
        return area_heal_spec(self._use, self._data, source=source_name)

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
            verb = "reaching " if self.heals() else "catching "
            parts.append(verb + ", ".join(n for n in caught if n))
        return " — ".join(parts) + ". The GM resolves it."

    def offline_requests(self, *, source_name: str = "") -> list[RollSpec]:
        """Off the air: a Dodge card and a save card for whoever is caught."""
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
            fit_to_screen(self, min_chars=DIALOG_CHARS)


# -- the GM's window ---------------------------------------------------------------


@dataclass(frozen=True)
class AreaTarget:
    """One creature in the area as the GM settled it."""

    party: ImpressParty
    #: Hit directly by the attack check: no Dodge, full rank.
    primary: bool = False
    cover: int = 0
    #: The GM's own adjustment to this creature's checks: a bonus, or a penalty.
    adjust: int = 0


@dataclass(frozen=True)
class AreaPlan:
    """Everything the GM decided, ready to roll — see :class:`AreaWindow`.

    ``use`` is the area with the GM's rank and attack adjustments already in it.
    """

    action_id: str
    use: AreaUse
    source: ImpressParty
    targets: tuple[AreaTarget, ...] = field(default=())
    mode: str = AREA_MODE_RESOLVE
    #: An area Healing: one check of the wielder's, no Dodge and no saves.
    heals: bool = False
    #: The primary target the GM left the area aimed at, hit or not — a request names it.
    primary_ref: str = ""

    @property
    def title(self) -> str:
        return area_title(self.source.name, self.use.power_name)


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
        return [self.include, self.cover, self.modifier]


class AreaWindow(ActionWindow):
    """The GM setting up and resolving an area attack — a player's, or one of their own.

    The GM's controls are the attacker (one the GM started — an NPC or a player), the
    power and its shape (the shape when the use is a free one and the attacker's sheet
    is to hand), an adjustment to the area's **rank** and to the **attack** check, who
    the area was aimed at and whether that attack hit, who else is in the area (a card
    dropped on the table ticks them), each creature's cover and a bonus or penalty on
    its own checks, and whether the table resolves it or each roll is only requested.
    :attr:`resolveRequested` hands the finished :class:`AreaPlan` to whoever rolls it.
    NPCs are rolled for; a player in the area is asked to roll from their own sheet,
    and what each roll came to comes back to this window.

    *sources* is who the GM may pick as the attacker, for an area the GM started;
    ``None`` for a player's declaration, whose attacker is fixed. *builds* are the
    attacker's builds to switch between; with none, they are read off its sheet.
    """

    resolveRequested = Signal(object)  # AreaPlan
    #: The GM asked for the attack against the primary — rolled for an NPC, asked of a
    #: player. Carries the spec and the attacker it is for.
    attackRequested = Signal(object, object)  # RollSpec, ImpressParty

    def __init__(
        self,
        data: GameData,
        *,
        attempt: AreaAttempt,
        source: ImpressParty,
        targets: list[ImpressParty],
        sources: list[ImpressParty] | None = None,
        builds: list[Power] | None = None,
        from_card: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            data,
            action_id=attempt.action_id,
            title=f"{ACTION_NAME}: {attempt.use.power_name}",
            targets=targets,
            go_caption="🎲 Resolve",
            box_title="In the area",
            columns=(
                ("Rolls", "What the server rolls for this creature, or who rolls it"),
                ("Cover", "Cover from the area's origin: a bonus to the Dodge check"),
            ),
            from_card=from_card,
            help_text=AREA_HELP,
            box_help=GM_BOX_HELP,
            parent=parent,
        )
        self.attempt = attempt
        self._source = source
        self._sources = list(sources or [])
        self._builds = list(builds or [])
        self._attack: tuple[int, int] | None = None
        self._attack_rerolled = False
        self._asked_attack = False
        #: The session's minion rules: an attack on a minion primary may be routine.
        self._minion_rules = BOOK_MINION_RULES

        setup = QWidget()
        form = QFormLayout(setup)
        form.setContentsMargins(0, 0, 0, 0)
        self._attacker: QComboBox | None = None
        if self._sources:
            self._attacker = QComboBox()
            guard_wheel(self._attacker)
            for party in self._sources:
                self._attacker.addItem(party.name, party.ref)
            self._attacker.setCurrentIndex(max(0, self._attacker.findData(source.ref)))
            form.addRow("Attacker:", self._attacker)
        self._power = QComboBox()
        guard_wheel(self._power)
        form.addRow("Power:", self._power)
        width = int(theme.metric("column.stat.spin"))
        self._rank_adjust = make_signed_spin_box(ADJUST_LIMIT, max_width=width)
        self._rank_adjust.setToolTip(
            "A bonus (+) or penalty (-) to the area's rank: its Dodge DC and every save DC"
        )
        self._attack_adjust = make_signed_spin_box(ADJUST_LIMIT, max_width=width)
        self._attack_adjust.setToolTip(
            "A bonus (+) or penalty (-) to the attack check against the primary target"
        )
        adjust_row = QWidget()
        adjust_layout = QHBoxLayout(adjust_row)
        adjust_layout.setContentsMargins(0, 0, 0, 0)
        adjust_layout.addWidget(QLabel("Rank"))
        adjust_layout.addWidget(self._rank_adjust)
        self._attack_caption = QLabel("Attack")
        adjust_layout.addWidget(self._attack_caption)
        adjust_layout.addWidget(self._attack_adjust)
        adjust_layout.addStretch(1)
        form.addRow("Adjust:", adjust_row)
        self._form = form
        self._use_note = QLabel()
        self._use_note.setTextFormat(Qt.TextFormat.RichText)
        self._use_note.setWordWrap(True)
        form.addRow(self._use_note)
        self.add_section(setup)

        self._primary_box = QGroupBox("Primary target")
        primary_layout = QGridLayout(self._primary_box)
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
        self._roll_attack = QPushButton()
        self._attack_note = QLabel()
        self._attack_note.setWordWrap(True)
        primary_layout.addWidget(self._primary, 0, 0)
        primary_layout.addWidget(self._hit, 0, 1)
        primary_layout.addWidget(self._roll_attack, 0, 2)
        primary_layout.addWidget(
            HelpButton(PRIMARY_HELP), 0, 3, alignment=Qt.AlignmentFlag.AlignRight
        )
        primary_layout.addWidget(self._attack_note, 1, 0, 1, 4)
        primary_layout.setColumnStretch(0, 1)
        self.add_section(self._primary_box)

        self._mode_host, self._mode_buttons, mode_group = mode_row(self, AREA_MODE_HELP)
        self._mode_buttons.get(attempt.mode, self._mode_buttons[AREA_MODE_RESOLVE]).setChecked(True)
        self.add_section(self._mode_host, after_targets=True)

        self.build_rows(lambda _party: [], set(attempt.targets) | ({attempt.primary} - {""}))
        self._fill_powers()
        self._sync_attacker()

        if self._attacker is not None:
            self._attacker.currentIndexChanged.connect(self._on_attacker)
        self._power.currentIndexChanged.connect(self._on_build)
        self._rank_adjust.valueChanged.connect(lambda _v: self.sync())
        self._attack_adjust.valueChanged.connect(lambda _v: self._on_attack_adjust())
        self._primary.currentIndexChanged.connect(self._on_primary)
        self._hit.toggled.connect(lambda _on: self.sync())
        self._roll_attack.clicked.connect(self._emit_attack)
        mode_group.buttonToggled.connect(lambda _button, on: on and self.sync())
        # A minion primary a routine attack hits is hit before anything is rolled.
        self._judge_hit()
        self.sync()

    def make_row(self, party, cells: list[QWidget]) -> ActionRow:
        row = AreaRow(party, self._data)
        row.modifier.setToolTip(
            f"A bonus (+) or penalty (-) to {party.name}'s checks against the area"
        )
        row.cover.currentIndexChanged.connect(lambda _i: self.sync())
        return row

    def _heading_text(self) -> str:
        """Whose declaration this is — nothing to say when the GM started it."""
        if not self._from_card:
            return ""
        who = html.escape(self._source.name)
        return f"<b>{who}</b> uses <b>{html.escape(self.attempt.use.power_name)}</b> on an area."

    @property
    def use(self) -> AreaUse:
        """The area as declared or picked — before the GM's adjustments."""
        return self.attempt.use

    def adjusted_use(self) -> AreaUse:
        """The area with the GM's rank and attack adjustments in it: what is rolled."""
        return area_use_adjusted(
            self.attempt.use,
            rank=self._rank_adjust.value(),
            attack=self._attack_adjust.value(),
        )

    def heals(self) -> bool:
        return area_use_heals(self.attempt.use, self._data)

    def mode(self) -> str:
        if self.heals():
            return AREA_MODE_RESOLVE
        return (
            AREA_MODE_REQUEST
            if self._mode_buttons[AREA_MODE_REQUEST].isChecked()
            else AREA_MODE_RESOLVE
        )

    def set_mode(self, mode: str) -> None:
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)

    def _on_attack_adjust(self) -> None:
        if not self._hit_by_hand:
            self._judge_hit()
        self.sync()

    def set_rank_adjust(self, value: int) -> None:
        self._rank_adjust.setValue(value)

    def set_attack_adjust(self, value: int) -> None:
        self._attack_adjust.setValue(value)

    # -- the attacker, the power and its shape ----------------------------------------------

    @property
    def source(self) -> ImpressParty:
        return self._source

    def _is_npc(self) -> bool:
        return self._source.kind != PARTY_PLAYER

    def set_attacker(self, ref: str) -> None:
        if self._attacker is not None:
            self._attacker.setCurrentIndex(max(0, self._attacker.findData(ref)))

    def _on_attacker(self) -> None:
        """Another attacker: their builds, their first area, and nothing rolled yet."""
        ref = self._attacker.currentData() if self._attacker is not None else None
        party = next((p for p in self._sources if p.ref == ref), None)
        if party is None or party is self._source:
            return
        self._source = party
        self._builds = []
        self._attack = None
        self._asked_attack = False
        self._fill_powers(keep=False)
        self._sync_attacker()
        self._on_build()

    def _sync_attacker(self) -> None:
        """Say whose attack the button is (who may be in the area is :meth:`sync`'s)."""
        self._roll_attack.setText("🎲 Roll attack" if self._is_npc() else "🎲 Ask for the attack")
        self._roll_attack.setToolTip(
            "Roll the attack against the primary"
            if self._is_npc()
            else f"Ask {self._source.name} to roll the attack against the primary"
        )
        self.set_heading(self._heading_text())

    def _area_builds(self) -> list[Power]:
        if self._builds:
            return self._builds
        character = self._source.character
        return area_capable_builds(character, self._data) if character is not None else []

    def _fill_powers(self, *, keep: bool = True) -> None:
        """Every area the attacker could make — one entry per area effect of each build.

        Without the attacker's sheet (a player whose snapshot has not come) there is
        only the area they declared.
        """
        character = self._source.character
        entries: list[tuple[str, str]] = []
        if character is not None:
            for build in self._area_builds():
                uses = power_area_uses(
                    build, character, self._data, npc=self._is_npc(), free_use=True
                )
                for use in uses:
                    name = build.name or use.power_name
                    if len(uses) > 1:
                        name = f"{name} — {use.effects[0].name}"
                    entries.append((use.key, name))
        wanted = self.attempt.use.key if keep else ""
        if keep and wanted not in {key for key, _name in entries}:
            entries.insert(0, (wanted, self.attempt.use.power_name or "Power"))
        self._power.blockSignals(True)
        self._power.clear()
        for key, name in entries:
            self._power.addItem(name, key)
        self._power.setCurrentIndex(max(0, self._power.findData(wanted)) if wanted else 0)
        self._power.blockSignals(False)
        self._form.setRowVisible(self._power, self._power.count() > 1)

    def _current_power(self) -> tuple[Power, int] | None:
        character = self._source.character
        key = str(self._power.currentData() or self.attempt.use.key)
        wanted = area_key_power_id(key)
        power = next((b for b in self._area_builds() if b.id == wanted), None)
        if power is None and character is not None:
            power = find_area_power(character, self._data, key)
        if power is None:
            return None
        return power, area_key_index(key)

    def _on_build(self) -> None:
        """A different attacker or power: work the area out again."""
        found = self._current_power()
        character = self._source.character
        if found is None or character is None:
            self.sync()
            return
        power, index = found
        use = power_area_use(
            power,
            character,
            self._data,
            # Whatever the attempt was declared with; a free use falls back to the
            # default shape (see AreaDialog).
            shape=self.attempt.use.shape,
            npc=self._is_npc(),
            free_use=True,
            effect_index=index,
        )
        if use is None:
            self.sync()
            return
        # Another power is another attack: one already rolled was not this one's.
        if self._attack is not None and use.key != self.attempt.use.key:
            self._attack = None
        self.attempt = replace(
            self.attempt, use=use, source_name=self._source.name, source_ref=self._source.ref
        )
        self.setWindowTitle(f"{ACTION_NAME}: {use.power_name}")
        self.set_heading(self._heading_text())
        self.sync()

    # -- the parties -----------------------------------------------------------

    def set_party_character(self, ref: str, character: Character | None) -> None:
        """A fresher model for one party (a player's new snapshot).

        The primary's Defense decides the Hit tick, so a new one re-reads it — unless the
        GM has set Hit by hand, which a changed sheet does not overrule.
        """
        if ref == self._source.ref:
            had = self._source.character is not None
            self._source.character = character
            if not had and character is not None and self.phase == PHASE_SETUP:
                self._fill_powers()
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
        if self.heals():
            return ""
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

    def set_attack(self, total: int, die: int, *, reroll: bool = False) -> None:
        """The attack check against the primary came in: say how it went, and tick Hit.

        A *reroll* replaces the roll before it and judges Hit afresh. Once the area is
        resolved, an attack that comes in changes nothing.
        """
        if self.phase != PHASE_SETUP:
            return
        self._attack = (total, die)
        self._attack_rerolled = reroll
        self._asked_attack = False
        self._judge_hit()
        self.sync()

    def set_minion_rules(self, rules: MinionRules) -> None:
        """The session's minion rules, which decide whether a routine attack counts."""
        self._minion_rules = rules
        self._judge_hit()
        self.sync()

    def routine_attack(self) -> int | None:
        """The attack's routine result against a minion primary, or ``None``.

        A non-minion may attack a minion as a routine check (PDF p25): 10 plus the
        attack, the GM's adjustment in. ``None`` when there is no attack check, the
        primary is not a minion, the attacker is one, or the session plays without it.
        """
        use = self.attempt.use
        row = self._row(self.primary_ref())
        if use.attack is None or row is None or not self._minion_rules.routine_attacks:
            return None
        if not row.party.minion or self._source.minion:
            return None
        return routine_total(use.attack) + self._attack_adjust.value()

    def _judge_hit(self) -> None:
        """Tick Hit from the attack's total, the GM's adjustment in, against the Defense.

        Against a minion the routine result counts too: the attacker would have taken
        10 had the roll been worse, so a hit either way is a hit — and one the routine
        result makes needs no roll at all.
        """
        dc = self._defense_dc()
        if dc is None:
            return
        routine = self.routine_attack()
        if self._attack is None:
            if routine is not None and routine >= dc and not self._hit_by_hand:
                self._hit.setChecked(True)
            return
        self._hit_by_hand = False
        rolled = self._attack[0] + self._attack_adjust.value()
        self._hit.setChecked(rolled >= dc or (routine is not None and routine >= dc))

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
        self._judge_hit()
        self.sync()

    def attack_spec(self) -> RollSpec:
        """The attack as rolled: the GM's adjustment is added when it is judged, not here,
        so an attack the player rolled before the GM set one counts it the same way."""
        return area_attack_spec(
            self.attempt.use,
            action_id=self.action_id,
            source=self._source.name,
            target=self.primary_name(),
        )

    def _emit_attack(self) -> None:
        if not self._is_npc():
            self._asked_attack = True
        self.attackRequested.emit(self.attack_spec(), self._source)
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
            use=self.adjusted_use(),
            source=self._source,
            targets=tuple(
                AreaTarget(
                    party=row.party,
                    primary=hit and row.party.ref == primary,
                    cover=int(row.cover.currentData() or 0),
                    adjust=row.adjust,
                )
                for row in self.included()
            ),
            mode=self.mode(),
            heals=self.heals(),
            primary_ref=primary,
        )

    def sync(self) -> None:
        if not hasattr(self, "_mode_host"):
            return  # still being built
        use = self.adjusted_use()
        heals = self.heals()
        self._use_note.setText(use_summary(use, self._data))
        self._primary_box.setVisible(not heals)
        self._mode_host.setVisible(not heals)
        self._attack_caption.setVisible(not heals)
        self._attack_adjust.setVisible(not heals)
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
        if not primary or use.attack is None:
            # Nothing to add: the combo reads "aimed at a point", or Hit is already ticked.
            self._attack_note.setText("")
        elif self._attack is None:
            routine = self.routine_attack()
            dc = self._defense_dc()
            if routine is not None and dc is not None and routine >= dc:
                # A minion primary a routine attack hits: nothing needs rolling.
                self._attack_note.setText(
                    f"Routine attack {routine} vs. Defense DC {dc} hits the minion — "
                    "no roll needed."
                )
                self._attack_note.setStyleSheet(tinted_style("tint.better", bold=False))
            else:
                waiting = f" — waiting for {self._source.name}'s roll" if self._asked_attack else ""
                self._attack_note.setText(f"Attack {_signed(use.attack)}, not rolled yet{waiting}.")
                self._attack_note.setStyleSheet(muted_style(italic=True))
        else:
            total, die = self._attack
            adjust = self._attack_adjust.value()
            shown = f"{total} {_signed(adjust)} = {total + adjust}" if adjust else str(total)
            dc = self._defense_dc()
            against = f" vs. Defense DC {dc}" if dc is not None else ""
            natural = f" (natural {die})" if die in (1, 20) else ""
            rerolled = " (rerolled)" if self._attack_rerolled else ""
            self._attack_note.setText(f"Attack rolled {shown}{natural}{rerolled}{against}.")
            self._attack_note.setStyleSheet(
                tinted_style("tint.better" if self.hit() else "tint.worse", bold=False)
            )
        if self.phase != PHASE_SETUP:
            return
        request = self.mode() == AREA_MODE_REQUEST
        for row in self._rows:
            # The attacker is never caught in their own area — but a Healing may include
            # its user (the book's Close Sphere Area Healing).
            mine = row.party.ref == self._source.ref and not heals
            row.include.setEnabled(not mine)
            if mine and row.include.isChecked():
                row.include.setChecked(False)
            on = row.include.isChecked()
            for widget in (row.cover, row.modifier):
                widget.setEnabled(on and not heals)
            # Said for every row, muted when it is out: what ticking it in would mean.
            is_primary = on and self.hit() and row.party.ref == primary
            if heals:
                row.role.setText("Healed")
                row.role.setStyleSheet("" if on else muted_style())
            elif request:
                row.role.setText("Saves from the card" if is_primary else "Rolls from the cards")
                row.role.setStyleSheet(muted_style(italic=True))
            elif row.party.kind == PARTY_PLAYER:
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
        if heals:
            who = "rolled here" if self._is_npc() else f"asked of {self._source.name}"
            parts.append(f"one check, {who}")
            self.go.setText("🎲 Heal")
        elif request:
            parts.append("a Dodge card and a save card in the history")
            self.go.setText("Request")
        else:
            if npcs:
                parts.append(f"{npcs} rolled here")
            if players:
                parts.append(f"{players} asked to roll")
            self.go.setText("🎲 Resolve")
        self.summary.setText(" · ".join(parts))
        self.go.setEnabled(count > 0)

    def lock_setup(self) -> None:
        super().lock_setup()
        for widget in (
            self._power,
            self._rank_adjust,
            self._attack_adjust,
            self._primary,
            self._hit,
            self._roll_attack,
            *self._mode_buttons.values(),
        ):
            widget.setEnabled(False)
        if self._attacker is not None:
            self._attacker.setEnabled(False)
        self._roll_attack.hide()

    def _emit_go(self) -> None:
        plan = self.plan()
        self.start_running()
        self.resolveRequested.emit(plan)

    @property
    def _resolve(self) -> QPushButton:
        """The Resolve button, by the name it always had."""
        return self.go
