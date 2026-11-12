"""Run Python's cycle collector on the GUI thread, and never on a worker's.

Python frees most objects the moment their last reference goes, on the thread that
dropped it. Objects caught in a reference cycle wait for the **cycle collector**
instead, and that runs whenever enough allocations have piled up — on *whichever
thread happens to be allocating at that moment*. In a session that is very often a
socket thread: the client's reader, the server's per-player threads.

That is fatal for a Qt object Python owns (a top-level widget, a parentless
``QObject``) that sits in a cycle, because collecting the wrapper runs the C++
destructor on that same thread. A Qt object must be destroyed on the thread it lives
on: one destroyed elsewhere cannot stop its timers (Qt says ``QObject::~QObject:
Timers cannot be stopped from another thread``), so they stay registered against
freed memory and the GUI thread's next event-loop turn fires one into it — a
segfault, blamed on whatever was running at the time. It killed one CI job in two
for weeks (see docs/notes/testing.md, "Qt objects must die on the GUI thread"), and
the live app runs the same threads.

Cycles through a Qt object are far too ordinary to hunt down one by one —
``self._helper = Helper(self)`` is one — so :func:`install` takes the collector off
automatic and makes the same decision from a GUI-thread timer instead: every
:data:`INTERVAL_MS`, collect whichever generations Python's own thresholds say are
due. Reference counting is untouched; only the cycle collector moves.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QCoreApplication, QTimer

#: How often the GUI thread checks whether a collection is due, in ms. Python checks
#: on every allocation; a few times a second is plenty, since garbage only waits a
#: little longer and the generations still decide how much each pass costs.
INTERVAL_MS = 250

_timer: QTimer | None = None


def collect_if_due() -> int:
    """Collect what the automatic collector would have by now, on *this* thread.

    The rule Python applies on allocation (``gc.get_threshold``/``gc.get_count``):
    nothing until the youngest generation is past its threshold, and then the oldest
    generation that is. Returns how many unreachable objects were found, ``0`` when
    nothing was due.
    """
    thresholds = gc.get_threshold()
    counts = gc.get_count()
    if thresholds[0] <= 0 or counts[0] <= thresholds[0]:
        return 0
    due = max(g for g, (c, t) in enumerate(zip(counts, thresholds, strict=True)) if c > t)
    return gc.collect(due)


def install(app: QCoreApplication) -> None:
    """Move the cycle collector onto *app*'s thread. Safe to call more than once."""
    global _timer
    gc.disable()
    if _timer is not None:
        return
    _timer = QTimer(app)
    _timer.setObjectName("gc-guard")
    _timer.timeout.connect(collect_if_due)
    _timer.start(INTERVAL_MS)


def uninstall() -> None:
    """Hand collection back to Python. For tests; the app keeps the guard for life."""
    global _timer
    timer, _timer = _timer, None
    if timer is not None:
        timer.stop()
        timer.deleteLater()
    gc.enable()


def installed() -> bool:
    return _timer is not None
