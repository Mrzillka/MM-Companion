"""The sections should read and write the shared Character model."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QLabel, QSpinBox

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    power_level_violations,
    power_points_spent,
    resistance_total,
    skill_total,
)
from mm_companion.ui import layout_tree as lt
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
    # ...and marks the sheet dirty with it: what is switched on is saved with the
    # build now, so a toggle nobody called an edit would be lost on close.
    assert dirtied
    assert char.powers[0].item_present is False
    assert resistance_total(char, data, "TOUGHNESS") == 0


def _pl_warning_shown(sheet: CharacterSheet) -> bool:
    """Whether any power card is showing the ⚠ Power-Level-breach marker.

    Flushes the bus first, because the power cards' ``refresh`` is a **coalescing**
    subscriber: an edit arms it and the redraw happens once when the turn settles,
    so a spin box dragged through ten ranks rebuilds the card tree once instead of
    ten times (see ``ui/blocks/registry.py::_COALESCED``). ``flush`` is what the
    running app's event loop does a moment later; a test that reads the widgets in
    the same breath as the edit has to say so.
    """
    from PySide6.QtWidgets import QLabel

    sheet.bus.flush()
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


def test_a_burst_of_ability_steps_rebuilds_the_card_trees_once(qapp: QApplication) -> None:
    """Dragging a spin box must not rebuild the two card trees once per step.

    Both are `facts-changed` subscribers that rebuild wholesale, and every spin box
    on the sheet raises that topic on every step — so a rank dragged from 0 to 10
    rebuilt them ten times over and showed the tenth. They coalesce now; this is the
    assertion that says so, from the outside.
    """
    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)]))
    sheet = CharacterSheet(data, char)
    sheet.bus.flush()

    rebuilds: list[int] = []
    original = sheet.powers._rebuild_list
    sheet.powers._rebuild_list = lambda: (rebuilds.append(1), original())[1]

    spin = sheet.abilities._abilities["STR"]
    for value in range(1, 11):
        spin.setValue(value)
    assert rebuilds == []  # nothing redrawn yet — the turn has not settled

    sheet.bus.flush()
    assert rebuilds == [1]


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
        "scene",
    }
    # Every block is placed exactly once across the arrangement — the rows on the
    # page plus the pinned strip, which the Dice and Scene blocks start in.
    region = sheet.arrangement()["region"]
    placed = lt.keys(sheet.canvas.page_tree())
    placed += lt.keys(lt.from_dict(region["root"], set(sheet.block_keys())))
    assert sorted(placed) == sorted(sheet.block_keys())


def test_reset_layout_redocks_and_reshows_panels(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sheet.float_block("skills")
    sheet.hide_block("powers")

    sheet.reset_layout()

    arrangement = sheet.arrangement()
    assert arrangement["floating"] == {}
    assert arrangement["hidden"] == []
    placed = lt.keys(sheet.canvas.page_tree())
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


def test_a_resistance_row_reads_as_ability_rank_and_total(qapp: QApplication) -> None:
    """The three numbers that make a resistance, each in its own column: the trait it
    derives from, the ranks bought on top, and what they come to."""
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.character.resistances["TOUGHNESS"] = 10  # ten bought ranks
    sheet.abilities._abilities["STA"].setValue(28)
    sheet.resistances.refresh_ranks()

    section = sheet.resistances
    assert section._resistance_base["TOUGHNESS"].text() == "28"
    assert section._resistances["TOUGHNESS"].value() == 10  # the ranks, not the total
    assert section._resistance_total["TOUGHNESS"].text() == "38"

    # The spin is the bought ranks outright, so an edit is a write and nothing else:
    # no base to subtract back out, and nothing a clamped display could rewrite.
    section._resistances["TOUGHNESS"].setValue(9)
    assert sheet.character.resistances["TOUGHNESS"] == 9
    assert section._resistance_total["TOUGHNESS"].text() == "37"


def test_a_resistance_with_no_trait_to_derive_from_shows_a_dash(qapp: QApplication) -> None:
    """Defence is bought outright. A 0 in its Ability column would claim a base it
    hasn't got; the total is still the number the game asks for."""
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.resistances._resistances["DEF"].setValue(4)

    assert sheet.resistances._resistance_base["DEF"].text() == "—"
    assert sheet.resistances._resistance_total["DEF"].text() == "4"
    # And Dodge derives from Defence, so its own Ability column followed that edit.
    assert sheet.resistances._resistance_base["DODGE"].text() == "4"


def test_an_enhanced_ability_tints_the_resistance_it_feeds(qapp: QApplication) -> None:
    """An Enhanced Trait moves the *base*, not the ranks, so it lands in the Ability
    column and says which power put it there."""
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.character.powers.append(
        Power(
            name="Iron Hide",
            effects=[PowerEffectInstance("enhanced_trait", rank=4, config={"target": "STA"})],
        )
    )
    sheet.resistances.refresh_readouts()

    cell = sheet.resistances._resistance_base["TOUGHNESS"]
    assert cell.text() == "4"
    assert cell.foreground().color().name() == theme.color("tint.better")
    assert "Iron Hide" in cell.toolTip()


def test_resistance_range_comes_from_the_data(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    expected = data.costs.trait_range("resistance")
    spin = sheet.resistances._resistances["TOUGHNESS"]
    assert (spin.minimum(), spin.maximum()) == (expected.min, expected.max)
    # Abilities keep their own, narrower range.
    ability = data.costs.trait_range("ability")
    assert sheet.abilities._abilities["STR"].maximum() == ability.max


def test_add_specialization_offers_the_catalog_without_closing_the_list(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Add specialization…* asks the way *Add focus…* does — the skill's common uses as
    ready choices, its note as the prompt where they cannot be listed, and anything the
    player types accepted past both."""
    from PySide6.QtWidgets import QInputDialog

    data = load_game_data()
    sheet = CharacterSheet(data)
    stealth = next(s for s in data.skills if s.name == "Stealth")
    perception = next(s for s in data.skills if s.name == "Perception")
    asked: list[tuple] = []

    def fake(_parent, _title, label, items, *a, **k):
        asked.append((label, list(items)))
        return ("Rooftops", True)

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(fake))
    sheet.skills._add_specialization(stealth)
    label, items = asked[-1]
    assert "Hiding" in items  # the catalog's own suggestions
    assert label == "By specific environment or terrain"  # …and its guidance as the prompt
    assert sheet.character.specializations["Stealth"] == ["Rooftops"]  # free text still wins

    # A pool already bought is not offered a second time.
    sheet.skills._add_specialization(stealth)
    assert "Rooftops" not in asked[-1][1]

    # A skill with nothing enumerable offers nothing, and asks for it in its own words.
    sheet.skills._add_specialization(perception)
    assert asked[-1] == ("By specific sense, e.g. sight, hearing, smell", [])


def test_cancelling_add_specialization_leaves_the_model_untouched(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled dialog must not seed an empty entry — ``_remove_specialization``
    goes out of its way to keep those out of the model, and one would be saved."""
    from PySide6.QtWidgets import QInputDialog

    data = load_game_data()
    sheet = CharacterSheet(data)
    skill = next(s for s in data.skills if not s.focused)

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    sheet.skills._add_specialization(skill)
    assert skill.name not in sheet.character.specializations

    # An accepted dialog still records the specialization.
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Forgery", True)))
    sheet.skills._add_specialization(skill)
    assert sheet.character.specializations[skill.name] == ["Forgery"]


def test_the_reach_row_is_only_there_once_something_has_moved_it(qapp: QApplication) -> None:
    """Reach is a row that is not there most of the time, caption and all.

    Every character has a reach and almost none of them has an interesting one — the
    baseline is your own Space, which is what a close attack already means — so the row
    appears only once a Growth, a Shrinking or an Elongation has moved it off what the
    bought size gives, and goes away again when they are removed.
    """

    data = load_game_data()
    sheet = CharacterSheet(data)
    system = sheet.system_info
    assert system._reach.isHidden() and system._reach_row_label.isHidden()

    grown = Power(name="Giant", effects=[PowerEffectInstance("growth", rank=3)])
    stretched = Power(name="Long Arms", effects=[PowerEffectInstance("elongation", rank=1)])
    sheet.character.powers.extend([grown, stretched])
    system.refresh_derived()

    assert not system._reach.isHidden() and not system._reach_row_label.isHidden()
    assert system._reach.text() == "~5 spaces / 33 ft."

    sheet.character.powers.clear()
    system.refresh_derived()
    assert system._reach.isHidden() and system._reach_row_label.isHidden()


def test_a_hidden_row_takes_its_whole_form_row_with_it(qapp: QApplication) -> None:
    """Hiding the two widgets left the *row* — spacing and all — behind.

    This block hides four rows (Reach and Movement on almost every sheet, Power Level and
    Hero Points on an NPC), so the leftover bands read as a mis-spaced block rather than
    as a missing row. ``QFormLayout.setRowVisible`` is the API that takes the row too.
    """

    sheet = CharacterSheet(load_game_data())
    system = sheet.system_info
    form = system._form

    row, _role = form.getWidgetPosition(system._reach)
    assert row >= 0
    assert not form.isRowVisible(row)  # nothing has moved this character's reach

    sheet.character.powers.append(
        Power(name="Giant", effects=[PowerEffectInstance("growth", rank=3)])
    )
    system.refresh_derived()
    assert form.isRowVisible(row)


def test_the_limits_row_is_only_there_for_a_cap_the_build_is_past(
    qapp: QApplication,
) -> None:
    """The block owns Power Level, so it owns what Power Level does.

    ``power_level_violations`` has evaluated the paired-resistance caps and the per-skill
    modifier cap all along, and its only caller was a minion's build — so a character
    over Power Level on their own defences got no mark anywhere on the sheet while a
    single power got a warning glyph. A legal build is the ordinary case, though, so the
    row appears only for a cap the build is genuinely past.
    """

    data = load_game_data()
    sheet = CharacterSheet(data)
    system = sheet.system_info
    assert system._limits.rendered_text() == []
    assert system._limits.isHidden()

    # PL 10 -> the paired cap is 20, and a build sitting exactly on it is legal.
    sheet.character.abilities["STA"] = 10
    sheet.character.resistances["DEF"] = 10
    system.refresh_limits()
    assert system._limits.rendered_text() == []
    assert not power_level_violations(sheet.character, data)

    sheet.character.resistances["DEF"] = 12
    system.refresh_limits()
    assert system._limits.rendered_text() == ["Dodge + Toughness 22/20"]
    assert not system._limits.isHidden()
    assert power_level_violations(sheet.character, data)


def test_the_limits_row_follows_the_edits_that_move_it(qapp: QApplication) -> None:
    """Its inputs are scattered across the sheet, so one topic was never enough.

    ``derived-changed`` covers the powers and the conditions and nothing else — so the
    row sat stale through the two edits that move it most directly: typing a Power
    Level, and typing a resistance.
    """

    sheet = CharacterSheet(load_game_data())
    sheet.set_locked(False)
    system = sheet.system_info

    sheet.resistances.findChildren(QSpinBox)[0].setValue(25)  # a real resistance spin
    assert system._limits.rendered_text(), "a resistance edit has to reach the row"

    # ...and raising the Power Level the caps are read off puts it away again.
    system._power_level.setValue(20)
    assert system._limits.rendered_text() == []


def test_the_limits_row_names_the_tightest_skill(qapp: QApplication) -> None:
    """A cap written per row collapses to the row standing closest to it."""

    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.character.abilities["AGL"] = 15
    sheet.character.skill_ranks["Stealth"] = 10  # 25, over the PL 10 cap of 20
    sheet.character.skill_ranks["Athletics"] = 1
    sheet.system_info.refresh_limits()

    assert "Skills (Stealth) 25/20" in sheet.system_info._limits.rendered_text()


def test_an_npc_has_no_limits_row(qapp: QApplication) -> None:
    """Its Power Level is estimated *from* its traits; measuring them back against it
    would be a tautology, not a limit."""

    sheet = CharacterSheet(load_game_data())
    sheet.character.abilities["STA"] = 12
    sheet.character.resistances["DEF"] = 12  # a breach a player sheet would report
    sheet.system_info.refresh_limits()
    assert sheet.system_info._limits.rendered_text()

    sheet.system_info.set_npc_mode(True)
    assert sheet.system_info._limits.rendered_text() == []
    assert sheet.system_info._limits.isHidden()


def test_a_build_over_its_budget_says_so(qapp: QApplication) -> None:
    """``170 / 150`` used to read in the same ink as ``140 / 150``."""

    sheet = CharacterSheet(load_game_data())
    system = sheet.system_info
    system._power_points.setValue(150)

    system.set_pool_current("power_points", 140)
    assert system._pool_current.styleSheet() == ""
    assert "10 left" in system._pool_current.toolTip()

    system.set_pool_current("power_points", 170)
    assert theme.color("tint.warning") in system._pool_current.styleSheet()
    assert "20 over budget" in system._pool_current.toolTip()


def test_a_sixth_hero_point_is_not_destroyed(qapp: QApplication) -> None:
    """Five pips was a cap in Python over a ruleset that allows 99.

    ``set_value`` clamped and ``_on_hero_points_changed`` wrote the clamped number back,
    so a GM granting a sixth hero point did not merely fail to draw it — it was lost.
    """

    sheet = CharacterSheet(load_game_data())
    pips = sheet.system_info._hero_points

    sheet.system_info.set_hero_points(7)
    assert pips.value() == 7
    assert sheet.character.characteristics["hero_points"] == 7

    sheet.system_info.set_hero_points(2)
    assert pips.value() == 2
    assert len(pips._buttons) == 5  # ...and the row settles back to its resting five
