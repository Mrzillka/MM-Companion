"""The character sheet's blocks.

Each block is a ``QGroupBox`` built by iterating over the loaded
:class:`~mm_companion.core.data_loader.GameData` — no hardcoded ability, skill,
or advantage names. The sheet wraps each block in a draggable
:class:`~mm_companion.ui.block_frame.BlockFrame` on its scrollable canvas.
"""

from __future__ import annotations

from mm_companion.ui.sections.abilities import AbilitiesSection
from mm_companion.ui.sections.advantages import AdvantagesSection
from mm_companion.ui.sections.base_info import BaseInfoSection
from mm_companion.ui.sections.powers import PowersSection
from mm_companion.ui.sections.resistances import ResistancesSection
from mm_companion.ui.sections.skills import SkillsSection

__all__ = [
    "AbilitiesSection",
    "AdvantagesSection",
    "BaseInfoSection",
    "PowersSection",
    "ResistancesSection",
    "SkillsSection",
]
