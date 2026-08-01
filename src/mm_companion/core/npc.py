"""Building a mook from a handful of numbers.

Most NPCs a GM writes mid-session are not builds — they are four numbers and a
name. A bandit hits at +6, does damage 5, is Defence 6 and Toughness 4, and
nothing else about it will ever be looked at. Walking that through the full
character sheet and the Power Constructor is several minutes of work for a
creature that lives one fight.

So this assembles one directly: a :class:`~mm_companion.core.character.Character`
with those numbers written where the rules layer already reads them, carrying the
two powers almost every NPC needs — something that hurts (Damage) and something
that stops you (Affliction). It is a *starting point*, not a special kind of
character: what comes back is an ordinary character that opens in the ordinary NPC
sheet, where anything about it can be changed.

Pure Python, no Qt — the wizard that collects the numbers lives in
:mod:`mm_companion.ui.npc_quick_dialog`, and this is provable without a display.
"""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
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


def quick_npc(
    data: GameData,
    *,
    name: str,
    attack: int,
    effect: int,
    defence: int,
    toughness: int,
    image_path: str | None = None,
) -> Character:
    """A ready-to-play NPC from the five numbers that actually matter.

    *attack* is written to the ``ATK`` ability, which is where every effect that
    makes an attack reads its bonus from unless it names an attack skill of its own
    (:func:`mm_companion.core.rules.effect_attack_skill_bonus`) — so it *is* the
    per-power attack bonus, and both powers below inherit it. *effect* is the rank
    of both powers, which sets their save DCs.

    *defence* and *toughness* are bought ranks. A quick NPC has no abilities, so
    every resistance base is 0 and the numbers entered are the numbers the sheet
    shows; Dodge derives from Defence and follows on its own.

    Nothing here fixes a power level: an NPC's PL is *estimated* from what it can do
    (:func:`mm_companion.core.rules.estimated_power_level`), which is exactly these
    numbers, so it comes out right without being stated.
    """
    npc = Character.new_default(data)
    npc.profile["hero_name"] = name
    npc.image_path = image_path or None

    keys = data.system.trait_keys
    npc.abilities[keys.attack] = int(attack)
    npc.resistances[keys.defense] = int(defence)
    npc.resistances[keys.toughness] = int(toughness)

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
