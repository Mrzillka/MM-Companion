# Minions

Matters when touching the minion flag, the Session settings page, the damage row on a
minion's card, or anything that resolves a check against a creature that might be one.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## The rules, and where each one lands

A **minion** (core book, PDF p25) is a minor NPC the rules make easier to defeat. The
GM marks one with `Character.minion`, and the rules below then apply. Each one is a
switch in `core/rules/minions.py`'s `MinionRules`, and every switch defaults to on
(the book).

| Rule (book) | Switch | Where it is applied |
| --- | --- | --- |
| A minion's natural 20 is no added success (p25) | `no_added_success` | `resolve_check(added_success=False)`; `RollRequest.minion` → `RollRecord.minion`; a group line's `minion`; `follow_up_for_result(added_success=)` reads the record so the save chip gets no crit DC |
| A failed save is the worst degree (p25) | `worst_degree` | `targeting.outcome_rung(worst=)` via `minions.minion_rung`, called by `answering.answer_rung`, `impress.impress_rung` and `area_attack.area_effect_rung`; the card's damage row becomes ✓ / ✗ |
| *(table choice)* stop at Incapacitated | `stop_at_incapacitated` | `minions.capped_escalation` cuts every escalation pair from `system.minions.ceiling` onwards, and the rung's note goes with it |
| A critical hit lands the worst, no save (p197) | `crit_auto_fails` | `AnswerContext.critical` → the history menu offers "💥 Critical: …, no save" (`GMWindow._crit_minion`); an area's minion primary (`AreaController._crit_minion`) |
| Attacks on minions may be routine (p25) | `routine_attacks` | `AreaWindow.routine_attack`: Hit is judged from the better of the roll and 10 + attack, and a routine hit needs no roll |
| Impress minions as a routine check at +5 (p201) | `routine_impress` | `ImpressController._routine_against` / `_target_dc`: each minion resists `routine_impress_dc`; an all-minion group rolls no check at all |
| *(table choice)* players see the badge | `show_players` | `_push_scene` strips the scene entry's `minion` key off the wire (the GM's own board keeps it) |

The two numbers are data, in `system.json`'s `minions` block: `impressBonus` (5) and
`ceiling` (`incapacitated`). Which switches are on is the session's, not data.

## Decisions the user made (2026-10-27)

- **Full automation, plus take-10 on the roller.** The roller's **Routine (take 10)**
  box is for any check, not only checks against minions, and it is **one-shot**: it
  unticks after each roll, so nobody takes 10 against a villain by accident. A routine
  roll travels as `RollRequest.routine`, the server sets the die to `ROUTINE_DIE`
  (never natural), the history reads "routine 10", and it cannot be rerolled.
- **Players see a badge** on a minion's scene card (protocol v18's scene `minion` key).
- **A minion stops at Incapacitated.** The book says a minion "can be killed simply
  with a successful attack and a failed resistance check, if the attacker wishes" (PDF
  p200). The table chose to stop at Incapacitated and mark Dead by hand.
- **Every rule is a GM switch on a "Session" settings page**, and it applies only to
  the open session:
  - a new session starts from the book;
  - with no session open, the page is greyed out and the book applies.

## Why the settings live on the session, not in `storage`

"Only this session" means three things: the switches survive a reconnect and a hub
restart, a joiner gets them, and a second session does not inherit them. The session's
shared-state channel already does all three. `TABLE_MOD_ID` `"mm-companion.table"`,
key `"session"`, holds `{"minions": {...}}`: stored in `SessionState.mod_state`,
persisted with the session, and replayed in every `Welcome`. So no new message was
needed.

- `ui.action_ui.session_minion_rules(bridge)` reads them. It returns the book when
  `bridge.in_session` is false: a stopped hosting server still holds its old state, and
  reading that would carry one session's rules past its end.
- `publish_minion_rules` writes them and keeps any other key in the entry, so a later
  session setting can sit beside them.
- **Players read nothing from it.** The GM's app applies every rule, and the badge is
  simply the GM leaving the key off the scene.

## The page

- `ui/settings/session_page.py` `SessionPage` is built bound to a bridge. **Save
  publishes** to the session and writes nothing local.
- `SettingsWindow(extra_pages=…)` appends it. Only the GM window passes one: Settings ▸
  Preferences… and the new Settings ▸ Session settings…, which opens on that page. A
  sheet's Preferences never shows it.
- The page reloads when the session's state moves (another GM seat, a rejoin), unless
  it holds unsaved edits.

## Where the flag is set

- **The NPC card:** a checkable **Minion** button beside Copy, a "Make a minion" / "Make
  a full NPC" menu entry, and "· minion" on the Power Level line, which a collapsed
  card shows too.
  - `GMWindow._set_npc_minion` takes the condition-change road: it replays onto an
    open sheet under `undo.absorbing` (setting the model and calling
    `system_info.reseed`), or saves the file.
- **The NPC sheet:** a **Minion** box in the System block, beside Estimated PL, shown in
  NPC mode only. It is an ordinary undoable edit.
- **Quick NPC:** a **Minion** box, remembered for the next one this run.
- **Copy** keeps the flag, since it copies through `to_dict`.

`to_dict` writes `minion` only when it is true, so every older save, and every
player's sheet, round-trips byte for byte.

## Rolling as a minion

Whatever rolls as an NPC tells the roller: `DiceRollerPanel.roll_spec(spec,
minion=)` / `load_spec(…, minion=)`. The flag goes with the spec and is forgotten with
it. These paths pass it, through `GMWindow.rolls_as_minion`:

- the NPC sheet's rolls (`_on_npc_request`);
- a card's pinned tiles (`_npc_roll`);
- history answers (`_roll_as`);
- the area window's NPC attacker (`area_roll_attack`);
- every Impress and area group line (`group_line(minion=)`).

A minion's save also travels with its outcome list cut to the worst rung
(`minion_save_spec`), so every seat's card reads what the GM's table applies, with no
extra spec key.

**A minion's 20 is suppressed whatever its target.** The rule says "against
non-minions", but the die is thrown before anyone knows who it lands on, and minions
fight heroes. A minion attacking another minion is the one case this rounds the wrong
way.

## Not done

See [the debts](debts.md): Takedown's chained attacks, the menace rules, Assess counting
identical minions as one opponent, and Summon or Minion-advantage builds becoming
creatures at the table.
