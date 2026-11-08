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
| 2 | Sheet indicators: dots, tinted chips, "Clear temporary" | done (2026-11-08) |
| 3 | Reusable grid popup, action icons, the **Actions** button | done (2026-11-08) |
| 4 | Roller rework: one two-way slider, the action lane, the attack picker | done (2026-11-08) |
| 5 | The four attack maneuvers, end to end (player) | done (2026-11-08) |
| 6 | Self actions: Aim, Charge/Slam, Defend, Recover, Drop Prone, Stand, Finishing, Surprise, Catch | done (2026-11-09) |
| 7 | The GM's side: NPC chooser first, conditions on the NPC | done (2026-11-09) |
| 7b | Team attack as a call to roll: a history card anyone answers, GM resolves | done (2026-11-09) |
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
11. **The popup's sections** (asked 2026-11-08, phase 3): four — *Attack Maneuvers*
    (Accurate, All-Out, Defensive, Power, Finishing, Surprise); *Actions* A–Z (Aid, Aim,
    Attack, Block, Catch, Charge, Defend, Disarm, Drop Prone, Escape, Feint, Grab,
    Impress, Recover, Smash, Stand, Trick, Trip); *Area & Group* (Area, and the GM's
    Team attack / Team check / Group check); *Dice* (Reroll).
12. **The Attack tile** (asked 2026-11-08, phase 3): pick an attack (the plain `QMenu`
    of the character's attacks), then **optionally** the target: a second menu whose
    **first line is "Skip"**. With no target the roller's **DC box counts as the
    Defense**. If neither a DC is set nor a target picked when the attack is rolled,
    show a **warning sign** (the roll still goes).
13. **Surprise Attack** (asked 2026-11-09, phase 6): **the GM grants it.** The tile is the
    GM's: pick a creature, and it gets Vulnerable or Defenseless as a temporary condition
    (built with the GM's side, phase 7). On a player's sheet the tile is greyed with "The
    GM grants a surprise attack" (`gmRuns`); the player then attacks normally, and the
    GM's hit judge already reads the target's conditions.
14. **Finishing Attack's intent to kill** (asked 2026-11-09, phase 6): **rewrite the
    ladder.** The tile offers "Finishing Attack" or "…, intent to kill"; the kill version
    turns the damage save's third and deeper failures into Dead, so the GM's auto-apply
    and the buttons land Dead with no by-hand step.
15. **Team attack with maneuvers** (asked 2026-11-09, phase 7): a **new team attack, as a
    call to roll**, built in its own phase (7b). The GM sets only the target and a
    bonus/penalty and sends it; a **button appears on a card in the history**. Every
    player who presses it then rolls an attack with **any maneuvers they like** (their
    own roller and lane), and the card updates with each new roll. **Only a roll (or
    reroll) made after pressing the button counts.** The GM presses **Resolve** when
    everyone has rolled; then the primary is picked, the +2/+5 (and critical +5) worked
    out, and the target's save rolled or asked. **Keep both**: the NPC-group Team attack
    window stays as it is (the GM rolls a squad at once, with its Adjust; no maneuvers
    there). An NPC joins the new one by the GM pressing the button and picking the NPC.
16. **The call's setup** (asked 2026-11-09, phase 7b): the Rolls block's **Team attack
    tile starts the call only** — the NPC-group window is reached from the group's ☰ menu
    alone. The GM sets the call up in a **small window**: the target (the Scene) and one
    signed Bonus/Penalty, then **Send**.

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
- **As built (phase 3):** `combat` = `menu` (sections), `limits`, `actions`. Kinds
  `maneuver`, `self`, `targeted`, `loader`, `attack` (the Attack tile) and `builtin`
  (run by the host's own window or the dice: Impress, Area, the team actions,
  Reroll). Records carry `caption` (the tile's short name), and the maneuvers already
  carry `bonus`/`penalty`/`lasting`. Loader: `CombatRules` / `CombatAction` /
  `CombatMenuSection` / `ManeuverLimits`, `GameData.combat`, `COMBAT_KIND_*`. The
  per-kind fields of the other kinds (`fixedModifier`, `check`, `outcomes`, …) are
  added by the phase that runs them.
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
  `protocol.sanitize_spec`'s whitelist. **The bump has already happened: v20, in phase
  2** (`ApplyCondition.amount`/`temporary`, `ClearTemporary`, the scene's condition
  keys). v20 is unreleased, so later phases add to it and do **not** bump again. Keep the
  server rules-free.
- **As built (phase 2):** `RollSpec.marks` (local only, `compare=False`) names the trait
  keys a spec's modifier folds condition mods into — an attack check carries
  `trait_keys.attack_check` — so a card knows which line wears the dot.
  `ConditionEffect.better` says an overlay only raises a stat (the tint follows it).
  `condition_asks_amount` + a record's `maxAmount` (5 on All-Out / Defensive Attack) drive
  the "+" menu's amount prompt. `clear_temporary_note` is the history line.

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
- **As built (phase 2):** the dots are `ui/stat_mark.py` — `StatMark` (a widget, for
  label readouts: the simple sheet's boxes and lines, a card's roll line) and
  `StatMarkDelegate` + `mark_item`/`MARK_ROLE` for table cells (the Resistances and
  Skills totals: painted in the cell's corner, so no column width moves). Token
  `mark.size`. **No dot on the System block**: nothing temporary moves Initiative or
  Speed yet; add one there when something does. Chips: `condition_tone_token(record)`
  colours every chip (GM cards, simple sheet; on the sheet's block only benefit or
  temporary chips, lasting hindrances keep the plain panel). A **✕ on temporary chips
  only** (decision 8 read as "each temporary chip"; the right-click still works on all).
  On the GM's cards "Clear temporary" is a small button on a line under the chips, never
  in the chip flow (`condition_names()` reads the flow). An NPC's clear replays onto an
  open NPC sheet with `note=False`, and the GM window writes "Thug: temporary conditions
  cleared: …"; a player's is a `ClearTemporary` command and their own sheet writes the
  line. `applyConditionRequested` on the player and NPC cards is now
  `(key, id, parameter, amount)`.
- **As built (phase 4):** the roller as above, with these names: `_modifier_slider` /
  `_modifier_spin` / `_modifier_label` (`MODIFIER_LIMIT`, `MODIFIER_LABEL`), the lane in
  `ui/roller/action_lane.py` (`ActionLane`, `ActionLine`, `LineLimit`, `NEEDS_ATTACK`),
  and on the panel `start_action_line(key, name, limit=, fixed=, needs_attack=, tone=)`,
  `action_lines()`, `active_lines()` (what phase 5's `apply_maneuvers` reads),
  `loaded_attack()`, `choose_attack()`, `menu_point()`. A roll records `lines` in the
  roll Reroll throws again. The picker's list is `core.rules.attack_choices`
  (`AttackChoice(caption, spec)`, in `core/rules/attack_choices.py`, beside
  `area_choices`); every chooser is `action_menu.choose_from_menu`. The no-DC warning
  (`NO_DC_WARNING`) is on **every** unaimed attack rolled with no DC, not only the
  Attack tile's. **The maneuver tiles are still greyed** (`NOT_BUILT`): phase 5 wires
  them to `start_action_line` with `maneuver_limits` as the *limit*.
- **As built (phase 5):** `core/rules/maneuvers.py` — `maneuver_action`,
  `has_attack_check` (the spec's `marks` carry `trait_keys.attack_check`),
  `maneuver_values`, `maneuver_limit(data, id, spec, char, others) -> ManeuverLimit`,
  `maneuver_caption`, `apply_maneuvers`, `lasting_conditions`; constants
  `MANEUVER_ATTACK_CHECK` / `_EFFECT_RANK` / `_DEFENSE` in `game_data.py`; a save's
  local-only `RollSpec.effect_rank` (DC − the effect's DC base); `rules.same_attack`.
  The panel: `set_line_shift`, `with_lines`, `_flight` / `_made_of` (the spec a roll is
  made of), `linesRolled`, `roll_spec(before=)`, `load_spec(localize=)`. The lane:
  `ActionLine.relimit` / `valueChanged`, `ActionLane._relimit_others`. The host:
  `DiceSection.start_maneuver`, `_land_lasting`, `conditionsRequested` → bus
  `condition-requested` → `ConditionsSection.take_conditions`. No `RollSpec.maneuvers`
  or `finishing` field was needed: the caption goes in the label.
- **As built (phase 6):** `core/rules/self_actions.py` — `self_action`, `action_reason`
  (`requires` / `refusedBy` / `gmRuns`), `line_key` / `split_line_key` (`action/option`),
  `line_fit`, `apply_actions` (the maneuvers + the lines, one caption; replaces
  `apply_maneuvers` as the panel's shift), `self_action_changes`, `apply_condition_changes`,
  `check_spec`, `recover_choices` (`RecoverChoice`), `self_action_note`, `speed_rank`
  (the fastest mode). Loader: `CombatEffect` (on the action and on each `CombatOption`,
  merged by `CombatAction.effect_for`), `ConditionChange`, `CombatCheck`
  (`COMBAT_CHECK_*`), `SlamRules` (the record's `slamRules`), `KillRule`, `RecoveryRule`.
  `RollSpec.critical_hit` (wire), `roll_twice` and `then` (local). Session (v20, no bump):
  `RollRequest.twice`, `RollRecord.other_die`. Panel: `ActionLine(fits=)`,
  `start_action_line(fits=)`, `remove_action_line`, `_twice`, `_then`. Host:
  `DiceSection.start_self_action`, `_start_self_line`, `RECOVER_DEFENSE_ONLY`;
  `take_conditions` takes `ConditionChange`s. See the roller notes' "self actions".
- **As built (phase 3):** `ui/action_grid.py` as above (tokens `action-grid.tile`,
  `.icon`, `.gap`, `.columns`; icons coloured from the tile's palette `ButtonText`, so no
  colour token). `ui/action_menu.combat_menu(data, offered, gm=, fallback=)` builds the
  sections; *offered* maps an id to `None` or a reason, an unmentioned id is greyed
  `NOT_BUILT`, `gmOnly` ones are dropped for a player. The panel's
  `set_action_menu(build)` is called with the panel's own entries (Reroll) at every
  opening; `open_actions()` returns the popup (tests drive it). Hosts:
  `DiceSection.action_menu`, `GMRollsMixin.action_menu` (+ `_choose_group`,
  `TEAM_ACTIONS`, `NO_NPC_GROUPS`). `set_actions`, `_reroll_button` and the Action row
  are gone; `can_reroll` is what tests read.
- **The GM (`ui/gm_window.py`, `ui/gm/*.py`):** the Rolls block's Actions button. A pick
  → the NPC chooser (the `_on_history_roll` menu pattern: portraits, board order). It
  loads that NPC's attack list into the picker, rolls as that NPC, and lands the lasting
  conditions on that NPC (`_apply_npc_damage`'s pattern: change the GM's model, then
  replay onto an open NPC sheet). On a player's card, "Clear temporary" goes to the
  owning app through the existing condition path in `session.md`.
- **As built (phase 7):** the sheet's maneuver / self-action code moved, unchanged in
  behaviour, into `ui/roller/combat_runner.py`: `CombatRunner` (wires the panel's
  attack choices, line shift and `linesRolled`; `start`, `start_maneuver`,
  `start_self_action`, `ensure_attack`, `choose_attack`, `load`, `own`, `act_as`,
  `drop_lines`) and `CombatActor(character, name, key, minion, land, throw)`;
  `RECOVER_DEFENSE_ONLY` lives there now. `DiceSection.combat` is one with a fixed actor
  (its `start_maneuver` / `start_self_action` are wrappers). The GM's is
  `ui/gm/combat.py` `GMCombatMixin` (`_setup_combat`, `combat_offered`,
  `_gm_combat_action`, `_choose_actor`, `_actor_for`, `_npc_throw`, `_follow_actor`,
  `_gm_attack`, `_grant`; `NO_NPC_TO_ACT`, `NO_ONE_ON_SCENE`, `NO_ATTACKS_MARK`). The
  `condition-requested` payload is read by `core.rules.apply_condition_requests` on both
  sides (`take_conditions` and `_land_npc_requests`; `_after_npc_condition_change(requests=)`
  replays it onto an open sheet). `choose_from_menu(icons=)`. `aim_own` declares the spec
  the die is made of (`area_roll_attack(before=)`). Surprise Attack's two options are data
  (`applies` vulnerable / defenseless, `temporary: true`).
- **As built (phase 7b):** the call to roll — core `core/rules/team_call.py`
  (`KIND_TEAM_CALL`, `team_call_spec`, `is_team_call`, `team_call_target`,
  `team_join_spec`, `team_entry_option`, `team_fits`, `TEAM_NOT_SAME`); the GM's
  `ui/team_call.py` (`TeamCallWindow`, `TeamCallController`, `TeamCall`; `RESOLVE_CAPTION`,
  `CALL_OFF_CAPTION`, `NO_SESSION_FOR_CALL`, `NO_TARGET_FOR_CALL`, `NOBODY_YET`;
  `GMWindow._team_calls`). `AttackController` gained `ignore`, `judge_entry`,
  `resolve_team`, and the shared `_team_aimed` / `_combine_team` (the group window's
  `team_attack` runs on them too) plus the module's `team_summary`. History:
  `team_call_of`, `JOIN_CAPTION`, `GroupTableCard.call` / `join_button` / `joinRequested`,
  `RollHistoryPanel.teamJoinRequested`, `DiceRollerView.teamJoinRequested`. Runner:
  `TEAM_LINE`, `team_call`, `join_team`, `attacker_name`. GM: `_gm_team_join`,
  `_choose_actor(skip_ref=)`, `TEAM_CALL_OVER`; `gm/rolls.py`'s `TEAM_ACTIONS` became
  `GROUP_ACTIONS` (team and group check). **No wire change**: the call is a request
  record whose spec uses only keys v20 already carries.
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
- [x] `StatMark` dot widget, tokens for its size (`mark.size`), plus a table delegate.
- [x] Dots in Resistances, Skills, and power/gear attack lines; the simple sheet. (System
      readouts: none — nothing temporary moves them yet.)
- [x] Chips tinted by `tone`, with the amount in the caption; ✕ on temporary chips.
- [x] "Clear temporary" on the Conditions block, GM player/NPC cards, and Scene cards,
      visible only when a temporary condition exists; one undo step; a history note.
- [x] For now the conditions come in through the existing "+" menu (the `maneuver`
      group), which shows that everything works without the roller. The menu asks for
      an amount when the record has no default (All-Out / Defensive Attack, 1–5).
- [x] The tint of a condition overlay follows its sign (Defending's +2 is green).
- [x] Protocol v20 (the feature's one bump): `ApplyCondition.amount`/`temporary`,
      `ClearTemporary`, the scene's `amount`/`temporary`.

### Phase 3 — Grid popup, icons, the Actions button
- [x] `ui/action_grid.py` (`ActionGridPopup`, `GridSection`, `GridItem`), reflowing
      columns, keyboard, Esc / click-outside, on-screen placement, tokens.
- [x] Draw the SVG icon set (`ui/assets/actions/`), with a fallback, tinted from the
      theme.
- [x] `actions.json` `combat.menu` + action records (names, icons, tooltips, kinds),
      and loader support.
- [x] Replace the Action row with the **Actions** button. Reroll, Impress and Area go
      through the popup and behave exactly as before. The not-yet-built actions show
      disabled ("coming soon" is **not** acceptable in a release — phase 10 checks
      every tile works).
- [x] The GM's Rolls block gets the same button (Team attack/check/Group check
      tiles: pick the group first).
- [x] `QToolButton:disabled` restated in the styled presets (greyed tiles read live).

### Phase 4 — Roller rework
- [x] One −20..+20 Bonus/Penalty slider (signed spin, tint, `adjustment()`,
      `add_bonus`, `_spend_extras`, quick rolls' bonus/penalty identity still
      round-trips — now read by the net).
- [x] The action lane with reserved height; `ActionLine` (configurable or fixed); ✕.
- [x] `set_attack_choices` + the plain `QMenu` attack picker; chip removed → lines
      greyed out; a new attack → limits reset to the max.
- [x] **The Attack tile** (decision 12): the attack picker, then a target menu with
      "Skip" first (Scene targets, through the aimed-attack road — `aim_attack`); no
      target → the DC box is the Defense; a warning sign when there is neither.
- [x] **The player's Area tile**: `core.rules.area_choices` → one opens at once,
      several on a menu → `DiceSection.start_area(area key)`.
- [x] Compact mode and the bottom-strip roller still fit (screenshots).
- [x] Move the existing tests onto the one slider. Full run: stopped at 82% by the
      machine running low on memory, no failures up to there (see the log).

### Phase 5 — Attack maneuvers end to end (player)
- [x] `core/rules/maneuvers.py`: limits, `apply_maneuvers`, `lasting_conditions`,
      with tests.
- [x] A roll with lines active: shifted spec, history caption, conditions land once
      the die settles, the lines are spent; nothing lands on an abandoned roll; Reroll
      adds no condition.
- [x] Aimed attacks pick up the active lines (the declaration carries the shifted spec).
- [x] Team attack's final ranks include them (p201) — done in **phase 7b**: a call's
      entries are rolled with their maneuvers, so their ranks as rolled are what combine.
- [x] Session: **no new wire key** (the label, modifier, DC and ladder rank already
      travel; `effect_rank` is local only), so nothing to whitelist and no bump. The GM
      sees the conditions through the player's snapshot.
- [x] The GM judges aimed / area / team attacks against `defense_class_in_play` (§5's
      open point), so All-Out's −X and Vulnerable now move the DC.
- [x] Combined caps across several lines (the lane re-limits the others on every move).

### Phase 6 — Self actions
- [x] Aim (best of two on the next attack; the server rolls both in a session),
      Charge (−2) + Slam options (rank + the self save at half rank, loaded after),
      Defend, Recover (+2 Defense + the recovery roll or a fatigue step), Drop Prone,
      Stand (DC 20 Acrobatics/speed, or Hindered temporary; Prone off), Finishing Attack
      (DC 10, a hit is a critical, intent to kill rewrites the ladder), Catch (Agility
      DC 10).
- [x] Surprise Attack: greyed on a player's sheet ("the GM grants it", decision 13);
      the GM's tile is **phase 7**.

### Phase 7 — The GM's side
- [x] Surprise Attack (decision 13): pick a creature → Vulnerable or Defenseless,
      temporary, on it (an NPC's model, or a player's sheet over the session).
- [x] The self actions on the GM's roller (the shared `CombatRunner`, acting as the
      chosen NPC: `apply_actions` with that NPC as *character*, conditions on the NPC).
- [x] Actions on the Rolls block → the NPC chooser → that NPC's attacks → roll as that
      NPC; conditions land on the NPC and replay onto an open NPC sheet.
- [x] Minions: the maneuvers still apply, and a minion's rolls go as a minion's. A
      minion's Finishing Attack hit **is** still a critical: the book (p25) takes away
      only the added success of "a roll of 20", and `judge_attack` already ORs
      `critical_hit` in.
- [x] An NPC group member acting: the chooser lists members, each with its group.
- [ ] ~~Team attack with the lane's maneuvers~~ — **redesigned by the user (decision
      15)** and moved to phase 7b.
- [x] Full run (see the log).

### Phase 7b — Team attack as a call to roll (decision 15)
- [x] The GM's Team attack tile (decision 16: the tile is the call; the group window is on
      the group's ☰ menu): a small window — target and bonus/penalty — and Send → the call
      in everyone's history as a table with a **Join** button (wire: a request record with
      a new spec kind, so **no** new key, inside v20).
- [x] Pressing it arms the presser's roller: the **next** attack they roll, maneuvers and
      all, is that member's entry; a reroll of it replaces it. A roll made before pressing
      does not count. The GM presses it for an NPC through the NPC chooser (the target
      left out).
- [x] The GM's card shows each entry as it comes (`judge_entry` against the target's
      `defense_class_in_play`, minion rules, `combine_team_attack`'s running summary).
      The entries' ranks as rolled (maneuvers included) decide who combines (`team_fits`).
- [x] **Resolve** (GM only): strikes the call, then the primary's save at the raised rank,
      rolled for an NPC, asked of a player — the road the group window's team attack
      uses, now shared (`_combine_team`). **Call off** and the GM's ✕ settle it.
- [x] Tests, notes (`actions.md` "A call to roll", `dice-and-rolling.md`,
      `npc-groups.md`), the plan.

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
- ~~The GM's hit judging ignores conditions.~~ **Resolved in phase 5**: the aimed
  attack, the area window's primary and the team attack judge against
  `defense_class_in_play`, so Vulnerable now halves the DC too (a visible change).

- Several maneuver lines at once grow the lane by a line each. Accepted unless the user
  objects on seeing it.
- Should Accurate/Power's "until start of next turn" ever matter (a second attack in
  one turn)? Treated as no.

---

## 6. Progress log

Newest first. Each entry: date, phase, what was done, what's next, gotchas, and the
commit hash.

- **2026-11-09 — Phase 7b (done).** The team attack as a call to roll. Asked the user two
  questions, now decision 16 (the tile is the call only; a small setup window). How it
  works (`actions.md` "A call to roll" has the detail, §3.3 "As built (phase 7b)" the
  names): Send posts a **request record** whose spec is `KIND_TEAM_CALL`; every history
  draws it as a `GroupTableCard` with a Join line; Join arms the roller with a fixed
  `TEAM_LINE`, and the next attack is stamped (`team_join_spec`: action id, title, the
  attacker as the row, the GM's bonus on the check) — so it fills the call's table on
  every screen; the GM's controller gathers entries off `rollAdded` and keeps its
  `ActionCard` (Resolve / Call off) judged and summarised; Resolve strikes the request and
  resolves on the aimed-attack road under the call's id. Tests:
  `tests/core/test_team_call.py` (7), `tests/ui/dice/test_team_call_join.py` (9),
  `tests/ui/gm/test_team_call.py` (17, one through a real hosted session);
  `test_actions_popup_gm.py` moved onto `GROUP_ACTIONS`. **Decisions I made (small):**
  (1) **no wire change** — a request record already carries every key the call needs, and
  the server's `sanitize_spec` passes an unknown kind; (2) the call and its entries are
  **one table card** (decision 15's "the card updates with each new roll"), not a request
  card beside a table; (3) Resolve **strikes** the request record, which is how the Join
  buttons go from every screen (the rows stay); Call off, or the GM's ✕ on the table,
  settles it with nothing done, and a struck entry leaves the team; (4) one entry per
  `(seat, actor)`, a later roll replacing the earlier (a re-roll of the entry re-throws
  the stamped spec, so it counts); a line left armed does **not** stamp a reroll of an
  earlier roll — Reroll never reads the lane; (5) the **first** entry sets the effect and
  resistance, a different one is shown "left out"; one more than `rankSpread` below the
  strongest is left out too (rank unknown → waived, as in the group window); (6) a single
  entry may be resolved (it is then that attack alone); (7) the tile is greyed **off the
  air** (nobody could answer; the group window still works there) and with nobody on the
  Scene; (8) the target's own roll never joins; (9) the GM's Join is the same table
  button players have, then the NPC chooser without the target. **Refactor:** the group
  window's `team_attack` and the call now share `_team_aimed` / `_combine_team` /
  `judge_entry` (`judge_entry` also passes a Finishing Attack's `critical_hit`, which the
  group roll never had). **Gotchas:** (1) a callable *instance* monkeypatched onto a
  class is not bound, so it misses `self` — patch a lambda that calls it; (2) a quick NPC
  has more than one attack choice; (3) heredocs with backticks still break the Bash tool
  (scripts go in the scratchpad). Next: phase 8 (Feint, Trick, Aid). **Full run: 4783 passed** (10 min 9 s).
  Commit: COMMIT_HASH.

- **2026-11-09 — Phase 7 (done).** The GM's roller runs the maneuvers, the self actions
  and the Attack tile as an NPC, and grants Surprise Attack. Asked the user how a team
  attack takes maneuvers; the answer redesigned it (decision 15: a call-to-roll card any
  player or NPC answers, GM resolves, the group window kept) and it became **phase 7b**.
  §3.3 "As built (phase 7)" has the names. How it works: a tile asks which NPC acts
  (`choose_from_menu` with portraits; one NPC → no menu; an NPC that can't — no attacks,
  Stand while not Prone — is greyed with the reason; a group member shows "· Thugs", a
  minion "(minion)"); the runner then acts as it: that NPC's attacks in the picker
  (labelled "Thug: …" with `actor`, so the history heads "Thug (GM)"), its numbers in the
  caps, a minion's rolls as a minion's, and what is left lands on its model and replays
  onto its open sheet as **temporary** (the old replay, `apply_condition_by_id`, would
  have lost the flag). Tests: `tests/ui/gm/test_combat_gm.py` (21). **Decisions I made
  (small):** (1) the sheet's code moved into a shared `CombatRunner` rather than being
  copied onto the GM window; (2) a different NPC acting — a tile for another one, or a
  roll off another NPC's card or sheet (`_follow_actor`) — **drops the lane's lines**:
  their limits read the last actor's numbers; (3) a loaded attack that isn't the actor's
  own (`spec.actor` differs, e.g. loaded off a card) makes the picker ask again; (4) a
  check an NPC's action loads (Stand's) is **held** like the chip's 🎲 recovery
  (`_recovering`), so a made roll sheds Prone off that NPC; (5) Surprise Attack lists
  everyone on the Scene, players included (to a player it goes as `ApplyCondition` with
  `temporary`); (6) a minion's Finishing hit stays a critical (book p25 only removes the
  natural-20 one). **Fixed on the way:** the GM's own aimed attack declared the spec
  *before* the lane shifted it, so a maneuver never reached the save it asked for
  (`aim_own` now uses `roll_spec(before=)`, as a player's does). **Gotchas:** (1) the
  runner must look `post_note` up at call time (`lambda text: self.post_note(text)`) — a
  test swaps it on the instance after the block is built; (2) a test stand-in for
  `QMenu` entries needs `setIcon` now (only called when there is a portrait). Next:
  phase 7b (the call-to-roll team attack), then 8. **Full run: 4751 passed** (16 min 47 s;
  it got through this time). It prints `Windows fatal exception: code 0x80040155` stack
  dumps from `simple/printing.py::make_printer` in `test_a_pdf_can_be_written`: COM noise
  faulthandler reports from the Windows printer setup, not a crash — the test passes.
  Commit: efcb4c7.

- **2026-11-09 — Phase 6 (done).** The self actions work on a player's sheet. Asked the
  user two questions, now decisions 13 (Surprise Attack is the GM's to grant: greyed for a
  player, built in phase 7) and 14 (intent to kill rewrites the damage ladder). Rules
  re-read in the book: Aim p200; Charge/Slam, Defend, Drop Prone p202; Recover, Stand
  p204; Finishing, Surprise p201; "Resistance" halves the rank before the save (p130).
  §3.3 "As built (phase 6)" has the names. Tests: `tests/core/test_self_actions.py` (23),
  `tests/ui/dice/test_self_actions_ui.py` (23). **Decisions I made (small):** (1) Recover's
  fatigue option steps **down** the ladder (Exhausted → Fatigued) rather than wiping it,
  my reading of "remove your most severe Fatigue condition" with Exhausted superseding
  Fatigued; (2) "speed rank" (Stand's check, Slam's rank) is the fastest mode the
  character has; (3) Catch's and Stand's checks **load** onto the chip (the player can
  still add a circumstance modifier), while Recover's recovery check rolls at once (it is
  part of the action); (4) the slammer's own save is loaded onto the chip once the slam
  settles (`RollSpec.then`), not posted as a request; (5) Charge is not limited to close
  attacks — a spec carries no range (tooltip says close); (6) a self action whose record
  does nothing yet (Block, Escape) stays greyed, `NOT_BUILT`; (7) the maneuvers' caps do
  not count Charge's −2 (an edge: a +2 attack charged could still be doubled to +4).
  **Gotchas:** (1) `reroll_d20` returns `(die, natural)`, so a second die for a lucky Aim
  must keep the kept die's own `natural` (caught in review, now tested both locally and on
  the server); (2) `degree_label` reads `critical` as "Nat 20!/Nat 1!" off the die, so a
  Finishing hit is **not** flagged critical in the readout's degree line — the save's
  label ("critical hit, DC X") says it; (3) an action-level `slam` dict would read as the
  effect's `slam: true` flag, hence the record's `slamRules`; (4) `cat > file` in a Bash
  call blocked on stdin again — write scripts to the scratchpad with the Write tool.
  Next: phase 7 (the GM's side: the NPC chooser, the maneuvers and self actions as an
  NPC, Surprise Attack, Team attack with maneuvers). **Tests:** a full run was
  **stopped at 70% by the system (low memory)**. Up to there it had found 4 failures, all
  one bug of mine: `self_actions` exported a `condition_changes` that, through `rules`'
  star-imports, shadowed `answering.condition_changes` (which `ui/gm/rolls.py` uses).
  Renamed to `self_action_changes` and checked no other export collides; the three
  failing files, the phase 6 tests and the sheet's bus / window-flash / conditions /
  recovery / extra-effort / temporary files are green (292 tests). The last 30% (mostly
  `tests/ui/sheet`, `widgets`) is left to phase 7's full run, recommended there anyway.
  Commit: a8566b8.

- **2026-11-08 — Phase 5 (done).** The four attack maneuvers work on a player's
  sheet: the tile starts a line (the picker first when nothing is loaded) at its
  maximum, the roll is made of the shifted attack (modifier, save DC, ladder rank, the
  label restated and the maneuvers named), the lines are spent, and All-Out / Defensive
  land as temporary conditions once the die settles. §3.3 "As built (phase 5)" has the
  names. Also: the GM's three judges now read `defense_class_in_play` (§5 resolved);
  an aimed attack declares the shifted spec (`roll_spec(before=declare)` — the
  declaration is sent from inside the roll, and a failed send calls the roll off and
  puts the chip back). Tests: `tests/core/test_maneuvers.py` (33),
  `tests/ui/dice/test_maneuvers_ui.py` (18). **Decisions I made (small):** (1) the caps
  are checked on the **combined** totals, as §2 says, so the lane re-limits every other
  line when one moves — values are only ever pulled down, which is what stops two lines
  chasing each other; (2) re-binding the **same** attack (an aimed copy included:
  `same_attack` ignores targets / action id / title / actor) keeps the player's value,
  only a different attack resets to the maximum — the old rule also reset when the
  maximum changed, which with combined caps would have reset on every move; (3) the
  line's colour is the tone of the condition it leaves (All-Out red, Defensive green,
  Accurate/Power green); (4) an attack bonus of +0 takes no maneuver at all (it can be
  neither lowered nor doubled), so the default character sees the tiles live but the
  line greyed with the reason; (5) the maneuvers' caption goes in the **label**
  (`… (All-Out Attack +3)`) rather than a new `RollSpec` field, so there is no new wire
  key and nothing to whitelist; (6) Team attack moved to phase 7 (GM-only). **Gotchas:**
  (1) a save's label carries its DC (`Toughness vs. 18`), so shifting the DC restates
  the last whole number in the label; (2) the effect rank is not on the wire and not on
  a non-damage ladder (`ladder.rank` is damage only), hence the local `effect_rank`;
  (3) a refused `roll_spec` must put the chip back **without** the localizer (a second
  pass adds the sheet's number again — the reroll bug in the roller notes);
  (4) `affected.py` takes no pytest flags like `-q -x` (it refuses them) and resolves
  to a full run on this branch while `pyproject.toml` differs from develop. Next:
  phase 6 (the self actions). **Full run: 4682 passed, 2 failed** — both
  `tests/ui/sheet/test_bus.py`, whose own `REQUEST_TOPICS` list (a tripwire for a new
  topic) needed `condition-requested`; fixed and re-run green. Commit: d2bad7c.

- **2026-11-08 — Phase 4 (done).** The roller: one signed Bonus/Penalty slider
  (−20..+20, arrows kept: `make_signed_spin_box(buttons=True)`), the action lane
  (`ui/roller/action_lane.py`) under it, the attack picker, and the Attack and Area
  tiles on the sheet's Dice block (§3.3 "As built (phase 4)"). Core:
  `core/rules/attack_choices.py` (`attack_choices`, `area_choices`). Quick rolls: the
  identity and the saved entries are read **by the net** (`quick_roll_key`,
  `_load_quick_rolls`, `toggle_quick_roll`), so an old "+4 / −1" chip is the +3 it now
  loads. Driver target `roller-lane`. Tests: `tests/core/test_attack_choices.py`,
  `tests/ui/dice/test_action_lane.py` (22); the old two-slider references moved onto
  `_modifier_spin` in `test_dice_roller.py`, `test_roll_routing.py`, `test_gm_window.py`,
  `test_session_player.py`, `test_extra_effort.py`, `test_play_affordances.py`.
  **Gotchas:** (1) **Python's default encoding here is cp1251**: a script that edits a
  file must pass `encoding="utf-8"` as well as `newline=""` (an edit without it mangled
  every em dash in `action_menu.py`; restored from git); (2) `affected.py` refuses
  `-p no:cacheprovider` (testmon needs the cache) — run it plainly; (3) `cat > file`
  with no heredoc blocks the Bash tool on stdin. **Decisions I made (small):** the
  maneuver tiles stay greyed until phase 5 — a line that changed nothing on the roll
  would have been misleading; the no-DC warning covers every unaimed attack (a card's
  🎲 too), since an ungraded attack is the same problem wherever it was clicked;
  dismissing the Attack tile's target menu cancels (Skip is the way to load without a
  target); with nobody on the Scene (or offline) there is no target menu at all; a
  fixed line is as tall as a spin box so the lane never moves; the DC/Routine rows were
  **not** merged into one row as the sketch in §1 shows (left for phase 10 if wanted).
  **Seen in the screenshot:** the chip of a picked attack reads "Rolling: Attack +0 vs.
  Defense" — the card's label, which does not name the power (with two attacks the
  chip can't tell them apart; the picker's caption does). Next: phase 5 (the four
  maneuvers: `maneuver_limits`, `apply_maneuvers`, `lasting_conditions`, wired through
  `start_action_line`). Full run: **stopped at 82% by the system (low memory)**, no failure up to there; the
  rest is covered by the targeted files (dice, sheet roller, extra effort, play
  affordances, window flash, the GM and session tests I edited — all green). Phase 7's
  full run (recommended there anyway) covers the remaining 18%. Commit: 846e626.

- **2026-11-08 — Phase 3 (done).** Asked the user two questions, now decisions 11
  (four sections) and 12 (the Attack tile: attack, then optional target with "Skip"
  first; DC = Defense; warning when neither — built in phase 4). Data: `actions.json`
  `combat` (menu, limits, 29 actions, paraphrased tooltips, provenance) and the loader
  records (§3.1 "As built"). `ui/action_grid.py` (the reusable popup),
  `ui/action_menu.py` (sections from data + what the host offers),
  `ui/svg_assets.py` (`action_icon_resource`, `tinted_svg_pixmap`,
  `action_icon_pixmap`), 30 SVGs in `ui/assets/actions/` (incl. `generic.svg`; added to
  `pyproject.toml` and `installer/mm_companion.spec`), tokens `action-grid.*` in
  `classic.json` + `token_meta.json`. The roller's Action row → one **Actions** button
  (`set_action_menu`, `open_actions`, `own_actions`, `can_reroll`). GM: Impress, Area,
  and the team tiles (greyed until a group exists; several groups → a `QMenu` chooser).
  Theme fix: `QToolButton:disabled` in the styled presets. Driver targets
  `actions-popup` / `actions-popup-gm`. Tests: `tests/core/test_combat_data.py`,
  `tests/ui/widgets/test_action_grid.py`, `tests/ui/dice/test_actions_button.py`,
  `tests/ui/gm/test_actions_popup_gm.py`, a qss rule test; `test_dice_roller.py` and
  `test_impress_ui.py` moved off the old row. **Gotchas:** (1) `DiceRollerView().panel`
  in a test lets the view be collected and its widgets deleted — hold the view;
  (2) `monkeypatch.setattr(QMenu, "exec", …)` does **not** reach a PySide instance
  (the existing GM test that does it only asserts it is *not* called) — patch the
  module's `QMenu` name with a stand-in class instead; (3) on the styled presets a
  disabled tool button painted like a live one (fixed in qss). **Decisions I made
  (small):** the player's Area tile stays greyed until phase 4 wires it (a player never
  had Area on the roller); the Actions button is disabled while the die tumbles (it is
  in `_input_widgets`); a group tile with one group skips the chooser. Next: phase 4
  (the slider, the lane, the attack picker, the Attack tile). **Not run:** the full
  affected-tests set was stopped by the machine running low on memory; the targeted
  files (popup, roller, GM, Impress, theme, packaging, data — 500+ tests) are green.
  Phase 4's full run covers it. Commit: 8186045.

- **2026-11-08 — Phase 2 (done).** Dots: `ui/stat_mark.py` (`StatMark`,
  `StatMarkDelegate`, `mark_item`, `MARK_ROLE`, token `mark.size`), on the Resistances
  and Skills totals (delegate on the Total column), on the power/gear cards' attack line
  (`RollsFooter(marks=…)`, driven by the new `RollSpec.marks`), and on the simple sheet's
  resistance boxes and skill lines. The overlay tint follows its sign
  (`ConditionEffect.better`), fixing phase 1's red Defending. Chips: `condition_tone_token`
  everywhere, ✕ on temporary chips (sheet, simple sheet, GM cards), temporary line in the
  tooltip. "Clear temporary": the block header (`clear_temporary_conditions`, history note
  via the block's new `noteRequested` → `NOTE_REQUESTED`), the simple sheet, the NPC card
  (`_clear_npc_temporary`, replays onto an open sheet), the player card
  (`_clear_player_temporary` → `bridge.clear_temporary`) and the Scene card
  (`_clear_scene_temporary`). The "+" menu asks for an amount (`prompt_condition_amount`,
  `maxAmount: 5` on the two maneuvers). **Protocol v20** landed here (see the phase 1 wire
  findings): later phases add to v20 without bumping. Tests: 7 new in
  `tests/core/test_temporary_conditions.py`, `tests/ui/sheet/test_temporary_indicators.py`
  (15), `tests/ui/gm/test_temporary_gm.py` (9), protocol/server/session-player cases.
  **Gotchas:** (1) `RollSpec.marks` must be `compare=False` — a spec round-tripped through
  `to_dict` is compared for equality in tests and in the history; (2) closing an NPC
  sheet window in a test after a GM change prompts "unsaved changes" and hangs —
  leave it to the conftest teardown; (3) a scratch script that edits files still has to
  live in the scratchpad (backticks in heredocs). **Decisions I made (small):** ✕ only on
  temporary chips; the cards' "Clear temporary" under the chips rather than on the button
  row (so a card never widens); no System-block dot yet; the "+" amount prompt starts at 1.
  Next: phase 3 (the grid popup, icons, the Actions button). Commit: 1bd595a.

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
  2). Next: phase 2. Commit: 495dd33.

- **2026-11-08 — Phase 0.** Rules checked against the book (p200–205). The user's
  answers recorded in §1. The plan and the `/combat-maneuvers` skill written. Branch
  `feature/combat-maneuvers` created off `develop` (f8084e2). Next: phase 1.
