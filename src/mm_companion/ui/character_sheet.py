"""The character sheet: six rearrangeable dock panels over one shared model.

The sheet is a :class:`QMainWindow` used purely as a dock host: each block (Base
Information, Abilities, Resistances, Advantages, Skills, Powers) lives in its own
:class:`QDockWidget`, so the user can drag a block to re-dock it, split or tab
blocks together, resize them, or tear one out into its own floating window. It is
embedded as the outer :class:`~mm_companion.ui.main_window.MainWindow`'s central
widget.

It still owns the shared :class:`Character` model that the blocks read and write,
and recomputes derived values (spent power points) whenever a block reports a
build change. The cross-block wiring (abilities feed skills and resistances,
powers feed the enhanced totals, the build facts re-derive the power cards) works
across windows unchanged — Qt signals don't care which window a block lives in.
Emits :attr:`edited` on any user edit so a host window can track unsaved changes.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtWidgets import QDockWidget, QMainWindow, QScrollArea, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.rules import power_points_spent
from mm_companion.ui.sections import (
    AbilitiesSection,
    AdvantagesSection,
    BaseInfoSection,
    PowersSection,
    ResistancesSection,
    SkillsSection,
)

# Bumped whenever the set of docks changes, so a saved arrangement from an older
# layout is rejected by restoreState and we fall back to the default.
LAYOUT_VERSION = 1


class CharacterSheet(QMainWindow):
    """Dock host for the character sheet's six blocks over a shared model."""

    edited = Signal()

    def __init__(
        self,
        data: GameData | None = None,
        character: Character | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        self.character = character or Character.new_default(self._data)

        self.setDockNestingEnabled(True)
        # A zero-size central widget lets the six docks tile the whole window.
        central = QWidget()
        central.setMaximumSize(0, 0)
        self.setCentralWidget(central)

        self.base_info = BaseInfoSection(self._data, self.character)
        self.abilities = AbilitiesSection(self._data, self.character)
        self.resistances = ResistancesSection(self._data, self.character)
        self.advantages = AdvantagesSection(self._data, self.character)
        self.skills = SkillsSection(self._data, self.character)
        self.powers = PowersSection(self._data, self.character)

        # (section, dock object name, dock title) — object names are required for
        # save/restoreState and must stay stable across releases.
        self._panels = [
            (self.base_info, "dock_base_info", "Base Information"),
            (self.abilities, "dock_abilities", "Abilities"),
            (self.resistances, "dock_resistances", "Resistances"),
            (self.advantages, "dock_advantages", "Advantages"),
            (self.skills, "dock_skills", "Skills"),
            (self.powers, "dock_powers", "Powers"),
        ]
        self.docks: dict[str, QDockWidget] = {
            name: self._make_dock(section, name, title) for section, name, title in self._panels
        }

        self._apply_default_layout()
        self._wire_sections()

    # -- dock construction / layout -----------------------------------------

    def _make_dock(self, section: QWidget, object_name: str, title: str) -> QDockWidget:
        """Wrap a section in a scrollable dock so it can be moved and floated."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(section)

        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(scroll)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        # Added here so the dock belongs to the window; _apply_default_layout
        # positions it.
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, dock)
        return dock

    def _apply_default_layout(self) -> None:
        """Arrange the docks in the default grid: Base Information across the top,
        Abilities | Resistances | Advantages in the next row, then Skills, then
        Powers. Also un-floats and re-shows any dock the user had closed."""
        d = self.docks
        for dock in d.values():
            dock.setFloating(False)
            dock.show()

        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, d["dock_base_info"])
        # Stack the main rows vertically under Base Information.
        self.splitDockWidget(d["dock_base_info"], d["dock_abilities"], Qt.Orientation.Vertical)
        self.splitDockWidget(d["dock_abilities"], d["dock_skills"], Qt.Orientation.Vertical)
        self.splitDockWidget(d["dock_skills"], d["dock_powers"], Qt.Orientation.Vertical)
        # Abilities | Resistances | Advantages side by side in their row.
        self.splitDockWidget(d["dock_abilities"], d["dock_resistances"], Qt.Orientation.Horizontal)
        self.splitDockWidget(d["dock_resistances"], d["dock_advantages"], Qt.Orientation.Horizontal)

    # -- layout persistence --------------------------------------------------

    def save_layout(self) -> str:
        """The dock arrangement as a base64 string (for settings.json)."""
        return bytes(self.saveState(LAYOUT_VERSION).toBase64()).decode("ascii")

    def restore_layout(self, state_b64: str | None) -> bool:
        """Restore a dock arrangement saved by :meth:`save_layout`.

        Returns whether it applied — a missing or incompatible state (e.g. from an
        older :data:`LAYOUT_VERSION`) returns False so the caller keeps the default.
        """
        if not state_b64:
            return False
        state = QByteArray.fromBase64(state_b64.encode("ascii"))
        return self.restoreState(state, LAYOUT_VERSION)

    def reset_layout(self) -> None:
        """Return the docks to the default arrangement."""
        self._apply_default_layout()

    def set_rearrangeable(self, enabled: bool) -> None:
        """Toggle whether the blocks can be dragged, floated, or closed.

        When disabled ("fixed" mode), the blocks snap back to the default
        arrangement and shed their dock title bars, so the sheet reads as the
        classic fixed stack it was before docking. When re-enabled, the native
        title bars and drag/float/close affordances return.
        """
        for dock in self.docks.values():
            if enabled:
                dock.setFeatures(
                    QDockWidget.DockWidgetFeature.DockWidgetMovable
                    | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                    | QDockWidget.DockWidgetFeature.DockWidgetClosable
                )
                dock.setTitleBarWidget(None)  # restore the native title bar
            else:
                dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
                dock.setTitleBarWidget(QWidget(dock))  # empty widget hides the title bar
        if not enabled:
            self._apply_default_layout()

    # -- signal wiring -------------------------------------------------------

    def _wire_sections(self) -> None:
        """Reproduce the cross-block wiring; see the class docstring for the shape."""
        # Skill totals follow the ability spin boxes.
        self.skills.set_ability_values(self.abilities.ability_values())
        self.abilities.abilityChanged.connect(self.skills.set_ability_value)
        # A moved ability re-seeds the resistances derived from it.
        self.abilities.abilityChanged.connect(lambda *_: self.resistances.follow_ability_change())

        # Recompute derived values whenever any block reports a build change.
        for section in self._sections():
            section.changed.connect(self._recompute_derived)
        self._recompute_derived()

        # A power change can add or drop a trait boost, so refresh the enhanced
        # ability/resistance totals and the skill totals that read them.
        self.powers.changed.connect(self.abilities.refresh_enhancements)
        self.powers.changed.connect(self.resistances.refresh_enhancements)
        self.powers.changed.connect(self.skills.refresh_totals)

        # And the reverse: a power's displayed numbers derive from character facts,
        # so editing an ability/resistance/advantage or the Power Level re-derives
        # the power cards. `refresh` only reads the model, so it never loops back.
        self.abilities.changed.connect(self.powers.refresh)
        self.resistances.changed.connect(self.powers.refresh)
        self.advantages.changed.connect(self.powers.refresh)
        self.base_info.changed.connect(self.powers.refresh)

        # Surface any user edit for unsaved-change tracking. The stats/skills
        # `changed` signals already fire on every edit; base_info has edits (name,
        # conditions, image) that don't affect the build, so it carries `edited`.
        self.base_info.edited.connect(self.edited)
        self.abilities.changed.connect(self.edited)
        self.resistances.changed.connect(self.edited)
        self.advantages.changed.connect(self.edited)
        self.skills.changed.connect(self.edited)
        self.powers.changed.connect(self.edited)

    def _sections(self) -> tuple:
        return (
            self.base_info,
            self.abilities,
            self.resistances,
            self.advantages,
            self.skills,
            self.powers,
        )

    def _recompute_derived(self) -> None:
        """Refresh values the model derives from the build (spent power points)."""
        spent = power_points_spent(self.character, self._data)
        self.base_info.set_pool_current("power_points", spent)

    def set_locked(self, locked: bool) -> None:
        """Toggle read-only view mode across every block."""
        for section in self._sections():
            section.set_locked(locked)
