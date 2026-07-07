"""The sections should read and write the shared Character model."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import power_points_spent, resistance_total, skill_total
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ability_spin_writes_to_model(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sheet.abilities._abilities["STR"].setValue(4)
    assert sheet.character.abilities["STR"] == 4


def test_skill_rank_flows_to_model_and_total(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.abilities._abilities["AGL"].setValue(3)

    # Drive the Stealth rank spin box via the row it renders into.
    stealth_row = next(row for row in sheet.skills._rows if row[1] == "Stealth")
    _, _, _, total_item, _ = stealth_row
    sheet.character.skill_ranks["Stealth"] = 4
    sheet.skills._refresh_totals()

    assert skill_total(sheet.character, data, "Stealth") == 7  # AGL 3 + 4 ranks
    assert total_item.text() == "7"


def test_spent_power_points_reflected_in_pool_label(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.abilities._abilities["STR"].setValue(4)  # 4 * 2 = 8 PP

    spent = power_points_spent(sheet.character, data)
    assert spent == 8
    assert sheet.base_info._pool_current["power_points"].text() == "8"


def test_raising_power_level_raises_the_budget_to_its_minimum(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.base_info._characteristics["power_level"].setValue(12)

    assert sheet.character.power_level == 12
    assert sheet.character.power_points_total == 12 * per_level
    assert sheet.base_info._characteristics["power_points"].value() == 12 * per_level


def test_raising_the_budget_past_a_border_raises_power_level(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.base_info._characteristics["power_points"].setValue(11 * per_level)

    assert sheet.character.power_level == 11
    assert sheet.base_info._characteristics["power_level"].value() == 11


def test_budget_within_a_band_leaves_power_level_alone(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.base_info._characteristics["power_points"].setValue(10 * per_level + 5)

    assert sheet.character.power_level == 10
    assert sheet.character.power_points_total == 10 * per_level + 5


def test_power_active_toggle_drops_the_bonus_live(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(
        Power(
            name="Armor",
            effects=[
                PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])
            ],
        )
    )
    sheet = CharacterSheet(data, char)
    assert resistance_total(char, data, "TOUGHNESS") == 6  # active by default

    checkbox = sheet.powers.findChild(QCheckBox)  # the row's "Active" switch
    assert checkbox is not None and checkbox.isChecked()

    fired: list[int] = []
    sheet.powers.changed.connect(lambda: fired.append(1))
    checkbox.setChecked(False)

    assert fired  # the section signals a change so the sheet re-derives
    assert char.powers[0].item_present is False
    assert resistance_total(char, data, "TOUGHNESS") == 0


def _pl_warning_shown(sheet: CharacterSheet) -> bool:
    """Whether any power card is showing the ⚠ Power-Level-breach marker."""
    from PySide6.QtWidgets import QLabel

    return any(label.text() == "⚠" for label in sheet.powers.findChildren(QLabel))


def test_raising_an_ability_re_derives_the_power_cards(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.power_level = 10  # attack cap of 20 on attack + effective rank
    char.abilities["STR"] = 4
    # Strength-Based Damage folds Strength into its rank: rank 15 + STR 4 = 19 (under
    # the cap), but STR 6 pushes it to 21 — the card must catch up when STR changes.
    char.powers.append(
        Power(
            name="Smash",
            effects=[
                PowerEffectInstance("damage", rank=15, extras=[ModifierSelection("strength_based")])
            ],
        )
    )
    sheet = CharacterSheet(data, char)
    assert not _pl_warning_shown(sheet)  # 19 ≤ 20

    sheet.abilities._abilities["STR"].setValue(6)  # editing the sheet fact
    assert _pl_warning_shown(sheet)  # 21 > 20 — the card re-derived and now warns

    sheet.abilities._abilities["STR"].setValue(4)
    assert not _pl_warning_shown(sheet)  # back under the cap, marker clears


def test_raising_power_level_clears_a_power_cards_warning(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.power_level = 10  # cap 20
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=21)]))
    sheet = CharacterSheet(data, char)
    assert _pl_warning_shown(sheet)  # rank 21 over the PL 10 cap

    sheet.base_info._characteristics["power_level"].setValue(11)  # cap rises to 22
    assert not _pl_warning_shown(sheet)  # the card re-derived against the new cap


def test_toggling_an_enhancer_re_derives_a_dependent_power_card(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.power_level = 12
    char.abilities["STR"] = 2
    # Rage boosts STR by 6 but is gated by Activation; Punch is Strength-Based, so its
    # save DC reads the *effective* STR — switching Rage off must move Punch's card.
    char.powers.append(
        Power(
            name="Rage",
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=6,
                    config={"target": "STR"},
                    flaws=[ModifierSelection("activation")],
                )
            ],
        )
    )
    char.powers.append(
        Power(
            name="Punch",
            effects=[
                PowerEffectInstance("damage", rank=10, extras=[ModifierSelection("strength_based")])
            ],
        )
    )
    sheet = CharacterSheet(data, char)
    # Rage on: effective STR 8 → Damage rank 18 → Toughness DC 28.
    assert "Toughness vs. 28" in sheet.powers._rolls_text(char.powers[1])

    sheet.powers.findChild(QCheckBox).setChecked(False)  # switch Rage off
    # Rage off: effective STR 2 → Damage rank 12 → Toughness DC 22.
    assert "Toughness vs. 22" in sheet.powers._rolls_text(char.powers[1])


def test_sheet_accepts_an_existing_character(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["INT"] = 5
    sheet = CharacterSheet(data, char)
    assert sheet.character is char
    assert sheet.abilities._abilities["INT"].value() == 5


def test_sheet_exposes_six_blocks(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())

    assert set(sheet.block_keys()) == {
        "base_info",
        "abilities",
        "resistances",
        "advantages",
        "skills",
        "powers",
    }
    # Every block is placed exactly once across the arrangement's rows.
    placed = [key for row in sheet.arrangement()["rows"] for key in row]
    assert sorted(placed) == sorted(sheet.block_keys())


def test_reset_layout_redocks_and_reshows_panels(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sheet.float_block("skills")
    sheet.hide_block("powers")

    sheet.reset_layout()

    arrangement = sheet.arrangement()
    assert arrangement["floating"] == {}
    assert arrangement["hidden"] == []
    placed = [key for row in arrangement["rows"] for key in row]
    assert "skills" in placed and "powers" in placed


def test_floating_a_block_keeps_cross_block_wiring_live(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.character.skill_ranks["Stealth"] = 2

    # Tear the Skills block out into its own window, then edit an ability: the
    # abilities→skills wiring must still fire across the window boundary.
    sheet.float_block("skills")
    sheet.abilities._abilities["AGL"].setValue(3)

    stealth_row = next(row for row in sheet.skills._rows if row[1] == "Stealth")
    assert stealth_row[3].text() == "5"  # AGL 3 + 2 ranks
