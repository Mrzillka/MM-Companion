"""Extra Effort: pushing past your limits, and what it costs (p20-21).

Extra Effort is a **non-action** a hero takes to do more than they bought — an extra
action, a bonus on a check, a deeper rank, a power stunt — and "because Extra Effort is
an extraordinary and costly move, its benefits can even increase your ranks or bonuses
beyond the normal Power Level limits". The price is paid a turn later: the character
gains the **Fatigued** condition, a Fatigued one becomes **Exhausted**, and an Exhausted
one becomes **Incapacitated** (p21).

Three things are modelled here, and nothing else is:

* **what it buys** — the ruleset's list of uses (``system.json``'s ``extra_effort``),
  each saying whether it has to name one of the character's own effects;
* **what it costs** — the fatigue ladder, walked from wherever the character already
  stands and applied through the ordinary condition resolver, so it bundles and
  supersedes like any other condition;
* **the one benefit that changes a number the sheet prints** — the rank increase, which
  is written into :attr:`~mm_companion.core.powers.PowerEffectInstance.extra_effort` and
  read back by :func:`~.runtime.effect_current_rank`, so a pushed effect's save DC,
  measures, trait boosts and card title all follow from the one number.

The rest are table business — an extra action, a renewed attempt, another resistance
check — and the app's honest contribution to those is to charge the fatigue and write the
sentence down. The **power stunt** is the exception among them: it is a whole temporary
Alternate Effect, so this module charges the effort and names the effect it was taken from
while the *build* is the Powers block's (:attr:`~mm_companion.core.powers.Power.stunt_of`,
and :meth:`~mm_companion.ui.sections.powers.PowersSection._open_stunt`).

Nothing here is a **cost**: Extra Effort is spent at the table, not bought, so no point
total, no Power Level check and no validation warning ever sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import ExtraEffortRules, ExtraEffortUse, GameData
from ..powers import Power, PowerEffectInstance, PowerGroup, PowerNode, power_is_stunt
from .appliers import CATEGORY_ABILITY, CATEGORY_MOVEMENT
from .conditions import apply_condition
from .derived import EFFORT_KEY_SEP, all_advantage_selections, effective_ability, effort_key
from .movement import speed_lines
from .powers_terms import effective_effect_stats
from .runtime import effect_current_rank
from .validation import leaf_powers

#: The ``target`` an effect-naming use declares (the rank increase and the power stunt).
TARGET_EFFECT = "effect"

#: The one use whose benefit the app can apply to the build itself. Named here rather
#: than matched on a label, so a ruleset may retitle it and still be understood.
USE_RANK_INCREASE = "rank_increase"

#: Its sibling: the other effect-naming use, and the seam the stunt pass will build on.
USE_POWER_STUNT = "power_stunt"

#: The use whose benefit is a number on the *next roll* rather than on the build: "a +2
#: bonus on a single check". Named for the same reason the two above are — a ruleset may
#: retitle it — and read so the roller can be handed the bonus rather than the player
#: having to remember to type it in.
USE_CHECK_BONUS = "bonus"


def extra_effort_rules(game_data: GameData) -> ExtraEffortRules:
    """The ruleset's Extra Effort block — the one door onto the dials below."""

    return game_data.system.extra_effort


def extra_effort_uses(game_data: GameData) -> tuple[ExtraEffortUse, ...]:
    """Every benefit this ruleset says Extra Effort can buy, in its own order."""

    return extra_effort_rules(game_data).uses


def extra_effort_use(game_data: GameData, use_id: str) -> ExtraEffortUse | None:
    """One use by id, or ``None`` when the ruleset does not describe it."""

    return next((use for use in extra_effort_uses(game_data) if use.id == use_id), None)


def effect_allows_extra_effort(effect: PowerEffectInstance, game_data: GameData) -> bool:
    """Whether this effect may be pushed or stunted with — is it *non-permanent*?

    "A Permanent effect cannot be improved using Extra Effort" (p104), and the Permanent
    flaw says the same from the other side: "you cannot improve a permanent effect using
    Extra Effort, including using it for Power Stunts" (p159). The question is asked of
    the effect's **resolved** duration rather than the base effect's own, because that is
    the whole point of the two modifiers that move it: the Sustained extra exists so a
    Permanent effect *can* be pushed ("Sustained Protection can be turned on and off,
    improved with Extra Effort, and used for power stunts", p155), and the Permanent flaw
    takes that away from a Continuous one.
    """

    duration = effective_effect_stats(effect, game_data).get("duration", "")
    if not duration:
        return False
    return duration not in extra_effort_rules(game_data).permanent_durations


def pushable_effects(power: Power, game_data: GameData) -> list[PowerEffectInstance]:
    """The effects of ``power`` that Extra Effort may name, in build order.

    Empty for a power built entirely out of Permanent effects — a Protection, an
    Immunity — which is exactly the case the rules single out.
    """

    return [effect for effect in power.effects if effect_allows_extra_effort(effect, game_data)]


def character_pushable_powers(char: Character, game_data: GameData) -> list[Power]:
    """Every power on the sheet holding at least one effect Extra Effort could name.

    Walks the whole tree rather than the *live* one: which alternate of an array is
    currently selected is a free action away, and a list that hid the others would be
    answering a different question from the one the player asked.
    """

    return [power for power in leaf_powers(char.powers) if pushable_effects(power, game_data)]


def _advantage_ranks(char: Character, game_data: GameData, name: str) -> int:
    """Total ranks of one advantage by name, 0 when it is not on the sheet.

    Bought *and* power-granted (:func:`~.derived.all_advantage_selections`): an
    Enhanced Advantage: Determination is a Determination, and straining is a thing the
    character can do because of it. This walked the bought list alone, which is the same
    gap the initiative readout had — a granted advantage is never written back to
    ``Character.advantages``, so it showed on the sheet and moved nothing here.
    """

    return sum(
        max(1, sel.rank) for sel in all_advantage_selections(char, game_data) if sel.name == name
    )


def extra_effort_rank_increase(char: Character | None, game_data: GameData) -> int:
    """How many ranks one Rank Increase grants this character.

    The ruleset's own ``rankIncrease`` (1), plus a rank for every rank of **Untapped
    Potential**: "when you use Extra Effort to increase an effect's rank, you gain 2
    ranks rather than just 1 ... each additional rank adds 1 to the increase" (p94). With
    no character in hand — the Power Constructor, where nobody is straining — it is the
    bare ruleset value.
    """

    rules = extra_effort_rules(game_data)
    if char is None:
        return rules.rank_increase
    return rules.rank_increase + _advantage_ranks(
        char, game_data, rules.untapped_potential_advantage
    )


def determination_ranks(char: Character, game_data: GameData) -> int:
    """How many times per adventure this character can shrug the fatigue off for free.

    Determination is "once per adventure" per rank (p85). The app tracks no adventure, so
    this is what the *sheet* says they have rather than what is left — the dialog offering
    it says as much, and the alternative (a per-adventure counter with nothing to reset
    it) would lie more confidently.
    """

    return _advantage_ranks(char, game_data, extra_effort_rules(game_data).determination_advantage)


def has_extraordinary_effort(char: Character, game_data: GameData) -> bool:
    """Whether this character may take **two** benefits for two rungs of fatigue (p86)."""

    rules = extra_effort_rules(game_data)
    return bool(_advantage_ranks(char, game_data, rules.extraordinary_effort_advantage))


def current_fatigue(char: Character, game_data: GameData) -> str:
    """The highest rung of the fatigue ladder this character already stands on, or ``""``.

    Read from the top down, because climbing the ladder *removes* what it climbed from —
    Exhausted supersedes Fatigued, so an exhausted character has no Fatigued row to find.
    """

    standing = {applied.condition_id for applied in char.conditions}
    for rung in reversed(extra_effort_rules(game_data).fatigue_ladder):
        if rung in standing:
            return rung
    return ""


def next_fatigue(char: Character, game_data: GameData, steps: int = 1) -> str:
    """The condition ``steps`` more uses of Extra Effort would leave the character in.

    One step is the rung above wherever they stand: nothing becomes Fatigued, Fatigued
    becomes Exhausted, Exhausted becomes Incapacitated. Already at the top of the ladder
    the answer is the top rung again — there is nothing worse for Extra Effort to do, and
    an Incapacitated character has bigger problems. ``""`` only for a ruleset that
    describes no ladder at all.

    ``steps`` is how Extraordinary Effort is *previewed* before it is taken (it costs two
    rungs at once), and it is deliberately a lookahead rather than a second code path:
    :func:`spend_extra_effort` still climbs one rung at a time, through the condition
    resolver, so what the dialog promised and what the sheet gains cannot disagree.
    """

    ladder = extra_effort_rules(game_data).fatigue_ladder
    if not ladder:
        return ""
    standing = current_fatigue(char, game_data)
    index = -1 if not standing else ladder.index(standing)
    return ladder[min(index + max(1, steps), len(ladder) - 1)]


def fatigue_label(condition_id: str, game_data: GameData) -> str:
    """A rung's display name (``"exhausted"`` → ``"Exhausted"``), out of the catalog."""

    condition = game_data.condition_catalog().get(condition_id)
    return condition.name if condition is not None else condition_id


@dataclass(frozen=True)
class ExtraEffortOutcome:
    """What one use of Extra Effort did, for the sheet to show and the history to record.

    ``ranks`` and ``rank`` are the Rank Increase's own: how many ranks were pushed in, and
    what the effect now runs at. ``fatigue`` is the rung gained, ``""`` when Determination
    shrugged it off. ``note`` is the sentence for the roll history, phrased as a predicate
    the way :func:`~mm_companion.ui.sections.system_info.hero_point_note` is — the block
    that writes it down names the character.
    """

    use: ExtraEffortUse
    ranks: int = 0
    rank: int = 0
    #: The Bonus use's own: what the next check is worth extra. Nothing on the build
    #: moves for it — it is a number for one roll — so it is reported rather than stored.
    check_bonus: int = 0
    target: str = ""
    fatigue: str = ""
    determination: bool = False
    doubled: bool = False
    note: str = ""


def spend_extra_effort(
    char: Character,
    game_data: GameData,
    use: ExtraEffortUse,
    *,
    effect: PowerEffectInstance | None = None,
    effect_name: str = "",
    trait: PushTarget | None = None,
    doubled: bool = False,
    determination: bool = False,
) -> ExtraEffortOutcome:
    """Take one use of Extra Effort: grant the benefit, then charge the fatigue.

    ``effect`` is the one a target-naming use points at. A Rank Increase pushes
    :attr:`~mm_companion.core.powers.PowerEffectInstance.extra_effort` up by
    :func:`extra_effort_rank_increase`; a power stunt changes nothing on the build — the
    GM adjudicates the stunt, and the app's part is the effort and the record.

    ``trait`` is the *other* thing a Rank Increase may name (:func:`pushable_traits`):
    the character's Strength, or one mode of their movement. It pushes the same number of
    ranks into :attr:`~mm_companion.core.character.Character.extra_effort` instead, from
    where :func:`~.derived.effort_contributions` puts it on the sheet. The two are
    mutually exclusive — a use names one thing — and ``effect`` wins if both arrive,
    since only a card can offer one.

    ``doubled`` is **Extraordinary Effort** (p86): two benefits — here, twice the ranks —
    for "two instances of the Fatigued condition", so the ladder is climbed twice.
    ``determination`` is the other advantage of the pair (p85, p22): the fatigue is
    removed as it arrives, so no rung is applied at all. Whether that cost a Hero Point or
    a use of the advantage is the caller's business; both mean *no rung* here.

    The push is deliberately **cumulative**: pushing the same effect twice is two uses of
    Extra Effort and two rungs of fatigue, and the ranks should stack the way the fatigue
    does. Taking them back off is :func:`clear_extra_effort`.
    """

    ranks = 0
    rank = 0
    if use.target == TARGET_EFFECT and effect is not None:
        if use.id == USE_RANK_INCREASE:
            ranks = extra_effort_rank_increase(char, game_data) * (2 if doubled else 1)
            effect.extra_effort = max(0, effect.extra_effort) + ranks
        # What the card will actually read, not bought + pushed: an effect turned down on
        # its dial, or held under a Dynamic share, is pushed from where it stands.
        rank = effect_current_rank(effect, game_data, char)
    elif use.id == USE_RANK_INCREASE and trait is not None:
        ranks = extra_effort_rank_increase(char, game_data) * (2 if doubled else 1)
        # Cumulative for the same reason an effect's push is: two uses are two rungs of
        # fatigue, and the ranks should stack the way the price does.
        char.extra_effort[trait.key] = max(0, char.extra_effort.get(trait.key, 0)) + ranks
        rank = _trait_rank(char, game_data, trait)
        effect_name = effect_name or trait.label
    check_bonus = 0
    if use.id == USE_CHECK_BONUS:
        # Doubled by Extraordinary Effort like every other benefit: "two benefits"
        # for two rungs, and twice a +2 is the honest reading of taking this one twice.
        check_bonus = extra_effort_rules(game_data).check_bonus * (2 if doubled else 1)
    fatigue = ""
    if not determination:
        for _ in range(2 if doubled else 1):
            fatigue = next_fatigue(char, game_data)
            if fatigue:
                apply_condition(char, fatigue, game_data)
    outcome = ExtraEffortOutcome(
        use=use,
        ranks=ranks,
        rank=rank,
        check_bonus=check_bonus,
        target=effect_name,
        fatigue=fatigue,
        determination=determination,
        doubled=doubled,
    )
    return replace(outcome, note=extra_effort_note(outcome, game_data))


def _trait_rank(char: Character, game_data: GameData, trait: PushTarget) -> int:
    """What a pushed trait now reads, so the note can say where it got to.

    Asked of the sheet rather than added up here: a movement mode's rank is the net of
    everything granting it, and a note that said "pushed Flight to 5" while the sheet
    said 4 would be the kind of disagreement this whole layer exists to avoid. ``0`` for
    a category with nothing to ask, which reads as "no number to report".
    """

    category, _, stat = trait.key.partition(EFFORT_KEY_SEP)
    if category == CATEGORY_MOVEMENT:
        return next((line.rank for line in speed_lines(char, game_data) if line.mode == stat), 0)
    if category == CATEGORY_ABILITY:
        return effective_ability(char, game_data, stat)
    return 0


def extra_effort_note(outcome: ExtraEffortOutcome, game_data: GameData) -> str:
    """The roll-history sentence for one use of Extra Effort.

    Says the benefit, then the price, because that is the order the table cares about and
    the price is the half nobody remembers. A rank increase names the effect and where it
    got to, since that number is about to appear on a card with nothing else explaining it.
    """

    if outcome.ranks and outcome.target:
        benefit = f"pushed {outcome.target} to rank {outcome.rank} with Extra Effort"
    elif outcome.check_bonus:
        # Names the number, because it is about to be sitting in the roller's bonus and
        # the history is where a player checks what it was for.
        benefit = f"took +{outcome.check_bonus} on a check with Extra Effort"
    else:
        # The article is a guess, and a cheap one: every use's label is a noun phrase the
        # ruleset wrote, so the alternative is either a grammar field in the data or a
        # sentence that reads "a extra action".
        label = outcome.use.label.lower()
        article = "an" if label[:1] in "aeiou" else "a"
        benefit = f"used Extra Effort for {article} {label}"
        if outcome.target:
            benefit += f" on {outcome.target}"
    if outcome.doubled:
        benefit += " (Extraordinary Effort)"
    if outcome.determination:
        return f"{benefit} — no fatigue, shrugged off with Determination"
    if not outcome.fatigue:
        return benefit
    return f"{benefit} — now {fatigue_label(outcome.fatigue, game_data)}"


def clear_power_extra_effort(power: Power) -> int:
    """The same for one power alone — what its card's own Clear entry takes back."""

    cleared = 0
    for effect in power.effects:
        if effect.extra_effort:
            effect.extra_effort = 0
            cleared += 1
    return cleared


def clear_extra_effort(char: Character) -> int:
    """Drop every rank Extra Effort has pushed into this character — effects and traits.

    Extra Effort lasts "until the end of your turn" and the app tracks no turns, so the
    end of one is a button rather than a tick — the same bargain the array's point split
    strikes. Returns how many things were cleared, so a caller can leave the sheet alone
    when there was nothing to clear.
    """

    cleared = 0
    for effect in pushed_effects(char):
        effect.extra_effort = 0
        cleared += 1
    cleared += len([key for key, ranks in char.extra_effort.items() if ranks])
    char.extra_effort.clear()
    return cleared


def stunt_powers(char: Character) -> list[Power]:
    """Every power stunt currently on the sheet, in the order they were invented."""

    return [power for power in leaf_powers(char.powers) if power_is_stunt(power)]


def clear_stunts(char: Character) -> int:
    """Drop every power stunt from the character's powers tree; returns how many went.

    A stunt is temporary by definition and the app tracks no scenes, so this is the same
    kind of button :func:`clear_extra_effort` is — the difference between the two is the
    clock they answer to, which is why they are two entries and not one: a push is over at
    the end of your **turn**, a stunt at the end of the **scene**.

    Recurses, because nothing stops a stunt card being dragged into a group.
    """

    def prune(nodes: list[PowerNode]) -> int:
        dropped = 0
        for node in list(nodes):
            if power_is_stunt(node):
                nodes.remove(node)
                dropped += 1
            elif isinstance(node, PowerGroup):
                dropped += prune(node.children)
        return dropped

    return prune(char.powers)


def pushed_effects(char: Character) -> list[PowerEffectInstance]:
    """Every effect currently holding Extra Effort ranks, in build order."""

    return [
        effect
        for power in leaf_powers(char.powers)
        for effect in power.effects
        if effect.extra_effort
    ]


@dataclass(frozen=True)
class PushTarget:
    """One thing on this sheet that Extra Effort's rank increase could name.

    The offer, not the push: ``key`` is where the ranks would be written in
    :attr:`~mm_companion.core.character.Character.extra_effort`, and ``label``/``note``
    are what the menu says. ``held`` is what is already pushed there, so an entry can
    show what a second use would build on.
    """

    key: str
    label: str
    note: str = ""
    held: int = 0


def pushable_traits(char: Character, game_data: GameData) -> list[PushTarget]:
    """The character's own traits Extra Effort may push, in the ruleset's order.

    The rank increase names an effect, "your Strength rank for either Damage or
    Lifting", or "your movement Speed rank in one mode of movement you have" (p21). The
    effects are :func:`character_pushable_powers`' business; these are the other two.

    A ``movement`` entry naming no ``stat`` is expanded into **the modes this character
    actually has** — "one mode of movement you have" is a fact about the sheet, so the
    ruleset states the rule and the sheet supplies the list. A character who cannot fly
    is offered no Flight to push.

    The Strength entry is offered as an *ability*, which is broader than the rules'
    "for Damage or Lifting": the app has no separate lifting trait to aim at, which is
    the same simplification the shipped **Lifting** effect already makes (it raises the
    ability too). The note says which uses the rules mean.
    """

    targets: list[PushTarget] = []
    for record in extra_effort_rules(game_data).pushable_traits:
        if record.category == CATEGORY_MOVEMENT and not record.stat:
            for line in speed_lines(char, game_data):
                key = effort_key(CATEGORY_MOVEMENT, line.mode)
                label = f"{record.label or 'Speed'}: {line.label}"
                targets.append(PushTarget(key, label, record.note, char.extra_effort.get(key, 0)))
            continue
        if not record.stat:
            continue
        key = effort_key(record.category, record.stat)
        targets.append(
            PushTarget(key, record.label or record.stat, record.note, char.extra_effort.get(key, 0))
        )
    return targets


def pushed_traits(char: Character) -> dict[str, int]:
    """Every trait currently holding Extra Effort ranks, keyed as they are stored."""

    return {key: ranks for key, ranks in char.extra_effort.items() if ranks}


def pushed_trait_labels(char: Character, game_data: GameData) -> dict[str, int]:
    """The same, keyed by what to *call* each one — the sheet's readable view.

    Resolved through :func:`pushable_traits` so a pushed mode is named the way the menu
    that offered it named it, and a key whose trait has since gone (a Flight power
    deleted while its push stood) falls back to the bare stat rather than vanishing —
    the ranks are still on the character and still want clearing.
    """

    by_key = {target.key: target.label for target in pushable_traits(char, game_data)}
    return {
        by_key.get(key, key.partition(EFFORT_KEY_SEP)[2] or key): ranks
        for key, ranks in pushed_traits(char).items()
    }
