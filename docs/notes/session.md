# The session layer

Matters when touching GM mode / online play.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

GM Mode and online play are a self-contained subsystem, split the usual
`ui → core → data` way. The full map is `docs/mm-session-architecture.md`; the
player-facing networking/troubleshooting guide is `docs/mm-session-networking.md`.
The shape:

- `src/mm_companion/core/session/` — **pure Python, no PySide6**: `protocol.py`
  (the wire vocabulary — newline-JSON messages, `PROTOCOL_VERSION`, size caps),
  `model.py` (`SessionState`/`PlayerSlot`/`RollRecord`, two token layers),
  `store.py` (`sessions/<id>/session.json` + an appended `rolls.jsonl`), `net.py`
  (`Connection`, the `Transport`/`Listener` ABCs, `TcpTransport`), `server.py`
  (`SessionServer` — accept + reader threads, **the server rolls**, hidden rolls
  never broadcast), `client.py` (`SessionClient`), `discovery.py` (join codes,
  UPnP, `publish_session` → a `Reachability` with verbatim `advice`, and the
  `transports` registry a relay plugs into), `relay.py` (`RelayTransport`
  registered under `mmrelay://` so a relay join code just works).
- **A link is kept warm, and a dropped one comes back.** `Ping`/`Pong` existed
  from the start and nothing sent one, so a table that was merely *roleplayed*
  moved no bytes and the relay reaped it after two minutes (the deployment
  worked around that with a 4 h idle timeout). Now a client's reader thread pings
  after `net.KEEPALIVE_INTERVAL` of silence and **either** end drops a peer after
  `net.PEER_TIMEOUT` — which is also what makes a half-open link visible at all,
  since `recv` otherwise just times out forever and the GM's roster shows a ghost
  as connected. One exchange warms a whole relayed pair (the relay stamps
  `last_active` on writes too), so there is no server heartbeat. `SessionClient`
  then runs a state machine (`STATE_*`, published as `EVENT_STATE`) and redials
  along `RECONNECT_DELAYS` for `RECONNECT_WINDOW`, re-presenting the
  `player_id`/`player_token` it holds so a blip lands back in the same seat.
  **`EVENT_DISCONNECTED` means the session is over**, not that a packet went
  missing — a blip raises `EVENT_STATE` and nothing else, which is what stops a
  two-second Wi-Fi drop tearing the shared roll history out of the Dice block.
  A server that stops says so (`REASON_SESSION_CLOSED`), or a deliberate end and
  a sleeping laptop are indistinguishable. Protocol **v7** exists for this: a v6
  client never pings and would be reaped, so it is refused at the door.
- **A returning player gets their own seat back**, three ways. The client's
  redial carries the token; `JoinSessionDialog.reclaim_ids` resolves a saved seat
  from the *code text* (it used to need a click on a history row nobody knew to
  make); and on a token miss `SessionState.player_by_id_if_free` hands back the
  seat the public `player_id` names — but only an **empty**, non-GM one. The
  trade-off is written out on that method.
- `src/mm_companion/ui/` — Qt: `session_bridge.py` (`SessionBridge`, the **only**
  place core `on_event` callbacks become signals; module-level
  `active_session()`/`live_session()`), `connection_indicator.py`, `gm_window.py`,
  `ui/npc_window.py`, `ui/player_card.py`, `ui/roll_history.py`,
  `ui/scene_card.py`/`ui/scene_board.py`/`ui/card_drop.py`/`ui/card_chips.py`, and
  `session_player.py`/`session_dialogs.py`. `SessionBridge.joined` follows the
  *socket* (False mid-blip, so a send fails honestly); `in_session` follows the
  *session*, and `live_session()` asks that one — asking `joined` meant a roll
  made during a blip rolled locally into the private history, a roll the table
  never saw and the player thought it had. `ConnectionIndicator` is the menu
  bar's corner widget (`setCornerWidget`, installed by `MainWindow` and
  `GMWindow`), and it exists because every other disconnect cue **fades** after
  ten seconds; it recomputes its whole state from the bridge on every signal
  rather than mapping signals to states one by one.
- **The two cards are one card.** A player card and an NPC card differ in what
  they may *do* (a player's is a remote snapshot the GM can only ask to change;
  an NPC's is the GM's own model, edited in place) but not in what a GM *reads*:
  both open their sheet from the **portrait** alone and both hover the same
  abilities/resistances/powers summary out of `ui/card_summary.py`. Opening used
  to be a click anywhere on an NPC card, which fought that card's own
  drag-to-reorder gesture — the two were told apart only by how far the pointer
  had moved.
- **Pinned parameters** are the strip down a card's right side: the four or five
  numbers *this* GM wants off *this* creature. `core/rules/pins.py` is the model
  — a `PinRef` names an ability, resistance, skill, initiative, defence DC or one
  of a power's rolls, and `resolve_pin` turns it into a caption, a reading and a
  `RollSpec`. **A reference, never a number**: the character underneath is live,
  so a frozen value would be right once.
- Three things a pin's *reading* has to get right. Values come off the **roll**
  builders (`resistance_roll`, not `resistance_total`), which folds condition
  overlays in for free while the build math stays condition-free — and
  `with_conditions=False` takes them back out again for the picker, which is a
  catalogue of the creature rather than a combat readout (the `RollSpec` is the
  same either way, so nothing about what a chip *rolls* moves). A **defence DC**
  is its own kind rather than a dressed-up resistance: the sheet's table shows
  the rank, and a chip quietly showing ten more would be a trap. And a pin that
  no longer resolves reads as a dash rather than vanishing — a chip that
  disappears leaves no way to remove it — which is why `PinnedValue.missing` is a
  field of its own and not `spec is None`: a **forced save** carries no spec
  either (the wielder never rolls their own target's save; it reaches the person
  who does as the attack's follow-up chip) but is perfectly well resolved.
- `ui/pin_panel.py` is the strip: **click loads** into the GM's roller and
  **double-click rolls** — the sheet's own bargain — plus drag to reorder and
  right-click to remove. A click is a release with *no* drag started and *no*
  double-click just handled, which is what keeps the four apart.
  `ui/pin_picker.py` is the modeless browser the "+" opens; it unpins as well as
  pins, and its `set_pinned` is a no-op when it already agrees — without that
  guard the card's echo rebuilds the tree *during* a toggle, deleting the row
  being restated. The chips sit in a `_ChipScroll` whose height is **set, never
  asked for** (`PinPanel._apply_cap` works it out from a real chip's height and
  `set_max_visible`, which a collapsed card caps at four): a scroll area reports a
  size hint unrelated to its content *and* is elastic in a panel that ends with a
  stretch, so left to negotiate the two split the room and a rebuilt strip
  collapsed to nothing with its chips laid out inside it, invisible. The drop
  coordinates are mapped through the scrolled host (`_drop_index`,
  `_indicator_rect`) — chip geometries are the host's, drop events are the
  panel's, and the indicator is the panel's child.
- **An NPC card collapses** (`ui/npc_card.py`). Expanded it is a good roster entry
  and a bad combat readout — a 96px portrait, a PL and two buttons, times a dozen
  mooks, is two cards on screen. Collapsed it keeps only what a GM reads mid-round:
  a thumbnail (still the *only* thing that opens the sheet), the name, an
  initiative badge, the pinned strip, the conditions and the damage row. Width is
  unchanged in both states, so cards stay column-aligned in the wrapping flow — the
  win is height. Three things worth knowing. The two name labels share **one slot**,
  exactly one showing: collapsed it elides (a wrapped name would need a second line
  the thumbnail's row height hasn't got), expanded it wraps, and giving the wrapped
  one a row of its own left a near-empty strip above the card's own name. The
  **initiative badge is a `QLabel`**, not a `QToolButton`, for the reason
  `PortraitButton` is one — a tool button wraps a word in forty pixels of chrome —
  and it swallows its press so clicking it can't start the card's drag-to-reorder.
  It is also the *only* control for initiative: left-click rolls, right-click
  clears (`initiativeCleared` → `_on_npc_initiative_cleared`, the twin of the roll
  handler), and the explicit "Initiative" button is gone rather than exist on one
  state only. And `set_collapsed` is **silent** like `PinPanel.set_pins`; only the
  caret emits `collapsedChanged`, since the owner telling a card what it already
  decided must not have the window save what it just read.
- **Right-click means "take that away"**, and the specific answer always wins over
  the general one. A condition chip sheds its condition, the initiative badge
  clears its roll, and the card itself offers Remove/Delete — so the first two
  **consume** the event rather than letting it reach the third. The chips' gesture
  is `widgets.attach_context_removal`, an event filter on a `QObject` parented to
  the chip (so it dies with it), used by both GM cards' `_ConditionChip` *and* the
  character sheet's `ConditionsSection` chips: one gesture wherever a chip appears.
  It replaced a visible `×`, which is the trade — a third of the width of a caption
  like "Hit ×3" back, at the cost of an affordance you cannot see, so the helper
  writes "Right-click to remove" into the tooltip. A chip that *cannot* be removed
  (an offline player's) still swallows the click: falling through to the card's
  "Remove player" is not what someone aiming at a chip asked for.
- The NPC block's header carries **one** Collapse all / Expand all button, whose
  caption is the action it will take — which makes it a readout of the board too.
  Anything still open means "collapse"; only a wholly shut board offers to expand.
  It tells each card **silently** and writes the whole decision once
  (`_toggle_collapse_all`), and `_refresh_collapse_all` restates it from the board
  after any change, since a caption that lies is worse than no button.
- A card's **hover summary sits on the name**, not on the card. A tooltip on the
  card fires wherever the pointer rests, so it landed over the pinned chip or the
  degree button a GM was lining up. The wrapped name takes it directly; the
  collapsed card's `ElidingLabel` takes it through `set_hover_text`, because that
  label owns its own tooltip (it shows the full caption there when the caption is
  clipped) and a plain `setToolTip` would be wiped by its next resize.
- Which cards are shrunk persists in `gm_collapsed`, keyed like `gm_pins`
  (`npc:<file name>`) and read through **`storage.gm_collapsed_cards()`** — never
  off `load_settings()`, for the reason spelled out on `gm_default_pins`. Only the
  shrunk ones are stored, so absent means expanded. It is also held on `_NpcEntry`,
  because `_refresh_npcs` destroys and rebuilds every card and anything kept on the
  widget is lost the first time an initiative is rolled. Unlike the sheet's lock and
  compact mode — deliberately *not* persisted — this is a standing judgement about
  one creature rather than one window's current view: the mooks stay shrunk and the
  villain stays open. A copy inherits it (the fourth guard wants the third guard's
  card); only a deletion forgets it.
- A GM also pins from the sheet opened off that card, by right-clicking a row.
  Two bus topics, `pin-requested` and `unpin-requested`, both served by the
  **sheet** rather than any block, since a pin's destination is outside the sheet
  entirely. Which of the two a row offers comes from `stat_table.PinMenuState`,
  fed by `CharacterSheet.set_pinned` — pushed from the card on open *and* on
  every change, so a sheet left open never offers to pin what is already there.
  The menu appears at all only once `MainWindow(pin_target=True)` says there is a
  card.
- Strips persist per card in `gm_pins`, seeded from `gm_default_pins` — the
  settings key the Settings window's **GM Mode page** edits, and the reason the NPC
  damage default is *late-bound* ("the first Damage power", resolving to the
  **attack roll** it makes — the save it forces belongs to the target), since the
  defaults are written long before the NPC is. Read it through
  **`storage.gm_default_pins()`**,
  never off `load_settings()`: that returns the settings file *verbatim* and does
  not merge `DEFAULT_SETTINGS`, so any key added after a workspace was created
  reads back as `None`. Every setting in that module has an accessor or an inline
  fallback for this reason; a new one needs the same or it is silently dead for
  every existing user. An **empty strip is persisted** rather than dropped to keep
  the file tidy: a missing key is what seeds the defaults, so skipping the empty
  ones handed a GM back the four chips they had just taken off.
- That page (`ui/settings/gm_page.py`, reached from the GM window's own
  `Settings ▸ Preferences…`) is two reorderable lists, one per card kind, and its
  shape follows from *when* a default is written. A default cannot name a power —
  a `Power.id` belongs to one character — so the picker it opens is the
  character-free `default_pin_choices(game_data)` rather than `available_pins`,
  offering the traits plus the one late-bound `select="first_damage"` row; the same
  `PinPickerDialog` serves both, with a `None` character putting it in that mode and
  hiding its "Now" column. Writing goes through `storage.set_gm_default_pins`, which
  merges per kind for the same reason the reader does, and the page always writes
  **both** kinds — an omitted kind reads back as the shipped strip, while an
  explicit `[]` is honoured. Editing the defaults deliberately leaves the board
  alone (`_pins_for` made each card's strip its own on first sight), so
  **Apply to cards on the board** is a separate, confirmed button:
  `storage.clear_gm_card_pins()` plus `GMWindow.reseed_pins_from_defaults()` on
  every open GM window — in that order, or a window still holding its old strips
  writes them back out and undoes the clear.
- **The scene is the one thing the whole table sees**, and everything about its
  shape follows from that. `core/session/model.py` holds three fields, not one:
  `scene` is public, `scene_sources` (an entry's opaque `ref` → `"npc:<file>"` /
  `"player:<id>"`) goes back to the **GM seat alone** like `npc_paths` and for the
  same reason, and `scene_portraits` is kept apart because it is not re-sent with
  the board. The GM **authors** it — `SetScene` carries `slot.is_gm` — and the
  server only stores and rebroadcasts, since `core/session/` may not import
  `core.rules` and has no idea what a condition id means.
- **The pictures travel apart from the board.** A scene is re-sent whole every time
  anything on it changes (a condition, an initiative, a drag, a join), and carrying
  a dozen thumbnails each time is the one thing that could make a relayed table
  expensive — a dozen in *one* message would blow `MAX_MESSAGE_BYTES` outright. So
  a portrait goes once as its entry joins, and the server follows each `Welcome`
  with one `ScenePortrait` per stored picture: N small messages cannot aggregate
  past the cap the way one large one can. They are 96px and capped at 8 KiB, much
  smaller than a sheet portrait, because a board's worth is stored per session and
  replayed to every joiner. `SceneBoard` decodes each **once** and keeps it across
  rebuilds, or ticking a condition would re-decode a dozen JPEGs.
- **A ref is opaque, and that is the point.** It is the only part of an entry that
  reaches a player, and an NPC's file name can be a spoiler outright
  ("TheTraitorIsMarcus.json"). `_SceneEntry` on the GM window is what maps it back;
  the public payload carries a name, an initiative and the conditions, **and
  nothing else**. The guarantee is not a rule the card keeps — it is that
  `sanitize_scene` carries nothing else, so a card cannot show what never left the
  GM's machine.
- **The payload is derived on every push, never kept.** Every field on it lives
  somewhere else and is live there — an NPC's conditions on its model, a player's
  in their last snapshot — so a copy would be right exactly once. What *is* kept is
  only what cannot be derived: which creature a place on the board belongs to.
- **Initiative is one number with one owner.** An NPC's stays on its `_NpcEntry`,
  where the card badge already reads it, and the board reads the same field — the
  two cannot come to disagree. A player's arrives on the shared roll log rather
  than in a message of its own: every roll carries the `RollSpec` describing it, so
  the GM window watches `rollAdded` for `spec.kind == "initiative"`. That needed no
  protocol work and catches both routes at once — answering the request card, and
  rolling Initiative off one's own sheet. `RollRecord.to_dict()` writes the parts
  and not the sum. **Roll initiative** keeps the NPC rolls local, as the badge's own
  docstring insists: a dozen mook rolls would bury the line the table is waiting
  for, and what the players need is the result, which is the board.
- **The scene orders itself exactly as the NPC grid does** (`order_scene`): rolled
  entries by initiative descending, then the un-rolled ones in the GM's arrangement.
  Deliberately the same rule, so a GM is not holding two orderings in their head —
  and a drop clears the dragged entry's initiative, plus a rolled neighbour it was
  put in front of, for the reason `_reorder_npc` gives.
- **The reorder gesture had to become a real `QDrag`.** The NPC card used to track
  the pointer itself and emit a preview for the window to draw, which is a fine
  gesture inside one container and cannot leave it: nothing crosses a widget
  boundary, so no other block can know a drag is happening. `ui/card_drop.py` is the
  drop target all three boards share — one MIME, one `(ref, index)` answer — and a
  ref the board already holds is a *move* while one it does not is an *add*, which
  is how a single handler serves both gestures. The bar showing where a card will
  land is now drawn by the container it will land in, which is the only way it can
  be right about a flow the card is not in.
- **Players join the board by themselves**, unless `gm_scene_auto_players` says
  otherwise (Settings ▸ GM Mode; read through **`storage.gm_scene_auto_players()`**,
  never off `load_settings()`, for the reason spelled out on `gm_default_pins` — a
  key added after a workspace was created reads back as `None`, which is falsy, and
  would have turned the default off for every existing GM). With it off the player
  cards grow the same 👁 an NPC card has; with it on that eye is hidden, since an
  action that does nothing is worse than none.
- **The player's Scene block is pinned**, beside the roller and for the roller's
  reason: the strip is the one region that does not scroll with the page, and a
  turn order that has scrolled away is no use in the round it matters. It states no
  `min_height` — at 120px the default arrangement wanted more vertical room than a
  small laptop has, and the strip answered by growing the scrollbar it exists to
  avoid. Along a **bottom** strip the two pinned lines split its length rather than
  stacking, so the roller reflows into less than the row of four it manages with the
  bar to itself: the honest cost of a second pinned block, one drag from undone.
  Like the Dice block it publishes and subscribes **nothing** — a scene arriving
  mid-edit must never mark the sheet dirty — and it follows `live_session()` rather
  than the socket, so a two-second blip does not empty the board.
- Adding a block invalidates a stored arrangement (the canvas requires every known
  block to appear exactly once), so shipping this reset every saved `layout` and
  `gm_layout` once. That is also what puts the block in the strip.
- `src/mm_companion/server/` and `src/mm_companion/relay/` — the two Qt-free,
  stdlib-only entrypoints (`python -m mm_companion.server` / `.relay`), each a
  thin `cli.py` around the core session server / a `selectors` byte-pump.
  `server/hub.py` is the **session hub** (`--hub`): every session in the
  workspace hosted at once, so a table outlives the GM's laptop.
- **Sessions can live on a server**, and that cost almost nothing because a relay
  join code *already* carries the session id
  (`mmrelay://host:port/<session-id>`): each session on the hub registers by
  dialling out to a relay exactly as a GM's app does — no inbound port, no new
  transport, no join-code change, and a player cannot tell the difference.
- **Creating a session needs no credential** — the server is a public utility and
  anyone who installs the app can host on it. What that costs is a rule about
  *ownership*, and it is the whole design: **creating is open, everything else
  needs the session's own `gm_token`**, handed back by the create and held by
  nobody else. Three secrets, and keeping them straight matters: the
  **`host_token`** is in the join code and everyone at the table has it; the
  **`gm_token`** claims the GM's *seat* and owns the session (a wrong one is
  **refused**, never quietly downgraded to a player — that failure "works" right
  until a hidden roll reaches the table); the **operator secret** in the server's
  `/etc` is the caretaker's override, granting the full list and deleting
  anything. Two rules fall out: **there is no way to list other people's
  sessions** (a GM's own live in `session_my_sessions` in *their app*, because a
  server-side list would hand every join code to whoever asked), and **a wrong
  token and an unknown id give the identical refusal**, or the endpoint becomes an
  oracle for which ids exist. A public box also needs brakes: a global ceiling, a
  per-connection create limit, and a sweep of sessions untouched for 30 days
  (`updated_at` moves on every join/roll/rename, so a live campaign is safe).
  A remote GM needed
  three things a local one got free: player sheets (the roster carries no
  characters, so `PlayerSnapshot` forwards each to the GM seat alone), the result
  of their own hidden roll (never broadcast), and kick/rename/cast (no wire
  message existed). `core/session/hub_client.py` is the app's side, deliberately
  unlike `client.py`: no reader thread, no events — connect, ask, read, close.
  It also carries `DEFAULT_SERVER`, the address a fresh install points at so
  someone can host without knowing anyone; it is a default, not a hardcoding.
- An idle session stays registered and joinable but sheds its roll history after
  ten minutes. The reload hangs on `SessionServer`'s `on_activate`, called
  **before** the handshake, not after: the `Welcome` carries the history, and
  sequence numbers come from the tail of that list, so reloading late would
  restart the numbering and corrupt the log.
- Deployment lives in `deploy/` (systemd units, `deploy.sh`, runbook) — tracked
  and secret-free. The operator's addresses and key paths are in a gitignored
  `SERVER.md`.
- Standing constraints: session networking stays **Qt-free and
  headless-testable** (Qt only in `ui/`), **no new dependencies** (PySide6 + the
  stdlib), and **nothing new under `data/`** — the session layer is MIT code, not
  OGL content. Verify with the fast, window-free files (`tests/test_session_*.py`,
  `tests/test_headless_server.py`); the GUI ones need `QT_QPA_PLATFORM=offscreen`.
