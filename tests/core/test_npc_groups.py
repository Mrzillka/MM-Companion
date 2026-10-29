"""NPC groups, headless: the arrangement the GM's board draws its squads from."""

from __future__ import annotations

from mm_companion.core.npc_groups import (
    NPCGroup,
    base_name,
    dissolve,
    group_of,
    groups_from_raw,
    groups_to_raw,
    join,
    leave,
    numbered_names,
    rename,
    shown_groups,
)


def test_two_loose_creatures_make_a_group_named_by_the_caller() -> None:
    groups = join([], ["b.json"], "a.json", name="Thug")
    (group,) = groups
    assert group.name == "Thug" and group.members == ["a.json", "b.json"]


def test_joining_a_grouped_creature_joins_its_group() -> None:
    groups = join([], ["b.json"], "a.json", name="Thug")
    groups = join(groups, ["c.json"], "b.json")
    assert len(groups) == 1 and groups[0].members == ["a.json", "b.json", "c.json"]


def test_dropping_on_a_group_by_id_joins_it() -> None:
    groups = join([], ["b.json"], "a.json")
    groups = join(groups, ["c.json"], groups[0].id, target_is_group=True)
    assert groups[0].members == ["a.json", "b.json", "c.json"]


def test_moving_out_of_a_pair_dissolves_the_pair() -> None:
    groups = join([], ["b.json"], "a.json")
    groups = join(groups, ["x.json"], "y.json")
    # b leaves a's pair for x and y's: the pair it left is gone.
    groups = join(groups, ["b.json"], "y.json")
    assert [g.members for g in groups] == [["y.json", "x.json", "b.json"]]


def test_a_creature_onto_itself_or_its_own_group_changes_nothing() -> None:
    groups = join([], ["b.json"], "a.json")
    assert join(groups, ["a.json"], "a.json") == groups
    assert join(groups, ["b.json"], "a.json")[0].members == ["a.json", "b.json"]


def test_leave_and_dissolve() -> None:
    groups = join([], ["b.json", "c.json"], "a.json")
    groups = leave(groups, "c.json")
    assert groups[0].members == ["a.json", "b.json"]
    assert leave(groups, "b.json") == []
    assert dissolve(groups, groups[0].id) == []


def test_rename_keeps_a_name_when_given_blank() -> None:
    groups = join([], ["b.json"], "a.json", name="Thug")
    assert rename(groups, groups[0].id, "Goons")[0].name == "Goons"
    assert rename(groups, groups[0].id, "  ")[0].name == "Thug"


def test_a_board_shows_only_members_in_its_cast() -> None:
    groups = join([], ["b.json", "c.json"], "a.json")
    assert shown_groups(groups, ["a.json", "c.json"])[0].members == ["a.json", "c.json"]
    assert shown_groups(groups, ["a.json"]) == []
    # The stored group is untouched.
    assert groups[0].members == ["a.json", "b.json", "c.json"]


def test_raw_round_trip_drops_the_malformed() -> None:
    raw = [
        {"id": "g1", "name": "Thug", "members": ["a.json", "b.json"]},
        {"id": "g2", "name": "Solo", "members": ["c.json"]},
        {"id": "g3", "members": ["a.json", "d.json", "e.json"]},
        "junk",
    ]
    groups = groups_from_raw(raw)
    assert [g.members for g in groups] == [["a.json", "b.json"], ["d.json", "e.json"]]
    assert groups[1].name == "Group"
    assert groups_from_raw(groups_to_raw(groups)) == groups
    assert group_of(groups, "e.json") is groups[1]


def test_squad_names_number_from_one_and_skip_names_in_use() -> None:
    assert numbered_names("Thug", 3, []) == ["Thug 1", "Thug 2", "Thug 3"]
    assert numbered_names("Thug 7", 2, ["Thug 1"]) == ["Thug 2", "Thug 3"]
    assert base_name("Goon-2") == "Goon" and base_name("Doctor X") == "Doctor X"


def test_groups_are_plain_data() -> None:
    assert NPCGroup("g", "Thug", ["a.json"]).to_dict() == {
        "id": "g",
        "name": "Thug",
        "members": ["a.json"],
    }


def test_a_malformed_member_list_is_skipped() -> None:
    raw = [
        {"id": "a", "name": "x", "members": None},
        {"id": "b", "name": "y", "members": "Thug 1"},
        {"id": "c", "name": "z", "members": ["t1.json", "t2.json"]},
    ]
    assert [g.id for g in groups_from_raw(raw)] == ["c"]
