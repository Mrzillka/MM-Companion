"""Targeted combat actions, the GM's half — Aid, Feint and Trick (combat maneuvers
plan, phase 8; the rules are :mod:`~mm_companion.core.rules.targeted`).

A player picks the tile, the creature (and for Aid the ally) off the Scene, and one of
their attacks; their app declares it (:data:`KIND_TARGETED`) and rolls the check. This
controller ties the two together by the declaration's ``action_id`` and puts an
**Accept / Decline** card in the GM's history, as an aimed attack does.

**Accept** settles it:

* **against a DC** (Aid's 10): the check's degree, judged as an attack is;
* **opposed** (Feint, Trick): the target resists the check's total with the best of its
  candidates — rolled by the server for a creature nobody plays, asked of a player;

and what the actor's degree lands (``wins`` / ``losses``) goes on the target, or on the
ally, each condition naming whom it is against. Those are *opposing* conditions:
the aimed-attack judge (``ui/attack_gm.py``) counts and spends them.

The GM's own (an NPC acting off the Rolls block's Actions popup) needs no accepting:
:meth:`TargetedController.act_own` rolls the check as the NPC and resolves it once the
die is in. Every roll lands in one table in the history, the actor's check and the
target's resistance as two rows.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.data_loader import ConditionChange, GameData
from mm_companion.core.data_loader.game_data import TARGETED_ON_ALLY
from mm_companion.core.rules import (
    MinionRules,
    RollSpec,
    actor_degree,
    concrete_changes,
    judge_attack,
    localize_spec,
    opposed_dc,
    targeted_action,
    targeted_outcome,
    targeted_outcome_text,
    targeted_resist_spec,
    targeted_title,
)
from mm_companion.core.session.model import KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.action_run import ActionRun, group_line, total_of
from mm_companion.ui.attack_gm import ACCEPT_CAPTION, DECLINE_CAPTION, roll_text
from mm_companion.ui.attack_targets import (
    KIND_TARGETED,
    TargetedDeclaration,
    targeted_roll_spec,
    targeted_table_title,
)
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty, new_action_id
from mm_companion.ui.roll_history import ActionCard


class TargetedHost(Protocol):
    """What the controller needs from the GM window."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]: ...

    def impress_notice(self, text: str, colour: str) -> None: ...

    def impress_show_offline(self, record: dict) -> None: ...

    def action_add_card(self, card: ActionCard) -> None: ...

    def area_roll_attack(self, spec: RollSpec, attacker=None, *, before=None) -> None: ...

    def minion_rules(self) -> MinionRules: ...

    def roller_adjustment(self) -> int: ...

    def combat_land(self, party: ImpressParty, changes: tuple[ConditionChange, ...]) -> bool: ...


@dataclass
class _Targeted:
    """One targeted action the GM has been told about and not settled yet."""

    declaration: TargetedDeclaration
    source: ImpressParty
    run: ActionRun
    card: ActionCard | None = None
    roll: dict | None = None
    accepted: bool = False
    resolving: bool = False
    done: bool = False
    said: list[str] = field(default_factory=list)


class TargetedController(QObject):
    """Runs every targeted action the GM window resolves (see the module docstring)."""

    def __init__(self, data: GameData, bridge, host: TargetedHost, parent_window: QWidget) -> None:
        super().__init__(parent_window)
        self._data = data
        self._bridge = bridge
        self._host = host
        self._acts: dict[str, _Targeted] = {}
        #: A check that came in before its declaration did, by action id.
        self._early: dict[str, dict] = {}
        bridge.actionRequested.connect(self._on_action_request)
        bridge.rollAdded.connect(self.take_roll)

    @property
    def acts(self) -> dict[str, _Targeted]:
        return dict(self._acts)

    # -- being told --------------------------------------------------------------------

    def _on_action_request(
        self, kind: str, action_id: str, player_id: str, payload: object
    ) -> None:
        if kind == KIND_TARGETED:
            self.declare(player_id, payload)

    def declare(self, player_id: str, payload: object) -> _Targeted | None:
        """A player took a targeted action: ask the GM to accept it."""
        declaration = TargetedDeclaration.from_dict(payload)
        if declaration is None or targeted_action(self._data, declaration.action) is None:
            return None
        existing = self._acts.get(declaration.action_id)
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
        act = self._make(declaration, source)
        card = ActionCard(declaration.action_id, "", ACCEPT_CAPTION, second=DECLINE_CAPTION)
        card.actionRequested.connect(self.accept)
        card.secondRequested.connect(self.decline)
        act.card = card
        self._describe(act)
        self._host.action_add_card(card)
        return act

    def act_own(
        self,
        source: ImpressParty,
        action_id: str,
        option: str,
        target: ImpressParty,
        spec: RollSpec,
        ally: ImpressParty | None = None,
    ) -> _Targeted | None:
        """One of the GM's own creatures takes a targeted action: roll its check on the
        GM's roller as it, and resolve it once the die is in (no card to accept)."""
        action = targeted_action(self._data, action_id)
        if action is None:
            return None
        run_id = new_action_id()
        title = targeted_table_title(
            action.name, source.name, target.name, ally.name if ally is not None else ""
        )
        spec = targeted_roll_spec(spec, run_id, title, source.name)
        declaration = TargetedDeclaration(
            run_id,
            action_id,
            target.ref,
            target.name,
            spec,
            source.name,
            option,
            ally.ref if ally is not None else "",
            ally.name if ally is not None else "",
        )
        act = self._make(declaration, source)
        act.accepted = True
        self._host.area_roll_attack(spec, source.character)
        return act

    def _make(self, declaration: TargetedDeclaration, source: ImpressParty) -> _Targeted:
        run = ActionRun(
            self._data,
            self._bridge,
            self._host,
            declaration.action_id,
            title=declaration.spec.title or declaration.target_name,
            asker="The GM",
            parent=self,
        )
        act = _Targeted(declaration, source, run)
        self._acts[declaration.action_id] = act
        early = self._early.pop(declaration.action_id, None)
        if early is not None:
            self._take(act, early)
        return act

    def take_roll(self, roll: object) -> None:
        """A roll came in: a resistance one of the actions rolled or asked for (its run
        gathers it), or an action's own check."""
        if not isinstance(roll, dict):
            return
        for act in list(self._acts.values()):
            if act.run.take_roll(roll):
                return
        if roll.get("group") or roll.get("kind", KIND_ROLL) != KIND_ROLL:
            return
        raw = roll.get("spec")
        # Off the air the GM's own roll carries the spec itself, not its wire form.
        spec = raw if isinstance(raw, RollSpec) else RollSpec.from_dict(raw)
        if spec is None or not spec.action_id or spec.follow_up is not None:
            return
        act = self._acts.get(spec.action_id)
        if act is None:
            if spec.targets and spec.title:
                self._early[spec.action_id] = roll
            return
        if spec.targets != (act.declaration.source_name,):
            return
        self._take(act, roll)

    def _take(self, act: _Targeted, roll: dict) -> None:
        if act.resolving or act.done:
            return
        act.roll = roll
        self._describe(act)
        if act.accepted:
            self.resolve(act)

    # -- the GM's answer -----------------------------------------------------------------

    def accept(self, action_id: str) -> None:
        act = self._acts.get(action_id)
        if act is None or act.done:
            return
        act.accepted = True
        if act.roll is None:
            if act.card is not None:
                act.card.set_status("Accepted — waiting for the roll.", button=ACCEPT_CAPTION)
                act.card.button.setEnabled(False)
            return
        self.resolve(act)

    def decline(self, action_id: str) -> None:
        act = self._acts.get(action_id)
        if act is None or act.done:
            return
        self._finish(act, "Declined — nothing happens.", notice=False)

    # -- resolving --------------------------------------------------------------------------

    def _party(self, ref: str) -> ImpressParty | None:
        """A creature on the board by its Scene ref (its sheet as fresh as the GM has it)."""
        _sources, targets = self._host.impress_parties()
        return next((p for p in targets if p.ref == ref), None)

    def resolve(self, act: _Targeted) -> None:
        """Judge the check against its DC, or set the target's resistance going."""
        if act.resolving or act.done or act.roll is None:
            return
        decl = act.declaration
        action = targeted_action(self._data, decl.action)
        if action is None:
            self._finish(act, "That action is not known here.")
            return
        target = self._party(decl.target_ref)
        if target is None:
            self._finish(act, f"{decl.target_name} is not on the Scene any more.")
            return
        ally = None
        if action.targeted is not None and action.targeted.ally:
            ally = self._party(decl.ally_ref)
            if ally is None:
                self._finish(act, f"{decl.ally_name or 'The ally'} is not on the Scene any more.")
                return
        act.resolving = True
        self._lock_card(act)
        roll = act.roll
        die = int(roll.get("die", 0))
        modifier = int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
        total = total_of(roll)
        title = targeted_title(action, decl.option)
        dc = decl.spec.dc
        if dc is not None:
            verdict = judge_attack(
                modifier,
                die,
                dc,
                natural=bool(roll.get("critical")) if die in (1, 20) else not roll.get("routine"),
                added_success=not roll.get("minion"),
            )
            act.said.append(f"{total} vs DC {dc}")
            self._land(act, action, decl.option, verdict.degree, target, ally)
            return
        against = opposed_dc(total, die if not roll.get("minion") else 0, self._data)
        resist = targeted_resist_spec(self._data, action, title, against)
        if resist is None:
            self._finish(act, "Nothing to resist with.")
            return
        act.said.append(f"{total} to beat")
        rules = self._host.minion_rules()

        def landed(record: dict) -> None:
            degree = record.get("degree")
            act.said.append(
                f"{target.name} {total_of(record)} vs {against}"
                + (" — resisted" if isinstance(degree, int) and degree > 0 else "")
            )
            self._land(
                act,
                action,
                decl.option,
                actor_degree(int(degree) if isinstance(degree, int) else None),
                target,
                ally,
            )

        if target.kind == PARTY_NPC:
            own = localize_spec(resist, target.character, self._data)
            own = replace(own, modifier=own.modifier + self._host.roller_adjustment(), trait_key="")
            line = group_line(
                target.name,
                own,
                title=act.run.title,
                dc=against,
                minion=target.minion and rules.no_added_success,
            )
            if not act.run.roll_group(
                [line], [target], lambda pairs: landed(pairs[0][1]) if pairs else None
            ):
                self._finish(act, "The resistance did not reach the session — nothing rolled.")
            return
        ask = replace(resist, targets=(target.name,))
        if not act.run.ask(target, ask, target, lambda _about, record: landed(record)):
            self._finish(act, f"Could not ask {target.name} to resist — no session.")
            return
        if act.card is not None:
            act.card.set_status(f"Waiting for {target.name}'s roll…", button=ACCEPT_CAPTION)

    def _land(self, act, action, option: str, degree: int, target, ally) -> None:
        """Put what the actor's *degree* lands on its bearers, and settle."""
        decl = act.declaration
        changes = concrete_changes(
            targeted_outcome(action.effect_for(option), degree),
            actor_name=act.source.name,
            target_name=target.name,
        )
        if not changes:
            act.said.append("no effect")
            self._finish(act, "; ".join(act.said))
            return
        for bearer in (target, ally):
            if bearer is None:
                continue
            role = TARGETED_ON_ALLY if bearer is ally else "target"
            mine = tuple(change for on, change in changes if on == role)
            if not mine:
                continue
            text = targeted_outcome_text(self._data, mine, bearer.name)
            if self._host.combat_land(bearer, mine):
                act.said.append(text)
                if bearer.kind == PARTY_PLAYER:
                    act.run.tell(bearer, f"{decl.spec.title}: {text}")
            else:
                act.said.append(f"{text} (not delivered)")
        self._finish(act, "; ".join(act.said))

    # -- saying so ------------------------------------------------------------------------

    def _describe(self, act: _Targeted) -> None:
        if act.card is None:
            return
        decl = act.declaration
        action = targeted_action(self._data, decl.action)
        name = targeted_title(action, decl.option) if action is not None else decl.action
        whom = f"<b>{html.escape(decl.target_name)}</b>"
        if decl.ally_name:
            whom = f"<b>{html.escape(decl.ally_name)}</b> against {whom}"
        text = f"<b>{html.escape(act.source.name)}</b>: {html.escape(name)} → {whom}"
        if act.roll is not None:
            total, _space, breakdown = roll_text(act.roll).partition(" ")
            text += f" — rolled <b>{total}</b> {html.escape(breakdown)}"
        act.card.set_text(text)
        if act.accepted:
            return
        if act.roll is None:
            act.card.set_status("Waiting for the roll…")
        elif decl.spec.dc is not None:
            act.card.set_status(f"Accept to judge it against DC {decl.spec.dc}.")
        else:
            act.card.set_status(f"Accept to roll {decl.target_name}'s resistance.")

    @staticmethod
    def _lock_card(act: _Targeted) -> None:
        card = act.card
        if card is None:
            return
        card.button.setEnabled(False)
        if card.second_button is not None:
            card.second_button.setEnabled(False)

    def _finish(self, act: _Targeted, text: str, *, notice: bool = True) -> None:
        act.done = True
        act.resolving = False
        if notice:
            self._host.impress_notice(
                f"{act.declaration.spec.title or act.source.name}: {text}",
                theme.color("accent"),
            )
        if act.card is not None:
            act.card.set_done(text)
        act.run.close()
