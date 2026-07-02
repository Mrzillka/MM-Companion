# Mutants & Masterminds (4th Edition) — Actions & Adventure Reference

> **Note on source & copyright:** Same basis as `mm-core-mechanics.md` — this is an original,
> paraphrased summary of the *functional* rules (formulas, procedures, numeric tables) from the
> Action & Adventure chapter, written for implementing game logic. It does not reproduce the
> rulebook's prose, flavor text, or examples. Companion file to `mm-core-mechanics.md` and
> `conditions.json`; read those first for the base resolution mechanic and the condition catalog
> referenced throughout this document.

---

## 1. Action Catalog

Every action a character can take on their turn (see `mm-core-mechanics.md` §8 for the
standard/simple/free/reaction action-type framework). Each entry: **type**, the **check**
involved (if any), and what a **success** does.

| Action | Type | Check | Result |
|---|---|---|---|
| Aid | Standard | Attack vs. DC 10 | 1 DoS: ally gets +2 to an attack or Defense vs. the target; 3+ DoS: +5 instead |
| Aim | Standard | none | Roll twice on your next attack this turn, keep the better result |
| Attack | Standard | Attack vs. Defense | See §3 (Attack Checks) in `mm-core-mechanics.md` |
| Block | Standard | Attack vs. Attack | 1+ DoS: the target's attack is blocked |
| Catch | Reaction | Agility vs. DC 10 | Catch a falling object within reach |
| Charge | Standard | Attack -2 vs. Defense | Move up to speed, then make a close attack |
| Command | Simple | none | Direct a character under your control |
| Concentrate | Standard | none | Maintain focus on an ongoing effect |
| Defend | Standard | none | +2 Defense until the start of your next turn |
| Disarm | Standard | Attack -2 vs. Defense | Opposed by target's Strength; 1+ DoS disarms them |
| Drop Prone | Free | none | Gain the Prone condition voluntarily |
| Escape | Simple | Strength or Sleight of Hand vs. hold DC | 1 DoS: hold ends; 2+ DoS: also move at speed -1 |
| Feint | Standard | Attack, Deception, or Insight | 1+ DoS: target is Vulnerable to your next attack |
| Grab | Standard | Attack vs. Defense, then Strength vs. Strength/Dodge | 1 DoS: partial hold; 2+ DoS: full hold |
| Impress | Standard | Interaction skill vs. same skill/Insight/Will | Success imposes a condition (see §5) |
| Move | Simple | none | Move up to your speed; DC 15 Athletics for +1 speed rank |
| Observe | Simple | Perception | Notice something previously missed or study something closely |
| Ready | Standard | none | Prepare an action to trigger later in the round |
| Recover | Standard | DC 10 Stamina | Recover from a damage condition; +2 Defense while doing so |
| Smash | Standard | Attack -2 vs. Defense (-5 for held/small objects) | Deal damage to a held/worn object |
| Stand | Free | DC 20 Acrobatics (or accept being Hindered) | Remove the Prone condition |
| Sustain | Free | none | Keep a Sustained-duration effect active |
| Trick | Standard | Deception vs. Deception/Insight | Success: target is unaware of a specific danger |
| Trip | Standard | Attack vs. Defense, then Attack/Strength vs. Strength/Dodge | 1+ DoS: target becomes Prone |
| Use Effect | Varies | Varies | Activate a power effect per its own rules |

---

## 2. Attack Maneuvers

Optional trade-offs a character can apply when declaring an Attack action. Each shifts up to
5 points from one value to another; the source can't go below 0 and the target can't more
than double from its base value. Opposite maneuvers cancel out if combined.

```
accurate_attack:   attack_check += X   (0 <= X <= 5)   effect_rank -= X
all_out_attack:    attack_check += X   (0 <= X <= 5)   defense     -= X
defensive_attack:  defense      += X   (0 <= X <= 5)   attack_rank -= X
power_attack:      effect_rank  += X   (0 <= X <= 5)   attack_check -= X
```

Maneuver bonuses/penalties last until the start of the character's next turn, and only apply
to values the Attack action actually uses (e.g. a maneuver modifying the attack check does
nothing for an attack with no attack roll, like a Perception-range effect).

### Finishing Attack
Normally an attack against a Defenseless target auto-hits (routine check). Choosing to roll a
full attack check against DC 10 instead means any hit counts as a **critical hit**; if the
attack is meant to kill and the resistance check fails by 3+ degrees, the target is Dead
rather than just Incapacitated/Dying.

### Surprise Attack
An attack against a target who is Vulnerable due to being caught off guard (via Stealth,
Concealment, or a successful Feint) gets an increased effect — treat it like a bonus similar
to a critical hit, at GM discretion.

### Team Attack
Multiple attackers combine attacks that share the same effect type and resisted-by
resistance, with effect ranks within 5 of each other:

```
primary_attack = the hit with the largest effect rank
for each other hit that connects:
    total_extra_degrees += that attack's degrees of success
if total_extra_degrees >= 1: primary_attack.effect_rank += 2
if total_extra_degrees >= 3: primary_attack.effect_rank += 5   # (replaces the +2, not additive)
```
All participating attackers must Ready to the same point in the initiative order (the
slowest attacker's turn) and each rolls their own attack check against the target's Defense
Class.

---

## 3. Recovery & Death

### Recovery Check
Damage conditions clear from least to most severe: **Hit → Staggered → Incapacitated**.

```
every 1 minute of rest:
    recovery_result = d20 + stamina_rank + cumulative_failed_attempts_bonus
    if recovery_result >= 10:
        remove the least-severe damage condition
    else:
        cumulative_failed_attempts_bonus += 1   # try again next minute
```
Recovery checks cannot be taken as routine checks (must actually roll). Objects and
characters without a Stamina rank don't recover on their own — they need repair or outside
help instead.

### Recovering from Dazed / Stunned
```
at the end of the character's next turn after gaining Dazed/Stunned:
    check = d20 + fortitude_rank vs. DC 10
    success -> condition removed; failure -> condition remains, check again next turn
```

### Dying and Death
```
on gaining the Dying condition:
    check = d20 + stamina_rank vs. DC 10
    2+ degrees of success -> stabilizes (becomes Incapacitated)
    success (1 degree)     -> no change, remains Dying
    failure                -> remains Dying, check again at the start of every turn
    3+ cumulative degrees of failure -> Dead
```
A DC 15 Treatment check from another character can also stabilize a Dying character. A
Finishing Attack (see §2) can kill a Dying character outright on a bad resistance check.
Minions skip all of this — a failed resistance check simply takes them out (GM's choice of
outcome), no death spiral needed.

---

## 4. Combat State-Machine Notes (for implementation)

Model a character's combat status as an explicit condition list rather than a numeric
health pool (see `conditions.json` for the full catalog and what each one does):

```
Character.conditions: Set[ConditionId]

on_damage_resistance_result(character, degree):
    if degree >= 2 (success):        pass  # no new condition (assuming no Hardened/etc. already handled)
    elif degree == 1 (success):      add(character, "hit")
    elif degree == -1 (failure):     add(character, "hit"); add_or_upgrade(character, "dazed")
    elif degree == -2 (failure):     add(character, "hit"); add(character, "stunned"); add(character, "staggered")
    elif degree <= -3 (failure):     add(character, "hit"); add(character, "staggered"); add_or_upgrade(character, "incapacitated")
```
`add_or_upgrade` should check the condition catalog's `supersedes` field — e.g. adding Dazed
to an already-Dazed character should instead apply Stunned (which supersedes it), and adding
Incapacitated to a Dying character should escalate per §3 instead.

---

## 5. Impress Checks (Social/Non-Combat Influence)

An **Impress** action lets a character use an interaction skill to impose a condition on a
target rather than dealing damage — the social equivalent of an attack.

```
impress_check = d20 + interaction_skill_modifier
opposed_by = target's check with the same skill, Insight, or Will — whichever is highest
```

### Situational Modifiers (representative, not exhaustive)
| Modifier | Situation |
|---|---|
| -5 | Impressing as a simple action instead of standard |
| -5 (cumulative) | Each prior Impress attempt against the same target this scene |
| -1 to -5 | Attempt clashes with the mood, or attacker is at a disadvantage |
| +1 to +5 | Attacker has a clear advantage, does something intimidating, or plays the moment well |
| +1 to +5 | Attempt matches the scene's mood |
| +2 | Target is Surprised or already hurt/impaired |
| +5 | Taking a full action instead of standard |

- **Quick Impression**: -5 penalty, but usable as a simple action.
- **Group Impression**: -5 penalty, targets everyone in a group with one roll (each target
  still rolls their own resistance check).
- **Impressing Minions**: treated as a routine check with a +5 bonus; minions who fail take
  the worst possible outcome of the impress effect, same as with Damage.
- A natural 20 is an added success (per the core degree-of-success rules); ties/near-ties are
  resolved the same way as any opposed check.

---

## 6. Challenge Sequences (Extended Non-Combat Tasks)

These are applications of the generic **Check Sequence** mechanic (`mm-core-mechanics.md`
§3) to common scenarios. Each is a reusable template: goal, time interval per check, which
checks are typically involved, and success/failure thresholds.

| Template | Typical time interval | Success threshold | Failure threshold |
|---|---|---|---|
| Averting a disaster | 1 round – 1 minute+ | 3 (minor) to 8+ (major) degrees of success | Same number of intervals as the success threshold |
| Chase / pursuit | 1 round+ | 6+ degrees of success to catch/escape | 6+ degrees of success for the opposition |
| Race | 1 round+ | Most degrees of success when intervals run out | — |
| Escaping a trap | 1 round – 1 minute | 5 degrees of success | Fixed number of intervals (GM sets) |
| Preventing a vehicle crash | 1 round | 5 degrees of success | 3+ degrees of failure |
| Technical fix under pressure | 1 round – 1 minute | 5+ degrees of success | Fixed number of intervals (GM sets) |

General notes:
- A significant speed advantage in a chase/race grants +1 per rank of Speed advantage, up to
  +5.
- Failing a check mid-sequence doesn't always end it — it typically costs time/adds
  complications rather than being instant failure, unless it pushes past the failure
  threshold.
- A failed vehicle-control-relevant check inside a sequence usually triggers a **control
  check** (vehicle-handling resistance check) rather than being resolved directly by the
  sequence math.

---

## 7. Out of Scope for This File

Kept out to stay focused on general-purpose action/adventure mechanics (same rationale as
`mm-core-mechanics.md` §11):

- Full environmental hazard rules (falling, drowning, extreme heat/cold, radiation, etc.) —
  worth their own reference if you implement hazard scenes.
- Vehicle operation/combat specifics and the Control Check table.
- The complete Attitude/Interaction-scene subsystem beyond the Impress check formula above.
- Cover/Concealment tables and ranged-attack range bands — already covered in
  `mm-core-mechanics.md` §9 to avoid duplication.
- Sample menace/minion tactical guidelines beyond what's already noted for damage resolution.

As before, treat these as project-specific data/design decisions rather than material to copy
from the source book.
