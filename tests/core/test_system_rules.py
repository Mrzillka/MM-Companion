"""The ``system.json`` de-hardcoding: system-level rule references live in data.

The trait keys, formulas, sentinel scope strings, and structural modifier ids that
``core.rules`` resolvers used to hardcode now come from ``system.json`` (parsed into
:class:`~mm_companion.core.data_loader.SystemRules`), so a mod can retune them and see
the change flow through the resolvers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import (
    clear_game_data_cache,
    load_game_data,
)
from mm_companion.core.rules import (
    condition_check_penalty,
    defense_class,
    heroic_advantage_budget,
    initiative_ability,
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    clear_game_data_cache()
    yield tmp_path
    clear_game_data_cache()


def _write_system_mod(root: Path, system: dict) -> None:
    """Create a workspace mod overriding part of ``system.json`` and enable it."""
    mod_dir = root / storage.MODS_DIRNAME / "systemmod"
    mod_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": "systemmod",
        "name": "System Mod",
        "version": "1.0",
        "priority": 0,
        "files": ["system.json"],
    }
    (mod_dir / "mod.json").write_text(json.dumps(manifest), encoding="utf-8")
    (mod_dir / "system.json").write_text(json.dumps({"system": system}), encoding="utf-8")
    storage.update_settings(enabled_mods=["systemmod"])
    clear_game_data_cache()


# --- base parse -------------------------------------------------------------


def test_base_system_rules_parse() -> None:
    sys = load_game_data().system
    assert sys.default_initiative_ability == "AGL"
    assert sys.defense_dc_base == 10
    assert sys.heroic_budget_divisor == 2
    assert sys.trait_keys.attack == "ATK"
    assert sys.trait_keys.defense == "DEF"
    assert sys.trait_keys.toughness == "TOUGHNESS"
    assert sys.unscoped_scope_values == ("All checks",)
    assert sys.alternate_effect_modifier == "alternate_effect"
    caps = {p.cap: p for p in sys.paired_caps}
    assert caps["defense_toughness"].traits == ("DODGE", "TOUGHNESS")
    assert caps["fortitude_will"].label == "Fortitude + Will"


# --- mod overrides flow through the resolvers ------------------------------


def test_mod_can_retune_defense_dc_base(_home: Path) -> None:
    char = Character()
    base = defense_class(char, load_game_data())

    _write_system_mod(_home, {"defense_dc_base": 8})
    assert defense_class(char, load_game_data()) == base - 2


def test_mod_can_swap_the_default_initiative_ability(_home: Path) -> None:
    char = Character()
    assert initiative_ability(char, load_game_data()) == "AGL"

    _write_system_mod(_home, {"default_initiative_ability": "INT"})
    assert initiative_ability(char, load_game_data()) == "INT"


def test_mod_can_retune_the_heroic_budget_divisor(_home: Path) -> None:
    _write_system_mod(_home, {"heroic_budget_divisor": 3})
    divisor = load_game_data().system.heroic_budget_divisor
    assert divisor == 3
    assert heroic_advantage_budget(12, divisor) == 4  # 12 // 3
    # The bare form still defaults to the core //2.
    assert heroic_advantage_budget(12) == 6


def test_mod_can_redefine_the_unscoped_scope_sentinel(_home: Path) -> None:
    # Disabled's -5 check penalty is unscoped when its parameter is the sentinel.
    from mm_companion.core.rules import apply_condition

    base_data = load_game_data()
    char = Character()
    apply_condition(char, "disabled", base_data)
    char.conditions[0].parameter = "Everything"  # not the base sentinel -> scoped, not counted
    assert condition_check_penalty(char, base_data) == 0

    _write_system_mod(_home, {"unscoped_scope_values": ["Everything"]})
    data = load_game_data()
    assert condition_check_penalty(char, data) == -5  # now treated as unscoped
