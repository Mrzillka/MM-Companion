"""Building a :class:`~mm_companion.core.powers.Power` from a standard configuration.

The rulebook names about ninety ready-made powers — *Blast*, *Dazzle*, *Force Field*,
*Invisibility* — and lists the effects, extras and flaws each is made of. They are
shorthand rather than new rules, so a configuration is turned into an ordinary
:class:`Power`: the player gets a real, editable build rather than a fixed catalog
entry, and it costs whatever the pieces cost.

Nothing here decides a price. What a built power is worth comes from
:func:`~.powers_cost.power_total_cost` reading the effects this module produced, exactly
as if the player had dragged each brick across themselves. A configuration's printed
``cost_note`` is reference text beside that number, never a substitute for it.
"""

from __future__ import annotations

from ..data_loader import ConfiguredEffect, ConfiguredModifier, GameData, PowerConfiguration
from ..powers import ModifierSelection, Power, PowerEffectInstance

__all__ = [
    "configuration_by_id",
    "configurations_for_effect",
    "effect_from_configuration",
    "power_from_configuration",
]


def _selection(configured: ConfiguredModifier) -> ModifierSelection:
    """One stored modifier selection from its configured description."""

    selection = ModifierSelection(modifier_id=configured.id)
    if configured.rank:
        selection.rank = configured.rank
    selection.config.update(configured.config)
    return selection


def effect_from_configuration(configured: ConfiguredEffect) -> PowerEffectInstance:
    """One live effect instance from a configuration's description of it.

    A ``rank`` of ``0`` means the configuration does not fix one, so the instance keeps
    its own default and the player sets it — which is the usual case, since most
    configurations are priced per rank. The few that *are* a fixed size (Invisibility is
    Concealment 2, True Sight is Enhanced Senses 7) name their rank and get it.

    The dicts are **copied**, not shared: a configuration is a frozen catalog record that
    may be built many times, and two powers editing one dict would drift into each other.
    """

    instance = PowerEffectInstance(effect_id=configured.effect_id)
    if configured.rank:
        instance.rank = configured.rank
    instance.config.update(_deepcopy_config(configured.config))
    instance.extras.extend(_selection(m) for m in configured.extras)
    instance.flaws.extend(_selection(m) for m in configured.flaws)
    return instance


def _deepcopy_config(config: dict) -> dict:
    """A config dict copied deeply enough that no list or row is shared.

    Config values are JSON shapes — scalars, lists of scalars, and lists of row dicts —
    so one level of recursion covers every case the data can hold.
    """

    copied: dict = {}
    for key, value in config.items():
        if isinstance(value, list):
            copied[key] = [dict(v) if isinstance(v, dict) else v for v in value]
        else:
            copied[key] = value
    return copied


def power_from_configuration(configuration: PowerConfiguration) -> Power:
    """A fresh, fully editable :class:`Power` built from a standard configuration.

    The power takes the configuration's name and description, so a dropped *Blast* is
    already called Blast, and its structure, which only matters for the handful of
    multi-effect configurations (Berserker Rage, Poltergeist, Absorption, Power Theft).

    The result is an ordinary power in every respect — it carries no back-reference to
    the configuration it came from, because the moment the player edits a rank it is no
    longer that configuration and a stale label would be worse than none.
    """

    return Power(
        name=configuration.name,
        description=configuration.description,
        structure=configuration.structure,
        effects=[effect_from_configuration(e) for e in configuration.effects],
    )


def configuration_by_id(game_data: GameData, configuration_id: str) -> PowerConfiguration | None:
    """The configuration with this id, or ``None`` if the ruleset ships no such record."""

    return next((c for c in game_data.configurations if c.id == configuration_id), None)


def configurations_for_effect(game_data: GameData, effect_id: str) -> list[PowerConfiguration]:
    """Every configuration filed under one base effect, in catalog order.

    What the palette groups by: an Affliction has eleven named configurations and a
    player looking for Stun is looking under Affliction.
    """

    return [c for c in game_data.configurations if c.base_effect == effect_id]
