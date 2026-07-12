"""The generic mechanics registry and the readout-kind vocabulary built on it."""

from __future__ import annotations

import pytest

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import PowerEffectInstance
from mm_companion.core.registry import Registry
from mm_companion.core.rules.powers_terms import READOUT_KINDS, EffectStat


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
