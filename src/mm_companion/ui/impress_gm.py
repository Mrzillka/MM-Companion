"""The GM's half of the Impress action: note it, open it, roll it, apply it.

:class:`~mm_companion.ui.impress.ImpressWindow` decides; this carries the decision
out, and it is the only part of the action that touches the session:

* a player's declared attempt arrives as an ``ActionRequest`` (kind ``"impress"``) and
  puts an :class:`~mm_companion.ui.roll_history.ActionCard` in the GM's history with a
  **Resolve Impress** button, the way an area attack does — whole, when the player chose
  the impression, the skill and the targets, or bare, when the GM is to. The GM can also
  start one for an NPC from the roller's Impress button or the NPC's card, which opens
  the window straight away;
* **resolve** rolls in two stages. The impress check first: rolled by the server for an
  NPC, or **asked of the player** who is impressing — it is their roll. Its total (a
  natural 20 adds the critical bonus) is the DC every target resists: each NPC's
  resistance is a line of one server-side group, each targeted player is asked to roll
  theirs. What each failure lands on is applied as it comes back — straight onto an
  NPC's model, or as condition commands to a player's app — and the window's Result
  column and the history's table fill in as it goes;
* **request only** rolls just the check, whose card then offers every target a button
  to resist with;
* **end of turn** (a creature's card on the board, right-clicked): every impression on
  that creature is resisted again against its original DC, and every Affliction overcome
  against its own — the server rolls an NPC's, a player is asked — and one that is made
  comes off, with every condition it put on. What put a condition on is written on the
  condition itself (a :class:`~mm_companion.core.character.ShakeOff`, see
  :mod:`mm_companion.core.rules.shaking`), so it is read off the NPC's model or the
  player's snapshot and outlives this app. Each condition whose own recovery check is
  made at the end of a turn (a damage-caused Dazed or Stunned) is rolled with it: an
  NPC's in the same group, a player's as a request in the history.

It reaches the GM window only through the small :class:`ImpressHost` surface, and the
rolling and the asking are :mod:`mm_companion.ui.action_run`'s.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.character import SHAKE_IMPRESSION, AppliedCondition, Character, ShakeOff
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    BOOK_MINION_RULES,
    CADENCE_CLEARS,
    CADENCE_MINUTE,
    CADENCE_TURN,
    MODE_REQUEST,
    ImpressAttempt,
    MinionRules,
    RollSpec,
    end_of_turn_recoveries,
    held_shake_offs,
    impress_ask_resist_spec,
    impress_label,
    impress_rung,
    impress_spec,
    impression_outcomes,
    impression_shake_off,
    maintained_by,
    minion_save_spec,
    reapplied_landing,
    reapply_request,
    reapply_roll,
    recovery_request,
    recovery_roll,
    recovery_succeeded,
    resolve_rung,
    routine_impress_dc,
    rung_caption,
    shake_off_cadence,
    shake_off_choice,
    shake_off_failed,
    shake_off_maintained,
    shake_off_request,
    shake_off_roll,
    shake_off_stopped,
)
from mm_companion.ui import theme
from mm_companion.ui.action_run import ActionRun, group_line, total_of
from mm_companion.ui.action_ui import PHASE_DONE, PHASE_SETUP
from mm_companion.ui.area_gm import roll_cell
from mm_companion.ui.impress import (
    KIND_IMPRESS,
    PARTY_NPC,
    PARTY_PLAYER,
    ImpressParty,
    ImpressPlan,
    ImpressWindow,
    PlannedTarget,
    new_action_id,
)
from mm_companion.ui.roll_history import ActionCard
from mm_companion.ui.sections.conditions import condition_display_name
from mm_companion.ui.session_bridge import SessionBridge

#: What the GM's history button says.
RESOLVE_CAPTION = "Resolve Impress"


class ImpressHost(Protocol):
    """What the controller needs from the GM window."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]:
        """``(who may impress, who may be targeted)`` as the board stands now."""

    def impress_apply(
        self,
        party: ImpressParty,
        changes: list[tuple[str, str | None]],
        shake_off: ShakeOff | None = None,
    ) -> bool:
        """Put conditions on one party; ``False`` if they could not be delivered.

        *shake_off* is how they come off again (an impression's, an Affliction's)."""

    def impress_show_offline(self, record: dict) -> None:
        """Show one roll made off the air in the GM's history."""

    def impress_notice(self, text: str, colour: str) -> None:
        """Say something on the GM window's notice card."""

    def action_add_card(self, card: ActionCard) -> None:
        """Put the GM's reminder card in their history."""

    def action_remove(self, party: ImpressParty, changes: list[tuple[str, str | None]]) -> bool:
        """Take conditions off one party; ``False`` if that could not be delivered."""

    def minion_rules(self) -> MinionRules:
        """The minion rules the open session plays by."""

    def turn_recover(
        self, party: ImpressParty, condition_id: str, parameter: str | None
    ) -> list[tuple[str, str | None]] | None:
        """Shed what a made recovery check sheds on an NPC: what it turned into, or
        ``None`` if there was nothing to shed."""

    def request_recovery(
        self, party: ImpressParty, condition_id: str, parameter: str | None
    ) -> None:
        """Ask a player to roll their recovery check for one condition."""


@dataclass
class _Resolving:
    plan: ImpressPlan
    pending: set[str]
    said: list[str] = field(default_factory=list)
    cells: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _Action:
    """One Impress the GM has been told about (or started) and not settled yet."""

    action_id: str
    sources: list[ImpressParty]
    attempt: ImpressAttempt | None
    run: ActionRun
    card: ActionCard | None = None
    window: ImpressWindow | None = None
    resolving: _Resolving | None = None
    #: A player's declaration waiting in the history, rather than the GM's own.
    from_card: bool = False
    #: Which NPC impresses, when the GM started it from that NPC's card.
    preferred: str = ""


def land_shake_failure(host, data: GameData, party: ImpressParty, shake: ShakeOff, changes) -> str:
    """*party* failed a check to shake *shake* off: put what that does on it, and say it.

    Progressive's deeper degree replaces the record's conditions (*changes*), Fatal's
    Dying goes on beside them, and a count a modifier reads is written back onto the
    conditions — through *host*'s ``impress_apply`` / ``action_remove``, so an NPC's
    model or a player's sheet. Used by End turn and by an NPC's failed Recover.
    """
    failure = shake_off_failed(data, shake)
    catalog = data.condition_catalog()
    worse = ""
    if failure.worse:
        host.action_remove(party, list(changes))
        host.impress_apply(party, [(cid, None) for cid in failure.worse], failure.record)
        worse = ", ".join(
            condition_display_name(AppliedCondition(cid), catalog.get(cid)) for cid in failure.worse
        )
    elif failure.record != shake:
        # Same conditions, the record moved on (one more check failed).
        host.impress_apply(party, list(changes), failure.record)
    said = f"is still {shake.name}"
    if worse:
        said += f", now {worse}"
    if failure.also:
        host.impress_apply(party, [(cid, None) for cid in failure.also])
        said += ", and " + ", ".join(
            condition_display_name(AppliedCondition(cid), catalog.get(cid)) for cid in failure.also
        )
    return said


@dataclass
class _Held:
    """One shake-off record on a creature, and the conditions carrying it.

    Read off the creature as its turn ends (:func:`held_shake_offs`): an impression to
    resist again, or an Affliction to overcome, against the original DC. Made, it comes
    off with every condition in ``changes``.
    """

    shake: ShakeOff
    changes: list[tuple[str, str | None]]


@dataclass
class _Reapply:
    """A maintained Affliction (Concentration, Sustained) of the creature whose turn
    ended, on one creature on the board: resisted anew as its wielder keeps it up."""

    target: ImpressParty
    shake: ShakeOff
    changes: list[tuple[str, str | None]]


@dataclass
class _TurnRecovery:
    """A condition an NPC's end of turn rolls off with its own recovery check."""

    condition_id: str
    parameter: str | None
    name: str
    spec: RollSpec


class ImpressController(QObject):
    """Runs every Impress the GM window resolves (see the module docstring)."""

    def __init__(
        self,
        data: GameData,
        bridge: SessionBridge,
        host: ImpressHost,
        parent_window: QWidget,
    ) -> None:
        super().__init__(parent_window)
        self._data = data
        self._bridge = bridge
        self._host = host
        self._parent_window = parent_window
        self._actions: dict[str, _Action] = {}
        #: End-of-turn rolls still coming back.
        self._turn_runs: list[ActionRun] = []
        bridge.actionRequested.connect(self._on_action_request)
        bridge.rollAdded.connect(self.take_roll)

    # -- what is in hand ------------------------------------------------------------

    @property
    def windows(self) -> dict[str, ImpressWindow]:
        return {aid: a.window for aid, a in self._actions.items() if a.window is not None}

    @property
    def actions(self) -> dict[str, _Action]:
        return dict(self._actions)

    def _make(
        self,
        action_id: str,
        sources: list[ImpressParty],
        attempt: ImpressAttempt | None,
        *,
        from_card: bool,
    ) -> _Action:
        run = ActionRun(
            self._data,
            self._bridge,
            self._host,
            action_id,
            title="Impress",
            asker="The GM",
            parent=self,
        )
        action = _Action(action_id, sources, attempt, run, from_card=from_card)
        run.waitingChanged.connect(lambda a=action: self._on_waiting(a))
        self._actions[action_id] = action
        return action

    # -- being told --------------------------------------------------------------------

    def open_new(self, source_key: str = "") -> ImpressWindow | None:
        """The GM pressed Impress: somebody — an NPC of theirs, or a player — impresses.

        *source_key* picks the impresser: an NPC's cast key when it was its card's
        button, or a player's id when it was their card on the board. A player picked
        here is asked for their check like one who declared it themselves.
        """
        sources, _targets = self._host.impress_parties()
        if not sources:
            self._host.impress_notice(
                "Add an NPC to the cast or a player to the scene first — an impression "
                "needs someone to make it.",
                theme.color("tint.warning"),
            )
            return None
        action = self._make(new_action_id(), sources, None, from_card=False)
        action.preferred = source_key
        return self.open(action.action_id)

    def _on_action_request(
        self, kind: str, action_id: str, player_id: str, payload: object
    ) -> None:
        if kind == KIND_IMPRESS:
            self.declare(player_id, payload)

    def declare(self, player_id: str, payload: object) -> _Action | None:
        """An Impress was declared: a player's waits in the history; the GM's own opens."""
        attempt = ImpressAttempt.from_dict(payload)
        if attempt is None:
            return None
        if attempt.impression and self._data.impress.impression(attempt.impression) is None:
            return None
        existing = self._actions.get(attempt.action_id)
        if existing is not None:
            return existing
        sources, _targets = self._host.impress_parties()
        if self._bridge.is_gm and player_id == self._bridge.own_player_id():
            # The GM's own sheet: an NPC of theirs, set up here and now.
            source = self._own_sheet_source(sources, attempt)
            action = self._make(attempt.action_id, [source], attempt, from_card=False)
            self.open(attempt.action_id)
            return action
        source = next(
            (p for p in sources if p.kind == PARTY_PLAYER and p.source == player_id), None
        ) or ImpressParty(
            ref=attempt.source_ref or f"player:{player_id}",
            name=attempt.source_name or "A player",
            kind=PARTY_PLAYER,
            source=player_id,
        )
        action = self._make(attempt.action_id, [source], attempt, from_card=True)
        self._show_card(action)
        return action

    # Kept by its old name: a declaration used to open the window straight away.
    open_for_attempt = declare

    @staticmethod
    def _own_sheet_source(sources: list[ImpressParty], attempt: ImpressAttempt) -> ImpressParty:
        """Who impressed from a sheet the GM has open — one of their NPCs, by name.

        The sheet's Impress button reaches this window rather than the wire (see
        :meth:`~mm_companion.ui.session_bridge.SessionBridge.send_action_request`), and
        the creature is whichever cast NPC goes by the sheet's name. One that is not in
        the cast still impresses, as a creature nobody plays — the GM's own roll.
        """
        found = next(
            (p for p in sources if p.kind == PARTY_NPC and p.name == attempt.source_name), None
        )
        if found is not None:
            return found
        name = attempt.source_name or "An NPC"
        return ImpressParty(
            ref=attempt.source_ref or f"sheet:{name}", name=name, kind=PARTY_NPC, source=""
        )

    def _show_card(self, action: _Action) -> None:
        if action.card is not None:
            return
        card = ActionCard(action.action_id, self._card_text(action), RESOLVE_CAPTION)
        card.actionRequested.connect(self.open)
        action.card = card
        self._host.action_add_card(card)

    def _card_text(self, action: _Action) -> str:
        attempt = action.attempt
        source = action.sources[0].name if action.sources else "Someone"
        who = f"<b>{html.escape(source)}</b>"
        if attempt is None or attempt.bare:
            return f"{who} is impressing — the GM chooses how, and whom."
        impression = self._data.impress.impression(attempt.impression)
        name = impression.name if impression is not None else attempt.impression
        text = (
            f"{who} impresses: <b>{html.escape(name)}</b> with {html.escape(attempt.skill_label)}"
        )
        _sources, targets = self._host.impress_parties()
        names = {p.ref: p.name for p in targets}
        aimed = [names[ref] for ref in attempt.targets if ref in names]
        if aimed:
            text += "<br>At: " + html.escape(", ".join(aimed))
        return text

    @staticmethod
    def _plan_text(plan: ImpressPlan) -> str:
        text = (
            f"<b>{html.escape(plan.source.name)}</b> impresses: "
            f"<b>{html.escape(plan.impression.name)}</b> with {html.escape(plan.skill.label)}"
        )
        if plan.targets:
            text += "<br>At: " + html.escape(", ".join(t.party.name for t in plan.targets))
        return text

    # -- the window -------------------------------------------------------------------

    def open(self, action_id: str) -> ImpressWindow | None:
        """Open (or raise) the window for *action_id*."""
        action = self._actions.get(action_id)
        if action is None:
            return None
        if action.window is not None:
            action.window.show()
            action.window.raise_()
            action.window.activateWindow()
            return action.window
        _sources, targets = self._host.impress_parties()
        sources = action.sources
        if action.attempt is not None and sources:
            # The impresser's fresh numbers: a player's latest snapshot.
            source = sources[0]
            fresh = next((p for p in targets if p.ref == source.ref), None)
            if fresh is not None and fresh.character is not None:
                source.character = fresh.character
            targets = [t for t in targets if t.ref != source.ref]
        window = ImpressWindow(
            self._data,
            action_id=action_id,
            sources=sources,
            targets=targets,
            attempt=action.attempt,
            from_card=action.from_card,
            parent=self._parent_window,
        )
        if action.preferred:
            preferred = next((p for p in sources if p.source == action.preferred), None)
            if preferred is not None:
                window.set_source(preferred.ref)
        action.window = window
        window.rollRequested.connect(lambda plan, a=action: self._roll(a, plan))
        window.cancelled.connect(lambda a=action: self._on_cancelled(a))
        window.dismissed.connect(lambda a=action: self._on_dismissed(a))
        window.calledOff.connect(lambda a=action: self._call_off(a))
        window.rollWaitingRequested.connect(action.run.roll_waiting)
        # The board's own cards for the scene copy, where the host can draw them.
        scene = getattr(self._host, "action_scene", None)
        if scene is not None:
            window.set_scene_view(*scene())
        window.show()
        return window

    def _on_cancelled(self, action: _Action) -> None:
        window, action.window = action.window, None
        if window is not None:
            window.deleteLater()
        if not action.from_card:
            action.run.close()
            self._actions.pop(action.action_id, None)

    def _on_dismissed(self, action: _Action) -> None:
        action.run.close()
        if action.card is not None:
            action.card.set_done("Dismissed.")
        window, action.window = action.window, None
        if window is not None:
            window.deleteLater()
        self._actions.pop(action.action_id, None)

    def _on_waiting(self, action: _Action) -> None:
        names = action.run.waiting()
        if action.window is not None:
            action.window.set_waiting(names)
        if action.card is not None and action.resolving is not None:
            action.card.set_status(
                ("Waiting for " + ", ".join(names) + "…") if names else "Rolling…",
                button="Open",
            )

    def take_roll(self, roll: object) -> None:
        """One record from the history — a stage's line or an asked roll, if it is ours."""
        for action in list(self._actions.values()):
            if action.run.take_roll(roll):
                return
        for run in list(self._turn_runs):
            if run.closed:
                self._turn_runs.remove(run)
            elif run.take_roll(roll):
                return

    # -- rolling ---------------------------------------------------------------------

    def _check_spec(self, plan: ImpressPlan) -> RollSpec:
        """The impress check, as rolled for an NPC or asked of a player."""
        request = plan.mode == MODE_REQUEST
        spec = impress_spec(
            self._data,
            plan.impression,
            plan.skill,
            plan.check_modifier,
            source=plan.source.name,
            request=request,
            hint=plan.hint,
        )
        label = impress_label(plan.impression, plan.skill.label)
        if plan.source.kind != PARTY_PLAYER:
            label = f"{plan.source.name}: {label}"
        return replace(spec, label=label, targets=(plan.source.name,), title=plan.title)

    def _roll(self, action: _Action, plan: ImpressPlan) -> None:
        resolving = _Resolving(plan, pending={t.party.name for t in plan.targets})
        action.resolving = resolving
        run = action.run
        run.title = plan.title
        # On the card from here on: the way back to the window while rolls come back.
        self._show_card(action)
        if action.card is not None:
            action.card.set_text(self._plan_text(plan))
            action.card.set_status("Rolling…", button="Open")
        if (
            plan.mode != MODE_REQUEST
            and plan.targets
            and all(self._routine_against(plan, t) for t in plan.targets)
        ):
            # Every target is a minion a routine check is made against (PDF p201):
            # there is nothing to roll, so nobody is asked and nothing waits.
            self._resolve_targets(action, None, None)
            return
        spec = self._check_spec(plan)
        if plan.source.kind == PARTY_PLAYER:
            asked = run.ask(plan.source, spec, None, lambda _a, r, a=action: self._on_check(a, r))
            if not asked:
                self._host.impress_notice(
                    f"Could not ask {plan.source.name} for the check.", theme.color("tint.worse")
                )
            elif action.window is not None:
                action.window.set_check(f"Waiting for {html.escape(plan.source.name)}'s check…")
            return
        lead = group_line(
            plan.source.name, spec, title=plan.title, minion=self._minion_rolls(plan.source)
        )
        lead.pop("dc_offset", None)
        run.roll_group([], [], lambda pairs, a=action: self._on_check(a, pairs[0][1]), lead=lead)

    def _on_check(self, action: _Action, record: dict) -> None:
        """The impress check is in: its total is every target's DC."""
        resolving = action.resolving
        if resolving is None:
            return
        plan = resolving.plan
        die = int(record.get("die", 0))
        total = total_of(record)
        if plan.mode == MODE_REQUEST:
            self._host.impress_notice(
                f"{plan.source.name}'s {plan.impression.name} is in the history — "
                "each target resists from its card.",
                theme.color("accent"),
            )
            if action.window is not None:
                action.window.set_check(f"Check: <b>{total}</b>")
            self._done(action, "Rolled — each target resists from the card.")
            return
        self._resolve_targets(action, die, total)

    def _rules(self) -> MinionRules:
        """The session's minion rules — the book's for a host that cannot say."""
        reader = getattr(self._host, "minion_rules", None)
        return reader() if callable(reader) else BOOK_MINION_RULES

    def _minion_rolls(self, party: ImpressParty) -> bool:
        """Whether *party*'s rolls are a minion's: its natural 20 no added success."""
        return party.minion and self._rules().no_added_success

    def _routine_against(self, plan: ImpressPlan, target: PlannedTarget) -> bool:
        """Whether *target* resists a routine check rather than the rolled one.

        A non-minion impressing a minion makes the check as a routine one at +5 (PDF
        p201) — "routine opposition" is the non-minion's, so a minion impressing
        another minion rolls as anyone does.
        """
        return target.party.minion and not plan.source.minion and self._rules().routine_impress

    def _target_dc(self, plan: ImpressPlan, target: PlannedTarget, dc: int | None) -> int:
        """The DC *target* resists: the rolled one, or a minion's routine one.

        A routine check is the impresser's *choice* (PDF p17): one who has rolled
        anyway — a mixed group — keeps the better of the roll and the routine result,
        so a high roll never makes the minions easier to impress than the rest.
        """
        routine = routine_impress_dc(plan.check_modifier, self._data, target.dc_offset)
        if dc is None:
            return routine
        if self._routine_against(plan, target):
            return max(dc + target.dc_offset, routine)
        return dc + target.dc_offset

    def _resolve_targets(self, action: _Action, die: int | None, total: int | None) -> None:
        """Roll every target's resistance — against the check's total, or for a minion
        a routine check's (``die`` and ``total`` are ``None`` when nothing was rolled)."""
        resolving = action.resolving
        if resolving is None:
            return
        plan = resolving.plan
        dc: int | None = None
        if total is not None and die is not None:
            # A minion's natural 20 is no added success (PDF p25), so no critical bonus.
            counts = die == 20 and not self._minion_rolls(plan.source)
            critical = self._data.system.critical_effect_bonus if counts else 0
            dc = total + critical
            if action.window is not None:
                natural = (
                    f" (natural 20: +{critical})"
                    if critical
                    else (" (natural 1)" if die == 1 else "")
                )
                note = f"Check: <b>{total}</b>{natural} — DC <b>{dc}</b> to resist"
                if any(self._routine_against(plan, t) for t in plan.targets):
                    if dc >= routine_impress_dc(plan.check_modifier, self._data):
                        note += "; minions too (the roll beat a routine check)"
                    else:
                        note += f"; minions resist {self._routine_text(plan)}"
                action.window.set_check(note)
        elif action.window is not None:
            action.window.set_check(f"Routine against minions: {self._routine_text(plan)}")
        if not plan.targets:
            self._done(action, "Rolled.")
            return

        npcs: list[PlannedTarget] = []
        for target in plan.targets:
            if target.party.kind == PARTY_PLAYER:
                spec = impress_ask_resist_spec(
                    self._data,
                    plan.impression,
                    plan.skill.key,
                    self._target_dc(plan, target, dc),
                    target=target.party.name,
                    action_id=plan.action_id,
                    source=plan.source.name,
                    choice=None if target.highest else target.resist,
                    title=plan.title,
                )
                if not action.run.ask(
                    target.party, spec, target, lambda t, r, a=action: self._land_roll(a, t, r)
                ):
                    self._land(action, target, "not asked — no session", "tint.worse")
            else:
                npcs.append(target)
        if not npcs:
            return
        lines = []
        rules = self._rules()
        for target in npcs:
            resist = target.resist
            spec = RollSpec(
                label=f"{resist.label if resist else 'Resist'} — resisting {plan.impression.name}",
                modifier=resist.modifier if resist is not None else 0,
                kind=resist.kind if resist is not None else "",
                outcomes=impression_outcomes(self._data, plan.impression),
            )
            if target.party.minion:
                spec = minion_save_spec(spec, rules)
            lines.append(
                group_line(
                    target.party.name,
                    spec,
                    title=plan.title,
                    dc=self._target_dc(plan, target, dc),
                    minion=self._minion_rolls(target.party),
                )
            )
        action.run.roll_group(
            lines,
            list(npcs),
            lambda pairs, a=action: [self._land_roll(a, t, r) for t, r in pairs],
        )

    def _routine_text(self, plan: ImpressPlan) -> str:
        """How a minion's routine DC is made: "10 + 8 + 5 = DC 23"."""
        bonus = self._data.system.minions.impress_bonus
        dc = routine_impress_dc(plan.check_modifier, self._data)
        return f"10 {plan.check_modifier:+d} +{bonus} = DC <b>{dc}</b> (routine)"

    # -- landing ---------------------------------------------------------------------

    def _land_roll(self, action: _Action, target: PlannedTarget, record: dict) -> None:
        """A target's resistance is in: put its rung on them."""
        resolving = action.resolving
        if resolving is None:
            return
        plan = resolving.plan
        spec = RollSpec.from_dict(record.get("spec"))
        label = spec.label if spec is not None else "Resist"
        label = label.partition(" — ")[0]
        cells = resolving.cells.setdefault(target.party.name, [])
        cells.append(roll_cell(label, record))
        degree = record.get("degree")
        rung = impress_rung(
            plan.impression,
            None if degree is None else int(degree),
            self._data,
            minion=target.party.minion,
            rules=self._rules(),
        )
        if rung is None:
            self._land(action, target, "resisted", "tint.better")
            return
        # Escalation reads the target's current conditions; with no sheet to read
        # yet (a player whose snapshot has not arrived) it reads none.
        character = target.party.character or Character()
        changes = list(resolve_rung(character, rung, self._data, source=plan.source.name))
        caption = rung_caption(rung, self._data)
        dc = record.get("dc")
        shake = None
        if isinstance(dc, int) and not isinstance(dc, bool):
            # Written on the conditions, so the creature's end of turn resists again.
            shake = impression_shake_off(
                plan.impression,
                plan.skill.key,
                dc,
                resist=None if target.highest else target.resist,
                source=plan.source.name,
                degree=None if degree is None else int(degree),
            )
        if changes and not self._host.impress_apply(target.party, changes, shake):
            caption = f"{caption} (not delivered)"
        else:
            if target.party.kind == PARTY_PLAYER:
                action.run.tell(target.party, f"{plan.impression.name}: {caption}")
        self._land(action, target, caption, "tint.worse")

    def held(self, party: ImpressParty) -> list[_Held]:
        """What *party*'s turn may shake off: each record on its conditions, read off the
        NPC's model or the player's latest snapshot (none before one has arrived)."""
        character = party.character
        if character is None:
            return []
        return [_Held(shake, list(changes)) for shake, changes in held_shake_offs(character)]

    # -- the end of a turn -------------------------------------------------------------

    def end_turn(self, party: ImpressParty) -> ActionRun | None:
        """*party*'s turn is over: shake off what a turn's end shakes off.

        Each impression on it is resisted again at its original DC, and each Affliction
        overcome against its own: one the creature makes comes off with everything it put
        on, one it fails stays another turn — or does what the Affliction's modifiers say
        (:func:`shake_off_failed`: a degree deeper, Dying, a harder next check). An
        Affliction at its third degree is checked once a minute instead (PDF p109), and a
        Prolonged one at the end of the scene, so those are only named; an Instant
        Recovery one clears with no roll. Each condition whose own recovery check is made
        as a turn ends (a damage-caused Dazed, Stunned) is rolled too; one an impression
        or an Affliction put on is not, since it comes off with what put it there.

        An NPC's rolls are one server-rolled group. A player is asked for each shake-off
        in turn, and gets a request in the history for each recovery check, which their
        own app settles.

        First, each Affliction *party* maintains (Concentration, Sustained) is resisted
        anew by everyone it is on, as a new use with no attack (PDF p110) — in a table of
        its own (:meth:`keep_up`).
        """
        maintained = self.maintained(party)
        self.keep_up(party, maintained)
        held = self.held(party)
        cadence = {id(h): shake_off_cadence(self._data, h.shake) for h in held}
        due = [h for h in held if cadence[id(h)] == CADENCE_TURN]
        waiting = [h for h in held if h not in due and cadence[id(h)] != CADENCE_CLEARS]
        cleared = [h for h in held if cadence[id(h)] == CADENCE_CLEARS]
        recoveries = self._turn_recoveries(party)
        for item in waiting:
            when = "a minute" if cadence[id(item)] == CADENCE_MINUTE else "a scene"
            self._host.impress_notice(
                f"{party.name}: {item.shake.name} — overcome once {when}, not as a turn ends.",
                theme.color("text.muted.rich"),
            )
        for item in cleared:
            # Instant Recovery: gone where the first check would come, with no roll.
            delivered = self._host.action_remove(party, item.changes)
            self._host.impress_notice(
                f"{party.name}: {item.shake.name} clears by itself"
                + ("." if delivered else " (not delivered)."),
                theme.color("tint.better"),
            )
        if not due and not recoveries:
            if not waiting and not cleared and not maintained:
                self._host.impress_notice(
                    f"{party.name}'s turn ends — nothing to shake off.", theme.color("accent")
                )
            return None
        run = ActionRun(
            self._data,
            self._bridge,
            self._host,
            new_action_id(),
            title=f"{party.name}: end of turn",
            asker="The GM",
            parent=self,
        )
        self._turn_runs = [r for r in self._turn_runs if not r.closed]
        self._turn_runs.append(run)
        if party.kind == PARTY_PLAYER:
            for recovery in recoveries:
                self._host.request_recovery(party, recovery.condition_id, recovery.parameter)
            self._ask_shake_off(run, party, list(due))
            return run
        character = party.character or Character()
        lines = []
        about: list[object] = []
        unrolled: list[str] = []
        for item in due:
            spec = shake_off_roll(character, self._data, item.shake)
            if spec is None:
                unrolled.append(item.shake.name)
                continue
            lines.append(group_line(party.name, spec, title=run.title, dc=spec.dc))
            about.append(item)
        for recovery in recoveries:
            lines.append(group_line(party.name, recovery.spec, title=run.title))
            about.append(recovery)
        if unrolled:
            self._host.impress_notice(
                f"{party.name}: nothing on the sheet to resist {', '.join(unrolled)} with"
                " — roll it by hand.",
                theme.color("tint.worse"),
            )
        if not lines:
            run.close()
            return run
        run.roll_group(lines, about, lambda pairs, r=run: self._shaken(r, party, pairs))
        return run

    # -- maintained Afflictions ---------------------------------------------------------

    def maintained(self, party: ImpressParty) -> list[_Reapply]:
        """Every maintained Affliction of *party*'s on a creature on the board — by the
        record each one's conditions carry, which names who put it on."""
        _sources, targets = self._host.impress_parties()
        out: list[_Reapply] = []
        for target in targets:
            if target.character is None:
                continue
            for shake, changes in held_shake_offs(target.character):
                if shake.source == party.name and shake_off_maintained(self._data, shake):
                    out.append(_Reapply(target, shake, list(changes)))
        return out

    def keep_up(self, party: ImpressParty, items: list[_Reapply] | None = None) -> ActionRun | None:
        """*party*'s turn ended: everyone its maintained Afflictions are on resists anew.

        Kept up while the power of that name on *party*'s sheet is switched on
        (:func:`maintained_by`); switched off, it stops — a maintained Instant Recovery
        one clears, any other is overcome as an unmaintained one from then on. An NPC's
        new checks are one server-rolled group, a player is asked for each. A failure
        lands as a new use (:func:`reapplied_landing`: the highest degree, or added up
        for a Cumulative one). ``None`` when nothing was rolled. *items* are
        :meth:`maintained`'s, when the caller has them already.
        """
        rolled: list[_Reapply] = []
        asked: list[_Reapply] = []
        for item in self.maintained(party) if items is None else items:
            name, target = item.shake.name, item.target
            state = maintained_by(party.character, item.shake)
            if state is None:
                self._host.impress_notice(
                    f"{party.name}: no power called {name} on the sheet — whether it is"
                    f" still kept up on {target.name} is yours to roll by hand.",
                    theme.color("tint.worse"),
                )
            elif not state:
                clears, record = shake_off_stopped(self._data, item.shake)
                if clears:
                    self._host.action_remove(target, item.changes)
                    said = f"{target.name} is free of it"
                else:
                    self._host.impress_apply(target, item.changes, record)
                    said = f"{target.name} overcomes it as usual from now on"
                self._host.impress_notice(
                    f"{party.name} stops keeping {name} up: {said}.",
                    theme.color("tint.better"),
                )
            elif target.kind == PARTY_PLAYER:
                asked.append(item)
            else:
                rolled.append(item)
        if not rolled and not asked:
            return None
        run = ActionRun(
            self._data,
            self._bridge,
            self._host,
            new_action_id(),
            title=f"{party.name}: keeps powers up",
            asker="The GM",
            parent=self,
        )
        self._turn_runs = [r for r in self._turn_runs if not r.closed]
        self._turn_runs.append(run)
        lines = []
        about: list[object] = []
        for item in rolled:
            spec = reapply_roll(item.target.character or Character(), self._data, item.shake)
            if spec is None:
                self._host.impress_notice(
                    f"{item.target.name}: nothing on the sheet to resist {item.shake.name}"
                    " with — roll it by hand.",
                    theme.color("tint.worse"),
                )
                continue
            lines.append(group_line(item.target.name, spec, title=run.title, dc=spec.dc))
            about.append(item)
        parts = [bool(lines), bool(asked)]

        def part_done() -> None:
            parts.pop()
            if not parts:
                run.close()

        if lines:
            run.roll_group(
                lines, about, lambda pairs: (self._reapplied(run, party, pairs), part_done())
            )
        else:
            parts.pop()
        if asked:
            self._ask_reapply(run, party, asked, part_done)
        elif not parts:
            run.close()
        return run

    def _ask_reapply(self, run: ActionRun, party: ImpressParty, queue, done) -> None:
        """Ask the next player under one of *party*'s maintained Afflictions to resist it
        anew; the rest wait their turn (one ask per player at a time)."""
        if not queue:
            done()
            return
        item = queue.pop(0)
        spec = reapply_request(item.shake)
        if spec is None:
            self._host.impress_notice(
                f"{item.target.name}: nothing to resist {item.shake.name} with — roll it by hand.",
                theme.color("tint.worse"),
            )
            self._ask_reapply(run, party, queue, done)
            return
        spec = replace(spec, targets=(item.target.name,), action_id=run.action_id, title=run.title)

        def finish(about, record) -> None:
            self._reapplied(run, party, [(about, record)])
            self._ask_reapply(run, party, queue, done)

        if not run.ask(item.target, spec, item, finish):
            self._host.impress_notice(
                f"Could not ask {item.target.name} to resist {item.shake.name}.",
                theme.color("tint.worse"),
            )
            done()

    def _reapplied(self, run: ActionRun, party: ImpressParty, pairs) -> None:
        """New checks against *party*'s maintained Afflictions are in: land the failures."""
        catalog = self._data.condition_catalog()
        said: list[str] = []
        for item, record in pairs:
            if not isinstance(item, _Reapply):
                continue
            name, target = item.shake.name, item.target
            degree = record.get("degree")
            degree = int(degree) if isinstance(degree, int) else None
            landing = reapplied_landing(target.character, self._data, item.shake, degree)
            if degree is not None and degree > 0:
                said.append(f"{target.name} resists it")
                continue
            if not landing.apply:
                said.append(f"{target.name} fails, no worse than before")
                continue
            if landing.remove:
                self._host.action_remove(target, list(landing.remove))
            delivered = self._host.impress_apply(target, list(landing.apply), landing.shake)
            names = ", ".join(
                condition_display_name(AppliedCondition(cid), catalog.get(cid))
                for cid, _parameter in landing.apply
            )
            said.append(
                f"{target.name} fails — {names}" + ("" if delivered else " (not delivered)")
            )
            if target.kind == PARTY_PLAYER and delivered:
                run.tell(target, f"{party.name}'s {name} lands again: {names}.")
        if said:
            name = pairs[0][0].shake.name if isinstance(pairs[0][0], _Reapply) else ""
            self._host.impress_notice(
                f"{party.name} keeps {name} up: " + "; ".join(said) + ".",
                theme.color("accent"),
            )

    def _turn_recoveries(self, party: ImpressParty) -> list[_TurnRecovery]:
        """The recovery checks *party*'s turn's end rolls.

        A condition an impression or an Affliction put on offers none
        (:func:`~mm_companion.core.rules.can_recover`): it comes off with what put it on.
        """
        character = party.character
        if character is None:
            return []
        catalog = self._data.condition_catalog()
        out: list[_TurnRecovery] = []
        for applied in end_of_turn_recoveries(character, self._data):
            if party.kind == PARTY_PLAYER:
                spec = recovery_request(self._data, applied)
            else:
                spec = recovery_roll(character, self._data, applied)
            if spec is None:
                continue
            name = condition_display_name(applied, catalog.get(applied.condition_id))
            out.append(_TurnRecovery(applied.condition_id, applied.parameter, name, spec))
        return out

    def _ask_spec(self, run: ActionRun, party: ImpressParty, held: _Held) -> RollSpec | None:
        """What a player is asked to roll to shake *held* off, or ``None`` if nothing."""
        shake = held.shake
        impression = self._data.impress.impression(shake.ref) if shake.ref else None
        if shake.kind == SHAKE_IMPRESSION and impression is not None:
            choice = None
            if shake.trait and party.character is not None:
                choice = shake_off_choice(party.character, self._data, shake)
            spec = impress_ask_resist_spec(
                self._data,
                impression,
                shake.skill,
                shake.dc,
                target=party.name,
                action_id=run.action_id,
                source=shake.source,
                choice=choice,
                title=run.title,
            )
            return replace(
                spec,
                label=f"Shake off {shake.name}",
                outcomes=(),
                hint="Make it and the impression comes off; fail it and it lasts another turn.",
            )
        spec = shake_off_request(shake, self._data)
        if spec is None:
            return None
        return replace(spec, targets=(party.name,), action_id=run.action_id, title=run.title)

    def _ask_shake_off(self, run: ActionRun, party: ImpressParty, queue: list[_Held]) -> None:
        """Ask a player to shake off the next thing on them; the rest wait their turn."""
        if not queue:
            run.close()
            return
        held = queue.pop(0)
        spec = self._ask_spec(run, party, held)
        if spec is None:
            self._host.impress_notice(
                f"{party.name}: nothing to resist {held.shake.name} with — roll it by hand.",
                theme.color("tint.worse"),
            )
            self._ask_shake_off(run, party, queue)
            return

        def finish(about, record) -> None:
            self._shaken(run, party, [(about, record)], close=False)
            self._ask_shake_off(run, party, queue)

        if not run.ask(party, spec, held, finish):
            self._host.impress_notice(
                f"Could not ask {party.name} to shake off {held.shake.name}.",
                theme.color("tint.worse"),
            )
            run.close()

    def _shaken(
        self,
        run: ActionRun,
        party: ImpressParty,
        pairs: list[tuple[object, dict]],
        *,
        close: bool = True,
    ) -> None:
        """Each end-of-turn check is in: a made one takes what it shakes off away."""
        said: list[str] = []
        for about, record in pairs:
            if isinstance(about, _TurnRecovery):
                said.append(self._recovered(party, about, record))
                continue
            if not isinstance(about, _Held):
                continue
            name = about.shake.name
            degree = record.get("degree")
            if degree is not None and int(degree) > 0:
                if not self._host.action_remove(party, about.changes):
                    said.append(f"shakes off {name} (not delivered)")
                    continue
                said.append(f"shakes off {name}")
                if party.kind == PARTY_PLAYER:
                    run.tell(party, f"You shake off {name}.")
            else:
                said.append(self._still(party, about))
        if said:
            self._host.impress_notice(
                f"{party.name} " + "; ".join(said) + ".", theme.color("accent")
            )
        if close:
            run.close()

    def _still(self, party: ImpressParty, held: _Held) -> str:
        """*party* failed to shake *held* off: what its modifiers make of that, said."""
        return land_shake_failure(self._host, self._data, party, held.shake, held.changes)

    def _recovered(self, party: ImpressParty, about: _TurnRecovery, record: dict) -> str:
        """One end-of-turn recovery check is back: shed the condition if it was made."""
        if not recovery_succeeded(self._data, about.spec, record.get("degree")):
            return f"is still {about.name}"
        added = self._host.turn_recover(party, about.condition_id, about.parameter)
        if added is None:
            return f"is no longer {about.name}"
        text = f"recovers from {about.name}"
        if added:
            catalog = self._data.condition_catalog()
            now = ", ".join(
                condition_display_name(AppliedCondition(cid, p), catalog.get(cid))
                for cid, p in added
            )
            text += f" — now {now}"
        return text

    def _land(self, action: _Action, target: PlannedTarget, text: str, token: str) -> None:
        resolving = action.resolving
        name = target.party.name
        if resolving is None or name not in resolving.pending:
            return
        resolving.pending.discard(name)
        resolving.said.append(f"{name}: {text}")
        cells = resolving.cells.get(name, [])
        shown = " · ".join([*cells, f"<b>{html.escape(text)}</b>"]) if cells else html.escape(text)
        if action.window is not None:
            action.window.set_result(target.party.ref, shown, token)
        if not resolving.pending:
            plan = resolving.plan
            self._host.impress_notice(
                f"{plan.impression.name} — " + "; ".join(resolving.said), theme.color("accent")
            )
            self._done(action, "All in — each row shows what it did.")

    def _done(self, action: _Action, text: str) -> None:
        if action.card is not None:
            action.card.set_done("Resolved — the table is in the history.")
        if action.window is not None:
            action.window.finish(text)
        action.run.close()
        self._retire(action)

    def _call_off(self, action: _Action) -> None:
        action.run.close()
        resolving = action.resolving
        left = sorted(resolving.pending) if resolving is not None else []
        if action.card is not None:
            action.card.set_done("Called off.")
        if action.window is not None:
            action.window.finish(
                "Called off" + (f" — never landed: {', '.join(left)}." if left else ".")
            )
        self._retire(action)

    def _retire(self, action: _Action) -> None:
        """Forget a settled action once its window is gone (or now, with no window)."""
        window = action.window
        if window is None or window.phase == PHASE_SETUP:
            self._actions.pop(action.action_id, None)
            return
        if window.phase == PHASE_DONE and not window.isVisible():
            self._actions.pop(action.action_id, None)
            window.deleteLater()
            action.window = None
            return
        window.finished.connect(lambda _r, a=action: self._on_done_window_closed(a))

    def _on_done_window_closed(self, action: _Action) -> None:
        if action.window is not None and action.window.phase == PHASE_DONE:
            self._actions.pop(action.action_id, None)
            action.window.deleteLater()
            action.window = None
