"""Area effects: shape and size, the Dodge check, and the area attack's records."""

from __future__ import annotations

import pytest

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    KIND_AREA,
    KIND_AREA_DODGE,
    KIND_BEST_OF,
    KIND_WORST_OF,
    AreaAttempt,
    AreaUse,
    RollSpec,
    area_dodge_bonus,
    area_dodge_modifier,
    area_effect_ladder,
    area_effect_rung,
    area_npc_dodge_spec,
    area_npc_save_specs,
    area_request_specs,
    area_resist_modifier,
    area_resisted_rank,
    area_size_text,
    area_stage_rank,
    effect_area_rank,
    effect_area_text,
    effect_game_terms,
    effect_stat_rows,
    estimated_power_level,
    evasion_negates,
    follow_up_for_result,
    localize_spec,
    power_area_use,
    power_offers_area,
    power_pl_violations,
    power_rolls,
    power_rolls_with_area,
    power_total_cost,
)
from mm_companion.core.session.protocol import sanitize_mod_payload, sanitize_spec


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _area(rank: int = 8, **config) -> PowerEffectInstance:
    return PowerEffectInstance(
        "damage", rank=rank, extras=[ModifierSelection("area_effect", config=dict(config))]
    )


def _hero(pl: int = 10, evasion: int = 0) -> Character:
    char = Character()
    char.power_level = pl
    if evasion:
        char.advantages.append(AdvantageSelection(name="Evasion", rank=evasion))
    return char


# -- the shape and its size ------------------------------------------------------


def test_the_area_block_parses(data) -> None:
    assert data.area.available
    assert data.area.modifiers == ("area_effect",)
    assert data.area.dodge == "DODGE"
    assert data.area.evasion is not None and data.area.evasion.dodge_bonus == 5
    assert [c.value for c in data.area.cover] == [0, 2, 5]


@pytest.mark.parametrize(
    ("shape", "applications", "expected"),
    [
        ("sphere", 1, "Sphere, 15 feet across"),
        ("sphere", 2, "Sphere, 30 feet across"),
        ("cone", 1, "Cone, 15 feet long, wide and high"),
        ("line", 3, "Line, 60 feet long"),
        ("shapeable", 1, "Shapeable, 8 spaces"),
        ("shapeable", 3, "Shapeable, 32 spaces"),
    ],
)
def test_an_area_reads_its_size(data, shape, applications, expected) -> None:
    # Each application past the first widens the shape by one distance rank, or
    # doubles a Shapeable area's spaces (which start at the effect's rank).
    assert area_size_text(shape, applications, 8, data) == expected


def test_a_made_dodge_halves_the_rank_rounding_up(data) -> None:
    assert area_resisted_rank(8, data) == 4
    assert area_resisted_rank(9, data) == 5


def test_area_applications_and_emanation_are_priced(data) -> None:
    def cost(**config) -> int:
        return power_total_cost(Power(effects=[_area(**config)]), data)

    assert cost() == 16  # Damage 1/rank + Area 1/rank
    assert cost(applications=2) == 24
    assert cost(applications=2, spread="emanation") == 32
    assert cost(spread="sensory") == 16


def test_the_area_row_states_size_and_dodge(data) -> None:
    rows = {r.key: r for r in effect_stat_rows(_area(shape="cone", spread="emanation"), data)}
    assert (
        rows["area"].value == "Cone, 15 feet long, wide and high, Emanation; Dodge DC 18 for half"
    )
    line = effect_game_terms(_area(), data)
    assert "Area: Sphere, 15 feet across; Dodge DC 18 for half" in line


def test_an_area_is_capped_at_power_level_not_attack_plus_rank(data) -> None:
    hero = _hero(pl=8)
    assert power_pl_violations(Power(effects=[_area(rank=8)]), hero, data) == []
    breach = power_pl_violations(Power(effects=[_area(rank=9)]), hero, data)
    assert len(breach) == 1 and "rank cap of 8" in breach[0]


# -- what a power offers -----------------------------------------------------------


def test_a_bought_area_offers_its_own_shape(data) -> None:
    power = Power(name="Fire Burst", effects=[_area(rank=8, shape="cone")])
    use = power_area_use(power, _hero(), data)
    assert use is not None and not use.free
    assert use.shape == "cone" and use.rank == 8
    assert use.dodge_dc(data) == 18
    assert use.effects[0].resistance == "TOUGHNESS"
    assert not power_offers_area(power, data)  # already an area: no switch to offer


def test_an_attack_offers_the_free_area_use_only_when_switched_on(data) -> None:
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=12)])
    assert power_offers_area(blast, data)
    assert power_area_use(blast, _hero(pl=10), data) is None
    blast.area_use = True
    use = power_area_use(blast, _hero(pl=10), data, shape="line")
    assert use is not None and use.free
    assert use.shape == "line"
    assert use.rank == 10  # capped at the Power Level for the area portion


def test_a_power_with_nothing_to_resist_offers_no_area(data) -> None:
    flight = Power(effects=[PowerEffectInstance("flight", rank=4)])
    assert not power_offers_area(flight, data)
    flight.area_use = True
    assert power_area_use(flight, _hero(), data) is None


def test_the_area_line_comes_last_so_pins_keep_their_index(data) -> None:
    power = Power(name="Fire Burst", effects=[_area()])
    plain = power_rolls(power, _hero(), data)
    with_area = power_rolls_with_area(power, _hero(), data)
    assert with_area[: len(plain)] == plain
    assert with_area[-1].kind == KIND_AREA
    assert with_area[-1].trait_key == power.id


def test_the_area_switch_is_saved(data) -> None:
    power = Power(effects=[PowerEffectInstance("damage", rank=4)], area_use=True)
    assert Power.from_dict(power.to_dict()).area_use
    assert "area_use" not in Power(effects=[]).to_dict()


# -- the Dodge check and the save behind it -----------------------------------------


def test_evasion_adds_to_the_dodge_and_negates_at_two(data) -> None:
    assert area_dodge_bonus(_hero(), data) == 0
    assert area_dodge_bonus(_hero(evasion=1), data) == 5
    assert not evasion_negates(_hero(evasion=1), data)
    assert evasion_negates(_hero(evasion=2), data)
    assert area_dodge_modifier(_hero(evasion=1), data) == area_dodge_modifier(_hero(), data) + 5


def test_the_stage_rank_after_a_dodge(data) -> None:
    hero = _hero()
    assert area_stage_rank(8, False, hero, data) == 8
    assert area_stage_rank(8, True, hero, data) == 4
    assert area_stage_rank(8, True, _hero(evasion=2), data) is None


def test_the_dodge_request_chains_the_save_at_full_or_half(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area(rank=8)]), _hero(), data)
    assert use is not None
    dodge, *rest = area_request_specs(use, data, source="Ana", caught=("Bo",))
    assert rest == []
    assert dodge.kind == KIND_AREA_DODGE and dodge.dc == 18 and dodge.targets == ("Bo",)
    failed = follow_up_for_result(dodge, 3, -1, data)
    assert failed is not None and failed.dc == 18
    made = follow_up_for_result(dodge, 15, 1, data)
    assert made is not None and made.dc == 14
    assert "Dodge made" in made.label
    # No critical on an area: a natural 20 on the Dodge raises nothing.
    assert follow_up_for_result(dodge, 20, 2, data).dc == 14


def test_a_hit_primary_player_gets_the_save_at_full_rank(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area(rank=8)]), _hero(), data)
    specs = area_request_specs(use, data, source="Ana", primary="Cy")
    assert len(specs) == 1
    assert specs[0].dc == 18 and specs[0].targets == ("Cy",) and specs[0].follow_up is None


def test_dodging_an_area_takes_evasion_on_the_clickers_sheet(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area()]), _hero(), data)
    dodge = area_request_specs(use, data, caught=("Bo",))[0]
    plain = localize_spec(dodge, _hero(), data)
    evasive = localize_spec(dodge, _hero(evasion=1), data)
    assert evasive.modifier == plain.modifier + 5
    assert "Evasion" in evasive.label


def test_a_save_lands_on_the_effects_own_ladder(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area()]), _hero(), data)
    effect = use.effects[0]
    assert area_effect_rung(effect, -1, data).conditions
    made = area_effect_rung(effect, 1, data)  # a made Toughness save still leaves a Hit
    assert made is not None


# -- what travels ----------------------------------------------------------------------


def test_an_attempt_round_trips(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area(shape="cube")]), _hero(), data)
    attempt = AreaAttempt("abc", use, primary="s1", targets=("s2", "s3"), source_name="Ana")
    again = AreaAttempt.from_dict(attempt.to_dict())
    assert again == attempt
    assert AreaAttempt.from_dict({"action_id": "x"}) is None
    assert AreaUse.from_dict({"effects": []}) is None


def test_the_new_spec_fields_survive_the_wire(data) -> None:
    spec = RollSpec(
        label="Dodge",
        kind=KIND_AREA_DODGE,
        follow_up=RollSpec(label="Toughness", dc=18, dc_if_resisted=14, note_if_resisted="x"),
        follow_up_always=True,
        targets=("Bo", "Cy"),
        action_id="abc",
        title="Ana: Burst (area)",
    )
    raw = sanitize_spec(spec.to_dict())
    assert RollSpec.from_dict(raw) == spec


# -- what the review turned up ----------------------------------------------------------


def _affliction_area(rank: int = 8) -> PowerEffectInstance:
    return PowerEffectInstance(
        "affliction",
        rank=rank,
        extras=[ModifierSelection("area_effect")],
        config={
            "resistance": "Will",
            "overcomeBy": "Will",
            "degree1": "dazed",
            "degree2": "stunned",
            "degree3": "incapacitated",
        },
    )


def test_an_attempt_keeps_its_ladder_through_the_session_sanitizer(data) -> None:
    # The server runs a player's attempt through the mod-payload sanitizer, which keeps
    # four levels: nested one deeper, an Affliction lost every condition on its ladder.
    use = power_area_use(Power(name="Haze", effects=[_affliction_area()]), _hero(), data)
    attempt = AreaAttempt("abc", use, targets=("s1",), source_name="Ana")
    raw = attempt.to_dict()
    assert sanitize_mod_payload(raw) == raw
    again = AreaAttempt.from_dict(sanitize_mod_payload(raw))
    assert again == attempt
    assert any(area_effect_ladder(again.use.effects[0], data))


def test_a_list_valued_config_stays_home_rather_than_breaking_the_payload(data) -> None:
    effect = _affliction_area()
    effect.config["extra"] = ["a", "b"]
    use = power_area_use(Power(name="Haze", effects=[effect]), _hero(), data)
    raw = AreaAttempt("abc", use).to_dict()
    assert sanitize_mod_payload(raw) == raw
    assert "extra" not in raw["effects"][0]["config"]


def _either(against: str) -> PowerEffectInstance:
    return PowerEffectInstance(
        "damage",
        rank=8,
        extras=[
            ModifierSelection("area_effect"),
            ModifierSelection(
                "alternate_resistance",
                config={"resistance": "Fortitude", "resistanceSecond": "Will", "against": against},
            ),
        ],
    )


@pytest.mark.parametrize(
    ("against", "kind", "expected"), [("best", KIND_BEST_OF, 7), ("worst", KIND_WORST_OF, 6)]
)
def test_a_choice_of_two_resistances_is_rolled_as_the_better_or_the_worse(
    data, against, kind, expected
) -> None:
    # An NPC's save used to be rolled at +0 whenever the card named two resistances.
    use = power_area_use(Power(name="Burst", effects=[_either(against)]), _hero(), data)
    effect = use.effects[0]
    assert effect.resistance_kind == kind
    npc = _hero()
    npc.resistances["FORTITUDE"] = 7
    npc.resistances["WILL"] = 6
    assert area_resist_modifier(npc, data, effect) == expected
    # A caught player's request fills in their own number the same way.
    (dodge,) = area_request_specs(use, data, caught=("Bo",))
    assert localize_spec(dodge.follow_up, npc, data).modifier == expected


def test_a_skill_in_a_resistances_place_is_rolled_too(data) -> None:
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        extras=[
            ModifierSelection("area_effect"),
            ModifierSelection("alternate_resistance", config={"resistance": "Athletics"}),
        ],
    )
    use = power_area_use(Power(name="Burst", effects=[effect]), _hero(), data)
    assert use.effects[0].resistance_kind == KIND_BEST_OF
    npc = Character.new_default(data)
    npc.skill_ranks["Athletics"] = 4
    plain = Character.new_default(data)
    assert area_resist_modifier(npc, data, use.effects[0]) == (
        area_resist_modifier(plain, data, use.effects[0]) + 4
    )


def test_a_hard_capped_area_is_held_to_its_rank_cap(data) -> None:
    # At PL 10 with Attack 4, a rank 14 area used to pass: the hard cap only trimmed
    # attack + rank to 20, while the rule for an area is its rank alone.
    hero = _hero(pl=10)
    hero.abilities[data.system.trait_keys.attack] = 4
    effect = _area(rank=14)
    for side in ("attack", "effect"):
        effect.pl_cap = side
        assert effect_area_rank(effect, data, hero) == 10
        assert power_pl_violations(Power(effects=[effect]), hero, data) == []


def test_the_pl_estimate_reads_an_area_as_the_validator_does(data) -> None:
    hero = _hero(pl=10)
    hero.abilities[data.system.trait_keys.attack] = 4
    hero.powers.append(Power(effects=[_area(rank=14)]))
    assert estimated_power_level(hero, data) == 14


def test_a_beneficial_area_offers_no_dodge(data) -> None:
    healing = PowerEffectInstance("healing", rank=6, extras=[ModifierSelection("area_effect")])
    text = effect_area_text(healing, data)
    assert text.startswith("Sphere") and "Dodge" not in text


def test_the_free_use_cap_rises_with_size(data) -> None:
    hero = Character.new_default(data)
    hero.power_level = 10
    hero.characteristics["size"] = "Huge"  # +2 to a Damage's rank, and to its cap
    stomp = Power(name="Stomp", effects=[PowerEffectInstance("damage", rank=10)], area_use=True)
    use = power_area_use(stomp, hero, data)
    assert use is not None and use.rank == 12


def test_an_npcs_free_use_is_capped_at_its_estimated_level(data) -> None:
    npc = _hero(pl=20)  # the level on file is no rule for an NPC
    npc.abilities[data.system.trait_keys.attack] = 10
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=14)], area_use=True)
    npc.powers.append(blast)
    assert power_area_use(blast, npc, data).rank == 14
    estimate = estimated_power_level(npc, data)
    assert estimate < 14
    assert power_area_use(blast, npc, data, npc=True).rank == estimate


def test_the_gms_cover_and_adjustment_ride_on_the_request(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area()]), _hero(), data)
    (dodge,) = area_request_specs(use, data, caught=("Bo",), cover=5, adjust=2)
    assert dodge.modifier == 7 and "+5 cover" in dodge.label
    assert dodge.follow_up.modifier == 2  # cover counts on the Dodge alone
    (save,) = area_request_specs(use, data, primary="Cy", adjust=-1)
    assert save.modifier == -1


def test_a_linked_areas_further_saves_stay_the_targets_to_roll(data) -> None:
    use = power_area_use(Power(name="Haze", effects=[_area(), _affliction_area()]), _hero(), data)
    _dodge, further = area_request_specs(use, data, caught=("Bo",))
    assert further.rolled_by_target and "if you made the Dodge" in further.label


def test_the_gms_table_lines_carry_each_creatures_number_and_dc(data) -> None:
    use = power_area_use(Power(name="Burst", effects=[_area(rank=8)]), _hero(), data)
    mook = _hero()
    dodge = area_npc_dodge_spec(use, mook, data, cover=2, adjust=1)
    assert dodge.dc == 18 and dodge.modifier == area_dodge_modifier(mook, data) + 3
    ((_effect, made),) = area_npc_save_specs(use, mook, data, dodged=True)
    assert made.dc == 14
    ((_effect, hit),) = area_npc_save_specs(use, mook, data, dodged=None, adjust=-2)
    assert hit.dc == 18
    assert hit.modifier == area_resist_modifier(mook, data, use.effects[0]) - 2
    assert area_npc_save_specs(use, _hero(evasion=2), data, dodged=True) is None
