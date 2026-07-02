# Mutants & Masterminds (4th Edition) — Core Mechanics Reference

> **Note on source & copyright:** This document is an original, paraphrased summary of the
> *functional rules* (the math, formulas, and procedures) of Mutants & Masterminds 4th Edition,
> written for the purpose of implementing a rules engine in code. It does **not** reproduce the
> rulebook's text, tables, flavor examples, or specific stat blocks. The M&M 4e rulebook itself
> is commercial, copyrighted material (the reviewed PDF is an "Origin Edition" playtest release
> explicitly marked as *not* Open Content and *not* under any open license), so this file
> deliberately sticks to game-mechanical facts — numbers, formulas, procedures — which are not
> copyrightable, rather than the book's creative expression. If you build tooling from this file,
> keep any in-app flavor text, skill/power/advantage write-ups, and example tables original to
> your own project rather than copied from the book.

---

## 1. The Core Dice Mechanic

Every uncertain action resolves the same way:

```
check_result = d20_roll + check_modifier
success = check_result >= difficulty_class (DC)
```

- **d20_roll**: a single roll of a 20-sided die (1–20).
- **check_modifier**: sum of the relevant ability rank, skill rank, combat ability rank,
  power/effect rank, and any situational bonuses/penalties.
- **DC (Difficulty Class)**: a target number set by context — a fixed table value for
  non-opposed tasks, or `10 + a relevant trait rank` for checks made against another
  character (attacks, resistances, etc.).

### Bonuses & Penalties
- Typically range from ±1 to ±5; net modifiers stack additively.
- ±2 = "minor" modifier, ±5 = "major" modifier (these are just common values, not hard limits).

### Routine Checks
When there's no time pressure and no risk, a check can be treated as "routine": skip the die
roll and treat the roll as if it were exactly **10**, then apply modifiers normally. This
guarantees success on anything with DC ≤ 10 + net modifier, but forfeits any chance at a
higher degree of success.

```
routine_result = 10 + check_modifier
```

---

## 2. Degrees of Success and Failure

For "graded" checks (where *how well* you succeed/fail matters), compare the margin between
the check result and the DC in increments of 5:

```
margin = check_result - DC

if margin >= 0:
    degrees_of_success = 1 + floor(margin / 5)      # 1 at margin 0-4, 2 at 5-9, 3 at 10-14...
else:
    degrees_of_failure = 1 + floor((-margin - 1) / 5)  # 1 at margin -1..-5, 2 at -6..-10...
```

Equivalently, in DC-relative bands:
| Result vs DC | Outcome |
|---|---|
| DC+15 or more | 4 degrees of success |
| DC+10 to DC+14 | 3 degrees of success |
| DC+5 to DC+9 | 2 degrees of success |
| DC to DC+4 | 1 degree of success |
| DC-5 to DC-1 | 1 degree of failure |
| DC-10 to DC-6 | 2 degrees of failure |
| DC-15 to DC-11 | 3 degrees of failure |
| DC-20 to DC-16 | 4 degrees of failure |

### Natural 20 / Natural 1 (Added Success / Added Failure)
On a **graded** check:
- Rolling a natural **20** on the die adds **one extra degree of success** to whatever the
  check would otherwise achieve (can flip a failure into a success). On an attack check this
  is a **critical hit**.
- Rolling a natural **1** subtracts **one degree of success / adds one degree of failure**
  (can flip a success into a failure). On an attack check this is a **critical miss**.

Implementation note: apply the raw margin calculation first, then adjust the resulting degree
by ±1 if the natural die result was 20 or 1, respectively.

---

## 3. Difficulty Classes

Two ways a DC is determined:

1. **Fixed/GM-set DC** — a flat number chosen for the task, generally in the range 0–40,
   scaled in increments of 5 to represent easy → nearly-impossible tasks.
2. **Derived from a target's trait** — most commonly `10 + target's Defense rank` (attacks)
   or `10 + effect rank` (resistance checks). See sections 5 and 7.

### Opposed Checks
Instead of a fixed DC, both participants roll a check and compare results directly:

```
winner = participant with higher check_result
# tie-break: higher check_modifier wins; if still tied, resolve randomly
```

### Team Checks
Helpers each roll a check against **DC 10**; every degree of success they achieve becomes a
**+1 bonus** for a designated "leader," who then makes the actual check with that bonus added.

### Group Checks
When a group all attempt the same check together, everyone rolls; if **at least half** the
group succeeds, the *entire group* is treated as succeeding (at the lowest degree of success
achieved among the successful members). Any individual added success/failure (nat 20/1) still
applies to that individual afterward.

### Check Sequences
For extended, multi-check tasks: define a **success threshold** and **failure threshold**
(cumulative degrees of success/failure needed), plus a set of required checks. Accumulate
degrees of success/failure across checks in the sequence until one threshold is reached first.

---

## 4. Abilities

Six core abilities, each with an integer **rank** (0 = human average; negative = below
average; positive scales toward superhuman):

| Ability | Abbrev. | Domain | Typically governs |
|---|---|---|---|
| Strength | STR | Physical | Melee damage, lifting/carrying, Athletics |
| Stamina | STA | Physical | Fortitude & Toughness resistance, endurance |
| Agility | AGL | Physical | Initiative, Acrobatics/Stealth/Sleight of Hand/Vehicles |
| Intellect | INT | Mental | Knowledge/technical skills |
| Awareness | AWE | Mental | Will resistance, Perception/Insight/Survival |
| Presence | PRE | Mental | Deception/Persuasion/Intimidation/Performance |

**Rank range:** roughly -5 (severely disabled) to +5 (peak human) to +20 or more (cosmic).

**Cost to raise:** each +1 rank costs a fixed amount of Power Points per rank (see §6).
Lowering a rank below 0 refunds points, down to a minimum rank.

Ability ranks feed directly into checks (`check_modifier` includes the relevant ability rank)
and into derived stats (resistances, initiative, etc. — see below).

---

## 5. Combat Abilities

Two additional ranked traits, distinct from the six core abilities, used specifically in
combat:

- **Attack** — the modifier added to attack checks.
- **Defense** — determines a character's **Defense Class**, the DC opponents must beat to
  hit them:

  ```
  defense_class = 10 + defense_rank
  ```

- **Initiative** — not independently purchased; derived as `agility_rank + misc bonuses`.
  Used to determine turn order (see §8).

Both Attack and Defense start at rank 0 and are purchased with Power Points like abilities.

---

## 6. Resistances

Four resistance tracks, each tied to a core ability but independently improvable:

| Resistance | Based on | Resists |
|---|---|---|
| Dodge | Defense rank | Reflex/evasion-based hazards |
| Fortitude | Stamina | Poison, disease, physical strain |
| Toughness | Stamina | Direct damage |
| Will | Awareness | Mental/emotional effects |

**Resistance check:**
```
resistance_result = d20 + resistance_rank
effect_dc = 10 + effect_rank
```
This is typically a **graded** check — see §2 for degree calculation, and §9 for how degrees
of failure map to conditions on a Damage resistance check specifically.

### Special Resistance Categories
A character's resistance to a *specific* effect can be shifted along a six-step scale:

| Category | Mechanical effect |
|---|---|
| Immune | No check needed; effect simply doesn't apply |
| Hardened | Roll resistance check twice, keep the higher result (for effects at/under the hardened rank) |
| Resistant | Halve the effect's rank (round up) before computing the DC |
| Normal | Standard resistance check, no modification |
| Susceptible | Penalty on the check equal to half the effect rank (round up) |
| Weakness | Same penalty as Susceptible, **and** the best possible outcome is capped at 1 degree of failure |

---

## 7. Power Points, Power Level, and Character Creation Budget

Characters are built by spending a pool of **Power Points (PP)** across traits. A campaign's
**Power Level (PL)** sets both the starting PP budget and caps on how high various combined
traits can go.

### Starting Power Points
Roughly linear: `starting_PP = power_level * 15`, tabulated per level by the GM (defaults to
PL 10 → 150 PP, but the GM can vary this).

### Basic Trait Costs
| Trait | Cost |
|---|---|
| Ability rank | 2 PP per rank |
| Combat ability rank (Attack/Defense) | 2 PP per rank |
| Resistance rank (above the ability-derived base) | 1 PP per rank |
| Skill rank | 1 PP per 2 ranks (1 PP per 4 ranks if "specialized") |
| Advantage | 1 PP per advantage / per rank in a rankable advantage |
| Power | `base_effect_cost + modifiers` per rank × rank, plus flat modifiers |

Lowering an ability, combat ability, or resistance rank below its starting value **refunds**
PP that can be spent elsewhere, down to a rank floor (typically -5).

### Power Level Caps
PL doesn't just set the starting budget — it also caps combined totals, roughly:

```
max_skill_modifier        <= power_level + 10
max_attack + effect_rank  <= power_level * 2
max_defense/dodge + toughness <= power_level * 2
max_fortitude + will       <= power_level * 2
max_heroic_advantages       ~ power_level / 2  (rounded, roughly)
```

(Exact table values scale in a regular step pattern per PL; implement as a lookup table keyed
by PL rather than hardcoding the formula if precision matters, since the book publishes the
per-level numbers directly.)

### Character Creation Flow (for a build wizard / character generator)
1. Pick a concept.
2. GM sets Power Level (and thus starting PP + caps).
3. Spend PP on: abilities → combat abilities (Attack/Defense) → resistances → skills →
   advantages → powers, respecting PL caps at each step.
4. Assign complications/motivation (narrative hooks, not point-costed).
5. Validate: total PP spent should equal the starting PP budget, and every derived value
   should respect the PL caps.

---

## 8. Rounds, Turns, Actions, and Initiative

### Time Scale
- **Round** = 6 seconds (the base unit of "action time"). 10 rounds ≈ 1 minute.
- Outside of tactical scenes, time passes narratively/unstructured ("scene time").

### Initiative
At the start of a tactical scene, every participant rolls:
```
initiative_result = d20 + agility_rank + misc_bonuses
```
Turn order = descending `initiative_result`. Ties break by: higher initiative modifier →
higher Agility → higher Awareness → random (die roll), in that order.

### Turn Structure (per character, per round)
On their turn a character may take, in any order:
- **One standard action** — a full, complex action (attack, use a power, most skill uses).
  Can be traded for a second simple action instead.
- **One simple action** — a lighter action (move, stand up, draw an item, etc.).
- **Any number of free actions**, at GM discretion (talk, drop an item, etc.).
- **One reaction per round** — usable off-turn, in response to a trigger; refreshes at the
  start of the character's next turn.

### Delaying
A character may voluntarily act later than their rolled initiative would allow, permanently
moving to that lower spot in the order for the rest of the scene (unless they delay all the
way to the end of the round, in which case they act first next round).

---

## 9. Attacks, Damage, and Combat Resolution

### Attack Check
```
attack_result = d20 + attack_modifier
target_dc = 10 + target_defense_rank      # "Defense Class"
hit = attack_result >= target_dc
```
This is a graded check — degrees of success/failure apply per §2, with an extra rule:

- **Critical hit** (natural 20 that also hits, or any nat-20 added success): the attack's
  effect rank is increased by **+5** for the purpose of the follow-up resistance check.
- **Critical miss** (natural 1 that causes a hit to become a miss, or any nat-1 added
  failure on a hit that still connects): the target gets a **+5 bonus** on their resulting
  resistance check.

### Damage Resistance Check
On a successful hit with a Damage effect, the target rolls a resistance check (typically
Toughness) against the attack's effect rank:

```
resist_result = d20 + toughness_rank
effect_dc = 10 + damage_rank
```

Outcome (graded, mapped to escalating **conditions** rather than raw "hit points"):

| Degrees | Result |
|---|---|
| 2+ degrees of success (with Hardened/Impervious/Impenetrable resistance vs. this attack) | No condition |
| 1 degree of success (or lower success without the above) | **Hit** condition (cumulative -1 penalty to further Damage resistance checks) |
| 1 degree of failure | Hit + **Dazed** (or **Stunned** if already Dazed) |
| 2 degrees of failure | Hit + **Stunned** + **Staggered** |
| 3+ degrees of failure | Hit + Staggered + **Incapacitated** (escalates to **Dying** → **Dead** on further failed checks) |

M&M uses this **condition ladder** instead of a hit-point pool: characters accumulate
conditions (Hit, Dazed, Stunned, Staggered, Incapacitated, Dying, Dead) that progressively
restrict their actions rather than losing numeric "HP." A rules engine should model this as a
state machine per character rather than a simple integer health value.

### Cover & Concealment (attack modifiers)
- **Partial concealment**: -2 to the attacker's check.
- **Full concealment**: -5 to the attacker's check (and may block targeting entirely without
  a successful Perception check).
- **Partial cover**: +2 Defense/Dodge for the defender.
- **Full cover**: +5 Defense/Dodge for the defender.
- **Total cover**: cannot be targeted by direct attacks at all.

### Two Special Defense States
- **Vulnerable**: target's Defense is halved (round down) for DC purposes.
- **Defenseless**: target's Defense is treated as 0 (attackers can even use a routine check
  to hit automatically).

---

## 10. Suggested Data Model (for implementation)

```
Character
├── abilities: { str, sta, agl, int, awe, pre } -> rank (int)
├── combat: { attack, defense } -> rank (int)
├── resistances: { dodge, fortitude, toughness, will } -> rank (int, base = linked ability)
├── initiative_bonus: int
├── skills: { skill_name -> rank (int) }
├── advantages: [ { name, rank? } ]
├── powers: [ { name, effect_type, rank, modifiers[] } ]
├── conditions: [ enum: hit, dazed, stunned, staggered, incapacitated, dying, dead, ... ]
├── power_points_total: int
└── power_level: int

CheckResult
├── die_roll: int (1-20)
├── modifier: int
├── total: int
├── dc: int
├── degree: int (signed: + success, - failure)
├── critical: bool
```

Core reusable function:
```
def resolve_check(modifier: int, dc: int, graded: bool = True, roll: int | None = None) -> CheckResult:
    d = roll if roll is not None else random_d20()
    total = d + modifier
    margin = total - dc
    if not graded:
        return CheckResult(d, modifier, total, dc, degree=(1 if margin >= 0 else -1), critical=False)
    degree = 1 + margin // 5 if margin >= 0 else -(1 + (-margin - 1) // 5)
    if d == 20:
        degree += 1
    elif d == 1:
        degree -= 1
    return CheckResult(d, modifier, total, dc, degree, critical=(d in (1, 20)))
```

---

## 11. Out of Scope for This File

To keep this reference focused on *base mechanics* (suitable for a dice/resolution engine and
character math), the following are **intentionally omitted** and would need their own
original write-ups if implemented, rather than being copied from the source book:

- Full skill list and per-skill rules text.
- Full advantage list and descriptions.
- Full powers/effects catalog (Comprehend, Damage, Movement, etc.) and their point costs.
- Equipment/vehicle rules and stat blocks.
- The complete condition glossary (Asleep, Compelled, Controlled, Prone, Surprised, etc.) —
  only the damage-condition ladder directly needed for combat resolution is included above.
- Sample characters/archetypes and setting material.

These are large, creatively-written content blocks best treated as *data* your project defines
itself (e.g., a `skills.json`, `powers.json`) rather than transcribed from the rulebook.
