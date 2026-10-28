"""A creature out of the fight fades: the GM's NPC card and every seat's Scene card."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect

from mm_companion.core import library, storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import DEFEAT_DEAD, DEFEAT_DOWN, DEFEAT_NONE
from mm_companion.ui import theme
from mm_companion.ui.defeat_look import greyscale
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.scene_card import SceneCard
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
    made.bridge.stop()


def _npc(window: GMWindow, name: str = "Thug") -> str:
    character = quick_npc(load_game_data(), name=name, attack=6, effect=6, defence=6, toughness=6)
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    return path.name


def _opacity(widget) -> float | None:
    effect = widget.graphicsEffect()
    return effect.opacity() if isinstance(effect, QGraphicsOpacityEffect) else None


def test_an_npc_card_fades_when_down_and_further_when_dead(window: GMWindow) -> None:
    name = _npc(window)
    card = window._npc_state[name].card
    assert card.defeat == DEFEAT_NONE and _opacity(card) is None

    window._apply_npc_condition(name, "incapacitated", None)
    card = window._npc_state[name].card
    assert card.defeat == DEFEAT_DOWN
    assert _opacity(card) == pytest.approx(theme.metric("opacity.down"))

    window._apply_npc_condition(name, "dead", None)
    card = window._npc_state[name].card
    assert card.defeat == DEFEAT_DEAD
    assert _opacity(card) == pytest.approx(theme.metric("opacity.dead"))


def test_a_creature_that_gets_up_is_lit_again(window: GMWindow) -> None:
    name = _npc(window)
    window._apply_npc_condition(name, "incapacitated", None)
    entry = window._npc_state[name]
    applied = next(c for c in entry.character.conditions if c.condition_id == "incapacitated")
    window._remove_npc_condition(name, applied.condition_id, applied.parameter)
    card = window._npc_state[name].card
    assert card.defeat == DEFEAT_NONE and _opacity(card) is None


def test_a_scene_card_fades_off_the_wire_conditions(qapp, data) -> None:
    card = SceneCard({"ref": "s1", "name": "Thug"}, data, gm=False)
    assert card.defeat == DEFEAT_NONE and _opacity(card) is None
    card.set_entry({"ref": "s1", "name": "Thug", "conditions": [{"id": "dying"}]})
    assert card.defeat == DEFEAT_DOWN
    assert _opacity(card) == pytest.approx(theme.metric("opacity.down"))
    card.set_entry({"ref": "s1", "name": "Thug", "conditions": [{"id": "dead"}]})
    assert _opacity(card) == pytest.approx(theme.metric("opacity.dead"))
    card.set_entry({"ref": "s1", "name": "Thug"})
    assert card.defeat == DEFEAT_NONE and _opacity(card) is None


def test_a_scene_portrait_greys_and_comes_back(qapp, data) -> None:
    red = QPixmap(8, 8)
    red.fill(QColor(220, 20, 20))
    card = SceneCard({"ref": "s1", "name": "Thug"}, data, gm=False)
    card.set_portrait(red)
    colour = card._thumb.pixmap().toImage().pixelColor(2, 2)
    assert colour.saturation() > 0
    card.set_entry({"ref": "s1", "name": "Thug", "conditions": [{"id": "incapacitated"}]})
    assert card._thumb.pixmap().toImage().pixelColor(2, 2).saturation() == 0
    card.set_entry({"ref": "s1", "name": "Thug"})
    assert card._thumb.pixmap().toImage().pixelColor(2, 2).saturation() > 0


def test_greyscale_keeps_transparency(qapp) -> None:
    clear = QPixmap(4, 4)
    clear.fill(QColor(200, 0, 0, 0))
    assert greyscale(clear).toImage().pixelColor(1, 1).alpha() == 0
