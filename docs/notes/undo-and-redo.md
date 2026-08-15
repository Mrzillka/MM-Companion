# Undo and redo

Matters when adding a block, or a field to the model.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

`Ctrl+Z` walks the sheet back, `Ctrl+Shift+Z` / `Ctrl+Y` forward, and two bare
menu-bar buttons `↶ ↷` sit immediately before the 🔒 (which stays the bar's last
entry). Per window and in memory: closing the sheet discards the history.

- **Snapshots, not commands.** ~60 places across eleven blocks write the model, and
  several move more than one field per gesture (a Power Level edit reconciles the
  budget; removing a skill drops its ranks, focuses *and* specializations), so a
  `QUndoCommand` per mutation would be that many inverses to keep right. A step is
  instead a whole `Character.to_dict()`, which means **an undo runs the same code
  path as opening a file** — the property that makes this safe on a sheet this
  large. Three things keep it cheap: a **debounce** (`ui/undo.py`
  `UNDO_COALESCE_MS`, matching the session pusher's, so a typed word is one step),
  a **string compare** (entries are canonical JSON, so a block that emits `changed`
  without changing anything costs nothing — and the text can't share nested
  containers with the live model the way a stored dict would, `to_dict` copying
  only one level deep), and a **depth cap**.
- **`Character.restore(raw)` mutates in place** (`core/character.py`), copying field
  by field over `dataclasses.fields` — dicts cleared and refilled, lists sliced,
  never rebound. `SkillsSection` aliases three of the model's dicts as its own
  attributes, so rebinding one would desync the block silently; driving it off
  `fields()` also carries a field added later for free.
- **Runtime survives a restore.** `capture_runtime`/`apply_runtime` carry the flags
  `to_dict` deliberately omits, which since powers started saving their own runtime
  (see "Runtime is saved" in [The powers layer](powers.md)) is just `EquipmentItem.worn`/`current_speed`,
  recursing into accessories, keyed by item **id**. Without them a plain round trip
  re-wears every stowed item, which is the lesson already written on `equipment.py`'s
  deep-copy comment. A *power's* runtime — `activated`/`item_present`/`array_active`,
  `PowerGroup.active_child_id`, each effect's `toggled_on`/`suppressed`/`current_rank`,
  and an item's own `build` — travels in the snapshot instead, so it is restored with
  the build and a runtime toggle is an ordinary undoable step. That is also why the
  per-effect position-keyed tuple is gone: an effect carries no id, and no longer
  needs one.
- **`CharacterSheet.reseed()` is the widget half**, duck-typed and fanned out like
  `sync_session`, guarded by `_restoring` so the sheet's `EDITED` subscriber drops
  the signal — the one chokepoint that covers all thirteen blocks *and* any mod block
  (six sections carry no `_loading` flag of their own). Two passes, and the split is
  the rule for a new block: a block whose widgets hold model values exposes
  `reseed()`; **anything a topic already restates is left to the topics**, which is
  why Powers and Equipment have none — their `refresh()` *is* the `facts-changed`
  handler, and a method here would rebuild the two costliest card trees twice. The
  fan-out is order-free because the model is restored first and every refresh reads
  it. `bus.RESEED_TOPICS` is every notification topic but `EDITED`, guarded by a
  test so a topic added later cannot be silently left out; `bus.publish_all` fires
  each handler **once** across them (several answer to two topics).
- Two things a `reseed()` must not do: **write the model** (the constructor's
  `focuses.setdefault` stays out of `SkillsSection.reseed`, or the next flush
  records a step nobody took), and **reconcile** — `SystemInfoSection.reseed` never
  calls `_link_pl_pp`, since both values in a restored state are authoritative.
  `AbilitiesSection.reseed` uses `set_stat_value`, never `setValue`: a spin box
  clamps silently, which here would *lose* the rank being restored.
- **A change pushed in from outside the window is absorbed, never recorded** —
  `undo.absorbing(sheet)` around the GM's condition/hero-point commands
  (`session_player.py`) and the damage rung replayed onto an open NPC sheet
  (`gm_window._after_npc_condition_change`, where the card's entry and the sheet are
  two different `Character` objects kept in step by replaying settled ids). Absorb
  commits any pending local edit first, so that stays undoable, and then **replays
  the change onto every state already in the history**: without that, "not
  undoable" would hold only for the next `Ctrl+Z`, and stepping further back
  through one's own edits would quietly shed the GM's condition — and push it back
  to the table. The replay goes one level into a dict, so a hero-point command
  patches `characteristics.hero_points` and leaves a locally-edited Power Level
  alone.
- The window's `*` marker is **re-derived** from `at_saved_state()`, which compares
  content rather than a stack position (a position lies the moment `absorb` moves
  the baseline), so undoing back to what was written to disk really does read clean.
  `_write` flushes *before* the save and marks *after* — `save_character` rewrites
  an external `image_path` to a workspace filename, which is the app tidying up
  rather than a step to walk back through.
- Undo stays available while the sheet is **locked** (conditions and hero points are
  editable there), and a GM's read-only `gm_view` window gets no controller and no
  buttons at all. Each action is added to the **window** as well as the bar, because
  compact mode hides the menu bar and a shortcut is inactive while its widget is.
  `Ctrl+Z` inside a text field still reaches that field's own undo first, which is
  what every other app does.
