"""A team attack as a **call to roll** (combat maneuvers plan, decision 15).

The GM names a target and a bonus or penalty, and sends the call: a request record in
everyone's history whose spec is a :data:`KIND_TEAM_CALL`. Anyone who presses its
**Join** — a player, or the GM for one of their NPCs — has their *next* attack stamped
as an entry (:func:`team_join_spec`): the call's action id and title, so it joins the
call's table on every screen, the attacker's name as its row, and the GM's bonus or
penalty on the check. The attack keeps its save, maneuvers and all, which is what the GM
combines when they resolve it.

Which entries combine is :func:`team_fits`: the same effect and resistance as the first
entry, and ranks within the rules' spread of the strongest. The arithmetic after that is
:mod:`.team_attack`'s, shared with the NPC-group window.
"""

from __future__ import annotations

from dataclasses import replace

from ..data_loader import GameData
from .maneuvers import _relabel_check, has_attack_check
from .rolls import RollSpec
from .team_attack import TeamOption

__all__ = [
    "KIND_TEAM_CALL",
    "TEAM_NOT_SAME",
    "is_team_call",
    "team_call_spec",
    "team_call_target",
    "team_entry_option",
    "team_fits",
    "team_join_spec",
]

#: The spec kind of a team attack's call to roll (a request record nobody rolls).
KIND_TEAM_CALL = "team-call"

#: Why an entry is left out of the team: its attack is not the first one's kind.
TEAM_NOT_SAME = "a different effect or resistance"


def team_call_spec(action_id: str, target_name: str, adjust: int = 0) -> RollSpec:
    """The call the GM sends: at *target_name*, *adjust* on every attack that joins."""

    title = f"Team attack → {target_name}"
    label = f"{title} ({adjust:+d})" if adjust else title
    return RollSpec(
        label=label,
        modifier=adjust,
        kind=KIND_TEAM_CALL,
        hint="Join, then roll an attack: it counts toward the team attack",
        targets=(target_name,),
        action_id=action_id,
        title=title,
    )


def is_team_call(spec: RollSpec | None) -> bool:
    """Whether *spec* is a team attack's call to roll."""

    return spec is not None and spec.kind == KIND_TEAM_CALL and bool(spec.action_id)


def team_call_target(call: RollSpec) -> str:
    """The name of the creature *call* is at (``""`` when it names nobody)."""

    return call.targets[0] if call.targets else ""


def team_join_spec(spec: RollSpec, call: RollSpec, attacker: str, game_data: GameData) -> RollSpec:
    """The attack *spec* as *attacker*'s entry in the team attack *call*.

    It carries the call's action id and title (so it joins the call's table) and names
    the attacker as its row; with a target named that way it is an aimed attack, so no
    card offers its save — the GM's app resolves it once, for the whole team. The call's
    bonus or penalty goes on the attack check, restated in the label.
    """

    adjust = call.modifier
    label = spec.label
    modifier = spec.modifier
    if adjust and has_attack_check(spec, game_data):
        modifier += adjust
        label = _relabel_check(label, spec.modifier, modifier)
    label = f"{label} (team attack {adjust:+d})" if adjust else f"{label} (team attack)"
    return replace(
        spec,
        label=label,
        modifier=modifier,
        action_id=call.action_id,
        title=call.title,
        targets=(attacker,),
    )


def team_entry_option(spec: RollSpec | None, key: str) -> TeamOption | None:
    """An entry's rolled attack as what it brings to the team (``None``: no save)."""

    if spec is None or spec.follow_up is None:
        return None
    return TeamOption(key, spec.label, spec.follow_up, spec)


def team_fits(options: list[TeamOption], game_data: GameData) -> dict[int, str]:
    """Which entries cannot combine, by index, with why (the rest combine).

    The first entry sets the effect and the resistance (the same ladder kind and trait
    as its save); one that differs is left out. Of the rest, an entry whose rank is more
    than the rules' spread below the strongest is left out too (a rank of 0 is unknown
    and waives the test, as the NPC-group window's does).
    """

    if not options:
        return {}
    first = options[0].save
    kind = first.ladder.kind if first.ladder is not None else ""
    out: dict[int, str] = {}
    for index, option in enumerate(options[1:], start=1):
        save = option.save
        other = save.ladder.kind if save.ladder is not None else ""
        if save.trait_key != first.trait_key or other != kind:
            out[index] = TEAM_NOT_SAME
    ranks = [o.rank for i, o in enumerate(options) if i not in out and o.rank]
    if ranks:
        spread = game_data.system.team_attack.rank_spread
        floor = max(ranks) - spread
        for index, option in enumerate(options):
            if index not in out and option.rank and option.rank < floor:
                out[index] = f"rank {option.rank}, more than {spread} below the strongest"
    return out
