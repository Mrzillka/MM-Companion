"""Answering a roll for a creature: its number, and the ladder a save carries as data.

Headless. The GM's history is the caller (``test_area_ui.py`` proves the window), and
what is proven here is the rules half: every builder of a save puts its ladder on the
spec concretely (an Affliction's picks, a Weaken's scope, the damage ladder's
escalation, an impression's ``{source}``), the ladder survives the wire, and a
creature's own number is what the menu offers.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mm_companion.core import storage
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    CATEGORY_AREA,
    CATEGORY_DAMAGE,
    CATEGORY_EFFECT,
    CATEGORY_IMPRESS,
    KIND_BEST_OF,
    KIND_SKILL,
    RollSpec,
    SpecLadder,
    answer_reading,
    answer_rung,
    area_resist_spec,
    best_of_key,
    condition_changes,
    effect_spec_ladder,
    impress_resist_spec,
    power_area_use,
    resolve_rung,
)
from mm_companion.core.session.protocol import sanitize_spec


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _damage_ladder(data) -> SpecLadder:
    ladder = effect_spec_ladder(PowerEffectInstance("damage", rank=8), data)
    assert ladder is not None
    return ladder


def test_a_damage_save_carries_the_damage_ladder(data) -> None:
    ladder = _damage_ladder(data)
    assert ladder.kind == CATEGORY_DAMAGE
    assert answer_rung(ladder, 1).conditions == ("hit",)  # a made save still costs a Hit
    assert answer_rung(ladder, -2).conditions == ("hit", "stunned", "staggered")


def test_escalation_travels_with_the_ladder(data) -> None:
    ladder = SpecLadder.from_dict(
        sanitize_spec(RollSpec(label="Toughness", ladder=_damage_ladder(data)).to_dict())["ladder"]
    )
    target = Character.new_default(data)
    target.conditions.append(AppliedCondition("dazed"))
    changes = resolve_rung(target, answer_rung(ladder, -1), data)
    assert ("stunned", None) in changes  # "Stunned instead of Dazed if already Dazed"


def test_an_affliction_carries_its_wielders_picks(data) -> None:
    effect = PowerEffectInstance(
        "affliction",
        rank=8,
        config={"degree1": ["dazed", "vulnerable"], "degree3": ["impaired"], "scope": "Strength"},
    )
    ladder = effect_spec_ladder(effect, data)
    assert ladder is not None and ladder.kind == CATEGORY_EFFECT
    assert ladder.rungs[0].conditions == ("dazed", "vulnerable")
    assert dict(ladder.rungs[2].parameters) == {"impaired": "Strength"}
    changes = resolve_rung(Character.new_default(data), ladder.rungs[2], data)
    assert changes == (("impaired", "Strength"),)


def test_an_impression_names_its_source(data) -> None:
    impression = next(i for i in data.impress.impressions if any(r.parameters for r in i.outcomes))
    spec = impress_resist_spec(data, impression, data.impress.skills[0])
    assert spec.ladder is not None and spec.ladder.kind == CATEGORY_IMPRESS
    rung = spec.ladder.rungs[-1]
    changes = resolve_rung(Character.new_default(data), rung, data, source="Thug")
    assert any(parameter == "Thug" for _cid, parameter in changes)


def test_an_area_save_is_the_areas_whatever_its_effect(data) -> None:
    burst = Power(
        name="Fire Burst",
        effects=[
            PowerEffectInstance(
                "damage",
                rank=8,
                extras=[ModifierSelection("area_effect", config={"shape": "cone"})],
            )
        ],
    )
    use = power_area_use(burst, Character.new_default(data), data)
    save = area_resist_spec(use, use.effects[0], data)
    assert save is not None and save.ladder is not None
    assert save.ladder.kind == CATEGORY_AREA
    assert answer_rung(save.ladder, -2).conditions == ("hit", "stunned", "staggered")


def test_the_wire_keeps_the_ladder_and_caps_it(data) -> None:
    spec = RollSpec(label="Toughness", ladder=_damage_ladder(data), actor="Mook")
    clean = RollSpec.from_dict(sanitize_spec(spec.to_dict()))
    assert clean.ladder == spec.ladder and clean.actor == "Mook"
    hostile = spec.to_dict()
    hostile["ladder"]["rungs"] = [{"ids": ["x" * 999] * 50, "escalates": "nope"}] * 50
    rungs = sanitize_spec(hostile)["ladder"]["rungs"]
    assert len(rungs) <= 12 and all(len(r["ids"]) <= 12 for r in rungs)
    assert "escalates" not in rungs[0]


def test_a_reading_names_the_trait_a_best_of_picks(data) -> None:
    npc = Character.new_default(data)
    npc.resistances["WILL"] = 9
    spec = RollSpec(
        label="Resist",
        kind=KIND_BEST_OF,
        trait_key=best_of_key([("resistance", "WILL"), ("skill", "Insight")]),
        modifier=1,
    )
    reading = answer_reading(spec, npc, data)
    assert reading is not None and reading.label == "Will"
    plain = answer_reading(RollSpec(label="Will", trait_key="WILL"), npc, data)
    assert reading.modifier == plain.modifier + 1


def test_a_trained_only_skill_is_flagged_but_read(data) -> None:
    skill = next(s for s in data.skills if s.trained_only and not s.focused)
    npc = Character.new_default(data)
    reading = answer_reading(
        RollSpec(label=skill.name, kind=KIND_SKILL, trait_key=skill.name), npc, data
    )
    assert reading is not None and not reading.usable


def test_condition_changes_count_stacks_and_skip_bundled(data) -> None:
    before = [AppliedCondition("hit", count=1), AppliedCondition("dazed")]
    after = [
        AppliedCondition("hit", count=2),
        AppliedCondition("stunned"),
        AppliedCondition("dazed", provenance="staggered"),
    ]
    added, removed = condition_changes(before, after)
    assert sorted(added) == [("hit", None), ("stunned", None)]
    assert removed == [("dazed", None)]


def test_an_ask_prompt_goes_without_the_ladder(data) -> None:
    from mm_companion.core.session.protocol import sanitize_mod_payload

    spec = RollSpec(label="Toughness", ladder=_damage_ladder(data))
    # Inside an action's payload a ladder is cut down without a word: why asks drop it.
    cut = sanitize_mod_payload({"spec": spec.to_dict()})["spec"]["ladder"]
    assert cut != spec.ladder.to_dict()
    assert replace(spec, ladder=None).to_dict().get("ladder") is None


def test_the_auto_apply_setting_merges_per_kind(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    assert all(storage.gm_auto_apply().values())
    storage.update_settings(gm_auto_apply={"damage": False})
    chosen = storage.gm_auto_apply()
    assert chosen["damage"] is False and chosen["area"] is True
