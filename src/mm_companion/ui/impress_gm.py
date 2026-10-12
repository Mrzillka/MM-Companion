"""The GM's half of the Impress action: open the window, ask, roll, apply.

:class:`~mm_companion.ui.impress.ImpressWindow` decides; this carries the decision
out, and it is the only part of the action that touches the session:

* a player's declared attempt arrives as an ``ActionRequest`` (kind ``"impress"``)
  and opens a window pre-filled with it; the GM can also start one for an NPC from
  the roller's Impress button;
* every player ticked as a target is sent an ``ActionPrompt`` (``"resist"``) asking
  how they resist, and their answer comes back as an ``ActionRequest``
  (``"resist"``) into the right window; when the window rolls or closes they are
  told it is settled (``"resist-closed"``);
* **resolve** rolls the check and every resistance as one server-side group roll,
  gathers the records again by their ``group``, and applies the rung each failure
  lands on — straight onto an NPC's model, or as a condition command to a player's
  app. **Request only** rolls just the check, whose card then offers every target a
  button to resist with. Off the air the same group is rolled here
  (:func:`~mm_companion.core.dice.resolve_group`) and shown as offline cards.

It reaches the GM window only through the small :class:`ImpressHost` surface, so the
window's own 3000 lines need know nothing about how an impression is resolved.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.dice import resolve_group
from mm_companion.core.rules import (
    MODE_REQUEST,
    ImpressAttempt,
    ResistAnswer,
    RollSpec,
    impress_label,
    impress_rung,
    impress_spec,
    impression_outcomes,
    resolve_rung,
    rung_caption,
)
from mm_companion.ui import theme
from mm_companion.ui.impress import (
    KIND_IMPRESS,
    KIND_RESIST,
    KIND_RESIST_CLOSED,
    PARTY_NPC,
    PARTY_PLAYER,
    ImpressParty,
    ImpressPlan,
    ImpressWindow,
    new_action_id,
)
from mm_companion.ui.session_bridge import SessionBridge


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
        self._windows: dict[str, ImpressWindow] = {}
        #: Group rolls in flight: group id -> (plan, how many records, those so far).
        self._pending: dict[str, tuple[ImpressPlan, int, list[dict]]] = {}
        bridge.actionRequested.connect(self._on_action_request)
        bridge.rollAdded.connect(self._on_roll)

    # -- opening ------------------------------------------------------------------

    @property
    def windows(self) -> dict[str, ImpressWindow]:
        return dict(self._windows)

    def open_new(self) -> ImpressWindow | None:
        """The GM pressed Impress: one of their creatures impresses somebody."""
        sources, targets = self._host.impress_parties()
        npcs = [party for party in sources if party.kind == PARTY_NPC]
        if not npcs:
            self._host.impress_notice(
                "Add an NPC to the cast first — an impression needs someone to make it.",
                theme.color("tint.warning"),
            )
            return None
        return self._open(new_action_id(), npcs, targets, None)

    def _on_action_request(
        self, kind: str, action_id: str, player_id: str, payload: object
    ) -> None:
        if kind == KIND_IMPRESS:
            self.open_for_attempt(player_id, payload)
        elif kind == KIND_RESIST:
            answer = ResistAnswer.from_dict(payload)
            window = self._windows.get(action_id)
            if answer is not None and window is not None:
                window.set_answer(player_id, answer)

    def open_for_attempt(self, player_id: str, payload: object) -> ImpressWindow | None:
        """A player declared an Impress; open it for the GM to set and roll."""
        attempt = ImpressAttempt.from_dict(payload)
        if attempt is None or self._data.impress.impression(attempt.impression) is None:
            return None
        existing = self._windows.get(attempt.action_id)
        if existing is not None:
            existing.raise_()
            return existing
        sources, targets = self._host.impress_parties()
        if self._bridge.is_gm and player_id == self._bridge.own_player_id():
            source = self._own_sheet_source(sources, attempt)
        else:
            source = next(
                (p for p in sources if p.kind == PARTY_PLAYER and p.source == player_id), None
            )
        if source is None:
            source = ImpressParty(
                ref=attempt.source_ref or f"player:{player_id}",
                name=attempt.source_name or "A player",
                kind=PARTY_PLAYER,
                source=player_id,
            )
        targets = [t for t in targets if t.ref != source.ref]
        return self._open(attempt.action_id, [source], targets, attempt)

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

    def _open(
        self,
        action_id: str,
        sources: list[ImpressParty],
        targets: list[ImpressParty],
        attempt: ImpressAttempt | None,
    ) -> ImpressWindow:
        window = ImpressWindow(
            self._data,
            action_id=action_id,
            sources=sources,
            targets=targets,
            attempt=attempt,
            parent=self._parent_window,
        )
        self._windows[action_id] = window
        window.promptNeeded.connect(lambda party, w=window: self._prompt(w, party))
        window.promptsChanged.connect(lambda w=window: self._reprompt(w))
        window.promptWithdrawn.connect(
            lambda party, w=window: self._bridge.prompt_action(
                party.source, KIND_RESIST_CLOSED, w.action_id, None
            )
        )
        window.rollRequested.connect(self._roll)
        window.cancelled.connect(lambda w=window: self._finish_window(w))
        window.destroyed.connect(lambda *_a, a=action_id: self._windows.pop(a, None))
        for party in window.players_to_prompt():
            self._prompt(window, party)
        window.show()
        return window

    # -- asking the targets -------------------------------------------------------

    def _prompt_payload(self, window: ImpressWindow) -> dict:
        impression, skill, source = window.impression(), window.skill(), window.source()
        return {
            "impression": impression.id if impression else "",
            "impression_name": impression.name if impression else "",
            "skill_row": skill.key if skill else "",
            "skill_label": skill.label if skill else "",
            "source_name": source.name if source else "",
        }

    def _prompt(self, window: ImpressWindow, party: ImpressParty) -> None:
        sent = self._bridge.prompt_action(
            party.source, KIND_RESIST, window.action_id, self._prompt_payload(window)
        )
        window.mark_prompted(party.ref, sent)

    def _reprompt(self, window: ImpressWindow) -> None:
        for party in window.prompted_parties():
            self._prompt(window, party)

    def _finish_window(self, window: ImpressWindow) -> None:
        """Tell every player still looking at a prompt that it is settled."""
        for party in window.prompted_parties():
            self._bridge.prompt_action(party.source, KIND_RESIST_CLOSED, window.action_id, None)
        self._windows.pop(window.action_id, None)

    # -- rolling --------------------------------------------------------------------

    def _lead_line(self, plan: ImpressPlan) -> dict:
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
        # The table card the group lands as is headed by this.
        spec = replace(spec, title=f"{plan.source.name}: {plan.impression.name}")
        return {
            "label": label,
            "bonus": max(0, plan.check_modifier),
            "penalty": max(0, -plan.check_modifier),
            "player_id": plan.source.source if plan.source.kind == PARTY_PLAYER else "",
            "spec": spec.to_dict(),
        }

    def _target_line(self, plan: ImpressPlan, index: int) -> dict:
        target = plan.targets[index]
        resist = target.resist
        modifier = resist.modifier if resist is not None else 0
        what = resist.label if resist is not None else "Resist"
        label = f"{what} — resisting {plan.impression.name}"
        if target.party.kind != PARTY_PLAYER:
            label = f"{target.party.name}: {label}"
        spec = RollSpec(
            label=label,
            modifier=modifier,
            kind=resist.kind if resist is not None else "",
            outcomes=impression_outcomes(self._data, plan.impression),
            # The row of the group's table card this line fills.
            targets=(target.party.name,),
            title=f"{plan.source.name}: {plan.impression.name}",
        )
        return {
            "label": label,
            "bonus": max(0, modifier),
            "penalty": max(0, -modifier),
            "dc_offset": target.dc_offset,
            "player_id": target.party.source if target.party.kind == PARTY_PLAYER else "",
            "spec": spec.to_dict(),
        }

    def _roll(self, plan: ImpressPlan) -> None:
        window = self._windows.get(plan.action_id)
        if window is not None:
            self._finish_window(window)
        lead = self._lead_line(plan)
        lines = (
            []
            if plan.mode == MODE_REQUEST
            else [self._target_line(plan, i) for i in range(len(plan.targets))]
        )
        group = f"{plan.action_id}-{uuid.uuid4().hex[:4]}"
        critical = self._data.system.critical_effect_bonus
        if self._bridge.in_session:
            # In session even through a blip: a roll the table never saw would be
            # one the players could not check, so it must fail visibly rather than
            # quietly roll here. Listen before asking: a hosting server raises each
            # record while it records it, inside the call below.
            self._pending[group] = (plan, 1 + len(lines), [])
            if not self._bridge.group_roll(group, lines, lead=lead, critical_bonus=critical):
                self._pending.pop(group, None)
                self._host.impress_notice(
                    f"The {plan.impression.name} roll did not reach the session.",
                    theme.color("tint.worse"),
                )
            return
        self._roll_offline(plan, group, lead, lines, critical)

    def _roll_offline(
        self, plan: ImpressPlan, group: str, lead: dict, lines: list[dict], critical: int
    ) -> None:
        """No table to roll at: resolve the same group here and show it as offline cards."""
        result = resolve_group(
            [(line["bonus"] - line["penalty"], line["dc_offset"]) for line in lines],
            lead_modifier=lead["bonus"] - lead["penalty"],
            critical_bonus=critical,
        )
        records = [
            {
                **lead,
                "die": result.lead_die,
                "dc": None,
                "degree": None,
                "critical": result.lead_die in (1, 20),
                "group": group,
            }
        ]
        for line, check in zip(lines, result.lines, strict=True):
            records.append(
                {
                    **line,
                    "die": check.die_roll,
                    "dc": check.dc,
                    "degree": check.degree,
                    "critical": check.critical,
                    "group": group,
                }
            )
        for record in records:
            self._host.impress_show_offline(record)
        self._apply(plan, records)

    def _on_roll(self, roll: object) -> None:
        if not isinstance(roll, dict):
            return
        group = str(roll.get("group", ""))
        pending = self._pending.get(group)
        if pending is None:
            return
        plan, expected, records = pending
        records.append(roll)
        if len(records) < expected:
            return
        del self._pending[group]
        records.sort(key=lambda r: int(r.get("seq", 0)))
        self._apply(plan, records)

    # -- applying -------------------------------------------------------------------

    def _apply(self, plan: ImpressPlan, records: list[dict]) -> None:
        """Put each failed target's rung on them, and say what happened."""
        if plan.mode == MODE_REQUEST:
            self._host.impress_notice(
                f"{plan.source.name}'s {plan.impression.name} is in the history — "
                "each target resists from its card.",
                theme.color("accent"),
            )
            return
        said: list[str] = []
        for target, record in zip(plan.targets, records[1:], strict=False):
            degree = record.get("degree")
            rung = impress_rung(plan.impression, None if degree is None else int(degree))
            name = target.party.name
            if rung is None:
                said.append(f"{name} resisted")
                continue
            # Escalation reads the target's current conditions; with no sheet to read
            # yet (a player whose snapshot has not arrived) it reads none.
            character = target.party.character or Character()
            changes = list(resolve_rung(character, rung, self._data, source=plan.source.name))
            caption = rung_caption(rung, self._data)
            if changes and not self._host.impress_apply(target.party, changes):
                said.append(f"{name}: {caption} (not delivered)")
            else:
                said.append(f"{name}: {caption}")
        if said:
            self._host.impress_notice(
                f"{plan.impression.name} — " + "; ".join(said), theme.color("accent")
            )
