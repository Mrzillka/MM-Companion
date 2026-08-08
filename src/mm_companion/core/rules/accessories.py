"""Accessories: gear fitted to other gear, and the build that results.

Below both :mod:`.equipment` (which prices things) and :mod:`.runtime` (which decides
what is currently live), because both need it and neither may import the other — pricing
reaches ``powers_cost`` and so ``derived``, and ``derived`` reads what runtime gathers.

An accessory is an item that names somewhere to attach (``implementation.attachesTo``)
rather than one filed under the ``accessory`` heading — that group is only where the
cards go, so a mod's accessory needs no category of its own.

The one rule to keep straight: :func:`item_effective_build` is **derived, never stored**.
The host keeps the build the catalog gave it, so it stays stock, keeps its printed price,
and detaching is lossless. Its price is likewise :func:`~.equipment.item_own_ep_cost` of
the *stored* build; the accessory's own price is added separately, or the same modifier
would be charged twice.
"""

from __future__ import annotations

from ..character import Character
from ..components import GATE_REMOVABLE
from ..data_loader import EquipmentEntry, GameData
from ..equipment import EquipmentItem
from ..powers import ModifierSelection, Power, PowerEffectInstance

__all__ = [
    "accessory_hosts",
    "attach_accessory",
    "detach_accessory",
    "entry_attaches_to",
    "item_accepts_accessory",
    "item_attaches_to",
    "item_attachment",
    "item_effective_build",
]


def _modifier_selections(
    refs, game_data: GameData
) -> tuple[list[ModifierSelection], list[ModifierSelection]]:
    """Printed modifier references, split into extras and flaws by their own ``category``.

    A flaw carrying the ``removable`` gate is dropped: an item is *already* removable
    (that is what equipment is), and its price already reflects the discount once. See
    the module docstring — ``omni_equipment`` is the one catalog entry that legitimately
    prints the flaw, and it is bought with Power Points rather than Equipment Points.
    """

    return _split_by_category(
        (ModifierSelection(modifier_id=ref.modifier, rank=ref.rank or 1) for ref in refs),
        game_data,
    )


def _split_by_category(
    selections, game_data: GameData
) -> tuple[list[ModifierSelection], list[ModifierSelection]]:
    """Modifier selections split into extras and flaws, dropping the removable gate.

    The one answer to "which pile does this go in", because there were two and they
    disagreed: an unknown category was a flaw when an entry was built and an extra when
    an accessory lent it. Unreachable against the shipped data — the loader tags every
    modifier from the array it was parsed out of — but a mod's is not, and being
    charged for a flaw is the harmless direction to be wrong in.

    Dropping the removable gate here is what makes the rule hold on *both* paths. It
    was enforced on the build path only, so an accessory whose ``attachment`` carried a
    removable flaw would have pushed one onto every effect of its host — costing
    nothing (the merged build is never priced) but reaching ``effect_is_active``, which
    would switch the host off whenever it was not "present".
    """

    catalog = game_data.modifier_catalog()
    extras: list[ModifierSelection] = []
    flaws: list[ModifierSelection] = []
    for selection in selections:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier.gate == GATE_REMOVABLE:
            continue
        (extras if modifier.category == "extra" else flaws).append(selection)
    return extras, flaws


def _entry_modifier_selections(
    entry: EquipmentEntry, game_data: GameData
) -> tuple[list[ModifierSelection], list[ModifierSelection]]:
    """The entry-wide extras and flaws every one of its effects carries."""

    return _modifier_selections(entry.modifiers, game_data)


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
    """The modifiers this accessory lends its host — its own, else its entry's printed ones.

    The fallback exists for gear saved before either field did (see
    :func:`item_attaches_to`), and it is reached only by an item carrying **neither** —
    which is what "saved before the fields existed" means. An item that names somewhere
    to attach has been through the constructor's accessory row, so an empty list there
    is the player saying "lends nothing" rather than "never asked", and reading the
    printed modifiers back over it would ignore a deliberate choice.
    """

    if item.attachment or item.attaches_to:
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

    An accessory carrying **effects** of its own brings those too, each labelled with
    the accessory's name. Catalog accessories have none — a laser sight is modifiers and
    nothing else — but a custom one may, and its effects are already folded into the
    host's price by :func:`item_ep_cost`, so leaving them out charged for something that
    appeared nowhere and did nothing.

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
    fitted = [
        (accessory, effect) for accessory in item.accessories for effect in accessory.build.effects
    ]
    if not fitted and (not lent or not item.build.effects):
        return item.build

    extras, flaws = _split_by_category(lent, game_data)

    def copies(selections):
        return [ModifierSelection.from_dict(s.to_dict()) for s in selections]

    effects = []
    for effect in item.build.effects:
        clone = PowerEffectInstance.from_dict(effect.to_dict())
        clone.extras = [*clone.extras, *copies(extras)]
        clone.flaws = [*clone.flaws, *copies(flaws)]
        effects.append(clone)
    # An accessory that carries effects of its own brings them along, labelled with the
    # accessory's name so the host's terms table and dice footer say *which* part of the
    # weapon is doing it. A catalog accessory has none — its whole contribution is the
    # modifiers above — but a custom one may, and until this it was paid for through the
    # host's price while being invisible and inert everywhere on the sheet.
    #
    # The lent modifiers are deliberately *not* applied to these: what an accessory
    # lends is lent to the host, and its own effects already carry their own modifiers.
    for accessory, effect in fitted:
        clone = PowerEffectInstance.from_dict(effect.to_dict())
        clone.label = clone.label or accessory.name
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
