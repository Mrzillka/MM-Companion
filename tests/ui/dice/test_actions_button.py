"""The roller's Actions button and the popup it opens (combat maneuvers, phase 3)."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.ui.action_menu import (
    ACTION_REQUEST,
    ACTION_REROLL,
    NO_REQUEST_TRAITS,
    NOT_BUILT,
    NOTHING_TO_REROLL,
    combat_menu,
)
from mm_companion.ui.dice_roller import ACTIONS_LABEL
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.sections.dice import DiceSection


def test_the_actions_button_sits_where_the_action_row_was(qapp) -> None:
    view = DiceRollerView()  # held: the panel's widgets die with it
    panel = view.panel
    assert panel._actions_button.text() == ACTIONS_LABEL
    assert not hasattr(panel, "_reroll_button")


def test_without_a_host_menu_the_popup_offers_the_panels_own_two(qapp) -> None:
    view = DiceRollerView()  # held: the panel's widgets die with it
    panel = view.panel
    popup = panel.open_actions()
    assert [t.key for t in popup.tiles] == [ACTION_REROLL, ACTION_REQUEST]
    assert not popup.tile(ACTION_REROLL).isEnabled()
    assert NOTHING_TO_REROLL in popup.tile(ACTION_REROLL).toolTip()
    # No traits to ask for until a host hands some over.
    assert not popup.tile(ACTION_REQUEST).isEnabled()
    assert NO_REQUEST_TRAITS in popup.tile(ACTION_REQUEST).toolTip()


def test_the_ask_tile_opens_the_form_and_the_form_asks(qapp, data) -> None:
    # The Request row used to sit on the roller itself, three greyed controls for a
    # verb used a few times a session. It is a small form off the Actions popup now.
    section = DiceSection(data, Character.new_default(data))
    panel = section.panel
    popup = panel.open_actions()
    tile = popup.tile(ACTION_REQUEST)
    assert tile.isEnabled()
    tile.click()
    qapp.processEvents()  # it opens once the Actions popup has closed
    assert panel._request_form.isVisible()

    asked: list = []
    panel.rollRequested.connect(asked.append)
    panel._request_combo.setCurrentIndex(panel._request_combo.findText("  Toughness"))
    panel._request_dc.setValue(18)
    panel._request_button.click()
    (spec,) = asked
    assert (spec.label, spec.dc) == ("Toughness", 18)
    assert not panel._request_form.isVisible()


def test_reroll_lights_once_a_roll_is_back_and_is_the_panels_own(
    qapp, instant_die, monkeypatch
) -> None:
    view = DiceRollerView()  # held: the panel's widgets die with it
    panel = view.panel
    panel._start_roll()
    panel._finish_roll()
    assert panel.can_reroll
    rerolled: list[bool] = []
    reported: list[str] = []
    monkeypatch.setattr(panel, "reroll", lambda: rerolled.append(True) or True)
    panel.actionRequested.connect(reported.append)
    popup = panel.open_actions()
    tile = popup.tile(ACTION_REROLL)
    assert tile.isEnabled()
    tile.click()
    assert rerolled == [True] and reported == []


def test_opening_it_again_closes_the_first(qapp) -> None:
    view = DiceRollerView()  # held: the panel's widgets die with it
    panel = view.panel
    first = panel.open_actions()
    second = panel.open_actions()
    assert not first.isVisible() and second.isVisible()


def test_a_menu_that_raises_costs_the_popup_not_the_panel(qapp, monkeypatch) -> None:
    logged: list[str] = []
    monkeypatch.setattr(
        "mm_companion.ui.dice_roller.log_exception", lambda context, *a: logged.append(context)
    )
    view = DiceRollerView()  # held: the panel's widgets die with it
    panel = view.panel

    def broken(_own):
        raise ValueError("bad mod data")

    panel.set_action_menu(broken)
    assert panel.open_actions() is None
    assert logged == ["Building the Actions popup"]


def test_a_players_menu_follows_the_data_and_leaves_out_the_gms_tiles(qapp, data) -> None:
    section = DiceSection(data, Character.new_default(data))
    popup = section.panel.open_actions()
    keys = [t.key for t in popup.tiles]
    order = [i for s in data.combat.menu for i in s.items]
    gm_only = {a.id for a in data.combat.actions if a.gm_only}
    assert keys == [k for k in order if k not in gm_only]
    titles = [t.text() for t in popup._titles]
    assert titles == ["Attack Maneuvers", "Actions", "Area & Group", "Dice"]
    assert popup.tile("impress").isEnabled()


def test_an_action_this_version_cannot_run_is_shown_greyed(qapp, data) -> None:
    """A mod's action the app has no road for is shown, greyed, saying so."""
    from dataclasses import replace

    from mm_companion.core.data_loader import CombatAction, CombatMenuSection, CombatRules

    blank = CombatAction("mod_blank", "Blank", kind="targeted")
    combat = CombatRules(
        menu=(*data.combat.menu, CombatMenuSection("mod", "Mod", ("mod_blank",))),
        actions=(*data.combat.actions, blank),
        limits=data.combat.limits,
    )
    modded = replace(data, combat=combat)
    section = DiceSection(modded, Character.new_default(modded))
    tile = section.panel.open_actions().tile("mod_blank")
    assert not tile.isEnabled() and NOT_BUILT in tile.toolTip()


@pytest.mark.parametrize("gm", [False, True])
def test_a_ruleset_with_no_menu_falls_back_to_the_hosts_own_actions(data, gm) -> None:
    from dataclasses import replace

    from mm_companion.core.data_loader import CombatRules
    from mm_companion.ui.impress import IMPRESS_ACTION

    bare = replace(data, combat=CombatRules())
    sections = combat_menu(
        bare, {"impress": None, ACTION_REROLL: None}, gm=gm, fallback=[IMPRESS_ACTION]
    )
    assert [[i.key for i in s.items] for s in sections] == [["impress", ACTION_REROLL]]
    assert sections[0].items[0].caption == "Impress"


def test_every_tile_a_player_sees_has_a_road(qapp, data) -> None:
    """Phase 10's audit: no built-in tile is greyed as "not built". A tile may still
    be greyed by a rule (no attack, no session, nothing to reroll), and says which."""
    section = DiceSection(data, Character.new_default(data))
    popup = section.panel.open_actions()
    shown = {tile.key for tile in popup.tiles}
    wanted = {
        action_id
        for menu in data.combat.menu
        for action_id in menu.items
        if not data.combat.action(action_id).gm_only
    }
    assert shown == wanted
    not_built = [tile.key for tile in popup.tiles if NOT_BUILT in tile.toolTip()]
    assert not_built == []
    greyed = [tile for tile in popup.tiles if not tile.isEnabled()]
    assert all("<i>" in tile.toolTip() or "<em>" in tile.toolTip() for tile in greyed)
