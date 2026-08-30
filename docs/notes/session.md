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
- **An NPC card is a cast list entry, not a turn-order one.** It carries no
  initiative and no 👁: a creature reaches the shared board by being *dragged* onto
  the Scene and leaves by its scene card's right-click, and its place in the order
  is read and set there. Both used to live here too, which meant one turn order on
  two boards and — worse — a grid that re-sorted itself under the GM's hands
  mid-round, on the cards they were reaching into. The card's **right-click menu**
  seats it and un-seats it, and that is not a convenience: with the 👁 gone the
  drag was briefly the only way onto the board, which needs a mouse, cannot be
  reached from a keyboard, and is impossible outright when the Scene block is
  hidden from the View menu — a GM could reach a state with no way at all to seat a
  creature. The drag is the quick answer; the menu is the one that always works. `_ordered_npcs` is now the GM's
  arrangement and nothing else, and `_reorder_npc` is a pure arrangement: it must
  not clear a number it no longer displays. The number's *owner* has not moved —
  it is still `_NpcEntry.initiative` — so nothing about the wire changed.
- **A card's width is not its own.** It reports what its contents need
  (`body_width_hint`, `pin_width_hint`) and `GMWindow._sync_card_widths` gives
  every card in a block the widest of those. Both halves matter: measuring is what
  gets a card down from the flat `210 + 150 + spacing` = 372px it used to cost
  regardless of what was on it (a strip pinned to "Dodge 12" spent most of a card
  on white space), and *sharing* is what keeps the cards column-aligned, since the
  wrapping `FlowLayout` lays items out at their own size hint and a card that
  merely fitted its own content would make the grid ragged and re-flow it on every
  pin change. Per block, not across both: the two grids are two flows with no
  columns in common. The pinned strip clamps between `gm.pin-strip.min` and
  `gm.pin-strip.max` — the floor keeps its "+" reachable when nothing is pinned,
  and the cap is the old fixed width, so a long caption elides exactly as it always
  did and nothing is ever *wider* than before. `PinPanel.widthHintChanged` is what
  re-triggers the measurement when a pin comes or goes.
- **An NPC card collapses** (`ui/npc_card.py`). Expanded it is a good roster entry
  and a bad combat readout — a 96px portrait, a PL and two buttons, times a dozen
  mooks, is two cards on screen. Collapsed it keeps only what a GM reads mid-round:
  a thumbnail (still the *only* thing that opens the sheet), the name, the pinned
  strip, the conditions and the damage row. The win is **height**: the portrait is
  in `body_width_hint` and a collapsed card sheds it, so a wholly shut board *can*
  narrow — but with the bundled ruleset the damage ladder is wider than 96px
  anyway, so in practice it does not. Two other things worth knowing. The two name
  labels share **one slot**, exactly one showing: collapsed it elides (a wrapped
  name would need a second line the thumbnail's row height hasn't got), expanded it
  wraps, and giving the wrapped one a row of its own left a near-empty strip above
  the card's own name. And `set_collapsed` is **silent** like `PinPanel.set_pins`;
  only the caret emits `collapsedChanged`, since the owner telling a card what it
  already decided must not have the window save what it just read.
- **Right-click means "take that away"**, and the specific answer always wins over
  the general one. A condition chip sheds its condition, a scene card's badge
  clears its roll, and an NPC card itself offers Remove/Delete — so the first two
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
- **The board comes back.** `scene_sources` is written on every push, persisted
  with the session and handed back in the GM's own `Welcome` — *for this*, exactly
  as `npc_paths` is. It had no reader for a while, and `_SceneEntry.from_wire_source`
  sat unused beside it: a GM who closed the app mid-fight came back to a full cast
  and an empty board, and the first push after that wrote the empty board over the
  stored one, so the fight was not merely forgotten but deleted under any player
  still watching. `_restore_scene` runs once, **after** `_refresh_npcs`, so a
  creature whose file went away is simply not found; it rebuilds in the *public*
  scene's order (that is the list the refs were written in) and brings the rolled
  initiatives with it, since a turn order without its numbers is most of the way to
  no turn order. **NPCs only** — a restored seat would be dropped by the very next
  roster anyway, which is the rule `_sync_scene_players` already keeps.
- **What a creature is to the table is public, and it is the only field on an
  entry that is a *judgement*.** Everything else on the wire is read off a model;
  `disposition` is the GM's answer to a question nothing else can answer, and
  telling friend from foe at a glance is most of what a player needs the board for.
  Four values (`enemy` / `friendly` / `neutral` / `player`), four colour tokens
  (`scene.*`, retuned per preset and editable in Settings ▸ Themes), drawn as the
  card's **edge** — readable at the size a dozen cards are on screen at once, which
  is the one thing a border does better than text. The card says it in words too,
  in its hover text and in the menu that sets it: a fact told only in colour is
  told to fewer people. A seat is always `player` and the GM is offered no way to
  say otherwise, on the card *or* through the signal. An absent or unknown value is
  **enemy**, which is the safe way round rather than the tidy one — a board is
  mostly things to fight, so the mistake the default can make is an ally drawn as a
  threat rather than a threat drawn as an ally.
- **The colours say *what*, a word says *who*.** Every seat wears the same blue, so
  the disposition cannot also answer "which one is me" — and that is the first
  thing a player does with a turn order. Their own entry reads `Nova (you)`, the
  same way a player card already marks the GM's own seat, from
  `bridge.own_player_id()`. A word rather than a fifth colour, which would have to
  compete with the four that already mean something.
- **The field is additive with no protocol bump**, unlike the four bumps before it.
  Each of those prevented an old peer answering *wrongly* — an empty board, a
  request drawn as a d20 that rolled nought, a client reaped for never pinging. An
  old peer here draws the same board correctly, without the colour: a smaller
  readout, not a wrong one, and not worth refusing a table at the door for.
- **A board bigger than the wire says so.** `sanitize_scene` keeps the first
  `MAX_SCENE_ENTRIES` (24) and drops the rest — silently, and by *insertion* order,
  which is not the order the board reads in, so what falls off the end can be a
  creature at the top of the initiative. The GM's own board shows all of it either
  way, so without `_warn_if_scene_is_truncated` the two screens disagree and only
  the players can tell.
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
- **A push that would say nothing does nothing**, and that guard is what makes it
  affordable to call `_push_scene` from everywhere a creature's visible state can
  move. One player editing a stat arrived there *twice* — once from their snapshot
  and once from the roster broadcast the same snapshot triggered — and each time
  redrew the GM's whole board and sent the whole scene to every client, whose board
  redrew in turn. It compares the payload, the GM's arrangement, and whether there is
  a table to send to (so a board built before hosting is still broadcast when hosting
  starts). Two things stay *outside* it. **What the cards are told** — the 👁 on a
  player card and whether an NPC's menu offers to add or remove it — because the
  *cards* can be new when the scene is not: `_refresh_npcs` rebuilds every NPC card
  and a fresh one starts off the board, so behind the guard a cast reloaded mid-fight
  came back claiming nothing was in the scene. And **the portraits**, because a
  picture travels on its own message, appears in no payload, and carries its own
  check. `_scene_portrait_for` caches the encoded thumbnail against the input it was
  made from — the encode is a file read or a base64 decode plus a scale and a JPEG
  re-encode, and it ran for every entry of every push, `_scene_portraits` having
  only ever suppressed the *send*.
- **A board keeps the card already showing a ref.** `SceneBoard._rebuild` restates a
  surviving entry through `SceneCard.set_entry` — which is what that method was
  always for, and which nothing called — and builds only for refs it has not seen;
  `SceneBoard.set_scene` ignores a scene identical to the one on screen outright.
  Two guards rather than one, at the two ends: the GM's guards the *sending*, this
  guards the *receiving*, so a chatty GM (or a mod) still costs a player nothing.
  Reuse also means a card's signals are connected exactly once. Which seat is the
  reader's own is fixed when a card is built, so `set_own_player_id` is the one
  update that drops them all — it is answered once, at the join.
- **Initiative is one number with one owner.** An NPC's stays on its `_NpcEntry`,
  where the card badge already reads it, and the board reads the same field — the
  two cannot come to disagree. A player's arrives on the shared roll log rather
  than in a message of its own: every roll carries the `RollSpec` describing it, so
  the GM window watches `rollAdded` for `spec.kind == "initiative"`. That needed no
  protocol work and catches both routes at once — answering the request card, and
  rolling Initiative off one's own sheet. `RollRecord.to_dict()` writes the parts
  and not the sum. Both **Roll initiative** and the per-entry badge keep the NPC
  rolls local: a dozen mook rolls would bury the line the table is waiting for, and
  what the players need is the result, which is the board.
- **The one control for initiative is the scene card's badge** (`InitiativeBadge`
  in `ui/card_chips.py`). Left-click rolls (`_roll_entry_initiative`), right-click
  clears — the pair belongs on one widget because the number *is* the thing being
  set. It is a `QLabel`, not a `QToolButton`, for the reason `PortraitButton` is
  one (a tool button wraps a word in forty pixels of chrome), and it swallows its
  press so a click can't start the card's drag. It is **live only for a GM, and
  only on an NPC's entry**: a player rolls their own on their own sheet, so a GM's
  click there would be rolling somebody else's die — `set_entry` tells the two
  apart by whether the wire entry carries a `player_id`. Rolling no longer rebuilds
  the NPC grid, which it had to while that grid sorted by initiative and which cost
  every hover and scroll position on the board.
- **The scene is the only board that sorts by initiative** (`order_scene`): rolled
  entries by initiative descending, then the un-rolled ones in the GM's
  arrangement. The NPC grid used to sort the same way, deliberately — and that was
  the mistake: it is one ordering shown twice, and the second copy re-arranged the
  cast under the GM's hands mid-round. A drop here clears the dragged entry's
  initiative, plus a rolled neighbour it was put in front of, because putting an
  un-rolled creature before a rolled one is impossible while the other keeps a
  number to sort by. A drop back on the entry's **own** slot is not a move and
  clears nothing (`_is_same_slot`) — both gaps either side of a card name its own
  place, and without that a GM who nudged a rolled card silently lost the roll.
- **The reorder gesture had to become a real `QDrag`.** The NPC card used to track
  the pointer itself and emit a preview for the window to draw, which is a fine
  gesture inside one container and cannot leave it: nothing crosses a widget
  boundary, so no other block can know a drag is happening. `ui/card_drop.py` is the
  drop target all three boards share — one MIME, one `(ref, index)` answer — and a
  ref the board already holds is a *move* while one it does not is an *add*, which
  is how a single handler serves both gestures. The bar showing where a card will
  land is now drawn by the container it will land in, which is the only way it can
  be right about a flow the card is not in. **A board that will not use a payload
  refuses it out loud** (`CardDropFlow(accepts=…)`): the two rosters took every
  card this app can drag, lit up green and then dropped the wrong ones on the
  floor — an NPC on the Players block, a player on the cast — which reads as a
  broken gesture rather than a refused one, and is the exact thing `DropFeedback`
  exists to stop a target doing.
- **An empty board is still a drop target**, and getting that wrong made the
  *first* drop of every session fail. The empty-state sentence used to be a sibling
  of the flow and the flow was hidden while the board held nothing — so the one
  thing on screen saying "drag one here" was the one widget in the block that could
  not take a drop, and Qt sends no drag events to a hidden widget at all. It is
  `CardDropFlow.set_placeholder` now: a child *of* the flow host, outside the
  `FlowLayout` (a label in the flow would be an item `drop_index` has to count) and
  transparent to the mouse, with `set_minimum_row_height` keeping a band to aim at
  since an empty `FlowLayout` reports `QSize(0, 0)`. The flow also takes the
  board's whole height (`stretch=1`, and the Scene box lost its trailing
  `addStretch`), because what a GM aims a card at is the *block*, not the thin band
  its cards happen to occupy.
- **"New scene" arms itself rather than opening a dialog** (`widgets.ConfirmButton`,
  the app's third answer for a destructive action alongside `QMessageBox.question`
  and right-click-plus-undo). Clearing the board is not reversible and is done
  mid-round, which is the one case a modal is worst for: it stops the table to ask
  something the button can say in its own caption. One click arms it — caption to
  "Confirm?", `tint.worse`, a single-shot timer — and the next within
  `CONFIRM_ARM_MS` goes through; a stray click disarms itself rather than leaving a
  live trigger on the board. The armed look is a *widget-level* stylesheet, since
  `QToolButton:checked` is in the app QSS and push buttons are not.
- **Quick NPC opens nothing.** It used to throw the new mook's full sheet up,
  which is exactly wrong for what the button is for: five numbers is all a mook
  needs, and a GM making five of them wanted five cards, not five windows to close.
  The card lands **collapsed** for the same reason. The sheet is one click on the
  card's portrait away either way.
- **Players join the board by themselves**, unless `gm_scene_auto_players` says
  otherwise (Settings ▸ GM Mode; read through **`storage.gm_scene_auto_players()`**,
  never off `load_settings()`, for the reason spelled out on `gm_default_pins` — a
  key added after a workspace was created reads back as `None`, which is falsy, and
  would have turned the default off for every existing GM). With it off the player
  cards grow a 👁; with it on that eye is hidden, since an action that does nothing
  is worse than none. It is the **only** eye left — an NPC card has none — and it
  survives because it is also the readout for the one case a drag cannot express:
  a seat that has joined the session but is sitting out the fight.
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
- **A mod can talk to the table, and the relay needed nothing for it.** Protocol
  **v10** adds three messages and one `Welcome` field. `SetModState` /
  `ModStateUpdate` carry one **keyed, opaque payload** the GM authors and everyone
  sees; `ModRequest` is the other half — any seat may send it, only the GM
  receives it, and it is neither stored nor broadcast; `ModNote` writes a mod's
  line into the one shared history as a `kind="mod"` record. The server stores and
  rebroadcasts and **never looks inside**, exactly as it treats a `RollSpec`: this
  layer may not import `core.rules`, and the standalone server loads no game data,
  so it could not interpret a payload if it wanted to. The **relay** required no
  change at all — it reads one envelope per connection and forwards every byte
  after that unread — which is its design guarantee holding, not luck.
- **Keyed, where the scene is sent whole**, and the asymmetry is the point. A
  scene is one authority's single picture that changes for half a dozen unrelated
  reasons at once, so sending it whole avoids reconciliation. Mod state is a *bag
  of independent things*: a GM starting one timer must not re-push the other five,
  and two mods must never be able to overwrite each other. A `payload` of `None`
  **deletes** the key and the deletion is broadcast — that is what a share toggle
  turning off looks like on the wire, not a flag saying "ignore this".
- **The caps are an aggregate, and that was a bug first.** The per-entry bounds
  (`MAX_MOD_IDS` × `MAX_MOD_KEYS` × `MAX_MOD_PAYLOAD_CHARS`) multiply out to 2 MiB
  — eight times `MAX_MESSAGE_BYTES` — so a session could accumulate state its own
  `Welcome` could not encode, and the failure would have landed on the *next
  player to join* rather than on whoever filled it. `MAX_MOD_STATE_CHARS` is the
  bound that actually makes the welcome sendable, and it is what a mod is refused
  against; a test asserts it against the message cap so retuning either stays
  honest. Refusing rather than evicting is deliberate: eviction would make a mod's
  state silently lossy, and the mod that lost an entry would not be the one that
  overran the cap.
- **Mod state is entirely public**, unlike `scene_sources` and `npc_paths`. There
  is no GM-only half, because being seen by the table is the whole reason to send
  it — a mod with a secret keeps it off this channel and in
  `storage.local_mod_state` instead. A `mod_id` the receiver has no mod for is
  **kept**, not dropped: the two ends of a table can legitimately load different
  mods (that is what `ERROR_MOD_SKEW` warns about), and a client does not get to
  decide whose state matters.
- **A request is attributed by the server, never by the sender.** `ModRequest`
  carries a `player_id` that the server stamps from the slot, the way a roll's
  attribution works. A field a client could fill in itself would make the whole
  channel an impersonation tool.
- A **mod line in the history is drawn as a note** (`roll_history.py`), and that
  is the design rather than a shortcut: the card has to read sensibly for a mod
  *this* app has never heard of, and a plain sentence attributed to a seat is
  exactly what a note already is. `record_mod_note` is GM-only so a countdown a
  dozen screens are watching says so once.
- **The GM window builds from a registry now** (`ui/blocks/gm_registry.py`). Its
  four panels were a literal list, so a mod could add a block to the character
  sheet and nothing to the board a GM actually runs a fight on. A *second*
  registry rather than a `surfaces` flag on `BlockDescriptor`: a sheet block is
  built from `(GameData, Character)` and joins the sheet's signal bus, while a GM
  panel is built from the **window**, has no character, and has no bus — one
  descriptor would have left half its fields meaningless whichever surface a block
  chose. Sizes still come from `block_sizes.json` under `gm_`-prefixed keys.
  Adding or removing a block invalidates a stored arrangement, so enabling a mod
  that registers one resets `gm_layout` once; the canvas already falls back to
  defaults rather than breaking.
- Deployment lives in `deploy/` (systemd units, `deploy.sh`, runbook) — tracked
  and secret-free. The operator's addresses and key paths are in a gitignored
  `SERVER.md`.
- Standing constraints: session networking stays **Qt-free and
  headless-testable** (Qt only in `ui/`), **no new dependencies** (PySide6 + the
  stdlib), and **nothing new under `data/`** — the session layer is MIT code, not
  OGL content. Verify with the fast, window-free files (`tests/test_session_*.py`,
  `tests/test_headless_server.py`); the GUI ones need `QT_QPA_PLATFORM=offscreen`.
