"""Extra Effort: what it buys, what it costs, and where it is spent from.

Checked against the core rulebook p20-22 (the mechanic), p85-86 and p94 (the three
advantages that change it), p104/p159 (the Permanent duration that refuses it) and p98
(the ceiling an alternate effect — and so a power stunt — is held to), rather than
against the implementation.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from mm_companion.core import library
from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    ModifierSelection,
    Power,
    PowerEffectInstance,
    PowerGroup,
    power_is_stunt,
)
from mm_companion.core.rules import (
    apply_condition,
    clear_extra_effort,
    clear_stunts,
    determination_ranks,
    effect_allows_extra_effort,
    effect_current_rank,
    effect_effective_rank,
    effect_stat_rows,
    effect_total_cost,
    extra_effort_rank_increase,
    extra_effort_use,
    extra_effort_uses,
    has_extraordinary_effort,
    next_fatigue,
    node_cost,
    power_pl_violations,
    power_stunt_violations,
    powers_points_spent,
    pushable_effects,
    pushed_effects,
    spend_extra_effort,
    stunt_powers,
    stunt_source,
)
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.extra_effort import ExtraEffortDialog, character_effort_menu
from mm_companion.ui.sections import powers as powers_module
from mm_companion.ui.sections.powers import _DraggableCard


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _hero() -> tuple[Character, Power]:
    """A character with one pushable power — a Blast 6."""
    data = load_game_data()
    char = Character.new_default(data)
    blast = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=6)])
    char.powers.append(blast)
    return char, blast


def _rank_increase(data):
    use = extra_effort_use(data, "rank_increase")
    assert use is not None
    return use


# -- the price ---------------------------------------------------------------


def test_the_fatigue_ladder_is_climbed_one_rung_per_use() -> None:
    # "At the start of your next turn ... your character gains the Fatigued condition. A
    # Fatigued character who uses Extra Effort becomes Exhausted, and an Exhausted
    # character who uses Extra Effort becomes Incapacitated" (p21).
    data = load_game_data()
    char, _ = _hero()
    use = extra_effort_use(data, "bonus")

    assert next_fatigue(char, data) == "fatigued"
    spend_extra_effort(char, data, use)
    assert {c.condition_id for c in char.conditions} >= {"fatigued"}

    assert next_fatigue(char, data) == "exhausted"
    spend_extra_effort(char, data, use)
    ids = {c.condition_id for c in char.conditions}
    # Exhausted supersedes Fatigued, so climbing removes the rung climbed from — the
    # condition resolver's own doing, which is why the ladder goes through it.
    assert "exhausted" in ids and "fatigued" not in ids

    assert next_fatigue(char, data) == "incapacitated"
    spend_extra_effort(char, data, use)
    assert "incapacitated" in {c.condition_id for c in char.conditions}
    # Nothing worse for Extra Effort to do: the top rung is the answer from then on.
    assert next_fatigue(char, data) == "incapacitated"


def test_the_ladder_is_read_from_wherever_the_character_already_stands() -> None:
    data = load_game_data()
    char, _ = _hero()
    apply_condition(char, "fatigued", data)
    assert next_fatigue(char, data) == "exhausted"
    # And two rungs at once is what Extraordinary Effort will cost — a lookahead, so the
    # dialog can promise exactly what spending it will do.
    assert next_fatigue(char, data, 2) == "incapacitated"


def test_determination_shrugs_the_fatigue_off_entirely() -> None:
    # "Using Determination to immediately remove the Fatigued condition effectively lets
    # you perform one use of Extra Effort without becoming Fatigued" (p85).
    data = load_game_data()
    char, _ = _hero()
    outcome = spend_extra_effort(char, data, extra_effort_use(data, "action"), determination=True)
    assert char.conditions == []
    assert outcome.fatigue == ""
    assert "Determination" in outcome.note


def test_determination_and_extraordinary_effort_are_read_off_the_sheet() -> None:
    data = load_game_data()
    char, _ = _hero()
    assert determination_ranks(char, data) == 0
    assert has_extraordinary_effort(char, data) is False
    char.advantages.append(AdvantageSelection(name="Determination", rank=3))
    char.advantages.append(AdvantageSelection(name="Extraordinary Effort"))
    assert determination_ranks(char, data) == 3
    assert has_extraordinary_effort(char, data) is True


# -- what it buys ------------------------------------------------------------


def test_a_permanent_effect_cannot_be_pushed_and_a_sustained_one_can() -> None:
    # "A Permanent effect cannot be improved using Extra Effort" (p104); the Sustained
    # extra exists precisely to take that restriction off (p155).
    data = load_game_data()
    protection = PowerEffectInstance("protection", rank=8)
    assert effect_allows_extra_effort(protection, data) is False
    protection.extras.append(ModifierSelection(modifier_id="sustained_extra"))
    assert effect_allows_extra_effort(protection, data) is True

    # And the Permanent flaw does it the other way round: "you cannot improve a permanent
    # effect using Extra Effort, including using it for Power Stunts" (p159).
    flight = PowerEffectInstance("flight", rank=4)
    assert effect_allows_extra_effort(flight, data) is True
    flight.flaws.append(ModifierSelection(modifier_id="permanent_flaw"))
    assert effect_allows_extra_effort(flight, data) is False


def test_a_power_offers_only_its_pushable_effects() -> None:
    data = load_game_data()
    armour = Power(
        name="Battlesuit",
        effects=[PowerEffectInstance("protection", rank=8), PowerEffectInstance("flight", rank=4)],
    )
    assert [e.effect_id for e in pushable_effects(armour, data)] == ["flight"]
    bare = Power(name="Tough", effects=[PowerEffectInstance("protection", rank=8)])
    assert pushable_effects(bare, data) == []


def test_a_rank_increase_reaches_every_number_the_rank_reaches() -> None:
    # "You immediately increase the rank of one of your non-permanent effects by +1 until
    # the end of your turn" (p21).
    data = load_game_data()
    char, blast = _hero()
    effect = blast.effects[0]
    cost = effect_total_cost(effect, data)

    spend_extra_effort(char, data, _rank_increase(data), effect=effect, effect_name="Damage")

    assert effect.extra_effort == 1
    assert effect_current_rank(effect, data, char) == 7
    assert effect_effective_rank(effect, data, char) == 7
    # It is not a build change: the bought rank, and so the price, is untouched.
    assert effect.rank == 6
    assert effect_total_cost(effect, data) == cost


def test_a_push_may_break_the_power_level_cap() -> None:
    # "Its benefits can even increase your ranks or bonuses beyond the normal Power Level
    # limits" (p20) — and a Power Level check is a statement about the *build*, which is
    # why validation never sees the push.
    data = load_game_data()
    char, blast = _hero()
    char.power_level = 3  # a Blast 6 is already over; pushing must not add a warning
    before = power_pl_violations(blast, char, data)
    spend_extra_effort(char, data, _rank_increase(data), effect=blast.effects[0])
    assert power_pl_violations(blast, char, data) == before


def test_untapped_potential_deepens_the_increase() -> None:
    # "You gain 2 ranks rather than just 1 ... each additional rank adds 1" (p94).
    data = load_game_data()
    char, blast = _hero()
    assert extra_effort_rank_increase(char, data) == 1
    char.advantages.append(AdvantageSelection(name="Untapped Potential", rank=2))
    assert extra_effort_rank_increase(char, data) == 3

    spend_extra_effort(char, data, _rank_increase(data), effect=blast.effects[0])
    assert effect_current_rank(blast.effects[0], data, char) == 9


def test_extraordinary_effort_doubles_the_benefit_and_the_price() -> None:
    # "You can gain two of the listed benefits, even stacking two of the same benefit.
    # However, you also double the cost of the effort, acquiring two instances of the
    # Fatigued condition, likely leaving you Exhausted" (p86).
    data = load_game_data()
    char, blast = _hero()
    outcome = spend_extra_effort(
        char, data, _rank_increase(data), effect=blast.effects[0], doubled=True
    )
    assert outcome.ranks == 2
    assert effect_current_rank(blast.effects[0], data, char) == 8
    assert "exhausted" in {c.condition_id for c in char.conditions}


def test_the_push_is_cumulative_and_can_be_taken_back() -> None:
    data = load_game_data()
    char, blast = _hero()
    effect = blast.effects[0]
    use = _rank_increase(data)
    spend_extra_effort(char, data, use, effect=effect)
    spend_extra_effort(char, data, use, effect=effect)
    assert effect.extra_effort == 2  # two uses, two rungs of fatigue, two ranks
    assert pushed_effects(char) == [effect]

    assert clear_extra_effort(char) == 1
    assert effect.extra_effort == 0
    assert effect_current_rank(effect, data, char) == 6
    assert clear_extra_effort(char) == 0  # nothing left to clear


def test_the_card_says_why_its_rank_moved() -> None:
    data = load_game_data()
    char, blast = _hero()
    effect = blast.effects[0]
    assert not [row for row in effect_stat_rows(effect, data, char) if row.key == "extra_effort"]

    spend_extra_effort(char, data, _rank_increase(data), effect=effect)
    row = next(row for row in effect_stat_rows(effect, data, char) if row.key == "extra_effort")
    assert row.label == "Extra Effort"
    assert "+1 rank" in row.value and "end of your turn" in row.value


def test_the_note_says_what_was_gained_and_what_it_cost() -> None:
    data = load_game_data()
    char, blast = _hero()
    outcome = spend_extra_effort(
        char, data, _rank_increase(data), effect=blast.effects[0], effect_name="Fire Blast"
    )
    assert outcome.note == "pushed Fire Blast to rank 7 with Extra Effort — now Fatigued"

    plain = spend_extra_effort(char, data, extra_effort_use(data, "bonus"))
    assert plain.note == "used Extra Effort for a bonus on a check — now Exhausted"


def test_a_push_is_saved_and_an_untouched_effect_writes_nothing() -> None:
    data = load_game_data()
    char, blast = _hero()
    assert "extra_effort" not in blast.effects[0].to_dict()

    spend_extra_effort(char, data, _rank_increase(data), effect=blast.effects[0])
    reloaded = Character.from_dict(char.to_dict())
    assert reloaded.powers[0].effects[0].extra_effort == 1


# -- the controls ------------------------------------------------------------


class _AcceptedDialog:
    """A stand-in for the confirmation dialog: says yes, and takes the fatigue."""

    doubled = False
    determination = False
    spend_hero_point = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    def exec(self) -> int:
        return QDialog.DialogCode.Accepted


class _HeroPointDialog(_AcceptedDialog):
    determination = True
    spend_hero_point = True


class _CancelledDialog(_AcceptedDialog):
    def exec(self) -> int:
        return QDialog.DialogCode.Rejected


def _card_menu_labels(section, power) -> list[str]:
    card = next(c for c in section.findChildren(_DraggableCard) if c.node_id == power.id)
    return [action.text() for action in section.card_menu(card, power).actions()]


def test_a_card_offers_extra_effort_only_where_something_could_be_pushed(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)])
    armour = Power(name="Armour", effects=[PowerEffectInstance("protection", rank=6)])
    char.powers.extend([blast, armour])
    section = CharacterSheet(data, char).powers

    labels = _card_menu_labels(section, blast)
    assert "Rank increase (+1)" in labels and "Power stunt" in labels
    # An always-on Protection can be neither readied nor pushed, so its card still has
    # no menu at all rather than an empty one.
    card = next(c for c in section.findChildren(_DraggableCard) if c.node_id == armour.id)
    assert section.card_menu(card, armour).actions() == []


def test_pushing_from_a_card_charges_the_fatigue_and_writes_it_down(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    monkeypatch.setattr(powers_module, "ExtraEffortDialog", _AcceptedDialog)

    assert sheet.powers.use_extra_effort(
        _rank_increase(data), blast, blast.effects[0], "Fire Blast"
    )

    assert blast.effects[0].extra_effort == 1
    # The condition went on through the core resolver, and the chips followed it there.
    assert "fatigued" in {c.condition_id for c in char.conditions}
    assert any("Fatigued" in chip for chip in _condition_chips(sheet))
    notes = [label.text() for label in _history_labels(sheet)]
    assert notes == ["pushed Fire Blast to rank 7 with Extra Effort — now Fatigued"]


def test_a_cancelled_dialog_spends_nothing(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    monkeypatch.setattr(powers_module, "ExtraEffortDialog", _CancelledDialog)

    assert (
        sheet.powers.use_extra_effort(_rank_increase(data), blast, blast.effects[0], "Blast")
        is False
    )
    assert blast.effects[0].extra_effort == 0
    assert char.conditions == []


def test_the_hero_point_route_moves_the_pips_in_the_block_that_owns_them(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    sheet.system_info.set_hero_points(3)
    monkeypatch.setattr(powers_module, "ExtraEffortDialog", _HeroPointDialog)

    sheet.powers.use_extra_effort(_rank_increase(data), blast, blast.effects[0], "Fire Blast")

    # No fatigue — it was shrugged off — and the point came off the System block's pips
    # rather than being written to the model behind them.
    assert char.conditions == []
    assert sheet.system_info._hero_points.value() == 2
    assert char.characteristics["hero_points"] == 2


def test_clearing_from_a_card_takes_the_ranks_back(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    section = CharacterSheet(data, char).powers
    monkeypatch.setattr(powers_module, "ExtraEffortDialog", _AcceptedDialog)
    section.use_extra_effort(_rank_increase(data), blast, blast.effects[0], "Fire Blast")

    assert "Clear Extra Effort" in _card_menu_labels(section, blast)
    assert section.clear_extra_effort(blast) is True
    assert blast.effects[0].extra_effort == 0
    assert "Clear Extra Effort" not in _card_menu_labels(section, blast)


def test_the_system_block_offers_the_uses_that_name_nothing(qapp: QApplication) -> None:
    data = load_game_data()
    char, _ = _hero()
    section = CharacterSheet(data, char).system_info
    menu = section.extra_effort_menu()
    entries = [(action.text(), action.isEnabled()) for action in menu.actions() if action.text()]

    # The header states the price before anything is chosen.
    assert entries[0] == ("Extra Effort — Fatigued at the start of your next turn", False)
    offered = {text: enabled for text, enabled in entries[1:]}
    assert offered["Extra action"] is True
    assert offered["Bonus on a check"] is True
    # A power stunt has to name an effect, so it is shown disabled pointing at the card —
    # the book lists six uses and a menu of four would read as a shorter rule.
    assert offered["Power stunt — on the power's card"] is False
    # The rank increase can name something this block *does* own (Strength, a movement
    # mode), so it becomes a submenu of them rather than a dead entry.
    increase = next(a for a in menu.actions() if a.text() == "Rank increase")
    assert increase.menu() is not None
    inner = [a.text() for a in increase.menu().actions() if a.text()]
    assert inner[0] == "Push one of your own traits"
    assert "Strength (for Damage or Lifting)" in inner
    assert "Speed: Ground speed (in one mode of movement you have)" in inner
    # ...and the effect half still points at the card, from inside the submenu.
    assert inner[-1] == "A power's rank — on the power's card"


def test_only_the_movement_modes_a_character_has_are_offered(qapp: QApplication) -> None:
    from mm_companion.core.rules import pushable_traits

    data = load_game_data()
    char, _ = _hero()
    # "your movement Speed rank in one mode of movement you have" (p21) — so which modes
    # are offered is a fact about the sheet, not a list in the ruleset.
    assert [t.key for t in pushable_traits(char, data)] == ["ability:STR", "movement:ground"]

    char.powers.append(Power(name="Wings", effects=[PowerEffectInstance("flight", rank=4)]))
    assert [t.key for t in pushable_traits(char, data)] == [
        "ability:STR",
        "movement:ground",
        "movement:flight",
    ]


def test_pushing_strength_and_a_movement_mode_moves_the_sheet(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mm_companion.core.rules import effective_ability, pushable_traits, speed_lines

    data = load_game_data()
    char, _ = _hero()
    char.abilities["STR"] = 4
    char.powers.append(Power(name="Wings", effects=[PowerEffectInstance("flight", rank=4)]))
    sheet = CharacterSheet(data, char)
    monkeypatch.setattr("mm_companion.ui.sections.system_info.ExtraEffortDialog", _AcceptedDialog)
    use = extra_effort_use(data, "rank_increase")

    strength = next(t for t in pushable_traits(char, data) if t.key == "ability:STR")
    assert sheet.system_info.push_trait(use, strength)
    assert effective_ability(char, data, "STR") == 5
    assert "fatigued" in {c.condition_id for c in char.conditions}

    flight = next(t for t in pushable_traits(char, data) if t.key == "movement:flight")
    assert sheet.system_info.push_trait(use, flight)
    assert next(line.rank for line in speed_lines(char, data) if line.mode == "flight") == 5

    # The push is added on top of everything the build nets, never weighed against it:
    # "its benefits can even increase your ranks or bonuses beyond the normal Power Level
    # limits" (p20).
    assert sheet.system_info.push_trait(use, strength)
    assert effective_ability(char, data, "STR") == 6


def test_the_system_block_says_what_extra_effort_is_holding_up(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mm_companion.core.rules import pushable_traits

    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    monkeypatch.setattr("mm_companion.ui.sections.system_info.ExtraEffortDialog", _AcceptedDialog)
    note = sheet.system_info._effort_note

    # Nothing pushed, nothing said.
    assert not note.isVisible()

    strength = next(t for t in pushable_traits(char, data) if t.key == "ability:STR")
    sheet.system_info.push_trait(extra_effort_use(data, "rank_increase"), strength)
    # A pushed *trait* has no card to explain itself on, and the enhancement column that
    # shows it looks like any other bonus — so it is named here.
    assert note.text() == "⚡ Strength +1"

    # An effect already says so on its own card, so it is counted rather than named.
    blast.effects[0].extra_effort = 2
    sheet.system_info.refresh_derived()
    assert note.text() == "⚡ Strength +1, 1 effect"

    sheet.system_info.clear_extra_effort()
    sheet.system_info.refresh_derived()
    assert note.text() == ""


def test_the_system_block_charges_a_use_and_can_clear_the_pushes(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    monkeypatch.setattr("mm_companion.ui.sections.system_info.ExtraEffortDialog", _AcceptedDialog)

    assert sheet.system_info.use_extra_effort(extra_effort_use(data, "action"))
    assert "fatigued" in {c.condition_id for c in char.conditions}
    assert [label.text() for label in _history_labels(sheet)] == [
        "used Extra Effort for an extra action — now Fatigued"
    ]

    # Nothing is pushed, so there is nothing to clear and no entry offering to.
    labels = [a.text() for a in sheet.system_info.extra_effort_menu().actions()]
    assert not [text for text in labels if text.startswith("Clear")]
    blast.effects[0].extra_effort = 2
    labels = [a.text() for a in sheet.system_info.extra_effort_menu().actions()]
    assert "Clear the ranks pushed into 1 trait" in labels
    assert sheet.system_info.clear_extra_effort() is True
    assert blast.effects[0].extra_effort == 0


# -- power stunts ------------------------------------------------------------


def _stunt_of(source: Power, rank: int = 6) -> Power:
    """A stunt built off ``source`` — an Obscure, the book's own example (p20)."""
    return Power(
        name="Smokescreen",
        effects=[PowerEffectInstance("obscure", rank=rank)],
        stunt_of=source.id,
    )


def test_a_stunt_is_a_card_of_its_own_and_costs_nothing() -> None:
    # "A power stunt is a temporary Alternate Effect ... acquired through Extra Effort and
    # the spending of Hero Points rather than the creation of a permanent set of alternate
    # effects" (p20, p101) — so it is on the sheet, and not in the point total.
    data = load_game_data()
    char, blast = _hero()
    stunt = _stunt_of(blast, rank=2)
    char.powers.append(stunt)

    assert power_is_stunt(stunt) and not power_is_stunt(blast)
    assert stunt_source(stunt, char) is blast
    assert node_cost(stunt, data, char) == 0
    assert powers_points_spent(char, data) == node_cost(blast, data, char)


def test_a_stunt_is_not_saved_but_does_survive_an_undo_snapshot(tmp_path) -> None:
    char, blast = _hero()
    char.powers.append(_stunt_of(blast))
    # A stunt dragged into a group is still a stunt, and still must not reach the file.
    char.powers.append(PowerGroup(children=[_stunt_of(blast), Power(name="Kept")]))

    path = library.save_character(char, directory=tmp_path)
    reloaded = library.load_character(path)
    assert not stunt_powers(reloaded)
    assert [n.name for n in reloaded.powers] == ["Fire Blast", ""]
    assert [c.name for c in reloaded.powers[1].children] == ["Kept"]

    # Undo snapshots the model as JSON, and a stunt that vanished on the next undo would
    # be worse than one that outlived its scene — so the round trip keeps it.
    assert len(stunt_powers(Character.from_dict(char.to_dict()))) == 2


def test_a_stunt_may_not_cost_more_than_the_power_it_came_from() -> None:
    # "An alternate effect can have a total cost in Power Points no greater than the base
    # power the alternate effect extra is applied to" (p98).
    data = load_game_data()
    char, blast = _hero()  # Damage 6 — 6 PP
    stunt = _stunt_of(blast, rank=2)  # Obscure 2, one sense type — 4 PP
    char.powers.append(stunt)
    assert power_stunt_violations(stunt, char, data) == []

    stunt.effects[0].rank = 12  # 12 PP of Obscure off a 6 PP Blast
    breach = power_stunt_violations(stunt, char, data)
    assert len(breach) == 1
    assert "12 PP" in breach[0] and "6 PP" in breach[0] and "Fire Blast" in breach[0]
    # An ordinary power is not held to anything of the sort.
    assert power_stunt_violations(blast, char, data) == []


def test_a_stunt_whose_power_is_gone_says_so() -> None:
    data = load_game_data()
    char, blast = _hero()
    stunt = _stunt_of(blast, rank=2)
    char.powers.append(stunt)
    char.powers.remove(blast)
    assert stunt_source(stunt, char) is None
    assert "no longer on the sheet" in power_stunt_violations(stunt, char, data)[0]


def test_stunts_are_dropped_together_when_the_scene_ends() -> None:
    char, blast = _hero()
    char.powers.append(_stunt_of(blast))
    char.powers.append(PowerGroup(children=[_stunt_of(blast)]))
    assert len(stunt_powers(char)) == 2
    assert clear_stunts(char) == 2
    assert stunt_powers(char) == []
    assert clear_stunts(char) == 0


def test_a_stunt_is_built_first_and_charged_on_the_way_back(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    stunt_use = extra_effort_use(data, "power_stunt")

    # Choosing the stunt opens the constructor and charges nothing: the player has yet to
    # build anything, and a rung of fatigue for a stunt that does not exist is invented.
    assert sheet.powers.use_extra_effort(stunt_use, blast, blast.effects[0], "Damage")
    assert sheet.powers._windows and char.conditions == []

    monkeypatch.setattr(powers_module, "ExtraEffortDialog", _AcceptedDialog)
    built = _stunt_of(blast, rank=2)
    built.stunt_of = ""  # what the constructor hands back knows nothing about stunts
    assert sheet.powers._on_stunt_saved(built, stunt_use, blast, blast.effects[0], "Damage")

    assert built.stunt_of == blast.id
    assert char.powers[-1] is built
    assert "fatigued" in {c.condition_id for c in char.conditions}
    assert [label.text() for label in _history_labels(sheet)] == [
        "used Extra Effort for a power stunt on Damage — now Fatigued"
    ]


def test_cancelling_the_cost_drops_the_build_rather_than_giving_it_away(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = load_game_data()
    char, blast = _hero()
    sheet = CharacterSheet(data, char)
    monkeypatch.setattr(powers_module, "ExtraEffortDialog", _CancelledDialog)

    saved = sheet.powers._on_stunt_saved(
        _stunt_of(blast), extra_effort_use(data, "power_stunt"), blast, blast.effects[0], "Damage"
    )
    assert saved is False
    assert stunt_powers(char) == [] and char.conditions == []


def test_the_stunt_card_says_what_it_is_and_what_it_came_from(qapp: QApplication) -> None:
    data = load_game_data()
    char, blast = _hero()
    char.powers.append(_stunt_of(blast, rank=2))
    section = CharacterSheet(data, char).powers
    texts = [label.text() for label in section.findChildren(QLabel)]

    assert "✦ stunt of Fire Blast" in texts
    # And it says "Stunt" where every other card says what it cost, since 0 PP beside a
    # real build reads as a bug rather than as a rule.
    assert "Stunt" in texts and "6 PP" in texts


def test_a_stunt_can_be_pushed_but_not_stunted_off(qapp: QApplication) -> None:
    data = load_game_data()
    char, blast = _hero()
    stunt = _stunt_of(blast, rank=2)
    char.powers.append(stunt)
    section = CharacterSheet(data, char).powers

    labels = _card_menu_labels(section, stunt)
    # A stunt is a non-permanent effect the character is using, so Extra Effort can push
    # it; but a stunt is an alternate of a power you *have*, and this one was invented
    # this scene, so there is no stunt of a stunt.
    assert "Rank increase (+1)" in labels
    assert "Power stunt" not in labels
    assert "Power stunt" in _card_menu_labels(section, blast)


def test_the_system_menu_drops_the_stunts_it_offers_to(qapp: QApplication) -> None:
    data = load_game_data()
    char, blast = _hero()
    char.powers.append(_stunt_of(blast))
    sheet = CharacterSheet(data, char)

    labels = [action.text() for action in sheet.system_info.extra_effort_menu().actions()]
    assert "Drop 1 power stunt" in labels
    assert sheet.system_info.drop_stunts() is True
    assert stunt_powers(char) == []
    assert "Drop 1 power stunt" not in [
        action.text() for action in sheet.system_info.extra_effort_menu().actions()
    ]


def test_the_dialog_states_the_benefit_and_the_price(qapp: QApplication) -> None:
    data = load_game_data()
    char, blast = _hero()
    char.advantages.append(AdvantageSelection(name="Extraordinary Effort"))
    dialog = ExtraEffortDialog(
        char, data, _rank_increase(data), effect=blast.effects[0], effect_name="Fire Blast"
    )
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("Fire Blast runs at rank 7" in text for text in texts)
    assert "Fatigued" in dialog._take_it.text()

    # Doubling up restates both halves at once, so the two can never disagree.
    dialog._twice.setChecked(True)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("Fire Blast runs at rank 8" in text for text in texts)
    assert "Exhausted" in dialog._take_it.text()


def test_the_menu_is_built_from_the_ruleset_rather_than_from_python(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    char, _ = _hero()
    section = CharacterSheet(data, char).system_info
    menu = character_effort_menu(section, char, data, lambda use: None)
    listed = [action.text() for action in menu.actions() if action.text()][1:]
    assert len(listed) == len(extra_effort_uses(data))


# -- helpers -----------------------------------------------------------------


def _history_labels(sheet: CharacterSheet) -> list[QLabel]:
    """The Dice block's private history, one label per note card."""
    from mm_companion.ui.roll_history import NoteCard

    return [
        card.findChild(QLabel) for card in sheet.dice.view._local_history.findChildren(NoteCard)
    ]


def _condition_chips(sheet: CharacterSheet) -> list[str]:
    """What the Conditions block currently shows, as text."""
    return [label.text() for label in sheet.conditions.findChildren(QLabel)]
