"""The character sheet's four stacked sections.

Each section is a ``QGroupBox`` built by iterating over the loaded
:class:`~mm_companion.core.data_loader.GameData` — no hardcoded ability, skill,
or advantage names.
"""

from __future__ import annotations

from mm_companion.ui.sections.base_info import BaseInfoSection
from mm_companion.ui.sections.powers import PowersSection
from mm_companion.ui.sections.skills import SkillsSection
from mm_companion.ui.sections.stats import StatsSection

__all__ = ["BaseInfoSection", "PowersSection", "SkillsSection", "StatsSection"]
