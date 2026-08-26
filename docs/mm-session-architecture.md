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
| `protocol.py` | The message vocabulary. `PROTOCOL_VERSION`, frozen message dataclasses (`Hello`, `CharacterSnapshot`, `RollRequest`, `Welcome`, `Roster`, `RollAdded`, `ApplyCondition`/`RemoveCondition`, `SetScene`/`SceneUpdate`, `SetScenePortrait`/`ScenePortrait`, `ErrorMessage`, `Kicked`, `Ping`/`Pong`) with generic, annotation-driven validation, and `encode`/`decode` (newline-delimited UTF-8 JSON, capped at `MAX_MESSAGE_BYTES` = 256 KiB). `sanitize_snapshot()` strips a character's `image_path` — a portrait path is meaningless on another machine; `sanitize_scene()` does the same job for the GM-supplied board. |
| `model.py` | `SessionState` (id, name, timestamps, `players`, `npc_paths`, `rolls`, `host_token`, and the three `scene*` fields below), `PlayerSlot`, and `RollRecord` — a roll, a note *or* a request, per its `kind` (see "Notes" and "Requests" below). Two token layers: the session's **`host_token`** (the join secret carried in the code) and a per-slot **`token`** a returning client presents to reclaim its seat. `visible_rolls()` filters out hidden GM rolls; `new_session(name)` mints one. |
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
fills it from the loaded `RollSpec` (see `docs/notes/dice-and-rolling.md`,
"Rolling from the sheet"), so
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

### Notes: the history's other kind of entry

Some things worth writing down are not rolls. A hero point spent or granted moves a
number on somebody's sheet and nothing else, and before notes existed the table only
learned about it if the player said so out loud.

A note is a `RollRecord` with `kind = "note"`, carrying its sentence in `text` and
leaving the dice fields at their defaults. **One record type, because the history
*is* a log** — seq-numbered from one counter, appended to `rolls.jsonl`, replayed to
a late joiner in the `Welcome`, strikeable by the GM — and a note wants every one of
those. A line written before notes existed loads as `kind = "roll"`, so the log is
backward compatible on disk; the wire is not, hence `PROTOCOL_VERSION` 6.

The same two rules apply as to a spec. The client composes the sentence (what is
worth noting is a rules question) and the server stores it opaquely, capping it at
`MAX_NOTE_CHARS` exactly as it caps a roll label, and attributing it to the seat it
arrived on rather than to anything the text claims. A note is never hidden — hiding
is a property of *rolls*, and a note says what happened at the table.

The **author is the player's own app**, whichever end the change came from: a GM's
`SetHeroPoints` lands on the player's sheet and the note is raised there, so a grant
and a click produce exactly one note and it reflects what actually landed. The GM's
window shows it through the same shared feed.

### Requests: the history's third kind of entry

The mirror image of a note. A note is something that already happened; a **request**
is something that has not happened yet — "everyone roll Perception vs DC 15" — and
it exists because the only way one screen could ever put a roll button on another's
was the attack → save chain, which needs an attack.

A request is a `RollRecord` with `kind = "request"`, and it needs **no new field**:
the trait's name goes in `label`, the difficulty in `dc`, and the descriptor — an
ordinary serialized `RollSpec` — in `spec`, all three of which a roll already
carried. `die` stays 0 as a note's does. Client → server it is `RollPrompt`, whose
only field is that spec, run through the same `sanitize_spec` a roll's goes through
and dropped rather than recorded if nothing survives (a card with a dead button on
it is worse than no card). `PROTOCOL_VERSION` 8 exists for it, for the reason 6
existed for notes: an old server rejects the unknown type, and an old client would
render a request as a d20 that rolled zero.

Three rules that are not a note's:

- **Anyone may ask.** Unlike striking a card, this carries no `slot.is_gm` gate. A
  player asking the GM to roll something is as ordinary as the reverse.
- **The button is on every card, the asker's included** — the opposite of the save
  chip, which is deliberately *not* localized on one's own card because you are not
  the target of your own attack. Here you are: someone asking the table to roll
  Perception is asking themselves too.
- **It travels with `modifier = 0` and a `trait_key`.** The asker must not send
  their own number; each client's Dice block fills in that character's through
  `localize_spec`, which for this reads the key together with the spec's `kind` so
  it can answer an ability, a resistance, a skill or initiative rather than only a
  resistance. An empty `kind` still means a resistance, which is what an older
  client's save says.

Powers and equipment are deliberately not offerable: a pin names a power by an id
belonging to one character, so there is nothing honest to localize it to on anyone
else's sheet.

### The scene: the one thing the whole table sees

Everything else the GM holds is the GM's. NPCs are never on the wire at all —
`npc_paths` is stored and handed back to the GM alone, precisely because it names
files in their workspace — and players cannot see each other, since
`PlayerSnapshot` goes to the GM seat only and a `Roster` entry deliberately
carries no character. The **scene** is the deliberate exception: a curated,
ordered list of who is in this fight, authored by the GM and rendered on every
screen.

`SessionState` gains three fields for it and the split between them is the whole
design:

| Field | Who sees it | Why it is its own field |
| --- | --- | --- |
| `scene` | everyone | The board: `{ref, name, player_id, initiative, conditions}` per entry, and nothing else. |
| `scene_sources` | the GM alone | `ref` → `"npc:<file>"` / `"player:<id>"`, handed back in the GM's `Welcome` like `npc_paths`. |
| `scene_portraits` | everyone, separately | `ref` → base64 thumbnail, sent once per entry rather than with every board. |

Four messages: `SetScene` / `SetScenePortrait` up, `SceneUpdate` / `ScenePortrait`
down. `PROTOCOL_VERSION` 9 exists for them, and the failure it prevents is quieter
than 8's: a v8 client joins happily, never learns the type exists, and shows an
empty board through a whole fight the rest of the table is watching.

Five decisions worth knowing:

- **The GM is the only writer.** `SetScene` carries `slot.is_gm` the way
  `RemoveRollRequest` does. The board is what everybody is looking at, so it has
  exactly one author and there is no reconciliation to get wrong.
- **It is sent whole, not as deltas.** It is small, it changes for half a dozen
  unrelated reasons (a condition applied, an initiative rolled, a card dragged, a
  player joining), and a delta stream only means anything replayed in order.
- **The pictures travel apart from the board, and are replayed after the welcome.**
  A scene is re-sent every time anything on it changes; carrying a dozen
  thumbnails along each time is the one thing that could make a relayed table
  expensive, and a dozen in one message would blow `MAX_MESSAGE_BYTES` outright. So
  a portrait goes once, when its entry joins, and the server follows each
  `Welcome` with one `ScenePortrait` per stored picture — N small messages cannot
  aggregate past the cap the way one large one can. They are also much smaller
  than a *sheet* portrait: 96px, capped at 8 KiB (`MAX_SCENE_PORTRAIT_CHARS`),
  because a board's worth is stored per session and replayed to every joiner.
- **A ref says nothing.** It is minted by the GM and opaque, because it is the only
  part of an entry that reaches a player: an NPC's file name can be a spoiler
  outright, and the scene is exactly where a GM would find that out too late.
  `scene_sources` is what maps it back, and it never leaves the GM's seat.
- **A scene card is not a statblock.** A player reads a thumbnail, a name, an
  initiative and the condition chips off it. The guarantee is not a rule the widget
  keeps — it is that `sanitize_scene` carries nothing else, so a card cannot show
  what never left the GM's machine.

**Initiative needed no message of its own.** An NPC's is rolled locally on the GM's
own card and reaches the table as the board's `initiative` field, because a dozen
mook rolls in the shared log would bury the line the table is waiting for. A
player's arrives on the log that already exists: every roll carries the `RollSpec`
that describes it, so the GM window watches `rollAdded` for `spec.kind ==
"initiative"` and puts the total on the board. That catches both routes at once —
answering the request card the GM's **Roll initiative** button posts, and a player
rolling Initiative off their own sheet — because the two produce the same record.
Note that `RollRecord.to_dict()` writes the parts and not the sum.

### The handshake

A joiner sends `Hello` (protocol version, host token, display name, app version,
mod fingerprint, and optionally a slot token to reclaim a seat). The server
checks, in order: **protocol version** (a mismatch refuses with a readable
message), **host token** (`ERROR_BAD_TOKEN` otherwise), then a **slot claim or a
new seat** subject to the client limit. On success it replies with `Welcome`
(session id/name, the player's id and slot token, the roster, and the recent roll
history). A **mod-fingerprint mismatch is a warning**, not a refusal — the session
works, but ids from the other end may not resolve against the mods loaded here.

**Finding the right seat** has two steps, and the second is a deliberate
weakening of the first. `player_by_token` matches the secret slot token, which is
the real claim. If that misses, `player_by_id_if_free` will hand back the seat the
client's *public* `player_id` names — but only when that seat is **empty** and
**not the GM's**. Without it, a player who cleared their settings, moved machines,
or pasted the join code by hand arrived as a second card on the GM's board while
their first sat there greyed out forever. What it costs is that a table-mate could
claim someone's offline seat and appear under their name; that is bounded (a live
seat can never be taken, no character is disclosed — snapshots go to the GM's
connection alone — and hidden rolls still need the GM token), and everyone who
could try it already holds the join code and is sitting at the table. Matching on
`display_name` was considered and rejected: two real players called "Sam" would
silently become one seat.

### Two rules the server never breaks

- **The server rolls.** Every roll — a player's request or the GM's own — goes
  through the one `_resolve_roll` path, so a client can never supply the die.
- **A hidden roll is never broadcast.** Only a slot marked `is_gm` may ask for
  one; a player's `hidden` flag is ignored. A hidden roll is recorded, persisted,
  and shown to the GM's own window with a 👁 marker, but it is left out of the
  wire entirely — there is nothing for a player client to peek at.

### Keeping a link alive, and what happens when it dies

`Ping`/`Pong` existed from the first version of the protocol and **nothing ever
sent one** until v7. The cost of that was concrete: a relay drops a pair that has
moved no bytes for `RelayLimits.idle_timeout` (120 s), and a table that is being
*roleplayed* rolls nothing and edits no sheet, so it was reaped mid-session. The
deployment worked around it by raising the timeout to four hours.

Now every client keeps its own link warm. Hearing nothing for
`net.KEEPALIVE_INTERVAL` (30 s) its reader thread sends a `Ping`; hearing nothing
for `net.PEER_TIMEOUT` (90 s — three missed keepalives, and deliberately *under*
the relay's 120 s) **either end** gives up on the peer. One exchange is enough for
a whole relayed pair, because the relay stamps `last_active` on writes as well as
reads: the ping is a read on the client's peer and a write on the session's, and
the Pong is the reverse. So there is no separate server heartbeat.

The peer deadline is the other half of the fix, and it matters on its own: before
it, a half-open link — a black-holed connection, a suspended laptop, a NAT that
dropped its mapping — was **invisible**. `recv` simply timed out forever on both
sides, so a player sat in a session that was not there and the GM's roster showed
a ghost as connected indefinitely.

A connection that ends for a reason worth retrying is **redialled** in the
background (`RECONNECT_DELAYS`, giving up after `RECONNECT_WINDOW` = 5 min),
re-presenting the `player_id`/`player_token` already in hand — which is what makes
a blip land back in the *same* seat with no user action. Retryable: a closed
socket, an unreachable host, a torn frame, our own peer timeout. Terminal: a kick,
an explicit `close()`, and a refusal in `TERMINAL_CODES` (bad token, protocol
version, session full, rate limit) — answers that five more minutes would not
change.

One rule holds this together and is worth stating on its own:

> **`EVENT_DISCONNECTED` means the session is over**, not that a packet went
> missing. A blip raises `EVENT_STATE` and nothing else.

Several things hang off that signal — the dice block swaps the table's shared roll
history back for the private one, the sheet says "Left the session" — and firing
it for a two-second Wi-Fi drop tells the player something false and throws away
the table's history. A successful reconnect re-raises `EVENT_CONNECTED` with a
fresh `Welcome`, so the roster and history repaint from it with no separate "you
are back" path.

Stopping a server **says so** (`Kicked` with `REASON_SESSION_CLOSED`), because a
deliberate end and a sleeping laptop are otherwise indistinguishable and players
would spend the whole retry window redialling a table that had closed.

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

Two properties that look like synonyms and are not. **`joined`** follows the
*socket* and goes False the moment it dies, which is what makes a send fail
honestly mid-blip. **`in_session`** follows the *session* and stays true across a
reconnect. `live_session()` asks the second one, and the reason is a bug it once
had: asking `joined` meant a roll made during a blip took the solo path, rolled
locally, and landed in the private history — a roll the table never saw and the
player thought it had. Asking `in_session` sends it, fails, and says so.

The windows and widgets:

- **`gm_window.py`** — the GM's console: host controls, the join code with a Copy
  button, the reachability advice, the **Players** grid, the **NPCs** grid, and a
  **Rolls** box (an embedded roller with a Hidden-roll switch beside the shared
  history).
- **`player_card.py`** — one card per connected player (never the GM's own seat):
  portrait (the transmitted thumbnail, else a placeholder), name, PL, hero points,
  condition chips (each with a hover hint), and a "+" that fast-applies a
  condition onto that player's live sheet.
- **`card_summary.py`** — the half of a card that is the same on both kinds. A
  player's card and an NPC's differ in what they may *do* (a player's is a remote
  snapshot the GM can only ask to change; an NPC's is the GM's own model, edited in
  place) but not in what a GM *reads*, so both hover the same abilities /
  resistances / powers summary and both open their sheet from the **portrait
  alone**. Opening used to be a click anywhere on an NPC card, which fought that
  card's own drag-to-reorder gesture — the two were told apart only by how far the
  pointer had moved.
- **`npc_window.py`** — an NPC is an ordinary `Character` in a simplified sheet
  (the point-pool row replaced by an *estimated* PL). NPCs are GM-only and never
  go on the wire; they live in the workspace `gm_characters/` dir.
- **`session_player.py`** — `SnapshotPusher` (sends the player's sheet on join and,
  **debounced**, on every edit) and `ConditionReceiver` (applies a GM's condition
  command to the player's live sheet). Both listen on the sheet's signal bus.
- **`roll_history.py`** — `RollHistoryPanel`, the shared log shown in both the GM
  window and each player's Dice Roller.
- **`connection_indicator.py`** — a dot and a short label in the menu bar's
  top-right corner (`setCornerWidget`), installed by both `MainWindow` and
  `GMWindow`. It exists because every other report of a lost session **fades**:
  the GM's notice strip after ten seconds by design, a status-bar message the
  same. It follows a bridge and recomputes its *whole* state from that bridge on
  every signal rather than mapping each signal to a state — several signals arrive
  for one transition, and one state function is what stops them disagreeing.

### Pinned parameters: the strip down a card's right side

The four or five numbers *this* GM wants off *this* creature, without opening its
sheet. `core/rules/pins.py` is the model and is pure Python like the rest of
`core`: a **`PinRef`** names an ability, a resistance, a skill, initiative, a
defence DC or one of a power's rolls, and `resolve_pin` turns it into a caption, a
reading and a `RollSpec`. A reference, **never a number** — the character
underneath is live, so a frozen value would be right once and then quietly wrong.

Three things the *reading* has to get right, each of which was a bug first:

- **Values come off the roll builders** (`resistance_roll`, not
  `resistance_total`), which folds the condition overlays in for free while the
  build math itself stays condition-free — and `with_conditions=False` takes them
  back out again for the picker, which is a catalogue of the creature rather than a
  combat readout. The `RollSpec` is identical either way, so nothing about what a
  chip *rolls* moves with it.
- **A defence DC is its own kind**, not a dressed-up resistance. The sheet's table
  shows the rank, and a chip quietly showing ten more would be a trap.
- **A pin that no longer resolves reads as a dash** rather than vanishing; a chip
  that disappears leaves no way to remove it. That is why `PinnedValue.missing` is
  a field of its own rather than `spec is None` — a **forced save** carries no spec
  either (the wielder never rolls their own target's save; it reaches the person
  who does as the attack's follow-up chip) but is perfectly well resolved.

`ui/pin_panel.py` is the strip: **click loads** into the GM's roller and
**double-click rolls**, the same bargain the sheet strikes everywhere else, plus
drag to reorder and right-click to remove. A click is a release with *no* drag
started and *no* double-click just handled, which is what keeps the four gestures
apart. `ui/pin_picker.py` is the modeless browser the "+" opens; it unpins as well
as pins, and its `set_pinned` is a no-op when it already agrees — without that
guard the card's echo rebuilds the tree *during* a toggle, deleting the row being
restated.

A GM also pins from the sheet opened off that card, by right-clicking a row. Two
bus topics — `pin-requested` and `unpin-requested` — served by the **sheet**
rather than by any block, since a pin's destination is outside the sheet entirely.
Which of the two a row offers comes from `stat_table.PinMenuState`, fed by
`CharacterSheet.set_pinned`, pushed from the card on open *and* on every change so
a sheet left open never offers to pin what is already there.

Strips persist per card in the `gm_pins` setting, seeded from `gm_default_pins`.
Three details:

- The NPC damage default is **late-bound** — "the first Damage power", resolving to
  the **attack roll** it makes, since the save it forces belongs to the target —
  because the defaults are written long before the NPC is.
- An **empty strip is written**, not dropped for tidiness. A GM who took every chip
  off a card meant it, and a missing key is what seeds the defaults.
- Read the setting through **`storage.gm_default_pins()`**, never off
  `load_settings()`: that returns the settings file verbatim and does not merge
  `DEFAULT_SETTINGS`, so a key added after a workspace was created reads back as
  `None` — which is exactly how this shipped once with every strip empty.

### Editing the defaults

The GM window has a **Settings** menu of its own, opening the app's Settings window
on its **GM Mode** page (`ui/settings/gm_page.py`) — two reorderable lists, one per
card kind, over `gm_default_pins`.

*When* a default is written down is the whole shape of that page. It predates the
card it will seed, so it cannot name that character's things: a `Power.id` belongs
to one character, and the only power a default may name is the late-bound
`select="first_damage"`. So the page opens the **same** `PinPickerDialog` the cards
do, with a `None` character putting it in defaults mode — listing
`default_pin_choices(game_data)` instead of `available_pins(char, …)` and hiding the
"Now" column, since there is nobody to read a value off.

Editing the defaults deliberately does **not** reach the board: `_pins_for` wrote
each card's strip into `gm_pins` the first time it saw the card, precisely so a
later change here cannot rearrange what is in play. **Apply to cards on the board**
is the confirmed override — `storage.clear_gm_card_pins()`, then
`GMWindow.reseed_pins_from_defaults()` on every open GM window. That order matters:
a window still holding its old strips in memory writes them straight back out on its
next edit and undoes the clear.

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

### Anyone may host; a session belongs to whoever made it

**Creating a session needs no credential.** The server is a public utility: a
stranger who has just installed the app can point it at one and run a game. What
that costs is a rule about *ownership*, and it is the whole design:

> Creating is open. Everything else needs the session's own `gm_token`, which the
> create handed back and nobody else ever sees.

| Secret | Who holds it | What it opens |
| --- | --- | --- |
| `host_token` | everyone at the table (it is *in* the join code) | one session, as a player |
| `gm_token` | whoever created the session | the **GM's seat**, plus renaming and deleting *that* session |
| operator secret | whoever runs the box, from its `/etc` | **everything** — the full list, and deleting anything |

Three consequences worth stating plainly:

- **There is no way to list other people's sessions.** `ListSessionsRequest` is
  refused for anyone but the operator. A GM's own sessions are remembered by
  their *app* (`session_my_sessions`), because a server-side list is exactly the
  thing that would hand every table's join code to whoever asked for it.
- **A wrong token and an unknown id give the identical refusal.** Telling them
  apart would make the endpoint an oracle for which session ids exist.
- **A wrong `gm_token` at the session handshake is refused, not downgraded.** Being
  quietly seated as a player "works", right up to the moment a hidden roll is
  broadcast to the table.

The operator secret is optional. Without one the box simply has no caretaker —
it does not stop anybody hosting, because nothing was gating that.

### Keeping a public box healthy

Three brakes, none of which a real GM ever notices:

- **A global ceiling** (`--max-sessions`), refused with a readable "this server is
  full" rather than by filling the disk.
- **A per-connection create limit**, so a script cannot make thousands cheaply.
- **A sweep** (`--retention-days`, 30 by default): a session nobody has touched in
  that long is deleted. `updated_at` moves on every join, roll and rename, so a
  monthly campaign is never at risk — only a table genuinely left behind. A
  session with somebody connected is never swept, however old its timestamp looks.

### What a remote GM needed that a local one got for free

- **Player sheets.** The roster deliberately carries no characters, so a
  socket-connected GM could see none. `PlayerSnapshot` forwards each one to the
  GM seat and nowhere else.
- **The result of their own hidden roll**, which is never broadcast.
- **Kick, rename and the NPC cast**, none of which had a wire message.

### The control plane

`ControlHello` opens the channel (with an empty secret, normally) and is answered
by `ControlWelcome`, whose `operator` flag says which kind of channel this is.
Create, rename, delete and status all answer with a `SessionInfo` describing that
one session — empty when it is gone, which is how a GM learns theirs was swept.
`SessionCatalog` exists but only ever reaches an operator.

`core/session/hub_client.py` is the app's side: no reader thread and no events,
unlike `client.py` — connect, ask, read, close.

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
- **A turn marker and a round counter.** The scene is an *order*, not a clock:
  there is no whose-turn-it-is highlight and no round number. Both are additive
  (a `turn` index on the scene, advanced by the GM and broadcast with it) and
  were left out because the order is what a table actually reads aloud.
- **End-to-end encryption.** TLS terminates at the relay, so the relay *operator*
  could in principle read traffic; self-hosting the relay is the answer for anyone
  who minds (one command). The stdlib has no symmetric cipher usable across
  Python 3.10–3.13 without a new dependency, which the project forbids.
