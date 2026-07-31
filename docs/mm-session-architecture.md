# The GM session and online play

MM-Companion lets a GM host a **live session** that players join over the
network. Everyone at the table sees the same roster, the same synchronised roll
history, and the GM can reach onto a connected player's sheet to apply a
condition. The session **persists**: closing and reopening the app resumes the
same session, roster, and full roll log, and players reconnect to it.

This document is the map of how that works. For the player-facing "how do I
actually connect over the internet" walkthrough — tunnels, the relay, and what to
do when a join fails — see [`mm-session-networking.md`](mm-session-networking.md).

## The layering

The session layer obeys the same `ui → core → data` rule as the rest of the app.
All the networking is **pure Python with no PySide6**, so it is headless-testable
and reusable by the standalone server and relay entrypoints; Qt appears only in
the `ui/` wrappers.

```
core/session/     pure Python — protocol, model, store, transport, server, client, discovery, relay
ui/               Qt — session_bridge, gm_window, npc_window, player_card, roll_history, session_*
relay/            python -m mm_companion.relay  — the public byte-pump box
server/           python -m mm_companion.server — a headless host for 24/7 uptime
```

### `core/session/` — the engine

| Module | What it holds |
| --- | --- |
| `protocol.py` | The message vocabulary. `PROTOCOL_VERSION`, frozen message dataclasses (`Hello`, `CharacterSnapshot`, `RollRequest`, `Welcome`, `Roster`, `RollAdded`, `ApplyCondition`/`RemoveCondition`, `ErrorMessage`, `Kicked`, `Ping`/`Pong`) with generic, annotation-driven validation, and `encode`/`decode` (newline-delimited UTF-8 JSON, capped at `MAX_MESSAGE_BYTES` = 256 KiB). `sanitize_snapshot()` strips a character's `image_path` — a portrait path is meaningless on another machine. |
| `model.py` | `SessionState` (id, name, timestamps, `players`, `npc_paths`, `rolls`, `host_token`), `PlayerSlot`, and `RollRecord`. Two token layers: the session's **`host_token`** (the join secret carried in the code) and a per-slot **`token`** a returning client presents to reclaim its seat. `visible_rolls()` filters out hidden GM rolls; `new_session(name)` mints one. |
| `store.py` | Workspace persistence, modelled on `core/library.py`: `sessions/<id>/session.json` plus an **appended** `rolls.jsonl`, so a roll never rewrites the whole history. `save_session`, `append_roll`, `load_session` (stitches the two back and clears stale `connected` flags), `list_sessions`, `delete_session`. Session ids are validated against `^[A-Za-z0-9_-]{1,64}$` before they touch a path — an id can arrive over the wire. |
| `net.py` | `Connection` (framed, buffered, lock-guarded writes), the `Transport`/`Listener` ABCs, and the loopback/LAN `TcpTransport`. `DEFAULT_PORT = 47331`. |
| `server.py` | `SessionServer` — an accept thread, one reader thread per peer, one `RLock` over every mutation. It **rolls** (a client sends a request; the server resolves with `core.dice.resolve_check`, so no client edits its own number), persists on every change, and broadcasts. A callback `on_event(kind, payload)` reports to the owner; the payload is always a plain dict. No Qt. |
| `client.py` | `SessionClient` — connect, handshake on the calling thread (returns the `Welcome` or raises with an `ERROR_*` code), then one reader thread emitting `EVENT_*`. Remembers `player_id`/`player_token` for reconnect. |
| `discovery.py` | Getting reachable: join-code encode/decode, UPnP/IGD port mapping and external-IP discovery (SSDP + SOAP, stdlib only), a manual-address fallback, `publish_session()` → a `Reachability` carrying the address **and finished advice prose**, and the `transports` registry / `transport_for()` seam a relay plugs into. |
| `relay.py` | `RelayTransport` — reach a session through a relay by dialling *out* to it. Registered into `discovery.transports` under `mmrelay://` (TLS) and `mmrelay+tcp://` (plaintext), so a relay join code just works: `server.py`/`client.py` are untouched. |

### What a roll is, and what it provokes

`RollRequest.label` / `RollRecord.label` carry the **name** of what was rolled
("Athletics", "Blast: 7 vs. Defense"). The field was designed in from the start
and sat empty until rolling from the character sheet landed; the dice roller now
fills it from the loaded `RollSpec` (see CLAUDE.md, "Rolling from the sheet"), so
a shared history reads as who rolled *what*.

`RollRequest.spec` / `RollRecord.spec` carry the **sheet's description** of the
roll — a serialized `RollSpec`: the save this attack will force, the degree ladder
that save reads, which resistance it is. It travels because the roll it describes
is *acted on by somebody else*: the target's player sees the attack land in the
shared history and clicks the save straight off its card, and everyone watches the
consequence. A chain only the roller could see would be pointless.

Two rules keep that from leaking rules into the session layer:

- **The server never interprets a spec.** It validates the shape with
  `protocol.sanitize_spec` — a key whitelist, caps on text, ladder length and
  `follow_up` nesting depth, because this is client-supplied data that gets
  broadcast and rendered on every other screen — then records and rebroadcasts it
  untouched. `core/session/` must never import `core.rules`; the standalone server
  loads no game data at all.
- **The consequences are derived on the clients**, from the broadcast `die` and
  `degree`. A critical hit's raised DC and the outcome a failed save reads are both
  deterministic functions of numbers everybody already has, so every screen at the
  table computes the same chip and the same sentence without the server knowing a
  single rule.

A hidden GM roll is never broadcast, so its spec reaches nobody either — the chain
is exactly as visible as the roll that started it.

### The handshake

A joiner sends `Hello` (protocol version, host token, display name, app version,
mod fingerprint, and optionally a slot token to reclaim a seat). The server
checks, in order: **protocol version** (a mismatch refuses with a readable
message), **host token** (`ERROR_BAD_TOKEN` otherwise), then a **slot claim or a
new seat** subject to the client limit. On success it replies with `Welcome`
(session id/name, the player's id and slot token, the roster, and the recent roll
history). A **mod-fingerprint mismatch is a warning**, not a refusal — the session
works, but ids from the other end may not resolve against the mods loaded here.

### Two rules the server never breaks

- **The server rolls.** Every roll — a player's request or the GM's own — goes
  through the one `_resolve_roll` path, so a client can never supply the die.
- **A hidden roll is never broadcast.** Only a slot marked `is_gm` may ask for
  one; a player's `hidden` flag is ignored. A hidden roll is recorded, persisted,
  and shown to the GM's own window with a 👁 marker, but it is left out of the
  wire entirely — there is nothing for a player client to peek at.

## The connection ladder

Reaching the GM's machine from the internet is the hard part, because most home
connections sit behind NAT — often **carrier-grade NAT**, where no port forward
can ever help. The app tries the cheapest thing first and falls back:

1. **Direct** — a listening socket, made reachable by **UPnP** if the router
   allows it, or a **manual port forward**, or a **tunnel** (playit.gg, ngrok,
   Tailscale) whose address the GM pastes into the tunnel field.
2. **Relay** — both ends dial *out* to a public box that splices them, which
   works from behind every NAT. This is the fallback, never the default: a GM who
   is reachable directly costs the relay nothing.

`publish_session()` never raises — every outcome is a `Reachability` with the
address to publish and `advice` (finished sentences) explaining what happened and
what to do. The GM window renders that advice **verbatim**, and the three
outcomes look visibly different (a green internet-reachable, an accent
manual/tunnel address, a red LAN-only). The relay itself is documented in
[`mm-session-networking.md`](mm-session-networking.md).

## The Qt side

`ui/session_bridge.py`'s **`SessionBridge`** is the *only* place where the core
`on_event` callbacks become Qt signals. A bridge is either hosting (owns a
`SessionServer`) or joined (owns a `SessionClient`), never both; the signals that
exist on both sides share a name so a shared widget — the roll history — is
written once. Worker threads emit directly and Qt's `AutoConnection` queues the
call onto the GUI thread. Module-level `active_session()`/`live_session()` are the
process-wide handle the sheet and the roller attach to without threading it
through every constructor.

The windows and widgets:

- **`gm_window.py`** — the GM's console: host controls, the join code with a Copy
  button, the reachability advice, the **Players** grid, the **NPCs** grid, and a
  **Rolls** box (an embedded roller with a Hidden-roll switch beside the shared
  history).
- **`player_card.py`** — one card per connected player (never the GM's own seat):
  portrait (the transmitted thumbnail, else a placeholder), name, PL, hero points,
  condition chips (each with a hover hint), "Open sheet", and a "+" that
  fast-applies a condition onto that player's live sheet.
- **`npc_window.py`** — an NPC is an ordinary `Character` in a simplified sheet
  (the point-pool row replaced by an *estimated* PL). NPCs are GM-only and never
  go on the wire; they live in the workspace `gm_characters/` dir.
- **`session_player.py`** — `SnapshotPusher` (sends the player's sheet on join and,
  **debounced**, on every edit) and `ConditionReceiver` (applies a GM's condition
  command to the player's live sheet). Both listen on the sheet's signal bus.
- **`roll_history.py`** — `RollHistoryPanel`, the shared log shown in both the GM
  window and each player's Dice Roller.

### Snapshot sync

A player's client pushes a **whole character snapshot** on join and on every
change, coalesced by a 400 ms debounce so a burst of edits is one send. A typical
PL 10 sheet encodes to **~3–4 KB** (measured with
`ui.session_player.snapshot_size`), so bandwidth is a non-issue at any plausible
scale and sending deltas is not needed — the debounce is. The snapshot's
`image_path` is stripped (a path is meaningless on another machine); the portrait
instead rides along as a **downscaled base64 thumbnail** under a `portrait` key
(`ui/session_portrait.py`), kept well under the message cap so it shares the same
snapshot. `SnapshotPusher` encodes it (caching until the source changes) and the
player card / GM's read-only sheet decode it; a card with none shows a placeholder.

## The two headless entrypoints

- **`python -m mm_companion.server`** (`mm-companion-server`) hosts a session
  without the GUI, for a GM who wants the table reachable around the clock on an
  always-on box. It runs the same `SessionServer`, shares a workspace via
  `MM_COMPANION_HOME`, and prints the join code and reachability banner. With
  **`--hub`** it hosts every session in the workspace at once and opens the
  control channel — see "Sessions that live on a server" above. Deploying it is
  `deploy/README.md`.
- **`python -m mm_companion.relay`** is the public relay: a single-threaded
  `selectors` loop that pairs two *inbound* connections and pumps bytes between
  them. It parses only the relay envelope and holds no session state, so one small
  box serves thousands of tables. Deploying it is covered in the networking guide.

## Sessions that live on a server

A session need not belong to the GM's laptop. `python -m mm_companion.server
--hub` hosts **every** session in its workspace at once, so a table outlives the
machine that started it: players join whenever they like, the GM dials in and
takes their seat, and closing GM Mode leaves the game running.

**Reachability is free**, and that is why this cost so little. A relay join code
already carries the session id (`mmrelay://host:port/<session-id>`), so each
session on the hub registers with a relay by dialling *out* to it, exactly as a
GM's app does. No inbound port, no new transport, no join-code change — a player
cannot tell a hub-hosted session from a laptop-hosted one.

### The two secrets

| Secret | Who holds it | What it opens |
| --- | --- | --- |
| `host_token` | everyone at the table (it is *in* the join code) | one session, as a player |
| `gm_token` | the GM alone | the **GM's seat** in one session |
| admin secret | the GM alone, from the server's `/etc` | the **catalog** — list, create, rename, delete |

`Hello.gm_token` is checked right after the host token. A wrong one is **refused**
rather than quietly seating the claimant as a player: that failure "works", right
up to the moment a hidden roll is broadcast to the table.

The admin secret is the whole of "only a GM can create a session". It never
travels in a join code, so a player holds nothing that opens the catalog.

### What a remote GM needed that a local one got for free

- **Player sheets.** The roster deliberately carries no characters, so a
  socket-connected GM could see none. `PlayerSnapshot` forwards each one to the
  GM seat and nowhere else.
- **The result of their own hidden roll**, which is never broadcast.
- **Kick, rename and the NPC cast**, none of which had a wire message.

### The control plane

`SessionCatalog` is the answer to *every* control request — create, rename,
delete and list alike. Returning the whole catalog rather than a delta costs a
few hundred bytes and removes any chance of a GM's list drifting out of step with
the server's. `core/session/hub_client.py` is the app's side: no reader thread
and no events, unlike `client.py` — connect, ask, read, close.

### Idle sessions

A session with nobody in it stays registered and joinable (that is one socket)
but sheds its roll history from memory after ten minutes, reloading on the next
arrival. The reload runs in `SessionServer`'s `on_activate` hook, called *before*
the handshake rather than after: the `Welcome` carries the recent history, and
sequence numbers are assigned from the tail of that list, so reloading late would
restart the numbering and corrupt the log.

## What is deliberately deferred

- **An opened player sheet on the GM side is a snapshot, not live.** The player
  *card* updates in real time; re-opening the sheet re-reads the latest snapshot.
  The GM's sheet is a **fully-locked read-only view** (`MainWindow(gm_view=True)`):
  only a View menu, no way to unlock, save, or edit. Live re-seeding needs a
  re-seed API on `CharacterSheet`.
- **End-to-end encryption.** TLS terminates at the relay, so the relay *operator*
  could in principle read traffic; self-hosting the relay is the answer for anyone
  who minds (one command). The stdlib has no symmetric cipher usable across
  Python 3.10–3.13 without a new dependency, which the project forbids.
