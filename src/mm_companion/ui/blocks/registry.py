"""The ordered registry of character-sheet block descriptors.

This is the single source of truth for *which* blocks the sheet has — replacing
the block set that used to be spelled out in three hardcoded places (the sheet's
``panels`` list, its ``_sections()`` tuple, and the canvas's ``DEFAULT_ROWS``).
:class:`~mm_companion.ui.character_sheet.CharacterSheet` iterates
:func:`block_descriptors` to build its blocks, and the canvas takes
:func:`default_rows` for its default arrangement.

The registry reuses the generic :class:`~mm_companion.core.registry.Registry`, so
it keeps insertion order and rejects a duplicate key unless ``replace=True`` — a
mod overriding a base block is explicit. The ten base blocks register at import;
a mod's Python module can :func:`register_block` a new one (its size table entry
travels on the descriptor, so no separate JSON edit is needed).
"""

from __future__ import annotations

from collections import defaultdict

from mm_companion.core.data_loader import BlockSpec, GameData
from mm_companion.core.registry import Registry
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize, load_block_sizes
from mm_companion.ui.blocks.base import BlockDescriptor
from mm_companion.ui.blocks.bus import (
    ABILITY_CHANGED,
    BUILD_CHANGED,
    CAPS_CHANGED,
    CONDITION_CHANGED,
    COST_RATES_CHANGED,
    DERIVED_CHANGED,
    EDITED,
    ENHANCEMENTS_CHANGED,
    FACTS_CHANGED,
)
from mm_companion.ui.blocks.declarative import DeclarativeBlock
from mm_companion.ui.sections import (
    AbilitiesSection,
    AdvantagesSection,
    BaseInfoSection,
    CharacterImageSection,
    ComplicationsSection,
    ConditionsSection,
    PowersSection,
    ResistancesSection,
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
    ``default_col``; rows come out in ascending ``default_row`` order.
    """
    rows: dict[int, list[BlockDescriptor]] = defaultdict(list)
    for descriptor in block_descriptors():
        rows[descriptor.default_row].append(descriptor)
    return [[d.key for d in sorted(rows[row], key=lambda d: d.default_col)] for row in sorted(rows)]


# One row per base block: (key, dock title, factory, default_row, default_col,
# publishes, subscribes). Listed in construction order; the row/col fields drive
# the default layout (see default_rows). Sizes are read from block_sizes.json at
# registration so that config stays tweakable. `publishes` maps a section Qt
# signal to the bus topics it raises; `subscribes` maps a topic to the section
# method that recomputes on it — together they reproduce the old hand-wired
# cross-block signal web (see mm_companion.ui.blocks.bus for the topic table).
_BASE_BLOCKS = [
    ("base_info", "Name & Details", BaseInfoSection, 0, 0, {"edited": (EDITED,)}, {}),
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
        },
        {DERIVED_CHANGED: "refresh_derived"},
    ),
    ("character_image", "Character Image", CharacterImageSection, 0, 2, {"edited": (EDITED,)}, {}),
    (
        "abilities",
        "Abilities",
        AbilitiesSection,
        1,
        0,
        {
            "abilityChanged": (ABILITY_CHANGED, DERIVED_CHANGED),
            "changed": (BUILD_CHANGED, FACTS_CHANGED, DERIVED_CHANGED, EDITED),
        },
        {ENHANCEMENTS_CHANGED: "refresh_enhancements", COST_RATES_CHANGED: "refresh_cost"},
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
            ENHANCEMENTS_CHANGED: "refresh_enhancements",
            COST_RATES_CHANGED: "refresh_cost",
        },
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
        },
    ),
    (
        "complications",
        "Complications",
        ComplicationsSection,
        3,
        0,
        {"edited": (EDITED,)},
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
            ENHANCEMENTS_CHANGED: "refresh_totals",
            COST_RATES_CHANGED: "refresh_totals",
        },
    ),
    (
        "powers",
        "Powers",
        PowersSection,
        5,
        0,
        {
            "changed": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED, EDITED),
            # A runtime on/off toggle drives the live refresh but is not a persisted
            # edit, so it omits EDITED (and FACTS_CHANGED, to avoid re-deriving itself).
            "runtimeChanged": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED),
        },
        {FACTS_CHANGED: "refresh", COST_RATES_CHANGED: "refresh"},
    ),
]


def register_base_blocks(*, replace: bool = False) -> None:
    """Register the ten base M&M blocks (called once at import)."""
    sizes = load_block_sizes()
    for key, title, factory, row, col, publishes, subscribes in _BASE_BLOCKS:
        register_block(
            BlockDescriptor(
                key,
                title,
                factory,
                sizes.get(key, BlockSize()),
                row,
                col,
                publishes,
                subscribes,
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
                {"edited": (EDITED,)},
                {},
            )
        )
        _declarative_keys.add(spec.id)
