"""The character sheet: four sections stacked vertically inside a scroll area."""

from __future__ import annotations

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.ui.base_info_section import BaseInfoSection
from mm_companion.ui.powers_section import PowersSection
from mm_companion.ui.skills_section import SkillsSection
from mm_companion.ui.stats_section import StatsSection


class CharacterSheet(QScrollArea):
    """Scrollable character sheet composed of the four main sections."""

    def __init__(self, data: GameData | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        data = data or load_game_data()

        content = QWidget()
        layout = QVBoxLayout(content)

        self.base_info = BaseInfoSection(data)
        self.stats = StatsSection(data)
        self.skills = SkillsSection(data)
        self.powers = PowersSection()

        for section in (self.base_info, self.stats, self.skills, self.powers):
            layout.addWidget(section)
        layout.addStretch()

        # Keep skill totals in sync with the ability spin boxes.
        self.skills.set_ability_values(self.stats.ability_values())
        self.stats.abilityChanged.connect(self.skills.set_ability_value)

        self.setWidget(content)
        self.setWidgetResizable(True)

    def set_locked(self, locked: bool) -> None:
        """Toggle read-only view mode across every section."""
        self.base_info.set_locked(locked)
        self.stats.set_locked(locked)
        self.skills.set_locked(locked)
