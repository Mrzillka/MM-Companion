"""The player's side of a session: the join dialog and the snapshot pusher.

The pusher runs against a real loopback server — its job is to talk to one — but
its interesting property is what it does *not* send: a burst of edits has to
coalesce into a single snapshot, because a snapshot is the largest message the
protocol carries and a per-keystroke push is what would make relayed traffic
expensive. :func:`test_a_realistic_snapshot_is_small_enough_to_relay` is the
measurement the relay bandwidth estimate rests on.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import apply_condition
from mm_companion.core.session import discovery
from mm_companion.core.session.model import new_session
from mm_companion.core.session.protocol import (
    MAX_MESSAGE_BYTES,
    CharacterSnapshot,
    encode,
    sanitize_snapshot,
)
from mm_companion.ui.blocks.bus import BUILD_CHANGED, EDITED
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.session_bridge import SessionBridge, active_session, set_active_session
from mm_companion.ui.session_dialogs import NO_CHARACTER, JoinSessionDialog
from mm_companion.ui.session_player import SnapshotPusher, snapshot_size
from mm_companion.ui.start_window import StartWindow


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
def table(qapp: QApplication):
    """A hosted session on loopback and a player bridge joined to it."""
    host = SessionBridge()
    address = host.host(new_session("Table"), port=0, bind="127.0.0.1")
    code = _code_for(host, address)
    player = SessionBridge()
    player.join(code, "Aria")
    yield host, player
    player.stop()
    host.stop()


def _code_for(host: SessionBridge, address: tuple[str, int]):
    return discovery.decode_join_code(
        discovery.encode_join_code(address[0], address[1], host.state.host_token)
    )


def wait_for(qapp: QApplication, predicate, timeout: float = 5.0) -> bool:
    """Pump the event loop until *predicate* holds (queued signals land here)."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


# -- how big a snapshot actually is ----------------------------------------


def built_character() -> Character:
    """A plausible mid-campaign PL 10 sheet — not a blank one, not a monster.

    Full ability spread, a dozen skills, ten advantages, four powers with
    modifiers, a filled-in profile, and a couple of conditions. This is the
    shape the relay cost estimate is built on.
    """
    data = load_game_data()
    character = Character.new_default(data)
    character.profile.update(
        {
            "hero_name": "Nightingale",
            "name": "Wren Alcott",
            "identity": "Secret",
            "occupation": "Field medic",
            "base": "The Roost, dockside",
            "group": "The Long Watch",
            "description": "Lean, grey coat, always the first one into the smoke.",
        }
    )
    ability_ranks = [4, 6, 2, 3, 5, 1, 4, 2]
    for index, ability in enumerate(data.abilities):
        character.abilities[ability.key] = ability_ranks[index % len(ability_ranks)]
    resistance_ranks = [8, 6, 6, 4]
    for index, resistance in enumerate(data.resistances):
        character.resistances[resistance.key] = resistance_ranks[index % len(resistance_ranks)]
    for skill in [s.name for s in data.skills][:12]:
        character.skill_ranks[skill] = 6
    for advantage in [a.name for a in data.advantages][:10]:
        character.advantages.append(AdvantageSelection(name=advantage, rank=2))
    modifiers = [m.id for m in data.modifiers][:2]
    for index, effect in enumerate(list(data.effects)[:4]):
        character.powers.append(
            Power(
                name=f"Signature Power {index + 1}",
                description="A paragraph of flavour text, which is what a real "
                "power carries and what makes a snapshot as big as it is.",
                descriptors=["magic", "light"],
                effects=[
                    PowerEffectInstance(
                        effect_id=effect.id,
                        rank=8,
                        extras=[ModifierSelection(modifier_id=m) for m in modifiers],
                        flaws=[],
                    )
                ],
            )
        )
    apply_condition(character, "dazed", data)
    apply_condition(character, "hindered", data)
    return character


def test_a_realistic_snapshot_is_small_enough_to_relay() -> None:
    size = snapshot_size(built_character())
    print(f"\nbuilt PL 10 character snapshot: {size} bytes")
    # Two independent bounds. The hard one is the protocol's own frame cap: an
    # oversized snapshot cannot be sent at all. The soft one guards the relay cost
    # estimate in GM_SESSION_PLAN.md, which this build measured at ~3 KB (a
    # 12-power PL 12 sheet reaches ~14 KB, a 40-power monster ~76 KB). If a
    # *typical* sheet ever crosses this, the estimate is wrong and sending deltas
    # rather than whole sheets becomes the next lever.
    assert size < MAX_MESSAGE_BYTES
    assert size < 8 * 1024


def test_a_blank_character_is_smaller_still() -> None:
    blank = snapshot_size(Character.new_default(load_game_data()))
    assert blank < snapshot_size(built_character())


def test_a_snapshot_carries_no_image_path() -> None:
    character = Character.new_default(load_game_data())
    character.image_path = "/home/someone/secret/portrait.png"
    line = encode(CharacterSnapshot(character=sanitize_snapshot(character.to_dict())))
    assert b"portrait.png" not in line


# -- the pusher -------------------------------------------------------------


def test_joining_pushes_the_sheet_straight_away(qapp: QApplication, table) -> None:
    host, player = table
    sheet = CharacterSheet(load_game_data())
    sheet.character.profile["hero_name"] = "Nightingale"

    pusher = SnapshotPusher(sheet, player)

    assert pusher.sent == 1
    slot_id = player.client.player_id
    assert wait_for(qapp, lambda: bool(host.state.players[slot_id].character))
    assert host.state.players[slot_id].character["profile"]["hero_name"] == "Nightingale"


def test_a_burst_of_edits_coalesces_into_one_send(qapp: QApplication, table) -> None:
    _host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=50)
    assert pusher.sent == 1  # the join snapshot

    for _ in range(20):
        sheet.bus.publish(EDITED)
    assert pusher.pending is True
    assert pusher.sent == 1  # nothing has gone out yet

    assert wait_for(qapp, lambda: pusher.sent > 1)
    assert pusher.sent == 2  # twenty edits, one snapshot


def test_a_runtime_change_pushes_too(qapp: QApplication, table) -> None:
    """Toggling a power on is not an *edit*, but the GM still needs to see it."""
    _host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=50)

    sheet.bus.publish(BUILD_CHANGED)
    assert wait_for(qapp, lambda: pusher.sent > 1)


def test_a_real_sheet_edit_reaches_the_gm(qapp: QApplication, table) -> None:
    host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=50)
    slot_id = player.client.player_id

    sheet.abilities._abilities["STR"].setValue(7)

    assert wait_for(qapp, lambda: pusher.sent > 1)
    assert wait_for(qapp, lambda: host.state.players[slot_id].character.get("abilities"))
    assert wait_for(qapp, lambda: host.state.players[slot_id].character["abilities"]["STR"] == 7)


def test_a_detached_pusher_sends_nothing_more(qapp: QApplication, table) -> None:
    _host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=10)
    sent = pusher.sent

    pusher.detach()
    sheet.bus.publish(EDITED)

    assert pusher.pending is False
    assert pusher.push_now() is False
    qapp.processEvents()
    assert pusher.sent == sent


def test_a_pusher_without_a_connection_is_harmless(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, SessionBridge())
    assert pusher.sent == 0
    sheet.bus.publish(EDITED)
    assert pusher.push_now() is False


# -- the join dialog --------------------------------------------------------


def test_the_dialog_refuses_a_mistyped_code(qapp: QApplication) -> None:
    dialog = JoinSessionDialog()
    dialog._name_edit.setText("Aria")
    dialog._code_edit.setCurrentText("AAAAA-BBBBB-CCCCC")

    dialog._try_accept()

    assert dialog.result() != int(dialog.DialogCode.Accepted)
    assert dialog._problem.text()


def test_the_dialog_refuses_an_empty_name(qapp: QApplication) -> None:
    dialog = JoinSessionDialog()
    dialog._name_edit.setText("   ")
    dialog._code_edit.setCurrentText("whatever")

    dialog._try_accept()

    assert "name" in dialog._problem.text().lower()


def test_a_good_code_is_decoded_and_remembered(qapp: QApplication) -> None:
    code = discovery.encode_join_code("203.0.113.7", 47331, "s3cret-token")
    dialog = JoinSessionDialog()
    dialog._name_edit.setText("Aria")
    dialog._code_edit.setCurrentText(code)

    dialog._try_accept()

    assert dialog.join_code().host == "203.0.113.7"
    assert dialog.join_code().token == "s3cret-token"
    assert dialog.display_name() == "Aria"
    settings = storage.load_settings()
    assert settings["session_player_name"] == "Aria"
    assert settings["session_recent_codes"][0] == code


def test_the_dialog_offers_the_saved_characters(qapp: QApplication) -> None:
    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = "Nightingale"
    path = library.save_character(character)

    dialog = JoinSessionDialog()

    assert dialog._character_box.itemText(0) == NO_CHARACTER
    assert dialog.character_path() is None
    labels = [dialog._character_box.itemText(i) for i in range(dialog._character_box.count())]
    assert any("Nightingale" in label for label in labels)
    dialog._character_box.setCurrentIndex(1)
    assert dialog.character_path() == path


def test_the_last_code_is_offered_back(qapp: QApplication) -> None:
    storage.update_settings(session_recent_codes=["AAAAA-BBBBB"], session_player_name="Aria")
    dialog = JoinSessionDialog()
    assert dialog._code_edit.currentText() == "AAAAA-BBBBB"
    assert dialog._name_edit.text() == "Aria"


# -- the launcher's join, end to end ---------------------------------------


@pytest.fixture
def joined_launcher(qapp: QApplication, monkeypatch: pytest.MonkeyPatch):
    """A hosted session, plus a launcher wired to join it with a saved character."""
    host = SessionBridge()
    address = host.host(new_session("Table"), port=0, bind="127.0.0.1")
    code = _code_for(host, address)

    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = "Nightingale"
    path = library.save_character(character)

    monkeypatch.setattr(JoinSessionDialog, "exec", lambda self: int(QDialog.DialogCode.Accepted))
    monkeypatch.setattr(JoinSessionDialog, "join_code", lambda self: code)
    monkeypatch.setattr(JoinSessionDialog, "display_name", lambda self: "Aria")
    monkeypatch.setattr(JoinSessionDialog, "character_path", lambda self: path)

    launcher = StartWindow()
    yield host, launcher
    for window in list(launcher._child_windows):
        window.close()
    if launcher._session_bridge is not None:
        launcher._session_bridge.stop()
    host.stop()


def test_the_launcher_joins_and_the_gm_gets_the_sheet(qapp: QApplication, joined_launcher) -> None:
    host, launcher = joined_launcher

    launcher._join_session()

    assert active_session() is launcher._session_bridge
    player_id = launcher._session_bridge.client.player_id
    assert wait_for(qapp, lambda: bool(host.state.players[player_id].character))
    assert host.state.players[player_id].character["profile"]["hero_name"] == "Nightingale"
    assert host.state.players[player_id].display_name == "Aria"


def test_closing_the_sheet_leaves_the_session(qapp: QApplication, joined_launcher) -> None:
    _host, launcher = joined_launcher
    launcher._join_session()
    window = launcher._child_windows[-1]

    window.close()

    assert active_session() is None
    assert launcher._session_bridge is None


def test_the_app_refuses_to_join_two_sessions_at_once(
    qapp: QApplication, joined_launcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, launcher = joined_launcher
    launcher._join_session()
    first = launcher._session_bridge

    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kw: told.append(args[2]) or None
    )
    launcher._join_session()

    assert launcher._session_bridge is first
    assert told
