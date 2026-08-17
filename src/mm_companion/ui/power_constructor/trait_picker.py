"""Choosing *which trait* a power raises or lowers — and, when it needs one, which row.

An Enhanced Trait names traits; a Reduced Trait names the traits paying for them. Both
store a single **trait key**, and that key may be *qualified* to narrow it to one row of
the sheet (:func:`~mm_companion.core.rules.split_trait_key`):

===============================  ========================================================
``STR``, ``TOUGHNESS``           an ability or resistance — nothing to qualify
``Stealth``                      a whole skill, every row of it
``Expertise::Law``               one focus of a focused skill
``Stealth::spec::Urban``         one specialized (narrow, half-cost) rank pool
``Improved Critical::Sword``     an advantage bought for a subject
===============================  ========================================================

:class:`TraitPicker` is one cell that asks both halves of that: a grouped trait combo,
and beside it a *qualifier* control that appears only when the chosen trait has rows to
choose between. It reads and writes the composed key, so everything downstream — pricing,
the boost, the Enhances row, the sheet — still sees one string and has no idea a second
control exists.

The module also owns the trait *lists* themselves (:func:`fill_trait_combo` and the
:data:`TRAIT_SOURCES` vocabulary the data names them by), because the picker and the
lists are one decision: which traits a field offers, and how each of them is then
narrowed. :mod:`.common` re-exports both so no caller had to move.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QToolButton,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, ParameterSpec, Skill
from mm_companion.core.rules import (
    SPECIALIZED_ROW_MARKER,
    TRAIT_QUALIFIER_SEP,
    advantage_by_name,
    split_trait_key,
)
from mm_companion.ui.advantage_parameters import parameter_options
from mm_companion.ui.wheel_guard import guard_wheel


@dataclass(frozen=True)
class CellContext:
    """What one config-row cell is being built against — the data, and whose sheet.

    ``character`` is optional and often absent: the standalone Power Constructor builds
    powers with no build in hand. A cell that offers something *the character* has (this
    module's focus and specialization lists, an advantage subject drawn from their
    powers) must degrade to the catalog rather than assume one, which is why the
    character travels with the game data instead of being reached for separately.
    """

    game_data: GameData
    character: Character | None = None


#: Config-field ``source`` values the trait picker answers to. ``"traits"`` lists only
#: the traits a power can *buy* up or down; ``"boost_traits"`` adds the advantages, which
#: an Enhanced Trait may raise and a Reduced Trait lower; ``"all_traits"`` instead adds
#: the derived stats that can still be rolled, for a field asking what the player checks
#: rather than what the power changes.
TRAIT_SOURCE_BUYABLE = "traits"
TRAIT_SOURCE_BOOSTABLE = "boost_traits"
TRAIT_SOURCE_ALL = "all_traits"
TRAIT_SOURCES = (TRAIT_SOURCE_BUYABLE, TRAIT_SOURCE_BOOSTABLE, TRAIT_SOURCE_ALL)

#: The qualifier combo's "no qualifier" row — a skill enhanced as a whole, every row of
#: it at once, which is what an unqualified skill key means.
_WHOLE_SKILL_LABEL = "— the whole skill —"


def _disable_section_headings(combo: QComboBox) -> None:
    """Grey out a trait combo's section-heading rows (those carrying ``None`` data)
    so they read as group labels rather than selectable traits."""
    model = combo.model()
    for index in range(combo.count()):
        if combo.itemData(index) is None:
            item = model.item(index)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)


def _set_item_hint(combo: QComboBox, hint: str) -> None:
    """Hang ``hint`` on the row just added, as its hover text."""

    if hint:
        combo.setItemData(combo.count() - 1, hint, Qt.ItemDataRole.ToolTipRole)


def _advantage_hint(advantage) -> str:
    """What an advantage is, for the row that offers it.

    A bare list of ninety names says nothing, and the palette bricks beside this picker
    all hint — so the advantages do too, with the two things a *builder* needs on top of
    the description: what kind of advantage it is, and whether it can be taken more than
    once. Rich text so Qt wraps it.
    """

    parts = [f"<b>{escape(advantage.name)}</b>"]
    detail = ", ".join(advantage.types)
    if advantage.ranked:
        cap = f" (max {advantage.max_rank})" if advantage.max_rank else ""
        detail = f"{detail} · ranked{cap}".strip(" ·")
    elif detail:
        detail = f"{detail} · not ranked"
    if detail:
        parts.append(escape(detail))
    if advantage.description:
        parts.append(f"<br>{escape(advantage.description)}")
    return "<br>".join(parts)


def _skill_hint(skill: Skill) -> str:
    """What a skill is, and whether picking it will ask for a focus."""

    parts = [f"<b>{escape(skill.name)}</b>"]
    if skill.focused:
        parts.append("Focused — choose which focus this raises")
    if skill.description:
        parts.append(f"<br>{escape(skill.description)}")
    return "<br>".join(parts)


def _fill_trait_combo(
    combo: QComboBox,
    game_data,
    current: str,
    *,
    derived: bool = False,
    advantages: bool = False,
) -> None:
    """Populate ``combo`` with the character's traits (abilities, resistances, skills)
    grouped under disabled headings, and select ``current``. Data-driven — the trait
    names come from the game data, never hardcoded. Shared by Enhanced Trait's "which
    trait goes up" rows and any modifier config field with a trait ``source`` (the
    Reduced Trait flaw's "which trait goes down", Check Required's "which check").

    With ``derived`` the list also covers the numeric stats that are computed rather
    than bought — the derived Defence aggregate and the ``derived_traits`` named in
    ``system.json`` (Initiative). Those can be *checked* but not raised, so they belong
    in Check Required's "which check" picker and not in a trait-boosting one.

    With ``advantages`` it also covers the advantages, which an Enhanced Trait can raise
    and a Reduced Trait lower even though an advantage totals nothing on the sheet. They
    are equally deliberately *not* in the checked-trait list: nobody rolls an advantage.

    Every skill and advantage row carries its own hover text, since those are the two
    lists long enough that a name alone does not identify the thing.

    ``current`` may be a *qualified* key; it selects the trait the key names, and the
    qualifier is :class:`TraitPicker`'s half of the question.

    Prefer :func:`fill_trait_combo` where the field's ``source`` is to hand — it maps the
    data's own vocabulary onto these two switches so no caller has to."""
    combo.addItem("— choose a trait —", "")
    combo.addItem("Abilities", None)  # a disabled section heading
    for ability in game_data.abilities:
        combo.addItem(f"  {ability.name}", ability.key)
    combo.addItem("Resistances", None)
    for res in game_data.resistances:
        if derived or not res.derived:  # the derived Defence aggregate can't be bought
            combo.addItem(f"  {res.name}", res.key)
    combo.addItem("Skills", None)
    for skill in game_data.skills:
        combo.addItem(f"  {skill.name}", skill.name)
        _set_item_hint(combo, _skill_hint(skill))
    if advantages:
        combo.addItem("Advantages", None)
        for advantage in game_data.advantages:
            combo.addItem(f"  {advantage.name}", advantage.name)
            _set_item_hint(combo, _advantage_hint(advantage))
    if derived:
        extra = [d for d in game_data.system.derived_traits if combo.findData(d.key) < 0]
        if extra:
            combo.addItem("Other", None)
            for trait in extra:
                combo.addItem(f"  {trait.label}", trait.key)
    _disable_section_headings(combo)
    index = combo.findData(split_trait_key(current)[0] if current else current)
    combo.setCurrentIndex(index if index >= 0 else 0)


def fill_trait_combo(combo: QComboBox, game_data, current: str, source: str) -> None:
    """Populate a trait combo for a config field's declared ``source``.

    The one place the data's :data:`TRAIT_SOURCES` vocabulary is turned into which lists
    :func:`_fill_trait_combo` offers, so an effect column, a modifier field and a mod's
    own field all read the same word the same way. An unknown source reads as the
    plain buyable-traits list."""
    _fill_trait_combo(
        combo,
        game_data,
        current,
        derived=source == TRAIT_SOURCE_ALL,
        advantages=source == TRAIT_SOURCE_BOOSTABLE,
    )


class TraitPicker(QWidget):
    """A trait choice, plus the row of it — one composed key in, one composed key out.

    The second control is not always there, and that is the design: an ability has one
    row and asking which would be noise, while *Expertise* has as many rows as the hero
    has fields of study and picking the skill alone would silently raise all of them.
    What appears beside the trait combo is decided by what was chosen:

    * a **focused skill** → an editable combo of the character's own focuses, then the
      skill's suggested ones from the game data, and free text past both — the same
      freedom the Skills block's *Add focus* has, because an Enhanced Trait may perfectly
      well grant a focus the hero has not bought;
    * **any skill** → its specialized (narrow, half-cost) rank pools, alongside a "+"
      that names a new one, since a power may grant a pool no sheet carries yet;
    * an **advantage with a subject** → whatever its
      :class:`~mm_companion.core.data_loader.ParameterSpec` asks for, resolved through
      :mod:`mm_companion.ui.advantage_parameters` so it offers exactly what the
      Advantages block offers when the same advantage is bought;
    * anything else → nothing, and the trait combo takes the whole width.

    Emits :attr:`changed` on any edit to either half; the host row commits from
    :meth:`value`.
    """

    changed = Signal()

    def __init__(self, context: CellContext, source: str, value: str = "") -> None:
        super().__init__()
        self._context = context
        self._value = value
        self._skill: Skill | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._trait = QComboBox()
        fill_trait_combo(self._trait, context.game_data, value, source or TRAIT_SOURCE_BUYABLE)
        guard_wheel(self._trait)
        layout.addWidget(self._trait, 1)

        # Both qualifier shapes are built once and shown as needed: rebuilding the widget
        # on every trait change would drop the focus the player is halfway through typing.
        self._choice = QComboBox()
        self._choice.setVisible(False)
        guard_wheel(self._choice)
        layout.addWidget(self._choice, 1)
        self._text = QLineEdit()
        self._text.setVisible(False)
        layout.addWidget(self._text, 1)
        # Holds the qualifier's half of the row open when there is no qualifier, so the
        # trait combos of a stack of rows line up instead of each one widening to fill
        # whatever its own row did not need.
        self._spacer = QWidget()
        layout.addWidget(self._spacer, 1)
        # Naming a *new* specialized pool — the one answer no list can offer, because the
        # pool a power grants need not exist on anyone's sheet yet.
        self._add_pool = QToolButton()
        self._add_pool.setText("+")
        self._add_pool.setToolTip("Name a specialized (half-cost) rank pool for this skill")
        self._add_pool.setVisible(False)
        self._add_pool.clicked.connect(self._on_add_pool)
        layout.addWidget(self._add_pool)
        # The button's own width, held open on the rows that do not have one: a fixed
        # widget on some rows and not others is the same misalignment ``_spacer`` exists
        # to prevent, one column further right.
        self._pool_pad = QWidget()
        self._pool_pad.setFixedWidth(self._add_pool.sizeHint().width())
        layout.addWidget(self._pool_pad)

        self._sync_qualifier(split_trait_key(value)[1])
        self._trait.currentIndexChanged.connect(self._on_trait_changed)
        self._choice.currentIndexChanged.connect(lambda _i: self._emit())
        self._choice.editTextChanged.connect(lambda _t: self._emit())
        self._text.textChanged.connect(lambda _t: self._emit())

    # -- reading and writing the composed key ------------------------------

    def value(self) -> str:
        """The composed trait key — ``""`` while no trait is chosen."""

        base = self._trait.currentData() or ""
        if not base:
            return ""
        qualifier = self._qualifier()
        return f"{base}{TRAIT_QUALIFIER_SEP}{qualifier}" if qualifier else base

    def _qualifier(self) -> str:
        """The chosen row, from whichever control is in play — ``""`` when neither is.

        Asked with ``isHidden`` rather than ``isVisible``: a widget in a window nobody
        has shown yet is *not visible* however it was configured, and reading the
        qualifier as empty there would quietly drop the one a saved power came in with
        the first time its row committed.
        """

        if not self._choice.isHidden():
            if self._choice.isEditable():
                text = self._choice.currentText().strip()
                # An editable combo answers with what is *typed*, except where a row's
                # label is not its value: a specialized pool reads "Urban (specialized)"
                # and means ``spec::Urban``. Everything else is its own value, so a
                # focus still comes back exactly as the player wrote it.
                index = self._choice.findText(text)
                return str(self._choice.itemData(index) or "") if index >= 0 else text
            return str(self._choice.currentData() or "")
        if not self._text.isHidden():
            return self._text.text().strip()
        return ""

    def _emit(self) -> None:
        self._value = self.value()
        self.changed.emit()

    def _on_trait_changed(self, _index: int) -> None:
        # A new trait means a new question, so the old answer is dropped rather than
        # carried over — "Law" is not a focus of Close Combat.
        self._sync_qualifier("")
        self._emit()

    # -- which qualifier control, if any -----------------------------------

    def _sync_qualifier(self, current: str) -> None:
        """Show the control the chosen trait needs, seeded with ``current``."""

        self._choice.setVisible(False)
        self._text.setVisible(False)
        target = self._trait.currentData() or ""
        skill = None
        if target:
            skill = next((s for s in self._context.game_data.skills if s.name == target), None)
            if skill is not None:
                self._sync_skill_qualifier(skill, current)
            else:
                advantage = advantage_by_name(self._context.game_data, target)
                if advantage is not None and advantage.parameter is not None:
                    self._sync_advantage_qualifier(advantage.parameter, current)
        # Remembered for the "+", which has to know *which* skill it is naming a pool for
        # long after the trait combo was last read.
        self._skill = skill
        self._add_pool.setVisible(skill is not None)
        self._pool_pad.setVisible(skill is None)
        self._spacer.setVisible(self._choice.isHidden() and self._text.isHidden())

    def _pool_rows(self, skill: Skill, current: str) -> list[tuple[str, str]]:
        """The specialized pools this picker knows of, as ``(value, label)`` rows.

        The character's own, plus whatever ``current`` names. That second half is not
        politeness: a granted pool need not be on the sheet at all — naming one the hero
        has not bought is the point of the "+" — and a closed list that did not carry it
        would reselect the whole skill and silently drop the row a saved power came in
        with, the first time its cell committed.
        """

        character = self._context.character
        pools = list((character.specializations if character else {}).get(skill.name, []))
        if current.startswith(SPECIALIZED_ROW_MARKER):
            granted = current.removeprefix(SPECIALIZED_ROW_MARKER)
            if granted and granted not in pools:
                pools.append(granted)
        return [(f"{SPECIALIZED_ROW_MARKER}{name}", f"{name} (specialized)") for name in pools]

    def _sync_skill_qualifier(self, skill: Skill, current: str) -> None:
        """A focus (free), or one of the specialized pools (chosen from a list)."""

        character = self._context.character
        pools = self._pool_rows(skill, current)
        if skill.focused:
            focuses = list((character.focuses if character else {}).get(skill.name, []))
            # The hero's own focuses first — those are the rows already on the sheet, and
            # the ones an enhancement usually means — then the catalog's suggestions, then
            # the pools, which are a different kind of row and so come after both.
            suggestions = focuses + [f for f in skill.focuses if f not in focuses]
            self._fill_choice(
                [(f, f) for f in suggestions] + pools,
                current,
                editable=True,
                hint=skill.focus_note,
            )
            self._choice.lineEdit().setPlaceholderText("Focus")
            return
        # Shown even with no pools yet: it is what names the whole-skill default, and it
        # is where the pool the "+" beside it creates will appear.
        self._fill_choice([("", _WHOLE_SKILL_LABEL)] + pools, current, editable=False)

    def _on_add_pool(self) -> None:
        """Name a new specialized pool for the chosen skill, and select it.

        Deliberately writes *nothing* to the character. The power grants the pool and
        pays for it; the constructor often has no character to write to at all; and a
        granted row the sheet carries none of its own is exactly what
        :func:`~mm_companion.core.rules.granted_skill_rows` grows a muted row for. The
        skill's own catalog specializations are offered as suggestions and never as a
        closed list, the way the Skills block's *Add focus* offers its focuses.
        """

        skill = self._skill
        if skill is None:
            return
        offered = {
            value.removeprefix(SPECIALIZED_ROW_MARKER)
            for value, _ in self._pool_rows(skill, self._qualifier())
        }
        options = [name for name in skill.specializations if name not in offered]
        name, ok = QInputDialog.getItem(
            self, f"Add {skill.name} specialization", "Specialization:", options, 0, True
        )
        name = name.strip()
        if not ok or not name:
            return
        # Rebuilt through the ordinary path rather than by poking a row in: the new pool
        # is just another one this picker knows of, and ``_pool_rows`` already folds a
        # pool nobody has bought into the list and selects it.
        self._sync_qualifier(f"{SPECIALIZED_ROW_MARKER}{name}")
        self._emit()

    def _sync_advantage_qualifier(self, spec: ParameterSpec, current: str) -> None:
        """Whatever the advantage's own parameter spec asks for."""

        if spec.kind == "choice":
            options = parameter_options(spec, self._context.game_data, self._context.character)
            # A choice with nothing to choose from (a "powers" source with no character,
            # or none built yet) falls back to free text rather than an empty combo the
            # player cannot answer.
            if options:
                self._fill_choice([(v, label) for v, label in options], current, editable=False)
                return
        self._text.setPlaceholderText(spec.label)
        self._text.blockSignals(True)
        self._text.setText(current)
        self._text.blockSignals(False)
        self._text.setVisible(True)

    def _fill_choice(
        self,
        options: list[tuple[str, str]],
        current: str,
        *,
        editable: bool,
        hint: str = "",
    ) -> None:
        """(Re)stock the qualifier combo without its own signals writing back."""

        self._choice.blockSignals(True)
        self._choice.clear()
        self._choice.setEditable(editable)
        for value, label in options:
            self._choice.addItem(label, value)
        if editable:
            # By value, not by text: a pool's row reads "Urban (specialized)" and the
            # raw ``spec::Urban`` in the line edit would be both wrong and unreadable.
            index = self._choice.findData(current)
            self._choice.setCurrentText(self._choice.itemText(index) if index >= 0 else current)
        else:
            index = self._choice.findData(current)
            self._choice.setCurrentIndex(max(index, 0))
        self._choice.setToolTip(hint)
        self._choice.blockSignals(False)
        self._choice.setVisible(True)


def build_trait_cell(context: CellContext, column, initial: dict, commit) -> QWidget:
    """The ``trait`` repeatable-column cell: a :class:`TraitPicker` wired to ``commit``.

    Data-driven from the column's own ``source``, so which traits a row offers is the
    data's decision and not this widget's.
    """

    cell = TraitPicker(context, column.source, str(initial.get(column.key, "")))
    cell.changed.connect(commit)
    return cell
