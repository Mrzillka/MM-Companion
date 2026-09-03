"""Headless-friendly driver for the MM-Companion PySide6 desktop app.

Instead of ``app.exec()`` (which blocks forever waiting for a human to close
the window), this builds a window, pumps the Qt event loop a few times, saves a
PNG screenshot, and exits. That makes every UI surface reachable programmatically
from a single command:

    python .claude/skills/run-mm-companion/driver.py start        # launcher (StartWindow)
    python .claude/skills/run-mm-companion/driver.py sheet        # editable character sheet
    python .claude/skills/run-mm-companion/driver.py sheet-demo   # sheet with values driven in
    python .claude/skills/run-mm-companion/driver.py sheet-locked # the read-only view
    python .claude/skills/run-mm-companion/driver.py sheet-unlocked      # its editable twin
    python .claude/skills/run-mm-companion/driver.py sheet-unlock-toggle # unlocked by the toggle
    python .claude/skills/run-mm-companion/driver.py constructor  # the Power Constructor
    python .claude/skills/run-mm-companion/driver.py compact      # the window shrunk to the roller
    python .claude/skills/run-mm-companion/driver.py compact-gm   # the same, from the GM window
    python .claude/skills/run-mm-companion/driver.py gm           # GM Mode, with a cast of NPCs
    python .claude/skills/run-mm-companion/driver.py npc          # the simplified NPC sheet
    python .claude/skills/run-mm-companion/driver.py all           # start + sheet + constructor

Screenshots land in ./_driver_shots/<target>.png by default (override with
--out). The workspace is redirected to a throwaway temp dir so the driver never
touches the user's real %APPDATA%\\MM-Companion (pass --keep-home to opt out).

``--theme <id>`` renders under a given theme preset (``classic``, ``slate-dark``,
``parchment-light``, or anything in the workspace ``themes/`` dir) and tags the
filename with it, so the same surface can be compared across looks:

    python .claude/skills/run-mm-companion/driver.py sheet --theme slate-dark

To drive a NEW flow, add a branch in build() that constructs the window and
pokes its real widgets before the screenshot — see the "sheet-demo" branch,
which sets ability spin boxes through the section API so the derived PP totals
and initiative recompute exactly as they would under a mouse.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def _pump(app, rounds: int = 8, ms: int = 60) -> None:
    """Process queued events + timers so the window fully paints before grab()."""
    from PySide6.QtCore import QEventLoop, QTimer

    for _ in range(rounds):
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, ms)
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()


def _shoot(widget, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    ok = pixmap.save(str(path), "PNG")
    if not ok:
        raise RuntimeError(f"failed to save screenshot to {path}")
    print(f"[driver] wrote {path}  ({pixmap.width()}x{pixmap.height()})")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


def _fixed_rng(value: int):
    """A stand-in RNG that always rolls *value* — the server owns the die, so a
    reproducible screenshot has to force it there."""
    import random

    rng = random.Random()
    rng.randint = lambda _a, _b: value  # type: ignore[method-assign]
    return rng


def _guest_seat(bridge, display_name: str) -> str:
    """A second seat at a hosted session, so a roll can come from somebody else.

    The screenshot needs an attack that this window did *not* make — that is the
    whole point of the chain — and adding a roster slot is cheaper than running a
    second app.
    """
    return bridge.server.state.add_player(display_name).player_id


NOTE_ORIGIN = """# Origin

Bitten by a **radioactive** spider on a school trip to the *Osborn* labs.
Told nobody for `three weeks`.

## The turn

- [x] Uncle Ben
- [ ] tell MJ

> With great power comes great responsibility.

See the [house rules](http://example.com) for Enhanced Strength.
"""

NOTE_LOG = """# Session Log

## Session 3

The **bank** job went badly.
"""


#: Windows a target built along the way and must not let Python garbage-collect
#: before the screenshot — a Qt window with no reference left is destroyed.
_KEEP: list = []


def _app_of(widget):
    """The running application, for a branch that needs to settle a layout early."""
    from PySide6.QtWidgets import QApplication

    del widget
    return QApplication.instance()


def build(target: str):
    """Construct and show the window for ``target``; return it."""
    from mm_companion.core.storage import ensure_workspace

    ensure_workspace()

    if target == "start":
        from mm_companion.core.mods import initialize_mods
        from mm_companion.ui.start_window import StartWindow

        initialize_mods()
        win = StartWindow()
    elif target in ("sheet-locked", "sheet-unlocked", "sheet-unlock-toggle"):
        # The read-only view a saved character opens in, which is a different
        # question from "sheet": locking sheds every field's input chrome, so it is
        # the other half of any change to how a spin box or a combo box is dressed.
        from mm_companion.ui.main_window import MainWindow

        # Three shots of one window, differing only in the lock, so they can be laid
        # side by side: locking must change a block's height but never its width, and
        # the *toggle* is the case a static shot cannot show — the sheet is built
        # locked, laid out, and only then unlocked, which is where a block that grew
        # used to clip against a window that stayed put.
        win = MainWindow(locked=target == "sheet-locked")
        for key, value in {"STR": 4, "STA": 6, "AGL": 8}.items():
            win._sheet.abilities._abilities[key].setValue(value)
        if target == "sheet-unlock-toggle":
            win.show()
            _pump(_app())
            # The real control, so the menu-bar wiring is in the picture too.
            win._lock_action.setChecked(False)
    elif target in ("sheet", "sheet-demo"):
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)  # editable
        if target == "sheet-demo":
            # Drive the real widgets: setting an ability spin box fires the
            # section's signal chain, so the "Abilities — N PP" header, the
            # power-point pool, initiative, and the STA-derived resistances all
            # recompute exactly as they would under a mouse.
            sheet = win._sheet
            for key, value in {"STR": 4, "STA": 6, "AGL": 8}.items():
                sheet.abilities._abilities[key].setValue(value)
            sheet.base_info._profile_fields["hero_name"].setText("Ghost")
    elif target in ("notes-demo", "notes-split"):
        # The Notes block: a tabbed markdown editor over the workspace's notes/
        # dir. The caret is parked mid-document so the marker rule shows both ways
        # in one frame — the ## and ** are muted on every line but the caret's,
        # and on its own line they are painted like the text they mark up.
        # "notes-split" is the same with a second Notes block beside it, which is
        # what the View menu's "New Notes Block" (or dragging a tab off the bar)
        # produces.
        from mm_companion.core import notes as notes_store
        from mm_companion.ui.main_window import MainWindow

        origin = notes_store.create_note("Origin")
        notes_store.write_note(origin, NOTE_ORIGIN)
        log = notes_store.create_note("Session Log")
        notes_store.write_note(log, NOTE_LOG)

        win = MainWindow(locked=False)
        sheet = win._sheet
        sheet.notes.open_note(origin)
        sheet.notes.open_note(log)
        if target == "notes-split":
            second = sheet.add_block_instance("notes")
            sheet.notes._close_ref(log)
            sheet._sections_by_key[second].open_note(log)
            keep = {"notes", second}
        else:
            sheet.notes._select(origin)
            keep = {"notes"}
        # Park the caret on the "## The turn" line: full-strength markers there,
        # dimmed on every other line, which is the whole editor design in one shot.
        editor = sheet.notes._open[0].editor
        cursor = editor.source.textCursor()
        cursor.setPosition(editor.text().index("## The turn") + 4)
        editor.source.setTextCursor(cursor)
        for key in sheet.block_keys():
            if key not in keep:
                sheet.hide_block(key)
        win.resize(900, 640)
    elif target == "equipment-demo":
        # The Equipment block with gear on it: two categories (so the automatic
        # grouping and the group order show), a stowed item beside a worn one, and
        # a piece of armour outclassed by a better one so its "superseded by" line
        # renders. Only this block is left visible — the shot is about the cards.
        from mm_companion.core.character import AdvantageSelection
        from mm_companion.core.rules import attach_accessory, build_item_from_entry
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.advantages.append(AdvantageSelection(name="Equipment", rank=3))
        catalog = sheet._data.equipment_catalog()
        for item_id in ("sword", "crossbow", "leather_armor", "chain_mail", "rifle", "laser_sight"):
            char.equipment.append(build_item_from_entry(catalog[item_id], sheet._data))
        char.equipment[1].worn = False  # the crossbow is stowed
        # Phase 8: a fitted accessory (which leaves the loose list and lends its
        # Accurate to the rifle) and a wielder strong enough to snap the sword.
        attach_accessory(char, char.equipment[4], char.equipment[5], sheet._data)
        char.abilities["STR"] = 12
        sheet.equipment.refresh()
        for key in sheet.block_keys():
            if key != "equipment":
                sheet.hide_block(key)
        win.resize(760, 720)
    elif target == "equipment-vehicles":
        # Phase 9: platforms. A tank (weapons, Impervious Toughness, a dice footer
        # naming each gun) and a jumbo jet (whose Flight reaches the Speed readout),
        # so the trait grid a vehicle card shows instead of a game-term table is
        # visible beside an ordinary weapon's card.
        from mm_companion.core.character import AdvantageSelection
        from mm_companion.core.rules import build_item_from_entry
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.advantages.append(AdvantageSelection(name="Equipment", rank=25))
        catalog = sheet._data.equipment_catalog()
        for item_id in ("tank", "jumbo_jet", "sword"):
            char.equipment.append(build_item_from_entry(catalog[item_id], sheet._data))
        sheet.equipment.refresh()
        sheet.system_info.refresh_derived()  # the jet's Flight joins the Speed readout
        for key in sheet.block_keys():
            if key not in ("equipment", "system_info"):
                sheet.hide_block(key)
        win.resize(820, 860)
    elif target == "equipment-platforms":
        # Phase 10: the two kinds of platform side by side, one printed and one built.
        # A moon-base (its own short trait grid, its fifteen Features on one row), a
        # tank (the throttle under its grid, which restates the Defense Class), and a
        # hand-built jet whose Flight the editor bought and whose Speed reaches the
        # System block exactly as a printed vehicle's does.
        from mm_companion.core.character import AdvantageSelection
        from mm_companion.core.equipment import PLATFORM_VEHICLE, EquipmentItem
        from mm_companion.core.rules import (
            apply_platform,
            build_item_from_entry,
            new_platform,
            platform_rules_category,
        )
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.advantages.append(AdvantageSelection(name="Equipment", rank=25))
        catalog = sheet._data.equipment_catalog()
        for item_id in ("tank", "moon_base"):
            char.equipment.append(build_item_from_entry(catalog[item_id], sheet._data))

        spec = new_platform(PLATFORM_VEHICLE, sheet._data)
        spec.vehicle_class, spec.size, spec.speed = "air", 3, 9
        spec.strength, spec.toughness, spec.defense_modifier = 10, 9, -3
        spec.features = ["autopilot", "alarm"]
        built = EquipmentItem(
            category=platform_rules_category(PLATFORM_VEHICLE, sheet._data),
            platform=spec,
        )
        built.build.name = "Skyhawk"
        built.build.description = "Built off the trait table rather than picked."
        apply_platform(built, spec, sheet._data)
        char.equipment.append(built)

        sheet.equipment.refresh()
        sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("equipment", "system_info"):
                sheet.hide_block(key)
        win.resize(820, 900)
    elif target in ("sheet-pinned", "sheet-pinned-bottom"):
        # The pinned strip with something in it: two blocks parked outside the
        # scrolling page. The bottom variant also moves the strip to another edge,
        # which flips the strip's stacking axis and the board's split.
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        win.show()
        sheet = win._sheet
        sheet.pin_block("conditions")
        # Beside the first one, in the same line, so the shot shows the strip
        # arranging blocks in both directions rather than as one stack.
        sheet.pin_block("abilities", line=0, slot=1, new_line=False)
        sheet.pin_block("resistances")
        if target == "sheet-pinned-bottom":
            sheet.canvas.set_pin_edge("bottom")
        return win
    elif target in ("grid-drop-beside", "grid-drop-merge"):
        # The two marks a block drag puts up, which is the whole of how the
        # placements are discoverable: over the side of a block, the half it would
        # take is washed and a line sits on the seam; over the middle, the frame
        # washes whole, meaning "these two become tabs". Dragging across one block
        # shows both in turn.
        from PySide6.QtCore import QPoint

        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        win.show()
        _pump(_app_of(win))
        sheet = win._sheet
        canvas = sheet.canvas
        # A block near the top of the page: the hit test refuses a drop on one
        # that is scrolled out of the viewport, which is honest and also means a
        # screenshot has to aim at something visible.
        frame = canvas.block_frame("conditions")
        rect = frame.rect()
        where = (
            QPoint(rect.width() // 2, rect.height() // 2)
            if target == "grid-drop-merge"
            else QPoint(rect.width() // 12, rect.height() // 2)
        )
        # As a real drag would have: the block being moved, then the hit test.
        canvas._drag_key = "skills"
        canvas._drag_active = True
        canvas._show_indicator(canvas._hit_test(frame.mapToGlobal(where)))
        return win
    elif target == "grid-close":
        # A block dragged past `grid.close-extent`: washed in the reject colour,
        # because letting go here closes it. Ctrl+Z brings it back.
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        win.show()
        _pump(_app_of(win))
        canvas = win._sheet.canvas
        row = next(w for w in canvas._row_widgets if hasattr(w, "update_collapse_marks"))
        sizes = row.sizes()
        spare = sum(sizes) - 20
        row.setSizes([20] + [spare // (len(sizes) - 1)] * (len(sizes) - 1))
        _pump(_app_of(win))
        row.update_collapse_marks()
        return win
    elif target == "focus":
        # Put keyboard focus on an ability spin box, so the focus ring — the only
        # visible sign that a wheel-guarded control now owns the scroll wheel —
        # shows up in the screenshot.
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        win.show()
        spin = win._sheet.abilities._abilities["AGL"]
        spin.setValue(8)
        spin.setFocus()
        return win
    elif target == "constructor":
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        win = PowerConstructorWindow()
    elif target == "enhanced-trait":
        # Enhanced Trait's trait allocation, built through the real widgets: several
        # traits raised out of one rank pool, each charged at what buying it costs, and
        # each narrowed to the row it really means. Strength 2 (4 PP) + Expertise:
        # Stealth 2 (1) + Improved Critical: Sword (1) = 6, halved by Limited to 3 —
        # and the rank spin reads 5 without anyone setting it.
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QSpinBox

        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.core.rules import split_trait_key
        from mm_companion.ui.power_constructor import PowerConstructorWindow, TraitPicker

        game_data = load_game_data()
        character = Character.new_default(game_data)
        character.focuses["Expertise"] = ["Law"]  # a focus of their own, offered first
        character.powers.append(
            Power(name="Sword", effects=[PowerEffectInstance("damage", rank=3)])
        )
        win = PowerConstructorWindow(game_data, character=character)
        win._name.setText("Berserker Rage")
        card = win.canvas.add_effect("enhanced_trait")

        def _rows():
            return card.findChildren(TraitPicker)

        # The flaw goes on *first* so the last thing driven is an allocation edit —
        # the path whose footer refresh was missing. Ending on attach_modifier would
        # redraw the cost line for its own reasons and hide that.
        card.attach_modifier("limited_enhanced_trait")
        add = next(b for b in card.findChildren(QPushButton) if b.text().endswith("Add"))
        allocation = [("STR", 2), ("Expertise::Stealth", 2), ("Improved Critical::Sword", 1)]
        while len(_rows()) < len(allocation):
            add.click()
        for picker, (key, ranks) in zip(_rows(), allocation, strict=True):
            base, qualifier = split_trait_key(key)
            combo = next(c for c in picker.findChildren(QComboBox) if c.findData("STR") >= 0)
            combo.setCurrentIndex(combo.findData(base))
            if qualifier:
                direct = Qt.FindChildOption.FindDirectChildrenOnly
                controls = [
                    *picker.findChildren(QComboBox, options=direct),
                    *picker.findChildren(QLineEdit, options=direct),
                ]
                cell = next(w for w in controls if not w.isHidden() and w is not combo)
                if isinstance(cell, QComboBox):
                    index = cell.findData(qualifier)
                    if index >= 0:
                        cell.setCurrentIndex(index)
                    else:
                        cell.setCurrentText(qualifier)
                else:
                    cell.setText(qualifier)
            picker.parent().findChild(QSpinBox).setValue(ranks)
    elif target == "enhanced-trait-sheet":
        # The other half of "enhanced-trait": what one Enhanced Trait does to the sheet
        # once saved. Five traits out of one rank pool — an ability, a skill, an
        # *advantage*, a skill *focus* the hero never bought, and an advantage bought for
        # a subject — so the enhancement column, the skill total and the Advantages block
        # each have to show their share of one power, and two of the rows have to be
        # grown by the blocks themselves.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.abilities["STR"] = 3
        char.skill_ranks["Treatment"] = 2
        char.powers.append(
            Power(
                name="Berserker Rage",
                effects=[
                    PowerEffectInstance(
                        "enhanced_trait",
                        rank=10,
                        config={
                            "traits": [
                                {"trait": "STR", "ranks": 2},
                                {"trait": "Treatment", "ranks": 6},
                                {"trait": "Fearless", "ranks": 2},
                                # A focus the hero never bought and an advantage bought
                                # for a subject: both have to find a row on the sheet.
                                {"trait": "Expertise::Stealth", "ranks": 2},
                                {"trait": "Improved Critical::Sword", "ranks": 1},
                            ]
                        },
                    )
                ],
            )
        )
        sheet.abilities.reseed()
        sheet.skills.reseed()
        sheet.advantages.reseed()
        sheet.powers.refresh()
        sheet.abilities.refresh_enhancements()
        sheet.abilities.refresh_cost()
        sheet.advantages.refresh_cost()
        sheet.skills.refresh_totals()
        sheet.system_info.refresh_derived()
        sheet._recompute_derived()  # the spent-PP total the pool shows
    elif target == "sheet-size":
        # A Huge character's sheet: the Size Table reaching Defence, Toughness and the
        # skills, and the Speed readout netting two Flight powers into one line.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.characteristics["size"] = "Huge"
        char.abilities["STA"] = 4
        char.abilities["AGL"] = 2
        char.abilities["PRE"] = 3
        char.skill_ranks["Stealth"] = 4
        char.skill_ranks["Intimidation"] = 3
        for name, rank in (("Wings", 4), ("Jets", 6)):
            power = Power(name=name, effects=[PowerEffectInstance("flight", rank=rank)])
            power.activated = True
            char.powers.append(power)
        sheet.abilities.reseed()
        sheet.resistances.reseed()
        sheet.skills.reseed()
        sheet.system_info.reseed()
        sheet.abilities.refresh_enhancements()
        sheet.resistances.refresh_bases()
        sheet.resistances.refresh_enhancements()
        sheet.skills._rebuild()
        sheet.system_info.refresh_derived()
    elif target == "size-ladder":
        # The Growth/Shrinking rung strip. A Growth 3 and a Shrinking 4 on a Medium
        # character, each dialled somewhere other than full rank, plus a Growth 1 (whose
        # single rung is the card's own switch, so it gets no strip) and a Growth 2 that
        # is switched off (nothing lit). The System block stays so the sheet's own Size
        # readout can be read against whichever rung is lit.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        for name, effect_id, rank, held, on in (
            ("Giant Form", "growth", 3, 2, True),
            ("Ant Size", "shrinking", 4, None, False),
            ("Slightly Bigger", "growth", 1, None, True),
        ):
            power = Power(name=name, effects=[PowerEffectInstance(effect_id, rank=rank)])
            power.activated = on
            power.effects[0].toggled_on = on
            power.effects[0].current_rank = held
            char.powers.append(power)
        sheet.powers.refresh()
        sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(820, 900)
    elif target in ("dynamic-array", "dynamic-array-split"):
        # A Dynamic array on the sheet, in both of the two regimes it has. Unsplit, the
        # header states the pool and says it is *not* split, the cards are still
        # clickable, and each member's slider is seated where the member is actually
        # running with no price on that one notch. Split, the header counts the pool
        # down, a hand-back button appears beside it, and every member runs at once.
        # The System block rides along for its Limits row and its Size readout.
        from mm_companion.core.powers import (
            STRUCTURE_ARRAY,
            Power,
            PowerEffectInstance,
            PowerGroup,
        )
        from mm_companion.ui.main_window import MainWindow
        from mm_companion.ui.sections.powers import _RankDial

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.profile["hero_name"] = "Empyrean"
        char.abilities["STA"] = 6
        char.resistances["DEF"] = 8
        char.skill_ranks["Stealth"] = 6
        growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=6)])
        flight = Power(name="Cosmic Flight", effects=[PowerEffectInstance("flight", rank=5)])
        armour = Power(name="Force Field", effects=[PowerEffectInstance("protection", rank=8)])
        growth.dynamic = flight.dynamic = armour.dynamic = True
        char.powers.append(
            PowerGroup(
                mode=STRUCTURE_ARRAY,
                name="Cosmic Power",
                children=[growth, flight, armour],
                active_child_id=growth.id,
            )
        )
        sheet.powers.refresh()
        sheet.system_info.refresh_derived()
        if target == "dynamic-array-split":
            # Spread the pool through the real controls, so the header counts down and
            # the hand-back button appears exactly as it would under a mouse.
            dials = [
                d
                for d in sheet.powers._list_host.findChildren(_RankDial)
                if any("PP" in text for text in d._labels.values())
            ]
            dials[1]._slider.setValue(2)  # some Flight...
            dials = [
                d
                for d in sheet.powers._list_host.findChildren(_RankDial)
                if any("PP" in text for text in d._labels.values())
            ]
            dials[2]._slider.setValue(2)  # ...and some Force Field, at the same time
            sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 980)
    elif target == "dynamic-effects":
        # The other level the same pool exists at: one power whose *own* effects are an
        # array, two of them Dynamic and sharing its points. The readout and the
        # hand-back button ride on the card's own header, because a leaf card has no
        # group bar above it to put them on.
        from mm_companion.core.powers import (
            STRUCTURE_ARRAY,
            Power,
            PowerEffectInstance,
        )
        from mm_companion.ui.main_window import MainWindow
        from mm_companion.ui.sections.powers import _RankDial

        win = MainWindow(locked=False)
        sheet = win._sheet
        bolt = PowerEffectInstance("damage", rank=8)
        shield = PowerEffectInstance("protection", rank=6)
        bolt.dynamic = shield.dynamic = True
        sheet.character.powers.append(
            Power(name="Cosmic Power", effects=[bolt, shield], structure=STRUCTURE_ARRAY)
        )
        sheet.powers.refresh()
        dials = [
            d
            for d in sheet.powers._list_host.findChildren(_RankDial)
            if any("PP" in text for text in d._labels.values())
        ]
        dials[0]._slider.setValue(2)
        sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 820)
    elif target == "size-ladder-reload":
        # The round trip a size power's rung has to survive: a Growth 3 dialled to
        # Large through the real rank dial, written to the workspace with
        # ``save_character``, and reopened from the file in a fresh window. The shot is
        # of the *reopened* sheet, so the notch the dial rests on and the Size line in
        # the System block are what came back off disk — before runtime was persisted
        # this came up at Gargantuan with everything switched on.
        from mm_companion.core import library
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow
        from mm_companion.ui.sections.powers import _RankDial

        first = MainWindow(locked=False)
        sheet = first._sheet
        sheet.character.profile["hero_name"] = "Colossus"
        sheet.character.powers.append(
            Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
        )
        sheet.powers.refresh()
        _pump(_app())
        # Large is the dial's first notch above Off for a Medium wielder. Setting the
        # value is the keyboard/groove path, which commits without a slider release.
        sheet.powers.findChild(_RankDial)._slider.setValue(1)
        path = library.save_character(sheet.character)
        # Left open rather than closed: the rung dirtied the sheet (which is the point
        # — a saved state that never marks the window unwritten is a state you lose),
        # so closing it here would stop on the modal Save/Discard prompt.
        first.hide()
        _KEEP.append(first)

        win = MainWindow(character=library.load_character(path), path=path, locked=False)
        sheet = win._sheet
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(820, 640)
    elif target == "size-ladder-narrow":
        # The strip in the width it is worst off in: a Growth 10 on a Diminutive
        # character (whose ten rungs the Size Table clamps to eight), in a block narrow
        # enough to force the flow to wrap, and in the locked sheet — where the rungs
        # stay live, being a play action rather than a build edit. Pass --theme to see
        # it under a light preset.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=True)
        sheet = win._sheet
        char = sheet.character
        char.characteristics["size"] = "Diminutive"
        power = Power(name="Titan Form", effects=[PowerEffectInstance("growth", rank=10)])
        power.effects[0].current_rank = 5
        char.powers.append(power)
        sheet.powers.refresh()
        sheet.system_info.reseed()
        sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(420, 620)
    elif target == "constructor-extended":
        # The Extended settings section, on a Huge character's Damage power: it only
        # appears once the build carries an effect it could apply to, and its note says
        # what this wielder's size is worth.
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        char.characteristics["size"] = "Huge"
        char.abilities["ATK"] = 6
        power = Power(name="Titan's Fists", effects=[PowerEffectInstance("damage", rank=8)])
        power.description = "Slabs of fist the size of a car door."
        win = PowerConstructorWindow(data, character=char, power=power)
    elif target == "constructor-bands":
        # The rank bands, which used to be two spin boxes on each chip. Two effects both
        # carrying Tiring, so both rows earn the italic effect subtitle that tells them
        # apart, beside a third modifier that has one effect to itself.
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import (
            STRUCTURE_LINKED,
            ModifierSelection,
            Power,
            PowerEffectInstance,
        )
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        blast = PowerEffectInstance(
            "damage",
            rank=12,
            flaws=[ModifierSelection("tiring", applies_from=9, applies_to=12)],
        )
        curse = PowerEffectInstance(
            "affliction",
            rank=8,
            config={"resistance": "Will", "degree1": "dazed"},
            extras=[ModifierSelection("ranged")],
            flaws=[ModifierSelection("tiring")],
        )
        power = Power(
            name="Sunfire",
            structure=STRUCTURE_LINKED,
            effects=[blast, curse],
        )
        power.description = "A blaze that scorches and blinds at once."
        win = PowerConstructorWindow(data, power=power)
        win.resize(1500, 900)
    elif target == "equipment-constructor":
        # The same builder in gear mode, opened on a catalog sword: the shot is about
        # what differs — the Equipment title, the EP total, and the group combo beside
        # the no-stacking opt-out.
        from mm_companion.core.character import AdvantageSelection, Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.rules import build_item_from_entry
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        char.advantages.append(AdvantageSelection(name="Equipment", rank=3))
        item = build_item_from_entry(data.equipment_catalog()["sword"], data)
        char.equipment.append(item)
        win = PowerConstructorWindow(data, character=char, item=item)
    elif target in ("dice", "dice-demo"):
        # The roller is a sheet block now, pinned in the strip by default, so the
        # shot is of the sheet — there is no standalone roller window.
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        if target == "dice-demo":
            # Drive a couple of rolls straight through the resolve path (skipping the
            # tumble animation) so the readout and a couple of history cards are
            # populated, and fill the quick-roll strip to one short of MAX_QUICK_ROLLS
            # so the shot shows both star states: the first roll's card is lit (it *is*
            # a quick roll), the second's is muted (it is not, and there is room).
            panel = win._sheet.dice.panel
            panel._bonus_spin.setValue(5)
            panel._penalty_spin.setValue(1)
            panel._dc_check.setChecked(True)
            panel._dc_spin.setValue(15)
            panel._finish_roll()
            panel._add_quick_roll({"bonus": 5, "penalty": 1, "dc": 15}, name="Perception")
            panel._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 10})
            for bonus in (3, 7, 11):
                panel._add_quick_roll({"bonus": bonus, "penalty": 0, "dc": None})
            panel._dc_check.setChecked(False)
            panel._bonus_spin.setValue(4)
            panel._finish_roll()
    elif target in ("roll-demo", "roll-demo-table"):
        # Rolling straight off the sheet. "roll-demo" is the solo case: a power's
        # attack goes through the same path a card's 🎲 uses, against a typed-in
        # target Defense, and the save it forced is rolled from the follow-up chip
        # on its own history card — the whole chain, ending in the outcome line.
        #
        # "roll-demo-table" is what the feature is actually for: a real loopback
        # session with two seats, where the *attacker* is somebody else and this
        # window is the target's. Its card carries the chip because the spec came
        # over the wire, and clicking it fills in this sheet's own Toughness.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui import dice_roller as dice_module
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=True)  # the play view: rolling works locked
        win.show()
        sheet = win._sheet
        for key, value in {"STR": 4, "STA": 5, "AGL": 3, "ATK": 7}.items():
            sheet.abilities._abilities[key].setValue(value)
        sheet.character.powers.append(
            Power(name="Force Blast", effects=[PowerEffectInstance("damage", rank=8)])
        )
        sheet.powers.refresh()

        panel = sheet.dice.panel
        attack, _save = sheet.powers._rolls(sheet.character.powers[0])

        if target == "roll-demo-table":
            from PySide6.QtWidgets import QPushButton

            from mm_companion.core.session.model import new_session
            from mm_companion.ui.session_bridge import SessionBridge, set_active_session

            bridge = SessionBridge()
            bridge.host(new_session("The Table"), port=0, bind="127.0.0.1")
            set_active_session(bridge)
            sheet.sync_session()
            # The server rolls, so the die is forced there rather than in the panel.
            bridge.server._rng = _fixed_rng(20)
            # Somebody else's attack lands, and it is a critical hit: the save it
            # forces is at +5 DC, and the spec travels with the roll so this window
            # — the target's — can act on it.
            bridge.server.roll(
                label=attack.label,
                bonus=attack.modifier,
                dc=12,
                spec=attack.to_dict(),
                player_id=_guest_seat(bridge, "Ada"),
            )
            _pump(_app())
            # Answer it from this sheet: the chip fills in our own Toughness (5).
            card = sheet.dice.view._session_history.cards()[0]
            chip = next(b for b in card.findChildren(QPushButton) if b.text().startswith("🎲"))
            bridge.server._rng = _fixed_rng(6)  # and the save fails
            dice_module.ROLL_DURATION_MS = 0  # no tumble, so the shot is deterministic
            chip.click()
            _pump(_app())
            win._driver_bridge = bridge  # keep it alive for the screenshot
            return win

        # Load the attack first: the *spec* is what carries the save it forces, so
        # without this the card comes out with no follow-up chip to click.
        panel.load_spec(attack)
        panel._dc_check.setChecked(True)
        panel._dc_spin.setValue(12)  # the target's Defense
        dice_module.roll_d20 = lambda *a, **k: 14  # a hit, deterministically
        panel._finish_roll()

        # The chip the hit put on the card: roll the save it forced.
        card = sheet.dice.view._local_history.cards()[0]
        from PySide6.QtWidgets import QPushButton

        chip = next(b for b in card.findChildren(QPushButton) if b.text().startswith("🎲"))
        chip.click()
        panel._bonus_spin.setValue(4)  # the target's Toughness
        dice_module.roll_d20 = lambda *a, **k: 6  # and it fails
        panel._finish_roll()
        return win
    elif target in ("compact", "compact-gm"):
        # Compact mode: the whole window collapsed to just the roller, frameless and
        # on top. Two variants because the two windows lend *different* roll
        # surfaces to the same mini page — the sheet a DiceRollerView's panel and
        # history, the GM a bare panel beside the shared GM history — and a shot of
        # only one would say nothing about whether the seam holds for both.
        if target == "compact-gm":
            from mm_companion.ui.gm_window import GMWindow

            win = GMWindow(bind="127.0.0.1")
            win.show()
            panel = win._roller
        else:
            from mm_companion.ui.main_window import MainWindow

            win = MainWindow(locked=False)
            win.show()
            panel = win._sheet.dice.panel

        # Fill the roller in first, so the mini window is not an empty state: a
        # loaded trait in the chip, a couple of quick-roll chips beside the die and
        # two resolved rolls in the history under it.
        from mm_companion.core.rules import RollSpec

        panel.load_spec(RollSpec(label="Athletics", modifier=9))
        panel._add_quick_roll({"bonus": 5, "penalty": 1, "dc": 15}, name="Perception")
        panel._add_quick_roll({"bonus": 8, "penalty": 0, "dc": None}, name="Toughness")
        panel._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 10})
        panel._dc_check.setChecked(True)
        panel._dc_spin.setValue(15)
        panel._finish_roll()
        panel._dc_check.setChecked(False)
        panel._finish_roll()

        win._compact.enter()
        return win
    elif target in ("dice-bottom", "dice-bottom-demo"):
        # The Dice block in a *bottom* strip — short and wide, so its four parts
        # reflow into one row instead of the column the right-hand strip gets.
        # Nothing else is pinned, so the shot is of the roller alone.
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        win.show()
        sheet = win._sheet
        sheet.canvas.set_pin_edge("bottom")
        if target == "dice-bottom-demo":
            panel = sheet.dice.panel
            panel._bonus_spin.setValue(5)
            panel._penalty_spin.setValue(1)
            panel._dc_check.setChecked(True)
            panel._dc_spin.setValue(15)
            panel._finish_roll()
            panel._add_quick_roll({"bonus": 5, "penalty": 1, "dc": 15}, name="Perception")
            panel._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 10})
            panel._finish_roll()
        return win
    elif target in ("settings", "settings-demo"):
        from mm_companion.ui.settings import SettingsWindow

        win = SettingsWindow()
        if target == "settings-demo":
            # Duplicate the active preset, dress it in Slate Dark's surfaces, and
            # recolour the accent — driven through the page's own handlers, so the
            # draft, the live preview and the dirty state all move exactly as they
            # would under a mouse. The window is previewing an unsaved theme.
            from mm_companion.ui import theme as theme_module
            from mm_companion.ui.settings.token_editor import seed_styled_surfaces

            page = win._pages[0]
            source = page._editor.draft()
            copy = source.__class__(
                **{
                    **source.__dict__,
                    "id": "driver-demo",
                    "name": "Driver Demo",
                    "description": f"Based on {source.name}.",
                }
            )
            from mm_companion.ui.theme import loader as theme_loader

            theme_loader.save_workspace_theme(copy)
            theme_module.reset()
            page._reload_presets(select="driver-demo")
            page._editor.load(
                seed_styled_surfaces(
                    page._editor.draft(), theme_module.available_themes()["slate-dark"]
                )
            )
            # Through the row itself, the way the colour picker does it, so the
            # swatch and the field show the new colour and not just the draft.
            row = page._editor._color_rows["accent"]
            row.set_value("#c0693c")
            row.valueChanged.emit("#c0693c")
            page._on_edited()
            page._preview_now()
            # And filter down to the tokens the demo actually changed, so the shot
            # shows what the filter box is for rather than the top of a long form.
            page._filter_field.setText("accent")
    elif target in ("gm", "gm-collapsed"):
        # GM Mode with a cast already in it, so the NPC panel is not an empty
        # state: NPCs are written into the workspace gm_characters/ dir and
        # registered with the session exactly as "Create NPC" would. Built through
        # quick_npc so they have the Damage power a card's default pinned strip
        # reads its third chip from — a cast of statless placeholders would show
        # that chip as a dash and say nothing about how the card really looks.
        #
        # "gm-collapsed" is the other half of the same picture: the same cast with
        # every card shrunk to its combat readout, which is the whole point of the
        # collapse (how many creatures fit on one screen) and cannot be judged from
        # one card in isolation. A bigger cast for that reason.
        from mm_companion.core import library
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.npc import quick_npc
        from mm_companion.ui.gm_window import GMWindow

        data = load_game_data()
        win = GMWindow(bind="127.0.0.1")
        cast = (("Bank Robber", 4), ("Ogre", 9))
        if target == "gm-collapsed":
            cast = (("Bank Robber", 4), ("Ogre", 9), ("Goon", 3), ("Sniper", 5), ("Thug", 3))
        for name, rank in cast:
            npc = quick_npc(data, name=name, attack=rank, effect=rank, defence=rank, toughness=rank)
            win._register_npc(library.save_character(npc, directory=win._npc_dir()))
        if target == "gm-collapsed":
            win.show()
            # Through the caret each card really carries, so the shot is of the
            # state a GM's click produces — including the conditions a couple of
            # them have taken, which is what the damage row leaves behind.
            for entry in win._npc_state.values():
                if entry.card is not None:
                    entry.card._collapse_button.click()
            names = list(win._npc_state)
            win._apply_npc_damage(names[0], 2)
            win._apply_npc_damage(names[1], 1)
            win._apply_npc_damage(names[1], 1)
    elif target == "gm-settings":
        # The GM Mode settings page, reached the way a GM reaches it: build the GM
        # window and fire its own Settings ▸ Preferences… handler, so the shot is of
        # the window that opens rather than one constructed to look like it — which
        # is what proves the menu lands on this page and not on Themes.
        from mm_companion.ui.gm_window import GMWindow

        gm = GMWindow(bind="127.0.0.1")
        gm.show()
        gm._open_settings()
        win = gm._settings_window
        win._gm_owner = gm  # keep the GM window alive for the shot
    elif target == "npc":
        from mm_companion.ui.npc_window import NPCWindow

        # The simplified sheet: the power-point pool is replaced by the Power
        # Level the build's cost would buy, so drive some abilities in to make
        # that estimate move.
        win = NPCWindow()
        win.sheet.base_info._profile_fields["hero_name"].setText("Ogre")
        for key, value in {"STR": 9, "STA": 9, "AGL": 1, "FGT": 4}.items():
            if key in win.sheet.abilities._abilities:
                win.sheet.abilities._abilities[key].setValue(value)
    elif target == "array-cost":
        # An array of three effects in one power. The cards read 20 / 8 / 10 and the
        # total line reads a bare number with no working — the gap this branch closes.
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import STRUCTURE_ARRAY, Power, PowerEffectInstance
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        power = Power(
            name="Elemental Command",
            structure=STRUCTURE_ARRAY,
            effects=[
                PowerEffectInstance("damage", rank=10),
                PowerEffectInstance("move_object", rank=8),
                PowerEffectInstance("flight", rank=10, dynamic=True),
            ],
        )
        win = PowerConstructorWindow(data, character=char, power=power)
        win.resize(1500, 900)
    elif target == "allocation-terms":
        # A Concealment hiding from every sight sense and every hearing sense. The
        # card's own combo says "4 ranks"; the game-terms panel on the right says
        # "Sight 2" — the tier *index*, not what it cost.
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        effect = PowerEffectInstance("concealment", rank=6)
        effect.config["senses"] = [{"id": "sight", "tier": 2}, {"id": "hearing", "tier": 2}]
        power = Power(name="Unseen", effects=[effect])
        win = PowerConstructorWindow(data, character=char, power=power)
        win.resize(1500, 900)
    elif target == "removable-total":
        # The one place a total already shows its working: a suit of armour whose
        # Removable discount is charged per 5 points of the whole power (p161).
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        effect = PowerEffectInstance("protection", rank=12)
        effect.flaws.append(ModifierSelection("removable"))
        power = Power(name="Powered Armour", effects=[effect])
        win = PowerConstructorWindow(data, character=char, power=power)
        win.resize(1500, 800)
    elif target == "configurations":
        # The fourth palette tab: the book's ready-made powers, grouped under the
        # effect each is built on and searchable.
        from PySide6.QtWidgets import QTabWidget

        from mm_companion.ui.power_constructor import PowerConstructorWindow

        win = PowerConstructorWindow()
        tabs = win.palette_zone.findChild(QTabWidget)
        tabs.setCurrentIndex(tabs.count() - 1)
        win.resize(1500, 900)
    elif target == "sub-build":
        # Summon's minion strip: the budget the rank buys, the button that opens a whole
        # character sheet for it, and — with Variable Type attached — a menu of several
        # rather than the one minion the effect otherwise buys.
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
        from mm_companion.core.rules import new_sub_build, power_sub_build_slots, store_sub_build
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        effect = PowerEffectInstance("summon", rank=6)
        # Variable Type turns the one minion into a menu: "you always summon the same
        # minion unless you apply the Variable Type modifier" (p145).
        effect.extras.append(ModifierSelection("variable_type"))
        power = Power(name="Call the Pack", effects=[effect])
        # Filled in before the window exists, so the card renders the strip it finds
        # rather than being poked into refreshing afterwards.
        slot = power_sub_build_slots(power, data, char)[0]
        for index, name in enumerate(("Dire Wolf", "Raven", "Bear")):
            minion = new_sub_build(slot, data)
            minion.profile["hero_name"] = name
            minion.abilities["STR"] = 6 + index
            store_sub_build(slot, index, minion)
        win = PowerConstructorWindow(data, character=char, power=power)
        win.resize(1500, 900)
    elif target == "improvise":
        # The collapsible improvise panel, expanded: the two-way time/DC trade and the
        # note it drives.
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        power = Power(name="Jury-Rigged Blast", effects=[PowerEffectInstance("damage", rank=8)])
        win = PowerConstructorWindow(data, character=char, power=power)
        win._improvised_toggle.setChecked(True)
        win.resize(1500, 900)
    elif target == "imposed-effect":
        # An Affliction whose third degree is Transformed: the sibling-gated picker for
        # the Personal-range effect it imposes appears only once a degree reads that.
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        data = load_game_data()
        char = Character.new_default(data)
        effect = PowerEffectInstance("affliction", rank=8)
        effect.config.update(
            {
                "resistance": "Fortitude",
                "degree1": "dazed",
                "degree2": "stunned",
                "degree3": "transformed",
            }
        )
        power = Power(name="Fleshcraft", effects=[effect])
        win = PowerConstructorWindow(data, character=char, power=power)
        win.resize(1500, 900)
    elif target in ("array-split", "array-split-locked"):
        # A Dynamic array group with its points split across its members, each share
        # made on that card's own rank slider. The fourth member is *not* Dynamic, so it
        # cannot hold a share and is switched off — but its card still looks exactly as
        # clickable as the others.
        from mm_companion.core.powers import (
            STRUCTURE_ARRAY,
            Power,
            PowerEffectInstance,
            PowerGroup,
        )
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=target.endswith("locked"))
        sheet = win._sheet
        blast = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=10)])
        fly = Power(name="Flame Jets", effects=[PowerEffectInstance("flight", rank=5)])
        fly.dynamic = True
        fly.dynamic_points = 4
        shield = Power(name="Heat Shield", effects=[PowerEffectInstance("protection", rank=8)])
        shield.dynamic = True
        shield.dynamic_points = 6
        wall = Power(name="Flame Wall", effects=[PowerEffectInstance("move_object", rank=6)])
        group = PowerGroup(
            mode=STRUCTURE_ARRAY, name="Fire Control", children=[blast, fly, shield, wall]
        )
        sheet.character.powers.append(group)
        sheet.powers.refresh()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 950)
    elif target == "effect-split":
        # The pool one level down: a single power whose *own* effects are a Dynamic
        # array, each sharing the power's points on its own slider.
        from mm_companion.core.powers import (
            STRUCTURE_ARRAY,
            Power,
            PowerEffectInstance,
        )
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        blast = PowerEffectInstance("damage", rank=10, label="Fire Blast")
        fly = PowerEffectInstance("flight", rank=5, label="Flame Jets")
        blast.dynamic = fly.dynamic = True
        fly.dynamic_points = 4
        blast.dynamic_points = 6
        sheet.character.powers.append(
            Power(name="Fire Control", structure=STRUCTURE_ARRAY, effects=[blast, fly])
        )
        sheet.powers.refresh()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 800)
    elif target == "sheet-broken":
        # A build that breaks three checks that used to be constructor-only: an
        # over-spent Concealment (6 ranks of senses bought with 2), an Affliction
        # imposing an effect it cannot afford, and an Obscure paying twice for sight.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        fog = PowerEffectInstance(
            "obscure",
            rank=4,
            config={"senses": ["sight", "hearing"], "senseTypes": ["Sight"]},
        )
        hidden = PowerEffectInstance("concealment", rank=2)
        hidden.config["senses"] = [{"id": "sight", "tier": 2}, {"id": "hearing", "tier": 2}]
        curse = PowerEffectInstance("affliction", rank=2)
        curse.config.update(
            {
                "resistance": "Will",
                "degree1": "dazed",
                "degree2": "stunned",
                "degree3": "transformed",
                "imposedEffect": "flight",
                "imposedRank": 20,
            }
        )
        for name, effect in (("Vanish", hidden), ("Hex", curse), ("Fog Bank", fog)):
            sheet.character.powers.append(Power(name=name, effects=[effect]))
        sheet.powers.refresh()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 1000)
    elif target == "sheet-stunt":
        # A power stunt as its own card — the badge naming its source and the word
        # "Stunt" where every other card prints a cost — beside the source power.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        source = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=10)])
        stunt = Power(name="Flame Shield", effects=[PowerEffectInstance("protection", rank=8)])
        stunt.stunt_of = source.id
        sheet.character.powers.extend([source, stunt])
        sheet.powers.refresh()
        sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 760)
    elif target == "effect-array":
        # A power whose *own effects* are an array: one card, three effects, and only
        # one of them running. Before the selector existed all three contributed at
        # once, so the array handed out an independent build's bonuses for an array's
        # price. The System block stays, so the Toughness the live effect grants can be
        # read against the card.
        from mm_companion.core.powers import STRUCTURE_ARRAY, Power, PowerEffectInstance
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        power = Power(
            name="Elemental Command",
            structure=STRUCTURE_ARRAY,
            effects=[
                PowerEffectInstance("protection", rank=10),
                PowerEffectInstance("flight", rank=6),
                PowerEffectInstance(
                    "enhanced_trait", rank=4, config={"traits": [{"trait": "STR", "ranks": 4}]}
                ),
            ],
        )
        power.description = "Stone skin, a gale at your back, or a giant's grip — one at a time."
        sheet.character.powers.append(power)
        sheet.powers.refresh()
        sheet.system_info.refresh_derived()
        for key in sheet.block_keys():
            if key not in ("powers", "system_info"):
                sheet.hide_block(key)
        win.resize(950, 900)
    elif target == "pushed-traits":
        # Extra Effort spent on the two things the rules name that are not effects:
        # Strength, and one mode of movement. Both are pushed here, so the Abilities and
        # System blocks should name Extra Effort as what is raising them.
        from mm_companion.core.powers import Power, PowerEffectInstance
        from mm_companion.core.rules import pushable_traits, spend_extra_effort
        from mm_companion.ui.main_window import MainWindow

        win = MainWindow(locked=False)
        sheet = win._sheet
        char = sheet.character
        char.abilities["STR"] = 4
        char.abilities["AGL"] = 2
        char.powers.append(Power(name="Wings", effects=[PowerEffectInstance("flight", rank=4)]))
        use = next(u for u in sheet._data.system.extra_effort.uses if u.id == "rank_increase")
        for key in ("ability:STR", "movement:flight"):
            target_trait = next(t for t in pushable_traits(char, sheet._data) if t.key == key)
            spend_extra_effort(char, sheet._data, use, trait=target_trait, determination=True)
        sheet.reseed()
        for key in sheet.block_keys():
            if key not in ("abilities", "system_info"):
                sheet.hide_block(key)
        win.resize(900, 780)
    else:  # pragma: no cover - guarded by argparse choices
        raise ValueError(target)

    win.show()
    return win


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        choices=[
            "start",
            "sheet",
            "sheet-demo",
            "sheet-locked",
            "sheet-unlocked",
            "sheet-unlock-toggle",
            "notes-demo",
            "notes-split",
            "equipment-demo",
            "equipment-vehicles",
            "equipment-platforms",
            "sheet-pinned",
            "sheet-pinned-bottom",
            "grid-drop-beside",
            "grid-drop-merge",
            "grid-close",
            "constructor",
            "constructor-extended",
            "constructor-bands",
            "enhanced-trait",
            "enhanced-trait-sheet",
            "sheet-size",
            "size-ladder",
            "dynamic-array",
            "dynamic-array-split",
            "dynamic-effects",
            "size-ladder-narrow",
            "size-ladder-reload",
            "equipment-constructor",
            "focus",
            "dice",
            "dice-demo",
            "roll-demo",
            "roll-demo-table",
            "dice-bottom",
            "dice-bottom-demo",
            "compact",
            "compact-gm",
            "settings",
            "settings-demo",
            "gm",
            "gm-collapsed",
            "gm-settings",
            "npc",
            "array-cost",
            "allocation-terms",
            "removable-total",
            "configurations",
            "sub-build",
            "improvise",
            "imposed-effect",
            "array-split",
            "array-split-locked",
            "effect-split",
            "sheet-broken",
            "sheet-stunt",
            "effect-array",
            "pushed-traits",
            "all",
        ],
        help="which UI surface to launch and screenshot",
    )
    parser.add_argument("--out", type=Path, default=Path("_driver_shots"))
    parser.add_argument(
        "--keep-home",
        action="store_true",
        help="use the real workspace instead of a throwaway temp dir",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="theme preset id to render under (default: whatever the workspace has)",
    )
    args = parser.parse_args(argv)

    if not args.keep_home and "MM_COMPANION_HOME" not in os.environ:
        os.environ["MM_COMPANION_HOME"] = tempfile.mkdtemp(prefix="mm-driver-home-")
        print(f"[driver] MM_COMPANION_HOME={os.environ['MM_COMPANION_HOME']}")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    # The real entry point installs the theme before the first widget exists; do
    # the same here, or every surface renders unstyled no matter what is saved.
    from mm_companion.core.storage import ensure_workspace
    from mm_companion.ui import theme

    ensure_workspace()
    if args.theme:
        theme.set_active_theme(args.theme)
    theme.apply(app)
    suffix = f".{theme.active_theme().id}" if args.theme else ""
    print(f"[driver] theme={theme.active_theme().id} ({theme.active_theme().chrome.mode})")

    targets = ["start", "sheet", "constructor"] if args.target == "all" else [args.target]
    for target in targets:
        win = build(target)
        _pump(_app())
        _shoot(win, args.out / f"{target}{suffix}.png")
        win.hide()
        win.deleteLater()
        _pump(app, rounds=2)

    print("[driver] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
