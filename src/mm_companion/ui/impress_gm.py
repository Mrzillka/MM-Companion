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
  to resist with.

It reaches the GM window only through the small :class:`ImpressHost` surface, and the
rolling and the asking are :mod:`mm_companion.ui.action_run`'s.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    MODE_REQUEST,
    ImpressAttempt,
    RollSpec,
    impress_ask_resist_spec,
    impress_label,
    impress_rung,
    impress_spec,
    impression_outcomes,
    resolve_rung,
    rung_caption,
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
from mm_companion.ui.session_bridge import SessionBridge

#: What the GM's history button says.
RESOLVE_CAPTION = "Resolve Impress"


class ImpressHost(Protocol):
    """What the controller needs from the GM window."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]:
        """``(who may impress, who may be targeted)`` as the board stands now."""

    def impress_apply(self, party: ImpressParty, changes: list[tuple[str, str | None]]) -> bool:
        """Put conditions on one party; ``False`` if they could not be delivered."""

    def impress_show_offline(self, record: dict) -> None:
        """Show one roll made off the air in the GM's history."""

    def impress_notice(self, text: str, colour: str) -> None:
        """Say something on the GM window's notice card."""

    def action_add_card(self, card: ActionCard) -> None:
        """Put the GM's reminder card in their history."""


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
        """The GM pressed Impress: one of their creatures impresses somebody.

        *source_key* is the NPC's cast key when it was its card's button, which picks it.
        """
        sources, _targets = self._host.impress_parties()
        npcs = [party for party in sources if party.kind == PARTY_NPC]
        if not npcs:
            self._host.impress_notice(
                "Add an NPC to the cast first — an impression needs someone to make it.",
                theme.color("tint.warning"),
            )
            return None
        action = self._make(new_action_id(), npcs, None, from_card=False)
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
        lead = group_line(plan.source.name, spec, title=plan.title)
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
        critical = self._data.system.critical_effect_bonus if die == 20 else 0
        dc = total + critical
        if action.window is not None:
            natural = (
                f" (natural 20: +{critical})" if critical else (" (natural 1)" if die == 1 else "")
            )
            action.window.set_check(f"Check: <b>{total}</b>{natural} — DC <b>{dc}</b> to resist")
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
                    dc + target.dc_offset,
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
        for target in npcs:
            resist = target.resist
            spec = RollSpec(
                label=f"{resist.label if resist else 'Resist'} — resisting {plan.impression.name}",
                modifier=resist.modifier if resist is not None else 0,
                kind=resist.kind if resist is not None else "",
                outcomes=impression_outcomes(self._data, plan.impression),
            )
            lines.append(
                group_line(target.party.name, spec, title=plan.title, dc=dc + target.dc_offset)
            )
        action.run.roll_group(
            lines,
            list(npcs),
            lambda pairs, a=action: [self._land_roll(a, t, r) for t, r in pairs],
        )

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
        rung = impress_rung(plan.impression, None if degree is None else int(degree))
        if rung is None:
            self._land(action, target, "resisted", "tint.better")
            return
        # Escalation reads the target's current conditions; with no sheet to read
        # yet (a player whose snapshot has not arrived) it reads none.
        character = target.party.character or Character()
        changes = list(resolve_rung(character, rung, self._data, source=plan.source.name))
        caption = rung_caption(rung, self._data)
        if changes and not self._host.impress_apply(target.party, changes):
            caption = f"{caption} (not delivered)"
        elif target.party.kind == PARTY_PLAYER:
            action.run.tell(target.party, f"{plan.impression.name}: {caption}")
        self._land(action, target, caption, "tint.worse")

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
