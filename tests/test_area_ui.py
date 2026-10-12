"""The area attack's windows, the GM's side of resolving one, and the table card.

Offscreen Qt. The rules are proven headless in ``test_area.py``; what is proven here is
that the windows hand those rules the right inputs, that a GM's resolve rolls the NPCs
in two stages and lands conditions on the right creature, that a player in the area is
asked rather than rolled for, and that a group lands in the history as one table.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.dice import CheckResult, GroupResult
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    KIND_AREA,
    KIND_AREA_DODGE,
    AreaAttempt,
    RollSpec,
    power_area_use,
)
from mm_companion.ui import area_gm
from mm_companion.ui.area import AreaDialog, AreaWindow, TargetDropField
from mm_companion.ui.cards.card import DraggableCard
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty
from mm_companion.ui.roll_history import (
    ActionCard,
    GroupTableCard,
    RollHistoryPanel,
    table_key,
)
from mm_companion.ui.session_bridge import set_active_session


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def data():
    return load_game_data()


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    for declared in made._area.declared.values():
        if declared.window is not None:
            declared.window.close()
    made.bridge.stop()


def burst(rank: int = 8) -> Power:
    return Power(
        name="Fire Burst",
        effects=[
            PowerEffectInstance(
                "damage",
                rank=rank,
                extras=[ModifierSelection("area_effect", config={"shape": "cone"})],
            )
        ],
    )


def creature(data, name: str) -> Character:
    character = Character.new_default(data)
    character.profile["hero_name"] = name
    character.power_level = 10
    return character


def write_npc(data, name: str, *, power: Power | None = None) -> Path:
    character = creature(data, name)
    if power is not None:
        character.powers.append(power)
    return library.save_character(character, directory=storage.get_workspace().gm_characters_dir)


def stages(*rounds: list[int]):
    """A stand-in for resolve_group: each call fails or makes its lines by these degrees."""
    queue = list(rounds)

    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        degrees = queue.pop(0)
        results = tuple(
            CheckResult(10, modifier, 10 + modifier, (dc or 0) + offset, degree, False)
            for (modifier, offset), degree in zip(lines, degrees, strict=False)
        )
        return GroupResult(None, None, results)

    return fake


# -- the history's table card -----------------------------------------------------


def _record(seq: int, group: str, label: str, name: str, degree: int, **spec) -> dict:
    return {
        "seq": seq,
        "kind": "roll",
        "player_name": "GM",
        "label": label,
        "die": 10,
        "bonus": 2,
        "penalty": 0,
        "dc": 18,
        "degree": degree,
        "group": group,
        "spec": RollSpec(
            label=label, targets=(name,), title="GM: Fire Burst (area)", **spec
        ).to_dict(),
    }


def test_a_stage_suffix_names_the_same_table() -> None:
    assert table_key("abc-12ef.1") == table_key("abc-12ef.2") == "abc-12ef"
    assert table_key("abc-12ef") == "abc-12ef"


def test_both_stages_of_a_group_land_in_one_table_a_row_per_creature(qapp) -> None:
    panel = RollHistoryPanel(gm=True)
    panel.add_roll(_record(1, "a.1", "Mook: Dodge", "Mook", -1))
    panel.add_roll(_record(2, "a.1", "Thug: Dodge", "Thug", 1))
    panel.add_roll(
        _record(3, "a.2", "Mook: Toughness", "Mook", -2, outcomes=("Dazed", "Staggered"))
    )
    (card,) = panel.cards()
    assert isinstance(card, GroupTableCard)
    heading, mook, thug = card.lines()
    assert "Fire Burst (area)" in heading
    # The Mook's Dodge and its save share a row, and the row ends on what it did.
    assert "Dodge" in mook and "Toughness" in mook and "Staggered" in mook
    assert "Thug" in thug and "Toughness" not in thug

    # Striking one roll takes its cell; the last one takes the table.
    panel.remove_roll(3)
    assert "Toughness" not in card.lines()[1]
    panel.remove_roll(1)
    panel.remove_roll(2)
    assert panel.cards() == []


def test_a_grouped_roll_that_provokes_a_follow_up_keeps_its_own_card(qapp) -> None:
    # Request-mode Impress: the check's card is where everyone finds the Resist button.
    panel = RollHistoryPanel()
    record = _record(1, "b", "Impress", "", 1)
    record["spec"] = RollSpec(label="Impress", follow_up=RollSpec(label="Resist")).to_dict()
    panel.add_roll(record)
    (card,) = panel.cards()
    assert not isinstance(card, GroupTableCard)


# -- the attacker's dialog ---------------------------------------------------------------


SCENE = [
    {"ref": "s1", "name": "Mook"},
    {"ref": "s2", "name": "Thug"},
    {"ref": "s3", "name": "Nova", "player_id": "me"},
]


def test_the_dialog_takes_dropped_cards_as_primary_and_area(qapp, data) -> None:
    hero = creature(data, "Ana")
    dialog = AreaDialog(data, hero, burst(), scene=SCENE, own_player_id="me")
    assert not dialog._ok.isEnabled()  # nobody aimed at yet
    # One's own card is no target.
    assert not dialog.area.add_ref("s3")
    assert dialog.primary.add_ref("s1")
    assert dialog.area.add_ref("s2")
    assert dialog._ok.isEnabled()
    # A creature is the primary or merely caught — never both.
    dialog.area.add_ref("s1")
    assert dialog.primary_ref() == "" and dialog.target_refs() == ["s2", "s1"]
    dialog.primary.add_ref("s2")
    assert dialog.primary_ref() == "s2" and dialog.target_refs() == ["s1"]

    attempt = dialog.attempt("act", source_name="Ana")
    assert attempt.primary == "s2" and attempt.targets == ("s1",)
    assert attempt.use.shape == "cone"
    attack = dialog.attack_spec("act", source_name="Ana")
    assert attack is not None and attack.action_id == "act" and "Thug" in attack.label
    assert attack.follow_up is None  # the primary's save is the GM's to resolve
    note = dialog.note_text(source_name="Ana")
    assert "Fire Burst" in note and "Thug" in note and "Mook" in note


def test_a_single_slot_keeps_only_the_last_card(qapp) -> None:
    field = TargetDropField("t", resolve=lambda ref: (ref, ref.upper()), single=True)
    field.add_ref("a")
    field.add_ref("b")
    assert field.refs() == ["b"]


def test_a_free_use_picks_its_shape_in_the_dialog(qapp, data) -> None:
    hero = creature(data, "Ana")
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=12)], area_use=True)
    dialog = AreaDialog(data, hero, blast, scene=SCENE, own_player_id="me")
    assert dialog._shape is not None
    dialog._shape.setCurrentIndex(dialog._shape.findData("line"))
    use = dialog.use()
    assert use.shape == "line" and use.free and use.rank == 10


def test_off_the_air_the_dialog_rolls_and_leaves_a_dodge_request(qapp, data) -> None:
    dialog = AreaDialog(data, creature(data, "Ana"), burst())
    assert dialog.primary is None and dialog._ok.isEnabled()
    (request,) = dialog.offline_requests(source_name="Ana")
    assert request.kind == KIND_AREA_DODGE and request.follow_up is not None


# -- the GM's window ---------------------------------------------------------------------


def _parties(data) -> tuple[ImpressParty, list[ImpressParty]]:
    source = ImpressParty("s0", "Ana", PARTY_PLAYER, "p1", creature(data, "Ana"))
    mook = ImpressParty("s1", "Mook", PARTY_NPC, "mook.json", creature(data, "Mook"))
    nova = ImpressParty("s2", "Nova", PARTY_PLAYER, "p2", creature(data, "Nova"))
    return source, [source, mook, nova]


def test_the_gm_window_ticks_a_dropped_card_and_reads_the_attack(qapp, data) -> None:
    source, targets = _parties(data)
    use = power_area_use(burst(), source.character, data)
    attempt = AreaAttempt("act", use, primary="s1", source_name="Ana")
    window = AreaWindow(data, attempt=attempt, source=source, targets=targets)
    # The attacker is no target, the primary starts out included.
    assert [r.party.name for r in window._rows] == ["Mook", "Nova"]
    assert [r.party.name for r in window.included()] == ["Mook"]
    # A roster card's own ref names the creature too.
    assert window.include_ref("player:p2")
    assert [r.party.name for r in window.included()] == ["Mook", "Nova"]

    window.set_attack(30, 14)  # well past a fresh creature's Defense
    assert window.hit()
    plan = window.plan()
    assert [(t.party.name, t.primary) for t in plan.targets] == [("Mook", True), ("Nova", False)]
    window.set_attack(2, 1)
    assert not window.hit()
    window.close()


# -- resolving, end to end in the GM window -------------------------------------------------


def _cast(window: GMWindow, data) -> tuple[str, str, Character]:
    """A Thug with a Fire Burst in the cast, and a Mook on the board."""
    thug = write_npc(data, "Thug", power=burst())
    mook = write_npc(data, "Mook")
    window._register_npc(thug)
    window._register_npc(mook)
    window._set_in_scene(SCENE_NPC, mook.name, True)
    character = library.load_character(thug)
    return thug.name, mook.name, character


def test_the_gm_resolves_an_npcs_area_in_two_stages(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    _thug, mook, thug_sheet = _cast(window, data)
    # The Mook fails its Dodge, then fails its Toughness save by two degrees.
    monkeypatch.setattr(area_gm, "resolve_group", stages([-1], [-2]))
    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], "Thug")
    assert area is not None
    (row,) = area._rows
    row.include.setChecked(True)
    area._resolve.click()

    model = window._npc_state[mook].character
    assert model.conditions, "the failed save's rung should land on the Mook"
    cards = window._history.cards()
    table = next(c for c in cards if isinstance(c, GroupTableCard))
    _heading, mook_row = table.lines()
    assert "Dodge" in mook_row and "Toughness" in mook_row
    action = next(c for c in cards if isinstance(c, ActionCard))
    assert action.button.isHidden()  # resolved: the button is gone


def test_a_made_dodge_rolls_the_save_at_half_rank(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    _thug, _mook, thug_sheet = _cast(window, data)
    seen: list[list] = []
    fake = stages([1], [1])

    def spy(lines, **kwargs):
        seen.append(lines)
        return fake(lines, **kwargs)

    monkeypatch.setattr(area_gm, "resolve_group", spy)
    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], "Thug")
    area._rows[0].include.setChecked(True)
    area._resolve.click()
    dodge, save = seen
    assert dodge[0][1] == 18  # Dodge vs. 10 + 8
    assert save[0][1] == 10 + 4  # the save at half of rank 8


def test_a_player_in_the_area_is_asked_rather_than_rolled_for(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data)
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    asked: list[dict] = []
    monkeypatch.setattr(window.bridge, "prompt_roll", lambda spec: asked.append(spec) or True)
    rolled: list = []
    monkeypatch.setattr(area_gm, "resolve_group", lambda lines, **kw: rolled.append(lines))

    use = power_area_use(burst(), source.character, data)
    declared = window._area.declare("p1", AreaAttempt("act", use, targets=("s2",)).to_dict())
    assert declared is not None and declared.source.name == "Ana"
    area = window._area.open("act")
    assert [r.party.name for r in area.included()] == ["Nova"]
    area._resolve.click()

    assert rolled == []  # nobody the GM plays was in it
    (request,) = (RollSpec.from_dict(s) for s in asked)
    assert request.kind == KIND_AREA_DODGE and request.targets == ("Nova",)
    assert request.follow_up is not None and request.follow_up_always


def test_a_declaration_waits_in_the_gm_history_and_picks_up_the_attack(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data)
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    use = power_area_use(burst(), source.character, data)
    window._area.declare("p1", AreaAttempt("act", use, primary="s1").to_dict())
    action = next(c for c in window._history.cards() if isinstance(c, ActionCard))
    assert "Fire Burst" in action.text() and "Mook" in action.text()

    # The attack against the primary arrives as an ordinary roll carrying the id.
    spec = RollSpec(label="Fire Burst: area attack", action_id="act")
    window._area._on_roll({"seq": 9, "die": 15, "bonus": 6, "penalty": 0, "spec": spec.to_dict()})
    assert window._area.declared["act"].attack == (21, 15)
    assert "attack 21" in action.text()
    area = window._area.open("act")
    assert area._attack == (21, 15)


# -- the power card -------------------------------------------------------------------------


def test_an_attack_card_offers_the_free_area_switch(qapp, data) -> None:
    char = Character.new_default(data)
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)])
    char.powers.append(blast)
    section = CharacterSheet(data, char).powers
    card = next(c for c in section.findChildren(DraggableCard) if c.node_id == blast.id)
    assert card.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    switch = section.card_menu(card, blast).actions()[-1]
    assert switch.text() == "Use as an area attack" and not switch.isChecked()
    switch.trigger()
    assert blast.area_use
    assert section._rolls(blast)[-1].kind == KIND_AREA


# -- through a real session -------------------------------------------------------------------


def _wait(qapp, predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        qapp.processEvents()
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def test_a_hosted_resolve_rolls_both_stages_on_the_server(qapp, window: GMWindow, data) -> None:
    import random

    from mm_companion.core.session import discovery
    from mm_companion.ui.session_dialogs import HostOptions

    window.bridge._publish_session = lambda port, **kw: discovery.Reachability(
        host="127.0.0.1", port=port, method=discovery.METHOD_LAN
    )
    window.start_hosting(HostOptions(name="Table", port=0, tunnel="", relay="", use_relay=False))
    _wait(qapp, lambda: window.bridge.hosting)
    window.bridge.server._rng = random.Random(3)
    _thug, _mook, thug_sheet = _cast(window, data)

    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], "Thug")
    area._rows[0].include.setChecked(True)
    area._resolve.click()

    def grouped() -> list[dict]:
        return [r for r in window.bridge.history() if r.get("group")]

    _wait(qapp, lambda: len(grouped()) == 2)
    dodge, save = grouped()
    assert table_key(dodge["group"]) == table_key(save["group"]) != dodge["group"]
    assert dodge["dc"] == 18 and save["dc"] in (14, 18)  # half rank only on a made Dodge
    assert save["dc"] == (14 if dodge["degree"] > 0 else 18)
    tables = [c for c in window._history.cards() if isinstance(c, GroupTableCard)]
    assert len(tables) == 1 and len(tables[0].lines()) == 2


def test_a_request_says_who_it_is_for_and_calls_out_the_reader(qapp) -> None:
    from mm_companion.ui.roll_history import RequestCard

    request = {
        "seq": 4,
        "kind": "request",
        "player_name": "GM",
        "spec": RollSpec(label="Dodge", kind=KIND_AREA_DODGE, targets=("Nova", "Bo")).to_dict(),
    }
    mine = RequestCard(request, own_names=frozenset({"Nova"}))
    theirs = RequestCard(request, own_names=frozenset({"Cy"}))
    assert mine.for_reader and not theirs.for_reader
