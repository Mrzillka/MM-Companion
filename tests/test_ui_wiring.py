"""The sections should read and write the shared Character model."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QLabel

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import power_points_spent, resistance_total, skill_total
from mm_companion.ui import theme
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.roll_history import NoteCard
from mm_companion.ui.sections.powers import _DraggableCard
from mm_companion.ui.sections.system_info import HeroPointsWidget, hero_point_note


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
    stealth_row = next(row for row in sheet.skills._rows if row.row_id == "Stealth")
    sheet.character.skill_ranks["Stealth"] = 4
    sheet.skills._refresh_totals()

    assert skill_total(sheet.character, data, "Stealth") == 7  # AGL 3 + 4 ranks
    assert stealth_row.total_item.text() == "7"


def test_spent_power_points_reflected_in_pool_label(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.abilities._abilities["STR"].setValue(4)  # 4 * 2 = 8 PP

    spent = power_points_spent(sheet.character, data)
    assert spent == 8
    assert sheet.system_info._pool_current.text() == "8"


def test_raising_power_level_raises_the_budget_to_its_minimum(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.system_info._power_level.setValue(12)

    assert sheet.character.power_level == 12
    assert sheet.character.power_points_total == 12 * per_level
    assert sheet.system_info._power_points.value() == 12 * per_level


def test_cost_config_change_reprices_every_block_and_shows_the_notice(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.abilities._abilities["STR"].setValue(5)  # 5 ranks
    assert sheet.abilities.block_title() == "Abilities — 10 PP"  # default 2/rank
    assert sheet.system_info._cost_notice.isHidden()

    # Homebrew the ability rate to 3/rank the way the dialog's Save does, then fire
    # the section's signals as _open_cost_config would after an accepted dialog.
    sheet.character.cost_overrides["ability_per_rank"] = 3
    sheet.system_info._refresh_cost_notice()
    sheet.system_info.costRatesChanged.emit()
    sheet.system_info.changed.emit()

    # The per-block subtotal, the pool total, and the homebrew notice all update.
    assert sheet.abilities.block_title() == "Abilities — 15 PP"  # 5 * 3
    assert sheet.system_info._pool_current.text() == "15"
    assert not sheet.system_info._cost_notice.isHidden()


def test_raising_the_budget_past_a_border_raises_power_level(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.system_info._power_points.setValue(11 * per_level)

    assert sheet.character.power_level == 11
    assert sheet.system_info._power_level.value() == 11


def test_budget_within_a_band_leaves_power_level_alone(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.system_info._power_points.setValue(10 * per_level + 5)

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

    card = sheet.powers.findChild(_DraggableCard)  # the card *is* the on/off switch
    assert card is not None and card.is_clickable()

    fired: list[int] = []
    dirtied: list[int] = []
    sheet.powers.runtimeChanged.connect(lambda: fired.append(1))
    sheet.edited.connect(lambda: dirtied.append(1))
    card.clicked.emit()

    assert fired  # the section signals a runtime change so the sheet re-derives
    assert not dirtied  # ...but a runtime toggle is not persisted, so it isn't an edit
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

    sheet.system_info._power_level.setValue(11)  # cap rises to 22
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
    assert "Toughness vs. 28" in sheet.powers._rolls_lines(char.powers[1])

    sheet.powers.findChild(_DraggableCard).clicked.emit()  # click Rage's card to switch it off
    # Rage off: effective STR 2 → Damage rank 12 → Toughness DC 22.
    assert "Toughness vs. 22" in sheet.powers._rolls_lines(char.powers[1])


def test_sheet_accepts_an_existing_character(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["INT"] = 5
    sheet = CharacterSheet(data, char)
    assert sheet.character is char
    assert sheet.abilities._abilities["INT"].value() == 5


def test_sheet_exposes_all_blocks(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())

    assert set(sheet.block_keys()) == {
        "base_info",
        "system_info",
        "character_image",
        "abilities",
        "resistances",
        "conditions",
        "advantages",
        "complications",
        "skills",
        "powers",
        "equipment",
        "notes",
        "dice",
    }
    # Every block is placed exactly once across the arrangement — the rows on the
    # page plus the pinned strip, which the Dice block starts in.
    arrangement = sheet.arrangement()
    placed = [key for row in arrangement["rows"] for key in row]
    placed += [key for line in arrangement["pinned"]["lines"] for key in line]
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

    stealth_row = next(row for row in sheet.skills._rows if row.row_id == "Stealth")
    assert stealth_row.total_item.text() == "5"  # AGL 3 + 2 ranks


def test_skill_modifier_column_hides_until_something_modifies_a_row(qapp: QApplication) -> None:
    from mm_companion.ui.sections.skills import COL_MODS

    data = load_game_data()
    sheet = CharacterSheet(data)
    # Nothing modifies a skill on a blank character, so the whole "+" column is hidden.
    assert all(t.isColumnHidden(COL_MODS) for t in sheet.skills._tables)

    sheet.character.powers.append(
        Power(
            name="Cat's Grace",
            effects=[PowerEffectInstance("enhanced_trait", rank=4, config={"target": "Stealth"})],
        )
    )
    sheet.skills.refresh_totals()

    assert all(not t.isColumnHidden(COL_MODS) for t in sheet.skills._tables)
    stealth = next(r for r in sheet.skills._rows if r.row_id == "Stealth")
    assert stealth.mod_item.text() == "+4"
    assert "Cat's Grace" in stealth.mod_item.toolTip()
    # A skill the power doesn't touch keeps an empty cell in the now-shown column.
    assert next(r for r in sheet.skills._rows if r.row_id == "Acrobatics").mod_item.text() == ""

    sheet.character.powers.clear()
    sheet.skills.refresh_totals()
    assert all(t.isColumnHidden(COL_MODS) for t in sheet.skills._tables)


def test_skill_modifier_column_nets_a_condition_penalty_against_a_boost(
    qapp: QApplication,
) -> None:
    from mm_companion.core.rules import apply_condition
    from mm_companion.ui.sections.skills import COL_MODS

    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.skills._ranks["Stealth"] = 5
    sheet.skills._ranks["Acrobatics"] = 5

    # A condition alone reveals the column — no power or advantage involved.
    apply_condition(sheet.character, "impaired", data, parameter="Stealth")
    sheet.skills.refresh_totals()
    assert all(not t.isColumnHidden(COL_MODS) for t in sheet.skills._tables)

    stealth = next(r for r in sheet.skills._rows if r.row_id == "Stealth")
    assert stealth.mod_item.text() == "-2"
    assert stealth.mod_item.foreground().color().name() == theme.color(
        "tint.worse"
    )  # a penalty reads red
    assert "Impaired" in stealth.mod_item.toolTip()
    assert stealth.total_item.text() == "3"  # 5 ranks - 2
    # The condition is scoped, so a different skill is untouched.
    assert next(r for r in sheet.skills._rows if r.row_id == "Acrobatics").mod_item.text() == ""

    # A boost on the same row nets against the penalty, and both are named on hover.
    sheet.character.powers.append(
        Power(
            name="Cat's Grace",
            effects=[PowerEffectInstance("enhanced_trait", rank=6, config={"target": "Stealth"})],
        )
    )
    sheet.skills.refresh_totals()
    stealth = next(r for r in sheet.skills._rows if r.row_id == "Stealth")
    assert stealth.mod_item.text() == "+4"  # +6 boost - 2 impaired
    assert stealth.mod_item.foreground().color().name() == theme.color(
        "tint.worse"
    )  # still penalised
    tip = stealth.mod_item.toolTip()
    assert "Cat's Grace" in tip and "Impaired" in tip
    assert stealth.total_item.text() == "9"  # 5 ranks + 6 boost - 2


def test_hero_points_pips_spend_and_gain(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    hero = sheet.system_info._hero_points
    hero.set_value(0)

    hero._on_click(2)  # light the 3rd pip → 1 hero point
    assert hero.value() == 1
    assert sheet.character.characteristics["hero_points"] == 1

    hero._on_click(2)  # click it again → spent, back to none
    assert hero.value() == 0
    assert sheet.character.characteristics["hero_points"] == 0


def test_hero_point_pips_toggle_in_any_order(qapp: QApplication) -> None:
    hero = HeroPointsWidget()
    hero.set_value(0)

    hero._on_click(3)  # the 4th pip alone, with nothing to its left
    assert hero.value() == 1
    assert hero._lit == {3}

    hero._on_click(0)
    assert hero.value() == 2
    assert hero._lit == {0, 3}


def test_hero_points_set_value_reconciles_to_the_count(qapp: QApplication) -> None:
    """An outside change carries a number, not an arrangement — see set_value."""
    hero = HeroPointsWidget()
    hero.set_value(0)
    hero._on_click(3)
    hero._on_click(4)  # a player's own arrangement: the two right-hand pips

    hero.set_value(1)  # the GM takes one — the right-most goes out
    assert hero._lit == {3}

    hero.set_value(3)  # and grants two — the left-most dark pips light up
    assert hero._lit == {0, 1, 3}

    hero.set_value(0)  # nothing left to preserve
    assert hero._lit == set()
    hero.set_value(2)
    assert hero._lit == {0, 1}


def test_hero_point_pips_show_the_held_and_spent_artwork(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    hero = sheet.system_info._hero_points
    hero.set_value(2)

    icons = [button.icon() for button in hero._buttons]
    assert not any(icon.isNull() for icon in icons)  # the SVGs actually rendered
    size = QSize(hero._pip_size, hero._pip_size)
    held = icons[0].pixmap(size).toImage()
    spent = icons[4].pixmap(size).toImage()
    assert held != spent  # a held point does not look like a spent one
    assert icons[1].pixmap(size).toImage() == held
    assert icons[2].pixmap(size).toImage() == spent  # the 3rd pip is the first spent one


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (3, 2, "spent a hero point — 2 left"),
        (2, 3, "gained a hero point — 3 left"),
        (5, 3, "spent 2 hero points — 3 left"),
        (0, 2, "gained 2 hero points — 2 left"),
        (2, 2, ""),  # nothing moved, nothing to say
    ],
)
def test_hero_point_note_wording(previous: int, current: int, expected: str) -> None:
    assert hero_point_note(previous, current) == expected


def test_spending_a_hero_point_writes_it_in_the_history(qapp: QApplication) -> None:
    """Off the air the note lands in the Dice block's own private history."""
    sheet = CharacterSheet(load_game_data())
    sheet.system_info._hero_points.set_value(3)
    sheet.system_info._last_hero_points = 3

    sheet.system_info._hero_points._on_click(2)  # put one out → 2 left

    notes = sheet.dice.view._local_history.findChildren(NoteCard)
    assert [n.findChild(QLabel).text() for n in notes] == ["spent a hero point — 2 left"]


def test_a_gm_granted_hero_point_is_written_down_too(qapp: QApplication) -> None:
    """The GM's command lands on the player's own model, so it takes the same path."""
    sheet = CharacterSheet(load_game_data())
    sheet.system_info._hero_points.set_value(1)
    sheet.system_info._last_hero_points = 1

    sheet.system_info.set_hero_points(3)

    notes = sheet.dice.view._local_history.findChildren(NoteCard)
    assert [n.findChild(QLabel).text() for n in notes] == ["gained 2 hero points — 3 left"]


def test_a_hero_point_note_does_not_reopen_a_closed_dice_block(qapp: QApplication) -> None:
    """A note is a side effect of an edit elsewhere — it must not grab the screen."""
    sheet = CharacterSheet(load_game_data())
    sheet._canvas.hide_block("dice")
    sheet.system_info._last_hero_points = sheet.system_info._hero_points.value()

    sheet.system_info._hero_points._on_click(4)

    assert sheet._canvas.is_hidden("dice")


def test_initiative_readout_follows_agility_and_advantages(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)

    sheet.abilities._abilities["AGL"].setValue(4)
    assert sheet.system_info._initiative.text() == "+4 (AGL)"


def test_active_growth_shows_the_effective_size(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(Power(name="Big", effects=[PowerEffectInstance("growth", rank=2)]))
    sheet = CharacterSheet(data, char)

    # Base size stays Medium; the readout shows the Growth-shifted effective size.
    assert sheet.system_info._size_combo.currentText() == "Medium"
    assert sheet.system_info._size_effective.text() == "→ Huge"


def test_speed_readout_names_each_mode_and_runs_only_on_the_ground(qapp: QApplication) -> None:
    """The worked example from the design: a walker who also flies."""
    data = load_game_data()
    char = Character.new_default(data)
    flight = Power(name="Fly", effects=[PowerEffectInstance("flight", rank=2)])
    flight.activated = True
    char.powers = [flight]
    sheet = CharacterSheet(data, char)

    assert sheet.system_info._speed.rendered_text() == (
        "Ground speed: 15 ft / 30 ft / 60 ft\nFlight: 30 ft / 60 ft"
    )


def test_a_speed_power_makes_the_ground_line_faster(qapp: QApplication) -> None:
    """Every rank bought counts: the ground line is a sum, not a ``max``."""
    data = load_game_data()
    char = Character.new_default(data)
    speed = Power(name="Fast", effects=[PowerEffectInstance("speed", rank=1)])
    speed.activated = True
    char.powers = [speed]
    sheet = CharacterSheet(data, char)

    assert sheet.system_info._speed.rendered_text() == "Ground speed: 30 ft / 60 ft / 120 ft"


def test_speed_unit_toggle_switches_to_km_per_hour(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    speed = sheet.system_info._speed

    assert "ft" in speed.rendered_text()
    speed._toggle_unit()
    assert "km/h" in speed.rendered_text()


def test_disabled_condition_lowers_the_initiative_readout(qapp: QApplication) -> None:
    from mm_companion.core.rules import apply_condition

    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.abilities._abilities["AGL"].setValue(4)
    assert sheet.system_info._initiative.text() == "+4 (AGL)"

    apply_condition(sheet.character, "disabled", data)  # -5 on all checks
    sheet.system_info.refresh_derived()

    text = sheet.system_info._initiative.text()
    assert "-1" in text  # +4 AGL - 5 = -1
    assert theme.color("tint.worse") in text  # the penalised value reads red
    assert "-5" in sheet.system_info._initiative.toolTip()


def test_hindered_condition_slows_the_ground_speed(qapp: QApplication) -> None:
    from mm_companion.core.rules import apply_condition

    data = load_game_data()
    sheet = CharacterSheet(data)

    apply_condition(sheet.character, "hindered", data)  # -1 speed rank
    sheet.system_info.refresh_derived()

    assert "-1 rank" in sheet.system_info._speed.rendered_text()
    assert theme.color("tint.worse") in sheet.system_info._speed.rendered_styles()
    assert "slowed" in sheet.system_info._speed.toolTip()


def test_immobile_condition_marks_the_ground_speed_immobilised(qapp: QApplication) -> None:
    from mm_companion.core.rules import apply_condition

    data = load_game_data()
    sheet = CharacterSheet(data)

    apply_condition(sheet.character, "immobile", data)  # zeroes ground speed
    sheet.system_info.refresh_derived()

    assert "immobilised" in sheet.system_info._speed.rendered_text()
    assert theme.color("tint.worse") in sheet.system_info._speed.rendered_styles()


def test_applying_a_condition_refreshes_derived_readouts_through_the_bus(
    qapp: QApplication,
) -> None:
    from mm_companion.core.rules import apply_condition

    data = load_game_data()
    sheet = CharacterSheet(data)

    # The condition is applied on the model and the section announces it the way the
    # Conditions block does; the system block's speed readout must catch up via the bus.
    apply_condition(sheet.character, "immobile", data)
    sheet.conditions.conditionsChanged.emit()

    assert "immobilised" in sheet.system_info._speed.rendered_text()


def test_high_resistance_total_is_not_clamped_away(qapp: QApplication) -> None:
    """A resistance spin box holds the *total*, which on a high-Stamina character runs
    past an ability's ceiling. Clamping it would make the next edit recompute the
    bought delta from the wrong number and silently refund points."""
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.character.resistances["TOUGHNESS"] = 10  # ten bought ranks
    sheet.abilities._abilities["STA"].setValue(28)
    sheet.resistances.refresh_bases()

    spin = sheet.resistances._resistances["TOUGHNESS"]
    assert spin.value() == 38  # 28 base + 10 bought, not clamped to 30

    # One decrement moves the bought delta by exactly one, not down to a clamp remainder.
    spin.setValue(spin.value() - 1)
    assert sheet.character.resistances["TOUGHNESS"] == 9


def test_resistance_range_comes_from_the_data(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    expected = data.costs.trait_range("resistance")
    spin = sheet.resistances._resistances["TOUGHNESS"]
    assert (spin.minimum(), spin.maximum()) == (expected.min, expected.max)
    # Abilities keep their own, narrower range.
    ability = data.costs.trait_range("ability")
    assert sheet.abilities._abilities["STR"].maximum() == ability.max


def test_cancelling_add_specialization_leaves_the_model_untouched(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled dialog must not seed an empty entry — ``_remove_specialization``
    goes out of its way to keep those out of the model, and one would be saved."""
    from PySide6.QtWidgets import QInputDialog

    data = load_game_data()
    sheet = CharacterSheet(data)
    skill = next(s for s in data.skills if not s.focused)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    sheet.skills._add_specialization(skill)
    assert skill.name not in sheet.character.specializations

    # An accepted dialog still records the specialization.
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Forgery", True)))
    sheet.skills._add_specialization(skill)
    assert sheet.character.specializations[skill.name] == ["Forgery"]
