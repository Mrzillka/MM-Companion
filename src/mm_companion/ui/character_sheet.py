"""The character sheet: four sections stacked vertically inside a scroll area."""

from __future__ import annotations

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.rules import power_points_spent
from mm_companion.ui.sections import (
    BaseInfoSection,
    PowersSection,
    SkillsSection,
    StatsSection,
)


class CharacterSheet(QScrollArea):
    """Scrollable character sheet composed of the four main sections.

    Owns the shared :class:`Character` model that the sections read and write, and
    recomputes derived values (spent power points) whenever a section reports a
    build change.
    """

    def __init__(
        self,
        data: GameData | None = None,
        character: Character | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        self.character = character or Character.new_default(self._data)

        content = QWidget()
        layout = QVBoxLayout(content)

        self.base_info = BaseInfoSection(self._data, self.character)
        self.stats = StatsSection(self._data, self.character)
        self.skills = SkillsSection(self._data, self.character)
        self.powers = PowersSection()

        for section in (self.base_info, self.stats, self.skills, self.powers):
            layout.addWidget(section)
        layout.addStretch()

        # Keep skill totals in sync with the ability spin boxes.
        self.skills.set_ability_values(self.stats.ability_values())
        self.stats.abilityChanged.connect(self.skills.set_ability_value)

        # Recompute derived values whenever any section reports a build change.
        for section in (self.base_info, self.stats, self.skills):
            section.changed.connect(self._recompute_derived)
        self._recompute_derived()

        self.setWidget(content)
        self.setWidgetResizable(True)

    def _recompute_derived(self) -> None:
        """Refresh values the model derives from the build (spent power points)."""

        spent = power_points_spent(self.character, self._data)
        self.base_info.set_pool_current("power_points", spent)

    def set_locked(self, locked: bool) -> None:
        """Toggle read-only view mode across every section."""
        self.base_info.set_locked(locked)
        self.stats.set_locked(locked)
        self.skills.set_locked(locked)
