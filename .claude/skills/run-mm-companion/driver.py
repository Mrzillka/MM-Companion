"""Headless-friendly driver for the MM-Companion PySide6 desktop app.

Instead of ``app.exec()`` (which blocks forever waiting for a human to close
the window), this builds a window, pumps the Qt event loop a few times, saves a
PNG screenshot, and exits. That makes every UI surface reachable programmatically
from a single command:

    python .claude/skills/run-mm-companion/driver.py start        # launcher (StartWindow)
    python .claude/skills/run-mm-companion/driver.py sheet        # editable character sheet
    python .claude/skills/run-mm-companion/driver.py sheet-demo   # sheet with values driven in
    python .claude/skills/run-mm-companion/driver.py constructor  # the Power Constructor
    python .claude/skills/run-mm-companion/driver.py all           # start + sheet + constructor

Screenshots land in ./_driver_shots/<target>.png by default (override with
--out). The workspace is redirected to a throwaway temp dir so the driver never
touches the user's real %APPDATA%\\MM-Companion (pass --keep-home to opt out).

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
    elif target == "constructor":
        from mm_companion.ui.power_constructor import PowerConstructorWindow

        win = PowerConstructorWindow()
    elif target in ("dice", "dice-demo"):
        from mm_companion.ui.dice_roller import DiceRollerWindow

        win = DiceRollerWindow()
        if target == "dice-demo":
            # Drive a couple of rolls straight through the resolve path (skipping
            # the 2s animation) so the readout, a couple of history cards, and a
            # saved quick roll are all populated in the screenshot.
            win._bonus_spin.setValue(5)
            win._penalty_spin.setValue(1)
            win._dc_check.setChecked(True)
            win._dc_spin.setValue(15)
            win._finish_roll()
            win._add_quick_roll({"bonus": 5, "penalty": 1, "dc": 15}, name="Perception")
            win._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 10})
            win._dc_check.setChecked(False)
            win._finish_roll()
    else:  # pragma: no cover - guarded by argparse choices
        raise ValueError(target)

    win.show()
    return win


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        choices=["start", "sheet", "sheet-demo", "constructor", "dice", "dice-demo", "all"],
        help="which UI surface to launch and screenshot",
    )
    parser.add_argument("--out", type=Path, default=Path("_driver_shots"))
    parser.add_argument(
        "--keep-home",
        action="store_true",
        help="use the real workspace instead of a throwaway temp dir",
    )
    args = parser.parse_args(argv)

    if not args.keep_home and "MM_COMPANION_HOME" not in os.environ:
        os.environ["MM_COMPANION_HOME"] = tempfile.mkdtemp(prefix="mm-driver-home-")
        print(f"[driver] MM_COMPANION_HOME={os.environ['MM_COMPANION_HOME']}")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    targets = ["start", "sheet", "constructor"] if args.target == "all" else [args.target]
    for target in targets:
        win = build(target)
        _pump(app)
        _shoot(win, args.out / f"{target}.png")
        win.hide()
        win.deleteLater()
        _pump(app, rounds=2)

    print("[driver] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
