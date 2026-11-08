"""The GM's combat actions off the Rolls block's Actions popup.

The attack maneuvers, the self actions and the Attack tile are a player's, taken by an
NPC: a pick first asks **which NPC acts** (a plain menu of the cast, each with its
picture), and from there the shared :class:`~mm_companion.ui.roller.combat_runner.
CombatRunner` runs them exactly as it does on a sheet — with that NPC's attacks in the
picker, its numbers in the caps, its name on every roll, and what the action leaves
landing on it (and replayed onto its open sheet). Surprise Attack is the GM's alone
(decision 13 of the combat maneuvers plan): pick a creature on the Scene, and it is
Vulnerable — or Defenseless — until the start of its next turn.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon

from mm_companion.core import npc_groups
from mm_companion.core.data_loader import CombatAction, ConditionChange
from mm_companion.core.data_loader.game_data import COMBAT_KIND_MANEUVER
from mm_companion.core.rules import (
    RollSpec,
    action_reason,
    attack_choices,
    maneuver_action,
    self_action,
    self_action_changes,
    self_action_note,
)
from mm_companion.ui import theme
from mm_companion.ui.action_menu import SKIP_TARGET, choose_from_menu
from mm_companion.ui.attack_targets import KIND_ATTACK
from mm_companion.ui.gm.model import _Recovering
from mm_companion.ui.impress import PARTY_NPC, ImpressParty
from mm_companion.ui.roller.combat_runner import CombatActor, CombatRunner

#: Why the GM's maneuvers and self actions are greyed with no NPC in the session.
NO_NPC_TO_ACT = "Add an NPC to the session first: the GM takes these actions as one of them."
#: Why Surprise Attack is greyed with nobody on the Scene.
NO_ONE_ON_SCENE = "Put someone on the Scene first: a surprise attack is granted against them."
#: Beside an NPC in the chooser that has no attack to take an attack action with.
NO_ATTACKS_MARK = "no attacks"


class GMCombatMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    def _setup_combat(self) -> None:
        """Wire the shared combat runner onto the Rolls block's roller (no actor yet)."""
        self._combat = CombatRunner(
            self._data, self._roller, note=lambda text: self._post_note(text), parent=self
        )

    # -- the popup ------------------------------------------------------------

    def combat_offered(self) -> dict[str, str | None]:
        """What the GM's Actions popup runs of the combat block: every maneuver and self
        action (an NPC takes it, so they wait for one in the cast) and the actions the GM
        grants (Surprise Attack: someone must be on the Scene)."""
        acting = None if self._acting_parties() else NO_NPC_TO_ACT
        offered: dict[str, str | None] = {KIND_ATTACK: acting}
        for action in self._data.combat.actions:
            if action.kind == COMBAT_KIND_MANEUVER or self_action(self._data, action.id):
                offered[action.id] = acting
            elif action.gm_runs and action.options:
                offered[action.id] = None if self._scene else NO_ONE_ON_SCENE
        return offered

    def _gm_combat_action(self, kind: str) -> bool:
        """Run *kind* if it is one of the combat actions; whether it was."""
        if kind == KIND_ATTACK:
            self._gm_attack()
            return True
        action = self._data.combat.action(kind)
        if action is not None and action.gm_runs and action.options:
            self._grant(action)
            return True
        if maneuver_action(self._data, kind) is None and self_action(self._data, kind) is None:
            return False
        party = self._choose_actor(action)
        if party is not None:
            self._combat.act_as(self._actor_for(party))
            self._combat.start(kind)
        return True

    # -- who acts -------------------------------------------------------------

    def _acting_parties(self) -> list[ImpressParty]:
        """The NPCs that can act, in the board's order (group members one by one)."""
        sources, _targets = self.impress_parties()
        return [p for p in sources if p.kind == PARTY_NPC and p.character is not None]

    def _actor_reason(self, party: ImpressParty, action: CombatAction | None) -> str:
        """Why *party* cannot take *action* now (``""``: it can) — shown beside it."""
        if action is None or action.needs_attack or action.kind == COMBAT_KIND_MANEUVER:
            if not attack_choices(party.character, self._data):
                return NO_ATTACKS_MARK
        if action is not None and self_action(self._data, action.id) is not None:
            reason = action_reason(self._data, action, party.character, gm=True)
            if reason:
                return reason.rstrip(".")
        return ""

    def _choose_actor(self, action: CombatAction | None) -> ImpressParty | None:
        """Which NPC takes *action* (``None``: the Attack tile): picked off a plain menu
        at the Actions button — each with its picture, its group and whether it is a
        minion, greyed with the reason when it cannot — or the only one there is."""
        parties = self._acting_parties()
        if not parties:
            self._show_notice(NO_NPC_TO_ACT, theme.color("text.muted.rich"))
            return None
        reasons = [self._actor_reason(p, action) for p in parties]
        if len(parties) == 1:
            if reasons[0]:
                self._show_notice(
                    f"{parties[0].name}: {reasons[0]}.", theme.color("text.muted.rich")
                )
                return None
            return parties[0]
        captions = []
        for party, reason in zip(parties, reasons, strict=True):
            text = party.name
            group = npc_groups.group_of(self._npc_groups, party.source)
            if group is not None:
                text += f" · {group.name}"
            if party.minion:
                text += " (minion)"
            if reason:
                text += f" — {reason}"
            captions.append(text)
        index = choose_from_menu(
            self,
            captions,
            self._roller.menu_point(),
            disabled=[i for i, reason in enumerate(reasons) if reason],
            icons=[self._portrait_icon(p.ref) for p in parties],
        )
        return parties[index] if index is not None else None

    def _portrait_icon(self, ref: str) -> QIcon | None:
        portrait = self._scene_board.portrait(ref)
        return QIcon(portrait) if portrait is not None else None

    def _actor_for(self, party: ImpressParty) -> CombatActor:
        """*party* (an NPC of the cast) as the roller's actor: its live model, its name
        on the rolls, and what it does landing on it."""
        key = party.source
        return CombatActor(
            party.character,
            name=party.name,
            key=key,
            minion=self.rolls_as_minion(party.character),
            land=lambda requests: self._land_npc_requests(key, requests),
            throw=lambda spec, load: self._npc_throw(key, spec, load),
        )

    def _npc_throw(self, key: str, spec: RollSpec, load: bool) -> None:
        """Roll (or load) a check an NPC's action makes — Recover's recovery check,
        Stand's Acrobatics. One that sheds a condition when made is held, as the chip's
        🎲 holds it, so the result takes the condition off this NPC."""
        entry = self._npc_state.get(key)
        minion = self.rolls_as_minion(entry.character if entry is not None else None)
        if spec.recovers:
            # Set first, as _roll_npc_recovery does: a roll can come back before
            # roll_spec returns.
            self._recovering = _Recovering(key, spec)
        if load:
            self._roller.load_spec(spec, minion=minion, localize=False)
        elif not self._roller.roll_spec(spec, minion=minion) and spec.recovers:
            self._recovering = None

    def _follow_actor(self, key: str) -> None:
        """An NPC's own card or sheet rolled something: the lines on the lane were
        another NPC's, so they go (their limits read that one's numbers)."""
        actor = self._combat.actor
        if actor is not None and actor.key != key:
            self._combat.act_as(None)

    # -- the Attack tile --------------------------------------------------------

    def _gm_attack(self) -> None:
        """The Attack tile on the GM's roller: which NPC, which of its attacks, and —
        when the Scene has anyone else on it — optionally at whom ("Skip" first). Aimed,
        it goes the GM's own aimed-attack road; skipped, it is loaded onto the chip."""
        party = self._choose_actor(None)
        if party is None:
            return
        self._combat.act_as(self._actor_for(party))
        spec = self._combat.choose_attack()
        if spec is None:
            return
        targets = self._aim_targets_for(party.source)
        if targets:
            captions = [SKIP_TARGET] + [
                f"{t.name} (minion)" if t.minion else t.name for t in targets
            ]
            index = choose_from_menu(self, captions, self._roller.menu_point())
            if index is None:
                return
            if index > 0:
                target = targets[index - 1]
                self._attack.aim_own(party, spec, target.ref, target.name)
                return
        self._combat.load(spec)

    # -- what the GM grants -------------------------------------------------------

    def _grant(self, action: CombatAction) -> None:
        """An action the GM grants against a creature (Surprise Attack): who, which way
        (its options: vulnerable, or defenseless when completely surprised), and the
        conditions go on it — an NPC's model, or a player's sheet over the session."""
        _sources, targets = self.impress_parties()
        if not targets:
            self._show_notice(NO_ONE_ON_SCENE, theme.color("text.muted.rich"))
            return
        point = self._roller.menu_point()
        index = choose_from_menu(
            self,
            [f"{t.name} (minion)" if t.minion else t.name for t in targets],
            point,
            icons=[self._portrait_icon(t.ref) for t in targets],
        )
        if index is None:
            return
        target = targets[index]
        choice = choose_from_menu(self, [o.name for o in action.options], point)
        if choice is None:
            return
        changes = self_action_changes(action.effect_for(action.options[choice].id), self._data)
        if not changes or not self._grant_to(target, changes, action.name):
            return
        self._post_note(self_action_note(self._data, target.name, action.name, changes))

    def _grant_to(
        self, target: ImpressParty, changes: tuple[ConditionChange, ...], what: str
    ) -> bool:
        """Put *changes* on *target*; whether they went."""
        if target.kind == PARTY_NPC:
            return self._land_npc_requests(target.source, changes)
        catalog = self._data.condition_catalog()
        delivered = True
        for change in changes:
            if change.remove:
                sent = self._bridge.remove_condition(target.source, change.condition, None)
            else:
                record = catalog.get(change.condition)
                temporary = (
                    change.temporary
                    if change.temporary is not None
                    else bool(record is not None and record.temporary)
                )
                sent = self._bridge.apply_condition(
                    target.source,
                    change.condition,
                    None,
                    amount=change.amount,
                    temporary=temporary,
                )
            delivered = bool(sent) and delivered
        if not delivered:
            self._show_notice(
                f"Could not reach {target.name} — {what} was not sent.",
                theme.color("tint.worse"),
            )
        return delivered
