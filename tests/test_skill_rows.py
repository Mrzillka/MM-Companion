"""The one listing of a skill's rows, which every view of the skills reads."""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    ROW_FOCUS,
    ROW_SKILL,
    ROW_SPECIALIZED,
    focus_row_id,
    own_skill_rows,
    skill_row_exists,
    specialized_row_id,
    split_trait_key,
)


def _skill(data, *, focused: bool):
    return next(s for s in data.skills if s.focused == focused)


def test_the_row_ids_are_the_qualified_key_format() -> None:
    assert focus_row_id("Expertise", "Law") == "Expertise::Law"
    assert specialized_row_id("Stealth", "Urban") == "Stealth::spec::Urban"
    assert split_trait_key(specialized_row_id("Stealth", "Urban")) == ("Stealth", "spec::Urban")


def test_a_plain_skill_has_its_own_row_then_its_pools() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    skill = _skill(data, focused=False)
    char.specializations[skill.name] = ["Urban"]

    rows = own_skill_rows(char, skill)

    assert [(r.kind, r.qualifier) for r in rows] == [(ROW_SKILL, ""), (ROW_SPECIALIZED, "Urban")]
    assert rows[0].row_id == skill.name
    assert all(skill_row_exists(char, data, r.row_id) for r in rows)


def test_a_focused_skill_has_a_row_per_focus_and_none_of_its_own() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    skill = _skill(data, focused=True)
    char.focuses[skill.name] = ["Law", "Magic"]

    rows = own_skill_rows(char, skill)

    assert [(r.kind, r.qualifier) for r in rows] == [(ROW_FOCUS, "Law"), (ROW_FOCUS, "Magic")]
    assert all(skill_row_exists(char, data, r.row_id) for r in rows)
