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
| Reach, revised (2026-07-22) | **A relay is committed, not optional.** Phase 3's probe against the dev machine found ISP-side NAT and no global IPv6 — no port forward, automatic or manual, can ever make that machine reachable, and a large share of users (mobile broadband, fibre resellers, student housing) are in the same position. Since *outbound* connections work from behind every NAT, the only universal answer is both ends dialling out to a public box. Order of work: the **tunnel path** (GM runs playit.gg/ngrok, players need nothing) is surfaced in the GM window first because it already works; the **relay** is then built as its own phase so the finished app needs no third-party service. Hole punching is **rejected** — it needs a rendezvous server anyway, fails on the symmetric NAT that CGNAT users sit behind, and would require the relay as a fallback regardless. |
| Relay hosting | Deliberately **deferred**. The relay is written to run on anything with a public IP and ships as `python -m mm_companion.relay`, with the relay URL a setting, so any GM can run their own rather than depending on one blessed box. Where the default instance lives is a deployment question, not a design one, and is answered when real players need to join. |
| Transport security | **Relay-terminated TLS.** A real certificate on the relay, `ssl.create_default_context()` on both peers: zero configuration for players, stdlib only, Python 3.10+, and complete protection against anyone on the network path. The relay *operator* can in principle read traffic — stated plainly in the docs, with self-hosting as the answer for anyone who minds. Rejected for v1: end-to-end via `cryptography` (breaks no-new-dependencies) and via `ssl` TLS-PSK (needs 3.13, drops 3.10–3.12). The data is character sheets and dice rolls; the join token is per-session and ephemeral. Revisit only if the threat model changes. |
| Relay cost | **Not a real constraint if the relay stays a dumb pipe.** The binding limit is concurrent sockets, and a `selectors` loop handles ~10k, not bandwidth. **What would make it expensive is building it wrong**: parsing messages, persisting state, accounts, or a thread per connection. |
| Relay cost, measured (2026-07-22) | The ~40 KB snapshot guess was **~10× too high**. Measured with `ui.session_player.snapshot_size` on the real framed message: blank sheet **442 B**, typical PL 10 build (12 skills, 10 advantages, 4 powers) **~3.1–3.8 KB**, heavy PL 12 (25 skills, 20 advantages, 12 powers, long text) **~14 KB**, a 40-power monster **~76 KB** — all inside `MAX_MESSAGE_BYTES` (256 KiB). So a table-hour relays on the order of **2–5 MB**, not 25 MB; a €4/month 20 TB box carries hundreds of thousands of weekly groups. Bandwidth is not a design constraint at any plausible scale, and **deltas are not needed** — the Phase 5 debounce alone is enough. Revisit only if portraits start moving (they would dwarf everything here). |
| Cost containment | Four levers, in order of effect: (1) **relay last, never first** — the app tries direct (UPnP / manual / tunnel) and only falls back to the relay, so every GM who can be reached directly costs nothing; (2) the relay is stateless with hard caps; (3) **snapshot debounce, later deltas** — built into Phase 5, not bolted on; (4) one-command self-hosting, so the default instance is a convenience and any heavy group can run its own. Explicitly **not** doing accounts, logins, or billing: it multiplies the work, creates a data-protection surface, and buys nothing for ephemeral anonymous sessions. |
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
**Status: done**
`core/session/{protocol,model,store}.py` plus the `core/storage.py` additions
(sessions dir, settings keys). Round-trip serialization, an appended roll log, and
`list_sessions` over the workspace. Headless tests — `tests/test_session_protocol.py`,
`tests/test_session_store.py`.

### Phase 2 — Server and client transport
**Status: done**
`core/session/{net,server,client}.py` over loopback TCP: framing, handshake
(protocol version, token, mod fingerprint), roster, character snapshots,
**server-side roll resolution**, persistence on every mutation, and a reconnect
that resumes the full history. Size caps, client limit, JSON-only decoding.
`tests/test_session_server.py` with ephemeral ports and daemon threads.

### Phase 3 — Connectivity
**Status: done**
`core/session/discovery.py`: join-code encode/decode, UPnP/IGD port mapping and
external-IP discovery over the stdlib, a manual-address fallback, and the
`Transport` seam with the relay protocol written down. `tests/test_session_discovery.py`
(UPnP mocked). **CGNAT defeats UPnP entirely — the fallback advice must surface in
the UI, not only the docs.**

### Phase 4 — Qt bridge and GM window shell
**Status: done**
`ui/session_bridge.py` and `ui/gm_window.py` with host/stop, the join code and a
Copy button, and a status line. The launcher's "Open GM Mode" button
(`ui/start_window.py`, currently `_not_implemented`) is finally wired.

**The connectivity surface is part of this phase, not a later polish pass.**
`publish_session()` returns `Reachability.advice` as finished prose — render it
verbatim under the join code, and make `internet_reachable == False` visibly
different from success rather than a silent LAN address. Alongside it, an
explicit **"I'm using a tunnel"** field: the GM pastes the `host:port` their
tunnel gave them (`parse_address` validates it), it goes to
`publish_session(manual_host=…)`, and the join code carries that hostname. That
is the whole tunnel path — it works with the Phase 3 code as it stands, and it
is what makes the app usable over the internet before the relay exists.

### Phase 5 — Player join and player cards
**Status: done**
The join dialog, snapshot push from the player's sheet (on `edited` and
`runtimeChanged`), and GM player cards: portrait placeholder, name, PL, hero
points, condition chips, and "Open sheet" into a read-only `MainWindow`. The
client dials through `discovery.transport_for(code.host)`, **not** a directly
constructed `TcpTransport` — that is what makes a relay join code work later
without touching this code.

**Snapshot push must be debounced from the start** (coalesce a burst of edits
into one send; a per-keystroke snapshot is the one thing that could make relay
traffic expensive). Also **measure a real snapshot's encoded size** and record
it in this file — the whole relay cost estimate rests on that number, currently
a guess of ~40 KB. Sending deltas rather than whole sheets is the follow-up
lever if the measurement warrants it.

### Phase 6 — The relay
**Status: not started**
The answer to "players anywhere, nothing to install". Both ends dial **out** to a
public box, which works from behind every NAT; this is the phase that makes the
app genuinely internet-wide rather than LAN-plus-a-tunnel.

- `core/session/relay.py` — `RelayTransport` implementing `net.Transport`,
  registered into `discovery.transports` under the `mmrelay` scheme, so
  `transport_for()` picks it up from a join code and **`server.py`/`client.py`
  need no changes at all**.
- `src/mm_companion/relay/__main__.py` — the relay itself: `python -m
  mm_companion.relay`, stdlib only, no app imports beyond the framing. It parses
  **only** the envelope (`relay_host` / `relay_join` → `relay_ok` /
  `relay_error`, the protocol already written down in `discovery.py`'s
  docstring) and is a dumb byte pipe after that, so it holds no session state and
  never needs a `PROTOCOL_VERSION` bump.
- **Build it as a `selectors` event loop, not a thread per connection.** This is
  the difference between one small box serving thousands of tables and needing a
  fleet; see the cost rows in the decisions table.
- **The relay never dials outward** — it only ever pairs two *inbound*
  connections. That is what stops an open relay from being usable as a general
  proxy against third parties, and it is a property to preserve deliberately, not
  an accident of the implementation.
- Hard caps, all configurable: max sessions, max clients per session, per-session
  byte rate, idle timeout, absolute session TTL.
- **TLS terminates at the relay** (decided; see the decisions table).
  `ssl.create_default_context()` on both peers, a real certificate on the box.
- **The connection ladder**: direct (UPnP / manual / tunnel) is tried first and
  the relay is the fallback, never the default. A GM who is reachable directly
  must not cost relay traffic.
- A relay URL setting with the default instance overridable, so a GM can run
  their own; degrade to a clear "ask your GM to forward a port or use a tunnel"
  rather than a mystery failure when the relay is unreachable or at capacity.
- Tests: relay envelope handling, a full GM↔player session over a relay running
  on loopback, a refused/unknown-session join, and the caps actually capping.

### Phase 7 — GM fast-apply conditions
**Status: not started**
The card's "+" menu → an `apply_condition` command → applied on the player's live
sheet through `rules.apply_condition`, snapshot bounces back, chips update on both
ends. A GM-applied condition marks the player's sheet dirty, exactly like a local
one.

### Phase 8 — Roll history sync
**Status: not started**
Extract `DiceRollerPanel`, route rolls through the server, show the shared history
in the GM window *and* in each player's roller, and add the GM's "Hidden roll"
checkbox.

### Phase 9 — NPCs
**Status: not started**
NPC cards and "Create NPC" on the GM window; `NPCWindow` with the simplified sheet
(PP pool row replaced by the estimated PL); storage in `gm_characters/` through the
existing `core/library.py` seams.

### Phase 10 — Headless server, docs, polish
**Status: not started**
`python -m mm_companion.server` (+ a `mm-companion-server` console script),
`docs/mm-session-architecture.md` and a networking/troubleshooting guide (the
tunnel walkthrough and the relay deployment guide land here),
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
2. **Carrier NAT is the normal case, not the edge case** — confirmed on the dev
   machine itself (ISP-side NAT, no global IPv6), so *nothing* reaches it
   inbound. The tunnel field in Phase 4 and the relay in Phase 6 are what make
   the feature real; until the relay ships, "works over the internet" means "the
   GM set up a tunnel". Do not let a later phase quietly assume a reachable host.
3. **Mod skew** — a GM running mods a player lacks means condition and effect ids
   do not match. The handshake exchanges a mod fingerprint and warns.
4. **App-version skew** — a `PROTOCOL_VERSION` mismatch refuses the join with a
   readable message rather than failing obscurely.

## Progress log

- **2026-07-22 — Phase 0 done.** Branched `feature/gm-session` off `develop`,
  wrote this plan, and added the `/gm-session` skill
  (`.claude/skills/gm-session/`) so a fresh session resumes at the first phase
  that is not `done`. Nothing under `src/` touched yet.

- **2026-07-22 — Phase 1 done.** The Qt-free session layer's data half.
  - **`core/session/protocol.py`** — `PROTOCOL_VERSION = 1`, `MAX_MESSAGE_BYTES`
    (256 KiB), the `ERROR_*` code constants, `ProtocolError`, and 12 frozen
    message dataclasses registered by their wire tag: `Hello`,
    `CharacterSnapshot`, `RollRequest`, `Ping` (client→server) and `Welcome`,
    `Roster`, `RollAdded`, `ApplyCondition`, `RemoveCondition`, `ErrorMessage`,
    `Kicked`, `Pong` (server→client). `Pong` is the one addition to the plan's
    list — `Ping` needs an answer for a client-side keepalive. `encode`/`decode`
    do newline-delimited UTF-8 JSON with the size cap enforced on both sides.
    **The validation is generic**: `Message.from_payload` walks `fields(cls)` and
    type-checks each value against its annotation string via `_coerce`, so every
    message gets strict shape-checking for free (a float or a `true` is not an
    `int`; a missing required field raises; unknown extra keys are ignored for
    forward compatibility). New messages just need plain annotations —
    `str`/`int`/`bool`/`dict`/`list[...]` and `| None` — or `_coerce` must learn
    the shape. `sanitize_snapshot()` strips `image_path` per the portrait
    decision.
  - **`core/session/model.py`** — `PlayerSlot` (with `to_dict` *and* a
    `public_dict` that drops the slot's private `token`, which is what goes on
    the wire), `RollRecord` (frozen; `modifier`/`total` derived, `dc`/`degree`
    both `None` for an ungraded d20, `from_check` builds one from a
    `dice.CheckResult`), and `SessionState` (roster ops, `player_by_token` for
    reconnect via `compare_digest`, `record_roll` assigning `seq`,
    `visible_rolls()` filtering hidden GM rolls, `to_dict(include_rolls=…)`).
    Two token layers: the session's `host_token` (the join secret in the code)
    and a per-`PlayerSlot` `token` a returning client presents to reclaim its
    seat.
  - **`core/session/store.py`** — `sessions/<id>/session.json` +
    `rolls.jsonl`. `save_session` deliberately does **not** write rolls (pass
    `write_rolls=True` only for create/import); `append_roll` is the only write
    on the rolling path; `load_session` stitches them back, skips a torn last
    line, and clears every slot's `connected` flag. `session_dir` validates the
    id against `^[A-Za-z0-9_-]{1,64}$` — ids arrive over the network later, so
    this is the path-traversal boundary. Plus `list_sessions` (newest first,
    junk skipped), `load_rolls`, `delete_session`, `SessionSummary`,
    `SessionStoreError`.
  - **`core/storage.py`** — `SESSIONS_DIRNAME`, `Workspace.sessions_dir`,
    creation in `ensure_workspace()`, and the settings keys `session_last_id`,
    `session_player_name`, `session_recent_codes`.
  - **Tests** — `tests/test_session_protocol.py` (37) and
    `tests/test_session_store.py` (35), both headless and sub-second.
  - Deferred: nothing from the phase. Note for Phase 2 — the server should reuse
    `SessionState.record_roll` + `store.append_roll` together (the model does not
    persist itself), and broadcast `roll.to_dict()` only for `not roll.hidden`.
  - Pre-existing, untouched: `ruff check .` at the repo root reports 6 errors in
    `.claude/skills/make-release/make_release.py` (committed in `a9e70e8`,
    unrelated to this work). `ruff check src tests` is clean.

- **2026-07-22 — Phase 2 done.** The session layer's live half: a real server and
  client over loopback TCP, still Qt-free.
  - **`core/session/net.py`** — `Connection` (framed, buffered newline-delimited
    reads; `send` is lock-guarded so several threads may write to one peer, reads
    are single-threaded by convention), `Transport`/`Listener` ABCs, and
    `TcpTransport`/`TcpListener`. `DEFAULT_PORT = 47331`, `TransportError`
    subclasses `OSError` so a reader loop catches socket failure and misuse
    together. Two details worth keeping: `close()` calls `shutdown()` first
    because closing a socket another thread is parked in `recv` on does not
    reliably wake it on Windows; and `_read_line` raises rather than buffering
    once `MAX_MESSAGE_BYTES` arrive with no newline in them. `Listener.accept()`
    returns `None` when the listener is closed — that is how the accept loop
    exits.
  - **`core/session/server.py`** — `SessionServer`: one accept thread, one reader
    thread per peer, one `RLock` over every state mutation, `store.save_session`
    on each roster/snapshot change and `store.append_roll` per roll. Handshake
    order is protocol version → host token → slot claim (`player_token`
    reconnect) → client limit. **The seating race that cost the most time:** a
    connection is registered in `_connections` the moment it claims a seat (so
    the limit counts it) but is held out of `_welcomed` until its `Welcome` has
    actually gone out — otherwise a concurrent roster broadcast overtakes the
    handshake answer and the client sees a `Roster` where a `Welcome` belongs.
    `broadcast`/`send_to` only ever target welcomed ids. Rolls go through the one
    `_resolve_roll` path (client request and GM alike), clamped to
    `MAX_ROLL_MODIFIER`/`MAX_LABEL_CHARS`; `hidden` is honoured only for a slot
    with `is_gm` and a hidden roll is recorded, persisted, emitted to the GM, and
    never broadcast. Also: per-connection rate limit (120 msgs / 5 s),
    `HANDSHAKE_TIMEOUT` on the pre-hello read, `on_event(kind, payload)` with the
    payload **always a plain dict** (see the `EVENT_*` constants), and callbacks
    wrapped so a bad UI handler cannot kill a worker thread.
  - **`core/session/client.py`** — `SessionClient.connect()` does the handshake
    on the *calling* thread and returns the `Welcome` (or raises
    `SessionClientError` with an `ERROR_*` `code`), then leaves one reader thread
    emitting `EVENT_*` events. `send_snapshot` sanitizes, so no caller can forget
    to. Remembers `player_id`/`player_token` for the reconnect.
  - **`core/session/protocol.py`** — two new codes only: `ERROR_RATE_LIMIT` and
    `ERROR_MOD_SKEW` (a *warning* sent after a successful `Welcome`, not a
    refusal). `PROTOCOL_VERSION` is unchanged.
  - **`core/mods.py`** — new `stack_fingerprint()`: a 16-hex-char sha256 of the
    active mod stack's `Mod.fingerprint()`s in load order, which is what the
    handshake compares. Phase 4's UI should pass it into both ends.
  - **Tests** — `tests/test_session_server.py` (46, real loopback sockets,
    ephemeral ports, daemon threads, ~2.5 s) plus 3 in `tests/test_mods.py`. The
    helpers there matter: `Events.next_of(kind)` waits on a queue and `wait_for`
    polls — nothing sleeps a fixed time, and `read_until(conn, cls)` skips the
    roster chatter a join stirs up.
  - Deferred, for the phase that needs it: **a network client cannot be the GM.**
    The host token is the only secret and every remote peer is seated as a
    player, so `is_gm` is only ever the in-process host slot; hidden rolls and
    condition commands from a socket are ignored. Phase 9's headless server needs
    a GM auth field in `Hello` (or a second token) before a GM can drive a remote
    session. Also deferred: no keepalive timer drives `Ping` yet (the message and
    `Pong` work, nothing schedules them), and reconnect-on-drop is the UI's job
    in Phase 5.

- **2026-07-22 — Post-review hardening pass** (after Phase 2, before Phase 3). A
  code review of the branch found four real issues; all fixed, all tested. What
  changed on the wire and in behavior — later phases must build on this:
  - **Roster entries no longer carry `character`** (`PlayerSlot.roster_dict()`;
    `public_dict()` still has it for host-side events). A full table's combined
    sheets would have outgrown `MAX_MESSAGE_BYTES` and broken every broadcast.
    Phase 5's GM cards get characters from `EVENT_SNAPSHOT` / `server.state`,
    **not** from roster payloads; no player client ever sees another's sheet.
  - **`Welcome.history` is capped** to the last `WELCOME_HISTORY_ROLLS` (200)
    visible rolls — an uncapped welcome stops encoding at roughly a thousand
    rolls and the join would be refused. Phase 7's player-side history shows the
    recent slice; the GM's full log still comes from `server.history()`.
  - **Token comparison runs over UTF-8 bytes** (`model.tokens_match`) —
    `secrets.compare_digest` raises `TypeError` on non-ASCII `str`, so a hostile
    token used to kill the handler thread instead of drawing `ERROR_BAD_TOKEN`.
  - **Welcomed connections run under `net.IO_TIMEOUT` (15 s)** instead of no
    timeout. Both read loops treat a timed-out `recv` as idle-and-fine
    (`except TimeoutError: continue`); a timed-out *send* means a stalled peer —
    it raises, and the server drops that peer instead of blocking a broadcast
    (in Phase 4 the blocked thread would have been the GM's UI). An encode
    failure (`ProtocolError`) in `send_to`/`broadcast` is now *our* error —
    reported as `EVENT_ERROR {"code": "encode"}`, never a reason to drop a peer.
  - Minor: `store.load_session` raises `SessionStoreError` on a non-object
    `session.json`; `_threads` prunes finished handlers; `SessionClient.close()`
    emits `EVENT_DISCONNECTED` at most once and only if a connection existed —
    the Phase 4 bridge can call it unconditionally.
  - Also made `test_a_flood_trips_the_rate_limit` robust to the Windows RST race
    (the kick can reset the socket before the error message is readable).

- **2026-07-22 — Phase 3 done.** `core/session/discovery.py` (one new module, plus
  its export from `core/session/__init__.py`) — join codes, UPnP, the advice the
  UI must show, and the relay seam. Still Qt-free, stdlib only.
  - **Join codes.** `encode_join_code(host, port, token)` / `decode_join_code` /
    the `JoinCode` record. The packed layout is `version | host-kind | host |
    port(2) | token-len | token | checksum`, base32'd, `=`-stripped, uppercased
    and dashed every `CODE_GROUP` (5). An IPv4 literal packs to 4 bytes;
    **anything else — hostname, IPv6 literal, or a relay URL — rides as UTF-8
    text**, which is what makes the relay work without a format change. Decoding
    strips every non-alphanumeric character, uppercases, and maps the two digits
    base32 does not have (`0→O`, `1→I`); the version byte gives a *named* refusal
    across versions and the checksum byte turns a typo into "that join code has a
    typo in it" instead of a connection attempt on a wrong address. Fields are
    capped (`MAX_HOST_BYTES` / `MAX_TOKEN_BYTES`, 128 each) — a join code is
    parsed before anything is trusted.
  - **Manual fallback.** `parse_address("host" | "host:port" | "[v6]:port")` →
    `(host, port)` with `DEFAULT_PORT` filled in, raising `AddressError`. The
    join dialog pairs this with a separately pasted token; `publish_session(...,
    manual_host=...)` is the host-side twin (a tunnel or hand-forwarded address,
    discovery skipped entirely).
  - **UPnP/IGD, stdlib only.** `discover_igd()` (SSDP `M-SEARCH` over UDP
    multicast for four search targets, `LOCATION` → device description → the
    first `WANIPConnection:1`/`WANPPPConnection:1` service, control URL made
    absolute), `external_ip()`, `add_port_mapping()` → a `PortMapping` with
    `release()`, and `delete_port_mapping()`. SOAP faults become `UpnpError` with
    the IGD **`errorCode`**, so callers branch on 718 (`UPNP_CONFLICT`) / 725
    (`UPNP_ONLY_PERMANENT_LEASES`) rather than on prose — 725 makes
    `add_port_mapping` retry once with a permanent lease, which is the one
    router quirk worth handling in code.
  - **`publish_session(port, ...) -> Reachability` is the single call the UI
    wants.** It never raises: every failure still returns the LAN address plus
    `advice`, a tuple of finished `ADVICE_*` prose. That is the phase's hard
    requirement discharged — `ADVICE_CGNAT`, `ADVICE_NO_IGD`,
    `ADVICE_UPNP_REFUSED`, `ADVICE_PORT_TAKEN`, `ADVICE_DOUBLE_NAT`,
    `ADVICE_NO_EXTERNAL_IP`, `ADVICE_FIREWALL`, `ADVICE_LAN_ONLY`,
    `ADVICE_MANUAL_ADDRESS`. **Phase 4 must render `Reachability.advice`
    verbatim in the GM window** (a list under the join code); it is written as
    player-facing sentences, not log lines. `internet_reachable` is the
    best-effort green light: a mapping exists *and* the WAN address is neither
    private (double NAT) nor CGNAT.
  - **The relay seam is now real, not a stub.** `transports` is a
    `core.registry.Registry` of `scheme -> factory`, and `transport_for(host)`
    returns `TcpTransport()` for a plain host or the registered transport for a
    `scheme://…` host, raising `UnknownTransportError` otherwise. So a relay is
    (a) a registration and (b) a join code whose host is
    `mmrelay://relay.example:9000/<session-id>` — no change to `server.py`,
    `client.py`, or the code format. **The relay protocol is written down in the
    module docstring**: same newline-JSON framing, one envelope frame
    (`relay_host` / `relay_join` → `relay_ok` / `relay_error`), then a dumb pipe;
    the relay holds no session state and never needs a `PROTOCOL_VERSION` bump.
    Phase 5's client should dial through `transport_for(code.host)` rather than
    constructing a `TcpTransport` itself.
  - **Two things to know before touching this again.** (1)
    `is_private_address` deliberately spells its CIDR list out instead of using
    `ipaddress.is_private` — that property's membership *changed* across the
    supported 3.10–3.13 range (CGNAT left it in 3.12), and this classification
    decides both what address gets published and which URLs UPnP will fetch. (2)
    The UPnP fetch path is hardened because `LOCATION` arrives from an
    unauthenticated multicast reply: `_require_local_url` checks the URL scheme
    and that the host is a private literal, **before the request and again on
    `response.geturl()`** (so a redirect off-network cannot be followed), and the
    body is capped at `MAX_DESCRIPTION_BYTES`.
  - **Tests** — `tests/test_session_discovery.py` (75, ~0.3 s). Fully offline:
    the `router` fixture monkeypatches `_ssdp_search` / `_http_get` / `_soap_post`
    into canned XML and a call recorder, so a "router" is a dict. An autouse
    fixture unregisters anything a test put in the process-wide `transports`
    registry.
  - **Verified read-only against real hardware** (the dev machine's own router:
    an `EC225-G5`, `WANIPConnection:1`). SSDP, the device description, the
    control URL, and `GetExternalIPAddress` all parsed on the first try. The run
    found two things canned XML could not, both now fixed:
    1. **`local_ip()` was the wrong mapping target.** It answered `10.0.x.x`
       (the default route) while the IGD that replied lives on `192.168.0.1` —
       a router told to forward a port to an address outside its own subnet
       refuses or silently does nothing. There is now `local_ip_for(host)`, and
       `publish_session` re-aims at the interface facing *the discovered router*
       before mapping (unless the caller passed `internal_ip` explicitly). The
       re-probe confirms it: `192.168.x.x` instead of `10.0.x.x`.
    2. **`ADVICE_DOUBLE_NAT` gave advice the GM could not act on.** That machine's
       WAN address is `10.120.x.x` — ISP-side NAT in RFC1918 space, *not*
       RFC6598, so `is_cgnat_address` is correctly False but the box in front is
       still not the user's. The text now covers both cases and points at the
       tunnel fallback. Worth knowing for Phase 4: **the developer's own network
       cannot host to the internet at all**, so the GM window must be testable —
       and honest — on the LAN-only path, and the relay is not hypothetical here.
    Nothing was written to the router: no `AddPortMapping` call has ever run
    against real hardware, so a mapping refusal is still an untested path.
  - Deferred: no `Reachability` refresh/renewal timer — a permanent lease needs
    none, but a router that forced a finite lease in `add_port_mapping` will drop
    the mapping when it expires. Nothing in `ui/` yet — Phase 4 wires
    `publish_session` and the advice into the GM window.

- **2026-07-22 — Reach decision: the relay is committed.** Not a phase; a
  decision, recorded so it is not re-argued. Prompted by the Phase 3 probe: the
  dev machine sits behind ISP-side NAT with no global IPv6, so no port forward
  can ever reach it, and a large share of users are in the same position.
  Outbound connections work from behind every NAT, so the only universal answer
  is both ends dialling out to a public box.
  - **Rejected: hole punching.** It needs a rendezvous server anyway (so it does
    not avoid the infrastructure), TCP punching is unreliable, and UDP punching
    fails on the symmetric NAT that CGNAT users sit behind — meaning the relay
    would have to exist as the fallback regardless. Build the relay; punching is
    at most a later bandwidth optimisation.
  - **Order:** the tunnel path first (Phase 4's manual-host field — it already
    works with the Phase 3 code and needs nothing from the player), then the
    relay as **Phase 6**, which pushed conditions/rolls/NPCs/polish to 7–10. The
    relay is placed right after player join so every later feature is exercised
    over the real transport rather than only loopback.
  - **Hosting is deliberately unanswered** — the relay runs anywhere with a
    public IP, the URL is a setting, and any GM can run their own. Pick a default
    instance when real players need to join.
- **2026-07-22 — Phase 4 done.** The session layer reaches the screen: a Qt
  bridge, the GM window, and the tunnel path.
  - **`ui/session_bridge.py`** — `SessionBridge(QObject)`, the *only* place core
    `on_event` callbacks become signals. It is either hosting (owns a
    `SessionServer`) or joined (owns a `SessionClient`), never both, and the
    signals that exist on both sides are the same ones (`rosterChanged`,
    `rollAdded`, `historyReplaced`, `error`) so a shared widget — the roll
    history in Phase 8 — can be written once. Host-only: `started`, `stopped`,
    `published`, `playerJoined`, `playerLeft`, `snapshotReceived`, `refused`.
    Client-only: `connected`, `disconnected`, `conditionCommand("apply"|"remove",
    payload)`, `kicked`. **Every payload signal is `Signal(object)`, deliberately
    not `Signal(dict)`/`Signal(list)`** — those map onto `QVariantMap`/
    `QVariantList` and would flatten nested values and `None`. Worker threads
    emit directly: the bridge lives on the GUI thread, so `AutoConnection`
    queues the call. Also `active_session()`/`set_active_session()` (the
    process-wide handle Phase 5's sheet and Phase 8's roller attach to) and
    `last_session()`, which resumes `session_last_id` from settings.
  - **Publishing runs off the GUI thread.** `bridge.publish()` spawns a thread
    around `discovery.publish_session` (SSDP + two HTTP round trips: seconds in
    the bad case) and answers with the `published` signal; `stop()` joins it,
    then releases any port mapping on *another* thread so a closing window never
    blocks on the router. The probe is behind `bridge._publish_session`, which is
    what lets every UI test cann a `Reachability` instead of touching a network.
  - **`ui/gm_window.py`** — `GMWindow`: session name, port (0 = "automatic"),
    the tunnel field, Start/Stop hosting, the join code with a Copy button, the
    advice, and a roster list. `discovery`'s `Reachability.advice` is rendered
    **verbatim**, one wrapped label per string, and the three outcomes are
    visibly different: manual/tunnel (accent, names the address), internet-
    reachable (green), LAN-only (red — *not* a silent LAN address that reads like
    success). Hosting locks the port and tunnel fields, because they are what the
    outstanding join code says.
  - **The tunnel path works end to end.** The GM pastes what playit.gg/ngrok gave
    them, `parse_address` validates it, and the join code carries that host *and
    that port*. That last part needed a one-line core fix: `publish_session`'s
    manual branch ignored `external_port` and put the **local** port in the code,
    which is wrong for every tunnel (the tunnel's public port is rarely the port
    being listened on). Covered by two new tests in `test_session_discovery.py`.
  - **The launcher's "Open GM Mode" is wired** (`_not_implemented` is gone). The
    launcher stays visible behind it — a GM still opens character sheets — and
    only one GM window ever exists; a second click raises it.
  - **The GM window is reusable, not deleted on close.** Closing stops hosting,
    releases the mapping and clears the active session; `showEvent` re-arms it,
    and the session state (name, roster, rolls) is still there. The first attempt
    used `WA_DeleteOnClose` plus `destroyed → launcher._forget_gm_window`, and
    that **hard-crashed Python** (access violation) when the launcher was
    garbage-collected and Qt emitted `destroyed` into a bound method on a
    half-finalized receiver. Do not reintroduce that pattern.
  - **Tests** — `tests/test_session_bridge.py` (18: real loopback server, queued
    signals drained with `processEvents`) and `tests/test_gm_window.py` (22,
    canned reachability, server bound to `127.0.0.1` on port 0 so no test ever
    pops a firewall prompt or collides). Full suite: 835 passed, only the known
    environmental `test_block_sizes` font failure.
  - Deferred: no player cards (Phase 5 — the roster is a plain name list for
    now), no NPC or roll-history panels, no "which saved session?" picker (the
    window resumes the last one), and no mapping-renewal timer. `bridge.join()`
    exists and is tested, but nothing in the UI calls it yet — Phase 5's join
    dialog is its first caller, and it already dials through
    `discovery.transport_for(code.host)`, so a relay join code will work there
    unchanged. One cosmetic nit for Phase 10: `ADVICE_FIREWALL` contains literal
    `*and*` asterisks, which read as markdown left in a GUI label.

- **2026-07-22 — Sustainability decisions for a public relay.** The open question
  behind the relay was cost: this is an open-source app that should "just work"
  on download, funded out of one person's pocket. Worked through with numbers
  (see the two cost rows in the decisions table) and settled:
  - **The cost fear is largely unfounded** — at ~25 MB per relayed table-hour, a
    €4/month box carries tens of thousands of weekly groups, and concurrent
    sockets bind before bandwidth does. What makes a relay expensive is building
    it wrong, so the dumb-pipe / `selectors` / stateless constraints in Phase 6
    are **cost decisions**, not style preferences, and should not be traded away
    for implementation convenience.
  - **Relay last, never first.** Direct connections cost nothing, so the ladder
    is direct → relay, and Phase 5 debounces snapshots from the start. The
    ~40 KB snapshot size the estimate rests on is a **guess to be measured in
    Phase 5** and corrected here.
  - **Transport security decided: relay-terminated TLS** — stdlib `ssl`, real
    certificate, zero player configuration, keeps Python 3.10+. End-to-end was
    rejected for v1 because the stdlib has no symmetric cipher: it would need
    either `cryptography` (breaking the no-new-dependencies rule) or 3.13-only
    TLS-PSK (dropping 3.10–3.12). The honest statement in the docs is that the
    relay operator *could* read traffic and that self-hosting is one command.
  - **Not building:** accounts, logins, billing, or hole punching. Also worth
    remembering that the real cost of public infrastructure is uptime
    expectation and abuse handling, not the €4 — hence best-effort with no SLA,
    hard caps, and a readable degradation path.

- **2026-07-22 — Phase 5 done.** Players can join, and the GM sees them live.
  - **`ui/session_dialogs.py`** — `JoinSessionDialog`: join code, display name,
    and a picker of the saved characters (plus "(no character)"). The code is
    validated *here* with `decode_join_code`, so a typo draws discovery's own
    "that join code has a typo in it" instead of a doomed connection attempt;
    an accepted dialog therefore hands back a real `JoinCode`. Name and code are
    remembered in `session_player_name` / `session_recent_codes` (newest first,
    capped at 5) and offered back next time.
  - **`ui/session_player.py`** — `SnapshotPusher(QObject)` plus the module-level
    `snapshot_size(character)`. **It listens on the sheet's `SignalBus`, not on
    named section signals** (`EDITED`, `BUILD_CHANGED`, `CONDITION_CHANGED`), so
    a mod block that publishes those keeps the GM in sync with no edit here —
    and `BUILD_CHANGED` is what catches a runtime power toggle, which is
    deliberately not an `edited`. Constructing one sends the join snapshot at
    once; everything after only *arms* a single-shot `QTimer`, so a 20-edit
    burst is one send. `SNAPSHOT_DEBOUNCE_MS = 400`; tests pass `debounce_ms`.
    The bus has no unsubscribe, so `detach()` gates the handler rather than
    removing it — a detached pusher stays subscribed but inert.
  - **`ui/player_card.py`** — `PlayerCard`: portrait placeholder (snapshots
    carry no `image_path`), display name (`(you)` / red `— offline`), character
    name, PL, hero-point circles, condition chips, "Open sheet". **Two feeds,
    deliberately:** `set_roster(entry)` and `set_character(raw)`, because a
    roster entry carries no character (see the Phase-2 hardening note) and a
    snapshot carries no name. Reuses `HeroPointsWidget` — made read-only with
    `WA_TransparentForMouseEvents`, *not* `setEnabled(False)`, which greys the
    circles out and reads as "this player has no hero points".
  - **`ui/sections/conditions.py`** — `_condition_display_name` was lifted to a
    module-level `condition_display_name(applied, record)` so a condition reads
    identically on a chip and on a GM card; the section now delegates to it.
  - **`ui/gm_window.py`** — the plain roster list is now a `FlowLayout` grid of
    cards. `_show_roster` **reconciles in place** rather than rebuilding: the
    roster is re-broadcast on every join *and* every snapshot, so a rebuild
    would flicker the whole grid whenever anyone edited their sheet.
    `_snapshots` is seeded from `SessionState.players` before hosting starts, so
    a resumed session comes up with populated cards instead of blanks. "Open
    sheet" builds a read-only `MainWindow` from the newest snapshot, replacing
    any sheet already open for that player, and the GM window closes them all
    when it closes.
  - **`ui/start_window.py`** — a "Join Session" button. It refuses a second
    join while `active_session()` is set, opens the chosen character in a
    locked sheet (conditions, hero points and power toggles all stay live while
    locked — exactly the play-time surface), attaches a `SnapshotPusher`, and
    leaves the session when that sheet closes. Disconnect/kick land in the
    sheet's status bar rather than a modal. `MainWindow` gained a public
    `sheet` property as the attach seam.
  - **The snapshot size was measured** — see the new "Relay cost, measured" row.
    Short version: **~3–4 KB for a typical sheet**, not the guessed 40 KB, so
    relay bandwidth is a non-issue and **deltas are not needed**. The debounce
    still is; keep it.
  - **Tests** — `tests/test_session_player.py` (17: real loopback session, the
    pusher's coalescing, the dialog, and the launcher's join end to end) and 9
    new ones in `tests/test_gm_window.py` (30 total). Full suite: 860 passed,
    only the known environmental `test_block_sizes` font failure.
  - Deferred, and worth knowing: **an opened player sheet does not update
    itself.** The card is live; the sheet is the snapshot at the moment it was
    opened, and clicking "Open sheet" again re-reads the latest. Making it live
    would need a re-seed API on `CharacterSheet` (every section currently seeds
    only at construction) — a real piece of work, not a line. Also deferred: no
    reconnect-on-drop (the client remembers `player_id`/`player_token`, but
    nothing retries), no kick control on the card (`server.kick` exists), and
    portraits still show a placeholder.
