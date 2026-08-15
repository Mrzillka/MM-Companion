"""``CharacterSheet.reseed`` — every block restated from a model that moved under it.

The widget half of undo. The first test here is the important one: it changes the
model behind *every* block's back and then asserts each block caught up, so a block
added later without a way to restate itself fails loudly rather than quietly showing
the old value after a Ctrl+Z.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import (
    AdvantageSelection,
    AppliedCondition,
    Character,
    Complication,
)
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.ui.blocks.bus import EDITED, NOTIFICATIONS, RESEED_TOPICS, SignalBus
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.powers import _DraggableCard


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_reseed_restates_every_block_from_a_model_changed_underneath_it(
    qapp: QApplication,
) -> None:
    """One assertion per block, because one forgotten block is the whole failure mode."""
    sheet = CharacterSheet(load_game_data())
    char = sheet.character

    char.profile["hero_name"] = "Ghost"
    char.power_level = 12
    char.characteristics["power_level"] = 12
    char.characteristics["size"] = "Large"
    char.characteristics["hero_points"] = 4
    char.abilities["STR"] = 7
    char.resistances["TOUGHNESS"] = 3
    char.skill_ranks["Stealth"] = 5
    char.advantages.append(AdvantageSelection("Close Attack", 2))
    char.complications.append(Complication("Enemy", "Dr. Volt."))
    char.conditions.append(AppliedCondition("dazed"))
    char.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )

    sheet.reseed()

    assert sheet.base_info._profile_fields["hero_name"].text() == "Ghost"
    assert sheet.system_info._power_level.value() == 12
    assert sheet.system_info._size_combo.currentText() == "Large"
    assert sheet.system_info._hero_points.value() == 4
    assert sheet.abilities._abilities["STR"].value() == 7
    assert sheet.resistances._resistances["TOUGHNESS"].value() >= 3
    assert any(spin.value() == 5 for spin in sheet.skills._editable_spins)
    assert len(sheet.advantages._row_refs) == 1
    assert not sheet.complications._empty_label.isVisibleTo(sheet.complications)
    assert len(sheet.conditions._condition_chips) == 1
    assert sheet.powers.findChild(_DraggableCard) is not None


def test_reseed_reports_no_edit(qapp: QApplication) -> None:
    """Re-seeding writes widgets, and a widget write normally *is* an edit."""
    sheet = CharacterSheet(load_game_data())
    sheet.character.abilities["STR"] = 5

    edits: list[int] = []
    sheet.edited.connect(lambda: edits.append(1))
    sheet.reseed()

    assert sheet.abilities._abilities["STR"].value() == 5
    assert not edits


def test_reseed_does_not_write_the_model(qapp: QApplication) -> None:
    """A reseed restates widgets from the model; it must not touch the model back.

    ``BaseInfoSection`` wrote every profile key it had a field for, so a restored
    state that omitted one came back carrying it as ``""``. That is not cosmetic:
    ``at_saved_state()`` compares canonical JSON, so the sheet read dirty forever
    against a file it exactly matched, and each save accreted another empty key.
    """
    sheet = CharacterSheet(load_game_data())
    # The field has to *hold* something first: setText only fires textChanged when
    # the text really changes, so a reseed over already-empty widgets writes nothing
    # and the bug hides.
    sheet.base_info._profile_fields["hero_name"].setText("Ghost")
    assert sheet.character.profile["hero_name"] == "Ghost"

    sheet.character.profile.clear()  # the restored state never had that key
    sheet.reseed()

    assert sheet.character.profile == {}


def test_reseed_does_not_clamp_a_rank_above_the_trait_range(qapp: QApplication) -> None:
    """A spin box clamps silently, which here would *lose* the rank being restored."""
    sheet = CharacterSheet(load_game_data())
    ceiling = sheet.abilities._abilities["STR"].maximum()
    sheet.character.abilities["STR"] = ceiling + 5

    sheet.reseed()

    assert sheet.abilities._abilities["STR"].value() == ceiling + 5


def test_reseed_leaves_power_level_and_points_as_restored(qapp: QApplication) -> None:
    """Both are authoritative in a restored state; reconciling one would rewrite it."""
    sheet = CharacterSheet(load_game_data())
    sheet.character.power_level = 12
    sheet.character.characteristics["power_level"] = 12
    sheet.character.power_points_total = 150
    sheet.character.characteristics["power_points"] = 150

    sheet.reseed()

    assert sheet.system_info._power_level.value() == 12
    assert sheet.system_info._power_points.value() == 150
    assert sheet.character.power_points_total == 150


def test_reseed_keeps_the_hero_point_note_baseline_honest(qapp: QApplication) -> None:
    """The pips are set silently, so the "spent/gained" baseline has to move by hand."""
    sheet = CharacterSheet(load_game_data())
    sheet.character.characteristics["hero_points"] = 3
    sheet.reseed()

    notes: list[str] = []
    sheet.system_info.noteRequested.connect(notes.append)
    sheet.system_info.set_hero_points(2)

    assert notes and "2 left" in notes[0]


def test_reseed_topics_are_every_notification_but_edited() -> None:
    """The guard-rail: a topic added later cannot be silently left out of a restore."""
    assert set(RESEED_TOPICS) == set(NOTIFICATIONS) - {EDITED}
    assert EDITED not in RESEED_TOPICS


def test_publish_all_fires_each_handler_once() -> None:
    """Several handlers answer to two topics, and each rebuilds a whole card tree."""
    bus = SignalBus()
    calls: list[str] = []

    def handler() -> None:
        calls.append("once")

    bus.subscribe("a", handler)
    bus.subscribe("b", handler)
    bus.subscribe("b", lambda: calls.append("other"))

    bus.publish_all(("a", "b"))

    assert calls == ["once", "other"]


def test_reseed_survives_a_block_with_nothing_to_restate(qapp: QApplication) -> None:
    """The Dice block holds no model value and exposes no ``reseed`` — and is skipped."""
    sheet = CharacterSheet(load_game_data(), Character.new_default(load_game_data()))

    assert not hasattr(sheet.dice, "reseed")
    sheet.reseed()  # must not raise
