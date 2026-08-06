"""The Equipment Point budget, an item's price, and turning a catalog entry into a build.

The second currency, kept strictly apart from the first. Power Points buy *ranks of
the Equipment advantage*; each rank grants
:attr:`~mm_companion.core.data_loader.EquipmentRules.points_per_advantage_rank`
Equipment Points (:func:`equipment_budget`), and those buy the items
(:func:`equipment_points_spent`). Neither total is ever a term in the other — an
item's price never reaches :func:`~.costs.power_points_spent`, and an advantage rank
never pays for gear.

The one rule that is easy to get silently wrong, so it is enforced in two places:
**the Removable discount is never applied to an equipment item.** The book prices gear
at what the same effects would cost *undiscounted*, precisely because the advantage
already handed out 5 points per rank; charging the flaw again would make every item
quietly cheap. :func:`build_item_from_entry` never attaches a removable-gated flaw,
and :func:`item_ep_cost` strips one before pricing a build that carries one anyway.

Runtime state — which items are worn, and what they contribute — lives in
:mod:`.runtime` beside the powers it shares its code with (:func:`~.runtime.worn_items`,
:func:`~.runtime.equipment_contributions`), and is re-exported here so the equipment
layer reads as one API.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..character import Character
from ..components import GATE_REMOVABLE
from ..data_loader import EquipmentEntry, GameData
from ..equipment import PER_RANK_COST_KINDS, EquipmentItem
from ..powers import ModifierSelection, Power, PowerEffectInstance
from .appliers import GROUP_EQUIPMENT, STACK_MAX, STACK_SUM
from .derived import trait_bonuses
from .powers_cost import power_total_cost
from .runtime import build_contributions, equipment_contributions, worn_items

__all__ = [
    "SupersededItemBonus",
    "build_item_from_entry",
    "equipment_advantage_rank",
    "equipment_budget",
    "equipment_contributions",
    "equipment_points_remaining",
    "equipment_points_spent",
    "equipment_violations",
    "item_ep_cost",
    "item_is_stock",
    "item_rank",
    "item_superseded",
    "worn_items",
]


# --- the budget ------------------------------------------------------------------------


def equipment_advantage_rank(char: Character, game_data: GameData) -> int:
    """Total ranks of the Equipment advantage the character has bought.

    *Which* advantage that is comes from the data
    (:attr:`~mm_companion.core.data_loader.EquipmentRules.advantage`, an id), matched to
    the character's selections by the advantage record's name — so a ruleset that
    renames or replaces it needs no code change. Ranks of several selections of the
    same advantage add up.
    """

    advantage = next(
        (a for a in game_data.advantages if a.id == game_data.equipment_rules.advantage), None
    )
    if advantage is None:
        return 0
    return sum(s.rank for s in char.advantages if s.name == advantage.name)


def equipment_budget(char: Character, game_data: GameData) -> int:
    """Equipment Points available: advantage rank × the ruleset's points per rank."""

    return equipment_advantage_rank(char, game_data) * (
        game_data.equipment_rules.points_per_advantage_rank
    )


# --- building an item from the catalog --------------------------------------------------


def _resistance_config_key(base) -> str:
    """The effect config field that sets its resistance, or ``""``.

    Found by the field's own ``overrides`` declaration rather than by name, so a
    catalog entry naming a resistance lands wherever *that* effect keeps it.
    """

    return next((f.key for f in base.config_fields if f.overrides == "resistance"), "")


def _ability_toggle_field(base, game_data: GameData):
    """The effect's checkbox that folds an ability in (Damage's Strength-Based), or ``None``.

    Data-driven on both sides: the field must ``toggles`` a modifier, and that modifier
    must be one that ``adds_ability``. Nothing here names Strength or Damage, so an
    effect a mod ships with the same shape is handled identically.
    """

    catalog = game_data.modifier_catalog()
    for cfg in base.config_fields:
        if not cfg.toggles:
            continue
        modifier = catalog.get(cfg.toggles)
        if modifier is not None and modifier.adds_ability:
            return cfg
    return None


def _entry_modifier_selections(
    entry: EquipmentEntry, game_data: GameData
) -> tuple[list[ModifierSelection], list[ModifierSelection]]:
    """An entry's printed extras and flaws, split by the modifier's own ``category``.

    A flaw carrying the ``removable`` gate is dropped: an item is *already* removable
    (that is what equipment is), and its price already reflects the discount once. See
    the module docstring — ``omni_equipment`` is the one catalog entry that legitimately
    prints the flaw, and it is bought with Power Points rather than Equipment Points.
    """

    catalog = game_data.modifier_catalog()
    extras: list[ModifierSelection] = []
    flaws: list[ModifierSelection] = []
    for ref in entry.modifiers:
        modifier = catalog.get(ref.modifier)
        if modifier is None or modifier.gate == GATE_REMOVABLE:
            continue
        selection = ModifierSelection(modifier_id=ref.modifier, rank=ref.rank or 1)
        (extras if modifier.category == "extra" else flaws).append(selection)
    return extras, flaws


def _build_effect(
    ref, entry: EquipmentEntry, game_data: GameData, rank: int
) -> PowerEffectInstance:
    """One catalog effect reference as a real :class:`PowerEffectInstance`."""

    base = next((e for e in game_data.effects if e.id == ref.effect), None)
    config = dict(ref.config)
    if ref.configuration:
        # A named preset ("snare", "stun"): no effect declares a field for it, but it is
        # the item's own description of what it does, so it is kept rather than dropped.
        config.setdefault("configuration", ref.configuration)

    extras, flaws = _entry_modifier_selections(entry, game_data)
    if base is not None:
        if ref.resistance:
            key = _resistance_config_key(base)
            if key:
                config[key] = ref.resistance
        # An Affliction's degree ladder: which config key holds each degree is declared
        # by the effect's own ``resistanceOutcomes``, so no degree key is named here.
        for outcome, degree in zip(base.resistance_outcomes, ref.degrees, strict=False):
            if outcome.config_key:
                config[outcome.config_key] = list(degree)
        if ref.strength_based:
            cfg = _ability_toggle_field(base, game_data)
            if cfg is not None:
                config[cfg.key] = True
                extras.insert(0, ModifierSelection(modifier_id=cfg.toggles))

    return PowerEffectInstance(
        effect_id=ref.effect,
        # A ref with no printed rank is one the player buys ranks of (Armored Costume's
        # Protection); ``rank`` is what they chose.
        rank=ref.rank if ref.rank is not None else max(1, rank),
        extras=extras,
        flaws=flaws,
        config=config,
        descriptors=list(ref.descriptors),
    )


def build_item_from_entry(
    entry: EquipmentEntry, game_data: GameData, *, rank: int = 1
) -> EquipmentItem:
    """Turn a catalog entry into an :class:`~mm_companion.core.equipment.EquipmentItem`.

    The entry's ``effects``/``modifiers`` become a real
    :class:`~mm_companion.core.powers.Power`, which is what makes every powers rules
    function work on gear unchanged. ``rank`` is the rank the player bought, used only
    by the refs whose rank the catalog leaves open (``rankIsPurchased``); a ref with a
    printed rank keeps it.

    An entry with no effects at all — the five accessories that only modify a host
    weapon — builds an empty power, which is correct: the item exists, has its printed
    price, and grants nothing on its own. Attaching it to a host is Phase 8's job.
    """

    effects = [_build_effect(ref, entry, game_data, rank) for ref in entry.effects]
    build = Power(name=entry.name, description=entry.description, effects=effects)
    return EquipmentItem(catalog_id=entry.id, build=build, category=entry.category)


# --- an item's price --------------------------------------------------------------------


def item_rank(item: EquipmentItem) -> int:
    """The rank an item was bought at — its build's first effect's, else 1."""

    return item.build.effects[0].rank if item.build.effects else 1


def _build_signature(power: Power) -> tuple:
    """A power's build reduced to what makes it cost what it costs.

    Ids are excluded (they are minted fresh every build) as are name, description and
    the runtime flags, so this compares two builds' *mechanics* and nothing else.
    """

    return (
        power.structure,
        power.cost_override,
        tuple(
            (
                effect.effect_id,
                effect.rank,
                tuple(sorted((m.modifier_id, m.rank) for m in effect.extras)),
                tuple(sorted((m.modifier_id, m.rank) for m in effect.flaws)),
                tuple(sorted((k, repr(v)) for k, v in effect.config.items())),
            )
            for effect in power.effects
        ),
    )


def item_is_stock(item: EquipmentItem, game_data: GameData) -> bool:
    """Whether the item's build still matches the catalog entry it came from.

    A stock item is priced at its printed cost; one the player has edited in the
    constructor is priced from its effects instead (:func:`item_ep_cost`). The
    comparison is against a *freshly built* entry at this item's own rank, so a
    ranked item is stock at every rank. A custom item (no ``catalog_id``, or an id no
    loaded ruleset defines) is never stock.
    """

    entry = game_data.equipment_catalog().get(item.catalog_id)
    if entry is None:
        return False
    stock = build_item_from_entry(entry, game_data, rank=item_rank(item)).build
    return _build_signature(stock) == _build_signature(item.build)


def _undiscounted(power: Power, game_data: GameData) -> Power:
    """The build with any removable-gated flaw stripped off, for pricing.

    Belt and braces to the rule in the module docstring: nothing this layer *builds*
    carries the flaw, but a hand-edited save, an imported power or a mod's entry could,
    and the failure mode is silent — every affected item would simply be cheap. A build
    carrying none is returned as-is.
    """

    catalog = game_data.modifier_catalog()

    def is_removable(selection: ModifierSelection) -> bool:
        modifier = catalog.get(selection.modifier_id)
        return modifier is not None and modifier.gate == GATE_REMOVABLE

    if not any(is_removable(f) for e in power.effects for f in e.flaws):
        return power

    stripped = []
    for effect in power.effects:
        clone = PowerEffectInstance.from_dict(effect.to_dict())
        clone.flaws = [f for f in effect.flaws if not is_removable(f)]
        stripped.append(clone)
    return Power(
        name=power.name,
        structure=power.structure,
        cost_override=power.cost_override,
        effects=stripped,
    )


def item_ep_cost(item: EquipmentItem, game_data: GameData, char: Character | None = None) -> int:
    """What one item costs in Equipment Points.

    Three answers, in order:

    1. :attr:`~mm_companion.core.equipment.EquipmentItem.ep_override` when set — the
       homerule seam, the twin of a power's ``cost_override``.
    2. The catalog's **printed** price, when the item is still stock
       (:func:`item_is_stock`) and the entry prints one. That is the book's own number,
       and for the ``per_rank``/``ranked`` kinds it is a price per rank, so it is
       multiplied by :func:`item_rank`.
    3. Otherwise the build's derived cost — the same
       :func:`~.powers_cost.power_total_cost` a power pays, minus any Removable
       discount, since an item's price is what its effects would cost *undiscounted*.
    """

    if item.ep_override is not None:
        return item.ep_override

    entry = game_data.equipment_catalog().get(item.catalog_id)
    if entry is not None and entry.cost is not None and item_is_stock(item, game_data):
        if entry.cost_kind in PER_RANK_COST_KINDS:
            return entry.cost * max(1, item_rank(item))
        return entry.cost

    return power_total_cost(_undiscounted(item.build, game_data), game_data, char)


def equipment_points_spent(char: Character, game_data: GameData) -> int:
    """Equipment Points the character's gear costs — every item, worn or not.

    Taking a jacket off does not refund it: ``worn`` is runtime state, the price is
    build state. Deliberately *not* a term in
    :func:`~.costs.power_points_spent`; the two currencies never mix.
    """

    return sum(item_ep_cost(item, game_data, char) for item in char.equipment)


def equipment_points_remaining(char: Character, game_data: GameData) -> int:
    """Unspent Equipment Points (budget minus spend; may go negative)."""

    return equipment_budget(char, game_data) - equipment_points_spent(char, game_data)


# --- what an item is not currently granting ---------------------------------------------


@dataclass(frozen=True)
class SupersededItemBonus:
    """A bonus one worn item grants that nothing on the sheet is reading.

    Equipment bonuses do not stack (``docs/mm-equipment-design.md`` §3), so a second
    suit of armour, or one outclassed by a power, sits on the sheet contributing
    nothing. A silently inert number reads as a bug, so the item's card says what beat
    it — ``stat`` and ``category`` name the trait, ``amount`` what this item offered,
    and ``beaten_by`` the source that won.
    """

    stat: str
    category: str
    amount: int
    beaten_by: str


def item_superseded(
    item: EquipmentItem, char: Character, game_data: GameData
) -> tuple[SupersededItemBonus, ...]:
    """Which of a worn item's bonuses lost to something better, and to what.

    The item's own contributions are gathered the same way the sheet gathers them
    (:func:`~.runtime.build_contributions`) and then looked up in the *resolved*
    sheet-wide bonuses: a contribution the resolver reported as
    :class:`~.appliers.SupersededBonus` is one this item is not granting. An item
    that is not worn contributes nothing at all and so supersedes nothing — its card
    is already dimmed, which is the honest explanation there.
    """

    if not item.worn:
        return ()

    own = build_contributions(
        item.build,
        char,
        game_data,
        stacking=STACK_SUM if item.stacks else STACK_MAX,
        group=GROUP_EQUIPMENT,
    )
    if not own:
        return ()

    resolved = trait_bonuses(char, game_data)
    beaten: list[SupersededItemBonus] = []
    for contribution in own:
        bonus = resolved.get(contribution.category, {}).get(contribution.stat)
        if bonus is None:
            continue
        for loser in bonus.superseded:
            if loser.source == contribution.source and loser.amount == contribution.amount:
                beaten.append(
                    SupersededItemBonus(
                        stat=contribution.stat,
                        category=contribution.category,
                        amount=contribution.amount,
                        beaten_by=loser.beaten_by,
                    )
                )
                break
    return tuple(beaten)


def equipment_violations(char: Character, game_data: GameData) -> list[str]:
    """Equipment budget breaches in the current build; an empty list means it is valid.

    Two of them, and both are warnings by default — whether they merely warn or block
    the save is the single seam
    :func:`mm_companion.core.storage.equipment_enforcement`, beside the Power Level
    one. A character with no gear at all is never in breach, whatever their advantage
    rank.
    """

    if not char.equipment:
        return []

    rules = game_data.equipment_rules
    unit = rules.currency_abbreviation
    spent = equipment_points_spent(char, game_data)
    budget = equipment_budget(char, game_data)
    if spent <= budget:
        return []

    rank = equipment_advantage_rank(char, game_data)
    if rank == 0:
        return [f"Equipment costing {spent} {unit} needs ranks of the Equipment advantage."]
    return [
        f"Equipment uses {spent} {unit}, exceeding the {budget} {unit} "
        f"granted by Equipment rank {rank}."
    ]
