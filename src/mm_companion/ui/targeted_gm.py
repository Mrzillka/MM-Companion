"""Targeted combat actions, the GM's half — Aid, Feint, Trick (combat maneuvers plan,
phase 8) and Grab, Trip, Disarm, Smash, Escape (phase 9); the rules are
:mod:`~mm_companion.core.rules.targeted`.

A player picks the tile, the creature (and for Aid the ally) off the Scene, and one of
their attacks; their app declares it (:data:`KIND_TARGETED`) and rolls the check. This
controller ties the two together by the declaration's ``action_id`` and puts an
**Accept / Decline** card in the GM's history, as an aimed attack does.

**Accept** settles it:

* **against a DC** (Aid's 10), or one **derived from the target** (Escape: 10 + the
  grabber's Strength): the check's degree, judged as an attack is;
* **against the target's Defense** (Grab, Trip, Disarm, Smash): judged as an aimed attack
  is — the opposing conditions between the two counted and spent, a minion target hit on
  the routine result. A miss ends it; a hit lands Smash's outcome, or goes on to …
* **an opposed stage** (Grab, Trip, Disarm): the actor's check (worked out on their
  sheet, carried in the declaration) — rolled by the server for an NPC, asked of a
  player — and then …
* **the target's resistance** (Feint, Trick, and that stage): the best of its
  candidates against the actor's total, rolled by the server for a creature nobody
  plays, asked of a player;

and what the actor's degree lands (``wins`` / ``losses``) goes on the target, the ally
or the actor, each condition naming whom it is against. Aid's, Feint's and Trick's are
*opposing* conditions: the aimed-attack judge (``ui/attack_gm.py``) counts and spends
them.

The GM's own (an NPC acting off the Rolls block's Actions popup) needs no accepting:
:meth:`TargetedController.act_own` rolls the check as the NPC and resolves it once the
die is in. Every roll lands in one table in the history, the actor's check and the
target's resistance as two rows.
"""

from __future__ import annotations

import html
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QWidget

from mm_companion.core.data_loader import ConditionChange, GameData
from mm_companion.core.data_loader.game_data import (
    TARGETED_ON_ACTOR,
    TARGETED_ON_ALLY,
    TARGETED_ON_TARGET,
)
from mm_companion.core.rules import (
    MinionRules,
    OpposingEffects,
    RollSpec,
    action_followup_spec,
    action_throw_damage,
    action_throw_limit,
    add_part,
    answer_rung,
    concrete_changes,
    defense_against,
    effective_ability,
    judge_attack,
    localize_spec,
    minion_save_spec,
    object_followup_caption,
    opposed_action_degree,
    opposing_effects,
    opposing_spent,
    resolve_rung,
    routine_total,
    rung_caption,
    targeted_action,
    targeted_dc_from,
    targeted_outcome_text,
    targeted_resist_spec,
    targeted_rung,
    targeted_title,
)
from mm_companion.core.session.model import KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.action_run import ActionRun, group_line, total_of
from mm_companion.ui.attack_gm import ACCEPT_CAPTION, DECLINE_CAPTION, roll_text_html
from mm_companion.ui.attack_targets import (
    KIND_TARGETED,
    TargetedDeclaration,
    targeted_roll_spec,
    targeted_table_title,
)
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty, new_action_id
from mm_companion.ui.roll_history import ActionCard
from mm_companion.ui.widgets import make_signed_spin_box, make_spin_box


class _RequiredInteger(QLineEdit):
    """A number the GM must supply, without quietly assuming zero."""

    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setValidator(QIntValidator(-100, 100, self))
        self.setPlaceholderText("Enter value")
        self.textChanged.connect(lambda _text: self.valueChanged.emit(self.value()))

    def value(self) -> int:
        return int(self.text()) if self.hasAcceptableInput() else 0

    def setValue(self, value: int) -> None:
        self.setText(str(value))


class TargetedHost(Protocol):
    """What the controller needs from the GM window."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]: ...

    def impress_notice(self, text: str, colour: str) -> None: ...

    def impress_show_offline(self, record: dict) -> None: ...

    def action_add_card(self, card: ActionCard) -> None: ...

    def area_roll_attack(self, spec: RollSpec, attacker=None, *, before=None) -> None: ...

    def minion_rules(self) -> MinionRules: ...

    def roller_adjustment(self) -> int: ...

    def action_result(self, title: str, text: str) -> None: ...

    def land_rung(self, party, changes, shake_off=None) -> tuple: ...

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
    critical: bool = False
    graze: bool = False


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
        self._followup_pending: dict[str, object] = {}
        self._followup_runs: list[ActionRun] = []
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
        *,
        opposed: RollSpec | None = None,
    ) -> _Targeted | None:
        """One of the GM's own creatures takes a targeted action: roll its check on the
        GM's roller as it, and resolve it once the die is in (no card to accept).
        *opposed* is its check in an opposed second stage (Grab's Strength)."""
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
            opposed,
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
        for run in list(self._followup_runs):
            if run.closed:
                self._followup_runs.remove(run)
            elif run.take_roll(roll):
                return
        raw_spec = roll.get("spec")
        made = raw_spec if isinstance(raw_spec, RollSpec) else RollSpec.from_dict(raw_spec)
        if made is not None and roll.get("kind", KIND_ROLL) == KIND_ROLL:
            pending = self._followup_pending.pop(made.action_id, None)
            if pending is not None:
                pending(roll)
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
        """Judge the check — against a DC, the target's Defense, or a DC its sheet gives —
        or set the target's resistance going."""
        if act.resolving or act.done or act.roll is None:
            return
        decl = act.declaration
        action = targeted_action(self._data, decl.action)
        if action is None or action.targeted is None:
            self._finish(act, "That action is not known here.")
            return
        rules = action.targeted
        target = self._party(decl.target_ref)
        if target is None:
            self._finish(act, f"{decl.target_name} is not on the Scene any more.")
            return
        ally = None
        if rules.ally:
            ally = self._party(decl.ally_ref)
            if ally is None:
                self._finish(act, f"{decl.ally_name or 'The ally'} is not on the Scene any more.")
                return
        if (rules.vs_defense or rules.dc_from is not None) and target.character is None:
            # A player whose sheet has not reached this table: their Defense (or
            # Strength) is unknown. Not over — the GM accepts again once it is in.
            waiting = f"{target.name}'s sheet has not reached this table yet"
            if act.card is None:
                self._finish(act, f"Cannot judge it: {waiting}.")
                return
            act.accepted = False
            act.card.set_status(f"{waiting} — Accept again once it has.", button=ACCEPT_CAPTION)
            act.card.button.setEnabled(True)
            return
        act.resolving = True
        self._lock_card(act)
        roll = act.roll
        if rules.vs_defense:
            self._judge_hit(act, action, target, ally, roll)
            return
        opposing = opposing_effects(
            act.source.character,
            act.source.name,
            target.character,
            target.name,
            self._data,
            check_only=True,
        )
        if opposing.notes:
            act.said.append("with " + ", ".join(opposing.notes))
        if opposing.any:
            self._spend(act, target, opposing)
        if opposing.attack:
            modifier = int(roll.get("bonus", 0)) - int(roll.get("penalty", 0)) + opposing.attack
            roll = {**roll, "bonus": max(0, modifier), "penalty": max(0, -modifier)}
        die = int(roll.get("die", 0))
        modifier = int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
        total = total_of(roll)
        dc = decl.spec.dc
        if rules.dc_from is not None:
            dc = targeted_dc_from(self._data, action, target.character)
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
        self._resist(act, action, target, ally, roll)

    def _judge_hit(self, act: _Targeted, action, target, ally, roll: dict) -> None:
        """The attack of a Grab, Trip, Disarm or Smash against the target's Defense,
        judged as an aimed attack is: the opposing conditions between the two count and
        are spent, and a minion target is hit on the routine result. A miss ends it."""
        decl = act.declaration
        rules = self._host.minion_rules()
        die = int(roll.get("die", 0))
        opposing = opposing_effects(
            act.source.character,
            act.source.name,
            target.character,
            target.name,
            self._data,
            attack_range=decl.spec.attack_range,
        )
        modifier = int(roll.get("bonus", 0)) - int(roll.get("penalty", 0)) + opposing.attack
        dc = defense_against(target.character, self._data, opposing)
        if opposing.notes:
            act.said.append("with " + ", ".join(opposing.notes))
        if opposing.any:
            self._spend(act, target, opposing)
        routine = None
        if rules.routine_attacks and target.minion and not act.source.minion:
            routine = routine_total(modifier)
        verdict = judge_attack(
            modifier,
            die,
            dc,
            natural=bool(roll.get("critical")) if die in (1, 20) else not roll.get("routine"),
            added_success=not roll.get("minion"),
            routine_total=routine,
        )
        total = routine if verdict.by_routine else total_of(roll) + opposing.attack
        if not verdict.hit:
            act.said.append(f"missed ({total} vs Defense {dc})")
            self._finish(act, "; ".join(act.said))
            return
        act.said.append(f"hit ({total} vs Defense {dc})")
        act.critical = verdict.critical
        act.graze = die == 1 and bool(roll.get("critical")) and not verdict.by_routine
        if action.targeted.opposed is None:
            self._land(act, action, decl.option, max(verdict.degree, 1), target, ally)
            return
        self._opposed(act, action, target, ally)

    def _spend(self, act: _Targeted, target, opposing: OpposingEffects) -> None:
        """Take the opposing conditions the attack counted off whoever bore them."""
        for party, spent in (
            (act.source, opposing.attacker_spent),
            (target, opposing.target_spent),
        ):
            if spent and not self._host.combat_land(party, opposing_spent(spent)):
                act.said.append(f"could not clear {party.name}'s used-up conditions")

    def _opposed(self, act: _Targeted, action, target, ally) -> None:
        """The second stage of a Grab, Trip or Disarm: the actor's check — rolled by the
        server for an NPC, asked of a player — whose total the target then resists."""
        source = act.source
        spec = act.declaration.opposed
        if spec is None:
            self._finish(act, "; ".join([*act.said, "nothing to make the opposed check with"]))
            return

        def rolled(record: dict) -> None:
            total = total_of(record)
            act.said.append(f"{source.name} {total}")
            self._resist(act, action, target, ally, record)

        if source.kind == PARTY_NPC:
            rules = self._host.minion_rules()
            own = add_part(spec, "GM adjustment", self._host.roller_adjustment())
            line = group_line(
                source.name,
                own,
                title=act.run.title,
                minion=source.minion and rules.no_added_success,
            )
            if not act.run.roll_group(
                [],
                [],
                lambda pairs: rolled(pairs[0][1]) if pairs else None,
                lead=line,
                lead_about=source,
            ):
                self._finish(act, "The opposed check did not reach the session — nothing rolled.")
            return
        ask = replace(spec, targets=(source.name,))
        if not act.run.ask(source, ask, source, lambda _about, record: rolled(record)):
            self._finish(act, f"Could not ask {source.name} for the opposed check — no session.")
            return
        if act.card is not None:
            act.card.set_status(f"Waiting for {source.name}'s roll…", button=ACCEPT_CAPTION)

    def _resist(self, act: _Targeted, action, target, ally, lead: dict) -> None:
        """The target resists the actor's *total* (a natural 20 on *die* counting as an
        added degree, unless a minion rolled it) with the best of its candidates."""
        decl = act.declaration
        title = targeted_title(action, decl.option)
        total = total_of(lead)
        resist = targeted_resist_spec(
            self._data, action, title, attack_range=decl.spec.attack_range
        )
        if resist is None:
            self._finish(act, "Nothing to resist with.")
            return
        resist = replace(resist, label=f"{resist.label} (opposed to {total})")
        act.said.append(f"{total} to oppose")
        rules = self._host.minion_rules()

        def landed(record: dict) -> None:
            if total_of(record) == total:
                actor_mod = int(lead.get("bonus", 0)) - int(lead.get("penalty", 0))
                target_mod = int(record.get("bonus", 0)) - int(record.get("penalty", 0))
                act.said.append(
                    f"Tied totals: modifiers {actor_mod:+d} vs {target_mod:+d}; "
                    + ("random tiebreak" if actor_mod == target_mod else "higher modifier wins")
                )
            degree = opposed_action_degree(
                int(lead.get("bonus", 0)) - int(lead.get("penalty", 0)),
                int(lead.get("die", 0)),
                int(record.get("bonus", 0)) - int(record.get("penalty", 0)),
                int(record.get("die", 0)),
                actor_natural=not lead.get("routine")
                and (not lead.get("reroll") or bool(lead.get("critical"))),
                target_natural=not record.get("routine")
                and (not record.get("reroll") or bool(record.get("critical"))),
                actor_added_success=not lead.get("minion"),
                target_added_success=not record.get("minion"),
            )
            act.said.append(
                f"{target.name} {total_of(record)} vs {total}"
                + (" — resisted" if degree < 0 else " — failed resistance")
                + f" ({abs(degree)} {'degree' if abs(degree) == 1 else 'degrees'})"
            )
            self._land(
                act,
                action,
                decl.option,
                degree,
                target,
                ally,
            )

        if target.kind == PARTY_NPC:
            own = localize_spec(resist, target.character, self._data)
            own = add_part(own, "GM adjustment", self._host.roller_adjustment(), trait_key="")
            line = group_line(
                target.name,
                own,
                title=act.run.title,
                minion=target.minion and rules.no_added_success,
            )
            if not act.run.roll_group(
                [],
                [],
                lambda pairs: landed(pairs[0][1]) if pairs else None,
                lead=line,
                lead_about=target,
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
        """Put what the actor's *degree* lands on its bearers — the target, the ally, the
        actor (Grab's Holding) — say what it does that nothing tracks, and settle."""
        decl = act.declaration
        rung = targeted_rung(action.effect_for(option), degree)
        changes = concrete_changes(
            rung.applies if rung is not None else (),
            actor_name=act.source.name,
            target_name=target.name,
        )
        if not changes and not (rung is not None and rung.text):
            act.said.append("no effect")
            self._finish(act, "; ".join(act.said))
            return
        for role, bearer in (
            (TARGETED_ON_TARGET, target),
            (TARGETED_ON_ALLY, ally),
            (TARGETED_ON_ACTOR, act.source),
        ):
            if bearer is None:
                continue
            mine = tuple(change for on, change in changes if on == role)
            if not mine:
                continue
            text = targeted_outcome_text(self._data, mine, bearer.name, bearer.character)
            if not self._host.combat_land(bearer, mine):
                act.said.append(f"{text or bearer.name} (not delivered)")
                continue
            if not text:
                continue
            act.said.append(text)
            if bearer.kind == PARTY_PLAYER:
                act.run.tell(bearer, f"{decl.spec.title}: {text}")
        if rung is not None and rung.text:
            act.said.append(
                rung.text.replace("{actor}", act.source.name).replace("{target}", target.name)
            )
        if action.reminder:
            act.said.append(action.reminder)
        self._finish(act, "; ".join(act.said))
        if degree > 0 and action.follow_up is not None:
            self._offer_followup(act, action, degree, target)

    def _offer_followup(self, act, action, degree: int, target) -> None:
        """Optional object/throw resolution stays beside the attack in history."""
        rule = action.follow_up
        follow_id = new_action_id()
        title = f"{act.source.name}: {action.name} follow-up on {target.name}"
        card = ActionCard(
            follow_id,
            html.escape(title),
            "Load object save" if rule.kind == "object" else "Resolve throw",
        )
        body = QWidget(card)
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        fields = {}
        card.fields = fields  # The live inline editors, also available to the UI driver.
        if rule.kind == "object":
            fields["name"] = QLineEdit(f"{target.name}'s object", body)
            fields["toughness"] = _RequiredInteger(body)
            fields["hits"] = make_spin_box(0, 100)
            fields["rank"] = make_spin_box(0, 100, value=act.declaration.spec.effect_rank or 0)
            fields["no_hit"] = QCheckBox("Apply Toughness defense exception", body)
            for key, label in (
                ("name", "Object"),
                ("toughness", "Object Toughness (enter)"),
                ("hits", "Existing object Hits"),
                ("rank", "Damage rank"),
                ("no_hit", "Defense exception"),
            ):
                form.addRow(label, fields[key])
        else:
            fields["mass"] = _RequiredInteger(body)
            fields["strength"] = make_signed_spin_box(100)
            if act.source.character is not None:
                fields["strength"].setValue(
                    effective_ability(act.source.character, self._data, rule.strength_trait)
                )
            fields["spaces"] = make_spin_box(0, 0)
            fields["rank"] = make_spin_box(0, rule.max_rank)
            detail = QLabel(body)
            detail.setWordWrap(True)
            for key, label in (
                ("mass", "Target mass rank (enter)"),
                ("strength", "Throwing Strength"),
                ("spaces", "Extra throw spaces (0 = no throw)"),
                ("rank", "Damage rank after falling protection"),
            ):
                form.addRow(label, fields[key])
            form.addRow(detail)

            def refresh():
                maximum = action_throw_limit(
                    degree, fields["strength"].value(), fields["mass"].value()
                )
                fields["spaces"].setMaximum(maximum)
                detail.setText(
                    f"{degree} degrees; Strength {fields['strength'].value()} "
                    f"vs mass rank {fields['mass'].value()}: up to {maximum} extra spaces."
                )

            fields["mass"].valueChanged.connect(refresh)
            fields["strength"].valueChanged.connect(refresh)
            fields["spaces"].valueChanged.connect(
                lambda value: fields["rank"].setValue(action_throw_damage(action, value))
            )
            refresh()
        card.add_controls(body)

        def load(_action_id):
            if rule.kind != "object" and (
                fields["spaces"].value() == 0 or fields["rank"].value() == 0
            ):
                text = "No additional falling Damage."
                card.set_done(text)
                body.hide()
                self._host.action_result(title, text)
                return
            required = fields["toughness"] if rule.kind == "object" else fields["mass"]
            if not required.hasAcceptableInput():
                card.set_status(
                    "Enter the object's Toughness."
                    if rule.kind == "object"
                    else "Enter the target's mass rank."
                )
                return
            spec = action_followup_spec(
                self._data,
                action,
                fields["rank"].value(),
                toughness=fields["toughness"].value() if rule.kind == "object" else 0,
                hits=fields["hits"].value() if rule.kind == "object" else 0,
                critical=act.critical if rule.kind == "object" else False,
                graze=act.graze if rule.kind == "object" else False,
            )
            if spec is None:
                return
            if rule.kind == "object" and fields["no_hit"].isChecked():
                spec = replace(
                    spec,
                    success_outcome=(
                        f"{spec.success_outcome} " "(unless its Toughness defense prevents the Hit)"
                    ),
                )
            if rule.kind != "object" and target.minion:
                spec = minion_save_spec(spec, self._host.minion_rules())
            name = fields["name"].text().strip() if rule.kind == "object" else target.name
            actor = "" if rule.kind != "object" and target.kind == PARTY_PLAYER else name
            spec = replace(spec, action_id=follow_id, title=title, targets=(name,), actor=actor)

            def settle(record):
                result = record.get("degree")
                if result is None:
                    return
                if rule.kind == "object":
                    caption = object_followup_caption(
                        spec,
                        int(result),
                        fields["hits"].value(),
                        no_hit=fields["no_hit"].isChecked(),
                    )
                else:
                    rung = answer_rung(
                        spec.ladder,
                        int(result),
                        target.character,
                        self._data,
                        self._host.minion_rules(),
                    )
                    caption = "Unaffected"
                    if rung is not None:
                        caption = rung_caption(rung, self._data)
                        changes = resolve_rung(
                            deepcopy(target.character), rung, self._data, source=act.source.name
                        )
                        delivered, _landed = self._host.land_rung(target, changes)
                        if not delivered:
                            caption += " (not delivered)"
                verdict = "Resisted" if int(result) > 0 else "Failed resistance"
                text = f"{name}: {total_of(record)} vs DC {spec.dc} — {verdict}; {caption}"
                card.set_done(text)
                self._host.action_result(title, text)

            if rule.kind != "object" and target.kind == PARTY_PLAYER:
                run = ActionRun(
                    self._data, self._bridge, self._host, follow_id, title=title, parent=self
                )
                self._followup_runs.append(run)
                if not run.ask(
                    target, spec, target, lambda _p, record: (settle(record), run.close())
                ):
                    card.set_status("Could not request the save. Try again.")
                    return
            else:
                self._followup_pending[follow_id] = settle
                if rule.kind != "object":
                    spec = replace(localize_spec(spec, target.character, self._data), trait_key="")
                if (
                    self._host.area_roll_attack(
                        spec, target.character if rule.kind != "object" else None
                    )
                    is False
                ):
                    self._followup_pending.pop(follow_id, None)
                    card.set_status("The die is busy. Try again after it settles.")
                    return
            card.button.setEnabled(False)
            body.hide()
            card.set_status("Waiting for the save — select its chip and click the die.")

        card.actionRequested.connect(load)
        self._host.action_add_card(card)

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
            text += f" — rolled {roll_text_html(act.roll)}"
        act.card.set_text(text)
        if act.accepted:
            return
        rules = action.targeted if action is not None else None
        if act.roll is None:
            act.card.set_status("Waiting for the roll…")
        elif rules is not None and rules.vs_defense:
            act.card.set_status(f"Accept to judge it against {decl.target_name}'s Defense.")
        elif rules is not None and rules.dc_from is not None:
            act.card.set_status(f"Accept to judge it against {decl.target_name}'s hold.")
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
        if act.done:
            return
        act.done = True
        act.resolving = False
        if notice:
            self._host.action_result(act.declaration.spec.title or act.source.name, text)
            self._host.impress_notice(
                f"{act.declaration.spec.title or act.source.name}: {text}",
                theme.color("accent"),
            )
        if act.card is not None:
            act.card.set_done(text)
        act.run.close()
