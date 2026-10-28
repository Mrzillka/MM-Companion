"""One action being rolled: the creatures nobody plays rolled by the server, players asked.

Impress and area attacks resolve the same way, so they share this. An action is rolled
in **stages** — an impression's check, then everyone's resistance; an area's Dodge
checks, then the saves behind them — and every roll of every stage is one of two kinds:

* a creature **nobody plays** is rolled by the session server as a line of a group roll
  (:meth:`ActionRun.roll_group`). Off the air the same group is rolled here
  (:func:`~mm_companion.core.dice.resolve_group`) and shown as offline cards;
* a **player** is asked (:meth:`ActionRun.ask`): a prompt pops up on their screen
  (``ActionPrompt`` kind ``"roll"``) and the same request waits in everyone's history,
  so they roll it on their own sheet with their own numbers. The roll carries the
  action's id and names them, which is how it is found again here.

Every roll lands in one table in the history: the groups are named ``"<action id>.<n>"``
(:func:`~mm_companion.ui.roll_history.table_key` reads the action id back), and an
answered request carries the action id on its spec, which the history reads the same way.

What each roll *means* is the controller's; this only gets it back to them, and lets
the GM roll for a player who is not answering (:meth:`ActionRun.roll_waiting`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, Signal

from mm_companion.core.data_loader import GameData
from mm_companion.core.dice import resolve_group
from mm_companion.core.rules import KIND_POWER_CHECK, RollSpec, localize_spec
from mm_companion.core.session.model import KIND_REQUEST, KIND_ROLL
from mm_companion.core.session.protocol import MAX_GROUP_LINES
from mm_companion.ui import theme
from mm_companion.ui.action_ui import KIND_OUTCOME, KIND_ROLL_ASK, KIND_ROLL_CLOSED

#: What a stage's finish is handed: each line's *about*, with the record it came back as.
Finish = Callable[[list[tuple[object, dict]]], None]


def group_line(
    name: str,
    spec: RollSpec,
    *,
    title: str = "",
    dc: int | None = None,
    minion: bool = False,
) -> dict:
    """One creature's roll as a line of a group: *spec* carries its number.

    *dc* is the line's whole DC — it rides as its offset from a group DC of 0, which is
    what lets every creature roll against a DC of its own in one group. ``None`` takes
    the spec's own. *minion* marks a minion's line, whose natural 20 is no added
    success (protocol v18).
    """
    modifier = spec.modifier
    label = spec.label if spec.label.startswith(f"{name}: ") else f"{name}: {spec.label}"
    return {
        "label": label,
        "bonus": max(0, modifier),
        "penalty": max(0, -modifier),
        "dc_offset": (spec.dc or 0) if dc is None else dc,
        "player_id": "",
        "spec": replace(spec, targets=(name,), title=title or spec.title).to_dict(),
        **({"minion": True} if minion else {}),
    }


def total_of(record: dict) -> int:
    """What a roll came to: the die and its modifier."""
    return int(record.get("die", 0)) + int(record.get("bonus", 0)) - int(record.get("penalty", 0))


@dataclass
class _Stage:
    """One group roll in flight (or several, split to the server's limit)."""

    about: list[object]
    finish: Finish
    waiting: dict[str, int] = field(default_factory=dict)
    records: dict[str, list[dict]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


@dataclass
class _Ask:
    """One roll asked of one player, not answered yet."""

    party: object
    spec: RollSpec
    about: object
    finish: Callable[[object, dict], None]
    #: The history's copy of the request, struck once it is answered.
    request_seq: int | None = None


class ActionRun(QObject):
    """Rolls one action's stages and gathers them again (see the module docstring)."""

    #: Who is being waited for changed — someone answered, or was asked.
    waitingChanged = Signal()

    def __init__(
        self,
        data: GameData,
        bridge,
        host,
        action_id: str,
        *,
        title: str = "",
        asker: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._bridge = bridge
        self._host = host
        self.action_id = action_id
        self.title = title
        self._asker = asker
        self._parts = 0
        self._stages: dict[str, _Stage] = {}
        #: Asks still out, by the name of the row they fill and whether it is an attack.
        self._asks: dict[tuple[str, bool], _Ask] = {}
        self.closed = False

    # -- naming -------------------------------------------------------------------

    def group_id(self) -> str:
        """A fresh group in this action's table: ``"<action id>.<n>"``."""
        self._parts += 1
        return f"{self.action_id}.{self._parts}"

    # -- the server's lines ----------------------------------------------------------

    def roll_group(
        self,
        lines: list[dict],
        about: list[object],
        finish: Finish,
        *,
        lead: dict | None = None,
        lead_about: object = None,
    ) -> bool:
        """Roll *lines* (and a *lead*, whose total nothing here reads) as one stage.

        Every line's DC is its ``dc_offset`` from a group DC of 0. *finish* gets each
        line's *about* with its record — the lead's first, as *lead_about*, when there is
        one. Split to the server's limit, and only finished once every part is back.
        ``False`` if the session would not take it (a notice has been shown).
        """
        if lead is not None:
            about = [lead_about, *about]
        stage = _Stage(about, finish)
        parts = [lines[i : i + MAX_GROUP_LINES] for i in range(0, len(lines), MAX_GROUP_LINES)]
        if not parts:
            parts = [[]]
        if not self._bridge.in_session:
            self._roll_offline(stage, parts, lead)
            return True
        for part in parts:
            group = self.group_id()
            stage.order.append(group)
            stage.waiting[group] = len(part) + (1 if lead is not None and not stage.waiting else 0)
            # Listen before asking: a hosting server raises each record while it
            # records it, inside the call below.
            self._stages[group] = stage
        for index, (group, part) in enumerate(zip(list(stage.order), parts, strict=True)):
            kwargs = {"lead": lead} if lead is not None and index == 0 else {"dc": 0}
            if not self._bridge.group_roll(
                group, part, critical_bonus=self._data.system.critical_effect_bonus, **kwargs
            ):
                for pending in stage.order:
                    self._stages.pop(pending, None)
                self._host.impress_notice(
                    f"The {self.title or 'action'} rolls did not reach the session.",
                    theme.color("tint.worse"),
                )
                return False
        return True

    def _roll_offline(self, stage: _Stage, parts: list[list[dict]], lead: dict | None) -> None:
        records: list[dict] = []
        for index, part in enumerate(parts):
            group = self.group_id()
            with_lead = lead is not None and index == 0
            result = resolve_group(
                [
                    (line["bonus"] - line["penalty"], line["dc_offset"], bool(line.get("minion")))
                    for line in part
                ],
                lead_modifier=(lead["bonus"] - lead["penalty"]) if with_lead else None,
                dc=None if with_lead else 0,
                critical_bonus=self._data.system.critical_effect_bonus,
            )
            if with_lead:
                record = {
                    **lead,
                    "die": result.lead_die,
                    "dc": None,
                    "degree": None,
                    "critical": result.lead_die == 1
                    or (result.lead_die == 20 and not lead.get("minion")),
                    "group": group,
                }
                self._host.impress_show_offline(record)
                records.append(record)
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
        stage.finish(list(zip(stage.about, records, strict=False)))

    # -- asking players ---------------------------------------------------------------

    def ask(
        self,
        party,
        spec: RollSpec,
        about: object,
        finish: Callable[[object, dict], None],
    ) -> bool:
        """Ask *party* (a player) to roll *spec*; *finish* gets *about* and their roll.

        The spec must name the row it fills in ``targets`` — the roll that comes back
        is matched by it. The player gets a prompt, and everyone a request card (which
        anyone may answer, an ally interposing); the first roll back counts.
        """
        spec = replace(spec, action_id=self.action_id, title=spec.title or self.title)
        name = spec.targets[0] if spec.targets else party.name
        if not spec.targets:
            spec = replace(spec, targets=(name,))
        key = (name, spec.kind == KIND_POWER_CHECK)
        self._asks[key] = _Ask(party, spec, about, finish)
        payload = spec.to_dict()
        # The prompt goes without the ladder. An action's payload is nested at most four
        # deep and capped at 4 KB (sanitize_mod_payload), so a ladder would be cut down
        # without a word, or sink the whole prompt; and the player needs none of it,
        # since this table applies what their save did. The request card keeps it: an
        # NPC the GM answers it with is put through it (see GMWindow._apply_answer).
        prompt = replace(spec, ladder=None).to_dict()
        prompted = bool(
            self._bridge.prompt_action(
                party.source, KIND_ROLL_ASK, self.action_id, {"spec": prompt, "asker": self._asker}
            )
        )
        carded = bool(self._bridge.prompt_roll(payload))
        if not (prompted or carded):
            self._asks.pop(key, None)
            self.waitingChanged.emit()
            return False
        self.waitingChanged.emit()
        return True

    def waiting(self) -> list[str]:
        """The rows still waiting on a player's roll, by name."""
        return [name for name, _attack in self._asks]

    def waiting_for(self, name: str) -> bool:
        return any(n == name for n, _attack in self._asks)

    # -- what comes back ----------------------------------------------------------------

    def take_roll(self, roll: object) -> bool:
        """One record from the history; ``True`` if it was this action's to take."""
        if self.closed or not isinstance(roll, dict):
            return False
        group = str(roll.get("group", "") or "")
        if group:
            return self._gather(group, roll)
        spec = RollSpec.from_dict(roll.get("spec"))
        if spec is None or spec.action_id != self.action_id or not spec.targets:
            return False
        key = (spec.targets[0], spec.kind == KIND_POWER_CHECK)
        ask = self._asks.get(key)
        if ask is None:
            return False
        if roll.get("kind") == KIND_REQUEST:
            # The history's copy of our own request: kept, to strike once it is answered.
            if isinstance(roll.get("seq"), int):
                ask.request_seq = roll["seq"]
            return True
        if roll.get("kind", KIND_ROLL) != KIND_ROLL:
            return False
        del self._asks[key]
        self._settle(ask)
        self.waitingChanged.emit()
        ask.finish(ask.about, roll)
        return True

    def _gather(self, group: str, roll: dict) -> bool:
        stage = self._stages.get(group)
        if stage is None:
            return False
        records = stage.records.setdefault(group, [])
        records.append(roll)
        if len(records) < stage.waiting.get(group, 0):
            return True
        del self._stages[group]
        if any(g in self._stages for g in stage.order):
            return True  # another part of this stage is still out
        ordered: list[dict] = []
        for part in stage.order:
            ordered += sorted(stage.records.get(part, []), key=lambda r: abs(int(r.get("seq", 0))))
        stage.finish(list(zip(stage.about, ordered, strict=False)))
        return True

    def _settle(self, ask: _Ask) -> None:
        """An ask is answered (or dropped): close its prompt, strike its card."""
        self._bridge.prompt_action(
            ask.party.source, KIND_ROLL_CLOSED, self.action_id, {"name": ask.spec.targets[0]}
        )
        if ask.request_seq is not None:
            self._bridge.remove_roll(ask.request_seq)

    # -- the GM stepping in ----------------------------------------------------------------

    def roll_waiting(self) -> None:
        """Roll every ask still out here, off the player's sheet as the GM last saw it.

        For a player who has gone quiet. Each is rolled by the server as the GM's own
        line, with the player's number filled in from their snapshot, and handed to the
        same *finish* their own roll would have reached.
        """
        asks = list(self._asks.values())
        self._asks.clear()
        self.waitingChanged.emit()
        for ask in asks:
            self._settle(ask)
            character = getattr(ask.party, "character", None)
            spec = replace(localize_spec(ask.spec, character, self._data), trait_key="")
            name = ask.spec.targets[0]
            line = group_line(name, spec, title=ask.spec.title)
            done = ask.finish

            def finish(pairs, about=ask.about, done=done) -> None:
                for _about, record in pairs:
                    done(about, record)

            if spec.dc is None:
                self.roll_group([], [], finish, lead=line, lead_about=ask.about)
            else:
                self.roll_group([line], [ask.about], finish)

    def tell(self, party, text: str) -> None:
        """Tell one player what the action did to them (a toast on their screen)."""
        if getattr(party, "source", ""):
            self._bridge.prompt_action(party.source, KIND_OUTCOME, self.action_id, {"text": text})

    def close(self) -> None:
        """Stop: withdraw every prompt still out, and take nothing more."""
        for ask in list(self._asks.values()):
            self._settle(ask)
        self._asks.clear()
        self._stages.clear()
        self.closed = True
        self.waitingChanged.emit()
