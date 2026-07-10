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
from mm_companion.ui.sections.character_image import CharacterImageSection
from mm_companion.ui.sections.conditions import ConditionsSection
from mm_companion.ui.sections.powers import PowersSection
from mm_companion.ui.sections.resistances import ResistancesSection
from mm_companion.ui.sections.skills import SkillsSection
from mm_companion.ui.sections.system_info import SystemInfoSection

__all__ = [
    "AbilitiesSection",
    "AdvantagesSection",
    "BaseInfoSection",
    "CharacterImageSection",
    "ConditionsSection",
    "PowersSection",
    "ResistancesSection",
    "SkillsSection",
    "SystemInfoSection",
]
