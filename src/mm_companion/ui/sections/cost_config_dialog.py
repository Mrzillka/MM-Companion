"""The Cost config dialog: homebrew the non-power PP-cost rates for one character.

A modal form over the point-cost rates from ``costs.json``. The top group holds the
*category* rates — ability / resistance / combat / advantage per-rank costs, the two
skill ranks-per-point rates, and the points-per-Power-Level budget. Below it, three
grouped tables let a player re-price a *single* ability, resistance, or skill away
from its category rate; a per-item spin left equal to its category rate simply follows
it. Edits are stored on the character as :attr:`Character.cost_overrides` (the category
rates) and :attr:`Character.item_cost_overrides` (the per-item rates); only values that
differ from the applicable default are kept. Advantages have no per-item table — they
keep the single :attr:`~..core.data_loader.TraitCosts.advantage_per_rank` rate. The
result drives :func:`~mm_companion.core.rules.has_cost_overrides`, which the sheet turns
into a "homebrew PP cost" notice. Powers keep their own Dev-mode override mechanism and
are out of scope here.
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    ability_category_key,
    ability_cost_rate,
    effective_pp_per_level,
    effective_trait_costs,
    resistance_category_key,
    resistance_cost_rate,
    skill_category_key,
    skill_cost_rate,
)
from mm_companion.core.rules.costs import (
    ABILITIES_CATEGORY,
    RESISTANCES_CATEGORY,
    SKILLS_CATEGORY,
)
from mm_companion.ui.widgets import make_spin_box

#: The editable category rates, in display order: ``(override key, label, unit suffix)``.
#: Every key but ``pp_per_level`` is a field of ``TraitCosts``.
_RATES: tuple[tuple[str, str, str], ...] = (
    ("ability_per_rank", "Ability", "PP / rank"),
    ("combat_per_rank", "Combat stat", "PP / rank"),
    ("resistance_per_rank", "Resistance", "PP / rank"),
    ("advantage_per_rank", "Advantage", "PP / rank"),
    ("skill_ranks_per_pp", "Skill (normal)", "ranks / PP"),
    ("skill_specialized_ranks_per_pp", "Skill (specialized)", "ranks / PP"),
    ("pp_per_level", "Points per Power Level", "PP / level"),
)

_FOCUSED_NOTE = (
    "Per-item rates override the category rate above for one trait only; a rate left "
    "equal to its category rate just follows it. Focused skills (Expertise, Close/Ranged "
    "Combat) price at the skill's own rate."
)


class _ItemRow:
    """One per-item override row: its spin box plus the category rate it inherits from."""

    __slots__ = ("category", "key", "global_key", "spin", "hint")

    def __init__(
        self, category: str, key: str, global_key: str, spin: QSpinBox, hint: QLabel
    ) -> None:
        self.category = category
        self.key = key
        self.global_key = global_key
        self.spin = spin
        self.hint = hint


class CostConfigDialog(QDialog):
    """A form over the non-power PP-cost rates; Save writes homebrew overrides."""

    def __init__(
        self, character: Character, game_data: GameData, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cost config")
        self._character = character
        self._data = game_data
        self._spins: dict[str, QSpinBox] = {}
        self._global_prev: dict[str, int] = {}
        self._items: list[_ItemRow] = []
        self._items_by_global: dict[str, list[_ItemRow]] = {}

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Homebrew the power-point cost of the non-power traits for this character. "
            "A rate left at its default is not overridden."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Everything scrolls: the per-item tables can be long.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)

        body.addWidget(self._build_category_group())
        body.addWidget(
            self._build_item_group(
                "Abilities",
                "PP / rank",
                ABILITIES_CATEGORY,
                self._data.abilities,
                lambda o: o.name,
                lambda o: o.key,
                ability_category_key,
                lambda o: ability_cost_rate(self._character, self._data, o),
            )
        )
        body.addWidget(
            self._build_item_group(
                "Resistances",
                "PP / rank",
                RESISTANCES_CATEGORY,
                self._data.resistances,
                lambda o: o.name,
                lambda o: o.key,
                resistance_category_key,
                lambda o: resistance_cost_rate(self._character, self._data, o),
            )
        )
        body.addWidget(
            self._build_item_group(
                "Skills",
                "ranks / PP",
                SKILLS_CATEGORY,
                self._data.skills,
                lambda o: o.name,
                lambda o: o.name,
                skill_category_key,
                lambda o: skill_cost_rate(self._character, self._data, o),
            )
        )

        note = QLabel(_FOCUSED_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        body.addWidget(note)
        body.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        reset = QPushButton("Reset to default")
        reset.setToolTip("Clear every homebrew rate and return to the ruleset defaults.")
        reset.clicked.connect(self._reset_to_default)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(520, 640)

    # -- widget construction --------------------------------------------------

    def _build_category_group(self) -> QGroupBox:
        """The top group of global category rates (the original Cost-config form)."""
        group = QGroupBox("Category rates")
        form = QFormLayout(group)
        current = self._current_values()
        for key, label, unit in _RATES:
            default = self._default_value(key)
            spin = make_spin_box(1, 9999, value=int(current[key]))
            spin.setToolTip(f"Default: {default} {unit}")
            spin.valueChanged.connect(lambda value, k=key: self._on_category_rate_changed(k, value))
            self._spins[key] = spin
            self._global_prev[key] = int(current[key])

            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(spin)
            unit_label = QLabel(unit)
            unit_label.setStyleSheet("color: palette(mid);")
            box.addWidget(unit_label)
            hint = QLabel(f"(default: {default})")
            hint.setStyleSheet("color: palette(mid);")
            box.addWidget(hint)
            box.addStretch()
            form.addRow(f"{label}:", row)
        return group

    def _build_item_group(
        self, title, unit, category, items, name_of, key_of, category_key_of, rate_of
    ) -> QGroupBox:
        """A per-item table for one category: a spin box per ability / resistance / skill."""
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setColumnStretch(0, 1)

        header = QLabel(f"Cost ({unit})")
        header.setStyleSheet("color: palette(mid);")
        grid.addWidget(QLabel(title.rstrip("s")), 0, 0)
        grid.addWidget(header, 0, 1, 1, 2)

        for row, item in enumerate(items, start=1):
            key = key_of(item)
            global_key = category_key_of(item)
            spin = make_spin_box(1, 9999, value=int(rate_of(item)))
            hint = QLabel(f"(cat: {self._spins[global_key].value()})")
            hint.setStyleSheet("color: palette(mid);")

            grid.addWidget(QLabel(name_of(item)), row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(hint, row, 2)

            record = _ItemRow(category, key, global_key, spin, hint)
            self._items.append(record)
            self._items_by_global.setdefault(global_key, []).append(record)
        return group

    # -- reactivity -----------------------------------------------------------

    def _on_category_rate_changed(self, key: str, value: int) -> None:
        """Re-baseline the per-item rows that were following this category rate."""
        old = self._global_prev.get(key, value)
        self._global_prev[key] = value
        for record in self._items_by_global.get(key, ()):
            record.hint.setText(f"(cat: {value})")
            if record.spin.value() == old:  # still following the category rate
                with QSignalBlocker(record.spin):
                    record.spin.setValue(value)

    # -- values ---------------------------------------------------------------

    def _current_values(self) -> dict[str, int]:
        """The character's current *effective* rate for each editable category key."""
        traits = effective_trait_costs(self._character, self._data)
        values = {key: getattr(traits, key) for key, _, _ in _RATES if key != "pp_per_level"}
        values["pp_per_level"] = effective_pp_per_level(self._character, self._data)
        return values

    def _default_value(self, key: str) -> int:
        """The ruleset default for a category rate, ignoring the character's overrides."""
        if key == "pp_per_level":
            return self._data.costs.power_level.pp_per_level
        return getattr(self._data.costs.traits, key)

    def _reset_to_default(self) -> None:
        for key, spin in self._spins.items():
            spin.setValue(self._default_value(key))  # cascades to following per-item rows
        for record in self._items:
            spin = record.spin
            with QSignalBlocker(spin):
                spin.setValue(self._default_value(record.global_key))

    def _save(self) -> None:
        """Store only the rates that differ from their default; clear the rest."""
        for key, spin in self._spins.items():
            value = spin.value()
            if value == self._default_value(key):
                self._character.cost_overrides.pop(key, None)
            else:
                self._character.cost_overrides[key] = value

        for record in self._items:
            category = self._character.item_cost_overrides.setdefault(record.category, {})
            category_rate = self._spins[record.global_key].value()
            if record.spin.value() == category_rate:  # follows the category → not an override
                category.pop(record.key, None)
            else:
                category[record.key] = record.spin.value()
        # Drop any now-empty category so a stock build serializes nothing.
        for category in (ABILITIES_CATEGORY, RESISTANCES_CATEGORY, SKILLS_CATEGORY):
            if not self._character.item_cost_overrides.get(category):
                self._character.item_cost_overrides.pop(category, None)
        self.accept()
