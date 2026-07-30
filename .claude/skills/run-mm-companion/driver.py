"""Headless-friendly driver for the MM-Companion PySide6 desktop app.

Instead of ``app.exec()`` (which blocks forever waiting for a human to close
the window), this builds a window, pumps the Qt event loop a few times, saves a
PNG screenshot, and exits. That makes every UI surface reachable programmatically
from a single command:

    python .claude/skills/run-mm-companion/driver.py start        # launcher (StartWindow)
    python .claude/skills/run-mm-companion/driver.py sheet        # editable character sheet
    python .claude/skills/run-mm-companion/driver.py sheet-demo   # sheet with values driven in
    python .claude/skills/run-mm-companion/driver.py constructor  # the Power Constructor
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


def build(target: str):
    """Construct and show the window for ``target``; return it."""
    from mm_companion.core.storage import ensure_workspace

    ensure_workspace()

    if target == "start":
        from mm_companion.core.mods import initialize_mods
        from mm_companion.ui.start_window import StartWindow

        initialize_mods()
        win = StartWindow()
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
    elif target == "roll-demo":
        # Rolling straight off the sheet. A power's attack is rolled through the
        # same path a card's 🎲 uses, against a typed-in target Defense, and then
        # the save it forced is rolled from the follow-up chip on its own history
        # card — so the shot shows the whole chain, ending in the outcome line.
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
    elif target == "gm":
        # GM Mode with a cast already in it, so the NPC panel is not an empty
        # state: two NPCs are written into the workspace gm_characters/ dir and
        # registered with the session exactly as "Create NPC" would.
        from mm_companion.core import library
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.ui.gm_window import GMWindow

        data = load_game_data()
        win = GMWindow(bind="127.0.0.1")
        for name, ranks in (("Bank Robber", 2), ("Ogre", 9)):
            npc = Character.new_default(data)
            npc.profile["hero_name"] = name
            for key in ("STR", "STA", "AGL", "FGT"):
                if key in npc.abilities:
                    npc.abilities[key] = ranks
            win._register_npc(library.save_character(npc, directory=win._npc_dir()))
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
            "sheet-pinned",
            "sheet-pinned-bottom",
            "constructor",
            "focus",
            "dice",
            "dice-demo",
            "roll-demo",
            "dice-bottom",
            "dice-bottom-demo",
            "settings",
            "settings-demo",
            "gm",
            "npc",
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
        _pump(app)
        _shoot(win, args.out / f"{target}{suffix}.png")
        win.hide()
        win.deleteLater()
        _pump(app, rounds=2)

    print("[driver] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
