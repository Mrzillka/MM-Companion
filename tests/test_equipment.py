"""The equipment model and its cost engine (Phase 3 of the Equipment feature).

Two things are being proved here, and the second is the one that would fail quietly.

*The model.* An :class:`EquipmentItem` wraps a real
:class:`~mm_companion.core.powers.Power`, so the whole powers rules layer works on
gear unchanged, and a catalog entry builds into that power faithfully.

*The two currencies.* Power Points buy ranks of the Equipment advantage; Equipment
Points buy the items. Neither is ever a term in the other, and the Removable discount
— which the advantage has already paid for — is never applied a second time. Both
failures are silent: the sheet would simply show a wrong total.
"""

from __future__ import annotations

import pytest

from mm_companion.core import storage
from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.equipment import (
    PER_RANK_COST_KINDS,
    PLATFORM_INSTALLATION,
    PLATFORM_VEHICLE,
    EquipmentItem,
    PlatformSpec,
)
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    MISSING_VALUE,
    PIN_EQUIPMENT,
    PinRef,
    accessory_hosts,
    apply_platform,
    attach_accessory,
    available_pins,
    build_item_from_entry,
    detach_accessory,
    effect_effective_rank,
    equipment_advantage_rank,
    equipment_budget,
    equipment_contributions,
    equipment_points_remaining,
    equipment_points_spent,
    equipment_speed_lines,
    equipment_violations,
    estimated_power_level,
    installation_pl_violations,
    installation_size_cost,
    installation_trait_cost,
    installation_trait_rows,
    item_accepts_accessory,
    item_attaches_to,
    item_breakage_warnings,
    item_effective_build,
    item_ep_cost,
    item_granted_advantages,
    item_is_stock,
    item_material_toughness,
    item_own_ep_cost,
    item_platform,
    item_platform_violations,
    item_price_warnings,
    item_rank,
    item_superseded,
    movement_mode_lines,
    new_platform,
    pin_label,
    platform_feature_cost,
    platform_is_stock,
    platform_movement_effect,
    platform_rules_category,
    platform_trait_cost,
    platform_trait_rows,
    power_level_violations,
    power_pl_violations,
    power_points_spent,
    power_rolls,
    power_total_cost,
    resistance_total,
    resolve_pin,
    speed_lines,
    trait_bonuses,
    vehicle_defense_class,
    vehicle_modifier_advantage_cost,
    vehicle_size_row,
    vehicle_stationary_dc,
    vehicle_trait_cost,
    vehicle_trait_rows,
    worn_items,
)


@pytest.fixture
def data():
    return load_game_data()


@pytest.fixture
def hero(data):
    """A blank character with 5 ranks of Equipment — a 25-point kit."""
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    return char


def _item(data, catalog_id: str, *, rank: int = 1) -> EquipmentItem:
    return build_item_from_entry(data.equipment_catalog()[catalog_id], data, rank=rank)


# --- the model -------------------------------------------------------------------------


def test_item_wraps_a_real_power(data) -> None:
    item = _item(data, "axe")
    assert isinstance(item.build, Power)
    assert item.build.name == "Axe"
    assert item.catalog_id == "axe"
    assert item.category == data.equipment_catalog()["axe"].category
    assert item.name == "Axe"


def test_item_round_trips_but_worn_does_not(data) -> None:
    """``worn`` is runtime state, exactly like a power's ``activated``.

    Taking a jacket off is a play action, not a build edit, so it is left out of the
    save and a loaded character comes up wearing everything.
    """
    item = _item(data, "axe")
    item.worn = False
    item.stacks = True
    item.ep_override = 3

    restored = EquipmentItem.from_dict(item.to_dict())

    assert restored.id == item.id
    assert restored.catalog_id == "axe"
    assert restored.build.to_dict() == item.build.to_dict()
    assert restored.stacks is True
    assert restored.ep_override == 3
    assert restored.worn is True  # runtime, not persisted
    assert "worn" not in item.to_dict()


def test_character_round_trips_equipment(data, hero) -> None:
    hero.equipment.append(_item(data, "axe"))
    hero.equipment_group_order = ["armor", "weapon"]

    restored = Character.from_dict(hero.to_dict())

    assert [i.catalog_id for i in restored.equipment] == ["axe"]
    assert restored.equipment[0].build.effects[0].effect_id == "damage"
    assert restored.equipment_group_order == ["armor", "weapon"]


def test_a_save_without_equipment_still_loads(data) -> None:
    """No schema bump: an older save carries neither key and loads unchanged."""
    raw = Character.new_default(data).to_dict()
    assert "equipment" not in raw and "equipment_group_order" not in raw

    restored = Character.from_dict(raw)

    assert restored.equipment == []
    assert restored.equipment_group_order == []


def test_worn_items_filters_on_the_runtime_flag(data, hero) -> None:
    worn, stowed = _item(data, "axe"), _item(data, "crossbow")
    stowed.worn = False
    hero.equipment += [worn, stowed]

    assert [i.catalog_id for i in worn_items(hero)] == ["axe"]


# --- building an item from the catalog --------------------------------------------------


def test_every_catalog_entry_builds_into_a_power(data) -> None:
    """The catalog's refs all resolve, so no item silently builds into nothing."""
    effects = {e.id for e in data.effects}
    modifiers = set(data.modifier_catalog())

    for entry in data.equipment:
        item = build_item_from_entry(entry, data)
        assert len(item.build.effects) == len(entry.effects)
        for instance in item.build.effects:
            assert instance.effect_id in effects
            assert instance.rank >= 1
            for selection in (*instance.extras, *instance.flaws):
                assert selection.modifier_id in modifiers


def test_strength_based_becomes_the_ability_folding_modifier(data) -> None:
    """The catalog's ``strengthBased`` flag ticks the effect's own checkbox.

    Found through the data — a config field that ``toggles`` a modifier which
    ``adds_ability`` — so nothing here names Strength or Damage.
    """
    axe = _item(data, "axe")
    effect = axe.build.effects[0]

    assert [s.modifier_id for s in effect.extras] == ["strength_based"]
    assert effect.config["strengthBased"] is True


def test_printed_qualities_land_in_the_effect_config(data) -> None:
    """An Affliction's resistance and its degree ladder come off the printed line.

    Which config key holds each degree is declared by the effect's own
    ``resistanceOutcomes``, not named here.
    """
    spray = _item(data, "pepper_spray").build.effects[0]

    assert spray.config["resistance"] == "Fortitude"
    assert spray.config["degree1"] == ["disabled"]
    assert spray.config["degree2"] == ["unaware"]
    assert spray.config["configuration"] == "dazzle"
    assert spray.descriptors == ["chemical"]


def test_entry_modifiers_split_into_extras_and_flaws(data) -> None:
    """Which bucket a printed modifier lands in comes off its own record's category."""
    blowgun = _item(data, "blowgun").build.effects[0]

    assert {s.modifier_id for s in blowgun.extras} == {"ranged"}
    assert {s.modifier_id for s in blowgun.flaws} == {"diminished_range", "resistible"}


def test_a_purchased_rank_reaches_the_open_ranked_effect(data) -> None:
    """Armored Costume prints no rank — the player buys it."""
    assert _item(data, "armored_costume", rank=7).build.effects[0].rank == 7
    assert _item(data, "armored_costume").build.effects[0].rank == 1
    # A ref that *does* print a rank keeps it, whatever is passed.
    assert _item(data, "axe", rank=7).build.effects[0].rank == 3


# --- the Removable discount is never reapplied -------------------------------------------


def test_the_removable_flaw_is_never_built_onto_an_item(data) -> None:
    """Omni-Equipment is the one entry that legitimately prints Removable.

    An item is already removable — that is what equipment *is* — and the Equipment
    advantage already paid for the discount. Charging the flaw again would make every
    affected item quietly cheap.
    """
    catalog = data.modifier_catalog()
    assert any(
        catalog[ref.modifier].gate == "removable"
        for ref in data.equipment_catalog()["omni_equipment"].modifiers
    )

    for entry in data.equipment:
        item = build_item_from_entry(entry, data)
        for instance in item.build.effects:
            gates = {catalog[s.modifier_id].gate for s in (*instance.extras, *instance.flaws)}
            assert "removable" not in gates


def test_a_hand_edited_removable_is_stripped_before_pricing(data, hero) -> None:
    """Belt and braces: a build that carries the flaw anyway is priced without it."""
    plain = EquipmentItem(
        build=Power(name="Blaster", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )
    discounted = EquipmentItem(
        build=Power(
            name="Blaster",
            effects=[
                PowerEffectInstance(
                    effect_id="damage",
                    rank=8,
                    flaws=[ModifierSelection(modifier_id="removable")],
                )
            ],
        )
    )

    assert item_ep_cost(discounted, data, hero) == item_ep_cost(plain, data, hero) == 8


# --- what an item costs -------------------------------------------------------------------


def test_a_stock_item_costs_exactly_what_the_catalog_prints(data, hero) -> None:
    for entry in data.equipment:
        if entry.cost is None or entry.cost_kind in PER_RANK_COST_KINDS:
            continue
        item = build_item_from_entry(entry, data)
        assert item_is_stock(item, data)
        assert item_ep_cost(item, data, hero) == entry.cost, entry.id


def test_a_per_rank_item_multiplies_its_printed_price(data, hero) -> None:
    entry = data.equipment_catalog()["armored_costume"]
    assert entry.cost_kind in PER_RANK_COST_KINDS

    item = build_item_from_entry(entry, data, rank=6)

    assert item_rank(item) == 6
    assert item_ep_cost(item, data, hero) == entry.cost * 6


def test_an_edited_item_is_priced_from_its_effects(data, hero) -> None:
    """Once the build diverges from the catalog, the printed price no longer applies."""
    item = _item(data, "axe")
    assert item_ep_cost(item, data, hero) == 5

    item.build.effects[0].rank = 9

    assert not item_is_stock(item, data)
    assert item_ep_cost(item, data, hero) > 5


def test_a_custom_item_is_never_stock(data, hero) -> None:
    custom = EquipmentItem(
        build=Power(name="Ray Gun", effects=[PowerEffectInstance(effect_id="damage", rank=4)])
    )

    assert not item_is_stock(custom, data)
    assert item_ep_cost(custom, data, hero) == 4


def test_ep_override_replaces_the_derived_price(data, hero) -> None:
    item = _item(data, "axe")
    item.ep_override = 2

    assert item_ep_cost(item, data, hero) == 2


# --- the budget ---------------------------------------------------------------------------


def test_budget_is_advantage_rank_times_the_ruleset_rate(data, hero) -> None:
    assert equipment_advantage_rank(hero, data) == 5
    assert equipment_budget(hero, data) == 5 * data.equipment_rules.points_per_advantage_rank


def test_no_equipment_advantage_is_a_zero_budget(data) -> None:
    assert equipment_budget(Character.new_default(data), data) == 0


def test_spend_counts_stowed_gear_too(data, hero) -> None:
    """``worn`` is runtime; the price is build state. Taking it off is not a refund."""
    axe, bow = _item(data, "axe"), _item(data, "crossbow")
    bow.worn = False
    hero.equipment += [axe, bow]

    expected = item_ep_cost(axe, data, hero) + item_ep_cost(bow, data, hero)
    assert equipment_points_spent(hero, data) == expected
    assert equipment_points_remaining(hero, data) == 25 - expected


def test_overspend_warns_and_names_both_numbers(data, hero) -> None:
    hero.equipment = [_item(data, "armored_costume", rank=30)]

    violations = equipment_violations(hero, data)

    assert len(violations) == 1
    assert "30" in violations[0] and "25" in violations[0]
    assert equipment_points_remaining(hero, data) < 0


def test_gear_without_the_advantage_is_a_violation(data) -> None:
    char = Character.new_default(data)
    char.equipment = [_item(data, "axe")]

    assert equipment_violations(char, data)


def test_no_gear_is_never_a_violation(data) -> None:
    assert equipment_violations(Character.new_default(data), data) == []


def test_equipment_enforcement_is_its_own_seam() -> None:
    assert storage.equipment_enforcement() == storage.EQUIPMENT_ENFORCE_WARN
    storage.update_settings(equipment_enforcement=storage.EQUIPMENT_ENFORCE_BLOCK)
    assert storage.equipment_enforcement() == storage.EQUIPMENT_ENFORCE_BLOCK
    storage.update_settings(equipment_enforcement="nonsense")
    assert storage.equipment_enforcement() == storage.EQUIPMENT_ENFORCE_WARN


# --- the two currencies never mix -----------------------------------------------------------


def test_equipment_never_reaches_the_power_point_total(data, hero) -> None:
    before = power_points_spent(hero, data)

    hero.equipment = [_item(data, "armored_costume", rank=8), _item(data, "axe")]

    assert power_points_spent(hero, data) == before
    assert equipment_points_spent(hero, data) > 0


def test_power_points_never_pay_for_gear(data, hero) -> None:
    """A power raising Toughness costs PP and nothing in EP."""
    hero.powers.append(
        Power(name="Force Field", effects=[PowerEffectInstance(effect_id="protection", rank=6)])
    )

    assert equipment_points_spent(hero, data) == 0
    assert power_points_spent(hero, data) > equipment_advantage_rank(hero, data)


# --- what worn gear contributes ---------------------------------------------------------


def test_worn_armour_raises_toughness(data, hero) -> None:
    hero.equipment = [_item(data, "armored_costume", rank=6)]

    assert resistance_total(hero, data, "TOUGHNESS") == 6
    assert equipment_contributions(hero, data)


def test_stowed_armour_contributes_nothing(data, hero) -> None:
    item = _item(data, "armored_costume", rank=6)
    item.worn = False
    hero.equipment = [item]

    assert equipment_contributions(hero, data) == ()
    assert resistance_total(hero, data, "TOUGHNESS") == 0


def test_two_pieces_of_armour_do_not_stack(data, hero) -> None:
    """Only the best applies, and the card is told what beat it."""
    best = _item(data, "armored_costume", rank=6)
    worse = _item(data, "armored_costume", rank=4)
    worse.build.name = "Flak Vest"
    hero.equipment = [best, worse]

    assert resistance_total(hero, data, "TOUGHNESS") == 6
    bonus = trait_bonuses(hero, data)["resistance"]["TOUGHNESS"]
    assert [s.source for s in bonus.superseded] == ["Flak Vest"]
    assert bonus.superseded[0].beaten_by == "Armored Costume"


def test_of_two_identical_items_only_the_loser_says_so(data, hero) -> None:
    """Identity, not resemblance — the winner must not disown the bonus it granted.

    Two copies of one item share a name and an amount, which is what an earlier
    ``(source, amount)`` match could not tell apart: *both* cards claimed to have been
    superseded, and the +1 actually on the sheet was owned by neither.
    """
    first = _item(data, "leather_armor")
    second = _item(data, "leather_armor")
    hero.equipment = [first, second]

    reports = [item_superseded(i, hero, data) for i in (first, second)]
    assert sum(1 for r in reports if r) == 1, "exactly one of the pair lost"
    assert resistance_total(hero, data, "TOUGHNESS") == 1
    loser = next(r for r in reports if r)
    assert loser[0].stat == "TOUGHNESS"
    assert loser[0].beaten_by == "Leather"


def test_a_genuinely_outclassed_item_names_what_beat_it(data, hero) -> None:
    weak = _item(data, "leather_armor")
    strong = _item(data, "chain_mail")
    hero.equipment = [weak, strong]

    assert item_superseded(strong, hero, data) == ()
    beaten = item_superseded(weak, hero, data)
    assert [(b.stat, b.beaten_by) for b in beaten] == [("TOUGHNESS", "Chain-Mail")]


def test_the_stacks_homerule_opts_one_item_back_in(data, hero) -> None:
    best = _item(data, "armored_costume", rank=6)
    extra = _item(data, "armored_costume", rank=4)
    extra.build.name = "Underlayer"
    extra.stacks = True
    hero.equipment = [best, extra]

    assert resistance_total(hero, data, "TOUGHNESS") == 10


def test_gear_does_not_stack_with_a_power_either(data, hero) -> None:
    """M&M grants the better of the two, never their sum."""
    hero.powers.append(
        Power(name="Force Field", effects=[PowerEffectInstance(effect_id="protection", rank=8)])
    )
    hero.equipment = [_item(data, "armored_costume", rank=6)]

    assert resistance_total(hero, data, "TOUGHNESS") == 8

    hero.powers.clear()

    assert resistance_total(hero, data, "TOUGHNESS") == 6


def test_a_sheet_without_gear_is_untouched(data, hero) -> None:
    """The guard on Phase 3: no equipment, no change to any derived number."""
    hero.powers.append(
        Power(name="Force Field", effects=[PowerEffectInstance(effect_id="protection", rank=4)])
    )

    assert equipment_contributions(hero, data) == ()
    assert resistance_total(hero, data, "TOUGHNESS") == 4


# --- rolling and derived wiring (Phase 6) ----------------------------------------------


def test_a_weapon_rolls_like_an_attack_power(data, hero) -> None:
    """The whole point of the wrapping model: ``power_rolls`` takes ``item.build`` as-is."""
    hero.abilities["ATK"] = 4
    item = _item(data, "axe")

    specs = power_rolls(item.build, hero, data)

    attack, save = specs
    assert attack.modifier == 4 and attack.dc is None and not attack.rolled_by_target
    # The wielder never makes their own target's save — it travels as the follow-up.
    assert save.rolled_by_target and save.dc is not None
    assert attack.follow_up is not None and attack.follow_up.dc == save.dc


def test_gear_that_rolls_nothing_has_no_rolls(data, hero) -> None:
    assert power_rolls(_item(data, "antitoxin").build, hero, data) == []


def test_a_pin_reads_one_of_an_items_rolls(data, hero) -> None:
    hero.abilities["ATK"] = 4
    item = _item(data, "axe")
    hero.equipment = [item]

    value = resolve_pin(hero, data, PinRef(PIN_EQUIPMENT, item.id, 0))

    assert value.label == "Axe"
    assert value.value == "+4"
    assert value.spec is not None and not value.missing


def test_an_items_forced_save_pins_as_a_difficulty_nobody_rolls(data, hero) -> None:
    item = _item(data, "axe")
    hero.equipment = [item]

    value = resolve_pin(hero, data, PinRef(PIN_EQUIPMENT, item.id, 1))

    assert value.value.startswith("DC ")
    assert value.spec is None  # a difficulty is read, not thrown
    assert not value.missing


def test_a_pin_names_the_item_not_its_build(data, hero) -> None:
    """The resolved ref must find the item again, and gear is indexed by its own id."""
    item = _item(data, "axe")
    hero.equipment = [item]

    resolved = resolve_pin(hero, data, PinRef(PIN_EQUIPMENT, item.id, 0)).ref

    assert resolved.key == item.id != item.build.id
    assert resolve_pin(hero, data, resolved).missing is False


def test_a_pin_to_gear_that_was_sold_reads_as_a_dash(data, hero) -> None:
    value = resolve_pin(hero, data, PinRef(PIN_EQUIPMENT, "gone", 0))

    assert value.missing and value.value == MISSING_VALUE
    assert pin_label(PinRef(PIN_EQUIPMENT, "gone"), data) == "Equipment"


def test_a_stowed_item_still_resolves_its_pin(data, hero) -> None:
    """Wearing is a runtime flag a GM flips constantly; a chip must not empty with it."""
    item = _item(data, "axe")
    item.worn = False
    hero.equipment = [item]

    assert not resolve_pin(hero, data, PinRef(PIN_EQUIPMENT, item.id, 0)).missing


def test_the_picker_offers_gear_that_rolls_and_nothing_else(data, hero) -> None:
    hero.equipment = [_item(data, "axe"), _item(data, "antitoxin")]

    groups = {group.title: group for group in available_pins(hero, data)}

    assert [v.label for v in groups["Equipment"].values] == ["Axe", "Axe"]


def test_the_picker_has_no_equipment_group_without_gear(data, hero) -> None:
    assert "Equipment" not in {group.title for group in available_pins(hero, data)}


def test_worn_gear_reaches_the_speed_readout(data, hero) -> None:
    """A glider flies, and the Speed block should say so — named by the *item*."""
    hero.equipment = [_item(data, "glider")]

    lines = equipment_speed_lines(hero, data)

    assert [line.label for line in lines] == ["Glider 6"]
    # ...and the sheet's own readout is the base line plus that one.
    assert [line.label for line in speed_lines(hero, data)][1:] == ["Glider 6"]


def test_the_base_ground_line_stays_first(data, hero) -> None:
    """What lets the condition overlay keep landing on ``lines[0]``."""
    hero.equipment = [_item(data, "glider")]

    assert speed_lines(hero, data)[0].label == "Base"


def test_stowed_gear_grants_no_speed(data, hero) -> None:
    item = _item(data, "glider")
    item.worn = False
    hero.equipment = [item]

    assert equipment_speed_lines(hero, data) == []


def test_worn_gear_grants_movement_modes_too(data, hero) -> None:
    """Gear-granted Swinging shows beside gear-granted Flight, named by the capability."""
    item = _item(data, "swing_line")
    effect = item.build.effects[0]
    effect.rank = 2
    effect.config["modes"] = [{"id": "swinging", "tier": 1}]
    hero.equipment = [item]

    assert [line.label for line in movement_mode_lines(hero, data)] == ["Swinging"]

    item.worn = False

    assert movement_mode_lines(hero, data) == []


def test_an_item_bought_with_power_points_costs_no_equipment_points(data, hero) -> None:
    """Omni-Equipment is a Variable power priced at 2 PP; it used to cost 7 EP.

    The two-currencies trap in its quietest direction — nothing raises, the budget bar
    is simply wrong. The entry states the Power Point price itself, so the rule is data.
    """
    item = _item(data, "omni_equipment")
    hero.equipment = [item]

    assert item_own_ep_cost(item, data) == 0
    assert equipment_points_spent(hero, data) == 0


def test_an_unpriceable_built_item_warns_rather_than_being_silently_free(data) -> None:
    """Trick Arrows has no printed price and nothing in its build to derive one from.

    Its array lives in ``implementation.alternates``, whose ranks and DCs the catalog
    never states, so there is nothing to price. It stays free — and says so, which is
    the whole difference: a free item otherwise looks exactly like a correct one.
    """
    item = _item(data, "trick_arrows")

    assert item_own_ep_cost(item, data) == 0
    assert item_price_warnings(item, data)


def test_a_priced_item_carries_no_price_warning(data) -> None:
    assert item_price_warnings(_item(data, "utility_kit"), data) == []
    assert item_own_ep_cost(_item(data, "utility_kit"), data) == 25
    assert item_price_warnings(_item(data, "leather_armor"), data) == []


def test_a_hand_set_price_settles_an_unpriceable_item(data) -> None:
    """``ep_override`` is the seam the warning points at."""
    item = _item(data, "trick_arrows")
    item.ep_override = 12

    assert item_own_ep_cost(item, data) == 12
    assert item_price_warnings(item, data) == []


def test_the_shipped_movement_gear_works_without_being_hand_configured(data, hero) -> None:
    """Straight off the catalog, no fixture surgery.

    The test above rewrites the swing line's allocation config before asserting, which
    is how the shipped data managed to spell the option ``"mode"`` — where every reader
    looks for ``"id"`` — through eleven phases without a single failure. Three items
    were bought, worn and drawn while granting nothing.
    """
    hero.equipment = [_item(data, "swing_line"), _item(data, "climbing_cable")]

    labels = [line.label for line in movement_mode_lines(hero, data)]
    assert "Swinging" in labels
    assert any(label.startswith("Wall-Crawling") for label in labels)


def test_a_shield_raises_the_defence_it_is_bought_as(data, hero) -> None:
    """``combat.defense`` is design vocabulary; ``DEF`` is the trait key.

    All three shields named the former, so :func:`trait_category` matched no list and
    the applier declined — a shield on the sheet moved no number at all.
    """
    hero.equipment = [_item(data, "large_shield")]

    assert resistance_total(hero, data, "DEF") == 3
    assert equipment_contributions(hero, data)


def test_a_shield_still_costs_the_book_price(data) -> None:
    """And its derived price agrees, so editing one moves no number either."""
    item = _item(data, "large_shield")
    assert item_own_ep_cost(item, data) == 6
    assert item_is_stock(item, data)


def test_a_weapons_attack_counts_toward_the_estimated_power_level(data) -> None:
    """A mook whose whole threat is its rifle used to estimate at 0."""
    char = Character()
    char.abilities["ATK"] = 6
    assert estimated_power_level(char, data) == 0

    char.equipment = [_item(data, "axe")]

    assert estimated_power_level(char, data) > 0


def test_a_sheathed_weapon_still_counts_toward_power_level(data) -> None:
    """A PL cap is a statement about the build; sheathing a sword validates nothing."""
    char = Character()
    char.abilities["ATK"] = 6
    char.equipment = [_item(data, "axe")]
    armed = estimated_power_level(char, data)

    char.equipment[0].worn = False

    assert estimated_power_level(char, data) == armed


def test_armour_toughness_counts_against_the_paired_cap(data, hero) -> None:
    """Gear does not buy its way out of Power Level (design doc §2)."""
    hero.power_level = 1
    assert power_level_violations(hero, data) == []

    hero.equipment = [_item(data, "armored_costume", rank=10)]

    assert any("Toughness" in message for message in power_level_violations(hero, data))


def test_an_over_ranked_weapon_marks_its_own_card(data, hero) -> None:
    """The per-build cap is the same seam a power card reads."""
    hero.power_level = 1
    hero.abilities["ATK"] = 10
    item = _item(data, "axe")

    assert power_pl_violations(item.build, hero, data)


# --- Phase 8: accessories --------------------------------------------------------------


def test_an_accessory_keeps_its_modifiers_off_its_own_build(data) -> None:
    """A laser sight's Accurate belongs to the rifle, so it is held, not built in.

    An accessory has no effects of its own for a modifier to hang on, and the old
    behaviour dropped them on the floor: the item cost its printed point and did
    nothing at all.
    """
    sight = _item(data, "laser_sight")

    assert sight.build.effects == []
    assert [m.modifier_id for m in sight.attachment] == ["accurate"]
    assert item_attaches_to(sight, data) == ("ranged_weapon",)


def test_an_accessory_only_fits_what_it_says_it_fits(data) -> None:
    sight = _item(data, "laser_sight")

    assert item_accepts_accessory(_item(data, "rifle"), sight, data)
    assert not item_accepts_accessory(_item(data, "sword"), sight, data)
    # Not onto another accessory (a chain with no weapon on the end), and not itself.
    assert not item_accepts_accessory(_item(data, "suppressor"), sight, data)
    assert not item_accepts_accessory(sight, sight, data)
    # A weapon is not an accessory, however much it would like to be one.
    assert not item_accepts_accessory(_item(data, "rifle"), _item(data, "sword"), data)


def test_fitting_an_accessory_moves_it_onto_its_host(data, hero) -> None:
    rifle, sight = _item(data, "rifle"), _item(data, "laser_sight")
    hero.equipment = [rifle, sight]

    assert [h.name for h in accessory_hosts(hero, sight, data)] == ["Rifle"]
    assert attach_accessory(hero, rifle, sight, data)

    assert hero.equipment == [rifle]
    assert rifle.accessories == [sight]


def test_a_fitted_accessory_lends_its_modifiers_to_the_host(data, hero) -> None:
    """The rifle rolls +2 — and its own stored build is untouched, so it is still stock."""
    rifle, sight = _item(data, "rifle"), _item(data, "laser_sight")
    hero.equipment = [rifle, sight]
    bare = power_rolls(rifle.build, hero, data)[0].modifier

    attach_accessory(hero, rifle, sight, data)
    fitted = power_rolls(item_effective_build(rifle, data), hero, data)[0].modifier

    assert fitted == bare + 2
    assert rifle.build.effects[0].extras == _item(data, "rifle").build.effects[0].extras
    assert item_is_stock(rifle, data)


def test_a_host_with_nothing_fitted_hands_back_its_own_build(data) -> None:
    """The usual case keeps the object identity every caller already had."""
    rifle = _item(data, "rifle")

    assert item_effective_build(rifle, data) is rifle.build


def test_a_fitted_accessory_is_paid_for_exactly_once(data, hero) -> None:
    """The budget walks the loose list, so a folded-in price is the only way it counts."""
    rifle, sight = _item(data, "rifle"), _item(data, "laser_sight")
    hero.equipment = [rifle, sight]
    loose = equipment_points_spent(hero, data)

    attach_accessory(hero, rifle, sight, data)

    assert equipment_points_spent(hero, data) == loose
    assert item_own_ep_cost(rifle, data) == data.equipment_catalog()["rifle"].cost
    assert item_ep_cost(rifle, data) == item_own_ep_cost(rifle, data) + 1


def test_taking_an_accessory_off_is_lossless(data, hero) -> None:
    rifle, sight = _item(data, "rifle"), _item(data, "laser_sight")
    hero.equipment = [rifle, sight]
    attack = power_rolls(rifle.build, hero, data)[0].modifier
    attach_accessory(hero, rifle, sight, data)

    assert detach_accessory(hero, rifle, sight)

    assert hero.equipment == [rifle, sight]
    assert rifle.accessories == []
    assert power_rolls(item_effective_build(rifle, data), hero, data)[0].modifier == attack


def test_a_fitted_accessory_round_trips_through_a_save(data, hero) -> None:
    rifle, sight = _item(data, "rifle"), _item(data, "laser_sight")
    hero.equipment = [rifle, sight]
    attach_accessory(hero, rifle, sight, data)

    reloaded = Character.from_dict(hero.to_dict())

    assert len(reloaded.equipment) == 1
    fitted = reloaded.equipment[0].accessories
    assert [a.catalog_id for a in fitted] == ["laser_sight"]
    assert [m.modifier_id for m in fitted[0].attachment] == ["accurate"]
    assert item_ep_cost(reloaded.equipment[0], data) == item_ep_cost(rifle, data)


def test_an_ordinary_item_serializes_exactly_as_it_did(data) -> None:
    """The three new fields are written only when they say something."""
    stored = _item(data, "sword").to_dict()

    assert "accessories" not in stored
    assert "attaches_to" not in stored
    assert "attachment" not in stored


def test_granted_advantages_are_scoped_to_the_item(data, hero) -> None:
    """A weapon's advantages are a trait of the weapon, never of the character."""
    axe = _item(data, "axe")

    granted = item_granted_advantages(axe, data)

    assert [g.name for g in granted] == ["Improved Critical", "Improved Smash"]
    assert {g.source for g in granted} == {"Axe"}
    # And nothing has quietly joined the character's own advantage list.
    assert [s.name for s in hero.advantages] == ["Equipment"]


def test_an_accessory_grant_is_named_after_the_accessory(data, hero) -> None:
    """The scope's Improved Aim is drawn on the rifle but is still the scope's."""
    rifle, scope = _item(data, "rifle"), _item(data, "targeting_scope")
    hero.equipment = [rifle, scope]
    attach_accessory(hero, rifle, scope, data)

    granted = item_granted_advantages(rifle, data)

    assert [(g.name, g.source) for g in granted] == [("Improved Aim", "Targeting Scope")]


# --- Phase 8: the weapon-Toughness warning ---------------------------------------------


def test_a_weapon_carries_strength_up_to_its_material_toughness(data, hero) -> None:
    sword = _item(data, "sword")
    hero.abilities["STR"] = 7

    assert item_material_toughness(sword, data) == 7
    assert item_breakage_warnings(sword, hero, data) == []

    hero.abilities["STR"] = 8

    (warning,) = item_breakage_warnings(sword, hero, data)
    assert "Strength 8" in warning and "Toughness 7" in warning and "break" in warning


def test_a_wooden_weapon_breaks_sooner_than_a_metal_one(data, hero) -> None:
    """The number is the material's, from the data — nothing here knows what a staff is."""
    hero.abilities["STR"] = 6

    assert item_breakage_warnings(_item(data, "staff"), hero, data)
    assert item_breakage_warnings(_item(data, "sword"), hero, data) == []


def test_breakage_warns_and_never_clamps(data, hero) -> None:
    """The break is an event at the table, so the bonus is still the whole bonus."""
    sword = _item(data, "sword")
    hero.abilities["STR"] = 12

    assert item_breakage_warnings(sword, hero, data)
    assert (
        effect_effective_rank(sword.build.effects[0], data, hero)
        == sword.build.effects[0].rank + 12
    )


def test_an_item_with_no_material_is_never_warned_about(data, hero) -> None:
    """A firearm carries no Strength, and a custom item has no entry to read."""
    hero.abilities["STR"] = 20

    assert item_breakage_warnings(_item(data, "rifle"), hero, data) == []
    assert item_material_toughness(EquipmentItem(), data) is None
    assert item_breakage_warnings(EquipmentItem(), hero, data) == []


# --- vehicles: a platform, not a bundle of effects --------------------------------------


def test_a_vehicles_traits_cost_what_the_size_table_says(data) -> None:
    """Size, plus every point of Strength/Toughness above the baseline it set.

    A motorcycle is size 0 with the size-0 Strength, so all it pays for is the two
    points of Toughness above baseline 5.
    """
    motorcycle = data.vehicle_catalog()["motorcycle"]
    assert vehicle_trait_cost(motorcycle, data) == 2

    # A jumbo jet: 4 for size, +6 Strength, +4 Toughness, nothing bought off Defense.
    assert vehicle_trait_cost(data.vehicle_catalog()["jumbo_jet"], data) == 14


def test_traits_plus_the_movement_effect_are_the_printed_price(data) -> None:
    """The split is the whole reason Speed is an effect rather than a sixth trait.

    A jumbo jet's 14 points of platform plus Flight 9 at 2 points a rank is its
    printed 32 — which is what says the model matches the book's own build.
    """
    jet = _item(data, "jumbo_jet")
    assert vehicle_trait_cost(data.vehicle_catalog()["jumbo_jet"], data) == 14
    assert power_total_cost(jet.build, data) == 18  # Flight 9, at 2 a rank
    assert item_ep_cost(jet, data) == 32


def test_a_stock_vehicle_is_priced_at_its_printed_number(data) -> None:
    """The derived arithmetic is the fallback, never an override of the table."""
    tank = _item(data, "tank")
    assert item_is_stock(tank, data)
    assert item_ep_cost(tank, data) == 76


def test_a_vehicle_with_no_printed_price_derives_traits_plus_movement(data) -> None:
    """The dimension hopper's own line says "6 + movement effect cost"; so does this."""
    hopper = _item(data, "dimension_hopper")
    assert data.vehicle_catalog()["dimension_hopper"].cost is None
    assert vehicle_trait_cost(data.vehicle_catalog()["dimension_hopper"], data) == 6
    assert item_ep_cost(hopper, data) == 12


def test_the_size_table_extends_arithmetically_past_its_last_row(data) -> None:
    """The book stops at 5 and says +2 STR / +1 TOU / −1 DEF a rank after that."""
    five = vehicle_size_row(5, data)
    seven = vehicle_size_row(7, data)
    assert (five.strength, five.toughness, five.defense) == (12, 10, -5)
    assert (seven.strength, seven.toughness, seven.defense) == (16, 12, -7)
    # And a rank below the table clamps to its first row rather than extrapolating down.
    assert vehicle_size_row(-3, data).size_rank == 0


def test_a_moving_vehicle_is_a_harder_target_than_a_parked_one(data) -> None:
    """The one defence in the app that is dynamic per round."""
    tank = data.vehicle_catalog()["tank"]
    assert vehicle_defense_class(tank, data, tank.speed) == 14  # 10 + 6 − 2
    assert vehicle_stationary_dc(tank, data) == 8  # 10 − 2


def test_a_vehicles_speed_reaches_the_speed_readout(data, hero) -> None:
    """A boat swims and a jet flies — the class axis, arriving on the sheet.

    Nothing in the movement layer knows what a vehicle is: its Speed became a real
    movement effect when the catalog entry was built, so the existing gear seam picks
    it up.
    """
    hero.equipment.append(_item(data, "jumbo_jet"))

    labels = [line.label for line in equipment_speed_lines(hero, data)]
    assert labels == ["Jumbo Jet 9"]
    assert any(line.label == "Jumbo Jet 9" for line in speed_lines(hero, data))


def test_a_parked_vehicle_moves_nobody(data, hero) -> None:
    """Stowing is the same runtime gate every other piece of gear has."""
    jet = _item(data, "jumbo_jet")
    hero.equipment.append(jet)
    jet.worn = False

    assert equipment_speed_lines(hero, data) == []


def test_a_vehicles_weapons_roll_under_their_own_names(data, hero) -> None:
    """Two Damage effects on one card, and a footer that says which is which."""
    tank = _item(data, "tank")

    labels = [spec.label for spec in power_rolls(tank.build, hero, data)]
    assert any(label.startswith("Cannon:") for label in labels)
    assert any(label.startswith("Heavy machine gun:") for label in labels)


def test_a_weapons_own_modifiers_land_on_that_weapon_alone(data, hero) -> None:
    """The cannon is Area; the machine gun is Multiattack. Neither is the tank's."""
    tank = _item(data, "tank")
    cannon, machine_gun = tank.build.effects[1], tank.build.effects[2]

    assert {m.modifier_id for m in cannon.extras} == {"ranged", "area_effect"}
    assert next(m for m in cannon.extras if m.modifier_id == "area_effect").rank == 6
    assert {m.modifier_id for m in machine_gun.extras} == {"ranged", "multiattack"}


def test_the_trait_rows_name_the_baselines_the_build_paid_to_beat(data) -> None:
    rows = {row.key: row for row in vehicle_trait_rows(_item(data, "tank"), data)}

    assert rows["strength"].value == "10" and rows["strength"].base == "6"
    assert rows["strength"].change == "better"
    assert rows["toughness"].value == "12 (Impervious 4)"
    assert rows["defense"].value == "-2" and rows["defense"].change == ""
    assert rows["speed"].value == "Speed 6"
    assert rows["defense_class"].value == "14 moving / 8 stationary"
    assert rows["vehicle_class"].value == "Ground"


def test_only_a_vehicle_has_trait_rows(data) -> None:
    """Which is how a card asks the question at all."""
    assert vehicle_trait_rows(_item(data, "sword"), data) == []
    assert vehicle_trait_rows(EquipmentItem(), data) == []


def test_a_vehicle_modifier_prices_the_advantage_not_the_gear(data) -> None:
    """Durable and Summonable add Power Points to the ranks funding the vehicle.

    The number is deliberately in the *other* currency — an Equipment Point answer
    here would be the two-currency leak this layer exists to prevent.
    """
    assert vehicle_modifier_advantage_cost(["durable"], 4, data) == 4
    assert vehicle_modifier_advantage_cost(["durable", "minion"], 4, data) == 0
    assert vehicle_modifier_advantage_cost(["nonesuch"], 4, data) == 0


def test_owning_a_vehicle_never_touches_the_power_point_pool(data, hero) -> None:
    """The one failure that would not raise: an Equipment Point spent as a Power Point."""
    before = power_points_spent(hero, data)
    hero.equipment.append(_item(data, "tank"))

    assert power_points_spent(hero, data) == before
    assert equipment_points_spent(hero, data) == 76


# --- custom platforms: the same shape, printed or built ---------------------------------


def _custom(kind: str, data) -> EquipmentItem:
    """A blank platform of *kind*, the way the block's Create Platform button makes one."""
    spec = new_platform(kind, data)
    item = EquipmentItem(category=platform_rules_category(kind, data), platform=spec)
    apply_platform(item, spec, data)
    return item


def test_a_printed_platform_and_a_built_one_are_one_shape(data) -> None:
    """The decision the phase turns on: ``item_platform`` normalises both.

    A stock vehicle's traits come off its printed record and a custom one's off the
    item, and nothing downstream can tell — which is why there is no custom-platform
    branch in the cost engine, the card, or the picker.
    """
    printed = item_platform(_item(data, "tank"), data)
    built = item_platform(_custom(PLATFORM_VEHICLE, data), data)

    assert isinstance(printed, PlatformSpec) and isinstance(built, PlatformSpec)
    assert printed.kind == built.kind == PLATFORM_VEHICLE
    assert printed.strength == 10 and printed.defenses[0].modifier_id == "impervious"
    assert item_platform(_item(data, "sword"), data) is None


def test_a_new_vehicle_starts_at_the_baselines_its_size_gives(data) -> None:
    """Size is chosen first, so a blank platform opens at what size 0 already confers."""
    spec = new_platform(PLATFORM_VEHICLE, data)
    baseline = vehicle_size_row(0, data)

    assert (spec.strength, spec.toughness, spec.defense_modifier) == (
        baseline.strength,
        baseline.toughness,
        baseline.defense,
    )
    assert vehicle_trait_cost(spec, data) == 0


def test_a_custom_vehicle_is_priced_off_the_table_plus_its_movement(data, hero) -> None:
    """The two halves the §5 table splits into, and the one that is *not* a trait.

    Traits are bought off the table; Speed is a movement effect and is priced through
    the build like any other effect. A price computed from either half alone is the
    bug this split exists to prevent.
    """
    item = _custom(PLATFORM_VEHICLE, data)
    spec = item.platform
    spec.size, spec.strength, spec.toughness, spec.defense_modifier = 2, 10, 12, -2
    spec.speed = 6
    apply_platform(item, spec, data)

    assert vehicle_trait_cost(spec, data) == 11  # 2 size + 4 Strength + 5 Toughness
    assert platform_trait_cost(spec, data) == 11  # no Features yet
    assert item_ep_cost(item, data, hero) == 11 + 6  # Speed 6 at one point a rank


def test_a_platforms_speed_is_a_real_effect_on_its_build(data, hero) -> None:
    """Which is what makes it reach the Speed readout and cost what movement costs.

    ``apply_platform`` is the one writer that keeps the two in step: the trait lives on
    the spec, the effect lives on the build, and nothing else in the app has to know
    that a vehicle's Speed is spelled twice.
    """
    item = _custom(PLATFORM_VEHICLE, data)
    item.build.name = "Hover Bike"
    spec = item.platform
    spec.vehicle_class, spec.speed = "air", 8
    apply_platform(item, spec, data)

    assert [(e.effect_id, e.rank) for e in item.build.effects] == [("flight", 8)]
    hero.equipment.append(item)
    assert [line.label for line in equipment_speed_lines(hero, data)] == ["Hover Bike 8"]

    spec.speed = 3
    apply_platform(item, spec, data)
    assert [(e.effect_id, e.rank) for e in item.build.effects] == [("flight", 3)]

    spec.speed = None
    apply_platform(item, spec, data)
    assert item.build.effects == []


def test_re_ranking_a_movement_effect_keeps_what_hangs_off_it(data) -> None:
    """A trip through the editor must not quietly strip an Enhanced Movement's mode."""
    hopper = _item(data, "dimension_hopper")
    spec = item_platform(hopper, data)
    assert hopper.build.effects[0].config["modes"]

    spec.movement_rank = 8
    apply_platform(hopper, spec, data)

    assert hopper.build.effects[0].effect_id == "enhanced_movement"
    assert hopper.build.effects[0].rank == 8
    assert hopper.build.effects[0].config["modes"]


def test_editing_a_stock_platforms_traits_moves_it_off_the_printed_price(data, hero) -> None:
    """The other half of ``item_is_stock``: a platform's traits live outside its build.

    Without it a jet whose Toughness the player raised would keep the book's printed
    number — the points would simply be free, and nothing would say so.
    """
    tank = _item(data, "tank")
    assert item_is_stock(tank, data) and item_ep_cost(tank, data, hero) == 76

    spec = item_platform(tank, data)
    apply_platform(tank, spec, data)  # a no-op pass through the editor
    assert platform_is_stock(tank, data)
    assert item_is_stock(tank, data) and item_ep_cost(tank, data, hero) == 76

    spec.toughness += 2
    apply_platform(tank, spec, data)
    assert not platform_is_stock(tank, data)
    assert not item_is_stock(tank, data)


def test_a_platforms_features_cost_a_point_each(data) -> None:
    """Off each Feature's own record, and a repeatable one bought twice costs twice."""
    item = _custom(PLATFORM_VEHICLE, data)
    item.platform.features = ["alarm", "alarm", "caltrops"]

    assert platform_feature_cost(item.platform, data) == 3
    assert platform_trait_cost(item.platform, data) == 3
    assert platform_feature_cost(PlatformSpec(features=["nonesuch"]), data) == 0


def test_a_platform_spec_round_trips_through_a_save(data) -> None:
    """And an item that is not a platform writes no ``platform`` key at all."""
    item = _custom(PLATFORM_VEHICLE, data)
    item.platform.features = ["autopilot"]
    item.platform.modifiers = ["durable"]

    restored = EquipmentItem.from_dict(item.to_dict())
    assert restored.platform == item.platform
    assert "platform" not in _item(data, "sword").to_dict()


def test_a_throttle_makes_a_vehicle_easier_to_hit(data) -> None:
    """The one defence in the app that changes within a round (§5 Combat).

    ``current_speed`` is runtime state like ``worn``: a tank crawling is DC 10, the
    same tank flat out is DC 14, and neither is a build edit.
    """
    tank = _item(data, "tank")
    rows = {row.key: row for row in platform_trait_rows(tank, data)}
    assert rows["defense_class"].value == "14 moving / 8 stationary"

    tank.current_speed = 2
    rows = {row.key: row for row in platform_trait_rows(tank, data)}
    assert rows["defense_class"].value == "10 moving / 8 stationary"
    assert "current_speed" not in tank.to_dict()


# --- installations ----------------------------------------------------------------------


def test_stock_installations_are_pickable_priced_gear(data, hero) -> None:
    """The Phase 9 bargain again: a printed installation is one more catalog entry."""
    catalog = data.equipment_catalog()
    assert catalog["moon_base"].category == platform_rules_category(PLATFORM_INSTALLATION, data)

    base = _item(data, "moon_base")
    assert item_is_stock(base, data)
    assert item_ep_cost(base, data, hero) == 39  # the printed number wins while stock
    assert base.build.effects == []  # what it *does* is its Features


def test_an_installation_starts_from_a_free_house(data, hero) -> None:
    """Size rank 5 and Toughness 6 cost nothing; everything above is what is bought."""
    item = _custom(PLATFORM_INSTALLATION, data)
    rules = data.installation_rules

    assert (item.platform.size, item.platform.toughness) == (
        rules.free_size_rank,
        rules.free_toughness,
    )
    assert installation_trait_cost(item.platform, data) == 0
    assert item_ep_cost(item, data, hero) == 0


def test_an_installations_size_refunds_below_the_pivot(data) -> None:
    """A room hands four points *back*; a small town costs six."""
    assert installation_size_cost(5, data) == 0
    assert installation_size_cost(1, data) == -4
    assert installation_size_cost(11, data) == 6
    # Past the printed table the same straight line continues, rather than clamping.
    assert installation_size_cost(13, data) == 8


def test_installation_toughness_is_a_point_per_two_rounded_up(data) -> None:
    """And Toughness under the free baseline refunds nothing — it is a starting point."""
    spec = new_platform(PLATFORM_INSTALLATION, data)
    spec.toughness = 12
    assert installation_trait_cost(spec, data) == 3

    spec.toughness = 11  # +5 over the baseline: three points, not two and a half
    assert installation_trait_cost(spec, data) == 3

    spec.toughness = 2
    assert installation_trait_cost(spec, data) == 0


def test_an_installation_is_priced_from_its_traits_and_its_features(data, hero) -> None:
    item = _custom(PLATFORM_INSTALLATION, data)
    item.platform.size = 7  # a mansion: +2
    item.platform.toughness = 12  # +3
    item.platform.features = ["laboratory", "security_system", "concealed", "concealed"]
    apply_platform(item, item.platform, data)

    assert platform_trait_cost(item.platform, data) == 9
    assert item_ep_cost(item, data, hero) == 9


def test_an_installation_never_grows_a_movement_effect(data) -> None:
    """They do not move, which is why the honest answer is no effect rather than rank 0."""
    item = _custom(PLATFORM_INSTALLATION, data)
    assert platform_movement_effect(item.platform, data) is None
    assert item.build.effects == []


def test_an_installation_has_its_own_power_level_cap_pair(data, hero) -> None:
    """Toughness may reach twice the series PL; Impervious may not (§6).

    A different pair from a character's, and the reason installations get a validator
    branch of their own rather than joining ``power_level_violations``.
    """
    spec = new_platform(PLATFORM_INSTALLATION, data)
    spec.toughness = hero.power_level * 2
    assert installation_pl_violations(spec, hero, data) == []

    spec.toughness += 1
    assert (
        "exceeds the PL 10 installation cap of 20"
        in installation_pl_violations(spec, hero, data)[0]
    )

    spec.toughness = 20
    spec.defenses = [ModifierSelection(modifier_id="impervious", rank=hero.power_level)]
    assert installation_pl_violations(spec, hero, data) == []
    spec.defenses = [ModifierSelection(modifier_id="impervious", rank=hero.power_level + 1)]
    assert (
        "Impervious 11 exceeds the PL 10 cap of 10"
        in installation_pl_violations(spec, hero, data)[0]
    )


def test_only_an_installation_answers_the_installation_cap(data, hero) -> None:
    """A vehicle's traits have no cap of their own — its weapons are capped as gear is."""
    tank = _item(data, "tank")
    assert item_platform_violations(tank, hero, data) == []
    assert item_platform_violations(_item(data, "sword"), hero, data) == []

    base = _custom(PLATFORM_INSTALLATION, data)
    base.platform.toughness = 99
    assert item_platform_violations(base, hero, data)


def test_an_installations_trait_rows_say_what_it_is_and_what_it_has(data) -> None:
    """Two traits and its Features — a far shorter grid than a vehicle's, on purpose."""
    rows = {row.key: row for row in installation_trait_rows(_item(data, "moon_base"), data)}

    assert rows["size"].value == "9" and "Skyscraper" in rows["size"].base
    assert rows["toughness"].base == "6"  # the free starting point it is measured against
    assert "Holding Cells" in rows["features"].value

    item = _custom(PLATFORM_INSTALLATION, data)
    item.platform.features = ["concealed", "concealed"]
    features = {row.key: row for row in installation_trait_rows(item, data)}["features"]
    assert features.value == "Concealed ×2"


def test_the_trait_grid_dispatches_on_the_kind(data) -> None:
    """One question from the card, one answer, and no platform branch in the block."""
    assert [row.key for row in platform_trait_rows(_item(data, "tank"), data)][0] == "vehicle_class"
    assert [row.key for row in platform_trait_rows(_item(data, "moon_base"), data)][0] == "size"
    assert platform_trait_rows(_item(data, "sword"), data) == []
    assert vehicle_trait_rows(_item(data, "moon_base"), data) == []
    assert installation_trait_rows(_item(data, "tank"), data) == []


def test_a_custom_platform_never_touches_the_power_point_pool(data, hero) -> None:
    """The two currencies again, this time with the money on the other side."""
    before = power_points_spent(hero, data)
    base = _custom(PLATFORM_INSTALLATION, data)
    base.platform.size = 11
    base.platform.features = ["laboratory"]
    hero.equipment.append(base)

    assert power_points_spent(hero, data) == before
    assert equipment_points_spent(hero, data) == 7
