# Combat maneuvers and the Actions menu — plan

Branch: **`feature/combat-maneuvers`** (off `develop`). Driven by the
`/combat-maneuvers` skill (`.claude/skills/combat-maneuvers/SKILL.md`), one phase per
session. This file is the single source of truth for the feature: the status table,
the decisions, the design, and the progress log at the bottom. **Update it at the end of
every session.**

## Status

| # | Phase | Status |
|---|---|---|
| 0 | Plan and skill | done (2026-11-08) |
| 1 | Temporary conditions in `core` (amount, temporary, tone, stat mods) | done (2026-11-08) |
| 2 | Sheet indicators: dots, tinted chips, "Clear temporary" | not started |
| 3 | Reusable grid popup, action icons, the **Actions** button | not started |
| 4 | Roller rework: one two-way slider, the action lane, the attack picker | not started |
| 5 | The four attack maneuvers, end to end (player) | not started |
| 6 | Self actions: Aim, Charge/Slam, Defend, Recover, Drop Prone, Stand, Finishing, Surprise, Catch | not started |
| 7 | The GM's side: NPC chooser first, conditions on the NPC | not started |
| 8 | Targeted actions I: Feint, Trick, Aid | not started |
| 9 | Targeted actions II: Grab, Trip, Disarm, Smash, Escape, Block | not started |
| 10 | Notes, debts, full test run, hand-off for merge | not started |

Status values: `not started`, `in progress (<what is left>)`, `done (<date>)`.

---

## 1. What the user asked for (2026-11-08)

- Combat maneuvers and the other combat actions, chosen from one **Actions** button in
  the roller, which replaces today's Action row (Reroll, Impress, Area).
- **Actions** opens a large popup: titled sections ("Attack Maneuvers", "Actions", …),
  each a **square grid of tiles**, each tile a simple SVG icon over its name. Not a
  native `QMenu` (it can't do a grid). The popup is built as a **reusable widget**, to be
  used again for other things.
- A **configurable** action (All-Out Attack +1..+5) adds a line under Bonus/Penalty: its
  name, an ✕ to cancel, and a slider. The slider **starts at the largest value allowed**.
- Bonus and Penalty become **one two-way slider**, centred on 0. Under it there is an
  **action lane** that keeps its height **even when empty**, so picking an action doesn't
  resize the roller.
- Penalties and bonuses that outlast the roll (All-Out's −Defense) become **temporary
  conditions**, red or green, and every stat they touch gets a **small red or green
  dot** beside it. There is no turn clock (far future), so the player or the GM removes
  them at the start of the creature's next turn.
- Target layout:

```
-----------------------------------------
| Bonus/Penalty      -----|----- [+0▲▼] |
| All-Out Attack x   ----------| [+5▲▼] |
| [*] DC [15▲▼]  | [] Routine (take 10) |
| Request  [Trait▼]     [DC▲▼]    Ask   |
|                Actions                |
-----------------------------------------

-----------------------------------------
|  Attack Maneuvers                     |
|   icon     icon     icon      icon    |
| Accurate  All-Out  Defensive  Power   |
|  Actions                              |
|   icon     icon     icon      icon    |
|   Aid      Aim     Attack     Block   |
|                ...                    |
```

### Decisions (the user's calls — do not re-litigate)

1. **Follow the book.** All-Out Attack = +attack check / −Defense (the user's first
   example had it on effect rank, which is Power Attack). A lasting penalty goes on
   when the attack is **rolled**, hit or miss.
2. **Only lasting modifiers become conditions.** Accurate and Power change only the
   attack being made (its check, and the DC/rank of the save it forces): no condition.
   All-Out → red "All-Out Attack −X" (Defense −X). Defensive → green "Defensive Attack
   +X" (Defense +X, attack checks −X until the next turn). A chip's colour is its main
   effect. Each stat's dot follows that stat's own sign.
3. **An attack maneuver needs an attack loaded.** Picking one with no attack on the chip
   opens a **simple context menu (plain `QMenu`)** listing the character's attacks. The
   one picked loads onto the chip. Removing the chip greys the maneuver line out (it
   stays). Loading another attack re-enables it, recomputes the limits, and resets the
   value to the new maximum. The slider runs `0..min(5, limits)`.
4. **Scope is all of it:** the maneuvers, the self actions, the targeted actions
   (opposed checks, conditions on someone else), and the existing actions (Impress,
   Area, Team attack, Team/Group check, Reroll) moved into the menu.
5. **Dots** go wherever the stat is shown: Resistances (DEF), System readouts, Skills,
   power-card attack roll lines (for attack-check mods), and the simple sheet. **GM cards
   show the chip only.**
6. **The GM** gets the same Actions button on the Rolls block. Picking an action first
   asks **which NPC is acting**, through the existing NPC-choosing context menu (the
   pattern in `ui/gm/rolls.py::_on_history_roll`). Everything after that works as for a
   player, and conditions land on that NPC.
7. **The slider** runs from −20 to +20, centred, and its spin box shows the sign. Extra
   Effort's +2 lands on it. A maneuver line is **spent by the roll** like the slider.
   Its condition stays. With no action picked, the lane is **empty reserved space**.
8. **Clearing:** an ✕ on each chip, plus a **"Clear temporary"** button on the
   Conditions block and on the GM's cards, **shown only while a temporary condition
   exists**. The GM's **End turn** does *not* clear them (they last until the *start* of
   the creature's next turn).
9. Plan and skill live **in the repo**, on the feature branch.
10. A **protocol bump** is expected and fine (memory: "ask before avoiding a protocol
    bump"). **Bump once for the whole feature.** If this branch has already bumped,
    reuse that version.

---

## 2. The rules (verified in the 4e book, *Origin Edition*, ch. 8)

Read with the `rulebook` skill (`reference/core-book/p0202.txt`–`p0207.txt` = printed
p200–205). Paraphrased for implementation; never copy the book's prose into `data/` or
`docs/`.

### Attack maneuvers (p200)

Each one shifts **X, 0 ≤ X ≤ 5**, from one value to another.

| id | bonus +X | penalty −X | lasting part |
|---|---|---|---|
| `accurate_attack` | attack check | effect rank | — |
| `all_out_attack` | attack check | Defense | Defense −X |
| `defensive_attack` | Defense | attack check | Defense +X, attacks −X |
| `power_attack` | effect rank | attack check | — |

- **The same two caps for all four:** the penalised value can't go below 0, so
  X ≤ its current value. The bonused value can't more than double, so X ≤ its
  current value. `X_max = min(5, penalised value, bonused value)`. If that's ≤ 0, the
  tile is disabled with the reason in its tooltip.
- "Attack check" means the loaded attack's **attack bonus**. "Effect rank" means the
  rank the attack forces a save against (the follow-up's rank). "Defense" means the
  character's **Defense rank** (`trait_keys.defense`, `rules.derived.defense_class` =
  base + rank). Read every one through `core.rules`, condition-adjusted, like the sheet.
- Modifications last **until the start of your next turn** (p201). A maneuver can't
  change a value the attack doesn't use, so an attack with no attack check (Perception
  range) can't take a maneuver that changes the attack check. The tile is disabled and
  the attack picker leaves such attacks out.
- Maneuvers combine, and opposite ones cancel (Accurate + Power, All-Out + Defensive).
  **Several lines may be active at once.** The caps are checked against the combined
  totals.
- **The effect-rank shift moves the save.** The save DC is base + rank, so ±X on the
  rank is ±X on the follow-up's DC **and** on its `SpecLadder.rank` (Impervious and the
  other `damage_defenses` measure against it). Every `also` follow-up (Linked) shifts
  the same way.

### Other maneuvers (p201)

- **Finishing Attack:** against a Defenseless target, roll the attack normally against
  DC 10 instead of hitting automatically. A hit is a **critical**. With intent to kill,
  three or more degrees of failure on the damage save → Dead.
- **Surprise Attack:** the target is Vulnerable (Defenseless if completely surprised).
  It's the GM's call, so the tile applies the condition to a picked target.
- **Slam Attack** (part of Charge, p202): Damage = max(normal Damage + 1, speed rank),
  +1 more after a full-speed move. The attacker resists the same Damage with Resistance.

### Actions (p200, p202–205)

| Action | Kind | What the app does |
|---|---|---|
| Aid | targeted (ally + opponent) | attack vs DC 10. 1 degree: the ally gets green **Aided +2** (attack *or* Defense vs that opponent, until the end of the ally's next turn). 3+ degrees: +5 |
| Aim | self | the next attack rolls twice and keeps the better (`KIND_BEST_OF` exists). The line has no slider |
| Block | self, reaction | green **Blocking** (until next turn). Rolling a block = close attack bonus vs the attacker's result (in the DC box). Each block after the first in a round is −2 more (the condition's `count`) |
| Catch | self | Agility vs DC 10 loader |
| Charge | self | this attack −2. Option: **Slam** (rank as above, then a self-Toughness save with Resistance) |
| Defend | self | green **Defending** +2 Defense, temporary. No roll |
| Disarm | targeted | attack −2 vs Defense. Then the attack's Damage rank vs the target's Strength rank (opposed). A win: the target drops the item (narrated) |
| Drop Prone | self | apply Prone (not temporary — it lasts until Stand) |
| Escape | self | Strength *or* Sleight of Hand vs the grab DC (DC box). 1 degree ends the hold (removes Restrained/Bound from self). 2+: also move |
| Feint | targeted | attack, Deception or Insight, vs the highest of the target's attack bonus, Deception or Insight. Success: the target is **Vulnerable** (temporary, against your next attack, until the end of your next turn). As a simple action: −5 |
| Grab | targeted | close attack vs Defense. Then Strength vs the target's Strength or Dodge. 1 degree: **Restrained**. 2+: **Bound**. The grabber is Hindered + Vulnerable while holding (temporary, cleared on release) |
| Impress | targeted | exists (`core.rules.impress`) — moved into the menu |
| Recover | self | green **Recovering** +2 Defense, temporary, plus the existing recovery roll for the most severe damage condition (`core.rules.recovery`). Once per scene (noted on the chip tooltip, not enforced) |
| Smash | targeted | attack −2 (−5 against a hand-held object) vs Defense. A hit: Damage to the object (narrated) |
| Stand | self | DC 20 Acrobatics *or* speed-rank check, **or** accept **Hindered** (temporary) — either way Prone comes off |
| Trick | targeted | Deception vs the target's Deception or Insight. A win: the target is Unaware (temporary). 2+ degrees of failure: the opponent gets +5 against you (green on *them*) |
| Trip | targeted | close attack vs Defense. Then close attack bonus *or* Strength vs the target's Strength or Dodge. A win: **Prone** |
| Area / Team attack / Team check / Group check | existing | moved into the menu (Team ones are GM-only, from a group) |
| Reroll | dice | moved into the menu |

**Left out on purpose** (no mechanic to automate): Command, Concentrate, Move, Observe,
Ready, Sustain, Use Effect (that's a power card's own roll). Record any change of mind
here.

---

## 3. Design

Read before designing anything: `docs/notes/actions.md`, `conditions-and-damage.md`,
`dice-and-rolling.md`, `theme.md`, `ui-utilities.md`, `session.md`, `testing.md`, and
for the GM `minions.md` and `npc-groups.md`. **The standing rules in `CLAUDE.md` apply
in full**: no rules content in Python, theme tokens only, widgets never compute game
numbers, `core` has no Qt, no minimum sizes decided by content, never show a parentless
widget, log failures.

### 3.1 Data (`src/mm_companion/data/`, OGL content: paraphrase and record provenance)

- **`actions.json` gains a `combat` section** (keep `impress` and `area` as they are):
  - `menu`: the ordered sections of the popup — `{id, title, items: [actionId…]}`. Each
    item names an action defined below, or an existing one (`impress`, `area`,
    `team_attack`, `team_check`, `group_check`, `reroll`).
  - `maneuvers.limits`: `{maxShift: 5, floor: 0, doubleCap: true}` — the two caps as
    data rather than Python.
  - `actions`: one record per action: `id`, `name`, `icon` (a key), `tooltip`, `kind`
    (`maneuver`, `self`, `targeted`, `loader`), `needsAttack` (bool), `gmOnly`, and per
    kind:
    - `maneuver`: `bonus` / `penalty` naming a **target** (`attack_check`,
      `effect_rank`, `defense`), and `lasting` (the condition id applied with amount X,
      or none);
    - `self`: `fixedModifier` (Charge −2), `rollKind` (`best_of` for Aim), `condition`
      (+ amount), `check` (a trait key + DC for Catch/Stand), `options` (Slam, Stand's
      "accept Hindered");
    - `targeted`: `check` (the actor's choices), `opposedBy` (the target's choices, or
      `highest of`), `stages` (attack vs Defense, then opposed), and `outcomes` (a
      ladder, same shape as an impression's `outcomes`, conditions + `temporary`
      flags).
  - Shape these as close to the existing `impress` / `area` records as the actions allow.
    `core.rules.impress` and `core.rules.targeting` already interpret a ladder
    generically. **Reuse, don't fork.**
- **`conditions.json` gains the temporary conditions:** `all_out_attack`,
  `defensive_attack`, `defending`, `recovering`, `aided`, `blocking` (stacking, for the
  −2 per extra block), `trick_advantage` (Trick's +5 against the trickster). As built,
  `aided` takes a parameter (`Attack` / `Defense`, which scopes its `amountMods`), and
  `aided` and `trick_advantage` are **not** temporary (they last to the end of a turn;
  see §5). A record-level `temporary: true` makes the others temporary from anywhere,
  the "+" menu included. Each one has:
  - `tone`: `"benefit"` or `"hindrance"` (the chip colour). The default for every
    existing record is hindrance, so nothing old changes colour;
  - `amountMods`: a list of `{trait: "DEF" | "attack_check" | …, sign: +1 | −1}`,
    multiplied by the applied instance's **amount**. A fixed one (`defending` +2) gives
    the amount in the record (`defaultAmount: 2`);
  - `group: "maneuver"` (a new `_meta.conditionGroups` entry, so the "+" menu offers
    them too), and `recovery` text.
- **Icons are UI assets, not game data:** `src/mm_companion/ui/assets/actions/<key>.svg`,
  one per `icon` key, plus `generic.svg` as the fallback (so a mod's action with an
  unknown key still draws). Single-colour line art on a 24×24 viewBox, drawn with
  `currentColor`/one fill that `svg_assets` recolours from a theme token. They are MIT
  code art, made here, never copied from anywhere.

### 3.2 Core (`src/mm_companion/core/`, pure Python)

- **`AppliedCondition`** gains `amount: int | None = None` and `temporary: bool = False`.
  Both are left out of `to_dict` at their defaults, so old saves load unchanged.
  `apply_condition(..., amount=, temporary=)`. A temporary instance of an existing
  condition (Feint's Vulnerable, Stand's Hindered) is the same record with
  `temporary=True`. The display name folds the amount in ("All-Out Attack −3").
  Re-applying the same maneuver **replaces** its amount (one All-Out per turn). It
  doesn't stack, except for `stacking` records.
- **`core/rules/conditions.py`:**
  - `condition_amount_mods(character, data) -> dict[str, int]`: the summed `amountMods`
    per trait key;
  - `temporary_marks(character, data) -> dict[str, Mark]`: per trait key, the sign
    (`better` / `worse`) and the names of the temporary conditions behind it, which is
    what the dots read;
  - `clear_temporary(character) -> list[AppliedCondition]`: removes every temporary
    instance (provenance members go too) and returns what went, for undo and the note.
  - Fold the DEF mods into the **displayed** Defense and Defense Class (the same
    display-only overlay as `condition_defense_mods`). Fold the `attack_check` mods into
    the attack modifier `power_rolls` builds. The build math stays condition-free.
  - **As built (phase 1):** `amount_contributions` is the one walk the others share;
    `condition_amount_mods`, `condition_attack_check_mod` (what `rolls.py` adds to an
    attack check, flagged by `EffectRollNumbers.attack_check`, Linked included),
    `temporary_marks` → `TemporaryMark(tone, delta, sources)` with `MARK_BETTER` /
    `MARK_WORSE`, `clear_temporary`, `has_temporary`, `applied_condition_name` (the shown
    name, moved into `core`; the UI's `condition_display_name` delegates), and
    `derived.defense_class_in_play` (the pin's Defense Class; `defense_class` stays the
    build's). The attack-check key is data: `system.json` `trait_keys.attack_check`.
    Note `condition_attack_mods` (Prone's ±5) was **never** wired into `power_rolls`,
    contrary to what this plan first said, and still isn't (out of scope).
- **`core/rules/maneuvers.py`** (new):
  - `maneuver_limits(action, spec, character, data) -> int`: `X_max` per §2, reading
    the attack bonus off the spec, the effect rank off its follow-up's ladder/DC, and
    Defense off the character. 0 means "not available", and the function also returns
    the reason as text;
  - `apply_maneuvers(spec, picks, data) -> RollSpec`: the attack spec with the
    modifier and every follow-up's DC and ladder rank shifted, plus a `maneuvers`
    caption (`"All-Out Attack +3"`) for the history. Several picks combine;
  - `lasting_conditions(picks, data) -> list[(condition_id, amount)]`: what to put on
    the actor once the roll is made;
  - also the self-action builders: `aim_spec`, `charge_spec`, `slam_spec`,
    `finishing_spec`, `stand_options`.
- **`RollSpec`** may need `maneuvers: tuple[str, …]` (the history caption) and
  `finishing: bool` (a hit is a critical). If either crosses the wire, add it to
  `protocol.sanitize_spec`'s whitelist and **bump the protocol (v19 → v20)**. Check that
  `AppliedCondition`'s new keys reach the GM on a player's sheet sync. Keep the server
  rules-free.

### 3.3 UI

- **`ui/action_grid.py` — `ActionGridPopup`** (reusable, rules-free): a frameless
  `Qt.Popup` `QFrame` built from `GridSection(title, items)` and
  `GridItem(key, caption, icon, tooltip, enabled)`. Each tile is a square `QToolButton`
  (`ToolButtonTextUnderIcon`). The column count follows the popup's width (reuse
  `FlowLayout` / `ColumnFlowPanels`, don't invent a fourth reflow). It has keyboard
  navigation, closes on Esc, on a click outside, or on a pick, and emits `picked(key)`.
  It is placed under its anchor and kept on screen. Every size and colour is a theme
  token (add tokens such as `grid.tile`, `grid.icon`, `grid.gap` to the presets). A
  disabled tile keeps its tooltip, which says why ("Load an attack first").
- **The roller (`ui/dice_roller.py`):**
  - one `Bonus/Penalty` slider −20..+20, centred. The spin box is a signed
    `make_spin_box` (`+3` / `−3`). It keeps the tint of `_mark_extras`, `better` above 0
    and `worse` below. `adjustment()` keeps its name and meaning. The net-to-pair split
    for the wire stays. `add_bonus` adds. `_spend_extras` zeroes it;
  - an **action lane** under it: a `QWidget` whose height reserves **one line even when
    empty** (an invisible placeholder line of the same height — not a minimum size, see
    the standing rule). Each active action is an `ActionLine`: its name, ✕, then a slider
    and spin over `0..X_max` (configurable) or a fixed caption (`"−2 attack"`, `"roll
    twice"`). A second line grows the lane (rare; accepted);
  - **the attack picker:** a picked `needsAttack` action with no attack on the chip
    opens a plain `QMenu` of the character's attack specs (`power_rolls`, filtered by
    `targetable_attack` / has an attack check). The host supplies that list, since the
    panel reads no game data: `set_attack_choices(callable)`, like
    `set_roll_choices`. Removing the chip greys out every `needsAttack` line. Loading
    another attack re-enables them, recomputes `X_max`, and resets each to its max;
  - the Action row is replaced by one centred **Actions** button that opens the popup.
    `set_actions` becomes `set_action_menu(sections)` (the host builds the sections
    from `actions.json` plus what it supports), and `actionRequested(key)` still
    reports the existing actions (Impress, Area, …), so their controllers don't change;
  - on a roll: `apply_maneuvers` on the spec. Then, **once the die has settled** (not in
    `_abandon_roll`), request the lasting conditions onto the actor and spend the lines
    beside `_spend_extras`. A **Reroll** throws the same maneuvers again but **adds no
    condition**. An **aimed attack** (right-click a line) takes the active lines too.
- **The conditions on the sheet:** a new bus request topic (e.g.
  `condition-requested`) carries `(id, amount, temporary)` from the roller to the
  Conditions block, which writes the model through `apply_condition` and fans out
  `condition-changed`. This keeps "no block names another". Undo comes through the
  model.
- **Dots (`ui/stat_mark.py`):** a small round `StatMark` painted through
  `ui.paint.painting`, coloured `tint.better` / `tint.worse`, with the temporary
  sources in its tooltip. It sits beside the Total in the Resistances table (DEF), the
  System readouts (Defense, Initiative if ever touched), Skills, and each power/gear
  card's attack roll line. The simple sheet gets it because it borrows the same blocks.
  It is hidden (not removed) when there is no mark, so rows don't jump.
- **Chips:** temporary chips are drawn in `tint.better` / `tint.worse` from `tone`,
  with the amount in the caption. **"Clear temporary"** sits on the Conditions block
  header, the GM's player and NPC cards, and the Scene cards, and is visible only while
  `any(c.temporary …)`.
- **The GM (`ui/gm_window.py`, `ui/gm/*.py`):** the Rolls block's Actions button. A pick
  → the NPC chooser (the `_on_history_roll` menu pattern: portraits, board order). It
  loads that NPC's attack list into the picker, rolls as that NPC, and lands the lasting
  conditions on that NPC (`_apply_npc_damage`'s pattern: change the GM's model, then
  replay onto an open NPC sheet). On a player's card, "Clear temporary" goes to the
  owning app through the existing condition path in `session.md`.
- **Targeted actions** reuse the targeting layer: the target is picked off the Scene
  (`ui/attack_targets.py`), the GM accepts on an `ActionCard`, the server rolls the
  NPC's side, and a player is asked. A two-stage action (Grab, Trip, Disarm) is the
  aimed-attack road for stage one and the impress road for stage two. Results land in
  the history as one table per action.

### 3.4 Tests (see `docs/notes/testing.md`)

- `tests/core/test_maneuvers.py`: the caps (each of the four, at 0, small values, and
  above 5), the combining and cancelling, the spec shift (modifier, the follow-up DC,
  the ladder rank, `also`), and `lasting_conditions`.
- `tests/core/test_temporary_conditions.py`: amount/temporary round trip, old saves,
  `condition_amount_mods`, `temporary_marks`, `clear_temporary`, DEF overlay, the
  attack-check mod reaching `power_rolls`.
- `tests/ui/test_action_grid.py`, the roller tests (the 50 references to
  `_bonus_spin` / `_penalty_spin` in `tests/ui/dice/`, `gm/`, `session/`, and
  `sheet/test_extra_effort.py` move to the one slider), the dot and chip tests, and the
  GM tests. Keep `test_window_flash.py` green (the popup must never show parentless).
- Use the `affected-tests` skill while working, plus `ruff check .` and `black .`. The
  full `pytest` run (~16 min) is required in phase 10, and also recommended at the end of
  phases 4 and 7, which touch widely used widgets.

---

## 4. Phases

Each phase ends with: tests green (affected), ruff + black clean, this file updated
(status, checkboxes, log), the relevant `docs/notes/` file updated, and **one commit on
`feature/combat-maneuvers`**. Never merge into `develop`: the user says when it's done.

### Phase 1 — Temporary conditions in `core`
- [x] `AppliedCondition.amount` / `.temporary`; (de)serialization; old saves load.
- [x] `apply_condition(amount=, temporary=)`; re-applying replaces the amount; the
      display name includes the amount.
- [x] `conditions.json`: `tone`, `amountMods`, `defaultAmount`; the seven new records;
      a `maneuver` condition group. Loader + `Condition` dataclass fields.
- [x] `condition_amount_mods`, `temporary_marks`, `clear_temporary`.
- [x] Fold DEF into the displayed Defense / Defense Class, and the attack-check mods
      into `power_rolls`.
- [x] Undo/redo works for the new fields (`docs/notes/undo-and-redo.md`).
- [x] Do the player→GM sheet sync and the save files carry the new keys? **Saves and
      the player's snapshot: yes** (both are the whole `to_dict`; `sanitize_snapshot`
      only drops `image_path`). **The Scene: no** — `_sanitize_scene_conditions`
      whitelists `id`/`parameter`/`count`. **GM → player: no** — `ApplyCondition` has
      no `amount`/`temporary`, and there is no "clear temporary" command. Phase 2's
      "Clear temporary" on the GM's and Scene cards needs both, so the **bump (v19 →
      v20) probably lands in phase 2**, not 5.

### Phase 2 — Sheet indicators
- [ ] `StatMark` dot widget, tokens for its size.
- [ ] Dots in Resistances, System readouts, Skills, and power/gear attack lines; the
      simple sheet.
- [ ] Chips tinted by `tone`, with the amount in the caption.
- [ ] "Clear temporary" on the Conditions block, GM player/NPC cards, and Scene cards,
      visible only when a temporary condition exists; one undo step; a history note.
- [ ] For now the conditions come in through the existing "+" menu (the `maneuver`
      group), which shows that everything works without the roller.

### Phase 3 — Grid popup, icons, the Actions button
- [ ] `ui/action_grid.py` (`ActionGridPopup`, `GridSection`, `GridItem`), reflowing
      columns, keyboard, Esc / click-outside, on-screen placement, tokens.
- [ ] Draw the SVG icon set (`ui/assets/actions/`), with a fallback, tinted from the
      theme.
- [ ] `actions.json` `combat.menu` + action records (names, icons, tooltips, kinds),
      and loader support.
- [ ] Replace the Action row with the **Actions** button. Reroll, Impress and Area go
      through the popup and behave exactly as before. The not-yet-built actions show
      disabled ("coming soon" is **not** acceptable in a release — phase 10 checks
      every tile works).
- [ ] The GM's Rolls block gets the same button (Team attack/check/Group check
      tiles: pick the group first).

### Phase 4 — Roller rework
- [ ] One −20..+20 Bonus/Penalty slider (signed spin, tint, `adjustment()`,
      `add_bonus`, `_spend_extras`, quick rolls' bonus/penalty identity still
      round-trips).
- [ ] The action lane with reserved height; `ActionLine` (configurable or fixed); ✕.
- [ ] `set_attack_choices` + the plain `QMenu` attack picker; chip removed → lines
      greyed out; a new attack → limits reset to the max.
- [ ] Compact mode and the Extended layout still fit (`dice-and-rolling.md`, compact
      section).
- [ ] Move the existing tests onto the one slider. Full run recommended.

### Phase 5 — Attack maneuvers end to end (player)
- [ ] `core/rules/maneuvers.py`: limits, `apply_maneuvers`, `lasting_conditions`,
      with tests.
- [ ] A roll with lines active: shifted spec, history caption, conditions land once
      the die settles, the lines are spent; nothing lands on an abandoned roll; Reroll
      adds no condition.
- [ ] Aimed attacks pick up the active lines. Team attack's final ranks include them
      (p201).
- [ ] Session: spec keys whitelisted, the protocol bump (once), the GM sees the
      conditions.

### Phase 6 — Self actions
- [ ] Aim (best of two on the next attack), Charge (−2) + Slam option (rank + self save
      with Resistance), Defend, Recover (+2 Defense + the recovery roll), Drop Prone,
      Stand (DC 20 Acrobatics/speed, or Hindered temporary; Prone off), Finishing Attack
      (DC 10, a hit is a critical, intent-to-kill toggle), Surprise Attack (GM: the
      target is Vulnerable/Defenseless), Catch (Agility DC 10).

### Phase 7 — The GM's side
- [ ] Actions on the Rolls block → the NPC chooser → that NPC's attacks → roll as that
      NPC; conditions land on the NPC and replay onto an open NPC sheet.
- [ ] Minions: the maneuvers still apply. Check `minions.md` for interactions (a
      minion's natural 20 is no critical, which matters for Finishing Attack).
- [ ] An NPC group member acting: the chooser lists members.
- [ ] Full run recommended.

### Phase 8 — Targeted actions I
- [ ] Feint (the actor picks attack/Deception/Insight; the target resists with the
      highest; a simple-action −5 toggle; Vulnerable, temporary).
- [ ] Trick (Deception vs Deception/Insight; Unaware temporary; 2+ failure → +5 to
      them).
- [ ] Aid (attack vs DC 10; pick the ally and the opponent; Aided +2 / +5 temporary,
      attack *or* Defense).

### Phase 9 — Targeted actions II
- [ ] Grab (attack vs Defense → Strength vs the target's Strength/Dodge; Restrained /
      Bound; the grabber Hindered + Vulnerable temporary).
- [ ] Trip (→ Prone), Disarm (−2; Damage rank vs Strength), Smash (−2 / −5; narrated).
- [ ] Escape (Strength/Sleight of Hand vs the grab DC; removes the hold from self).
- [ ] Block (Blocking temporary, stacking −2 per extra block; the roll vs the
      attacker's result).

### Phase 10 — Finish
- [ ] Every menu tile works; none is disabled except by a rule (the tooltip says
      which).
- [ ] `docs/notes/actions.md` gets a "Combat maneuvers" section,
      `conditions-and-damage.md` temporary conditions, `dice-and-rolling.md` the new
      slider/lane/Actions button, and `CLAUDE.md`'s summary paragraph one sentence.
- [ ] Remaining work goes to `docs/notes/debts.md`.
- [ ] `ruff check .`, `black --check .`, and the **full `pytest`**. Run the app
      (`run-mm-companion` skill) and screenshot the roller and popup for the user.
- [ ] Tell the user it's ready for them to call done. They merge it, not us. Remind
      them that the hub/relay must be redeployed for the protocol bump (memory:
      deployment notes).

---

## 5. Open points (resolve with the user when reached; record the answer in §1)

- **Effects that last to the *end* of a turn** (Aided: the ally's next turn; Feint's
  Vulnerable and Trick Advantage: the actor's next turn). "Clear temporary" is a
  start-of-turn clear, so these can't simply be `temporary`. Phase 1 leaves Aided and
  Trick Advantage lasting (removed by hand). Decide in phase 8: a second flag
  (`untilEndOfTurn`), clearing them by hand, or accepting the early clear.
- **The GM's hit judging ignores conditions.** `ui/attack_gm.py` and `ui/area.py` judge
  against `defense_class` (the build's number), so neither Vulnerable nor All-Out's −X
  moves the DC an aimed attack must beat. `defense_class_in_play` exists now; switching
  the judges to it belongs in phase 5 (it also starts honouring Vulnerable, a visible
  change).

- Several maneuver lines at once grow the lane by a line each. Accepted unless the user
  objects on seeing it.
- Should Accurate/Power's "until start of next turn" ever matter (a second attack in
  one turn)? Treated as no.
- The order and grouping of the menu tiles beyond the user's sketch.

---

## 6. Progress log

Newest first. Each entry: date, phase, what was done, what's next, gotchas, and the
commit hash.

- **2026-11-08 — Phase 1 (done).** `AppliedCondition.amount`/`.temporary` (omitted at
  defaults); `apply_condition(amount=, temporary=)` with the record's `defaultAmount`
  and `temporary` flag; re-apply replaces the amount; lasting beats temporary. Loader:
  `AmountMod`, `Condition.tone`/`amount_mods`/`default_amount`/`temporary`,
  `TraitKeys.attack_check`, `MECH_AMOUNT_MOD`, `TONE_*` in `components.py`. Data: the
  seven records in a new `maneuver` group ("Maneuvers & Actions"), plus `_meta`
  `toneNote`/`amountNote`/`temporaryNote` and the `amount_mod` mechanism. Rules: see
  §3.2 "As built". DEF folds into the Defense row (after halve/zero) and the Defense
  Class pin; the attack-check mod folds into `power_rolls` (incl. the Linked check, with
  a "Conditions: …" hint). Tests: `tests/core/test_temporary_conditions.py` (23).
  **Gotchas:** (1) the plan's claim that `condition_attack_mods` reaches `power_rolls`
  was wrong — nothing reads it; (2) the GM's hit judging uses the condition-free
  `defense_class` (§5); (3) on this machine a Python script that edits files must open
  them with `newline=""`, or it writes CRLF into LF files, and heredocs containing
  backticks break the Bash tool — write the script to the scratchpad and run it.
  **Phase 2 needs to know:** the Resistances table tints any active condition overlay
  as a penalty, so Defending's +2 currently shows in the *worse* tint until phase 2
  tints by `tone`/sign; the "+" menu applies an All-Out Attack with no amount (no
  `defaultAmount`), which moves nothing — phase 2 should ask for the amount, or the
  record gets a default. Wire findings in the phase 1 checklist (bump likely in phase
  2). Next: phase 2. Commit: (see below).

- **2026-11-08 — Phase 0.** Rules checked against the book (p200–205). The user's
  answers recorded in §1. The plan and the `/combat-maneuvers` skill written. Branch
  `feature/combat-maneuvers` created off `develop` (f8084e2). Next: phase 1.
