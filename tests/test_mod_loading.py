"""Phase 6: mod Python-module loading, the trust gate, and the sample mods.

Covers the code-executing half of a mod (``python_module``) and verifies the two
shipped sample mods (``docs/sample-mods/``) work end-to-end when dropped into a
fresh workspace: enable + trust -> initialize -> the mod's data and its registered
behaviour both show up.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mm_companion.core import mods, storage
from mm_companion.core.data_loader import clear_game_data_cache, load_game_data
from mm_companion.core.powers import PowerEffectInstance
from mm_companion.core.rules.powers_terms import READOUT_KINDS, effect_readout_rows

SAMPLE_MODS = Path(__file__).resolve().parents[1] / "docs" / "sample-mods"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    clear_game_data_cache()
    yield tmp_path
    clear_game_data_cache()


def _install_sample(home: Path, mod_dirname: str) -> Path:
    """Copy a shipped sample mod into the workspace ``mods/`` dir."""
    dest = storage.ensure_workspace().mods_dir / mod_dirname
    shutil.copytree(SAMPLE_MODS / mod_dirname, dest)
    return dest


# --- load_mod_python_modules: the trust gate --------------------------------


def _mod(mod_id: str, *, is_base: bool = False, module: str | None = "m") -> mods.Mod:
    return mods.Mod(
        id=mod_id,
        name=mod_id,
        version="1",
        priority=0,
        files=(),
        is_base=is_base,
        python_module=module,
    )


def test_trusted_module_is_imported() -> None:
    seen: list[str] = []
    loaded = mods.load_mod_python_modules([_mod("m1")], trusted={"m1"}, importer=seen.append)
    assert loaded == ["m1"]
    assert seen == ["m"]


def test_untrusted_module_is_skipped() -> None:
    seen: list[str] = []
    loaded = mods.load_mod_python_modules([_mod("m1")], trusted=set(), importer=seen.append)
    assert loaded == []
    assert seen == []


def test_base_module_is_always_trusted() -> None:
    seen: list[str] = []
    loaded = mods.load_mod_python_modules(
        [_mod("base", is_base=True)], trusted=set(), importer=seen.append
    )
    assert loaded == ["base"]


def test_mod_without_python_module_is_ignored() -> None:
    loaded = mods.load_mod_python_modules(
        [_mod("m1", module=None)], trusted={"m1"}, importer=lambda name: None
    )
    assert loaded == []


def test_import_failure_does_not_crash() -> None:
    def boom(name: str) -> None:
        raise ImportError("broken mod")

    loaded = mods.load_mod_python_modules([_mod("m1")], trusted={"m1"}, importer=boom)
    assert loaded == []


# --- settings seams ---------------------------------------------------------


def test_set_mod_enabled_and_trusted_roundtrip(_home: Path) -> None:
    mods.set_mod_enabled("x", True)
    mods.set_mod_trusted("x", True)
    settings = storage.load_settings()
    assert settings["enabled_mods"] == ["x"]
    assert settings["trusted_mods"] == ["x"]
    # Disabling also revokes trust.
    mods.set_mod_enabled("x", False)
    settings = storage.load_settings()
    assert settings["enabled_mods"] == []
    assert settings["trusted_mods"] == []


# --- sample mods, end-to-end ------------------------------------------------


def test_sample_data_only_mod_adds_advantage_and_block(_home: Path) -> None:
    _install_sample(_home, "campaign-notes")
    mods.set_mod_enabled("campaign-notes", True)
    clear_game_data_cache()

    data = load_game_data()
    assert any(a.id == "signature_gear" for a in data.advantages)
    block = next(b for b in data.blocks if b.id == "campaign_notes")
    assert block.title == "Campaign Notes"
    assert any(f.key == "campaign_faction" for f in block.fields)


def test_sample_python_mod_registers_readout_kind(_home: Path) -> None:
    root = _install_sample(_home, "flat-bonus-readouts")
    mods.set_mod_enabled("flat-bonus-readouts", True)
    mods.set_mod_trusted("flat-bonus-readouts", True)
    clear_game_data_cache()
    try:
        loaded = mods.initialize_mods()
        assert "flat-bonus-readouts" in loaded
        assert "flat_bonus" in READOUT_KINDS

        data = load_game_data()
        effect = PowerEffectInstance(effect_id="damage", rank=5)
        rows = effect_readout_rows(effect, data)
        assert any(r.value == "+2" and r.label == "Signature Bonus" for r in rows)
    finally:
        # This test mutates the process-global registry and sys.path; undo both so
        # other tests see a pristine engine.
        if "flat_bonus" in READOUT_KINDS:
            READOUT_KINDS.unregister("flat_bonus")
        import sys

        sys.path[:] = [p for p in sys.path if p != str(root)]
        sys.modules.pop("flat_bonus_mod", None)


def test_sample_python_mod_honors_its_option(_home: Path) -> None:
    root = _install_sample(_home, "flat-bonus-readouts")
    mods.set_mod_enabled("flat-bonus-readouts", True)
    mods.set_mod_trusted("flat-bonus-readouts", True)
    # Configure the bonus larger than the JSON default of +2.
    mods.set_mod_options("flat-bonus-readouts", {"bonus_amount": 5})
    clear_game_data_cache()
    try:
        mods.initialize_mods()
        data = load_game_data()
        effect = PowerEffectInstance(effect_id="damage", rank=5)
        rows = effect_readout_rows(effect, data)
        assert any(r.value == "+5" and r.label == "Signature Bonus" for r in rows)
    finally:
        if "flat_bonus" in READOUT_KINDS:
            READOUT_KINDS.unregister("flat_bonus")
        import sys

        sys.path[:] = [p for p in sys.path if p != str(root)]
        sys.modules.pop("flat_bonus_mod", None)


def test_untrusted_python_mod_data_loads_but_code_does_not(_home: Path) -> None:
    _install_sample(_home, "flat-bonus-readouts")
    mods.set_mod_enabled("flat-bonus-readouts", True)  # enabled, NOT trusted
    clear_game_data_cache()

    loaded = mods.initialize_mods()
    assert loaded == []
    assert "flat_bonus" not in READOUT_KINDS

    # Data still merges (the readout entry is present) — it just renders to nothing
    # because no handler is registered for its kind.
    data = load_game_data()
    assert "damage" in data.effect_readouts
    effect = PowerEffectInstance(effect_id="damage", rank=5)
    assert effect_readout_rows(effect, data) == []
