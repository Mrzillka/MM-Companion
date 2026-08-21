# Accepted debts

Work that has been agreed and **not yet done**. It is here rather than in an issue
tracker for the reason the rest of `docs/notes/` exists: the useful half of a debt is
*why* it is owed and what the current shape gets wrong, and that is worth reading before
touching the area rather than after.

Take one off this list by doing it, and delete its entry in the same commit.

## Increased Action should be takeable more than once

`increased_action` in `src/mm_companion/data/modifiers.json` carries
`"stepField": "action"`, `"stepBy": 1` and is **not** `ranked`, so it can only ever move
an effect's action one step along the `gameTermLadders` ordering. The rules let a flaw
like this be taken repeatedly — a standard action to a move action to a full action —
and the build has no way to say so.

It wants `"ranked": true` with a sensible `maxRank`, `stepBy` multiplied by the chip's
rank where the step is applied, and nothing else: the modifier chip already grows a `×N`
spin box for a ranked modifier (`ui/power_constructor/modifier_chip.py`), so the control
arrives for free once the data says it is ranked. **Check `increased_duration` beside
it** — it has exactly the same shape and the same limitation.

Watch the cost: `costValue` is per rank already, so a ranked version prices itself, but
the step has to multiply or two ranks would cost twice and move once.

## The Resistances table should show ability, rank and total

The Resistances block shows one number per row. It should show three, because three
different things move it and a single total says nothing about which:

- **Ability** — the ability the resistance derives from (Fortitude from Stamina, and so
  on). An **Enhanced Trait** raises *this* column; tint it and explain on hover which
  power is responsible.
- **Rank** — what was bought, starting at **0**. **Protection** raises *this*.
- **Total** — ability + rank + everything else. A **condition** moves *this*.

The table is built by the shared `build_stat_table` in `ui/sections/stat_table.py`, which
the Abilities block uses too, so the extra columns have to be scoped to the Resistances
caller or made opt-in rather than added for both. The numbers themselves already exist in
`core.rules` (`resistance_total` and the contribution walk behind it) — this is a display
change, and no rule should be re-derived in the widget.
