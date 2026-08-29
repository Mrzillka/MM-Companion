"""Phase 6: mod Python-module loading, the trust gate, and the sample mods.

Covers the code-executing half of a mod (``python_module``) and verifies the two
shipped sample mods (``docs/sample-mods/``) work end-to-end when dropped into a
fresh workspace: enable + trust -> initialize -> the mod's data and its registered
behaviour both show up.
"""

from __future__ import annotations

import json
import shutil
import zipfile
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
        # Handlers take a character now; a mod's own must survive being handed one.
        from mm_companion.core.character import Character

        with_char = effect_readout_rows(effect, data, Character())
        assert [(r.label, r.value) for r in with_char] == [(r.label, r.value) for r in rows]
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


# --- the equipment sample: a mod that registers a stat applier --------------


def _uninstall_python_mod(root: Path, module: str) -> None:
    """Undo what importing a mod module did to the process (sys.path + sys.modules)."""

    import sys

    sys.path[:] = [p for p in sys.path if p != str(root)]
    sys.modules.pop(module, None)


def test_sample_equipment_mod_registers_stat_applier(_home: Path) -> None:
    """The vest is data; ``partial_bonus`` is the one thing that needs code."""

    from mm_companion.core.character import AdvantageSelection, Character
    from mm_companion.core.rules import build_item_from_entry, resistance_total
    from mm_companion.core.rules.appliers import STAT_APPLIERS

    root = _install_sample(_home, "field-kit")
    mods.set_mod_enabled("field-kit", True)
    mods.set_mod_trusted("field-kit", True)
    clear_game_data_cache()
    try:
        assert "field-kit" in mods.initialize_mods()
        assert "partial_bonus" in STAT_APPLIERS

        data = load_game_data()
        entry = data.equipment_catalog()["ablative_vest"]
        assert entry.category == "armor"  # merged into a stock category, not a new one

        char = Character.new_default(data)
        char.advantages.append(AdvantageSelection(name="Equipment", rank=1))
        char.equipment.append(build_item_from_entry(entry, data))
        # Rank 4 of Ablative Weave, halved and rounded up by the mod's applier.
        assert resistance_total(char, data, "TOUGHNESS") == 2
    finally:
        if "partial_bonus" in STAT_APPLIERS:
            STAT_APPLIERS.unregister("partial_bonus")
        _uninstall_python_mod(root, "field_kit_mod")


def test_untrusted_equipment_mod_buys_the_item_but_grants_nothing(_home: Path) -> None:
    """An unregistered apply kind yields no contributions rather than raising.

    So the gear a disabled-Python mod added is still on the sheet, still priced and
    still worn — it simply does nothing, which is what keeps a saved character
    loadable after its mod loses trust.
    """

    from mm_companion.core.character import AdvantageSelection, Character
    from mm_companion.core.rules import build_item_from_entry, item_ep_cost, resistance_total
    from mm_companion.core.rules.appliers import STAT_APPLIERS

    _install_sample(_home, "field-kit")
    mods.set_mod_enabled("field-kit", True)  # enabled, NOT trusted
    clear_game_data_cache()

    assert mods.initialize_mods() == []
    assert "partial_bonus" not in STAT_APPLIERS

    data = load_game_data()
    entry = data.equipment_catalog()["ablative_vest"]
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Equipment", rank=1))
    item = build_item_from_entry(entry, data)
    char.equipment.append(item)

    assert item_ep_cost(item, data) == 3
    assert resistance_total(char, data, "TOUGHNESS") == 0


# --- installing from an archive ---------------------------------------------
#
# The distribution path: a mod is downloaded as a .zip and opened *before* the
# user has decided whether to trust its Python, which is the least trusted moment
# there is. Every test here is about that.


def _zip_sample(tmp_path: Path, mod_dirname: str, *, wrap: bool = True) -> Path:
    """Zip a shipped sample mod, with or without a wrapping folder."""
    archive = tmp_path / f"{mod_dirname}.zip"
    source = SAMPLE_MODS / mod_dirname
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                inner = path.relative_to(source)
                zf.write(path, str(Path(mod_dirname) / inner) if wrap else str(inner))
    return archive


def test_a_mod_installs_from_a_wrapped_archive(_home: Path, tmp_path: Path) -> None:
    """The shape every zip tool produces when you compress a directory."""
    storage.ensure_workspace()
    archive = _zip_sample(tmp_path, "campaign-notes", wrap=True)

    mod = mods.import_mod_archive(archive)

    assert mod.id == "campaign-notes"
    assert (storage.get_workspace().mods_dir / "campaign-notes" / "mod.json").exists()
    assert [m.id for m in mods.discover_workspace_mods()] == ["campaign-notes"]


def test_a_mod_installs_from_a_flat_archive(_home: Path, tmp_path: Path) -> None:
    """Telling a user their mod is "wrongly zipped" is a worse answer than
    looking one level down — so both shapes work."""
    storage.ensure_workspace()
    archive = _zip_sample(tmp_path, "campaign-notes", wrap=False)

    assert mods.import_mod_archive(archive).id == "campaign-notes"


def test_an_archive_cannot_write_outside_the_workspace(_home: Path, tmp_path: Path) -> None:
    """Zip slip. ``extractall`` follows a ``..`` out of the directory you gave it,
    and this runs on a file from the internet."""
    storage.ensure_workspace()
    outside = tmp_path / "outside.txt"
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("evil/mod.json", json.dumps({"id": "evil", "name": "E", "version": "1"}))
        zf.writestr("../outside.txt", "pwned")
        zf.writestr("evil/../../outside.txt", "pwned")

    mods.import_mod_archive(archive)

    assert not outside.exists()
    assert not (tmp_path.parent / "outside.txt").exists()


def test_an_archive_leaves_no_compiled_python_behind(_home: Path, tmp_path: Path) -> None:
    """A ``.pyc`` compiled against another Python would shadow the ``.py``
    beside it and fail obscurely at import time."""
    storage.ensure_workspace()
    archive = tmp_path / "kit.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("kit/mod.json", json.dumps({"id": "kit", "name": "K", "version": "1"}))
        zf.writestr("kit/kit_mod.py", "X = 1\n")
        zf.writestr("kit/__pycache__/kit_mod.cpython-312.pyc", "not really bytecode")

    mods.import_mod_archive(archive)

    installed = storage.get_workspace().mods_dir / "kit"
    assert (installed / "kit_mod.py").exists()
    assert not (installed / "__pycache__").exists()


def test_a_file_that_is_not_an_archive_is_refused(_home: Path, tmp_path: Path) -> None:
    not_a_zip = tmp_path / "mod.zip"
    not_a_zip.write_text("this is not a zip", encoding="utf-8")

    with pytest.raises(mods.ModImportError):
        mods.import_mod_archive(not_a_zip)


def test_an_archive_with_no_manifest_is_refused(_home: Path, tmp_path: Path) -> None:
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "nothing to install here")

    with pytest.raises(mods.ModImportError):
        mods.import_mod_archive(archive)


def test_an_archive_holding_two_mods_is_refused_rather_than_guessed(
    _home: Path, tmp_path: Path
) -> None:
    """Which one was meant is genuinely ambiguous, and guessing is worse."""
    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in ("one", "two"):
            zf.writestr(f"{name}/mod.json", json.dumps({"id": name, "name": name, "version": "1"}))

    with pytest.raises(mods.ModImportError):
        mods.import_mod_archive(archive)
