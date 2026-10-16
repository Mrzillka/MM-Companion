"""The GM's half of an area attack: note it, open it, roll it, apply it.

:class:`~mm_companion.ui.area.AreaWindow` decides; this carries the decision out, and
it is the only part of the action that touches the session:

* a player's declared area arrives as an ``ActionRequest`` (kind ``"area"``) and puts an
  :class:`~mm_companion.ui.roll_history.ActionCard` in the GM's history with a **Resolve
  Area Attack** button — whole, when the player chose who is caught, or bare, when the
  GM is to. The attack check against the primary lands as an ordinary roll carrying the
  attempt's id and is paired up with it here; a bare one's attack is asked of the
  player from the window once the GM has picked a primary;
* the GM can start one for anybody — an NPC from its card or its sheet, a player from
  their card on the board, either from the roller — which opens the window straight
  away, the attacker still the GM's to change;
* **resolving** rolls the creatures nobody plays in two stages, each a server-side group
  roll: every NPC's Dodge check, then — once those are in — each one's save at the rank
  its Dodge left. A **player** in the area is asked for their Dodge, and once it is back,
  for the save at the rank it left. Every roll lands in one table in the history and in
  the window's Result column, and the rung each failed save lands on is applied — onto
  an NPC's model, or as condition commands to a player's app;
* **requesting** instead leaves the history a Dodge card and a save card (full rank or
  half) for everyone caught, and rolls nothing more;
* an area **Healing** is one check of the wielder's, and each degree of success takes a
  condition off everyone in the area.

It reaches the GM window only through :class:`AreaHost`, the Impress host surface plus
two methods. :mod:`mm_companion.ui.action_run` does the rolling and the asking.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    AREA_MODE_REQUEST,
    KIND_POWER_CHECK,
    AreaAttempt,
    AreaEffect,
    RollSpec,
    area_ask_dodge_spec,
    area_ask_save_spec,
    area_capable_builds,
    area_effect_rung,
    area_heal_removals,
    area_heal_spec,
    area_npc_dodge_spec,
    area_npc_save_specs,
    area_primary_rank,
    area_request_specs,
    area_stage_rank,
    power_area_use,
    power_area_uses,
    resolve_rung,
    rung_caption,
)
from mm_companion.ui import theme
from mm_companion.ui.action_run import ActionRun, group_line, total_of
from mm_companion.ui.action_ui import PHASE_DONE, PHASE_SETUP
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

    def action_add_card(self, card: ActionCard) -> None:
        """Put the GM's reminder card in their history."""

    def area_roll_attack(self, spec: RollSpec) -> None:
        """Roll an NPC's attack against the primary on the GM's roller."""

    def action_remove(self, party: ImpressParty, changes: list[tuple[str, str | None]]) -> bool:
        """Take conditions off one party; ``False`` if that could not be delivered."""

    def action_request(self, spec: RollSpec) -> None:
        """Leave a request card for *spec* in the history (the table's, or the GM's own)."""


def roll_cell(label: str, record: dict) -> str:
    """One roll as the window's Result column shows it: what, how much, and how it went."""
    text = f"{html.escape(label)} <b>{total_of(record)}</b>"
    dc = record.get("dc")
    if dc is not None:
        text += f" vs {int(dc)}"
    degree = record.get("degree")
    if degree is not None:
        degree = int(degree)
        text += " ✓" if degree > 0 else " " + "✗" * max(1, min(3, abs(degree)))
    return text


@dataclass
class _Resolving:
    """One resolve in flight: who is still to land, and what has been said so far."""

    plan: AreaPlan
    pending: set[str]
    said: list[str] = field(default_factory=list)
    cells: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _Declared:
    """One area attack the GM has been told about (or started) and not settled yet."""

    attempt: AreaAttempt
    source: ImpressParty
    card: ActionCard | None
    run: ActionRun
    attack: tuple[int, int] | None = None
    window: AreaWindow | None = None
    builds: list[Power] = field(default_factory=list)
    resolving: _Resolving | None = None
    #: A player's declaration waiting in the history, rather than the GM's own.
    from_card: bool = True
    #: Who the GM may switch the attacker to — an area the GM started; empty for a
    #: player's declaration, whose attacker is theirs.
    sources: list[ImpressParty] = field(default_factory=list)


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
        declared = self._make(attempt, source, from_card=True)
        self._show_card(declared)
        return declared

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

    def _make(self, attempt: AreaAttempt, source: ImpressParty, *, from_card: bool) -> _Declared:
        run = ActionRun(
            self._data,
            self._bridge,
            self._host,
            attempt.action_id,
            title=f"{source.name}: {attempt.use.power_name} (area)",
            asker="The GM",
            parent=self,
        )
        declared = _Declared(attempt, source, None, run, from_card=from_card)
        run.waitingChanged.connect(lambda d=declared: self._on_waiting(d))
        self._declared[attempt.action_id] = declared
        early = self._early.pop(attempt.action_id, None)
        if early is not None:
            declared.attack = early
        return declared

    def _show_card(self, declared: _Declared) -> None:
        """Put the declaration's card in the GM's history (once)."""
        if declared.card is not None:
            return
        card = ActionCard(declared.attempt.action_id, "", RESOLVE_CAPTION)
        card.actionRequested.connect(self.open)
        declared.card = card
        self._describe(declared)
        self._host.action_add_card(card)

    def _describe(self, declared: _Declared) -> None:
        if declared.card is None:
            return
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
        elif not attempt.primary:
            text += "<br>Who is caught is yours to set."
        declared.card.set_text(text)

    def start_for_npc(
        self,
        character: Character,
        power: Power,
        key: str,
        *,
        name: str = "",
        effect_index: int = 0,
    ) -> AreaWindow | None:
        """The GM used an NPC's power on an area — from its card or its sheet: open it.

        *key* is the NPC's cast key (its file), which is how it is found among the
        parties — never by *name*, which two identical minions share. *name* is what to
        call it when it is not in the cast at all. Any attack will do: the GM is the one
        choosing to spread it, so the free use is read as on. *effect_index* is which of
        the power's area effects (see :func:`~mm_companion.core.rules.area_key`).
        """
        use = power_area_use(
            power, character, self._data, npc=True, free_use=True, effect_index=effect_index
        )
        if use is None:
            return None
        sources, _targets = self._host.impress_parties()
        name = name or key or "An NPC"
        source = next(
            (p for p in sources if p.kind == PARTY_NPC and key and p.source == key), None
        ) or ImpressParty(
            ref=f"sheet:{key or name}", name=name, kind=PARTY_NPC, source=key, character=character
        )
        if source.character is None:
            source.character = character
        if source not in sources:
            sources = [source, *sources]
        return self._open_new(
            source, use, sources, builds=area_capable_builds(character, self._data)
        )

    def start_for(self, party: ImpressParty) -> AreaWindow | None:
        """The GM picked *party* — an NPC or a player — to use an attack on an area.

        From a creature's card on the board, or the roller's Area button. The window
        opens on the creature's first area, and the GM may switch the attacker, the
        power and its shape there. A player is read off their latest snapshot, and asked
        for the attack like one who declared it.
        """
        sources, _targets = self._host.impress_parties()
        party = next((p for p in sources if p.ref == party.ref), party)
        character = party.character
        if character is None:
            self._host.impress_notice(
                f"No sheet from {party.name} yet — their attacks are not known.",
                theme.color("tint.warning"),
            )
            return None
        npc = party.kind != PARTY_PLAYER
        use = next(
            (
                use
                for build in area_capable_builds(character, self._data)
                for use in power_area_uses(build, character, self._data, npc=npc, free_use=True)
            ),
            None,
        )
        if use is None:
            self._host.impress_notice(
                f"{party.name} has no attack to use on an area.", theme.color("tint.warning")
            )
            return None
        if party not in sources:
            sources = [party, *sources]
        return self._open_new(party, use, sources)

    def open_new(self) -> AreaWindow | None:
        """The roller's Area button: the first creature with an attack, the GM to change it."""
        sources, _targets = self._host.impress_parties()
        for party in sources:
            character = party.character
            if character is None:
                continue
            if area_capable_builds(character, self._data):
                return self.start_for(party)
        self._host.impress_notice(
            "Nobody in the cast or on the scene has an attack to use on an area.",
            theme.color("tint.warning"),
        )
        return None

    def _open_new(
        self,
        source: ImpressParty,
        use,
        sources: list[ImpressParty],
        *,
        builds: list[Power] | None = None,
    ) -> AreaWindow | None:
        attempt = AreaAttempt(new_action_id(), use, source_name=source.name, source_ref=source.ref)
        declared = self._make(attempt, source, from_card=False)
        declared.builds = list(builds or [])
        declared.sources = list(sources)
        return self.open(attempt.action_id)

    # -- the window -------------------------------------------------------------

    def open(self, action_id: str) -> AreaWindow | None:
        """Open (or raise) the window resolving *action_id*."""
        declared = self._declared.get(action_id)
        if declared is None:
            return None
        if declared.window is not None:
            declared.window.show()
            declared.window.raise_()
            declared.window.activateWindow()
            return declared.window
        _sources, targets = self._host.impress_parties()
        if declared.source.character is None:
            # A player's snapshot, for the shape and their own attack's numbers.
            found = next((p for p in targets if p.ref == declared.source.ref), None)
            if found is not None:
                declared.source.character = found.character
        window = AreaWindow(
            self._data,
            attempt=declared.attempt,
            source=declared.source,
            targets=targets,
            sources=declared.sources or None,
            builds=declared.builds,
            from_card=declared.from_card,
            parent=self._parent_window,
        )
        if declared.attack is not None:
            window.set_attack(*declared.attack)
        declared.window = window
        window.resolveRequested.connect(lambda plan, d=declared: self._go(d, plan))
        window.attackRequested.connect(
            lambda spec, source, d=declared: self._attack(d, spec, source)
        )
        window.cancelled.connect(lambda d=declared: self._on_cancelled(d))
        window.dismissed.connect(lambda d=declared: self._on_dismissed(d))
        window.calledOff.connect(lambda d=declared: self._call_off(d))
        window.rollWaitingRequested.connect(declared.run.roll_waiting)
        window.show()
        return window

    def _on_cancelled(self, declared: _Declared) -> None:
        """Closed before resolving. A player's stays in the history to come back to."""
        window, declared.window = declared.window, None
        if window is not None:
            window.deleteLater()
        if not declared.from_card:
            declared.run.close()
            self._declared.pop(declared.attempt.action_id, None)

    def _on_dismissed(self, declared: _Declared) -> None:
        declared.run.close()
        if declared.card is not None:
            declared.card.set_done("Dismissed.")
        window, declared.window = declared.window, None
        if window is not None:
            window.deleteLater()
        self._declared.pop(declared.attempt.action_id, None)

    def _attack(self, declared: _Declared, spec: RollSpec, source: ImpressParty) -> None:
        """The window asked for the attack: rolled for an NPC, asked of a player."""
        declared.source = source
        if declared.source.kind != PARTY_PLAYER:
            self._host.area_roll_attack(spec)
            return
        if not declared.run.ask(declared.source, spec, None, lambda _a, r, d=declared: None):
            self._host.impress_notice(
                f"Could not ask {declared.source.name} for the attack.", theme.color("tint.worse")
            )
            if declared.window is not None:
                declared.window.set_attack_asked(False)

    def _on_waiting(self, declared: _Declared) -> None:
        names = declared.run.waiting()
        if declared.window is not None:
            declared.window.set_waiting(names)
        if declared.card is not None and declared.resolving is not None:
            declared.card.set_status(
                ("Waiting for " + ", ".join(names) + "…") if names else "Rolling…",
                button="Open",
            )

    # -- the rolls coming back ----------------------------------------------------

    def take_roll(self, roll: object) -> None:
        """One roll in the history: a stage's line, an asked roll, or the attack.

        Fed by the session's ``rollAdded``, and by the GM window for a roll it made off
        the air, which no session reports: an NPC's attack rolled from the area window.
        """
        if not isinstance(roll, dict):
            return
        for declared in list(self._declared.values()):
            if declared.run.take_roll(roll):
                spec = RollSpec.from_dict(roll.get("spec"))
                if (
                    spec is not None
                    and spec.kind == KIND_POWER_CHECK
                    and not roll.get("group")
                    and declared.resolving is None
                ):
                    self._take_attack(declared, roll)
                return
        if roll.get("group") or roll.get("kind", "roll") != "roll":
            return
        spec = RollSpec.from_dict(roll.get("spec"))
        if spec is None or not spec.action_id or spec.kind not in (KIND_POWER_CHECK, ""):
            return
        declared = self._declared.get(spec.action_id)
        if declared is None:
            self._early[spec.action_id] = (total_of(roll), int(roll.get("die", 0)))
            return
        self._take_attack(declared, roll)

    def _take_attack(self, declared: _Declared, roll: dict) -> None:
        """The attack against the primary came in — or was rerolled — before resolving.

        Once the GM has resolved (or requested) the area, an attack that comes in
        changes nothing: what it decided has already been rolled on.
        """
        if declared.resolving is not None:
            return
        attack = (total_of(roll), int(roll.get("die", 0)))
        declared.attack = attack
        self._describe(declared)
        if declared.window is not None:
            declared.window.set_attack(*attack, reroll=bool(roll.get("reroll")))

    # -- resolving ------------------------------------------------------------------

    def _go(self, declared: _Declared, plan: AreaPlan) -> None:
        """The window's go: resolve the area, heal with it, or only request the rolls."""
        declared.source = plan.source
        declared.attempt = AreaAttempt(
            declared.attempt.action_id,
            plan.use,
            source_name=plan.source.name,
            source_ref=plan.source.ref,
            mode=plan.mode,
        )
        resolving = _Resolving(plan, pending={t.party.name for t in plan.targets})
        declared.resolving = resolving
        self._show_card(declared)
        if declared.card is not None:
            declared.card.set_status("Rolling…", button="Open")
        declared.run.title = plan.title
        if plan.heals:
            self._heal(declared, plan)
        elif plan.mode == AREA_MODE_REQUEST:
            self._request(declared, plan)
        else:
            self._resolve(declared, plan)

    def _resolve(self, declared: _Declared, plan: AreaPlan) -> None:
        """Roll the NPCs' Dodge checks, ask the players, and go on to the saves."""
        run = declared.run

        for target in plan.targets:
            if target.party.kind != PARTY_PLAYER:
                continue
            if target.primary:
                self._ask_saves(declared, target, made=None)
            else:
                spec = area_ask_dodge_spec(
                    plan.use,
                    self._data,
                    target=target.party.name,
                    action_id=plan.action_id,
                    source=plan.source.name,
                    cover=target.cover,
                    adjust=target.adjust,
                )
                if not run.ask(target.party, spec, target, self._player_dodged_for(declared)):
                    self._land(declared, target, "not asked — no session", "tint.worse")

        dodging = [t for t in plan.targets if t.party.kind != PARTY_PLAYER and not t.primary]
        if not dodging:
            self._npc_saves(declared, {})
            return
        lines = [
            group_line(
                target.party.name,
                area_npc_dodge_spec(
                    plan.use,
                    target.party.character,
                    self._data,
                    cover=target.cover,
                    adjust=target.adjust,
                ),
                title=plan.title,
            )
            for target in dodging
        ]
        run.roll_group(lines, list(dodging), lambda pairs, d=declared: self._npc_dodged(d, pairs))

    # -- requesting ----------------------------------------------------------------------

    def _request(self, declared: _Declared, plan: AreaPlan) -> None:
        """Leave the history a Dodge card and a save card, and roll nothing more.

        Creatures the GM set the same cover and adjustment for share their cards; the
        primary target the attack hit makes no Dodge, so it is named on the save alone.
        """
        groups: dict[tuple[int, int], list[str]] = {}
        hit: AreaTarget | None = None
        for target in plan.targets:
            if target.primary:
                hit = target
                continue
            groups.setdefault((target.cover, target.adjust), []).append(target.party.name)
        specs: list[RollSpec] = []
        for (cover, adjust), names in groups.items():
            specs += area_request_specs(
                plan.use,
                self._data,
                source=plan.source.name,
                caught=tuple(names),
                action_id=plan.action_id,
                cover=cover,
                adjust=adjust,
            )
        if hit is not None:
            specs += area_request_specs(
                plan.use,
                self._data,
                source=plan.source.name,
                primary=hit.party.name,
                action_id=plan.action_id,
                adjust=hit.adjust,
            )
        for spec in specs:
            self._host.action_request(spec)
        resolving = declared.resolving
        if resolving is not None:
            resolving.pending.clear()
        if declared.window is not None:
            for target in plan.targets:
                declared.window.set_result(
                    target.party.ref,
                    "save from the card" if target.primary else "Dodge and save from the cards",
                )
        self._host.impress_notice(
            f"{plan.use.power_name}: the Dodge and the save are in the history — everyone "
            "caught rolls from the cards.",
            theme.color("accent"),
        )
        if declared.card is not None:
            declared.card.set_done("Requested — the cards are in the history.")
        if declared.window is not None:
            declared.window.finish("Requested — everyone caught rolls from the cards.")
        declared.run.close()
        self._retire(declared)

    # -- healing ------------------------------------------------------------------------

    def _heal(self, declared: _Declared, plan: AreaPlan) -> None:
        """An area Healing: the wielder's one check, then a condition off each per degree."""
        spec = area_heal_spec(
            plan.use, self._data, source=plan.source.name, action_id=plan.action_id
        )
        if spec is None:
            return
        run = declared.run
        if plan.source.kind == PARTY_PLAYER:
            if not run.ask(plan.source, spec, None, lambda _a, r, d=declared: self._healed(d, r)):
                self._host.impress_notice(
                    f"Could not ask {plan.source.name} for the check.", theme.color("tint.worse")
                )
            return
        line = group_line(plan.source.name, spec, title=plan.title, dc=spec.dc)
        run.roll_group([line], [None], lambda pairs, d=declared: self._healed(d, pairs[0][1]))

    def _healed(self, declared: _Declared, record: dict) -> None:
        resolving = declared.resolving
        if resolving is None:
            return
        plan = resolving.plan
        degree = record.get("degree")
        successes = max(0, int(degree)) if degree is not None else 0
        check = roll_cell("Check", record)
        for target in plan.targets:
            self._cell(declared, target, check)
            character = target.party.character
            if character is None:
                self._land(declared, target, "no sheet seen yet — heal by hand", "tint.warning")
                continue
            removals = list(area_heal_removals(plan.use, character, successes, self._data))
            if not removals:
                text = "nothing to heal" if successes else "the check failed"
                self._land(declared, target, text, "")
                continue
            catalog = self._data.condition_catalog()
            names = [catalog[c].name if c in catalog else c for c, _p in removals]
            text = "removed " + ", ".join(names)
            if not self._host.action_remove(target.party, removals):
                text += " (not delivered)"
            elif target.party.kind == PARTY_PLAYER:
                declared.run.tell(target.party, f"{plan.use.power_name}: {text}")
            self._land(declared, target, text, "tint.better")

    # -- the NPCs ---------------------------------------------------------------------

    def _npc_dodged(self, declared: _Declared, pairs: list[tuple[object, dict]]) -> None:
        made: dict[str, bool] = {}
        for target, record in pairs:
            if not isinstance(target, AreaTarget):
                continue
            made[target.party.ref] = record.get("degree") is not None and int(record["degree"]) > 0
            self._cell(declared, target, roll_cell("Dodge", record))
        self._npc_saves(declared, made)

    def _npc_saves(self, declared: _Declared, made: dict[str, bool]) -> None:
        """Every NPC's save, at the rank its Dodge check (or a direct hit) left it."""
        resolving = declared.resolving
        if resolving is None:
            return
        plan = resolving.plan
        lines: list[dict] = []
        about: list[tuple[AreaTarget, AreaEffect]] = []
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
                self._land(declared, target, "dodged clear — no effect", "tint.better")
                continue
            if not saves:
                self._land(declared, target, "nothing to resist", "")
                continue
            for effect, spec in saves:
                lines.append(group_line(target.party.name, spec, title=plan.title))
                about.append((target, effect))
        if lines:
            declared.run.roll_group(
                lines, list(about), lambda pairs, d=declared: self._npc_saved(d, pairs)
            )

    def _npc_saved(self, declared: _Declared, pairs: list[tuple[object, dict]]) -> None:
        """Put each failed save's rung on its NPC."""
        outcomes: dict[str, list[str]] = {}
        targets: dict[str, AreaTarget] = {}
        for about, record in pairs:
            target, effect = about  # type: ignore[misc]
            targets[target.party.name] = target
            self._cell(declared, target, roll_cell(effect.resistance_label or "Save", record))
            caption = self._apply(declared, target, effect, record)
            outcomes.setdefault(target.party.name, []).append(caption)
        for name, captions in outcomes.items():
            said = [c for c in captions if c]
            text = "; ".join(said) if said else "resisted"
            self._land(declared, targets[name], text, "tint.worse" if said else "tint.better")

    # -- the players --------------------------------------------------------------------

    def _player_dodged_for(self, declared: _Declared):
        return lambda target, record: self._player_dodged(declared, target, record)

    def _player_dodged(self, declared: _Declared, target: AreaTarget, record: dict) -> None:
        made = record.get("degree") is not None and int(record["degree"]) > 0
        self._cell(declared, target, roll_cell("Dodge", record))
        self._ask_saves(declared, target, made=made)

    def _ask_saves(self, declared: _Declared, target: AreaTarget, *, made: bool | None) -> None:
        """Ask a player for each save their Dodge (or a direct hit) left them, in turn."""
        resolving = declared.resolving
        if resolving is None:
            return
        use = resolving.plan.use
        queue: list[tuple[AreaEffect, int]] = []
        for effect in use.effects:
            rank = (
                area_primary_rank(effect)
                if made is None
                else area_stage_rank(effect.rank, made, target.party.character, self._data)
            )
            if rank is None:
                self._land(declared, target, "dodged clear — no effect", "tint.better")
                return
            queue.append((effect, rank))
        self._ask_next_save(declared, target, queue, [])

    def _ask_next_save(
        self,
        declared: _Declared,
        target: AreaTarget,
        queue: list[tuple[AreaEffect, int]],
        said: list[str],
    ) -> None:
        resolving = declared.resolving
        if resolving is None:
            return
        plan = resolving.plan
        while queue:
            effect, rank = queue.pop(0)
            spec = area_ask_save_spec(
                plan.use,
                effect,
                rank,
                self._data,
                target=target.party.name,
                action_id=plan.action_id,
                source=plan.source.name,
                adjust=target.adjust,
            )
            if spec is None:
                continue

            def finish(about, record, effect=effect) -> None:
                self._player_saved(declared, target, effect, record, queue, said)

            if not declared.run.ask(target.party, spec, (target, effect), finish):
                self._land(declared, target, "not asked — no session", "tint.worse")
            return
        captions = [c for c in said if c]
        text = "; ".join(captions) if captions else "resisted"
        self._land(declared, target, text, "tint.worse" if captions else "tint.better")

    def _player_saved(
        self,
        declared: _Declared,
        target: AreaTarget,
        effect: AreaEffect,
        record: dict,
        queue: list[tuple[AreaEffect, int]],
        said: list[str],
    ) -> None:
        self._cell(declared, target, roll_cell(effect.resistance_label or "Save", record))
        caption = self._apply(declared, target, effect, record)
        said.append(caption)
        if caption:
            declared.run.tell(target.party, f"{declared.attempt.use.power_name}: {caption}")
        self._ask_next_save(declared, target, queue, said)

    # -- landing ---------------------------------------------------------------------------

    def _apply(
        self, declared: _Declared, target: AreaTarget, effect: AreaEffect, record: dict
    ) -> str:
        """Put the rung a save landed on onto its creature; what it did, in words."""
        degree = record.get("degree")
        rung = area_effect_rung(effect, None if degree is None else int(degree), self._data)
        if rung is None:
            return ""
        source = declared.resolving.plan.source.name if declared.resolving else ""
        # Escalation reads the target's current conditions; with no sheet to read yet
        # (a player whose snapshot has not arrived) it reads none.
        character = target.party.character or Character()
        changes = list(
            resolve_rung(character, rung, self._data, source=source, config=effect.config)
        )
        caption = rung_caption(rung, self._data, effect.config)
        if changes and not self._host.impress_apply(target.party, changes):
            return f"{caption} (not delivered)"
        return caption

    def _cell(self, declared: _Declared, target: AreaTarget, cell: str) -> None:
        resolving = declared.resolving
        if resolving is None:
            return
        cells = resolving.cells.setdefault(target.party.name, [])
        cells.append(cell)
        if declared.window is not None:
            declared.window.set_result(target.party.ref, " · ".join(cells))

    def _land(self, declared: _Declared, target: AreaTarget, text: str, token: str) -> None:
        """One creature's part is over: show what it came to, and finish once all are."""
        resolving = declared.resolving
        if resolving is None or target.party.name not in resolving.pending:
            return
        resolving.pending.discard(target.party.name)
        resolving.said.append(f"{target.party.name}: {text}")
        cells = resolving.cells.get(target.party.name, [])
        shown = " · ".join([*cells, f"<b>{html.escape(text)}</b>"]) if cells else html.escape(text)
        if declared.window is not None:
            declared.window.set_result(target.party.ref, shown, token)
        if not resolving.pending:
            self._finish(declared)

    def _finish(self, declared: _Declared) -> None:
        resolving = declared.resolving
        if resolving is None:
            return
        summary = f"{resolving.plan.use.power_name} — " + "; ".join(resolving.said)
        self._host.impress_notice(summary, theme.color("accent"))
        if declared.card is not None:
            declared.card.set_done("Resolved — the table is in the history.")
        if declared.window is not None:
            declared.window.finish("All in — each row shows what it did.")
        declared.run.close()
        self._retire(declared)

    def _call_off(self, declared: _Declared) -> None:
        """The GM stopped waiting: nothing still out counts."""
        declared.run.close()
        resolving = declared.resolving
        left = sorted(resolving.pending) if resolving is not None else []
        if declared.card is not None:
            declared.card.set_done("Called off.")
        if declared.window is not None:
            declared.window.finish(
                "Called off" + (f" — never landed: {', '.join(left)}." if left else ".")
            )
        self._retire(declared)

    def _retire(self, declared: _Declared) -> None:
        """Forget a settled action once its window is gone (or now, with no window)."""
        window = declared.window
        if window is None or window.phase == PHASE_SETUP:
            self._declared.pop(declared.attempt.action_id, None)
            return
        if window.phase == PHASE_DONE and not window.isVisible():
            self._declared.pop(declared.attempt.action_id, None)
            window.deleteLater()
            declared.window = None
            return
        window.finished.connect(lambda _r, d=declared: self._on_done_window_closed(d))

    def _on_done_window_closed(self, declared: _Declared) -> None:
        if declared.window is not None and declared.window.phase == PHASE_DONE:
            self._declared.pop(declared.attempt.action_id, None)
            declared.window.deleteLater()
            declared.window = None
