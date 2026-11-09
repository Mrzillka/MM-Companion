"""The cycle collector runs on the GUI thread, never on a worker's (``ui/gc_guard``).

A Qt object Python owns, caught in a reference cycle, is destroyed by whichever
thread collects it. Off the GUI thread that leaves its timers registered against
freed memory, and the next event-loop turn crashes — see docs/notes/testing.md.
"""

from __future__ import annotations

import gc
import threading

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from mm_companion.ui import gc_guard


class _CyclicTimerOwner(QObject):
    """A parentless QObject with a running timer, kept alive only by a cycle."""

    def __init__(self, record: list[str]) -> None:
        super().__init__()
        self._me = self  # the cycle
        self._record = record
        self._timer = QTimer(self)
        self._timer.start(60_000)

    def __del__(self) -> None:
        self._record.append(threading.current_thread().name)


def _allocate_past_the_threshold() -> list:
    """Enough live container objects to put generation 0 well past its threshold."""
    return [[] for _ in range(gc.get_threshold()[0] * 3)]


def test_the_ui_suite_runs_with_the_guard_installed() -> None:
    """The tests run the collector the way the app does (tests/ui/conftest.py)."""
    assert gc_guard.installed()
    assert not gc.isenabled()


def test_a_busy_worker_thread_never_collects_a_qt_object(qapp: QApplication) -> None:
    died_on: list[str] = []
    gc.collect()  # a clean slate, so generation 0 holds only what follows
    _CyclicTimerOwner(died_on)
    kept: list = []

    worker = threading.Thread(
        target=lambda: kept.append(_allocate_past_the_threshold()), name="socket-thread"
    )
    worker.start()
    worker.join()

    assert died_on == []  # a worker allocating never ran the collector
    assert gc_guard.collect_if_due() > 0
    assert died_on == [threading.main_thread().name]


def test_nothing_is_collected_until_it_is_due() -> None:
    gc.collect()

    assert gc_guard.collect_if_due() == 0


def test_the_guard_collects_on_its_timer(qapp: QApplication) -> None:
    """The app never calls collect_if_due itself: the GUI thread's timer does."""
    died_on: list[str] = []
    gc.collect()
    _CyclicTimerOwner(died_on)
    kept = _allocate_past_the_threshold()

    deadline = threading.Event()
    QTimer.singleShot(gc_guard.INTERVAL_MS * 4, deadline.set)
    while not died_on and not deadline.is_set():
        qapp.processEvents()
        deadline.wait(0.01)

    assert died_on == [threading.main_thread().name]
    del kept
