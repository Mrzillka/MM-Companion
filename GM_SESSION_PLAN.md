# GM Mode & Online Session — working plan

> **Temporary file.** It tracks the multi-session build of GM mode and the online
> session, and is deleted when `feature/gm-session` merges into `develop`.
> Resume the work with the **`/gm-session`** skill — it reads this file, picks the
> first phase whose `Status:` is not `done`, and does that one phase.

## What we are building

An **online session** the GM hosts from the app and players connect to over the
internet, plus the **GM window** that drives it:

- **Player cards** for everyone connected — portrait, name, PL, current hero
  points, current conditions — with "Open sheet" (read-only view of their live
  stats) and a "+" menu that fast-applies a condition straight onto that player's
  sheet.
- **NPC cards** and a "Create NPC" button. An NPC is an ordinary `Character` in a
  *simplified* sheet: no power-point accounting, just an **estimated** PL.
- **A shared roll history** — every player roll and every GM roll, synchronised to
  everyone and visible in each player's Dice Roller window.
- **A roller with a "hidden" checkbox** so the GM can roll without it appearing in
  anyone's history.

The session **persists**: closing the app and reopening resumes the same session,
roster and full history, and players reconnect to it.

## Decisions already taken (do not relitigate)

| Topic | Decision |
| --- | --- |
| Hosting | The **GM's app hosts** the session server. All state is persisted to the workspace so it resumes on reopen. A headless `python -m mm_companion.server` entrypoint runs the same session on an always-on box for GMs who want 24/7 uptime. |
| Reach | **Internet from day one**: automatic UPnP/IGD port-mapping + a short join code, with a manual port-forward / tunnel fallback. The transport is a swappable interface with a documented **relay protocol**, so a relay can be dropped in later without reworking the session layer. |
| Player sync | **Live push.** The player's client sends a character snapshot on join and on every change; GM cards and sheets update in real time, and GM-applied conditions are pushed back onto the player's live sheet. |
| NPCs | GM-only, **reusing the `Character` model**, saved in the existing workspace `gm_characters/` dir. The PP pool row is replaced by an estimated PL from `rules.power_level_for_points`. |
| Roll sources | **Dice Roller only.** Rolling from the character sheet stays out of scope; the protocol carries a free-form `label` so it can be added later without a protocol change. |
| Roll authority | **The server rolls.** A client sends a *roll request* (label + modifiers + DC); the server resolves it with `core.dice.resolve_check` and broadcasts the result, so no client can edit its own numbers. The roller's existing 1.4 s tumble animation covers the round-trip. |
| Portraits | Character snapshots carry **no `image_path`** — resolving a remote peer's path would read the *receiver's* files. v1 shows a placeholder; portraits move later as a size-capped base64 payload. |

## Architecture

Strict `ui → core → data` is preserved. Networking is non-Qt logic, so it lives in
a new pure-Python subpackage.

### `src/mm_companion/core/session/` — pure Python, no PySide6

| Module | Contents |
| --- | --- |
| `protocol.py` | Message vocabulary, frozen dataclasses with `to_dict`/`from_dict`, `PROTOCOL_VERSION`, `encode`/`decode` (newline-delimited UTF-8 JSON, size-capped). |
| `model.py` | `SessionState` (id, name, created/updated, `players`, `npc_paths`, `rolls`, host token), `PlayerSlot`, `RollRecord` — same `to_dict`/`from_dict` idiom as `core/character.py`. |
| `store.py` | Workspace persistence: `sessions/<id>/session.json` plus an appended `rolls.jsonl`, so a roll never rewrites the whole history. `list_sessions`/`load_session`/`save_session`/`append_roll`/`delete_session`, modelled on `core/library.py`. |
| `net.py` | Framing helpers shared by server and client, and the `Transport` interface (`listen`/`connect`) a relay later implements. |
| `server.py` | `SessionServer` — stdlib `socket` + `threading` accept loop, handshake, per-connection reader threads, applies messages to `SessionState`, persists, broadcasts. Callback API (`on_event`), no Qt. |
| `client.py` | `SessionClient` — connect, handshake, reader thread, `send()`, `on_event` callback. |
| `discovery.py` | Join codes (base32 of host/port/token), UPnP/IGD port mapping (SSDP `M-SEARCH` over UDP + SOAP `AddPortMapping`, stdlib only), external-IP discovery, and the documented relay hook. |

`core/storage.py` gains `SESSIONS_DIRNAME = "sessions"`, `Workspace.sessions_dir`,
creation in `ensure_workspace()`, and settings keys `session_last_id`,
`session_player_name`, `session_recent_codes`.

### `src/mm_companion/ui/` additions

| Module | Contents |
| --- | --- |
| `session_bridge.py` | `SessionBridge(QObject)` wrapping a server or client, turning core callbacks into Qt signals (`connected`, `disconnected`, `rosterChanged`, `rollAdded`, `historyReplaced`, `conditionCommand`, `error`). Cross-thread `emit()` delivers queued, so worker threads emit directly. Module-level `active_session()`/`set_active_session()` so the sheet and the roller both attach without threading a handle through every constructor. |
| `gm_window.py` | `GMWindow(QMainWindow)` — host controls + join code, **Players** panel, **NPCs** panel, **Roll history** panel, embedded roller with a "Hidden roll" checkbox. |
| `npc_window.py` | `NPCWindow(QMainWindow)` — a `CharacterSheet` in NPC mode, saving to `gm_characters/`. |
| `session_dialogs.py` | Join-session dialog (code + display name + character picker) and the host/new-session dialog. |

### Reuse — do not rebuild these

- `ui/dice_roller.py` — extract the roll column into a reusable
  `DiceRollerPanel(QWidget)`; `DiceRollerWindow` becomes a thin `QMainWindow`
  around it and `GMWindow` embeds the same panel. `RollCard` and `degree_text`
  serve the shared history verbatim.
- `ui/sections/conditions.py` + `ui/sections/condition_dialog.py` — the GM's
  fast-apply menu reuses the same catalog filter (`category in ("condition",
  "damage_condition")`) and `ConditionParameterDialog`.
- `ui/sections/system_info.py::HeroPointsWidget` — the player card's hero-point
  circles, read-only.
- `ui/flow_layout.py` (`FlowLayout`/`FlowContainer`) and `ui/start_window.py`'s
  `CharacterCard` shape for the player/NPC card grids.
- `core/library.py` — already directory-parameterized, so NPC storage is nearly
  free: `list_saved_characters(workspace.gm_characters_dir)`,
  `save_character(npc, directory=...)`, `delete_character`, `display_name`.
- `core/rules.apply_condition` / `decrement_condition` — a GM command applies
  through the same resolver on the player's model.
- `core/rules.power_level_for_points` (`core/rules/costs.py`) — the NPC sheet's
  estimated PL.
- `core/dice.resolve_check` — server-side roll resolution.

### Protocol v1

**Client → server:** `hello` (protocol version, token, display name, app version,
mod fingerprint) · `character_snapshot` · `roll_request` (label, bonus, penalty,
dc, hidden) · `ping`.

**Server → client:** `welcome` (session id/name, player id, roster, history) ·
`roster` · `roll_added` · `apply_condition` · `remove_condition` · `error` ·
`kicked`.

A hidden roll is stored with `hidden: true` and **omitted entirely** from the
broadcast — it never reaches a player client, so there is nothing to peek at. The
GM's own window renders it with a 👁 marker.

## Phases

One phase per working session. Each ends green (`ruff check .`, `black .`, the
targeted tests) and updates this file's `Status:` line and the progress log before
its commit.

### Phase 0 — Scaffolding
**Status: done**
This plan file, the `/gm-session` skill, and the `feature/gm-session` branch off
`develop`.

### Phase 1 — Session model, protocol, and store
**Status: not started**
`core/session/{protocol,model,store}.py` plus the `core/storage.py` additions
(sessions dir, settings keys). Round-trip serialization, an appended roll log, and
`list_sessions` over the workspace. Headless tests — `tests/test_session_protocol.py`,
`tests/test_session_store.py`.

### Phase 2 — Server and client transport
**Status: not started**
`core/session/{net,server,client}.py` over loopback TCP: framing, handshake
(protocol version, token, mod fingerprint), roster, character snapshots,
**server-side roll resolution**, persistence on every mutation, and a reconnect
that resumes the full history. Size caps, client limit, JSON-only decoding.
`tests/test_session_server.py` with ephemeral ports and daemon threads.

### Phase 3 — Connectivity
**Status: not started**
`core/session/discovery.py`: join-code encode/decode, UPnP/IGD port mapping and
external-IP discovery over the stdlib, a manual-address fallback, and the
`Transport` seam with the relay protocol written down. `tests/test_session_discovery.py`
(UPnP mocked). **CGNAT defeats UPnP entirely — the fallback advice must surface in
the UI, not only the docs.**

### Phase 4 — Qt bridge and GM window shell
**Status: not started**
`ui/session_bridge.py` and `ui/gm_window.py` with host/stop, the join code and a
Copy button, and a status line. The launcher's "Open GM Mode" button
(`ui/start_window.py`, currently `_not_implemented`) is finally wired.

### Phase 5 — Player join and player cards
**Status: not started**
The join dialog, snapshot push from the player's sheet (on `edited` and
`runtimeChanged`), and GM player cards: portrait placeholder, name, PL, hero
points, condition chips, and "Open sheet" into a read-only `MainWindow`.

### Phase 6 — GM fast-apply conditions
**Status: not started**
The card's "+" menu → an `apply_condition` command → applied on the player's live
sheet through `rules.apply_condition`, snapshot bounces back, chips update on both
ends. A GM-applied condition marks the player's sheet dirty, exactly like a local
one.

### Phase 7 — Roll history sync
**Status: not started**
Extract `DiceRollerPanel`, route rolls through the server, show the shared history
in the GM window *and* in each player's roller, and add the GM's "Hidden roll"
checkbox.

### Phase 8 — NPCs
**Status: not started**
NPC cards and "Create NPC" on the GM window; `NPCWindow` with the simplified sheet
(PP pool row replaced by the estimated PL); storage in `gm_characters/` through the
existing `core/library.py` seams.

### Phase 9 — Headless server, docs, polish
**Status: not started**
`python -m mm_companion.server` (+ a `mm-companion-server` console script),
`docs/mm-session-architecture.md` and a networking/troubleshooting guide,
README/CLAUDE.md updates, full regression, and deletion of this file at the merge.

## Conventions for this work

- **One branch: `feature/gm-session`**, off `develop`. Commit as many times as it
  takes; merge into `develop` with `--no-ff` **only when the user says the feature
  is done**. Never commit on `develop` or `main`. No pull requests.
- **No new dependencies.** Everything is PySide6 + the standard library.
- **No new files under `src/mm_companion/data/`** — the session layer is code
  (MIT), so the OGL content boundary is untouched.
- Core session code stays **Qt-free and headless-testable**; Qt only in `ui/`.
- Verify core changes with the fast, window-free test files. The full GUI suite
  needs `QT_QPA_PLATFORM=offscreen`; one `test_block_sizes` font test fails
  locally on Windows for environmental reasons and passes on CI.

## Risks to keep visible

1. **An exposed listening port.** Token in the join code, JSON-only decoding
   (never `pickle`), message size cap, max clients, per-connection rate limit.
   First host pops a Windows Firewall prompt.
2. **CGNAT** defeats UPnP; the manual forward / Tailscale / Playit fallback has to
   be reachable from the UI. This is why the relay hook exists from Phase 3.
3. **Mod skew** — a GM running mods a player lacks means condition and effect ids
   do not match. The handshake exchanges a mod fingerprint and warns.
4. **App-version skew** — a `PROTOCOL_VERSION` mismatch refuses the join with a
   readable message rather than failing obscurely.

## Progress log

- **2026-07-22 — Phase 0 done.** Branched `feature/gm-session` off `develop`,
  wrote this plan, and added the `/gm-session` skill
  (`.claude/skills/gm-session/`) so a fresh session resumes at the first phase
  that is not `done`. Nothing under `src/` touched yet.
