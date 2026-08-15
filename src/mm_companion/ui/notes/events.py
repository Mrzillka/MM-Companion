"""Workspace-wide notifications about note *files*.

A note belongs to no character, so renaming or deleting one is not an event about
the block whose picker did it — it is an event about the workspace, and every
block holding that file needs to hear it, on this sheet and on any other window
open at the time.

The picker used to carry its own ``noteRenamed``/``noteDeleted`` signals, which
reached exactly one block: the one that owned that picker. A second block with
the same note open (after a split, or on another sheet) kept the dead ref, never
noticed — the disk refresh is gated on the file still being there — and the next
keystroke wrote it straight back out, recreating a note the user had deleted and
leaving the two blocks silently disagreeing.

So the signal is module-level, like :func:`~..session_bridge.active_session`.
Emit through :func:`note_events`; connect to it once per block.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class NoteEvents(QObject):
    """The one hub. Do not construct — ask :func:`note_events`."""

    #: A note's file was renamed: ``(old_ref, new_ref)``.
    renamed = Signal(str, str)
    #: A note's file was removed from the workspace.
    deleted = Signal(str)


_EVENTS: NoteEvents | None = None


def note_events() -> NoteEvents:
    """The process-wide note-file hub, created on first use.

    Lazily, because importing this module must not need a ``QApplication`` — the
    headless tests import the notes layer without one.
    """
    global _EVENTS
    if _EVENTS is None:
        _EVENTS = NoteEvents()
    return _EVENTS
