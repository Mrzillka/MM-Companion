"""Typed behaviour components for power effects — the "C" of the ECS split.

An assembled power's effects are the *entities*
(:class:`~mm_companion.core.powers.PowerEffectInstance`); the frozen records here
are the *components* describing how one effect behaves. They come from two sources:

* **Content components** parsed once per base
  :class:`~mm_companion.core.data_loader.Effect` from its ``statIntegration`` — the
  activation :data:`Integration.pattern` and, for the passive trait-boosting effects
  (Enhanced Trait, Protection), a :class:`TraitBoost`.
* **Gate kinds** derived per instance from the flaws attached to it — what can
  switch an otherwise-active effect off.

The *systems* that read these (``effect_is_active``, ``power_trait_bonuses``) live in
:mod:`mm_companion.core.rules`. See ``mm-powers-architecture.md`` §5-7. This module is
pure data and imports nothing from the app.
"""

from __future__ import annotations

from dataclasses import dataclass

# statIntegration.pattern — how an effect's contribution reaches the sheet (§5).
PASSIVE_PERMANENT = "passive_permanent"  # always on unless gated/suppressed
PASSIVE_TOGGLE = "passive_toggle"  # a Sustained/Continuous on-off effect
INSTANT_ACTION = "instant_action"  # used on demand, never a standing bonus
RESOURCE_POOL = "resource_pool"  # grants a sub-pool (Variable), not a stat patch
PATTERNS = (PASSIVE_PERMANENT, PASSIVE_TOGGLE, INSTANT_ACTION, RESOURCE_POOL)

# Gate kinds — what can switch an otherwise-active effect off (§7), tagged onto the
# gating flaws in ``modifiers.json``. The toggle gate is implied by a
# :data:`PASSIVE_TOGGLE` pattern rather than a modifier.
GATE_ACTIVATION = "activation"  # the whole power must be switched on first
GATE_REMOVABLE = "removable"  # only while the associated item is present
GATE_TOGGLE = "toggle"  # a Sustained/Continuous switch the player sets
GATE_LIMITED = "limited"  # a free-text condition the player self-applies (informational)


@dataclass(frozen=True)
class TraitBoost:
    """An effect adds its rank to a trait (Enhanced Trait, Protection).

    ``affects`` is the set of trait categories the effect can raise
    (``ability``/``resistance``/``defense``/``skill``/…); ``target`` is the fixed
    trait key for a baked-in booster like Protection (``"TOUGHNESS"``), left ``""``
    when ``configurable`` and the player picks the target at build time (stored on
    the instance's ``config['target']``).
    """

    affects: frozenset[str] = frozenset()
    target: str = ""
    configurable: bool = False


@dataclass(frozen=True)
class Integration:
    """The parsed ``statIntegration`` of a base effect.

    ``pattern`` is one of :data:`PATTERNS`; ``trait_boost`` is present only for the
    passive trait-boosting effects and ``None`` otherwise.
    """

    pattern: str = ""
    trait_boost: TraitBoost | None = None
