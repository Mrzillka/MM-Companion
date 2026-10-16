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
    area_attack_spec,
    power_area_use,
)
from mm_companion.ui import action_run
from mm_companion.ui.action_ui import KIND_OUTCOME, KIND_ROLL_ASK, KIND_ROLL_CLOSED
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
    thug, mook, thug_sheet = _cast(window, data)
    # The Mook fails its Dodge, then fails its Toughness save by two degrees.
    monkeypatch.setattr(action_run, "resolve_group", stages([-1], [-2]))
    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], thug)
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
    thug, _mook, thug_sheet = _cast(window, data)
    seen: list[list] = []
    fake = stages([1], [1])

    def spy(lines, **kwargs):
        seen.append(lines)
        return fake(lines, **kwargs)

    monkeypatch.setattr(action_run, "resolve_group", spy)
    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], thug)
    area._rows[0].include.setChecked(True)
    area._resolve.click()
    dodge, save = seen
    assert dodge[0][1] == 18  # Dodge vs. 10 + 8
    assert save[0][1] == 10 + 4  # the save at half of rank 8


class Asked:
    """Everything the GM's bridge was asked to put to a player, captured."""

    def __init__(self, window: GMWindow, monkeypatch) -> None:
        self.cards: list[dict] = []
        self.prompts: list[tuple[str, str, str, dict]] = []
        self.removed: list[int] = []
        bridge = window.bridge
        monkeypatch.setattr(bridge, "prompt_roll", lambda spec: self.cards.append(spec) or True)
        monkeypatch.setattr(
            bridge,
            "prompt_action",
            lambda pid, kind, aid, payload=None: self.prompts.append((pid, kind, aid, payload))
            or True,
        )
        monkeypatch.setattr(bridge, "remove_roll", lambda seq: self.removed.append(seq) or True)

    def specs(self) -> list[RollSpec]:
        return [RollSpec.from_dict(spec) for spec in self.cards]

    def kinds(self) -> list[str]:
        return [kind for _pid, kind, _aid, _payload in self.prompts]


def _answer(spec: RollSpec, *, die: int, seq: int = 50) -> dict:
    """The roll a player's sheet makes off a request, as the history records it."""
    return {
        "seq": seq,
        "kind": "roll",
        "player_id": "p2",
        "player_name": "Nova",
        "label": spec.label,
        "die": die,
        "bonus": max(0, spec.modifier),
        "penalty": max(0, -spec.modifier),
        "dc": spec.dc,
        "degree": None if spec.dc is None else (1 if die + spec.modifier >= spec.dc else -2),
        "spec": spec.to_dict(),
    }


def test_a_player_in_the_area_is_asked_rather_than_rolled_for(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data)
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    asked = Asked(window, monkeypatch)
    rolled: list = []
    monkeypatch.setattr(action_run, "resolve_group", lambda lines, **kw: rolled.append(lines))
    applied: list = []
    monkeypatch.setattr(
        window,
        "impress_apply",
        lambda party, changes: applied.append((party.name, changes)) or True,
    )

    use = power_area_use(burst(), source.character, data)
    declared = window._area.declare("p1", AreaAttempt("act", use, targets=("s2",)).to_dict())
    assert declared is not None and declared.source.name == "Ana"
    area = window._area.open("act")
    assert [r.party.name for r in area.included()] == ["Nova"]
    area._resolve.click()

    assert rolled == []  # nobody the GM plays was in it
    # Asked twice over: a prompt on their screen, and a request in the history.
    assert asked.kinds() == [KIND_ROLL_ASK]
    (dodge,) = asked.specs()
    assert dodge.kind == KIND_AREA_DODGE and dodge.targets == ("Nova",)
    assert dodge.action_id == "act" and dodge.follow_up is None
    assert area.isVisible() and area._roll_waiting.isVisible()
    assert "Nova" in area.summary.text()

    # Their Dodge comes back — failed — and the save is asked for at full rank.
    window._area.take_roll({**_answer(dodge, die=1), "kind": "request", "seq": 7})
    window._area.take_roll(_answer(dodge, die=1))
    assert KIND_ROLL_CLOSED in asked.kinds() and asked.removed == [7]
    save = asked.specs()[-1]
    assert save.dc == 10 + 8 and save.targets == ("Nova",)
    assert "Dodge" in area.result_text("s2")

    # The save fails: the GM applies what it did, and tells the player.
    window._area.take_roll(_answer(save, die=1, seq=51))
    assert applied and applied[0][0] == "Nova"
    assert KIND_OUTCOME in asked.kinds()
    assert "act" not in window._area.declared or not area._roll_waiting.isVisible()
    action = next(c for c in window._history.cards() if isinstance(c, ActionCard))
    assert action.button.isHidden()
    area.close()


def test_the_gm_can_roll_for_a_player_who_does_not_answer(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data)
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    Asked(window, monkeypatch)
    monkeypatch.setattr(window, "impress_apply", lambda party, changes: True)
    monkeypatch.setattr(action_run, "resolve_group", stages([1]))
    use = power_area_use(burst(), source.character, data)
    window._area.declare("p1", AreaAttempt("act", use, targets=("s2",)).to_dict())
    area = window._area.open("act")
    area._resolve.click()
    declared = window._area.declared["act"]
    assert declared.run.waiting() == ["Nova"]
    area._roll_waiting.click()
    # The Dodge is in (made, off their snapshot); the half-rank save is asked for next.
    assert "Dodge" in area.result_text("s2")
    assert declared.run.waiting() == ["Nova"]
    area.close()


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
    window._area.take_roll({"seq": 9, "die": 15, "bonus": 6, "penalty": 0, "spec": spec.to_dict()})
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
    thug, _mook, thug_sheet = _cast(window, data)

    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], thug)
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


def test_a_hosted_npc_affliction_lands_its_conditions(qapp, window: GMWindow, data) -> None:
    """The review's worst find: through the session an Affliction lost its ladder."""
    import random

    from mm_companion.core.session import discovery
    from mm_companion.ui.session_dialogs import HostOptions

    window.bridge._publish_session = lambda port, **kw: discovery.Reachability(
        host="127.0.0.1", port=port, method=discovery.METHOD_LAN
    )
    window.start_hosting(HostOptions(name="Table", port=0, tunnel="", relay="", use_relay=False))
    _wait(qapp, lambda: window.bridge.hosting)
    haze = Power(
        name="Haze",
        effects=[
            PowerEffectInstance(
                "affliction",
                rank=30,  # nobody makes a DC 40 save
                extras=[ModifierSelection("area_effect")],
                config={
                    "resistance": "Will",
                    "overcomeBy": "Will",
                    "degree1": "dazed",
                    "degree2": "stunned",
                    "degree3": "incapacitated",
                },
            )
        ],
    )
    source, targets = _parties(data)
    mook = targets[1]
    window.impress_parties = lambda: ([source], targets)
    applied: list = []
    window.impress_apply = lambda party, changes: applied.append((party.name, changes)) or True
    use = power_area_use(haze, source.character, data)
    # The GM's own seat goes through the server's sanitizer too, so this is the wire.
    window.bridge.send_action_request(
        "area", "act", AreaAttempt("act", use, targets=("s1",)).to_dict()
    )
    _wait(qapp, lambda: "act" in window._area.declared)
    window.bridge.server._rng = random.Random(1)
    area = window._area.open("act")
    assert [r.party.name for r in area.included()] == [mook.name]
    area._resolve.click()
    _wait(qapp, lambda: bool(applied))
    (name, changes), *_ = applied
    assert name == "Mook" and changes


# -- the review's fixes in the windows ---------------------------------------------------------


def test_a_waiting_action_card_survives_a_replay_and_the_trim(qapp, monkeypatch) -> None:
    from mm_companion.ui import roll_history

    panel = RollHistoryPanel(gm=True)
    waiting = ActionCard("act", "Ana uses Fire Burst", "Resolve Area Attack")
    done = ActionCard("old", "Bo uses Haze", "Resolve Area Attack")
    done.set_done("Resolved")
    panel.add_card(done)
    panel.add_card(waiting)
    # A reconnect's Welcome replays the log over the panel.
    panel.set_rolls([{"seq": 1, "kind": "note", "player_name": "GM", "text": "hello"}])
    cards = panel.cards()
    assert cards[0] is waiting and done not in cards
    # And a full log never pushes it off the bottom.
    monkeypatch.setattr(roll_history, "MAX_CARDS", 2)
    for seq in range(2, 6):
        panel.add_roll({"seq": seq, "kind": "note", "player_name": "GM", "text": f"n{seq}"})
    assert waiting in panel.cards()


def test_the_area_line_asks_for_an_area_not_a_roll(qapp) -> None:
    from mm_companion.ui.cards.rolls import RollLine, RollsFooter

    footer = RollsFooter([RollSpec(label="Area: Sphere", kind=KIND_AREA, trait_key="p1")])
    rolled: list = []
    aimed: list = []
    footer.rollRequested.connect(rolled.append)
    footer.areaRequested.connect(aimed.append)
    footer.findChild(RollLine).clicked.emit()
    assert aimed == ["p1"] and rolled == []


def test_the_sheet_opens_the_area_dialog_without_blocking_the_board(qapp, data) -> None:
    char = creature(data, "Ana")
    char.powers.append(burst())
    sheet = CharacterSheet(data, char)
    dice = sheet.section("dice")
    # The Powers block's area line reaches the roller through its own topic.
    sheet.powers.areaRequested.emit(char.powers[0].id)
    dialog = dice._area_dialog
    assert dialog is not None and dialog.isVisible()
    # Modal would swallow the press that drags a scene card into it.
    assert not dialog.isModal()
    dialog.close()
    assert dice._area_dialog is None


def test_an_offline_npc_attack_reaches_the_area_window(qapp, window: GMWindow, data) -> None:
    thug, mook, thug_sheet = _cast(window, data)
    area = window._area.start_for_npc(thug_sheet, thug_sheet.powers[0], thug)
    area.set_primary(window._scene_entry_for(SCENE_NPC, mook).ref)
    assert area._roll_attack.isVisibleTo(area)
    spec = area_attack_spec(area.attempt.use, action_id=area.action_id, target="Mook")
    # What the GM's roller hands the window off the air: no rollAdded will follow.
    window._show_offline_roll(
        {"die": 15, "bonus": 6, "penalty": 0, "dc": None, "label": "", "spec": spec.to_dict()}
    )
    assert area._attack == (21, 15)
    assert not area._roll_attack.isVisibleTo(area)


def test_a_fresher_snapshot_rereads_the_hit_unless_the_gm_set_it(qapp, data) -> None:
    source, targets = _parties(data)
    use = power_area_use(burst(), source.character, data)
    window = AreaWindow(
        data, attempt=AreaAttempt("act", use, primary="s2"), source=source, targets=targets
    )
    window.set_attack(14, 9)
    assert window.hit()  # against a fresh creature's Defense
    tougher = creature(data, "Nova")
    tougher.resistances[data.system.trait_keys.defense] = 10
    window.set_party_character("s2", tougher)
    assert not window.hit()
    window._hit.click()  # the GM overrules it
    window.set_party_character("s2", creature(data, "Nova"))
    window.set_party_character("s2", tougher)
    assert window.hit()
    window.close()


def test_namesakes_on_the_board_are_numbered_and_found_by_file(
    qapp, window: GMWindow, data
) -> None:
    first = write_npc(data, "Thug", power=burst())
    second = write_npc(data, "Thug", power=burst())
    for path in (first, second):
        window._register_npc(path)
        window._set_in_scene(SCENE_NPC, path.name, True)
    _sources, targets = window.impress_parties()
    assert sorted(p.name for p in targets) == ["Thug (1)", "Thug (2)"]

    sheet = library.load_character(second)
    area = window._area.start_for_npc(sheet, sheet.powers[0], second.name, name="Thug")
    # The second Thug attacks: the first is a target, and the attacker is not.
    assert [r.party.source for r in area._rows] == [first.name]
    assert area._source.name in ("Thug (1)", "Thug (2)")
    area.close()


def test_a_players_cover_and_adjustment_reach_their_request(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data)
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    asked = Asked(window, monkeypatch)
    monkeypatch.setattr(window, "impress_apply", lambda party, changes: True)
    use = power_area_use(burst(), source.character, data)
    window._area.declare("p1", AreaAttempt("act", use, targets=("s2",)).to_dict())
    area = window._area.open("act")
    (row,) = area.included()
    row.cover.setCurrentIndex(row.cover.findData(5))
    row.modifier.setValue(2)
    area._resolve.click()
    (dodge,) = asked.specs()
    assert dodge.modifier == 7  # cover and the GM's bonus, on the Dodge
    # A made Dodge: the save is asked for at half rank, with the bonus alone.
    window._area.take_roll(_answer(dodge, die=20))
    save = asked.specs()[-1]
    assert save.modifier == 2 and save.dc == 10 + 4
    area.close()


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


# -- the reworked flow -----------------------------------------------------------------------


def test_an_answered_request_joins_its_actions_table(qapp) -> None:
    panel = RollHistoryPanel(gm=True)
    panel.add_roll(_record(1, "act.1", "Mook: Dodge", "Mook", -1))
    asked = RollSpec(
        label="Dodge Ana's Fire Burst (area)",
        kind=KIND_AREA_DODGE,
        targets=("Nova",),
        action_id="act",
        title="GM: Fire Burst (area)",
    )
    panel.add_roll(
        {
            "seq": 2,
            "kind": "roll",
            "player_name": "Nova",
            "label": asked.label,
            "die": 12,
            "bonus": 3,
            "penalty": 0,
            "dc": 18,
            "degree": -1,
            "spec": asked.to_dict(),
        }
    )
    (table,) = panel.cards()
    assert isinstance(table, GroupTableCard)
    assert any("Nova" in line and "Dodge" in line for line in table.lines())


def test_an_npc_card_offers_area_and_impress(qapp, window: GMWindow, data) -> None:
    thug, mook, _sheet = _cast(window, data)
    card = window._npc_state[thug].card
    assert card.area_builds() and not card._area_button.isHidden()
    plain = window._npc_state[mook].card
    assert plain.area_builds() == [] and plain._area_button.isHidden()

    opened: list = []
    window._area.start_for_npc = lambda *args, **kw: opened.append(args) or None
    card._area_button.click()  # one attack: straight to the window
    assert opened and opened[0][2] == thug
    menu = card.build_context_menu()
    assert any(a.text() == "Impress…" for a in menu.actions())


def test_any_npc_attack_can_be_spread_from_its_card(qapp, window: GMWindow, data) -> None:
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)])
    thug = write_npc(data, "Thug", power=blast)
    window._register_npc(thug)
    entry = window._npc_state[thug.name]
    # A plain attack, its free-use switch off: the card still offers it.
    assert [b.name for b in entry.card.area_builds()] == ["Blast"]
    window._start_card_area(thug.name, entry.character.powers[0].id)
    (area,) = window._area.windows.values()
    assert area.use.free and area._shape.isEnabled()
    area._shape.setCurrentIndex(area._shape.findData("line"))
    assert area.use.shape == "line"
    area.close()


def test_a_bare_declaration_leaves_the_gm_every_choice(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data)
    source.character.powers.append(burst())
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    use = power_area_use(burst(), source.character, data)
    window._area.declare("p1", AreaAttempt("act", use, source_name="Ana").to_dict())
    action = next(c for c in window._history.cards() if isinstance(c, ActionCard))
    assert "yours to set" in action.text()
    area = window._area.open("act")
    assert area.included() == []
    # The GM picks a primary; a player's attack is asked of them.
    asked = Asked(window, monkeypatch)
    area.set_primary("s1")
    assert area._roll_attack.text().startswith("🎲 Ask")
    area._roll_attack.click()
    (attack,) = asked.specs()
    assert attack.action_id == "act" and attack.targets == ("Mook",)
    assert not area._roll_attack.isVisibleTo(area)
    window._area.take_roll({**_answer(attack, die=18), "dc": None, "degree": None})
    assert area._attack is not None and area._attack[1] == 18
    area.close()


def test_the_sheet_sends_an_area_bare_unless_the_player_chooses(qapp, data, monkeypatch) -> None:
    from mm_companion.ui import action_ui
    from mm_companion.ui.sections import dice as dice_module

    class Bridge:
        in_session = True
        is_gm = False

        def __init__(self) -> None:
            self.sent: list = []
            self.notes: list = []
            self.rules: dict = {}

        def mod_state(self, mod_id=""):
            return self.rules

        def scene(self):
            return list(SCENE)

        def own_player_id(self):
            return "me"

        def send_action_request(self, kind, action_id, payload=None):
            self.sent.append((kind, payload))
            return True

        def post_note(self, text):
            self.notes.append(text)
            return True

    bridge = Bridge()
    monkeypatch.setattr(dice_module, "live_session", lambda: bridge)
    char = creature(data, "Ana")
    char.powers.append(burst())
    sheet = CharacterSheet(data, char)
    dice = sheet.section("dice")

    # Off by default: no window, the power goes to the GM as it stands.
    assert dice.start_area(char.powers[0].id) is None
    ((kind, payload),) = bridge.sent
    attempt = AreaAttempt.from_dict(payload)
    assert kind == "area" and attempt.targets == () and attempt.primary == ""
    assert bridge.notes and "sets it up" in bridge.notes[0]

    # The player wants to choose, and the table lets them: the window opens.
    storage.set_resolve_actions_myself(True)
    dialog = dice.start_area(char.powers[0].id)
    assert dialog is not None
    dialog.close()
    # ...unless the GM keeps the choosing.
    bridge.rules = {action_ui.TABLE_ACTIONS_KEY: {"players_choose": False}}
    assert dice.start_area(char.powers[0].id) is None
