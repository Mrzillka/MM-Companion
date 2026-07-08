"""Section 4: powers.

The most complex part of a character. An "Add Power" button opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` brick-builder in
its own window; saving there hands the finished
:class:`~mm_companion.core.powers.Power` back through
:attr:`~mm_companion.ui.power_constructor.PowerConstructorWindow.powerSaved`, which
this section appends to the shared :class:`~mm_companion.core.character.Character`
and shows as a *card*. Each card reads top-to-bottom like a stat-block entry: a
header (name, assembled point cost, a ⚠ marker when the power breaks a Power Level
cap, and — for a runtime-gated power — an on/off switch), the free-text description,
a per-effect summary listing each effect's extras and flaws, and a bottom line
dedicated to roll information (attack bonus, save DC). Hovering the card reveals the
full auto-generated game-term breakdown as a tooltip, the same data the Power
Constructor shows while building. Each card carries an edit button that reopens the
constructor pre-loaded with that power — editing a deep copy that replaces the
original in place on save — and a remove button. It follows the standard section
contract (``data`` + ``character`` constructor, ``changed`` signal, ``set_locked``)
so it slots into the sheet like the others, and — because saved powers live on the
model — a loaded character repopulates its list at construction.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    ModifierSelection,
    Power,
    PowerEffectInstance,
)
from mm_companion.core.rules import (
    array_alternate_cost,
    array_base,
    array_base_index,
    array_members,
    debilitated_traits,
    effect_attack_skill_bonus,
    effect_effective_rank,
    effect_stat_rows,
    linked_group,
    power_array_violations,
    power_display_cost,
    power_pl_violations,
    power_runtime_gates,
    powers_points_spent,
)
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import hline_separator, title_with_cost

# Tints for a stat a modifier changed, matching the Power Constructor's
# PowerTermsView: an extra improved it (green), a flaw limited it (red).
_TINT_BETTER = "#2e9e4f"
_TINT_WORSE = "#d15b5b"
_TINTS = {"better": _TINT_BETTER, "worse": _TINT_WORSE}


class PowersSection(TitledSection):
    """Powers section: launches the Power Constructor and lists saved powers."""

    changed = Signal()

    def __init__(
        self,
        data: GameData,
        character: Character,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._locked = False
        # Keep constructor windows referenced so Qt doesn't garbage-collect them
        # the moment the click handler returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)
        self._empty = QLabel("No powers yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        # The saved powers stack above the Add button, one removable row each.
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list_host)

        self._add_button = QPushButton("Add Power")
        self._add_button.clicked.connect(self._open_constructor)
        layout.addWidget(self._add_button)

        # Seed from the (possibly loaded) model.
        self._rebuild_list()

    # -- constructor lifecycle --------------------------------------------
    def _open_constructor(self) -> None:
        window = PowerConstructorWindow(self._data, character=self._character)
        window.powerSaved.connect(self._on_power_saved)
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_saved(self, power: Power) -> None:
        self._character.powers.append(power)
        self._rebuild_list()
        self.changed.emit()

    def _edit_power(self, power: Power) -> None:
        """Reopen the constructor pre-loaded with an existing power for editing.

        The constructor edits a deep copy and hands it back on save; the copy then
        replaces the original in place (identity match), so an unsaved close is a
        no-op and a save swaps in exactly the power that was opened.
        """
        window = PowerConstructorWindow(self._data, character=self._character, power=power)
        window.powerSaved.connect(
            lambda edited, original=power: self._on_power_edited(original, edited)
        )
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_edited(self, original: Power, edited: Power) -> None:
        for index, existing in enumerate(self._character.powers):
            if existing is original:  # identity, not value — powers can be equal
                self._character.powers[index] = edited
                break
        else:  # the original was removed while the editor was open — treat as an add
            self._character.powers.append(edited)
        self._rebuild_list()
        self.changed.emit()

    def _on_window_closed(self, window: PowerConstructorWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    # -- power list -------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the cards from the current character state.

        The public seam the sheet calls when a fact *outside* this section changes a
        power's displayed numbers — an ability (a Strength-Based Damage folds in
        Strength; an attack power's PL cap tracks Attack) or the character's Power
        Level (which sets every attack cap). Re-derives cost, effective ranks, roll
        values, the PL-breach warning, and the tooltip. It only reads the model, so it
        never emits :attr:`changed` (no signal loop back to the triggering section).
        """
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the row per power from the model, toggling the empty label."""
        self._normalize_arrays()  # exactly one active member per array before drawing
        while self._list_layout.count():
            widget = self._list_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for power in self._character.powers:
            self._list_layout.addWidget(self._make_card(power))
        self._empty.setVisible(not self._character.powers)
        # Keep the section title's running point cost current.
        self.set_block_title(
            title_with_cost("Powers", powers_points_spent(self._character, self._data))
        )

    def _make_card(self, power: Power) -> QFrame:
        """A stat-block card for one power: header, description, effects, roll line.

        The whole card carries the full game-term breakdown on its tooltip (the same
        data the Power Constructor shows while building), so hovering reveals every
        derived system value.
        """
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setToolTip(self._system_tooltip(power))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        layout.addWidget(self._header_row(power))

        if power.description:
            desc = QLabel(power.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: gray; font-style: italic;")
            layout.addWidget(desc)

        # A cross-power relationship note (Alternate Effect of / Linked with), and —
        # on an array's base — a picker for which member is currently active.
        note = self._relationship_note(power)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet("color: #6a86c0; font-style: italic;")
            layout.addWidget(label)
        selector = self._array_selector(power)
        if selector is not None:
            layout.addWidget(selector)

        effects = self._effects_block(power)
        if effects is not None:
            layout.addWidget(effects)

        # A dedicated bottom line for the numbers that come up mid-play: the attack
        # bonus and the save DC each effect imposes.
        layout.addWidget(hline_separator())
        layout.addWidget(self._rolls_label(power))
        return card

    def _header_row(self, power: Power) -> QWidget:
        """Name + PL warning on the left; the on/off switch, cost, and edit/remove
        chrome on the right.

        Returns a host widget (not a bare layout) so every child has a parent the
        moment it is created. Calling ``setVisible(True)`` on a *parentless* widget
        shows it as a momentary top-level window — on Windows that flashes a small
        window on screen and is slow to realize; the edit/remove buttons hit exactly
        that path, so the header must own them before their visibility is set.
        """
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        name = QLabel(power.name or "Unnamed Power")
        # A Debilitated condition naming this power loses it — strike the header through
        # and redden it (display-only; the power's point cost is untouched).
        if power.name and power.name in debilitated_traits(self._character, self._data):
            name.setStyleSheet("font-weight: bold; font-size: 14px; color: #d15b5b;")
            font = name.font()
            font.setStrikeOut(True)
            name.setFont(font)
            name.setToolTip("Debilitated — this power is effectively lost")
        else:
            name.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(name)

        # A power that breaks a PL cap — or is an invalid Alternate Effect (costs more
        # than its base) — carries a warning marker naming the breach; enforcement is a
        # warning for now (see storage.pl_enforcement).
        violations = power_pl_violations(
            power, self._character, self._data
        ) + power_array_violations(power, self._character, self._data)
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet("color: #d1a01e; font-weight: bold;")
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)
        layout.addStretch()

        # A power with a runtime gate (Activation / Removable / a Sustained toggle)
        # gets an on/off switch; while off its standing bonuses drop off the sheet.
        if power_runtime_gates(power, self._data):
            active = QCheckBox("Active")
            active.setChecked(self._power_is_active(power))
            active.setToolTip("Switch this power on/off — its bonuses apply only while active.")
            active.setEnabled(not self._locked)
            active.toggled.connect(lambda on, p=power: self._set_power_active(p, on))
            layout.addWidget(active)

        # An Alternate Effect contributes only its flat pooled cost; every other power
        # shows its full assembled cost (power_display_cost handles the distinction).
        cost = QLabel(f"{power_display_cost(power, self._character, self._data)} PP")
        cost.setEnabled(False)
        layout.addWidget(cost)

        # Add each button to the (host-owned) layout *before* setting visibility:
        # addWidget reparents it to `host`, so setVisible acts on a parented child.
        # Calling setVisible on a still-parentless widget shows it as a momentary
        # top-level window (the Windows flash / lag this method's docstring warns of).
        edit = QPushButton("✎")
        edit.setFixedWidth(24)
        edit.setToolTip("Edit this power")
        edit.clicked.connect(lambda _checked=False, p=power: self._edit_power(p))
        layout.addWidget(edit)
        edit.setVisible(not self._locked)  # editing chrome hidden in view mode

        remove = QPushButton("✕")
        remove.setFixedWidth(24)
        remove.setToolTip("Remove this power")
        remove.clicked.connect(lambda _checked=False, p=power: self._remove_power(p))
        layout.addWidget(remove)
        remove.setVisible(not self._locked)  # editing chrome hidden in view mode
        return host

    # -- effect summary (name + extras/flaws) -----------------------------
    def _effects_block(self, power: Power) -> QWidget | None:
        """A stacked, per-effect summary; ``None`` for a power with no effects."""
        if not power.effects:
            return None
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(3)
        for index, effect in enumerate(power.effects):
            layout.addWidget(self._effect_summary(power, effect, index))
        return host

    def _effect_summary(self, power: Power, effect: PowerEffectInstance, index: int) -> QWidget:
        """One effect: its name and effective rank, a composite role note, and its
        attached extras (green) and flaws (red)."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel(self._effect_title(effect))
        title.setStyleSheet("font-weight: bold;")
        header.addWidget(title)
        note = self._role_note(power, index)
        if note:
            role = QLabel(note)
            role.setStyleSheet("color: gray; font-style: italic;")
            header.addWidget(role)
        header.addStretch()
        layout.addLayout(header)

        extras = self._modifier_names(effect.extras)
        if extras:
            label = QLabel("Extras: " + ", ".join(extras))
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_TINT_BETTER};")
            layout.addWidget(label)
        flaws = self._modifier_names(effect.flaws)
        if flaws:
            label = QLabel("Flaws: " + ", ".join(flaws))
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_TINT_WORSE};")
            layout.addWidget(label)
        return box

    def _effect_title(self, effect: PowerEffectInstance) -> str:
        """``"Damage 8"`` — the effect's name at its effective rank (a Strength-Based
        Damage folds in the wielder's Strength, matching the constructor)."""
        base = next((e for e in self._data.effects if e.id == effect.effect_id), None)
        rank = effect_effective_rank(effect, self._data, self._character)
        return f"{base.name if base else effect.effect_id} {rank}"

    def _modifier_names(self, selections: list[ModifierSelection]) -> list[str]:
        """Resolve each selection to its modifier name, tagging a ranked one taken
        above rank 1 with its rank (e.g. ``"Accurate ×2"``)."""
        catalog = self._data.modifier_catalog()
        names: list[str] = []
        for selection in selections:
            modifier = catalog.get(selection.modifier_id)
            if modifier is None:
                continue
            label = modifier.name
            if modifier.ranked and selection.rank > 1:
                label = f"{modifier.name} ×{selection.rank}"
            names.append(label)
        return names

    def _role_note(self, power: Power, index: int) -> str:
        """A composite effect's part: ``"base"``/``"alternate …"`` for an array or
        ``"linked"``; empty for a single or independent-multi effect."""
        if len(power.effects) < 2:
            return ""
        if power.structure == STRUCTURE_LINKED:
            return "linked"
        if power.structure == STRUCTURE_ARRAY:
            if index == array_base_index(power, self._data, self._character):
                return "base"
            return f"alternate ({array_alternate_cost(self._data)} pt)"
        return ""

    # -- roll line --------------------------------------------------------
    def _rolls_label(self, power: Power) -> QLabel:
        """The bottom roll line; a muted placeholder when the power rolls nothing."""
        text = self._rolls_text(power)
        label = QLabel(f"🎲 {text}" if text else "No attack or resistance roll")
        label.setWordWrap(True)
        if text:
            label.setStyleSheet("color: #6a86c0;")  # a calm blue reserved for dice info
        else:
            label.setEnabled(False)
        return label

    def _rolls_text(self, power: Power) -> str:
        """The attack bonus and save DC each effect imposes, read from the same
        game-term rows the constructor shows; effect-prefixed for a multi-effect power."""
        multi = len(power.effects) > 1
        parts: list[str] = []
        for effect in power.effects:
            attack_bonus = effect_attack_skill_bonus(effect, self._character, self._data)
            rows = {
                r.key: r
                for r in effect_stat_rows(effect, self._data, self._character, attack_bonus)
            }
            segments = []
            if "check" in rows:
                segments.append(rows["check"].value)
            if "resistance" in rows:
                segments.append(rows["resistance"].value)
            elif "effect_dc" in rows:  # a save DC with no shown check/resistance phrase
                segments.append(rows["effect_dc"].value)
            if not segments:
                continue
            line = " · ".join(segments)
            if multi:
                base = next((e for e in self._data.effects if e.id == effect.effect_id), None)
                line = f"{base.name if base else effect.effect_id}: {line}"
            parts.append(line)
        return "    ".join(parts)

    # -- hover tooltip: the full game-term breakdown ----------------------
    def _system_tooltip(self, power: Power) -> str:
        """Rich-text breakdown of every effect's game-term stats for the card tooltip.

        Mirrors the Power Constructor's PowerTermsView: a structure header for a
        composite power, then each effect at its effective rank with its stat rows,
        each modifier-changed value tinted green (better) or red (worse)."""
        if not power.effects:
            return ""
        blocks: list[str] = []
        header = self._structure_header(power)
        if header:
            blocks.append(f"<b>{html.escape(header)}</b>")
        for index, effect in enumerate(power.effects):
            attack_bonus = effect_attack_skill_bonus(effect, self._character, self._data)
            title = html.escape(self._effect_title(effect))
            note = self._role_note(power, index)
            if note:
                title += f" <i>{html.escape(note)}</i>"
            rows = []
            for stat in effect_stat_rows(effect, self._data, self._character, attack_bonus):
                value = html.escape(stat.value)
                tint = _TINTS.get(stat.change)
                if tint:
                    value = f"<span style='color:{tint}'>{value}</span>"
                rows.append(f"{html.escape(stat.label)}: {value}")
            body = "<br>".join(rows)
            blocks.append(f"<p style='margin:4px 0 0 0'><b>{title}</b><br>{body}</p>")
        return "".join(blocks)

    @staticmethod
    def _structure_header(power: Power) -> str:
        if len(power.effects) < 2:
            return ""
        if power.structure == STRUCTURE_LINKED:
            return "Linked (all effects activate together)"
        if power.structure == STRUCTURE_ARRAY:
            return "Array (one effect active at a time)"
        return ""

    def _remove_power(self, power: Power) -> None:
        if power in self._character.powers:
            self._character.powers.remove(power)
            self._rebuild_list()
            self.changed.emit()

    # -- cross-power relationships ----------------------------------------
    def _relationship_note(self, power: Power) -> str:
        """A muted note naming this power's cross-power ties, or empty for none.

        Reports an Alternate Effect's resolved base and the (transitively closed)
        set of powers it switches on/off with."""
        parts: list[str] = []
        base = array_base(self._character, power)
        if base is not None:
            parts.append(f"Alternate Effect of {base.name or 'Unnamed Power'}")
        linked = [p for p in linked_group(self._character, power) if p is not power]
        if linked:
            names = ", ".join(p.name or "Unnamed Power" for p in linked)
            parts.append(f"Linked with {names}")
        return " · ".join(parts)

    def _array_selector(self, power: Power) -> QWidget | None:
        """On an array's base card, a picker for which member is currently active.

        Only the base carries it (an alternate points *at* the base), and only when
        the array actually has alternates. Choosing a member activates it and gates
        the others off (via ``array_active`` → ``rules.effect_is_active``)."""
        if power.alternate_of:
            return None  # an alternate — the selector lives on its base
        members = array_members(self._character, power)
        if len(members) < 2:
            return None
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Active:"))
        combo = QComboBox()
        for member in members:
            combo.addItem(member.name or "Unnamed Power", member.id)
        active = next((m for m in members if m.array_active), members[0])
        index = combo.findData(active.id)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.setToolTip("Only one array member runs at a time — pick the active one.")
        combo.setEnabled(not self._locked)
        guard_wheel(combo)
        combo.currentIndexChanged.connect(
            lambda _i, ms=members, c=combo: self._set_array_active(ms, c.currentData())
        )
        row.addWidget(combo)
        row.addStretch()
        return host

    def _normalize_arrays(self) -> None:
        """Keep exactly one active member per cross-power array before rendering.

        Each fresh alternate defaults ``array_active=True`` like the base, so an array
        can transiently have several 'active' members; this collapses each array to a
        single active one (keeping the current choice when there is exactly one, else
        defaulting to the base). Any power not in a multi-member array is forced active
        so a former alternate is never left permanently gated off. Pure runtime
        housekeeping — it doesn't emit :attr:`changed`."""
        handled: set[str] = set()
        for power in self._character.powers:
            base = array_base(self._character, power) or power
            if base.id in handled:
                continue
            handled.add(base.id)
            members = array_members(self._character, base)
            if len(members) < 2:
                base.array_active = True
                continue
            active = [m for m in members if m.array_active]
            chosen = active[0] if len(active) == 1 else members[0]
            for member in members:
                member.array_active = member is chosen

    def _set_array_active(self, members: list[Power], member_id: str) -> None:
        """Make one array member the active one and gate the rest off, then re-derive."""
        for member in members:
            member.array_active = member.id == member_id
        self._rebuild_list()
        self.changed.emit()

    # -- runtime on/off ---------------------------------------------------
    @staticmethod
    def _power_is_active(power: Power) -> bool:
        """Whether every runtime switch on the power is currently in its 'on' state."""
        return power.activated and power.item_present and all(e.toggled_on for e in power.effects)

    def _set_power_active(self, power: Power, active: bool) -> None:
        """Flip the power's runtime switches — and its whole linked group — together.

        A single "Active" control drives whichever gate the power carries (Activation,
        Removable, or a Sustained toggle); ``rules.effect_is_active`` reads only the
        flags the power's gates make relevant. Linked powers switch on/off as one, so
        every member of :func:`~mm_companion.core.rules.linked_group` is flipped too.
        The ``changed`` signal is already wired to refresh the stats/skills sections,
        so the boosted totals update live.

        The cards are rebuilt too: switching off a power that boosts an ability
        (Enhanced Trait) lowers the *effective* ability another power reads — a
        Strength-Based Damage's rank, PL cap, and save DC all move with it.
        """
        for member in linked_group(self._character, power):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.changed.emit()

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points (Add / Remove)."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        self._rebuild_list()
