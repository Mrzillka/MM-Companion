"""What a target's Toughness modifiers do to a Damage save: Hardened, Impervious and kin.

The damage ladder (:mod:`.damage`) says what failing a Toughness save does. Three
modifiers on the *target's* Toughness change that, and one on the *attack* changes them
back (p152, p154, p116):

- **Impervious** — the target is unaffected by a Damage rank no greater than its
  Impervious rank. **Impenetrable** is the same, and holds against Penetrating too.
- **Hardened, Impervious, Impenetrable** — two or more degrees of success on the save
  leave no damage conditions at all, not even the Hit a made save normally costs.
- **Hardened** — against a Damage rank no greater than its Hardened rank the target
  rolls twice and keeps the higher.
- **Penetrating** — against Impervious the target resists the attack's Penetrating
  rank rather than being immune, and two degrees of success still leave a Hit.

They used to be words in the damage ladder's note ("unless the target's Toughness is
Hardened…"), so a GM applying an NPC's failed save put a Hit on a creature the rules say
was never touched. Which modifiers play which part is data
(``system.json``'s ``damage_defenses``), so nothing here names one.

What a defence *covers* is the ranks of Toughness it is bought on: the ranks of each
active Toughness boost (a Protection, an Enhanced Toughness, a suit of armour) carrying
the modifier, or the part of them its rank band covers. A defence on several boosts
adds up, the way the boosts themselves do.

Hardened's second die is the one part the app can only *say*: dice are rolled by the
roller and, online, by the session server, neither of which reads what a save means —
so the save's label tells the roller to roll twice.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import GameData
from ..powers import Power, PowerEffectInstance
from .accessories import item_effective_build
from .powers_cost import selection_band
from .runtime import effect_is_active, live_powers, resolved_trait_allocation, worn_items

__all__ = [
    "ToughnessDefense",
    "toughness_defense",
    "penetrating_ranks",
    "damage_negated",
    "damage_dc_shift",
    "damage_defense_note",
]


@dataclass(frozen=True)
class ToughnessDefense:
    """The ranks of one creature's Toughness each kind of defence covers (0: none)."""

    #: Damage ranks up to this are rolled against twice, keeping the higher.
    roll_twice: int = 0
    #: Damage ranks up to this do nothing at all.
    immune: int = 0
    #: Of :attr:`immune`, the ranks that hold against Penetrating as well.
    immune_to_penetrating: int = 0
    #: Whether two degrees of success leave no conditions (any such defence at all).
    two_degrees: bool = False
    #: Whether that holds against Penetrating too.
    two_degrees_vs_penetrating: bool = False

    @property
    def any(self) -> bool:
        return bool(self.roll_twice or self.immune or self.two_degrees)


def _covered(selection, effect: PowerEffectInstance, boosted: int) -> int:
    """How many of an effect's *boosted* Toughness ranks one selection covers."""

    if not (selection.applies_from or selection.applies_to):
        return boosted
    lo, hi = selection_band(selection, effect.rank)
    return max(0, min(hi - lo + 1, boosted))


def _builds(char: Character, game_data: GameData) -> list[Power]:
    """Every build standing on the character: live powers, then worn gear."""

    builds = list(live_powers(char.powers))
    builds.extend(item_effective_build(item, game_data) for item in worn_items(char))
    return builds


def toughness_defense(char: Character | None, game_data: GameData) -> ToughnessDefense:
    """What *char*'s Toughness modifiers do against Damage, as ranks covered."""

    if char is None:
        return ToughnessDefense()
    rules = game_data.system.damage_defenses
    wanted = {*rules.roll_twice, *rules.two_degrees, *rules.immune}
    if not wanted:
        return ToughnessDefense()
    toughness = game_data.system.trait_keys.toughness
    covered: dict[str, int] = {}
    for build in _builds(char, game_data):
        for effect in build.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None or base.integration is None:
                continue
            if not effect_is_active(build, effect, base, game_data, char):
                continue
            boosted = sum(
                ranks
                for target, ranks in resolved_trait_allocation(effect, base, game_data, char)
                if target == toughness
            )
            if boosted <= 0:
                continue
            for selection in (*effect.extras, *effect.flaws):
                if selection.modifier_id in wanted:
                    covered[selection.modifier_id] = covered.get(
                        selection.modifier_id, 0
                    ) + _covered(selection, effect, boosted)

    def most(ids) -> int:
        return max((covered.get(mid, 0) for mid in ids), default=0)

    proof = [mid for mid in rules.immune if mid in rules.ignores_penetrating]
    return ToughnessDefense(
        roll_twice=most(rules.roll_twice),
        immune=most(rules.immune),
        immune_to_penetrating=most(proof),
        two_degrees=any(covered.get(mid) for mid in rules.two_degrees),
        two_degrees_vs_penetrating=any(
            covered.get(mid) for mid in rules.two_degrees if mid in rules.ignores_penetrating
        ),
    )


def penetrating_ranks(effect: PowerEffectInstance, game_data: GameData) -> int:
    """How many of an attacking effect's ranks are Penetrating (0 for none)."""

    rules = game_data.system.damage_defenses
    total = 0
    for selection in (*effect.extras, *effect.flaws):
        if selection.modifier_id in rules.penetrating:
            total += _covered(selection, effect, effect.rank)
    return min(total, effect.rank)


def damage_negated(
    defense: ToughnessDefense, *, rank: int, penetrating: int = 0, degree: int | None = None
) -> str:
    """Why a Damage save leaves this target untouched — ``""`` when it does not.

    *rank* is the attack's Damage rank (0: unknown, which waives the rank tests),
    *penetrating* how many of its ranks are Penetrating, and *degree* the save's signed
    degree (``None`` before it is rolled). The reason is words for the GM's notice.
    """

    if rank and rank <= defense.immune_to_penetrating:
        return f"Impenetrable {defense.immune_to_penetrating}"
    if rank and rank <= defense.immune and not penetrating:
        return f"Impervious {defense.immune}"
    if degree is not None and degree >= 2 and defense.two_degrees:
        if not penetrating or defense.two_degrees_vs_penetrating:
            return "two degrees of success against a hardened Toughness"
    return ""


def damage_dc_shift(defense: ToughnessDefense, *, rank: int, penetrating: int = 0) -> int:
    """How far the save's DC drops when Penetrating cuts through Impervious.

    Against an Impervious rank the attack would otherwise not exceed, the target
    "makes a normal resistance check against an effect rank equal to your Penetrating
    rank" (p154): the DC falls by the ranks that are not Penetrating.
    """

    if not (rank and penetrating) or rank > defense.immune:
        return 0
    if rank <= defense.immune_to_penetrating:
        return 0
    return max(0, rank - min(penetrating, rank))


def damage_defense_note(defense: ToughnessDefense, *, rank: int, penetrating: int = 0) -> str:
    """What the target's defences mean for this save, before it is rolled — or ``""``."""

    negated = damage_negated(defense, rank=rank, penetrating=penetrating)
    if negated:
        return f"unaffected: {negated}"
    notes = []
    shift = damage_dc_shift(defense, rank=rank, penetrating=penetrating)
    if shift:
        notes.append(f"Penetrating {penetrating} vs. Impervious: DC -{shift}")
    if rank and rank <= defense.roll_twice:
        notes.append(f"Hardened {defense.roll_twice}: roll twice, keep the higher")
    return "; ".join(notes)


def localized_damage_save(spec, char: Character | None, game_data: GameData):
    """*spec* with the target's defences folded in, for a damage-ladder save.

    The DC drops for Penetrating against Impervious, and the label says what the
    defences do — unaffected, roll twice — so whoever rolls it sees the rule rather
    than having to remember it.
    """

    ladder = getattr(spec, "ladder", None)
    if char is None or ladder is None or not ladder.rank:
        return spec
    if ladder.kind != _damage_ladder_kind():
        return spec
    defense = toughness_defense(char, game_data)
    if not defense.any:
        return spec
    note = damage_defense_note(defense, rank=ladder.rank, penetrating=ladder.penetrating)
    if not note:
        return spec
    shift = damage_dc_shift(defense, rank=ladder.rank, penetrating=ladder.penetrating)
    dc = spec.dc - shift if spec.dc is not None else None
    return replace(spec, dc=dc, label=f"{spec.label} ({note})")


def _damage_ladder_kind() -> str:
    # Imported late: ``rolls`` sits above this module and imports it.
    from .rolls import LADDER_DAMAGE

    return LADDER_DAMAGE
