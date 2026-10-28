# NPC groups (squads)

Matters when touching the GM's NPC board, Quick NPC, Copy, or anything that treats
several NPCs as one.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## What a group is

A name and an ordered list of NPC **file names** (`core/npc_groups.py`, `NPCGroup`).
It is the GM's **board arrangement**, not table state and not a rule: nothing rolls or
resists as a group yet (see "Group rolls" below).

- **Stored per GM**, in the workspace settings beside the collapsed cards:
  `storage.gm_npc_groups()` / `set_gm_npc_groups()`, key `gm_npc_groups`. Chosen over
  per-session storage (2026-10-28, the user's call): no protocol change, and a squad
  follows its creatures into every session they are cast in.
- **Invariants** the module keeps: an NPC is in at most one group; a stored group has
  two members or more (one that falls to one dissolves); a board shows a group only
  when two of its members are in *that* cast (`shown_groups`), and leaves the stored
  group alone otherwise.
- All the operations (`join`, `leave`, `dissolve`, `rename`, `numbered_names`,
  `base_name`) are pure and return new lists; `tests/test_npc_groups.py` covers them.

## How groups are made

- **Quick NPC × N.** The dialog's **Make** box (1–`SQUAD_MAX`, remembered for the
  run). `GMWindow._make_squad` names them "Thug 1…N" (skipping names in the cast),
  writes every file, changes the cast once and the board once, and groups them. Same
  numbers for each — what the dialog showed. Not put on the Scene (user's call).
- **Drop one card onto another.** `CardDropFlow(merges=…)` splits a card into zones:
  its middle half is "onto it" (a `DropRegion` wash; `merged(ref, widget)`), its
  edges are the reorder they always were (`dropped(ref, index)`).
  `GMWindow._merge_npcs`: onto a loose card makes a group named after the target's
  base name; onto a grouped card or a group card joins it; onto itself or its own
  group does nothing. A whole group's header drags as `npcgroup:<id>` and merges too.
- **Copy** of a group member joins its group, right after the original.

## How a group is drawn

- `ui/npc_group.py` `NPCGroupCard`: a frame with a header (name, ×N, "2 up · 2 down"
  from `defeat_state`, ☰ menu: Rename, Put all on / Take all off the Scene, Ungroup) and
  one **row** per member.
- A row is an ordinary `NPCCard` built with `row=True`, not a second widget: the same
  signals, menus, tiles, damage row and defeat look, wired by the one
  `GMWindow._build_npc_card`. Row mode lays the pieces along one `FlowLayout` that
  wraps (who / damage row / tiles), is always collapsed (Collapse all passes it by),
  hides its caret, and adds "Leave the group" to its menu.
- A group takes a **line of the board** (`apply_width(strip, room)`); its rows share
  the board's tile width but not the loose cards' body width (`_sync_card_widths`).
- The board's order is still `_manual_order` (file names). A group is drawn where its
  first member stands. Reordering counts **flow items** (a group is one) through
  `_board_blocks` / `_reorder_npcs`, which reduce to the old single-card arithmetic when
  there are no groups.
- A row dropped *between* cards leaves its group; "Leave the group" does the same.
  Deleting an NPC takes it out of its group everywhere; removing it from a session
  does not.

## Not done

- **Group rolls for players.** Team attack, team check and group check are built for
  NPC groups (see [the actions notes](actions.md#team-attack)). The rules modules
  (`team_attack.py`, `group_checks.py`) take plain entries, so a players' team can use
  them; what is missing is a way to gather players' rolls into one.
- A second GM seat does not see the first one's groups (they are per GM).
