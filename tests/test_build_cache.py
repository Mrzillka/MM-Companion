"""The scope that lets one refresh pass work the build out once.

:mod:`mm_companion.core.rules.build_cache` is a pure optimisation, so the tests that
matter are the ones that pin its *contract*: inside a scope the answer is computed
once, outside one nothing is remembered at all, and either way the answer is the same
one the uncached path gives. The last is the important one — a memo that returns a
different number from the function it memoizes is a rules bug wearing a performance
hat.
"""

from __future__ import annotations

import threading

import pytest

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    build_item_from_entry,
    derived,
    invalidate_build_cache,
    skill_total,
    stable_build,
    trait_bonuses,
    trait_contributions,
)


@pytest.fixture(scope="module")
def data():
    return load_game_data()


@pytest.fixture
def hero(data) -> Character:
    """A character carrying every kind of contribution the gather walks.

    Powers, a granted advantage's source, worn gear and a bought advantage — so a memo
    that quietly dropped one of the five sources would show up as a wrong number rather
    than merely as a faster one.
    """
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 3, "AGL": 2, "AWE": 2})
    char.skill_ranks["Athletics"] = 5
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    for catalog_id in ("chain_mail", "leather_armor"):
        char.equipment.append(build_item_from_entry(data.equipment_catalog()[catalog_id], data))
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)]))
    char.powers.append(Power(name="Armor", effects=[PowerEffectInstance("protection", rank=6)]))
    return char


@pytest.fixture
def counted(monkeypatch):
    """Count how often the gather actually runs, under the memo it normally wears."""
    calls: list[int] = []
    inner = trait_contributions.__wrapped__

    def counting(char, game_data):
        calls.append(1)
        return inner(char, game_data)

    counting.__qualname__ = inner.__qualname__
    monkeypatch.setattr(derived.trait_contributions, "__wrapped__", counting, raising=False)
    monkeypatch.setattr(
        derived, "trait_contributions", derived.build_scoped(counting), raising=True
    )
    return calls


def test_outside_a_scope_nothing_is_memoized(data, hero, counted) -> None:
    """The default has to stay exactly what it was, or the change is not safe."""
    for _ in range(4):
        derived.trait_contributions(hero, data)

    assert len(counted) == 4


def test_inside_a_scope_the_build_is_gathered_once(data, hero, counted) -> None:
    with stable_build():
        for _ in range(4):
            derived.trait_contributions(hero, data)

    assert len(counted) == 1


def test_the_memo_is_dropped_when_the_scope_ends(data, hero, counted) -> None:
    with stable_build():
        derived.trait_contributions(hero, data)
    with stable_build():
        derived.trait_contributions(hero, data)

    assert len(counted) == 2


def test_scopes_nest_and_only_the_outermost_drops_the_memo(data, hero, counted) -> None:
    """They nest in practice: a subscriber inside a scope may open one of its own."""
    with stable_build():
        derived.trait_contributions(hero, data)
        with stable_build():
            derived.trait_contributions(hero, data)
        derived.trait_contributions(hero, data)

    assert len(counted) == 1


def test_invalidating_forces_the_next_call_to_recompute(data, hero, counted) -> None:
    """What a handler that writes the model mid-pass reaches for."""
    with stable_build():
        derived.trait_contributions(hero, data)
        invalidate_build_cache()
        derived.trait_contributions(hero, data)

    assert len(counted) == 2


def test_invalidating_outside_a_scope_is_a_no_op(data, hero) -> None:
    invalidate_build_cache()  # nothing was remembered; must not raise


def test_a_scope_changes_no_number(data, hero) -> None:
    """The whole point: same answers, fewer of them."""
    plain = {
        "contributions": trait_contributions(hero, data),
        "bonuses": trait_bonuses(hero, data),
        "athletics": skill_total(hero, data, "Athletics"),
    }
    with stable_build():
        scoped = {
            "contributions": trait_contributions(hero, data),
            "bonuses": trait_bonuses(hero, data),
            "athletics": skill_total(hero, data, "Athletics"),
        }

    assert scoped == plain


def test_a_changed_character_is_not_answered_from_a_finished_scope(data, hero) -> None:
    """The scope promises the model holds *while it is open*, and nothing beyond."""
    with stable_build():
        before = skill_total(hero, data, "Athletics")

    hero.abilities["STR"] = hero.abilities["STR"] + 3  # Athletics is STR-based

    with stable_build():
        assert skill_total(hero, data, "Athletics") == before + 3


def test_a_scope_does_not_leak_into_another_thread(data, hero, counted) -> None:
    """The session layer runs reader threads; a scope is one thread's promise only."""
    seen: list[int] = []

    def worker() -> None:
        derived.trait_contributions(hero, data)
        derived.trait_contributions(hero, data)
        seen.append(len(counted))

    with stable_build():
        derived.trait_contributions(hero, data)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

    # One gather on this thread, and two more on the other — which memoized nothing,
    # because it never opened a scope of its own.
    assert seen == [3]
