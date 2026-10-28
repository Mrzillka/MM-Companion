"""Aimed attacks, the GM's half: judge the hit, resolve the save, land what it does.

A player right-clicks an attack line and picks a creature off the Scene
(``ui/attack_targets.py``). Their app declares it (:data:`KIND_ATTACK`) and rolls it;
this controller ties the two together by the declaration's ``action_id``, and puts an
**Accept / Decline** card in the GM's history.

**Accept** makes every check the attack needs and lands it:

* the **hit**, against the target's Defense (``rules.judge_attack``) — routine against a
  minion when the session allows it, and a natural 20 that counts is a critical;
* a **minion** struck by a critical takes the worst, unrolled (``critical_answer``);
* **one** save from an NPC is rolled by the server straight away, at the GM roller's
  current bonus and penalty; **several** (a Linked power's, say) open
  :class:`AttackWindow`, where the GM can change each resistance and adjust each roll;
* a **player** target is asked for each save, as an area attack asks.

**Decline** settles the card and nothing else happens. An attack the GM aims from one
of their own NPCs (a card's tile, an NPC sheet) needs no accepting: it runs straight
through once its die is in.
"""

from __future__ import annotations

import html
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    MinionRules,
    RollSpec,
    answer_rung,
    critical_answer,
    defense_class,
    follow_ups_for_result,
    judge_attack,
    localize_spec,
    minion_save_spec,
    resistance_total,
    resolve_rung,
    routine_total,
    rung_caption,
)
from mm_companion.ui import theme
from mm_companion.ui.action_run import ActionRun, group_line, total_of
from mm_companion.ui.action_ui import ADJUST_LIMIT
from mm_companion.ui.attack_targets import KIND_ATTACK, AttackDeclaration, aimed_spec
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty, new_action_id
from mm_companion.ui.roll_history import ActionCard
from mm_companion.ui.widgets import guard_wheel, make_signed_spin_box

ACCEPT_CAPTION = "Accept"
DECLINE_CAPTION = "Decline"


class AttackHost(Protocol):
    """What the controller needs from the GM window (``AreaHost``'s subset, plus one)."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]: ...

    def impress_apply(self, party: ImpressParty, changes: list) -> bool: ...

    def impress_notice(self, text: str, colour: str) -> None: ...

    def impress_show_offline(self, record: dict) -> None: ...

    def action_add_card(self, card: ActionCard) -> None: ...

    def area_roll_attack(self, spec: RollSpec, attacker=None) -> None: ...

    def minion_rules(self) -> MinionRules: ...

    def roller_adjustment(self) -> int: ...


@dataclass
class _Aimed:
    """One aimed attack the GM has been told about and not settled yet."""

    declaration: AttackDeclaration
    source: ImpressParty
    run: ActionRun
    card: ActionCard | None = None
    roll: dict | None = None
    accepted: bool = False
    resolving: bool = False
    done: bool = False
    window: AttackWindow | None = None
    said: list[str] = field(default_factory=list)


class AttackController(QObject):
    """Runs every aimed attack the GM window resolves (see the module docstring)."""

    def __init__(self, data: GameData, bridge, host: AttackHost, parent_window: QWidget) -> None:
        super().__init__(parent_window)
        self._data = data
        self._bridge = bridge
        self._host = host
        self._parent_window = parent_window
        self._aimed: dict[str, _Aimed] = {}
        #: A roll that came in before its declaration did, by action id.
        self._early: dict[str, dict] = {}
        bridge.actionRequested.connect(self._on_action_request)
        bridge.rollAdded.connect(self.take_roll)

    @property
    def aimed(self) -> dict[str, _Aimed]:
        return dict(self._aimed)

    # -- being told --------------------------------------------------------------

    def _on_action_request(
        self, kind: str, action_id: str, player_id: str, payload: object
    ) -> None:
        if kind == KIND_ATTACK:
            self.declare(player_id, payload)

    def declare(self, player_id: str, payload: object) -> _Aimed | None:
        """A player aimed an attack: ask the GM to accept it."""
        declaration = AttackDeclaration.from_dict(payload)
        if declaration is None:
            return None
        existing = self._aimed.get(declaration.action_id)
        if existing is not None:
            return existing
        sources, _targets = self._host.impress_parties()
        source = next(
            (p for p in sources if p.kind == PARTY_PLAYER and p.source == player_id), None
        ) or ImpressParty(
            ref=f"player:{player_id}",
            name=declaration.source_name or "A player",
            kind=PARTY_PLAYER,
            source=player_id,
        )
        aimed = self._make(declaration, source)
        card = ActionCard(declaration.action_id, "", ACCEPT_CAPTION, second=DECLINE_CAPTION)
        card.actionRequested.connect(self.accept)
        card.secondRequested.connect(self.decline)
        aimed.card = card
        self._describe(aimed)
        self._host.action_add_card(card)
        return aimed

    def aim_own(self, source: ImpressParty, spec: RollSpec, target_ref: str, target_name: str):
        """The GM aims one of their own creature's attacks: roll it, then resolve it.

        No card to accept — the GM chose this — but the roll goes through the GM's
        roller like any NPC attack (a minion's 20 no critical), and the resolution
        waits for it the way a player's does.
        """
        action_id = new_action_id()
        spec = aimed_spec(spec, action_id, target_name, source.name)
        declaration = AttackDeclaration(action_id, target_ref, target_name, spec, source.name)
        aimed = self._make(declaration, source)
        aimed.accepted = True
        self._host.area_roll_attack(spec, source.character)
        return aimed

    def _make(self, declaration: AttackDeclaration, source: ImpressParty) -> _Aimed:
        run = ActionRun(
            self._data,
            self._bridge,
            self._host,
            declaration.action_id,
            title=f"{source.name} → {declaration.target_name}",
            asker="The GM",
            parent=self,
        )
        aimed = _Aimed(declaration, source, run)
        self._aimed[declaration.action_id] = aimed
        early = self._early.pop(declaration.action_id, None)
        if early is not None:
            self._take(aimed, early)
        return aimed

    def take_roll(self, roll: object) -> None:
        """A roll came in: an aimed attack's own die, when its spec says so."""
        if not isinstance(roll, dict) or roll.get("group") or roll.get("kind", "roll") != "roll":
            return
        raw = roll.get("spec")
        # Off the air the GM's own roll carries the spec itself, not its wire form.
        spec = raw if isinstance(raw, RollSpec) else RollSpec.from_dict(raw)
        if spec is None or not spec.action_id or spec.follow_up is None:
            return
        aimed = self._aimed.get(spec.action_id)
        if aimed is None:
            self._early[spec.action_id] = roll
            return
        self._take(aimed, roll)

    def _take(self, aimed: _Aimed, roll: dict) -> None:
        if aimed.resolving or aimed.done:
            return
        aimed.roll = roll
        self._describe(aimed)
        if aimed.accepted:
            self.resolve(aimed)

    # -- the GM's answer -----------------------------------------------------------

    def accept(self, action_id: str) -> None:
        aimed = self._aimed.get(action_id)
        if aimed is None or aimed.done:
            return
        aimed.accepted = True
        if aimed.roll is None:
            if aimed.card is not None:
                aimed.card.set_status("Accepted — waiting for the roll.", button=ACCEPT_CAPTION)
                aimed.card.button.setEnabled(False)
            return
        self.resolve(aimed)

    def decline(self, action_id: str) -> None:
        aimed = self._aimed.get(action_id)
        if aimed is None or aimed.done:
            return
        self._finish(aimed, "Declined — nothing happens.", notice=False)

    # -- resolving ---------------------------------------------------------------------

    def _target(self, aimed: _Aimed) -> ImpressParty | None:
        """The target as the board stands now (its sheet as fresh as the GM has it)."""
        _sources, targets = self._host.impress_parties()
        return next((p for p in targets if p.ref == aimed.declaration.target_ref), None)

    def resolve(self, aimed: _Aimed) -> None:
        """Judge the hit, and set the save (or the critical's worst) going."""
        if aimed.resolving or aimed.done or aimed.roll is None:
            return
        aimed.resolving = True
        roll = aimed.roll
        target = self._target(aimed)
        if target is None or target.character is None:
            self._finish(aimed, f"{aimed.declaration.target_name} is not on the Scene any more.")
            return
        rules = self._host.minion_rules()
        spec = aimed.declaration.spec
        die = int(roll.get("die", 0))
        modifier = int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
        dc = defense_class(target.character, self._data)
        routine = None
        if rules.routine_attacks and target.minion and not aimed.source.minion:
            routine = routine_total(modifier)
        verdict = judge_attack(
            modifier,
            die,
            dc,
            natural=bool(roll.get("critical")) if die in (1, 20) else not roll.get("routine"),
            added_success=not roll.get("minion"),
            routine_total=routine,
        )
        total = total_of(roll)
        if not verdict.hit:
            self._finish(aimed, f"Missed — {total} against Defense DC {dc}.")
            return
        how = (
            "routine hit" if verdict.by_routine else ("critical hit" if verdict.critical else "hit")
        )
        aimed.said.append(f"{how} ({total} vs DC {dc})")
        saves = follow_ups_for_result(
            spec,
            die,
            max(verdict.degree, 1),
            self._data,
            total,
            added_success=not roll.get("minion"),
        )
        if not saves:
            self._finish(aimed, "; ".join(aimed.said))
            return
        if target.kind == PARTY_NPC:
            if verdict.critical and target.minion and rules.crit_auto_fails:
                self._crit_minion(aimed, target, saves, rules)
                return
            if len(saves) == 1:
                self._roll_npc_saves(
                    aimed, target, [(saves[0], saves[0].trait_key, self._host.roller_adjustment())]
                )
                return
            self._open_window(aimed, target, saves)
            return
        self._ask_player(aimed, target, list(saves))

    def _crit_minion(self, aimed, target, saves, rules) -> None:
        """A critical hit on a minion: every save's worst, unrolled (PDF p197)."""
        captions = []
        for save in saves:
            if save.ladder is None:
                continue
            rung = critical_answer(save.ladder, target.character, self._data, rules)
            if rung is None:
                captions.append("unaffected")
                continue
            captions.append(self._land(aimed, target, rung))
        said = [c for c in captions if c]
        aimed.said.append("no save — " + ("; ".join(said) if said else "nothing lands"))
        self._finish(aimed, "; ".join(aimed.said))

    def _save_spec(self, target: ImpressParty, save: RollSpec, key: str, adjust: int) -> RollSpec:
        """*save* as *target* rolls it: their own number for resistance *key*, plus *adjust*."""
        char = target.character or Character()
        if key and key != save.trait_key:
            save = replace(save, trait_key=key, label=f"{self._resistance_name(key)} vs. {save.dc}")
        own = localize_spec(save, char, self._data)
        own = replace(own, modifier=own.modifier + adjust, trait_key="")
        if target.minion:
            own = minion_save_spec(own, self._host.minion_rules())
        return own

    def _resistance_name(self, key: str) -> str:
        return next((r.name for r in self._data.resistances if r.key == key), key.title())

    def _roll_npc_saves(self, aimed, target, chosen: list[tuple[RollSpec, str, int]]) -> None:
        """Roll the NPC's saves through the server as one group, then land each."""
        lines = []
        about = []
        rules = self._host.minion_rules()
        for save, key, adjust in chosen:
            spec = self._save_spec(target, save, key, adjust)
            lines.append(
                group_line(
                    target.name,
                    spec,
                    title=aimed.run.title,
                    minion=target.minion and rules.no_added_success,
                )
            )
            about.append(save)

        def landed(pairs) -> None:
            captions = []
            for save, record in pairs:
                degree = record.get("degree")
                if save.ladder is None or degree is None:
                    continue
                rung = answer_rung(save.ladder, int(degree), target.character, self._data, rules)
                captions.append(self._land(aimed, target, rung) if rung is not None else "")
            said = [c for c in captions if c]
            aimed.said.append("; ".join(said) if said else "resisted")
            self._finish(aimed, "; ".join(aimed.said))

        aimed.run.roll_group(lines, about, landed)

    def _ask_player(self, aimed, target, queue: list[RollSpec]) -> None:
        """Ask a player for each save in turn, landing each as it comes back."""
        if not queue:
            self._finish(aimed, "; ".join(aimed.said))
            return
        save = queue.pop(0)
        spec = replace(save, targets=(target.name,), action_id=aimed.declaration.action_id)
        rules = self._host.minion_rules()

        def answered(_about, record) -> None:
            degree = record.get("degree")
            caption = ""
            if save.ladder is not None and degree is not None:
                rung = answer_rung(save.ladder, int(degree), target.character, self._data, rules)
                if rung is not None:
                    caption = self._land(aimed, target, rung)
            aimed.said.append(caption or "resisted")
            if caption:
                aimed.run.tell(target, f"{aimed.declaration.spec.label}: {caption}")
            self._ask_player(aimed, target, queue)

        if not aimed.run.ask(target, spec, save, answered):
            self._finish(aimed, f"Could not ask {target.name} to save — no session.")
            return
        if aimed.card is not None:
            aimed.card.set_status(f"Waiting for {target.name}'s save…", button=ACCEPT_CAPTION)

    def _land(self, aimed: _Aimed, target: ImpressParty, rung) -> str:
        """Put *rung* on *target*; what it did, in words."""
        character = deepcopy(target.character) if target.character is not None else Character()
        changes = list(resolve_rung(character, rung, self._data, source=aimed.source.name))
        caption = rung_caption(rung, self._data)
        if changes and not self._host.impress_apply(target, changes):
            return f"{caption} (not delivered)"
        return caption

    # -- several saves: the window ------------------------------------------------------

    def _open_window(self, aimed: _Aimed, target: ImpressParty, saves: list[RollSpec]) -> None:
        window = AttackWindow(
            self._data,
            target,
            saves,
            adjust=self._host.roller_adjustment(),
            title=aimed.run.title,
            parent=self._parent_window,
        )
        aimed.window = window
        window.chosen.connect(lambda chosen, a=aimed, t=target: self._roll_npc_saves(a, t, chosen))
        window.rejected.connect(
            lambda a=aimed: a.done or self._finish(a, "Cancelled — nothing rolled.")
        )
        if aimed.card is not None:
            aimed.card.set_status("Choose the saves in the window.", button=ACCEPT_CAPTION)
        window.show()

    # -- saying so ---------------------------------------------------------------------

    def _describe(self, aimed: _Aimed) -> None:
        if aimed.card is None:
            return
        decl = aimed.declaration
        text = (
            f"<b>{html.escape(aimed.source.name)}</b> attacks "
            f"<b>{html.escape(decl.target_name)}</b> ({html.escape(decl.spec.label)})"
        )
        if aimed.roll is not None:
            text += f" — rolled <b>{total_of(aimed.roll)}</b>"
        aimed.card.set_text(text)
        if aimed.accepted:
            return
        if aimed.roll is None:
            aimed.card.set_status("Waiting for the roll…")
        else:
            aimed.card.set_status("Accept to judge the hit and roll the save.")

    def _finish(self, aimed: _Aimed, text: str, *, notice: bool = True) -> None:
        aimed.done = True
        aimed.resolving = False
        summary = f"{aimed.source.name} → {aimed.declaration.target_name}: {text}"
        if notice:
            self._host.impress_notice(summary, theme.color("accent"))
        if aimed.card is not None:
            aimed.card.set_done(text)
        if aimed.window is not None:
            aimed.window.close()
        aimed.run.close()


class AttackWindow(QDialog):
    """Several saves from one aimed attack: a resistance and an adjustment for each.

    Opened when an NPC must make more than one save (a Linked power's Damage and
    Affliction, say). Each row is one save: which resistance the creature uses — the
    attack's own by default, any other the GM picks — and a bonus or penalty on that
    roll alone, starting at the roller's. **Roll saves** rolls them all at once.
    """

    #: ``[(save spec, resistance key, adjustment), …]`` — the GM's choices.
    chosen = Signal(object)

    def __init__(
        self,
        data: GameData,
        target: ImpressParty,
        saves: list[RollSpec],
        *,
        adjust: int = 0,
        title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Saves: {title}" if title else "Saves")
        self._saves = list(saves)
        self._rows: list[tuple[QComboBox, object]] = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{html.escape(target.name)}</b> makes these saves:"))
        grid = QGridLayout()
        grid.addWidget(QLabel("Save"), 0, 0)
        grid.addWidget(QLabel("Resists with"), 0, 1)
        grid.addWidget(QLabel("Adjust"), 0, 2)
        character = target.character or Character()
        resistances = [r for r in data.resistances if r.key != data.system.trait_keys.defense]
        for row, save in enumerate(self._saves, start=1):
            grid.addWidget(QLabel(html.escape(save.label)), row, 0)
            combo = QComboBox()
            guard_wheel(combo)
            for resistance in resistances:
                value = resistance_total(character, data, resistance.key)
                combo.addItem(f"{resistance.name} {value:+d}", resistance.key)
            combo.setCurrentIndex(max(0, combo.findData(save.trait_key)))
            grid.addWidget(combo, row, 1)
            spin = make_signed_spin_box(ADJUST_LIMIT)
            spin.setValue(adjust)
            grid.addWidget(spin, row, 2)
            self._rows.append((combo, spin))
        layout.addLayout(grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Roll saves")
        buttons.accepted.connect(self._roll)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choices(self) -> list[tuple[RollSpec, str, int]]:
        return [
            (save, str(combo.currentData() or save.trait_key), int(spin.value()))
            for save, (combo, spin) in zip(self._saves, self._rows, strict=True)
        ]

    def set_choice(self, index: int, key: str, adjust: int) -> None:
        """Set one row (for the tests)."""
        combo, spin = self._rows[index]
        combo.setCurrentIndex(max(0, combo.findData(key)))
        spin.setValue(adjust)

    def _roll(self) -> None:
        self.chosen.emit(self.choices())
        self.accept()
