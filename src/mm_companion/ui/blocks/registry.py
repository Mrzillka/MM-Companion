"""The ordered registry of character-sheet block descriptors.

This is the single source of truth for *which* blocks the sheet has — replacing
the block set that used to be spelled out in three hardcoded places (the sheet's
``panels`` list, its ``_sections()`` tuple, and the canvas's ``DEFAULT_ROWS``).
:class:`~mm_companion.ui.character_sheet.CharacterSheet` iterates
:func:`block_descriptors` to build its blocks, and the canvas takes
:func:`default_rows` for its default arrangement.

The registry reuses the generic :class:`~mm_companion.core.registry.Registry`, so
it keeps insertion order and rejects a duplicate key unless ``replace=True`` — a
mod overriding a base block is explicit. The nine base blocks register at import;
a mod's Python module can :func:`register_block` a new one (its size table entry
travels on the descriptor, so no separate JSON edit is needed).
"""

from __future__ import annotations

from collections import defaultdict

from mm_companion.core.registry import Registry
from mm_companion.ui.block_sizes import BlockSize, load_block_sizes
from mm_companion.ui.blocks.base import BlockDescriptor
from mm_companion.ui.blocks.bus import (
    ABILITY_CHANGED,
    BUILD_CHANGED,
    CAPS_CHANGED,
    CONDITION_CHANGED,
    DERIVED_CHANGED,
    EDITED,
    ENHANCEMENTS_CHANGED,
    FACTS_CHANGED,
)
from mm_companion.ui.sections import (
    AbilitiesSection,
    AdvantagesSection,
    BaseInfoSection,
    CharacterImageSection,
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
        1,
        0,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, CAPS_CHANGED), "edited": (EDITED,)},
        {DERIVED_CHANGED: "refresh_derived"},
    ),
    ("character_image", "Character Image", CharacterImageSection, 0, 1, {"edited": (EDITED,)}, {}),
    (
        "abilities",
        "Abilities",
        AbilitiesSection,
        2,
        0,
        {
            "abilityChanged": (ABILITY_CHANGED, DERIVED_CHANGED),
            "changed": (BUILD_CHANGED, FACTS_CHANGED, DERIVED_CHANGED, EDITED),
        },
        {ENHANCEMENTS_CHANGED: "refresh_enhancements"},
    ),
    (
        "resistances",
        "Resistances",
        ResistancesSection,
        2,
        1,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, EDITED)},
        {ABILITY_CHANGED: "follow_ability_change", ENHANCEMENTS_CHANGED: "refresh_enhancements"},
    ),
    (
        "conditions",
        "Conditions",
        ConditionsSection,
        3,
        0,
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
        4,
        0,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, DERIVED_CHANGED, EDITED)},
        {CAPS_CHANGED: "refresh_limits", CONDITION_CHANGED: "refresh_conditions"},
    ),
    (
        "skills",
        "Skills",
        SkillsSection,
        5,
        0,
        {"changed": (BUILD_CHANGED, FACTS_CHANGED, EDITED)},
        {ABILITY_CHANGED: "refresh_totals", ENHANCEMENTS_CHANGED: "refresh_totals"},
    ),
    (
        "powers",
        "Powers",
        PowersSection,
        6,
        0,
        {
            "changed": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED, EDITED),
            # A runtime on/off toggle drives the live refresh but is not a persisted
            # edit, so it omits EDITED (and FACTS_CHANGED, to avoid re-deriving itself).
            "runtimeChanged": (BUILD_CHANGED, ENHANCEMENTS_CHANGED, DERIVED_CHANGED),
        },
        {FACTS_CHANGED: "refresh"},
    ),
]


def register_base_blocks(*, replace: bool = False) -> None:
    """Register the nine base M&M blocks (called once at import)."""
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
