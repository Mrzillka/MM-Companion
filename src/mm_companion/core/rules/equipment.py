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
from .derived import effective_ability, trait_bonuses
from .powers_cost import power_total_cost
from .runtime import build_contributions, equipment_contributions, worn_items

__all__ = [
    "GrantedAdvantage",
    "SupersededItemBonus",
    "accessory_hosts",
    "attach_accessory",
    "build_item_from_entry",
    "detach_accessory",
    "entry_attaches_to",
    "equipment_advantage_rank",
    "equipment_budget",
    "equipment_contributions",
    "equipment_points_remaining",
    "equipment_points_spent",
    "equipment_violations",
    "item_accepts_accessory",
    "item_attaches_to",
    "item_attachment",
    "item_breakage_warnings",
    "item_effective_build",
    "item_ep_cost",
    "item_granted_advantages",
    "item_is_stock",
    "item_material_toughness",
    "item_own_ep_cost",
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
    ref,
    game_data: GameData,
    rank: int,
    modifiers: tuple[list[ModifierSelection], list[ModifierSelection]],
) -> PowerEffectInstance:
    """One catalog effect reference as a real :class:`PowerEffectInstance`.

    ``modifiers`` is the entry's own ``(extras, flaws)``, computed once by the caller
    and copied here — an accessory's are withheld entirely (they belong to its host,
    see :func:`item_effective_build`), and a Strength-Based ref prepends to its own
    copy rather than to the list every other effect is sharing.
    """

    base = next((e for e in game_data.effects if e.id == ref.effect), None)
    config = dict(ref.config)
    if ref.configuration:
        # A named preset ("snare", "stun"): no effect declares a field for it, but it is
        # the item's own description of what it does, so it is kept rather than dropped.
        config.setdefault("configuration", ref.configuration)

    extras, flaws = list(modifiers[0]), list(modifiers[1])
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

    An **accessory** — an entry declaring ``implementation.attachesTo`` — is built
    differently in one respect: its printed modifiers become the item's
    :attr:`~mm_companion.core.equipment.EquipmentItem.attachment` rather than being
    folded into effects of its own. That is the only place they can live and mean
    anything: a laser sight has no effect for its Accurate to modify until it is
    fitted to a rifle, and until then the item is a real, priced, effect-less thing.
    """

    attaches_to = entry_attaches_to(entry)
    modifiers = _entry_modifier_selections(entry, game_data)
    effects = [
        _build_effect(ref, game_data, rank, ((), ()) if attaches_to else modifiers)
        for ref in entry.effects
    ]
    build = Power(name=entry.name, description=entry.description, effects=effects)
    return EquipmentItem(
        catalog_id=entry.id,
        build=build,
        category=entry.category,
        attaches_to=attaches_to,
        attachment=[*modifiers[0], *modifiers[1]] if attaches_to else [],
    )


# --- accessories: items fitted to other items -------------------------------------------


def entry_attaches_to(entry: EquipmentEntry) -> tuple[str, ...]:
    """The host categories a catalog entry may be fitted to, from ``implementation.attachesTo``.

    Written either as one id or as a list of them, so both are accepted; ``()`` for
    everything that is not an accessory. Being *an accessory at all* is this field
    being non-empty rather than the entry's ``category`` — the shipped ``accessory``
    group is where the cards are filed, which is presentation, and a mod is free to
    attach something filed elsewhere.
    """

    hosts = entry.implementation.get("attachesTo")
    if not hosts:
        return ()
    if isinstance(hosts, str):
        return (hosts,)
    return tuple(str(h) for h in hosts)


def item_attaches_to(item: EquipmentItem, game_data: GameData) -> tuple[str, ...]:
    """The host categories this *item* fits — its own, else its catalog entry's.

    The item's own copy is authoritative (it is what survives a mod being disabled,
    the same bargain ``category`` strikes), and the entry is the fallback for gear
    saved before accessories existed, which carries neither field.
    """

    if item.attaches_to:
        return item.attaches_to
    entry = game_data.equipment_catalog().get(item.catalog_id)
    return entry_attaches_to(entry) if entry is not None else ()


def item_attachment(item: EquipmentItem, game_data: GameData) -> list[ModifierSelection]:
    """The modifiers this accessory lends its host — its own, else its entry's printed
    ones (see :func:`item_attaches_to` for why there is a fallback at all)."""

    if item.attachment:
        return list(item.attachment)
    entry = game_data.equipment_catalog().get(item.catalog_id)
    if entry is None or not entry_attaches_to(entry):
        return []
    extras, flaws = _entry_modifier_selections(entry, game_data)
    return [*extras, *flaws]


def item_accepts_accessory(
    host: EquipmentItem, accessory: EquipmentItem, game_data: GameData
) -> bool:
    """Whether ``accessory`` may be fitted to ``host``.

    Being an accessory *at all* is naming somewhere to attach (:func:`item_attaches_to`)
    rather than sitting in the ``accessory`` group, which is only where the cards are
    filed — so a mod may attach something filed anywhere. Those names are host
    *categories* (a laser sight goes on a ranged weapon), checked against the host's own
    ``category``, which is stored on the item rather than looked up so gear from a mod
    that has since been disabled still answers. An accessory is never fitted to another
    accessory: that is a chain with no weapon on the end of it.
    """

    hosts = item_attaches_to(accessory, game_data)
    if not hosts or host is accessory or item_attaches_to(host, game_data):
        return False
    return host.category in hosts


def accessory_hosts(
    char: Character, accessory: EquipmentItem, game_data: GameData
) -> list[EquipmentItem]:
    """The character's items this accessory could be fitted to, in sheet order."""

    return [item for item in char.equipment if item_accepts_accessory(item, accessory, game_data)]


def attach_accessory(
    char: Character, host: EquipmentItem, accessory: EquipmentItem, game_data: GameData
) -> bool:
    """Fit ``accessory`` to ``host``, taking it out of the character's loose gear.

    An attached accessory lives on its host and *only* there — that is what keeps its
    scope honest (the targeting scope's Improved Aim belongs to that weapon) and what
    stops :func:`equipment_points_spent` counting it twice, since the host's price now
    folds it in. Returns False, changing nothing, when the two do not fit.
    """

    if not item_accepts_accessory(host, accessory, game_data):
        return False
    if accessory in char.equipment:
        char.equipment.remove(accessory)
    if accessory not in host.accessories:
        host.accessories.append(accessory)
    return True


def detach_accessory(char: Character, host: EquipmentItem, accessory: EquipmentItem) -> bool:
    """Take ``accessory`` off ``host`` and put it back among the loose gear.

    Lossless, because attaching never rewrote either build: the host goes back to being
    exactly the catalog's item and the accessory back to its own card at its own price.
    It returns to the end of the list, where a newly added item lands. False when it
    was not fitted there.
    """

    if accessory not in host.accessories:
        return False
    host.accessories.remove(accessory)
    if accessory not in char.equipment:
        char.equipment.append(accessory)
    return True


def item_effective_build(item: EquipmentItem, game_data: GameData) -> Power:
    """The item's build **as it is actually used**: its own, plus what is fitted to it.

    Every fitted accessory's
    :attr:`~mm_companion.core.equipment.EquipmentItem.attachment` modifiers are added
    to each of the host's effects, so a rifle with a laser sight rolls its attack at +2
    exactly the way a Damage power with Accurate does — the terms table, the dice
    footer and the pinned readouts all take this build and need no idea that
    accessories exist.

    Two things it deliberately is **not**. It is not stored: the item keeps the build
    the catalog gave it, so it stays :func:`item_is_stock` and detaching is lossless.
    And it is never priced — an accessory's cost is its own printed price, added to the
    host's by :func:`item_ep_cost`; deriving a price from this build instead would
    charge for the same modifier twice.

    The host's own build is returned unchanged when nothing is fitted, which is the
    usual case and keeps the object identity every caller already had.
    """

    lent = [
        selection
        for accessory in item.accessories
        for selection in item_attachment(accessory, game_data)
    ]
    if not lent or not item.build.effects:
        return item.build

    catalog = game_data.modifier_catalog()
    extras, flaws = [], []
    for selection in lent:
        modifier = catalog.get(selection.modifier_id)
        (flaws if modifier is not None and modifier.category == "flaw" else extras).append(
            selection
        )

    def copies(selections):
        return [ModifierSelection.from_dict(s.to_dict()) for s in selections]

    effects = []
    for effect in item.build.effects:
        clone = PowerEffectInstance.from_dict(effect.to_dict())
        clone.extras = [*clone.extras, *copies(extras)]
        clone.flaws = [*clone.flaws, *copies(flaws)]
        effects.append(clone)
    return Power(
        name=item.build.name,
        description=item.build.description,
        structure=item.build.structure,
        cost_override=item.build.cost_override,
        effects=effects,
        activated=item.build.activated,
        item_present=item.build.item_present,
    )


@dataclass(frozen=True)
class GrantedAdvantage:
    """An advantage an item hands its wielder, and which item hands it over.

    Equipment-granted advantages are a trait of the *thing*, not of the character
    (``docs/design-data/equipment-design.json``, ``_meta.weaponTraits.advantageScope``):
    an axe's Improved Smash applies while swinging the axe and nowhere else, which is
    exactly why they are reported per item and never merged into the character's own
    advantage list. ``source`` names the accessory when one is what granted it, so a
    scope's Improved Aim reads as the scope's.
    """

    name: str
    source: str


def item_granted_advantages(
    item: EquipmentItem, game_data: GameData
) -> tuple[GrantedAdvantage, ...]:
    """Every advantage this item and its fitted accessories grant, scoped to the item.

    Read off the catalog entry's ``grants`` and resolved to the advantage's display
    name — falling back to the id, so a mod's entry naming something this ruleset
    dropped still says *something* rather than vanishing. A wholly custom item grants
    nothing; there is no entry to read.
    """

    catalog = game_data.equipment_catalog()
    names = {a.id: a.name for a in game_data.advantages}
    granted: list[GrantedAdvantage] = []
    for owner in (item, *item.accessories):
        entry = catalog.get(owner.catalog_id)
        if entry is None:
            continue
        for advantage_id in entry.grants.get("advantages", ()):
            granted.append(GrantedAdvantage(names.get(advantage_id, advantage_id), owner.name))
    return tuple(granted)


# --- breakage: more Strength than the thing can carry -----------------------------------


def item_material_toughness(item: EquipmentItem, game_data: GameData) -> int | None:
    """How much Toughness this item has, from the material its catalog entry names.

    ``None`` when the entry names no material, when the ruleset's table does not list
    it, or when there is no entry at all — all of which mean the same thing to the
    caller: nothing to warn about. Nothing here knows what a sword is made of; the
    material is on the entry and the table is in ``_meta.strengthBasedDamage``.
    """

    entry = game_data.equipment_catalog().get(item.catalog_id)
    if entry is None:
        return None
    material = entry.implementation.get("material")
    if not isinstance(material, str):
        return None
    return game_data.equipment_rules.material_toughness.get(material)


def item_breakage_warnings(item: EquipmentItem, char: Character, game_data: GameData) -> list[str]:
    """One line per ability this item cannot carry: "Strength 10 exceeds ... Toughness 7".

    A Strength-Based weapon adds the wielder's Strength to its Damage, but an ordinary
    weapon can only carry Strength up to its own Toughness; past that it **breaks when
    used** (``docs/mm-equipment-design.md`` §4). That is a real event at the table
    rather than a build error, so this warns and never clamps — the bonus stays exactly
    what :func:`~.powers_cost.effect_effective_rank` says it is, and the sheet says what
    is going to happen.

    The ability is whichever one a modifier on the effect folds in (``adds_ability``),
    and it is the ability *exerted* — the wielder's own, before the §4 divisor cuts how
    much of it reaches the Damage rank. Halving what arrives does not halve what is
    being put through the haft.
    """

    toughness = item_material_toughness(item, game_data)
    if toughness is None:
        return []

    catalog = game_data.modifier_catalog()
    abilities = {a.key: a.name for a in game_data.abilities}
    warnings: list[str] = []
    seen: set[str] = set()
    for effect in item.build.effects:
        for selection in (*effect.extras, *effect.flaws):
            modifier = catalog.get(selection.modifier_id)
            if not (modifier and modifier.adds_ability) or modifier.adds_ability in seen:
                continue
            seen.add(modifier.adds_ability)
            exerted = effective_ability(char, game_data, modifier.adds_ability)
            if exerted > toughness:
                label = abilities.get(modifier.adds_ability, modifier.adds_ability)
                warnings.append(
                    f"{label} {exerted} exceeds this {item.name or 'item'}'s "
                    f"Toughness {toughness} — it will break on use."
                )
    return warnings


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


def item_own_ep_cost(
    item: EquipmentItem, game_data: GameData, char: Character | None = None
) -> int:
    """What the item itself costs in Equipment Points, ignoring anything fitted to it.

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

    Note this prices ``item.build`` and never
    :func:`item_effective_build`: an accessory's modifiers are already paid for by the
    accessory's own printed price, and pricing the merged build would charge for them a
    second time.
    """

    if item.ep_override is not None:
        return item.ep_override

    entry = game_data.equipment_catalog().get(item.catalog_id)
    if entry is not None and entry.cost is not None and item_is_stock(item, game_data):
        if entry.cost_kind in PER_RANK_COST_KINDS:
            return entry.cost * max(1, item_rank(item))
        return entry.cost

    return power_total_cost(_undiscounted(item.build, game_data), game_data, char)


def item_ep_cost(item: EquipmentItem, game_data: GameData, char: Character | None = None) -> int:
    """What one item costs in Equipment Points **with everything fitted to it**.

    An accessory's price folds into its host's (``docs/mm-equipment-design.md`` §2
    pattern I), which is also the only way the budget can count it: an attached
    accessory is off the character's loose gear list, so
    :func:`equipment_points_spent` would otherwise walk straight past it. Use
    :func:`item_own_ep_cost` for the item's own line.
    """

    return item_own_ep_cost(item, game_data, char) + sum(
        item_ep_cost(accessory, game_data, char) for accessory in item.accessories
    )


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
