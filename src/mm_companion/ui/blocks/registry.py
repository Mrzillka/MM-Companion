"""The ordered registry of character-sheet block descriptors.

This is the single source of truth for *which* blocks the sheet has — replacing
the block set that used to be spelled out in three hardcoded places (the sheet's
``panels`` list, its ``_sections()`` tuple, and the canvas's ``DEFAULT_ROWS``).
:class:`~mm_companion.ui.character_sheet.CharacterSheet` iterates
:func:`block_descriptors` to build its blocks, and the canvas takes
:func:`default_rows` plus :func:`default_pin_lines` for its default arrangement —
the page and the pinned strip respectively.

The registry reuses the generic :class:`~mm_companion.core.registry.Registry`, so
it keeps insertion order and rejects a duplicate key unless ``replace=True`` — a
mod overriding a base block is explicit. The fourteen base blocks register at import;
a mod's Python module can :func:`register_block` a new one (its size table entry
travels on the descriptor, so no separate JSON edit is needed).
"""

from __future__ import annotations

from collections import defaultdict

from mm_companion.core.data_loader import BlockSpec, GameData
from mm_companion.core.registry import Registry
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize, load_block_sizes
from mm_companion.ui.blocks.base import BlockDescriptor, instance_template
from mm_companion.ui.blocks.bus import (
    ABILITY_CHANGED,
    BONUS_REQUESTED,
    BUILD_CHANGED,
    CAPS_CHANGED,
    CONDITION_CHANGED,
    COST_RATES_CHANGED,
    DERIVED_CHANGED,
    EDITED,
    ENHANCEMENTS_CHANGED,
    FACTS_CHANGED,
    HERO_POINT_REQUESTED,
    LOAD_REQUESTED,
    NOTE_REQUESTED,
    PIN_REQUESTED,
    ROLL_REQUESTED,
    UNPIN_REQUESTED,
)
from mm_companion.ui.blocks.declarative import DeclarativeBlock
from mm_companion.ui.sections import (
    AbilitiesSection,
    AdvantagesSection,
    BaseInfoSection,
    CharacterImageSection,
    ComplicationsSection,
    ConditionsSection,
    DiceSection,
    EquipmentSection,
    NotesSection,
    PowersSection,
    ResistancesSection,
    SceneSection,
    SkillsSection,
    SystemInfoSection,
)

# The live registry. Ordered (insertion order = block construction order).
BLOCKS: Registry[BlockDescriptor] = Registry("blocks")


def register_block(descriptor: BlockDescriptor, *, replace: bool = False) -> BlockDescriptor:
    """Add *descriptor* to the registry (raises on a duplicate key unless *replace*)."""
    BLOCKS.register(descriptor.key, descriptor, replace=replace)
    return descriptor


def unregister_block(key: str) -> None:
    """Drop the block *key* if present (no error when it is absent)."""
    BLOCKS.unregister(key)


def block_descriptors() -> list[BlockDescriptor]:
    """Every registered block descriptor, in registration order."""
    return [BLOCKS.get(key) for key in BLOCKS.keys()]


def default_rows() -> list[list[str]]:
    """The default arrangement as rows of block keys, derived from the descriptors.

    Blocks are grouped by ``default_row`` and ordered within a row by
    ``default_col``; rows come out in ascending ``default_row`` order. A
    ``default_pinned`` block is not in a row at all — see :func:`default_pin_lines`.
    """
    rows: dict[int, list[BlockDescriptor]] = defaultdict(list)
    for descriptor in block_descriptors():
        if descriptor.default_pinned:
            continue
        rows[descriptor.default_row].append(descriptor)
    return [[d.key for d in sorted(rows[row], key=lambda d: d.default_col)] for row in sorted(rows)]


def default_pin_lines() -> list[list[str]]:
    """The blocks that start in the pinned strip, one per line along it.

    The complement of :func:`default_rows`: together the two cover every
    registered block exactly once, which is what the arrangement model requires.
    Ordered by ``default_row``/``default_col`` like the rows are, so a second
    pinned block lands under the first predictably.
    """
    pinned = [d for d in block_descriptors() if d.default_pinned]
    pinned.sort(key=lambda d: (d.default_row, d.default_col))
    return [[d.key] for d in pinned]


# Every block whose lines can be rolled says so the same way: one `rollRequested`
# signal carrying the RollSpec. Named once so the five tables below stay readable
# and a sixth requester is a one-word addition.
#
# `pinRequested` rides along with it, because a line a GM can roll is exactly a
# line a GM can pin to that character's card — same rows, same stashed key. The
# sheet serves the topic (the card is outside the sheet entirely) and the menu
# only appears once it says there is a card, so a player's own sheet is unchanged.
_ROLLS = {
    "rollRequested": (ROLL_REQUESTED,),
    "pinRequested": (PIN_REQUESTED,),
    "unpinRequested": (UNPIN_REQUESTED,),
}

# A *stat readout* also loads on a single click, so the sliders and the DC can be
# set before anything is thrown. A power card's roll line does not: it is an
# explicit "roll this" affordance rather than a number being read off the sheet, so
# one click there throws the die and it keeps the plain table above.
_ROLLS_AND_LOADS = {**_ROLLS, "loadRequested": (LOAD_REQUESTED,)}


# One row per base block: (key, dock title, factory, default_row, default_col,
# publishes, subscribes, requests, serves). Listed in construction order; the
# row/col fields drive the default layout (see default_rows). Sizes are read from
# block_sizes.json at registration so that config stays tweakable. `publishes` maps
# a section Qt signal to the bus topics it raises; `subscribes` maps a topic to the
# section method that recomputes on it — together they reproduce the old hand-wired
# cross-block signal web. `requests`/`serves` are the same pair on the bus's payload
# channel, which today carries only "roll this" (see mm_companion.ui.blocks.bus).
def _notes_factory(block_key: str):
    """A block factory for one Notes instance, closed over which one it is.

    Notes is the only ``multi`` block, and an instance has to know its own key to
    find its entry in ``Character.notes``. The registered descriptor closes over
    the template key; the sheet builds the same closure for ``notes#2`` and up.
    """

    def factory(data: GameData, character):
        return NotesSection(data, character, block_key=block_key)

    return factory


_BASE_BLOCKS = [
    ("base_info", "Name & Details", BaseInfoSection, 0, 0, {"edited": (EDITED,)}, {}, {}, {}),
    (
        "system_info",
        "Power Level & System",
        SystemInfoSection,
        0,
        1,
        {
            "changed": (BUILD_CHANGED, FACTS_CHANGED, CAPS_CHANGED),
            "costRatesChanged": (COST_RATES_CHANGED,),
            "edited": (EDITED,),
            # Extra Effort is charged here, and its price is a condition: the same
            # fan-out the Conditions block raises, because it is the same event.
            "conditionsChanged": (
                ENHANCEMENTS_CHANGED,
                FACTS_CHANGED,
                DERIVED_CHANGED,
                CONDITION_CHANGED,
            ),
        },
        {
            DERIVED_CHANGED: "refresh_derived",
            # The Power Level limits are read off three things this block does not own:
            # the level (its own spin box raises CAPS_CHANGED) and every trait any other
            # block edits (FACTS_CHANGED). Without them the row stayed stale for exactly
            # the two edits that move it most directly — typing a Power Level, and
            # typing a Dodge rank — since neither raises DERIVED_CHANGED.
            CAPS_CHANGED: "refresh_limits",
            FACTS_CHANGED: "refresh_limits",
        },
        # the Initiative readout, the note a hero-point change writes, and the +2 Extra
        # Effort buys on a check — the one benefit that lands on the next roll rather
        # than on the build, so it goes to the block that owns the sliders.
        {
            **_ROLLS_AND_LOADS,
            "noteRequested": (NOTE_REQUESTED,),
            "bonusRequested": (BONUS_REQUESTED,),
        },
        # The pips are this block's, so it is the block that moves them — for the
        # Powers block's Extra Effort shrugged off with a Determination heroic feat.
        {HERO_POINT_REQUESTED: "adjust_hero_points"},
    ),
    (
        "character_image",
        "Character Image",
        CharacterImageSection,
        0,
        2,
        {"edited": (EDITED,)},
        {},
        {},
        {},
    ),
    (
        "abilities",
        "Abilities",
        AbilitiesSection,
        1,
        0,
        {
            # Only ABILITY_CHANGED, though a rank edit does move the derived readouts:
            # the block emits ``abilityChanged`` and ``changed`` for the same edit (see
            # AbilitiesSection._on_ability_changed), and ``changed`` already carries
            # DERIVED_CHANGED. Naming it here too published the topic twice per spin
            # step, so the System block re-derived twice — and since refresh_derived
            # calls refresh_limits, which FACTS_CHANGED also calls, the Power Level
            # caps were computed three times for one tick of an arrow key.
            "abilityChanged": (ABILITY_CHANGED,),
            "changed": (BUILD_CHANGED, FACTS_CHANGED, DERIVED_CHANGED, EDITED),
        },
        {ENHANCEMENTS_CHANGED: "refresh_enhancements", COST_RATES_CHANGED: "refresh_cost"},
        _ROLLS_AND_LOADS,
        {},
    ),
    (
        "resistances",
        "Resistances",
        ResistancesSection,
        1,
        1,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, EDITED)},
        {
            ABILITY_CHANGED: "follow_ability_change",
            ENHANCEMENTS_CHANGED: "refresh_readouts",
            COST_RATES_CHANGED: "refresh_cost",
        },
        _ROLLS_AND_LOADS,
        {},
    ),
    (
        "conditions",
        "Conditions",
        ConditionsSection,
        1,
        2,
        {
            "conditionsChanged": (
                ENHANCEMENTS_CHANGED,
                FACTS_CHANGED,
                DERIVED_CHANGED,
                CONDITION_CHANGED,
            ),
            "changed": (BUILD_CHANGED,),
            "edited": (EDITED,),
        },
        # The chips are a view over the model, and this is no longer the only block that
        # writes to it: Extra Effort's fatigue is applied by the core resolver from
        # wherever the effort was spent. Subscribing means the chips follow it there —
        # including this block's own changes, which re-render idempotently.
        {CONDITION_CHANGED: "reseed"},
        {},
        {},
    ),
    (
        "advantages",
        "Advantages",
        AdvantagesSection,
        2,
        0,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, DERIVED_CHANGED, EDITED)},
        {
            CAPS_CHANGED: "refresh_limits",
            CONDITION_CHANGED: "refresh_conditions",
            FACTS_CHANGED: "refresh_power_options",
            COST_RATES_CHANGED: "refresh_cost",
            ENHANCEMENTS_CHANGED: "refresh_granted",
        },
        {},
        {},
    ),
    (
        "complications",
        "Complications",
        ComplicationsSection,
        3,
        0,
        {"edited": (EDITED,)},
        {},
        {},
        {},
    ),
    (
        "skills",
        "Skills",
        SkillsSection,
        4,
        0,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, EDITED)},
        {
            ABILITY_CHANGED: "refresh_totals",
            # Not refresh_totals: a power can grant a skill *row* the character never
            # bought (an Enhanced Trait naming a focus), and a row that does not exist
            # cannot have its total refreshed. refresh_granted rebuilds when the granted
            # set moves and falls through to refresh_totals when it hasn't.
            ENHANCEMENTS_CHANGED: "refresh_granted",
            COST_RATES_CHANGED: "refresh_totals",
        },
        _ROLLS_AND_LOADS,
        {},
    ),
    (
        "powers",
        "Powers",
        PowersSection,
        5,
        0,
        {
            "changed": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED, EDITED),
            # A runtime on/off toggle drives the same live refreshes, and carries
            # EDITED with them: a power's on/off state and a size effect's dialled
            # rung are saved with the build now, so a toggle the sheet did not call an
            # edit would show no `*`, prompt nothing on close, and be lost. It still
            # omits FACTS_CHANGED, to avoid re-deriving itself.
            "runtimeChanged": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED, EDITED),
            # A card's Extra Effort costs a rung of the fatigue ladder, applied to the
            # shared model — so it drives what any other condition change drives.
            "conditionsChanged": (
                ENHANCEMENTS_CHANGED,
                FACTS_CHANGED,
                DERIVED_CHANGED,
                CONDITION_CHANGED,
            ),
        },
        {FACTS_CHANGED: "refresh", COST_RATES_CHANGED: "refresh"},
        # the 🎲 beside each of a power card's roll lines, plus the two prices a card's
        # Extra Effort is paid in: a sentence for the history and a hero point spent
        # through the block that owns the pips.
        {
            **_ROLLS,
            "noteRequested": (NOTE_REQUESTED,),
            "heroPointRequested": (HERO_POINT_REQUESTED,),
        },
        {},
    ),
    (
        "equipment",
        "Equipment",
        EquipmentSection,
        6,
        0,
        {
            "changed": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED, EDITED),
            # Wearing a jacket is a play action, not a build edit, so it drives the
            # same live refreshes minus EDITED — the same split the Powers block's
            # runtime toggle makes.
            "runtimeChanged": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED),
        },
        # An item's card restates itself from character facts (a Strength-Based weapon
        # folds in Strength) and its *budget* is a rank of the Equipment advantage, so
        # an advantage edit has to reach it.
        {FACTS_CHANGED: "refresh", COST_RATES_CHANGED: "refresh"},
        _ROLLS,  # the 🎲 beside each line of a weapon card's dice footer
        {},
    ),
    (
        "notes",
        "Notes",
        # The class itself, not ``_notes_factory("notes")``: a base descriptor's
        # factory *is* its block class (tests/test_bus.py reads the signal names
        # off it), and ``block_key`` already defaults to the template's own key.
        # The closure is only how the *extra* instances are built.
        NotesSection,
        7,
        0,
        # Opening, closing or reordering a tab is a character edit. Typing in a
        # note is not — that autosaves to its own file and never reaches the bus
        # (see mm_companion.ui.sections.notes).
        {"edited": (EDITED,)},
        {},
        {},
        {},
    ),
    (
        "scene",
        "Scene",
        SceneSection,
        # After the roller in row order, which is what puts it *under* the Dice
        # block in the pinned strip: ``default_pin_lines`` sorts the pinned blocks
        # by (row, col) and gives each one a line of its own.
        7,
        0,
        # Publishes and subscribes nothing, for the Dice block's reason: the scene
        # is the GM's, not this character's, and an update landing mid-edit must
        # never mark the sheet dirty. It serves nothing either — there is no
        # request a block could send it.
        {},
        {},
        {},
        {},
    ),
    (
        "dice",
        "Dice Roller",
        DiceSection,
        6,
        0,
        # A roll is not a character edit and must never mark the sheet dirty, and
        # the roller reads nothing off the build — so it publishes and subscribes
        # nothing. It is, however, the block that *answers* a roll request, and the
        # one that owns a history for a note to be written in.
        {},
        {},
        {},
        {
            ROLL_REQUESTED: "perform_roll",
            LOAD_REQUESTED: "load_roll",
            NOTE_REQUESTED: "post_note",
            BONUS_REQUESTED: "add_bonus",
        },
    ),
]

# Blocks that start in the pinned strip instead of in a row (see
# ``BlockDescriptor.default_pinned``). The strip is the one region that does not
# scroll with the page, which is exactly where a die belongs: it stays in view
# through a fight rather than scrolling away under the sheet.
_PINNED_BY_DEFAULT = frozenset({"dice", "scene"})

# How a block the sheet may build more than one of makes its extra instances
# (see BlockDescriptor.instance_factory): the builder is handed the new block's
# key and returns a factory closed over it. The View menu offers "New <title>
# Block" for each block listed here, and an instance beyond the first takes a
# `notes#2`-style key the registry never sees.
_INSTANCE_FACTORIES = {"notes": _notes_factory}


def register_base_blocks(*, replace: bool = False) -> None:
    """Register the fourteen base M&M blocks (called once at import)."""
    sizes = load_block_sizes()
    for key, title, factory, row, col, publishes, subscribes, requests, serves in _BASE_BLOCKS:
        register_block(
            BlockDescriptor(
                key,
                title,
                factory,
                sizes.get(instance_template(key), BlockSize()),
                row,
                col,
                key in _PINNED_BY_DEFAULT,
                publishes,
                subscribes,
                requests,
                serves,
                _INSTANCE_FACTORIES.get(key),
            ),
            replace=replace,
        )


register_base_blocks()

# Keys of the declarative blocks currently registered from game data, so a re-sync
# (e.g. after enabling a different mod set) can drop the previous batch first.
_declarative_keys: set[str] = set()


def _declarative_factory(spec: BlockSpec):
    """A ``(data, character)`` block factory that builds *spec*'s declarative block."""

    def factory(data: GameData, character):
        return DeclarativeBlock(data, character, spec)

    return factory


def _block_size(spec: BlockSpec) -> BlockSize:
    return BlockSize(
        min_width=spec.min_width or 0,
        min_height=spec.min_height or 0,
        max_width=spec.max_width or UNBOUNDED,
        max_height=spec.max_height or UNBOUNDED,
    )


def sync_declarative_blocks(data: GameData) -> None:
    """Register a declarative block for every :class:`BlockSpec` in *data*.

    Data-only mods contribute blocks through ``blocks.json`` (parsed into
    :attr:`GameData.blocks`); this turns each spec into a
    :class:`~mm_companion.ui.blocks.declarative.DeclarativeBlock` descriptor so it
    joins the sheet like a built-in block. Idempotent: the previously-synced
    declarative blocks are unregistered first, so re-loading with a different mod
    set replaces them cleanly. Declarative blocks are strictly *additive* — a spec
    whose id collides with a block the engine already owns (a base block, or a
    mod's Python-registered one) is skipped rather than clobbering a descriptor a
    re-sync could not restore. The sheet calls this once it has the active
    :class:`GameData`, before it reads :func:`block_descriptors`.
    """
    for key in _declarative_keys:
        unregister_block(key)
    _declarative_keys.clear()
    for spec in data.blocks:
        if spec.id in BLOCKS:  # never overwrite a block we can't put back
            continue
        register_block(
            BlockDescriptor(
                spec.id,
                spec.title or spec.id,
                _declarative_factory(spec),
                _block_size(spec),
                spec.row,
                spec.col,
                publishes={"edited": (EDITED,)},
                subscribes={},
            )
        )
        _declarative_keys.add(spec.id)
