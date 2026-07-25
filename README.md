# MM-Companion

A desktop **character creator** and **dice roller** for the *Mutants &
Masterminds* tabletop RPG (3rd / 4th edition), built with Python and PySide6.

## Status

🚧 **Early development (pre-alpha, `0.2.2`) — but functional.** The character
creator is real and usable today: you can build a character point-by-point,
assemble powers in a drag-and-drop constructor, track conditions, and save/load
your work. The rules engine, powers layer, conditions, save/load, and mod support
all work. A **dice roller** and **GM Mode with online play** — a GM hosts a live
session that players join over the internet, sharing a roster, synchronised rolls,
and NPCs — are in place on the development branch. Expect breaking changes between
versions.

## Features (available now)

**Launcher.** The app opens on a standalone start window: create a new character,
open an existing one, join an online session, open GM Mode, or pick from a
scrollable library of saved-character cards (portrait, name, Power Level).
Right-click a card to delete it.

**Character sheet.** The whole sheet is one scrollable page of rearrangeable
blocks — drag them around, float a block into its own window, redock it, or
show/hide blocks from the **View** menu; your layout persists between sessions.
The blocks:

- **Name & Details** and **Character Image** — profile fields and portrait.
- **System / Power Level** — Power Level, the power-point pool, size, speed,
  initiative, and hero points, with derived readouts (e.g. speed and initiative
  recompute as abilities, advantages, and powers change).
- **Abilities** and **Resistances** — point-buy grids that drive the rest of the
  sheet.
- **Conditions** — an applied-condition chip tracker.
- **Advantages** and **Skills** — data-driven tables from the 4e catalogs.
- **Powers** — your built powers as stat-block cards.

A read-only **locked** viewer and an editable mode share the same sheet; unsaved
changes are flagged in the title and prompt you on close.

**Rules engine.** A headless, pure-Python `core` layer handles d20 resolution and
degrees of success, the mutable character model, derived character math,
point-cost accounting, and Power Level validation. Game *content* — ability costs,
skills, advantages, conditions, effects, modifiers, tables — lives in editable
JSON data files, not hardcoded in Python.

**Powers.** There is no fixed catalog of powers. You assemble one in the
drag-and-drop **Power Constructor**: combine base effects with extras and flaws,
set a rank, and (for multi-effect powers) choose a structure — *independent*,
*linked*, or *array*. The engine derives the point cost, a full game-term stat
block, effective ranks, runtime on/off state, and per-power PL validation. An
active power's trait boosts flow through the entire sheet (e.g. Enhanced Strength
raises your effective Strength everywhere it matters).

**Conditions.** Apply and remove conditions from a chip tracker that understands
umbrella bundling, supersession, Hit stacking, and debilitation cascades.

**Save / load.** Characters persist to a per-user workspace as JSON. Portraits are
copied into the workspace so a saved character keeps its picture even if the
original image moves. Saving, Save As, opening, and deleting are wired through the
File menu and the launcher.

**GM Mode & online play.** A GM hosts a **live session** that players join over
the network with a short join code. Everyone shares one roster of player cards
(portrait, name, PL, hero points, conditions), a **synchronised roll history**,
and the GM can roll **hidden**, keep a cast of **NPCs**, and apply a condition
straight onto a connected player's live sheet. The session **persists** — reopen
the app and it resumes. Reaching players over the internet uses an automatic
ladder (UPnP → a tunnel you paste in → a relay both ends dial out to), so it works
even from behind carrier-grade NAT; a headless `python -m mm_companion.server`
hosts the same session on an always-on box. See
[`docs/mm-session-architecture.md`](docs/mm-session-architecture.md) and the
[networking guide](docs/mm-session-networking.md).

**Mods.** The app is data-first and moddable: the base ruleset loads through the
same pipeline as user-installed mods, and an in-app **Mod Manager** lets you
enable, order, and configure both data-only mods and data+Python mods. See
[`docs/modding.md`](docs/modding.md).

**Cross-platform core; Windows installer today.** The app is Python + PySide6 and
runs from source on Windows, macOS, and Linux. A packaged one-click installer
currently exists for **Windows only**.

## Install

### Windows (installer)

**[⬇ Download the latest installer](https://github.com/Mrzillka/MM-Companion/releases/latest)**
— grab `MM-Companion-Setup-<version>.exe` from the **Assets** of the newest
release, then run it. No Python is required. During setup you can add a desktop
shortcut and optionally choose a **Portable** install (a single folder that keeps
its data beside itself).

User data — settings, saved characters, and installed mods — lives in the per-user
workspace at `%APPDATA%\MM-Companion` (or a `data\` folder beside the exe for a
Portable install), so it is never overwritten by an upgrade. See
[`docs/packaging.md`](docs/packaging.md) for how the installer is built and what it
does.

### From source (all platforms)

Requires **Python 3.10+**.

```bash
# 1. Clone
git clone https://github.com/Mrzillka/MM-Companion.git
cd MM-Companion

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 3. Install the package with dev dependencies (editable install)
pip install -e ".[dev]"

# 4. Run the app
python -m mm_companion   # or: python run.py   or: mm-companion

# 5. Run the test suite
pytest
```

#### PyCharm

1. Open the project folder in PyCharm.
2. Point the project interpreter at the `.venv` created above.
3. Mark **`src/`** as the *Sources Root*
   (right-click `src` → *Mark Directory as* → *Sources Root*) so imports like
   `import mm_companion` resolve correctly.
4. The `.idea/` folder is intentionally **not** committed (see `.gitignore`).

## Playing online (GM Mode)

MM-Companion has a built-in **live session**: one person hosts as the GM, and
players join over the network to share a roster, a synchronised roll history, and
the GM's NPCs. Rolls are resolved by the host (no one can fake a die), the GM can
roll **hidden**, and the session is saved to the workspace so it survives closing
the app — reopen and it resumes.

### Host a session (GM)

1. From the launcher, click **Open GM Mode**.
2. Click **Start hosting**. A **join code** appears, along with a short
   reachability banner telling you whether players on the internet can reach you
   and, if not, exactly what to do next.
3. Click **Copy** and send the join code to your players (chat, email — anything).

That is the whole flow when your connection is reachable. The join code encodes
the address, port, and the session's secret, so it is the only thing a player
needs; it is per-session and ephemeral.

### Join a session (player)

1. From the launcher, click **Join Session**.
2. Paste the **join code** from the GM.
3. Pick a display name and, optionally, one of your saved characters, then
   connect. Your card joins the shared roster.

### If players can't reach you

Home internet connections often sit behind NAT (or carrier-grade NAT, where **no**
port forward can help). The banner under the join code names your case and the
fix. In short, the host tries an automatic ladder and you have three options:

- **UPnP (automatic).** If your router allows it, the app forwards the port for
  you and players connect directly — nothing to do.
- **A tunnel.** Run a TCP tunnel (e.g. [playit.gg](https://playit.gg), ngrok,
  Tailscale Funnel) pointed at the host port, paste its public address into the
  **"I'm using a tunnel"** field, then host. Works from behind CGNAT; players
  still need only the join code.
- **A relay.** Put a relay's address in the **Relay address** field and tick the
  fallback box; both ends dial *out* to it, so it works behind any NAT. You can
  run your own with `python -m mm_companion.relay` (there is no default public
  relay bundled yet).

You can also host headlessly on an always-on box with
`python -m mm_companion.server` (see `--help`). The full walkthrough, tunnel and
relay setup, and a troubleshooting table are in the
[networking guide](docs/mm-session-networking.md); for how the pieces fit
together, see [`docs/mm-session-architecture.md`](docs/mm-session-architecture.md).

## Future plans

Direction, not commitments — roughly in priority order:

- **Rolling from the sheet** — wiring the roller into the sheet and powers (attack
  and resistance checks straight off a stat block, with their DCs), building on the
  standalone dice roller that exists today.
- **A public relay** — a default hosted relay so online play works on download with
  nothing to run, plus portraits travelling with a snapshot and a live GM-side view
  of a player's sheet.
- **More** — richer character exports, more of the rules surface flowing into the
  displayed sheet numbers, and continued expansion of the moddable data catalogs.

## Project layout

```
src/mm_companion/
  core/   # rules engine — dice, character model, powers, rules math, conditions,
          # data loading, workspace storage & library, and core/session/ (no Qt)
  data/   # game data as JSON (Open Game Content — see LICENSE-CONTENT.md)
  ui/     # PySide6 user interface (launcher, sheet, power constructor, GM Mode)
  server/ # python -m mm_companion.server — a headless session host
  relay/  # python -m mm_companion.relay  — the public relay box
tests/    # pytest / pytest-qt tests
docs/     # documentation, incl. modding guide and Open Game License text
installer/# Windows installer pipeline (PyInstaller + Inno Setup)
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the rationale behind the `core` /
`data` / `ui` split.

## Documentation

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — architecture conventions and how to
  contribute.
- [`docs/modding.md`](docs/modding.md) — authoring data-only and data+Python mods.
- [`docs/mm-powers-architecture.md`](docs/mm-powers-architecture.md) — the powers model.
- [`docs/mm-conditions-design.md`](docs/mm-conditions-design.md) — the conditions system.
- [`docs/mm-session-architecture.md`](docs/mm-session-architecture.md) — GM Mode and the online session.
- [`docs/mm-session-networking.md`](docs/mm-session-networking.md) — playing over the internet, tunnels, the relay, troubleshooting.
- [`docs/packaging.md`](docs/packaging.md) — building the Windows installer.

## License

- **Source code:** MIT — see [`LICENSE`](LICENSE).
- **Game data** under `src/mm_companion/data/`: distributed under the Open Game
  License 1.0a — see [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md) and
  [`docs/open_game_license.md`](docs/open_game_license.md).

## Disclaimer

MM-Companion is an **unofficial, non-commercial fan project**. It is **not
affiliated with, sponsored by, or endorsed by Green Ronin Publishing**.
*Mutants & Masterminds* is a trademark of Green Ronin Publishing, LLC.
