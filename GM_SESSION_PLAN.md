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
  - Deferred: no live-network verification (adding a real port mapping edits the
    user's router configuration, so it was not done unasked) — the SSDP/SOAP path
    is exercised only against canned data, and the first real host is where a
    router quirk would show up. No `Reachability` refresh/renewal timer either: a
    permanent lease needs none, but a router that forced a finite lease in
    `add_port_mapping` will drop the mapping when it expires. Nothing in `ui/`
    yet — Phase 4 wires `publish_session` and the advice into the GM window.
