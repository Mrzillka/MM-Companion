"""Quick rolls: saved rolls as a row of pills under the die, one click to reuse, named,
dragged to reorder, and kept in the settings.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.diagnostics import log_exception
from mm_companion.core.rules import RollSpec, same_attack
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer
from mm_companion.ui.roll_history import (
    MAX_QUICK_ROLLS,
    quick_roll_key,
)

# The quick-roll list is stored under this settings key as a list of plain dicts
# ``{"bonus": int, "penalty": int, "name"?: str}`` so no Qt types leak into the JSON
# settings layer. A roll saved off an attack made with action lines on it (All-Out
# Attack +5) also carries its **recipe** — ``"spec"`` (the attack as loaded, a
# ``RollSpec.to_dict``), ``"lines"`` (``[[key, value], …]``) and ``"circumstance"`` (the
# modifier box) — so a click re-arms the same lines and they leave their conditions
# again (the user's call, 2026-11-10: the pill used to keep the +15 and drop the
# All-Out Attack's Defense penalty).
QUICK_ROLLS_KEY = "quick_rolls"

#: How many recipes a panel remembers for the rolls it made, for a star to save.
RECIPE_MEMORY = 64


# The MIME type carrying a chip's index while it is dragged to reorder.
_DRAG_MIME = "application/x-mm-quick-roll"


def _params_label(params: dict) -> str:
    """The numbers a quick roll loads, as text, e.g. ``"+3"``.

    Only the numbers it *has*: a quick roll carries no DC (see
    :func:`~mm_companion.ui.roll_history.roll_parameters`).
    """
    modifier = params["bonus"] - params["penalty"]
    return f"{modifier:+d}"


#: What an unnamed quick roll is captioned by, before its modifier: "d20 +2".
PLAIN_CHIP = "d20"


def quick_label(params: dict) -> str:
    """A chip's caption: what it rolls and at what — "Perception +7", or "d20 +2".

    Both halves, always. A chip used to show its name *or* its numbers, so a named one
    hid its modifier and an unnamed one was a bare "+2" that said nothing of what it
    rolled. A chip with a recipe is named for the attack as rolled, which is long, so
    it is captioned short (:func:`recipe_caption`); the whole name is its tooltip.
    """
    if params.get("spec") and params.get("name"):
        return recipe_caption(str(params["name"]), _params_label(params))
    return f"{params.get('name') or PLAIN_CHIP} {_params_label(params)}"


#: The word an attack maneuver's name ends in, left off a pill: the Actions popup's
#: tiles call them "Accurate", "All-Out", "Power" too.
_MANEUVER_TAIL = re.compile(r"\s+Attack$")
#: A signed number at the end of a phrase: "All-Out Attack +5".
_TRAILING_NUMBER = re.compile(r"\s*[+\-−]\d+$")
#: How a power's attack check is named after the power: "Blast: Attack".
_ATTACK_TAIL = re.compile(r":\s*Attack$")


def recipe_caption(name: str, modifier: str) -> str:
    """A short caption for a pill saved off a maneuvered attack.

    The roll's own name is the whole story — "Rumbling: Attack +15 vs. Defense
    (Accurate Attack +5, All-Out Attack +5)" — and elided in a pill it lost the part
    that tells two such pills apart. Short, it is what the attack is, its number, and
    which maneuvers ride on it: "Rumbling +15 · Accurate, All-Out". A name that is not
    in that shape is left whole.
    """
    base, paren, lines = name.partition(" (")
    if not paren or not lines.endswith(")"):
        return name
    # "Blast: Attack +11 vs. Defense" → "Blast"; "Close Combat: Unarmed +8 vs. …" keeps
    # its "Unarmed", which is what tells it from the other close attacks.
    head = _TRAILING_NUMBER.sub("", base.split(" vs", 1)[0].strip())
    head = _ATTACK_TAIL.sub("", head).strip()
    picks = []
    for part in lines[:-1].split(","):
        part = _MANEUVER_TAIL.sub("", _TRAILING_NUMBER.sub("", part.strip()))
        if part:
            picks.append(part)
    caption = f"{head or base} {modifier}"
    return f"{caption} · {', '.join(picks)}" if picks else caption


def clean_recipe(entry: dict) -> dict:
    """The recipe fields of a stored or saved entry, checked; ``{}`` when it has none."""
    spec = entry.get("spec")
    lines = entry.get("lines")
    if not isinstance(spec, dict) or not isinstance(lines, list) or not lines:
        return {}
    clean_lines = []
    for line in lines:
        if not isinstance(line, (list, tuple)) or len(line) != 2:
            return {}
        key, value = line
        try:
            clean_lines.append([str(key), None if value is None else int(value)])
        except (TypeError, ValueError):
            return {}
    try:
        circumstance = int(entry.get("circumstance", 0) or 0)
    except (TypeError, ValueError):
        circumstance = 0
    return {"spec": spec, "lines": clean_lines, "circumstance": circumstance}


class _QuickRollButton(QToolButton):
    """A pill's caption: a click rolls it, a drag moves the pill along the row.

    The pill used to carry a ``⠿`` grip for the drag, a third control on something
    that is mostly one word. A drag only starts once the pointer has moved past the
    platform's drag distance, so a click is still a click.

    A tool button rather than a push button: a push button asks for the platform's
    default width, which made every pill a slab, and the theme's padding on it clipped
    any width worked out by hand.
    """

    def __init__(self, text: str, index: int) -> None:
        super().__init__()
        self._index = index
        self._press: QPoint | None = None
        # The whole caption; what is drawn may be cut short (see :meth:`_elide`).
        self._full = text
        self.setText(text)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def full_text(self) -> str:
        """The whole caption, however much of it is drawn."""
        return self._full

    def _chrome(self) -> int:
        return 2 * (int(theme.metric("space.sm")) + int(theme.metric("focus.width")))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The caption's width and a little air — not the platform's tool-button padding
        — and never past ``column.quick-roll-max``.

        The style asked for ~30px more than the text, which with the room kept for the
        ``×`` made a two-character pill three times its caption. The theme draws a tool
        button's box as just its focus ring, so the text needs that ring's width either
        side and nothing more. A long caption (a pill saved off an attack with three
        maneuvers named in it) is capped and elided: at its full width it widened the
        whole roller.
        """
        height = super().sizeHint().height()
        snug = self.fontMetrics().horizontalAdvance(self._full) + self._chrome()
        return QSize(min(snug, int(theme.metric("column.quick-roll-max"))), height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """An ellipsis and its air: the strip may squeeze a long pill that far, and never
        widens the roller for it."""
        least = self.fontMetrics().horizontalAdvance("…") + self._chrome()
        return QSize(min(self.sizeHint().width(), least), super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        room = max(0, self.width() - self._chrome())
        metrics = QFontMetrics(self.font())
        shown = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, room)
        if shown != self.text():
            self.setText(shown)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        moved = self._press is not None and (
            (event.position().toPoint() - self._press).manhattanLength()
            >= QApplication.startDragDistance()
        )
        if moved and event.buttons() & Qt.MouseButton.LeftButton:
            self._press = None
            # Let go of the press first, so the release the drag swallows is no click.
            self.setDown(False)
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(_DRAG_MIME, str(self._index).encode("ascii"))
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._press = None
        super().mouseReleaseEvent(event)


class _QuickChip(QFrame):
    """One quick roll as a pill, whose ``×`` shows only while the pointer is over it.

    The ``×`` **floats** over the pill's right end rather than sitting in its layout:
    it used to keep its room while hidden, which left every pill's caption off-centre
    with a blank stretch on its right. Floating, it takes no room at all, so the row
    never re-wraps under the pointer either. Right-click ▸ Remove does the same for
    anyone not using a mouse there.
    """

    def __init__(self, remove: QPushButton) -> None:
        super().__init__()
        self._remove = remove
        # Parented before anything can show it: a parentless widget is a window.
        remove.setParent(self)
        remove.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place_remove()

    def _place_remove(self) -> None:
        inset = int(theme.metric("space.xs"))
        height = max(1, self.height() - 2 * inset)
        width = self._remove.width()
        self._remove.setGeometry(self.width() - width - inset, inset, width, height)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._place_remove()
        self._remove.show()
        self._remove.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._remove.hide()
        super().leaveEvent(event)


class QuickRollStrip(FlowContainer):
    """The quick-roll chip host: a flow container that accepts reorder drops."""

    reordered = Signal(int, int)  # source index, insertion index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasFormat(_DRAG_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasFormat(_DRAG_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not event.mimeData().hasFormat(_DRAG_MIME):
            return
        source = int(bytes(event.mimeData().data(_DRAG_MIME)).decode("ascii"))
        target = self._drop_index(event.position().toPoint())
        event.acceptProposedAction()
        self.reordered.emit(source, target)

    def _drop_index(self, pos) -> int:
        """The list index the dragged chip should be inserted before."""
        layout = self.layout()
        if layout is None:
            return 0
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is None:
                continue
            geo = widget.geometry()
            if pos.y() <= geo.bottom() and pos.x() < geo.center().x():
                return i
        return layout.count()


class RollerQuickRollsMixin:
    """Part of :class:`~mm_companion.ui.dice_roller.DiceRollerPanel`; see the module docstring."""

    # -- quick rolls ---------------------------------------------------------

    def _load_quick_rolls(self) -> list[dict]:
        """The saved strip, normalised to what a quick roll is *now*.

        A workspace written before this carries a ``dc`` on every entry, and two
        chips that differed only by their DC collapse onto one key once it is
        dropped — so the read de-duplicates as well as truncating, or the strip
        would hold two chips that look and behave identically and only the first
        would ever answer a click.
        """
        stored = storage.load_settings().get(QUICK_ROLLS_KEY) or []
        rolls: list[dict] = []
        seen: set[tuple[str, int, int]] = set()
        for entry in stored:
            name = str(entry.get("name", "")).strip()
            # By the net, as the one Bonus/Penalty slider loads it (see quick_roll_key).
            net = int(entry.get("bonus", 0)) - int(entry.get("penalty", 0))
            clean = {"bonus": max(net, 0), "penalty": max(-net, 0)}
            if name:
                clean["name"] = name
            clean.update(clean_recipe(entry))
            key = quick_roll_key(clean)
            if key in seen:
                continue
            seen.add(key)
            rolls.append(clean)
            # Capped here rather than by slicing the file, so a settings file written
            # before there was a cap (or by hand) cannot hold the strip open past it.
            if len(rolls) == MAX_QUICK_ROLLS:
                break
        return rolls

    def _persist_quick_rolls(self) -> None:
        storage.update_settings(**{QUICK_ROLLS_KEY: self._quick_rolls})

    def quick_roll_keys(self) -> set[tuple[str, int, int]]:
        """What the strip holds, as :func:`quick_roll_key` tuples — for the stars."""
        return {quick_roll_key(entry) for entry in self._quick_rolls}

    def quick_rolls_full(self) -> bool:
        """Whether the strip has reached :data:`MAX_QUICK_ROLLS` and holds no more."""
        return len(self._quick_rolls) >= MAX_QUICK_ROLLS

    def toggle_quick_roll(self, params: dict) -> None:
        """Save *params* as a quick roll, or take it back out if it is already one.

        Public because it is what a history card's star ends up in, and the history —
        local or shared — is a sibling of this panel, not part of it. The star is a
        two-way switch, so this is one slot rather than a save and an unsave: the card
        reports the click and the panel, which owns the strip, decides which it was.

        A saved roll is matched by :func:`quick_roll_key`, so a click takes out the
        chip this card's roll saved and no other.
        """
        saved = self._find_quick_roll(params)
        if saved is None:
            self._add_quick_roll(params)
        else:
            self._remove_quick_roll(saved)

    def _add_quick_roll(self, params: dict, name: str | None = None) -> None:
        """Save a roll's name and numbers as a quick roll.

        The name comes with *params* (a roll travels named); *name* overrides it,
        which is how a caller with a better one says so. Refused when the strip
        already holds this exact roll — by :func:`quick_roll_key` — or when it is
        full. No DC is stored: see
        :func:`~mm_companion.ui.roll_history.roll_parameters` for why.
        """
        net = int(params["bonus"]) - int(params["penalty"])
        entry = {"bonus": max(net, 0), "penalty": max(-net, 0)}
        chosen = str(name if name is not None else params.get("name", "")).strip()
        if chosen:
            entry["name"] = chosen
        # A roll this panel made with action lines on it is saved with them.
        entry.update(clean_recipe(params) or self._recipe_for(params))
        if self._find_quick_roll(entry) is not None or self.quick_rolls_full():
            return
        self._quick_rolls.append(entry)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def _remove_quick_roll(self, entry: dict) -> None:
        if entry in self._quick_rolls:
            self._quick_rolls.remove(entry)
            self._persist_quick_rolls()
            self._rebuild_quick_strip()

    def _rename_quick_roll(self, entry: dict) -> None:
        """Ask for this chip's caption; an empty answer puts its numbers back.

        The one place a quick roll is named. Saving is a single click on a star with no
        dialog in the way, so the name comes afterwards, from the chip itself.

        The stored entry is looked up by :func:`quick_roll_key` rather than written
        through *entry*: only the dict a chip's own lambda closed over is the one in the
        list, and this is reachable from anywhere with a matching set of numbers.
        """
        stored = self._find_quick_roll(entry)
        if stored is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename quick roll",
            f"Name for {_params_label(stored)} (leave empty for none):",
            text=str(stored.get("name", "")),
        )
        if not ok:
            return
        name = name.strip()
        renamed = (
            dict(stored, name=name) if name else {k: v for k, v in stored.items() if k != "name"}
        )
        clash = self._find_quick_roll(renamed)
        if clash is not None and clash is not stored:
            # The name is part of a chip's identity now, so this would make two
            # chips one roll — and only the first would ever answer a click.
            return
        if name:
            stored["name"] = name
        else:
            stored.pop("name", None)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def _find_quick_roll(self, params: dict) -> dict | None:
        """The stored entry loading the same inputs as *params*, if there is one."""
        key = quick_roll_key(params)
        for entry in self._quick_rolls:
            if quick_roll_key(entry) == key:
                return entry
        return None

    def _reorder_quick_roll(self, source: int, insert_index: int) -> None:
        """Move the quick roll at *source* to *insert_index* (a drop position)."""
        if not 0 <= source < len(self._quick_rolls):
            return
        entry = self._quick_rolls.pop(source)
        # The insertion index was measured against the full list; account for the
        # entry we just removed if it sat before the drop point.
        if insert_index > source:
            insert_index -= 1
        insert_index = max(0, min(insert_index, len(self._quick_rolls)))
        self._quick_rolls.insert(insert_index, entry)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def remember_recipe(self, params: dict, spec: RollSpec, lines, circumstance: int) -> None:
        """Note how the roll *params* names was made — the attack *spec* as loaded, the
        action *lines* on it and the modifier box — so a star saving that roll saves a
        pill that re-arms them (see :data:`QUICK_ROLLS_KEY`)."""
        recipes = self._recipes
        key = quick_roll_key(params)
        recipes.pop(key, None)
        recipes[key] = {
            "spec": spec.to_dict(),
            "lines": [[str(k), v] for k, v in lines],
            "circumstance": int(circumstance),
        }
        while len(recipes) > RECIPE_MEMORY:
            recipes.pop(next(iter(recipes)))

    def _recipe_for(self, params: dict) -> dict:
        """The recipe remembered for the roll *params* names, or ``{}``.

        The session may have cut a long label short on the way (``MAX_LABEL_CHARS``),
        so a name that is the start of a remembered one, with the same numbers, counts.
        """
        recipes = self._recipes
        name, bonus, penalty = quick_roll_key(params)
        found = recipes.get((name, bonus, penalty))
        if found is None and name:
            found = next(
                (
                    recipe
                    for (known, b, p), recipe in reversed(recipes.items())
                    if (b, p) == (bonus, penalty) and known.startswith(name)
                ),
                None,
            )
        return dict(found) if found else {}

    def _apply_recipe(self, entry: dict) -> bool:
        """Load a pill's recipe — its attack, its modifier box and its action lines —
        in place of whatever the lane held. ``False`` when it has none to load.

        The attack is taken fresh off the character's list when it is still there under
        the same name (:func:`~mm_companion.core.rules.same_attack`), so a pill follows
        the sheet; otherwise it rolls as it was saved. The lines are re-armed by the
        host (:meth:`set_line_rearm`), each set back to its value as far as its cap now
        allows.
        """
        recipe = clean_recipe(entry)
        if not recipe:
            return False
        try:
            spec = RollSpec.from_dict(recipe["spec"])
        except Exception:  # noqa: BLE001 - a hand-edited settings file costs the recipe
            log_exception("Reading a quick roll's saved attack")
            spec = None
        if spec is None:
            return False
        fresh = next((c.spec for c in self.attack_choices() if same_attack(c.spec, spec)), None)
        for line in list(self.action_lines()):
            self.remove_action_line(line.key)
        self._modifier_spin.setValue(recipe["circumstance"])
        # Already this sheet's number (or the actor's), so not localised a second time.
        self.load_spec(fresh or spec, localize=False)
        if self._line_rearm is not None:
            for key, value in recipe["lines"]:
                try:
                    self._line_rearm(key, value)
                except Exception:  # noqa: BLE001 - one bad line must not cost the roll
                    log_exception("Re-arming a quick roll's action line")
        return True

    def set_line_rearm(self, rearm) -> None:
        """Have *rearm* put an action line back on the lane at a value —
        ``rearm(key, value)`` — for a pill that re-arms its lines. The panel reads no
        game data, so the host (its combat runner) knows what each key starts."""
        self._line_rearm = rearm

    def _apply_quick_roll(self, entry: dict) -> None:
        """Load a saved quick roll into the inputs and roll it immediately.

        A *named* chip also loads its name as a spec, so a saved roll reaches the
        table under that name. Its numbers stay in the sliders, so the spec carries
        no modifier of its own: the chip is a caption, not a second bonus, and
        **transient** like :meth:`roll_spec`'s, since the roll spends the sliders it
        names (:meth:`_spend_extras`).

        **The DC box is left as it is.** A quick roll carries none
        (:func:`~mm_companion.ui.roll_history.roll_parameters`): the difficulty in
        front of the player now beats one saved weeks ago.
        """
        if self._rolling:
            return
        if self._apply_recipe(entry):
            # Rolled once and let go, as a named pill is: the lines are spent by the roll.
            self._transient = True
            self._start_roll()
            return
        self._modifier_spin.setValue(int(entry["bonus"]) - int(entry["penalty"]))
        name = str(entry.get("name", "")).strip()
        self.load_spec(RollSpec(label=name) if name else None)
        self._transient = bool(name)
        self._start_roll()

    def _rebuild_quick_strip(self) -> None:
        """Render one chip per quick roll, and report that the strip changed.

        The report matters as much as the render. A chip coming or going changes what
        this panel's minimum is, and two things have to hear about it: the stars in a
        paired history (which roll is saved), and whatever is dividing space with this
        panel — :meth:`DiceRollerView._redivide`. ``updateGeometry`` is what makes the
        enclosing :class:`~mm_companion.ui.block_frame.BlockFrame` ask again, since a
        minimum that has just *shrunk* provokes no resize of its own.
        """
        while self._quick_flow.count():
            item = self._quick_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        for index, entry in enumerate(self._quick_rolls):
            self._quick_flow.addWidget(self._make_quick_chip(entry, index))
        # Nothing at all while empty: the empty history says how to fill it.
        self._quick_part.setVisible(bool(self._quick_rolls))
        # ...and in the compact shape the result moves under the die only while there
        # are pills beside it.
        self.sync_die_arrangement()
        self.updateGeometry()
        self.quickRollsChanged.emit()
        self.contentChanged.emit()

    def _make_quick_chip(self, entry: dict, index: int) -> QWidget:
        remove_button = QPushButton("×")
        # Not flat: it floats over the caption's end, so it needs a face of its own.
        remove_button.setFixedWidth(int(theme.metric("column.chip-button")))
        remove_button.setToolTip("Remove this quick roll")
        remove_button.clicked.connect(lambda _=False, e=entry: self._remove_quick_roll(e))

        chip = _QuickChip(remove_button)
        chip.setObjectName("quickChip")
        chip.setStyleSheet(
            "#quickChip {"
            f" border: {int(theme.metric('border.width'))}px solid"
            f" {theme.wash('accent.dice', 0.45)};"
            f" border-radius: {int(theme.metric('radius.chip'))}px;"
            f" background: {theme.wash('accent.dice', 0.08)};"
            "}"
        )
        # Preferred, not Maximum: a layout only widens an item whose policy lets it grow,
        # and the justified strip shares each line's spare width out between its pills.
        chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # Right-click is where a chip is named. Set on the frame alone: a child button
        # leaves a context-menu event unhandled, so it propagates up to here.
        chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        chip.customContextMenuRequested.connect(
            lambda pos, c=chip, e=entry: self._show_chip_menu(c, pos, e)
        )
        layout = QHBoxLayout(chip)
        small = int(theme.metric("space.xs"))
        side = int(theme.metric("space.sm"))
        layout.setContentsMargins(side, small, side, small)
        layout.setSpacing(small)

        caption = quick_label(entry)
        roll_button = _QuickRollButton(caption, index)
        again = " Its action lines go back on the lane." if entry.get("spec") else ""
        # A recipe pill's caption is short (recipe_caption): the hover has it in full.
        whole = str(entry["name"]) if entry.get("spec") and entry.get("name") else caption
        roll_button.setToolTip(
            f"{whole}\n\nClick to roll it.{again} Drag to reorder; right-click to rename "
            "or remove."
        )
        roll_button.clicked.connect(lambda _=False, e=entry: self._apply_quick_roll(e))
        # The caption takes whatever width the justified row gives the pill, centred.
        roll_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(roll_button)
        return chip

    def _show_chip_menu(self, chip: QWidget, pos: QPoint, entry: dict) -> None:
        """The chip's own menu: name it, or throw it away."""
        menu = QMenu(chip)
        menu.addAction("Rename…").triggered.connect(lambda: self._rename_quick_roll(entry))
        menu.addAction("Remove").triggered.connect(lambda: self._remove_quick_roll(entry))
        menu.exec(chip.mapToGlobal(pos))
