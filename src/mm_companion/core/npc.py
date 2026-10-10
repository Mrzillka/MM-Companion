"""Building a mook from a handful of numbers.

Most NPCs a GM writes mid-session are not builds — they are a handful of numbers
and a name. A bandit hits at +6, does damage 5, is Defence 6 and Toughness 4, and
nothing else about it will ever be looked at. Walking that through the full
character sheet and the Power Constructor is several minutes of work for a
creature that lives one fight.

So this assembles one directly: a :class:`~mm_companion.core.character.Character`
with those numbers written where the rules layer already reads them, carrying the
two powers almost every NPC needs — something that hurts (Damage) and something
that stops you (Affliction). It is a *starting point*, not a special kind of
character: what comes back is an ordinary character that opens in the ordinary NPC
sheet, where anything about it can be changed.

The numbers themselves usually start from a **preset** (:func:`preset_stats`): a
Power Level and a shape — a Brute, a Speedster, an even split — and every pair of
stats that shares a Power Level cap is put at that cap in the proportions the preset
names. The presets, the shares and which stats pair up are all ``system.json``'s
``quick_npc`` block; the totals are the ``costs.json`` caps the validator already
measures against. Nothing about a preset is spelled here.

Pure Python, no Qt — the wizard that collects the numbers lives in
:mod:`mm_companion.ui.npc_quick_dialog`, and this is provable without a display.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, fields

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, QuickNPCPreset
from mm_companion.core.powers import Power, PowerEffectInstance

#: The effect a quick NPC's damaging power is built from.
DAMAGE_EFFECT = "damage"

#: And its debilitating one.
AFFLICTION_EFFECT = "affliction"

#: What the Affliction does at each degree of failure. A conventional
#: dazed → stunned → incapacitated ladder, which is what most published mooks use;
#: the ids are the ones ``conditions.json`` defines, and the GM re-picks them in the
#: Power Constructor if this creature does something else.
AFFLICTION_DEGREES = {
    "resistance": "Fortitude",
    "degree1": ["dazed"],
    "degree2": ["stunned"],
    "degree3": ["incapacitated"],
}


@dataclass(frozen=True)
class QuickStats:
    """The six numbers a quick NPC is written from (see :func:`quick_npc`).

    The field names are the stat vocabulary of ``system.json``'s ``quick_npc`` block
    (:data:`~mm_companion.core.data_loader.QUICK_NPC_STATS`).
    """

    attack: int
    effect: int
    defence: int
    toughness: int
    fortitude: int
    will: int


def find_preset(data: GameData, preset_id: str) -> QuickNPCPreset | None:
    """The quick-NPC preset called *preset_id*, or ``None``."""
    return next((p for p in data.system.quick_npc.presets if p.id == preset_id), None)


def pair_total(data: GameData, cap: str, power_level: int) -> int:
    """What the Power Level cap *cap* allows two stats between them at *power_level*.

    ``0`` for a cap ``costs.json`` does not define, so an unknown name in a mod's
    ``quick_npc`` block fills nothing rather than raising.
    """
    rule = data.costs.power_level.caps.get(cap)
    return max(0, rule.limit(int(power_level))) if rule is not None else 0


def share_bounds(data: GameData, total: int) -> tuple[int, int]:
    """The ``(low, high)`` split of *total*: the larger share rounded up, and its rest."""
    numerator, denominator = data.system.quick_npc.high_share
    high = math.ceil(total * numerator / denominator)
    return min(total - high, high), max(total - high, high)


def _share_value(share: str, total: int, bounds: tuple[int, int], rng: random.Random) -> int:
    low, high = bounds
    if share == "high":
        return high
    if share == "low":
        return low
    if share == "random":
        return rng.randint(low, high)
    return total // 2


def preset_stats(
    data: GameData,
    preset_id: str,
    power_level: int,
    rng: random.Random | None = None,
) -> QuickStats:
    """The six numbers preset *preset_id* gives a creature of *power_level*.

    Every pair in ``quick_npc.pairs`` is measured against its Power Level cap. A
    **fill** pair always lands on that cap exactly — the first stat takes its share and
    the second the remainder, which is what makes a PL 10 Brute effect 14 and attack
    +6 rather than 13 and 6. A pair that does not fill (Fortitude and Will) sets each
    stat on its own share, and a random second stat is held to what the first left
    under the cap. A stat no pair names is 0; an unknown preset reads as all-even.

    *rng* is for tests; a fresh generator otherwise.
    """
    rng = rng or random.Random()
    preset = find_preset(data, preset_id) or QuickNPCPreset(id=preset_id, name=preset_id)
    values = {f.name: 0 for f in fields(QuickStats)}
    for pair in data.system.quick_npc.pairs:
        total = pair_total(data, pair.cap, power_level)
        bounds = share_bounds(data, total)
        first, second = pair.stats
        values[first] = _share_value(preset.share(first), total, bounds, rng)
        if pair.fill:
            values[second] = total - values[first]
            continue
        value = _share_value(preset.share(second), total, bounds, rng)
        values[second] = max(0, min(value, total - values[first]))
    return QuickStats(**values)


def quick_npc(
    data: GameData,
    *,
    name: str,
    attack: int,
    effect: int,
    defence: int,
    toughness: int,
    fortitude: int = 0,
    will: int = 0,
    power_level: int | None = None,
    image_path: str | None = None,
) -> Character:
    """A ready-to-play NPC from the six numbers that actually matter.

    *attack* is written to the ``ATK`` ability, which is where every effect that
    makes an attack reads its bonus from unless it names an attack skill of its own
    (:func:`mm_companion.core.rules.effect_attack_skill_bonus`) — so it *is* the
    per-power attack bonus, and both powers below inherit it. *effect* is the rank
    of both powers, which sets their save DCs.

    *defence*, *toughness*, *fortitude* and *will* are bought ranks. A quick NPC has
    no abilities, so every resistance base is 0 and the numbers entered are the
    numbers the sheet shows; Dodge derives from Defence and follows on its own.

    *power_level* is recorded on the character when given, but nothing reads it as a
    rule: an NPC's PL is *estimated* from what it can do
    (:func:`mm_companion.core.rules.estimated_power_level`), which is exactly these
    numbers, so it comes out right without being stated.
    """
    npc = Character.new_default(data)
    npc.profile["hero_name"] = name
    npc.image_path = image_path or None
    if power_level is not None:
        # Both: the model keeps the level in two places, and a load reconciles them
        # (Character.from_dict), so setting one would only hold until the file is read.
        npc.power_level = int(power_level)
        npc.characteristics["power_level"] = int(power_level)

    keys = data.system.trait_keys
    npc.abilities[keys.attack] = int(attack)
    npc.resistances[keys.defense] = int(defence)
    npc.resistances[keys.toughness] = int(toughness)
    npc.resistances[keys.fortitude] = int(fortitude)
    npc.resistances[keys.will] = int(will)

    rank = max(0, int(effect))
    npc.powers = [
        Power(name="Damage", effects=[PowerEffectInstance(DAMAGE_EFFECT, rank=rank)]),
        Power(
            name="Affliction",
            effects=[
                PowerEffectInstance(
                    AFFLICTION_EFFECT,
                    rank=rank,
                    config={
                        key: list(value) if isinstance(value, list) else value
                        for key, value in AFFLICTION_DEGREES.items()
                    },
                )
            ],
        ),
    ]
    return npc
