"""The damage ladder: rungs as steps, escalation, and the order they apply in.

Headless — this is the rules layer, so none of it needs a display. The UI that
offers these steps as buttons is exercised in ``tests/test_gm_window.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import (
    Effect,
    GameData,
    ResistanceOutcome,
    SystemRules,
    load_game_data,
)
from mm_companion.core.rules import (
    apply_condition,
    apply_damage_step,
    damage_step_summary,
    damage_steps,
    resolve_damage_step,
)


@pytest.fixture(scope="module")
def data() -> GameData:
    return load_game_data()


def _ids(char: Character) -> list[str]:
    return [applied.condition_id for applied in char.conditions]


def _step(data: GameData, index: int):
    return next(step for step in damage_steps(data) if step.index == index)


# -- the ladder as steps ---------------------------------------------------


def test_the_ladder_is_the_made_save_then_one_step_per_degree(data: GameData) -> None:
    steps = damage_steps(data)

    assert [step.index for step in steps] == [0, 1, 2, 3]
    # Index 0 is not a degree of failure, so it is not captioned like one.
    assert steps[0].label == "✓"
    assert [step.label for step in steps[1:]] == ["1", "2", "3"]
    assert steps[0].caption == "Hit"
    assert steps[1].caption == "Hit + Dazed"


def test_a_made_save_still_costs_a_hit(data: GameData) -> None:
    """The whole reason the success rung exists — it is not "nothing happened"."""
    char = Character()

    assert apply_damage_step(char, _step(data, 0), data) == ("hit",)
    assert _ids(char) == ["hit"]


def test_an_effect_with_no_ladder_offers_no_steps(data: GameData) -> None:
    """A caller then shows no control at all, rather than an inert one."""
    bare = dataclasses.replace(data, system=SystemRules(damage_effect="no-such-effect"))

    assert damage_steps(bare) == ()


def test_a_rung_that_names_no_conditions_is_skipped(data: GameData) -> None:
    """Affliction's rungs read their ids off the *instance*, so there is nothing
    here to put on a creature sitting on a card."""
    effect = Effect(
        id="prose",
        name="Prose",
        effect_type="Attack",
        resistance_outcomes=(
            ResistanceOutcome(text="the GM decides"),
            ResistanceOutcome(conditions=("dazed",)),
        ),
    )
    patched = dataclasses.replace(data, effects=[effect], system=SystemRules(damage_effect="prose"))

    steps = damage_steps(patched)

    assert [step.conditions for step in steps] == [("dazed",)]


# -- escalation ------------------------------------------------------------


def test_one_degree_dazes_a_fresh_target(data: GameData) -> None:
    char = Character()

    assert apply_damage_step(char, _step(data, 1), data) == ("hit", "dazed")


def test_one_degree_stuns_a_target_that_is_already_dazed(data: GameData) -> None:
    """The book's "Stunned instead of Dazed if already Dazed", as data."""
    char = Character()
    apply_condition(char, "dazed", data)

    assert apply_damage_step(char, _step(data, 1), data) == ("hit", "stunned")
    # And the Dazed is gone rather than sitting under the Stunned: Stunned
    # supersedes it, which the resolver gets for free.
    assert "dazed" not in _ids(char)
    assert "stunned" in _ids(char)


def test_escalation_chains_down_the_deep_end_of_the_ladder(data: GameData) -> None:
    """ "Further failed checks escalate to Dying, then Dead" — one rung per click."""
    char = Character()
    step = _step(data, 3)

    assert apply_damage_step(char, step, data)[-1] == "incapacitated"
    assert apply_damage_step(char, step, data)[-1] == "dying"
    assert apply_damage_step(char, step, data)[-1] == "dead"
    # And it settles there rather than cycling back to the bottom of the ladder.
    assert apply_damage_step(char, step, data)[-1] == "dead"


def test_escalation_reads_the_target_and_changes_nothing(data: GameData) -> None:
    """What lets a button say what it will do before it is clicked."""
    char = Character()
    apply_condition(char, "dazed", data)
    before = _ids(char)

    assert resolve_damage_step(char, _step(data, 1), data) == ("hit", "stunned")
    assert _ids(char) == before


# -- the order a rung applies in -------------------------------------------


def test_two_degrees_leaves_no_stray_dazed(data: GameData) -> None:
    """The regression this module exists to prevent.

    The rung is printed ``hit, stunned, staggered``. Applied in that order the
    Staggered arrives *after* the Stunned and brings its own Dazed with it, with
    nothing left to supersede it — so a two-degree hit left a target both Stunned
    and Dazed. The resolver applies the superseded sibling first instead.
    """
    char = Character()

    applied = apply_damage_step(char, _step(data, 2), data)

    assert applied.index("staggered") < applied.index("stunned")
    assert "dazed" not in _ids(char)
    assert {"hit", "staggered", "stunned"} <= set(_ids(char))


def test_three_degrees_supersedes_the_whole_staggered_bundle(data: GameData) -> None:
    char = Character()

    apply_damage_step(char, _step(data, 3), data)

    assert "staggered" not in _ids(char)
    assert "incapacitated" in _ids(char)


def test_a_dead_target_is_not_also_left_staggered(data: GameData) -> None:
    """Dead is the terminal rung, and supersedes what Incapacitated does.

    Without that, the third click's escalation replaced Incapacitated with Dead —
    which supersedes nothing — and the Staggered the same rung brings stayed on a
    corpse.
    """
    char = Character()
    step = _step(data, 3)
    for _ in range(3):
        apply_damage_step(char, step, data)

    assert "dead" in _ids(char)
    assert "staggered" not in _ids(char)


# -- what a button says it will do -----------------------------------------


def test_the_summary_names_the_rung_and_its_caveat(data: GameData) -> None:
    char = Character()

    summary = damage_step_summary(char, _step(data, 0), data)

    assert summary.startswith("Hit")
    assert "Hardened" in summary  # the made-save caveat rides along


def test_the_summary_says_when_it_will_escalate(data: GameData) -> None:
    char = Character()
    apply_condition(char, "dazed", data)

    summary = damage_step_summary(char, _step(data, 1), data)

    assert "Stunned" in summary
    assert "escalated" in summary


def test_the_summary_keeps_the_printed_order(data: GameData) -> None:
    """The apply order is an implementation detail; "Staggered + Stunned" is not
    how the book says it."""
    char = Character()

    assert damage_step_summary(char, _step(data, 2), data) == "Hit + Stunned + Staggered"
