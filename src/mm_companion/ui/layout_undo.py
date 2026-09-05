"""Taking back a layout gesture, and interleaving that with taking back an edit.

Layout used to be invisible to undo, and that was defensible while the only
gesture was reordering a block: a mis-drag put a block one row out and the fix
was to drag it back. A page where every divider moves and any two blocks merge is
a page you can wreck in one careless drag, and `View › Reset Layout` — which
throws away the *whole* arrangement — is a poor answer to "I did not mean that".

Two pieces, and the split is deliberate:

* :class:`LayoutHistory` is the same snapshot idea
  :class:`~mm_companion.ui.undo.UndoController` uses, over
  ``json.dumps(canvas.arrangement())`` instead of the character. One entry per
  *completed* gesture, recorded on release rather than per frame, or a divider
  dragged across the page would fill the history with fifty steps nobody wants.

* :class:`UndoRouter` keeps the chronological order of the two histories and
  sends ``Ctrl+Z`` to whichever moved last. That is the "one visible history"
  the feature was asked for: you undo what you last did, whatever kind of thing
  it was, without having to know there are two stacks behind it.

The character's history is left completely alone. Its ``absorb``/``_rebase``
machinery replays edits onto stored entries and is the subtlest code in the
window; teaching it a second kind of entry to carry unchanged would be a real
risk for no gain, when a router outside it does the job.

**Layout stays global, not per character** (see
``docs/notes/sheet-and-blocks.md``), so undoing a layout step must never mark the
sheet dirty — a moved block is not an unsaved change to anybody's character.
"""

from __future__ import annotations

import json
from collections import deque
from enum import Enum

from PySide6.QtCore import QObject, Signal

#: How many layout gestures are kept. Generous, because a step costs a small JSON
#: string and the whole point is that a page tuned over an evening can be walked
#: back through.
LAYOUT_DEPTH = 50


class Step(Enum):
    """Which history a step went into."""

    CHARACTER = "character"
    LAYOUT = "layout"


class LayoutHistory(QObject):
    """Undo/redo over one canvas's arrangement.

    Records on demand rather than by listening: the canvas emits
    ``arrangement_changed`` for *every* structural move including the ones this
    class makes itself, and for the intermediate states of a drag. The host calls
    :meth:`record` when a gesture has finished, which is the only moment worth
    keeping.
    """

    stateChanged = Signal()

    def __init__(self, canvas, *, depth: int = LAYOUT_DEPTH, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._depth = max(1, int(depth))
        self._undo: deque[str] = deque(maxlen=self._depth)
        self._redo: list[str] = []
        self._baseline = self._snapshot()
        self._applying = False

    def _snapshot(self) -> str:
        return json.dumps(self._canvas.arrangement(), sort_keys=True, separators=(",", ":"))

    @property
    def applying(self) -> bool:
        """Whether this history is currently the one moving the canvas."""
        return self._applying

    def record(self) -> bool:
        """Keep the arrangement as it was before whatever just happened.

        A no-op when nothing actually moved, so a gesture that ended where it
        started — a divider nudged and put back, a block dropped where it already
        was — does not become a step that appears to do nothing.
        """
        if self._applying:
            return False
        current = self._snapshot()
        if current == self._baseline:
            return False
        self._undo.append(self._baseline)
        self._baseline = current
        self._redo.clear()
        self.stateChanged.emit()
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._baseline)
        self._apply(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._baseline)
        self._apply(self._redo.pop())
        return True

    def _apply(self, state: str) -> None:
        self._applying = True
        try:
            self._canvas.apply_arrangement(json.loads(state))
        finally:
            self._applying = False
        # Re-read rather than trusting *state*: applying an arrangement settles
        # live splitter sizes that the snapshot could not have known, and taking
        # the baseline from what is actually there is what stops that turning up
        # as a phantom step the next time anything is recorded.
        self._baseline = self._snapshot()
        self.stateChanged.emit()

    def rebase(self) -> None:
        """Take the current arrangement as the baseline without recording a step.

        For a change nobody should be able to undo *past* — restoring a saved
        layout at startup, or Reset Layout, which is itself the escape hatch.
        """
        self._baseline = self._snapshot()


class UndoRouter(QObject):
    """One visible history over two stacks: undo whatever moved last.

    Holds only the *order* — which history each step went into — and asks that
    history to do the work. Both stacks stay entirely themselves, which is what
    lets the character's keep its replay machinery untouched.
    """

    stateChanged = Signal()

    def __init__(self, character, layout: LayoutHistory, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._character = character
        self._layout = layout
        self._order: deque[Step] = deque(maxlen=LAYOUT_DEPTH * 2)
        self._redo_order: list[Step] = []
        # Set while this router is the one moving a history, so its own undo and
        # redo cannot be mistaken for the user making a fresh edit.
        self._driving = False
        if character is not None:
            character.stateChanged.connect(self._on_character_changed)
        layout.stateChanged.connect(self.stateChanged.emit)
        self._character_depth = self._character_depth_now()

    # -- keeping the order ----------------------------------------------------

    def _character_can_undo(self) -> bool:
        return bool(self._character is not None and self._character.can_undo)

    def _character_depth_now(self) -> int:
        """How many steps the character's history holds (see ``UndoController``)."""
        if self._character is None:
            return 0
        return int(self._character.undo_depth)

    def _on_character_changed(self) -> None:
        """Notice a *new* character step, so the order stays true.

        The controller announces plenty that is not a new step — a redo, an undo,
        the saved marker moving — so this watches the one thing that means one was
        added: its **depth** growing.

        It used to watch the stack becoming non-empty, which is only the *first*
        edit of a session. Every one after that went unrecorded, so an edit made
        after a layout gesture was filed behind it and ``Ctrl+Z`` moved the divider
        back rather than taking back what the user had just typed. The two came
        back in the wrong order and both eventually came back, which is exactly the
        kind of fault nobody reports and everybody notices.

        And nothing is noted while this router is itself driving: a redo pushes a
        state onto the undo stack, which is a depth the router is about to record
        in the order on its own.
        """
        before, self._character_depth = self._character_depth, self._character_depth_now()
        if not self._driving and self._character_depth > before:
            self._note(Step.CHARACTER)
        self.stateChanged.emit()

    def note_layout_step(self) -> None:
        """Called when a layout gesture has finished and was actually a change."""
        if self._layout.record():
            self._note(Step.LAYOUT)
            self.stateChanged.emit()

    def _note(self, step: Step) -> None:
        self._order.append(step)
        self._redo_order.clear()

    # -- moving through it ----------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return self._character_can_undo() or self._layout.can_undo

    @property
    def can_redo(self) -> bool:
        redo = self._character is not None and self._character.can_redo
        return bool(redo) or self._layout.can_redo

    def undo(self) -> bool:
        """Take back the last thing that happened, whichever kind it was."""
        step = self._order.pop() if self._order else self._fallback_undo()
        if step is None:
            return False
        if not self._perform(step, redo=False):
            # The history it named had nothing after all (an absorb dropped it);
            # try the other rather than swallowing the gesture.
            other = Step.LAYOUT if step is Step.CHARACTER else Step.CHARACTER
            if not self._perform(other, redo=False):
                return False
            step = other
        self._redo_order.append(step)
        self._character_depth = self._character_depth_now()
        self.stateChanged.emit()
        return True

    def redo(self) -> bool:
        step = self._redo_order.pop() if self._redo_order else self._fallback_redo()
        if step is None:
            return False
        if not self._perform(step, redo=True):
            other = Step.LAYOUT if step is Step.CHARACTER else Step.CHARACTER
            if not self._perform(other, redo=True):
                return False
            step = other
        self._order.append(step)
        self._character_depth = self._character_depth_now()
        self.stateChanged.emit()
        return True

    def _perform(self, step: Step, *, redo: bool) -> bool:
        self._driving = True
        try:
            if step is Step.LAYOUT:
                return self._layout.redo() if redo else self._layout.undo()
            if self._character is None:
                return False
            return self._character.redo() if redo else self._character.undo()
        finally:
            self._driving = False

    def _fallback_undo(self) -> Step | None:
        """What to undo when the order is empty but a history is not.

        The character's controller coalesces, so its first edit of a burst is
        undoable before this has been told about it; and an `absorb` can add a
        state without ever announcing one. Preferring the character here matches
        what the user was doing — typing — when the order does not know.
        """
        if self._character_can_undo():
            return Step.CHARACTER
        return Step.LAYOUT if self._layout.can_undo else None

    def _fallback_redo(self) -> Step | None:
        if self._character is not None and self._character.can_redo:
            return Step.CHARACTER
        return Step.LAYOUT if self._layout.can_redo else None


__all__ = ["LAYOUT_DEPTH", "LayoutHistory", "Step", "UndoRouter"]
