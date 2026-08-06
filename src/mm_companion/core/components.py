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
:mod:`mm_companion.core.rules`. See ``docs/mm-powers-architecture.md`` §5-7. This module is
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
GATE_REQUIRES_EFFECT = "requires_effect"  # only while another named effect is active

# statIntegration.apply — what a :class:`TraitBoost` record *means* once its effect is
# active. Each kind is a registered applier in :mod:`mm_companion.core.rules.appliers`
# that turns the record and a rank into trait contributions; a mod registers another.
APPLY_BONUS = "bonus"  # add the amount to a numeric trait (the historical rule)
APPLY_PENALTY_REMOVED = "penalty_removed"  # cancel that much of a standing penalty
APPLY_PENALTY_REPLACED = "penalty_replaced"  # leave a smaller penalty in its place
APPLY_SPEED = "speed"  # grant ranks of a named movement mode
APPLY_SENSE = "sense"  # grant ranks of a named sense quality
APPLY_KINDS = (
    APPLY_BONUS,
    APPLY_PENALTY_REMOVED,
    APPLY_PENALTY_REPLACED,
    APPLY_SPEED,
    APPLY_SENSE,
)

# Condition mechanism tags — which engine subsystem a condition feeds
# (``docs/mm-conditions-design.md`` §4). Each names one place in the derived-stats /
# turn-resolution layer; the condition supplies data, the subsystem does the math.
# The resolver systems that read these live in :mod:`mm_companion.core.rules`.
MECH_ACTION_LIMIT = "action_limit"  # caps available actions
MECH_CHECK_PENALTY = "check_penalty"  # a flat penalty on checks (all or scoped)
MECH_DEFENSE_MOD = "defense_mod"  # alters Defense/Dodge
MECH_MOVEMENT_MOD = "movement_mod"  # alters movement speed rank
MECH_PERCEPTION_MOD = "perception_mod"  # awareness / auto-failed Perception
MECH_RESISTANCE_MOD = "resistance_mod"  # scoped resistance penalty
MECH_STACKING_PENALTY = "stacking_penalty"  # accumulates per instance (Hit)
MECH_RECURRING_SAVE = "recurring_save"  # a recovery check on a cadence (roll layer)
MECH_DEBILITATE_TRAIT = "debilitate_trait"  # removes a trait, may cascade
MECH_ATTACK_MOD = "attack_mod"  # attack checks by/against the character (Prone)
MECH_RANDOM_ACTION = "random_action"  # the turn's action is rolled (roll layer)
MECH_NARRATIVE = "narrative"  # no computed effect; a GM prompt/note
MECHANISMS = (
    MECH_ACTION_LIMIT,
    MECH_CHECK_PENALTY,
    MECH_DEFENSE_MOD,
    MECH_MOVEMENT_MOD,
    MECH_PERCEPTION_MOD,
    MECH_RESISTANCE_MOD,
    MECH_STACKING_PENALTY,
    MECH_RECURRING_SAVE,
    MECH_DEBILITATE_TRAIT,
    MECH_ATTACK_MOD,
    MECH_RANDOM_ACTION,
    MECH_NARRATIVE,
)


@dataclass(frozen=True)
class TraitBoost:
    """An effect's *stat effect* — what it does to a trait (Enhanced Trait, Protection).

    ``affects`` is the set of trait categories the effect can raise
    (``ability``/``resistance``/``defense``/``skill``/…); ``target`` is the fixed
    trait key for a baked-in booster like Protection (``"TOUGHNESS"``), left ``""``
    when ``configurable`` and the player picks the target at build time (stored on
    the instance's ``config['target']``).

    ``apply`` names which of :data:`APPLY_KINDS` interprets the record, and
    ``per_rank``/``flat`` say what it is worth at a given rank
    (``flat + rank × per_rank``). The defaults are M&M's own rule — one kind of
    bonus, worth exactly the effect's rank — so a record that states neither
    behaves as it always did.
    """

    affects: frozenset[str] = frozenset()
    target: str = ""
    configurable: bool = False
    apply: str = APPLY_BONUS
    per_rank: int = 1
    flat: int = 0


@dataclass(frozen=True)
class Integration:
    """The parsed ``statIntegration`` of a base effect.

    ``pattern`` is one of :data:`PATTERNS`; ``trait_boost`` is present only for the
    passive trait-boosting effects and ``None`` otherwise.
    """

    pattern: str = ""
    trait_boost: TraitBoost | None = None
