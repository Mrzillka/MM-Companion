"""Extra Effort: what it buys, what it costs, and where it is spent from.

Checked against the core rulebook p20-22 (the mechanic), p85-86 and p94 (the three
advantages that change it) and p104/p159 (the Permanent duration that refuses it),
rather than against the implementation.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    apply_condition,
    clear_extra_effort,
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
    power_pl_violations,
    pushable_effects,
    pushed_effects,
    spend_extra_effort,
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

    assert sheet.powers.use_extra_effort(_rank_increase(data), blast.effects[0], "Fire Blast")

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

    assert sheet.powers.use_extra_effort(_rank_increase(data), blast.effects[0], "Blast") is False
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

    sheet.powers.use_extra_effort(_rank_increase(data), blast.effects[0], "Fire Blast")

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
    section.use_extra_effort(_rank_increase(data), blast.effects[0], "Fire Blast")

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
    # The two that have to name an effect are shown, disabled, pointing at the card —
    # the book lists six uses and a menu of four would read as a shorter rule.
    assert offered["Rank increase — on the power's card"] is False
    assert offered["Power stunt — on the power's card"] is False


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
    assert "Clear the ranks pushed into 1 effect" in labels
    assert sheet.system_info.clear_extra_effort() is True
    assert blast.effects[0].extra_effort == 0


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
