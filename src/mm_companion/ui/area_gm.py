"""The GM's half of an area attack: note it, open it, roll it, apply it.

:class:`~mm_companion.ui.area.AreaWindow` decides; this carries the decision out, and
it is the only part of the action that touches the session:

* a player's declared area arrives as an ``ActionRequest`` (kind ``"area"``) and
  puts an :class:`~mm_companion.ui.roll_history.ActionCard` in the GM's history with
  a **Resolve Area Attack** button. The attack check against the primary target lands
  as an ordinary roll carrying the attempt's id, and is paired up with it here;
* **resolving** rolls the creatures nobody plays in two stages, each a server-side
  group roll: every NPC's Dodge check (``"<id>.1"``), then — once those are in — each
  one's save at the rank its Dodge left (``"<id>.2"``). The history draws both stages
  as one table (:func:`~mm_companion.ui.roll_history.table_key`), and the rung each
  failed save lands on goes straight onto the NPC's model;
* a **player** in the area is not rolled for: the history is left a request to roll —
  the Dodge check chained to the save — which their own sheet answers, and whose
  outcome they apply themselves.

Off the air the same groups are rolled here (:func:`~mm_companion.core.dice.resolve_group`)
and shown as offline cards. It reaches the GM window only through
:class:`AreaHost`, the Impress host surface plus two methods.
"""

from __future__ import annotations

import html
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.dice import resolve_group
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    AreaAttempt,
    AreaEffect,
    RollSpec,
    area_effect_rung,
    area_npc_dodge_spec,
    area_npc_save_specs,
    area_request_specs,
    power_area_use,
    resolve_rung,
    rung_caption,
)
from mm_companion.core.session.protocol import MAX_GROUP_LINES
from mm_companion.ui import theme
from mm_companion.ui.area import KIND_AREA_ATTACK, AreaPlan, AreaTarget, AreaWindow
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty, new_action_id
from mm_companion.ui.roll_history import ActionCard
from mm_companion.ui.session_bridge import SessionBridge

#: What the GM's history button says.
RESOLVE_CAPTION = "Resolve Area Attack"


class AreaHost(Protocol):
    """What the controller needs from the GM window."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]:
        """``(who may act, who may be targeted)`` as the board stands now."""

    def impress_apply(self, party: ImpressParty, changes: list[tuple[str, str | None]]) -> bool:
        """Put conditions on one party; ``False`` if they could not be delivered."""

    def impress_show_offline(self, record: dict) -> None:
        """Show one roll made off the air in the GM's history."""

    def impress_notice(self, text: str, colour: str) -> None:
        """Say something on the GM window's notice card."""

    def area_add_card(self, card: ActionCard) -> None:
        """Put the GM's reminder card in their history."""

    def area_roll_attack(self, spec: RollSpec) -> None:
        """Roll an NPC's attack against the primary on the GM's roller."""


@dataclass
class _Declared:
    """One area attack the GM has been told about and not resolved yet."""

    attempt: AreaAttempt
    source: ImpressParty
    card: ActionCard
    attack: tuple[int, int] | None = None
    window: AreaWindow | None = None


@dataclass
class _Stage:
    """One stage of a resolve in flight: its lines, and the groups still to come back."""

    plan: AreaPlan
    lines: list[dict]
    #: What each line is about, in line order — handed to *finish* with its record.
    about: list[object]
    finish: Callable[[AreaPlan, list[tuple[object, dict]]], None]
    waiting: dict[str, int] = field(default_factory=dict)
    records: dict[str, list[dict]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


class AreaController(QObject):
    """Runs every area attack the GM window resolves (see the module docstring)."""

    def __init__(
        self,
        data: GameData,
        bridge: SessionBridge,
        host: AreaHost,
        parent_window: QWidget,
    ) -> None:
        super().__init__(parent_window)
        self._data = data
        self._bridge = bridge
        self._host = host
        self._parent_window = parent_window
        self._declared: dict[str, _Declared] = {}
        #: An attack roll that arrived before its declaration did.
        self._early: dict[str, tuple[int, int]] = {}
        #: Group id -> the stage it belongs to.
        self._stages: dict[str, _Stage] = {}
        #: Action id -> its table's id, and how many groups that table has had.
        self._tables: dict[str, str] = {}
        self._parts: dict[str, int] = {}
        bridge.actionRequested.connect(self._on_action_request)
        bridge.rollAdded.connect(self.take_roll)

    # -- being told -----------------------------------------------------------

    @property
    def declared(self) -> dict[str, _Declared]:
        return dict(self._declared)

    @property
    def windows(self) -> dict[str, AreaWindow]:
        """The windows open now, by action id — for a player's fresher snapshot."""
        return {aid: d.window for aid, d in self._declared.items() if d.window is not None}

    def _on_action_request(
        self, kind: str, action_id: str, player_id: str, payload: object
    ) -> None:
        if kind == KIND_AREA_ATTACK:
            self.declare(player_id, payload)

    def declare(self, player_id: str, payload: object) -> _Declared | None:
        """A player declared an area attack: note it in the GM's history."""
        attempt = AreaAttempt.from_dict(payload)
        if attempt is None:
            return None
        existing = self._declared.get(attempt.action_id)
        if existing is not None:
            return existing
        sources, _targets = self._host.impress_parties()
        if self._bridge.is_gm and player_id == self._bridge.own_player_id():
            source = self._own_sheet_source(sources, attempt)
        else:
            source = next(
                (p for p in sources if p.kind == PARTY_PLAYER and p.source == player_id), None
            ) or ImpressParty(
                ref=attempt.source_ref or f"player:{player_id}",
                name=attempt.source_name or "A player",
                kind=PARTY_PLAYER,
                source=player_id,
            )
        return self._note(attempt, source)

    @staticmethod
    def _own_sheet_source(sources: list[ImpressParty], attempt: AreaAttempt) -> ImpressParty:
        """Who attacked from a sheet the GM has open — one of their NPCs, by name."""
        found = next(
            (p for p in sources if p.kind == PARTY_NPC and p.name == attempt.source_name), None
        )
        if found is not None:
            return found
        name = attempt.source_name or "An NPC"
        return ImpressParty(
            ref=attempt.source_ref or f"sheet:{name}", name=name, kind=PARTY_NPC, source=""
        )

    def _note(self, attempt: AreaAttempt, source: ImpressParty) -> _Declared:
        card = ActionCard(attempt.action_id, "", RESOLVE_CAPTION)
        card.actionRequested.connect(self.open)
        declared = _Declared(attempt, source, card)
        self._declared[attempt.action_id] = declared
        early = self._early.pop(attempt.action_id, None)
        if early is not None:
            declared.attack = early
        self._describe(declared)
        self._host.area_add_card(card)
        return declared

    def _describe(self, declared: _Declared) -> None:
        attempt, use = declared.attempt, declared.attempt.use
        _sources, targets = self._host.impress_parties()
        names = {p.ref: p.name for p in targets}
        text = (
            f"<b>{html.escape(declared.source.name)}</b> uses "
            f"<b>{html.escape(use.power_name)}</b> on an area — "
            f"{html.escape(use.size_text(self._data))}"
        )
        if attempt.primary:
            text += f"<br>Aimed at {html.escape(names.get(attempt.primary, 'someone'))}"
            if declared.attack is not None:
                text += f" — attack {declared.attack[0]}"
        caught = [names[ref] for ref in attempt.targets if ref in names]
        if caught:
            text += "<br>Caught: " + html.escape(", ".join(caught))
        declared.card.set_text(text)

    def start_for_npc(
        self, character: Character, power: Power, key: str, *, name: str = ""
    ) -> AreaWindow | None:
        """The GM used an NPC's power on an area, from its sheet: open it straight away.

        *key* is the NPC's cast key (its file), which is how it is found among the
        parties — never by *name*, which two identical minions share. *name* is what to
        call it when it is not in the cast at all.
        """
        use = power_area_use(power, character, self._data, npc=True)
        if use is None:
            return None
        sources, _targets = self._host.impress_parties()
        name = name or key or "An NPC"
        source = next(
            (p for p in sources if p.kind == PARTY_NPC and key and p.source == key), None
        ) or ImpressParty(ref=f"sheet:{key or name}", name=name, kind=PARTY_NPC, source=key)
        attempt = AreaAttempt(new_action_id(), use, source_name=source.name, source_ref=source.ref)
        self._note(attempt, source)
        return self.open(attempt.action_id)

    # -- the window -------------------------------------------------------------

    def open(self, action_id: str) -> AreaWindow | None:
        """Open (or raise) the window resolving *action_id*."""
        declared = self._declared.get(action_id)
        if declared is None:
            return None
        if declared.window is not None:
            declared.window.raise_()
            return declared.window
        _sources, targets = self._host.impress_parties()
        window = AreaWindow(
            self._data,
            attempt=declared.attempt,
            source=declared.source,
            targets=targets,
            parent=self._parent_window,
        )
        if declared.attack is not None:
            window.set_attack(*declared.attack)
        declared.window = window
        window.resolveRequested.connect(self._resolve)
        window.attackRequested.connect(self._host.area_roll_attack)
        window.destroyed.connect(lambda *_a, d=declared: setattr(d, "window", None))
        window.show()
        return window

    # -- the rolls coming back ----------------------------------------------------

    def take_roll(self, roll: object) -> None:
        """One roll in the history: a stage's line, or the attack against a primary.

        Fed by the session's ``rollAdded``, and by the GM window for a roll it made off
        the air, which no session reports: an NPC's attack rolled from the area window.
        """
        if not isinstance(roll, dict):
            return
        group = str(roll.get("group", "") or "")
        if group:
            self._gather(group, roll)
            return
        spec = RollSpec.from_dict(roll.get("spec"))
        if spec is None or not spec.action_id:
            return
        die = int(roll.get("die", 0))
        attack = (die + int(roll.get("bonus", 0)) - int(roll.get("penalty", 0)), die)
        declared = self._declared.get(spec.action_id)
        if declared is None:
            self._early[spec.action_id] = attack
            return
        declared.attack = attack
        self._describe(declared)
        if declared.window is not None:
            declared.window.set_attack(*attack)

    def _gather(self, group: str, roll: dict) -> None:
        stage = self._stages.get(group)
        if stage is None:
            return
        records = stage.records.setdefault(group, [])
        records.append(roll)
        if len(records) < stage.waiting.get(group, 0):
            return
        del self._stages[group]
        if any(g in self._stages for g in stage.order):
            return  # another part of this stage is still out
        ordered: list[dict] = []
        for part in stage.order:
            ordered += sorted(stage.records.get(part, []), key=lambda r: int(r.get("seq", 0)))
        stage.finish(stage.plan, list(zip(stage.about, ordered, strict=False)))

    # -- resolving ------------------------------------------------------------------

    def _resolve(self, plan: AreaPlan) -> None:
        """Roll the NPCs' Dodge checks, ask the players, and go on to the saves."""
        declared = self._declared.get(plan.action_id)
        if declared is not None:
            declared.card.set_done("Resolved — the table is in the history.")
        self._ask_players(plan)
        dodging = [t for t in plan.targets if t.party.kind != PARTY_PLAYER and not t.primary]
        lines = [
            self._line(
                plan,
                target,
                area_npc_dodge_spec(
                    plan.use,
                    target.party.character,
                    self._data,
                    cover=target.cover,
                    adjust=target.adjust,
                ),
            )
            for target in dodging
        ]
        if not lines:
            self._saves(plan, [])
            return
        self._roll_stage(plan, lines, dodging, self._saves)

    def _ask_players(self, plan: AreaPlan) -> None:
        """Leave the caught players a request to roll — they apply what it does.

        One request per set of numbers the GM chose: the cover and the bonus or penalty
        set on a player's row ride on the request they are asked with, so two players
        set differently are asked separately.
        """
        players = [t for t in plan.targets if t.party.kind == PARTY_PLAYER]
        asks: list[RollSpec] = []
        groups: dict[tuple[int, int], list[str]] = {}
        for target in players:
            if target.primary:
                asks += area_request_specs(
                    plan.use,
                    self._data,
                    source=plan.source.name,
                    primary=target.party.name,
                    adjust=target.adjust,
                )
            else:
                groups.setdefault((target.cover, target.adjust), []).append(target.party.name)
        for (cover, adjust), names in groups.items():
            asks += area_request_specs(
                plan.use,
                self._data,
                source=plan.source.name,
                caught=tuple(names),
                cover=cover,
                adjust=adjust,
            )
        for spec in asks:
            if not self._bridge.prompt_roll(spec.to_dict()):
                self._host.impress_notice(
                    "The request for the players' rolls did not reach the session.",
                    theme.color("tint.worse"),
                )
                return

    def _saves(self, plan: AreaPlan, dodged: list[tuple[object, dict]]) -> None:
        """Every NPC's save, at the rank its Dodge check (or a direct hit) left it."""
        made = {
            target.party.ref: (record.get("degree") is not None and int(record["degree"]) > 0)
            for target, record in dodged
            if isinstance(target, AreaTarget)
        }
        lines: list[dict] = []
        about: list[tuple[AreaTarget, AreaEffect]] = []
        shrugged: list[str] = []
        for target in plan.targets:
            if target.party.kind == PARTY_PLAYER:
                continue
            saves = area_npc_save_specs(
                plan.use,
                target.party.character,
                self._data,
                dodged=None if target.primary else made.get(target.party.ref, False),
                adjust=target.adjust,
            )
            if saves is None:
                shrugged.append(target.party.name)
                continue
            for effect, spec in saves:
                lines.append(self._line(plan, target, spec))
                about.append((target, effect))
        if shrugged:
            self._host.impress_notice(
                ", ".join(shrugged) + " dodged clear — no effect.", theme.color("accent")
            )
        if not lines:
            self._finish(plan, [])
            return
        self._roll_stage(plan, lines, about, self._apply)

    def _apply(self, plan: AreaPlan, saves: list[tuple[object, dict]]) -> None:
        """Put each failed save's rung on its NPC, and say what happened."""
        said: list[str] = []
        for about, record in saves:
            target, effect = about  # type: ignore[misc]
            degree = record.get("degree")
            degree = None if degree is None else int(degree)
            rung = area_effect_rung(effect, degree, self._data)
            name = target.party.name
            if rung is None:
                said.append(f"{name} resisted")
                continue
            character = target.party.character or Character()
            changes = list(
                resolve_rung(
                    character, rung, self._data, source=plan.source.name, config=effect.config
                )
            )
            caption = rung_caption(rung, self._data, effect.config)
            if changes and not self._host.impress_apply(target.party, changes):
                said.append(f"{name}: {caption} (not delivered)")
            else:
                said.append(f"{name}: {caption}" if caption else f"{name} resisted")
        self._finish(plan, said)

    def _finish(self, plan: AreaPlan, said: list[str]) -> None:
        players = [t.party.name for t in plan.targets if t.party.kind == PARTY_PLAYER]
        if players:
            said.append(", ".join(players) + " roll from the request")
        if said:
            self._host.impress_notice(
                f"{plan.use.power_name} — " + "; ".join(said), theme.color("accent")
            )

    # -- rolling ----------------------------------------------------------------------

    @staticmethod
    def _line(plan: AreaPlan, target: AreaTarget, spec: RollSpec) -> dict:
        """One creature's roll as a group line: *spec* carries its number and its DC."""
        name = target.party.name
        modifier = spec.modifier
        return {
            "label": f"{name}: {spec.label}",
            "bonus": max(0, modifier),
            "penalty": max(0, -modifier),
            # No lead: each line's whole DC rides as its offset from a DC of 0, which is
            # what lets every creature roll against a DC of its own in one group.
            "dc_offset": spec.dc or 0,
            "player_id": "",
            "spec": replace(spec, targets=(name,), title=plan.title).to_dict(),
        }

    def _roll_stage(
        self,
        plan: AreaPlan,
        lines: list[dict],
        about: list,
        finish: Callable[[AreaPlan, list[tuple[object, dict]]], None],
    ) -> None:
        """Roll *lines* as one stage — split into groups the server will take."""
        stage = _Stage(plan, lines, about, finish)
        parts = [lines[i : i + MAX_GROUP_LINES] for i in range(0, len(lines), MAX_GROUP_LINES)]
        if not self._bridge.in_session:
            self._roll_offline(stage, parts)
            return
        for part in parts:
            group = self._group_id(plan)
            stage.order.append(group)
            stage.waiting[group] = len(part)
            # Listen before asking: a hosting server raises each record while it
            # records it, inside the call below.
            self._stages[group] = stage
        for group, part in zip(list(stage.order), parts, strict=True):
            if not self._bridge.group_roll(group, part, dc=0):
                for pending in stage.order:
                    self._stages.pop(pending, None)
                self._host.impress_notice(
                    f"The {plan.use.power_name} rolls did not reach the session.",
                    theme.color("tint.worse"),
                )
                return

    def _group_id(self, plan: AreaPlan) -> str:
        """A fresh group id in this action's table: ``"<table>.<n>"``."""
        key = self._tables.setdefault(plan.action_id, f"{plan.action_id}-{uuid.uuid4().hex[:4]}")
        self._parts[key] = self._parts.get(key, 0) + 1
        return f"{key}.{self._parts[key]}"

    def _roll_offline(self, stage: _Stage, parts: list[list[dict]]) -> None:
        """No table to roll at: resolve the same groups here, as offline cards."""
        records: list[dict] = []
        for part in parts:
            group = self._group_id(stage.plan)
            result = resolve_group(
                [(line["bonus"] - line["penalty"], line["dc_offset"]) for line in part], dc=0
            )
            for line, check in zip(part, result.lines, strict=True):
                record = {
                    **line,
                    "die": check.die_roll,
                    "dc": check.dc,
                    "degree": check.degree,
                    "critical": check.critical,
                    "group": group,
                }
                self._host.impress_show_offline(record)
                records.append(record)
        stage.finish(stage.plan, list(zip(stage.about, records, strict=False)))
