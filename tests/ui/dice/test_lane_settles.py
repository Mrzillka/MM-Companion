"""Several attack maneuvers on one lane settle within the rules' caps.

The lane limits each line by the others' values; a pull-down can tighten another line as
well as loosen it, so it passes until nothing moves (see the dice notes, "Lines limit
each other"). These check what the player can roll: no value combined past the floor or
past double, and every slider's range exactly its cap.
"""

from __future__ import annotations

import random

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import attack_choices
from mm_companion.core.rules.maneuvers import maneuver_action, maneuver_limit, maneuver_values
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")

ACCURATE, ALL_OUT, DEFENSIVE, POWER = (
    "accurate_attack",
    "all_out_attack",
    "defensive_attack",
    "power_attack",
)


def _hero(data, *, attack: int, defense: int, rank: int) -> Character:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = attack
    hero.resistances["DEF"] = defense
    blast = PowerEffectInstance("damage", rank=rank)
    blast.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    jab = PowerEffectInstance("damage", rank=max(1, rank - 3))
    hero.powers = [Power(name="Blast", effects=[blast]), Power(name="Jab", effects=[jab])]
    return hero


def _section(data, **traits) -> DiceSection:
    section = DiceSection(data, _hero(data, **traits))
    section.panel.load_spec(attack_choices(section._character, data)[0].spec)
    return section


def _problems(section: DiceSection, data) -> list[str]:
    """What breaks the caps on the lane as it stands (``[]`` when nothing does)."""
    panel = section.panel
    spec = panel.loaded_attack()
    picks = panel.active_lines()
    shifts: dict[str, int] = {}
    for key, amount in picks:
        action = maneuver_action(data, key)
        if action.bonus:
            shifts[action.bonus] = shifts.get(action.bonus, 0) + amount
        if action.penalty:
            shifts[action.penalty] = shifts.get(action.penalty, 0) - amount
    problems = []
    for target, base in maneuver_values(spec, section._character, data).items():
        net = shifts.get(target, 0)
        if base is None:
            continue
        if base + net < 0:
            problems.append(f"{target} {base}{net:+d} is below 0")
        if net > 0 and base + net > 2 * base:
            problems.append(f"{target} {base}{net:+d} is more than doubled")
    for line in panel.action_lines():
        others = {key: value for key, value in picks if key != line.key}
        cap = maneuver_limit(data, line.key, spec, section._character, others).maximum
        if line.maximum() != cap:
            problems.append(f"{line.key} slider goes to {line.maximum()}, its cap is {cap}")
    return problems


def test_a_defense_drop_settles_three_maneuvers_within_the_floor(qapp, data) -> None:
    # Two fixed passes used to leave Defensive 2, Power 5, All-Out 3 here: attack −1.
    section = _section(data, attack=3, defense=3, rank=5)
    for key in (DEFENSIVE, POWER, ALL_OUT):
        section.combat.start_maneuver(key)
    section.panel.action_lines()[-1].set_value(5)
    section.combat.start_maneuver(POWER)
    section._character.resistances["DEF"] = 1
    section.refresh_limits()
    assert _problems(section, data) == []


def test_a_new_maneuver_counts_in_the_others_ranges(qapp, data) -> None:
    # All-Out raises the attack Power spends: Power's room grows to the full 5.
    section = _section(data, attack=4, defense=4, rank=5)
    section.combat.start_maneuver(POWER)
    section.panel.action_lines()[0].set_value(3)
    section.combat.start_maneuver(ALL_OUT)
    power = next(line for line in section.panel.action_lines() if line.key == POWER)
    assert power.maximum() == 5
    assert _problems(section, data) == []


@pytest.mark.parametrize("seed", range(40))
def test_any_sequence_of_maneuvers_stays_within_the_caps(qapp, data, seed) -> None:
    rng = random.Random(seed)
    traits = {
        "attack": rng.randint(1, 8),
        "defense": rng.randint(0, 6),
        "rank": rng.randint(1, 6),
    }
    section = _section(data, **traits)
    choices = attack_choices(section._character, data)
    steps = [str(traits)]
    for _ in range(40):
        roll = rng.random()
        lines = section.panel.action_lines()
        if roll < 0.3 or not lines:
            key = rng.choice((ACCURATE, ALL_OUT, DEFENSIVE, POWER))
            section.combat.start_maneuver(key)
            steps.append(f"add {key}")
        elif roll < 0.9:
            line = rng.choice(lines)
            if line.maximum():
                top = line.maximum()
                value = top if rng.random() < 0.6 else rng.randint(0, top)
                line.set_value(value)
                steps.append(f"{line.key}={value}")
        elif roll < 0.94:
            spec = rng.choice(choices).spec
            section.panel.load_spec(spec)
            steps.append(f"load {spec.label}")
        elif roll < 0.97:
            section._character.resistances["DEF"] = rng.randint(0, 6)
            section.refresh_limits()
            steps.append(f"Defense {section._character.resistances['DEF']}")
        else:
            line = rng.choice(lines)
            section.panel.remove_action_line(line.key)
            steps.append(f"remove {line.key}")
        assert _problems(section, data) == [], steps
