# MM-Companion

A desktop **character creator** and **dice roller** for the *Mutants &
Masterminds* tabletop RPG (3rd / 4th edition), built with Python and PySide6.

## Status

🚧 **Early development (pre-alpha, `0.8.1`) — but functional.** You can build a
character point by point, assemble powers in a drag-and-drop constructor, buy
equipment, roll everything straight off the sheet, play from a one-page simple
sheet or print it, and run a live online session with your group. The installed
app keeps itself up to date. Expect breaking changes between versions.

## Features (available now)

**Launcher.** The app opens on a standalone start window: create a new character,
open an existing one, join an online session, open GM Mode, or pick from a
scrollable library of saved-character cards (portrait, name, Power Level).
Right-click a card to delete it. The launcher shows the installed version and
offers an **Update** button when a newer release is out — the installed app
downloads it, closes up (asking about unsaved work first) and restarts on the new
version.

**Character sheet.** The whole sheet is one page of rearrangeable blocks on a
resizable grid — drag them around, resize them, float a block into its own window,
pin blocks to a side strip that stays put while the page scrolls, or show/hide
them from the **View** menu; your layout persists between sessions. The blocks:

- **Name & Details** and **Character Image** — profile fields and portrait.
- **System / Power Level** — Power Level, the power-point pool, size, speed,
  initiative, and hero points, with derived readouts that recompute as abilities,
  advantages, powers and gear change.
- **Abilities** and **Resistances** — point-buy grids that drive the rest of the
  sheet.
- **Conditions** — an applied-condition tracker with the damage ladder.
- **Advantages** and **Skills** — data-driven tables from the 4e catalogs, with
  uses tracked for the advantages that have them.
- **Powers** and **Equipment** — your powers and gear as stat-block cards.
- **Complications**, **Notes** and **Scene** — the rest of the page, including
  the shared scene a GM sets during a session.
- **Dice** — the roller, pinned beside the sheet by default.

A read-only **locked** view and an editable mode share the same sheet; edits can be
**undone and redone**; unsaved changes are flagged in the title and prompt you on
close. The look comes from switchable **theme presets** (Classic, Slate Dark,
Parchment Light, Crimson & Gold), or one you make in Settings.

**Simple sheet & printing.** **View ▸ Simple Sheet** shows the same character on
one tight page made for play — every roll, toggle and tracker still works there —
and you can set saved characters to open in it. **File ▸ Print** and **Export as
PDF** print it as real, selectable text, and let you pick which blocks go on the
page.

**Rolling.** Click an ability, resistance or skill to load it into the roller, or
double-click to roll it at once; a power's 🎲 rolls its attack. Bonus and penalty
sliders cover the situational extras for a single roll, a DC box grades the
result in degrees of success, and rolls you make often can be starred into
**quick rolls**. An attack that hits offers the save it forces right on its card in
the history, so the target can roll it from there.

**Rules engine.** A headless, pure-Python `core` layer handles d20 resolution and
degrees of success, the mutable character model, derived character math,
point-cost accounting, and Power Level validation. Game *content* — ability costs,
skills, advantages, conditions, effects, modifiers, equipment, tables — lives in
editable JSON data files, not hardcoded in Python.

**Powers.** There is no fixed catalog of powers. You assemble one in the
drag-and-drop **Power Constructor**: combine base effects with extras and flaws,
set a rank, and (for multi-effect powers) choose a structure — *independent*,
*linked*, or *array*. The engine derives the point cost, a full game-term stat
block, effective ranks, runtime on/off state, and per-power PL validation. An
active power's trait boosts flow through the entire sheet (e.g. Enhanced Strength
raises your effective Strength everywhere it matters), and its rank can be dialled
down in play with the pips on its card.

**Equipment.** Gear is chosen from a catalog and bought with Equipment Points;
click a card to wear or put it away, and roll a weapon like an attack power.
Vehicles and installations are bought as traits off their own tables.

**Conditions.** Apply and remove conditions from a chip tracker that understands
umbrella bundling, supersession, Hit stacking, and debilitation cascades.

**Save / load.** Characters persist to a per-user workspace as JSON. Portraits are
copied into the workspace so a saved character keeps its picture even if the
original image moves. Saving, Save As, opening, and deleting are wired through the
File menu and the launcher.

**GM Mode & online play.** A GM runs a **live session** that players join with a
short join code. Everyone shares one roster of player cards (portrait, name, PL,
hero points, conditions), a **synchronised roll history**, and a shared scene; the
GM can roll **hidden**, keep a cast of **NPCs**, ask the table for a roll, and
apply a condition straight onto a connected player's live sheet. Sessions live on
a public **session server** by default, so a game keeps running when the GM closes
the app and players can drop in whenever they like — or you can host on your own
machine. See the [networking guide](docs/mm-session-networking.md) and
[`docs/mm-session-architecture.md`](docs/mm-session-architecture.md).

**Mods.** The app is data-first and moddable: the base ruleset loads through the
same pipeline as user-installed mods, and an in-app **Mod Manager** lets you
enable, order, and configure both data-only mods and data+Python mods. See
[`docs/modding.md`](docs/modding.md).

**Cross-platform.** The app is Python + PySide6 and runs from source on Windows,
macOS, and Linux. Packaged builds exist for **Windows** (an installer) and
**Linux** (a tarball with an install script); macOS runs from source.

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

### Linux

**[⬇ Download the latest release](https://github.com/Mrzillka/MM-Companion/releases/latest)**
— grab `MM-Companion-<version>-linux-x86_64.tar.gz` from the **Assets**, then:

```bash
tar -xzf MM-Companion-*-linux-x86_64.tar.gz
cd MM-Companion
./install.sh                 # just for you, no root needed
# or: sudo ./install.sh --system   for every user on the machine
```

That adds MM-Companion to your applications menu and an `mm-companion` command.
No Python is required — the build carries its own. To upgrade, run a newer
release's `install.sh` the same way; to remove it, run
`~/.local/opt/mm-companion/install.sh --uninstall` (add `--purge` to delete your
data too). You can also run `./MM-Companion` straight from the extracted folder.

Your data lives in `~/.local/share/MM-Companion`, so an upgrade never touches it.
When a new version is out, the launcher's **Update** button opens the release page
(updating in place is Windows-only for now). The build needs glibc 2.35 or newer
(Ubuntu 22.04, Debian 12, Fedora 36 or later).

#### Steam Deck

SteamOS is Linux, so the same tarball works — install it from **Desktop Mode**:

- Open Konsole, extract the tarball and run `./install.sh` — the **per-user**
  install. Don't use `--system`: SteamOS's system folders are read-only and are
  replaced by OS updates. The per-user install and your characters both live in
  your home folder, so they survive updates.
- To play from **Game Mode**, right-click MM-Companion in the application menu and
  choose **Add to Steam**; it then appears in your library as a non-Steam game.
- The right trackpad works as a mouse and the touchscreen works; **Steam + X**
  brings up the on-screen keyboard. Building a character means a lot of typing, so
  that is easiest in Desktop Mode or with a keyboard attached; the **Simple Sheet**
  (View ▸ Simple Sheet) suits the Deck's 1280×800 screen best at the table.
- Game Mode shows one window at a time, so a dialog or the Power Constructor can
  open behind the sheet — press the Steam button to switch to it.

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

MM-Companion has a built-in **live session**: one person runs it as the GM, and
players join over the internet to share a roster, a synchronised roll history, a
scene, and the GM's NPCs. Rolls are resolved by the session (no one can fake a
die), and the GM can roll **hidden**.

### Run a session (GM)

The app ships pointing at a public session server, so there is usually nothing to
set up:

1. From the launcher, click **Open GM Mode**.
2. Press **New session**, name it, and press **Open**. You are its GM.
3. **Session ▸ Copy join code**, and send the code to your players (chat, email —
   anything).

No account or password is needed. The session lives on the server, so it keeps
running when you close GM Mode, and players can join whether or not you are
there. Your app holds the session's GM token, which is what makes it yours — keep
your app's settings, since the token is handed out only once.

### Join a session (player)

1. From the launcher, click **Join Session**.
2. Paste the **join code** from the GM.
3. Pick a display name and, optionally, one of your saved characters, then
   connect. Your card joins the shared roster.

A join code makes somebody a player; it never makes them the GM.

### Hosting it yourself

Clear the **Session server** address in GM Mode and the session is hosted on your
own computer instead. Home connections often sit behind NAT (or carrier-grade
NAT, where **no** port forward can help), so the host tries an automatic ladder and
the banner under the join code tells you which case you are in and what to do:

- **UPnP (automatic).** If your router allows it, the app forwards the port for
  you and players connect directly — nothing to do.
- **A tunnel.** Run a TCP tunnel (e.g. [playit.gg](https://playit.gg), ngrok,
  Tailscale Funnel) pointed at the host port, paste its public address into the
  **"I'm using a tunnel"** field, then host. Players still need only the join code.
- **A relay.** Put a relay's address in the **Relay address** field and tick the
  fallback box; both ends dial *out* to it, so it works behind any NAT.

You can also run your own session server for your group, or host one session
headless on an always-on box, with `python -m mm_companion.server` (see `--help`),
and run your own relay with `python -m mm_companion.relay`. The full walkthrough
and a troubleshooting table are in the [networking guide](docs/mm-session-networking.md).

## Future plans

Direction, not commitments:

- Richer character exports, more of the rules surface flowing into the displayed
  sheet numbers, and continued expansion of the moddable data catalogs.

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
installer/# release builds: Windows (PyInstaller + Inno Setup), Linux (tarball)
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
- [`docs/packaging.md`](docs/packaging.md) — building the Windows installer and Linux tarball.

## License

- **Source code:** MIT — see [`LICENSE`](LICENSE).
- **Game data** under `src/mm_companion/data/`: distributed under the Open Game
  License 1.0a — see [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md) and
  [`docs/open_game_license.md`](docs/open_game_license.md).

## Disclaimer

MM-Companion is an **unofficial, non-commercial fan project**. It is **not
affiliated with, sponsored by, or endorsed by Green Ronin Publishing**.
*Mutants & Masterminds* is a trademark of Green Ronin Publishing, LLC.
