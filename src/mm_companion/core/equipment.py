"""The equipment model: a piece of gear as a real power, bought in a second currency.

Equipment is **not** a power (``docs/mm-equipment-design.md`` §1) — a character built
entirely from gear legitimately answers "no powers" — but mechanically an item *is*
a bundle of effects, so an :class:`EquipmentItem` **wraps** a
:class:`~mm_companion.core.powers.Power` in its :attr:`~EquipmentItem.build` rather
than re-describing one. Every function in the powers rules layer
(``power_total_cost``, ``effect_stat_rows``, ``power_rolls``, ``effect_is_active``)
takes ``item.build`` unchanged; that is the whole point of the shape.

The two currencies never mix. Power Points buy *ranks of the Equipment advantage*;
each rank grants :attr:`~mm_companion.core.data_loader.EquipmentRules.points_per_advantage_rank`
Equipment Points, and those buy the items. The cost engine is
:mod:`mm_companion.core.rules.equipment`; nothing here derives a number.

Plain data, JSON-serializable, no PySide6 — the same idiom as :mod:`.powers`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .powers import Power

# The ``costKind`` vocabulary an :class:`~mm_companion.core.data_loader.EquipmentEntry`
# declares (``equipment.json``'s ``_meta.costKindKey``). A closed vocabulary named in
# Python the way :data:`~mm_companion.core.powers.STRUCTURES` and the ``components``
# patterns are — the *items* are data, but what "per_rank" means to the cost engine is
# engine behaviour.
COST_FIXED = "fixed"  # a flat printed price
COST_RANKED = "ranked"  # priced over a small number of defined ranks
COST_PER_RANK = "per_rank"  # the printed price is per rank
COST_BUILT = "built"  # assembled from components; no single printed price
COST_KINDS = (COST_FIXED, COST_RANKED, COST_PER_RANK, COST_BUILT)

#: The kinds whose printed ``cost`` is a price *per rank* rather than a total.
PER_RANK_COST_KINDS = (COST_RANKED, COST_PER_RANK)


@dataclass
class EquipmentItem:
    """One piece of gear on a character sheet.

    ``catalog_id`` names the :class:`~mm_companion.core.data_loader.EquipmentEntry`
    it was picked from (``""`` for a wholly custom item), ``category`` the group the
    Equipment block files it under — copied off the entry at pick time so a card can
    be grouped without a catalog lookup, and so an item whose entry a disabled mod
    took away still has a home.

    ``worn`` is **runtime** state, exactly like a power's
    :attr:`~mm_companion.core.powers.Power.activated`: taking a jacket off is a
    play-time action, not a build edit, so it is deliberately left out of
    :meth:`to_dict` and a loaded character comes up wearing everything.

    ``stacks`` is build state and the one per-item homerule: equipment bonuses do not
    stack with each other or with powers (``docs/mm-equipment-design.md`` §3), and
    ticking this opts *this* item out of that rule, adding its bonus on top of the
    winner instead of competing with it. See
    :func:`~mm_companion.core.rules.appliers.resolve_contributions`.

    ``ep_override`` is the Equipment-Point twin of
    :attr:`~mm_companion.core.powers.Power.cost_override`: when set it *replaces* the
    item's derived price. ``None`` leaves the cost fully derived
    (:func:`~mm_companion.core.rules.equipment.item_ep_cost`).
    """

    catalog_id: str = ""
    build: Power = field(default_factory=Power)
    category: str = ""
    worn: bool = True
    stacks: bool = False
    ep_override: int | None = None
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def name(self) -> str:
        """The item's display name — its build's, which the catalog seeded."""

        return self.build.name

    def to_dict(self) -> dict:
        """Serialize the *build* state; runtime (:attr:`worn`) is left out on purpose."""

        data: dict = {
            "catalog_id": self.catalog_id,
            "build": self.build.to_dict(),
            "category": self.category,
            "id": self.id,
        }
        if self.stacks:
            data["stacks"] = True
        if self.ep_override is not None:
            data["ep_override"] = self.ep_override
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> EquipmentItem:
        override = raw.get("ep_override")
        return cls(
            catalog_id=raw.get("catalog_id", ""),
            build=Power.from_dict(raw.get("build", {})),
            category=raw.get("category", ""),
            stacks=bool(raw.get("stacks", False)),
            ep_override=None if override is None else int(override),
            id=raw.get("id") or uuid4().hex,
        )
