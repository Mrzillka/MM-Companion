"""Undo and redo for one character sheet: a stack of whole-model snapshots.

**Why snapshots and not commands.** Around sixty places across eleven blocks write
to the shared :class:`~mm_companion.core.character.Character`, and several of them
move more than one field in a single gesture (editing Power Level reconciles the
point budget; removing a skill drops its ranks, focuses and specializations). A
``QUndoCommand`` per mutation would be that many inverses to write and to keep
right. One snapshot per step needs none: :meth:`Character.to_dict` /
:meth:`~mm_companion.core.character.Character.restore` is the round trip save and
load already rest on, so **an undo runs the same code path as opening a file** —
which is the property that makes this safe to bolt onto a sheet this large.

**What it costs, and the three things that keep it cheap.**

* A **debounce**, exactly like :class:`~.session_player.SnapshotPusher`'s (whose
  per-edit ``to_dict()`` already proves the cost affordable): an edit only *arms* a
  timer, so a typed word or a dragged spin box is one step rather than six.
* A **string compare**. Snapshots are stored as JSON text, so deciding whether the
  model actually moved is a string equality rather than a recursive dict walk — and
  a block that emits ``changed`` without changing anything costs nothing. The text
  also detaches the entry from the live model, which a dict would not: ``to_dict``
  copies one level deep, so a stored dict would share its nested containers with a
  restored character and drift with it.
* A **depth cap**, so a long session cannot grow without bound.

**What is undoable** is everything that marks the sheet dirty — everything reaching
the bus's ``edited`` topic. A power's runtime state (switched on, held at a size rung,
which member of an array is live) is part of that: it is saved with the build, so it is
in every snapshot and a toggle steps back like any other change. What is *not* is the
gear in the character's hands — :attr:`~mm_companion.core.equipment.EquipmentItem.worn`
and ``current_speed`` — which stays out of the file and must survive an undo instead,
which is :meth:`Character.restore`'s job, not this module's.

**What is absorbed.** A change pushed in from outside the window — a GM applying a
condition or setting hero points over a session, a damage rung replayed onto an
open NPC sheet — becomes the new baseline instead of a step, via :meth:`absorb`.
Undo is for taking back what *this* user did; a player quietly reverting the GM's
Stunned, and pushing that back to the table, is not that.
"""

from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext

from PySide6.QtCore import QObject, QTimer, Signal

from mm_companion.core.character import Character

#: How long a burst of edits is allowed to coalesce into one undo step. Matched to
#: :data:`~.session_player.SNAPSHOT_DEBOUNCE_MS` — the same "one gesture, one
#: record" judgement about the same signals.
UNDO_COALESCE_MS = 400

#: How many steps back the history goes. A cap rather than a budget: a heavy
#: character's snapshot is a few tens of kilobytes of JSON, so this is single-digit
#: megabytes at worst, and nobody walks back fifty edits by hand.
UNDO_DEPTH = 50


def snapshot_of(character: Character) -> str:
    """One history entry: *character*'s build state as canonical JSON text.

    ``sort_keys`` is not cosmetic — the entries are compared as strings to decide
    whether anything changed, so a difference in key order would look like an edit.
    """
    return json.dumps(character.to_dict(), sort_keys=True, separators=(",", ":"))


def _replay(entry: str, before: dict, after: dict) -> str:
    """*entry* with the difference between *before* and *after* applied to it.

    One level into a dict, so a hero-point command patches
    ``characteristics.hero_points`` and leaves a Power Level the player edited
    locally alone; anything else is replaced whole (a condition list is not
    meaningfully mergeable, and the incoming one is the resolved truth).
    """
    state = json.loads(entry)
    for key in set(before) | set(after):
        old, fresh = before.get(key), after.get(key)
        if old == fresh:
            continue
        target = state.get(key)
        if isinstance(old, dict) and isinstance(fresh, dict) and isinstance(target, dict):
            for sub in set(old) | set(fresh):
                if old.get(sub) == fresh.get(sub):
                    continue
                if sub in fresh:
                    target[sub] = fresh[sub]
                else:
                    target.pop(sub, None)
        elif key in after:
            state[key] = fresh
        else:
            state.pop(key, None)
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def absorbing(sheet):
    """:meth:`UndoController.absorb` for *sheet*, or a no-op if it has no controller.

    The seam the two outside-the-window callers use, so neither imports this class
    and neither has to care whether the sheet it was handed has a history at all (a
    GM's read-only view of a player sheet does not).
    """
    controller = getattr(sheet, "undo", None)
    return controller.absorb() if controller is not None else nullcontext()


class UndoController(QObject):
    """The undo/redo history for one :class:`~.character_sheet.CharacterSheet`.

    In memory and per window: closing the sheet discards it, which is the promise
    the feature was asked for.
    """

    #: The stacks, the saved-state marker, or both moved — anything a menu-bar
    #: button's enabled state or the window's ``*`` marker is derived from.
    stateChanged = Signal()

    def __init__(
        self,
        sheet,
        *,
        coalesce_ms: int = UNDO_COALESCE_MS,
        depth: int = UNDO_DEPTH,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._sheet = sheet
        self._depth = max(1, int(depth))
        self._undo: list[str] = []
        self._redo: list[str] = []
        # The state at the top of the history: what the model is *committed* to,
        # which after a settled burst is what the model currently is.
        self._baseline = self._snapshot()
        self._saved: str | None = None
        # Set while this controller is the one moving the model, so its own work
        # cannot arm the timer and record itself.
        self._applying = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(coalesce_ms))
        self._timer.timeout.connect(self.flush)

        # Listens for itself, like SnapshotPusher: a controller that has to be wired
        # up by its host is a controller that can be built and quietly record
        # nothing. `edited` is the sheet's own "the user changed something" signal,
        # which is exactly the scope this history covers.
        sheet.edited.connect(self.note_edit)
        # And registers itself, so `absorbing(sheet)` finds it. The two callers that
        # push a change in from outside hold the sheet, not the window that owns it.
        sheet.set_undo(self)

    # -- recording ------------------------------------------------------------

    def note_edit(self) -> None:
        """The sheet reported an edit; the step is recorded once the burst settles.

        The state announcement is only for the *first* edit of a burst — that is when
        the Undo button has something to offer (:attr:`can_undo` counts a coalescing
        step, or the button would sit dead for the length of the debounce). The rest
        of the burst changes nothing anyone is showing.
        """
        if self._applying:
            return
        armed = self._timer.isActive()
        self._timer.start()
        if not armed:
            self.stateChanged.emit()

    @property
    def pending(self) -> bool:
        """Whether a coalesced step is armed and waiting."""
        return self._timer.isActive()

    def flush(self) -> None:
        """Record the pending step now, if the model really moved.

        The sync twin of the timer, and the reason this class needs no event loop to
        be tested. Called before anything that must not straddle a burst: an undo, a
        redo, a save, an absorbed change from outside.
        """
        self._timer.stop()
        snapshot = self._snapshot()
        if snapshot != self._baseline:
            self._undo.append(self._baseline)
            del self._undo[: max(0, len(self._undo) - self._depth)]
            self._baseline = snapshot
            # A fresh edit is the user branching away from the states they had
            # walked back through, so there is nothing left to redo.
            self._redo.clear()
        self.stateChanged.emit()

    # -- moving through the history -------------------------------------------

    @property
    def can_undo(self) -> bool:
        """Whether there is a state to go back to — counting one still coalescing."""
        return bool(self._undo) or self.pending

    @property
    def can_redo(self) -> bool:
        """Whether a state was undone and not yet branched away from."""
        return bool(self._redo)

    def undo(self) -> bool:
        """Step back one state. Returns whether there was one to step back to."""
        self.flush()
        if not self._undo:
            return False
        self._redo.append(self._baseline)
        self._apply(self._undo.pop())
        return True

    def redo(self) -> bool:
        """Step forward again. Returns whether there was a state to step forward to."""
        self.flush()
        if not self._redo:
            return False
        self._undo.append(self._baseline)
        self._apply(self._redo.pop())
        return True

    def _apply(self, state: str) -> None:
        """Put *state* on the model and make every block agree with it."""
        self._applying = True
        try:
            # Model first, then the widgets: every block's refresh reads the model,
            # so the fan-out has no order to get wrong. Runtime flags are carried
            # across inside restore(), which also has to happen before the reseed —
            # rendering an array normalises its live member.
            self._sheet.character.restore(json.loads(state))
            self._sheet.reseed()
        finally:
            self._applying = False
        # Re-read rather than trusting *state*: a render is allowed to normalise the
        # model, and taking the baseline from what is actually there is what stops
        # that showing up as a phantom step on the next flush.
        self._baseline = self._snapshot()
        self._timer.stop()
        self.stateChanged.emit()

    @contextmanager
    def absorb(self):
        """Run a change from outside this window without recording it.

        Any pending local edit is committed first, so it stays undoable; whatever the
        body does then becomes the new baseline. The redo stack is deliberately left
        alone — an incoming condition is not the user branching the history.
        """
        self.flush()
        before = self._baseline
        self._applying = True
        try:
            yield
        finally:
            self._applying = False
            self._timer.stop()
            self._baseline = self._snapshot()
            self._rebase(before, self._baseline)
            self.stateChanged.emit()

    def _rebase(self, before: str, after: str) -> None:
        """Replay an outside change onto every state already remembered.

        Without this, "not undoable" would only hold for the *next* Ctrl+Z: every
        stored state predates the GM's condition, so stepping back through the
        player's own earlier edits would quietly take it off again — and push that
        back to the table. Applying the same change to the whole history is what
        makes the promise hold however far back someone walks.
        """
        if before == after:
            return
        old, new = json.loads(before), json.loads(after)
        self._undo[:] = [_replay(entry, old, new) for entry in self._undo]
        self._redo[:] = [_replay(entry, old, new) for entry in self._redo]

    # -- the saved-state marker -----------------------------------------------

    def mark_saved(self) -> None:
        """Remember the state just written to disk as the clean one."""
        self.flush()
        self._baseline = self._snapshot()
        self._saved = self._baseline

    @property
    def has_saved_baseline(self) -> bool:
        """Whether there is a saved state to compare against at all.

        A sheet that has never been written has nothing to be clean *against*, so
        :meth:`at_saved_state` answers False for it — which is right for "is this
        on disk?" and wrong for "should the title show a marker?". A caller
        re-deriving the dirty flag asks this first and leaves a never-saved sheet
        to whatever its own edits said.
        """
        return self._saved is not None

    def at_saved_state(self) -> bool:
        """Whether the model currently matches what was last saved.

        Compares *content*, not a position in the stack. A position would lie the
        moment :meth:`absorb` moved the baseline without moving the stack, and it
        would also miss the honest case this gets right: walking back to the saved
        bytes by any route is clean, however many steps it took.
        """
        return self._saved is not None and self._baseline == self._saved

    def _snapshot(self) -> str:
        return snapshot_of(self._sheet.character)
