"""The generic mechanics registry and the readout-kind vocabulary built on it."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.components import (
    GATE_ACTIVATION,
    GATE_LIMITED,
    GATE_REMOVABLE,
    GATE_TOGGLE,
    INSTANT_ACTION,
    MECH_CHECK_PENALTY,
    MECH_DEBILITATE_TRAIT,
    MECH_DEFENSE_MOD,
    PASSIVE_PERMANENT,
    PASSIVE_TOGGLE,
    RESOURCE_POOL,
)
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.registry import Registry
from mm_companion.core.rules.conditions import (
    MECHANISM_SCOPES,
    ScopeContribution,
    apply_condition,
    condition_scope_penalty,
)
from mm_companion.core.rules.powers_terms import (
    CONFIG_DISPLAY_KINDS,
    READOUT_KINDS,
    EffectStat,
    _config_display,
)
from mm_companion.core.rules.runtime import (
    GATE_KINDS,
    PATTERN_BEHAVIOURS,
    PatternBehaviour,
    effect_is_active,
)


def test_register_and_get() -> None:
    reg: Registry[str] = Registry("demo")
    reg.register("a", "alpha")
    assert reg.get("a") == "alpha"
    assert "a" in reg
    assert reg.get("missing") is None
    assert len(reg) == 1
    assert reg.keys() == ("a",)


def test_duplicate_key_raises_unless_replace() -> None:
    reg: Registry[str] = Registry("demo")
    reg.register("a", "alpha")
    with pytest.raises(KeyError):
        reg.register("a", "beta")
    reg.register("a", "beta", replace=True)
    assert reg.get("a") == "beta"


def test_handler_decorator_and_unregister() -> None:
    reg: Registry[object] = Registry("demo")

    @reg.handler("f")
    def _f() -> str:
        return "hi"

    assert reg.get("f") is _f
    reg.unregister("f")
    assert reg.get("f") is None
    reg.unregister("f")  # no error on absent key


def test_base_readout_kinds_are_registered() -> None:
    for kind in ("size_table", "state", "measure_offsets", "thresholds", "config_flag"):
        assert kind in READOUT_KINDS


def test_mod_can_register_a_new_readout_kind() -> None:
    """A Python mod registering a new ``kind`` flows through ``effect_readout_rows``."""

    data = load_game_data()

    class FakeReadout:
        kind = "shout"
        label = "Shout"
        data = {"word": "boom"}

    READOUT_KINDS.register(
        "shout",
        lambda ro, effect, gd: [EffectStat("shout", ro.label, "", ro.data["word"].upper(), "")],
    )
    try:
        from mm_companion.core.rules.powers_terms import _readout_rows

        rows = _readout_rows(FakeReadout(), PowerEffectInstance("growth", rank=1), data)
        assert [(r.label, r.value) for r in rows] == [("Shout", "BOOM")]
    finally:
        READOUT_KINDS.unregister("shout")


def test_base_pattern_behaviours_are_registered() -> None:
    assert PATTERN_BEHAVIOURS.get(PASSIVE_PERMANENT) == PatternBehaviour(True, toggled=False)
    assert PATTERN_BEHAVIOURS.get(PASSIVE_TOGGLE) == PatternBehaviour(True, toggled=True)
    assert PATTERN_BEHAVIOURS.get(INSTANT_ACTION) == PatternBehaviour(False, toggled=False)
    assert PATTERN_BEHAVIOURS.get(RESOURCE_POOL) == PatternBehaviour(False, toggled=False)


def test_base_gate_kinds_are_registered() -> None:
    # The gates that actually switch an effect off carry a blocker predicate…
    assert GATE_REMOVABLE in GATE_KINDS
    assert GATE_TOGGLE in GATE_KINDS
    # …while the Activation gate (the power's master switch) and the informational
    # Limited gate register none, so they are ignored in effect_is_active.
    assert GATE_ACTIVATION not in GATE_KINDS
    assert GATE_LIMITED not in GATE_KINDS


def test_mod_can_register_a_new_gate_kind() -> None:
    """A Python mod registering a blocker for a gate kind gates effect_is_active."""

    data = load_game_data()
    base = {e.id: e for e in data.effects}["protection"]
    effect = PowerEffectInstance("protection", rank=2, flaws=[ModifierSelection("limited")])
    power = Power(effects=[effect])
    # The Limited gate is informational by default — no blocker, so the effect stands.
    assert effect_is_active(power, effect, base, data) is True

    GATE_KINDS.register(GATE_LIMITED, lambda p, e: True)
    try:
        assert effect_is_active(power, effect, base, data) is False
    finally:
        GATE_KINDS.unregister(GATE_LIMITED)
    assert effect_is_active(power, effect, base, data) is True


def test_base_config_display_kinds_are_registered() -> None:
    # Only the config-field types whose stored value isn't a plain option value carry
    # a display handler; select/multiselect/text fall through to the generic renderer.
    assert "allocation" in CONFIG_DISPLAY_KINDS
    assert "repeatable" in CONFIG_DISPLAY_KINDS
    assert "select" not in CONFIG_DISPLAY_KINDS


def test_config_display_falls_back_to_option_labels() -> None:
    """An unregistered field type resolves stored values to their option labels."""

    from mm_companion.core.data_loader import ConfigOption, EffectConfigField

    field = EffectConfigField(
        key="mode",
        label="Mode",
        type="select",
        options=(ConfigOption("dazed", "Dazed"), ConfigOption("stunned", "Stunned")),
    )
    assert _config_display(field, "stunned") == "Stunned"
    assert _config_display(field, ["dazed", "stunned"]) == "Dazed + Stunned"


def test_mod_can_register_a_new_config_display_kind() -> None:
    """A Python mod registering a field-type handler renders through ``_config_display``."""

    from mm_companion.core.data_loader import EffectConfigField

    field = EffectConfigField(key="shout", label="Shout", type="shout")

    CONFIG_DISPLAY_KINDS.register("shout", lambda f, value: str(value).upper())
    try:
        assert _config_display(field, "boom") == "BOOM"
    finally:
        CONFIG_DISPLAY_KINDS.unregister("shout")
    # Unregistered again → falls back to generic rendering (raw value, no options).
    assert _config_display(field, "boom") == "boom"


def test_base_mechanism_scopes_are_registered() -> None:
    # Only the two mechanisms that overlay a displayed stat row carry a scope handler;
    # a mechanism read by a dedicated accessor (defense_mod) has none here.
    assert MECH_CHECK_PENALTY in MECHANISM_SCOPES
    assert MECH_DEBILITATE_TRAIT in MECHANISM_SCOPES
    assert MECH_DEFENSE_MOD not in MECHANISM_SCOPES


def test_mod_can_register_a_new_mechanism_scope() -> None:
    """A Python mod giving a mechanism a scope handler overlays a stat row through it."""

    data = load_game_data()
    char = Character()
    apply_condition(char, "defenseless", data)  # carries only defense_mod

    # defense_mod does not overlay a stat row by default — the overlay is inert.
    assert not condition_scope_penalty(char, data, {"AGL"}).active

    MECHANISM_SCOPES.register(
        MECH_DEFENSE_MOD, lambda cond, applied, keys: ScopeContribution(delta=-2, label="cursed")
    )
    try:
        overlay = condition_scope_penalty(char, data, {"AGL"})
        assert overlay.delta == -2
        assert "defenseless" in overlay.condition_ids
    finally:
        MECHANISM_SCOPES.unregister(MECH_DEFENSE_MOD)
    assert not condition_scope_penalty(char, data, {"AGL"}).active
