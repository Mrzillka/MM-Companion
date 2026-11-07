"""Write down what went wrong, somewhere a windowed build can still be read.

The installed app has no console. PyInstaller's windowed build sets ``sys.stderr``
to ``None``, so an uncaught Python error — one raised inside a Qt slot or event
handler, which PySide reports and then carries on past — goes nowhere at all, and
a native crash leaves only an access-violation code in the Windows event log. That
is how a sheet came to go blank and then die with nothing on record to say why.

:func:`install` puts three things in the workspace's ``logs`` folder:

* ``errors.log`` — every uncaught Python exception, from the main thread, a worker
  thread, or (through ``sys.excepthook``, which PySide calls) a Qt callback, plus
  anything the UI chose to survive and :func:`log_exception` was told about;
* ``crash.log`` — :mod:`faulthandler`'s traceback of every thread when the process
  dies of a native fault, which is the only Python-level record a hard crash can
  leave;
* a line per session in ``crash.log`` saying when it started and which version,
  so a trace can be matched to the run that wrote it.

Stdlib only and Qt-free, so it can be installed before ``QApplication`` exists and
the headless entry points could use it too. The Qt half — routing Qt's own
warnings here — lives with the UI (see ``mm_companion.__main__``).

A failure inside a loop (a paint event that raises on every frame) must not grow
the log without bound, so each distinct failure is written out in full a few times
and then only counted.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import IO

ERRORS_LOG = "errors.log"
CRASH_LOG = "crash.log"

#: A log past this many bytes is rotated to ``<name>.1`` when the app starts.
MAX_LOG_BYTES = 1_000_000

#: How many times one distinct failure is written in full before it is only counted.
FULL_REPEATS = 3

_errors_path: Path | None = None
# Held open for the life of the process: faulthandler writes to a file descriptor
# from inside a signal handler, so the file has to be open before the crash.
_crash_file: IO[str] | None = None
_repeats: dict[str, int] = {}
_lock = threading.Lock()


def install(logs_dir: Path, *, version: str = "", crash_trace: bool = True) -> None:
    """Start recording errors and crashes under *logs_dir*. Safe to call twice.

    Never raises: a workspace that cannot be written to must not stop the app
    from starting, it only means there is nowhere to write things down.
    *crash_trace* off leaves :mod:`faulthandler` alone — for a host (the test
    runner) that has already pointed it somewhere of its own.
    """
    global _errors_path, _crash_file
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        _rotate(logs_dir / ERRORS_LOG)
        _rotate(logs_dir / CRASH_LOG)
        _errors_path = logs_dir / ERRORS_LOG
        if crash_trace and _crash_file is None:
            _crash_file = open(logs_dir / CRASH_LOG, "a", encoding="utf-8")  # noqa: SIM115
            _crash_file.write(f"=== {_now()} started {version or '?'} (pid {os.getpid()})\n")
            _crash_file.flush()
            faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        _errors_path = None
        return
    if getattr(sys.excepthook, "_mm_diagnostics", False):
        return
    previous_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def excepthook(
        kind: type[BaseException], value: BaseException, tb: TracebackType | None
    ) -> None:
        _write_exception("Uncaught exception", kind, value, tb)
        if sys.stderr is not None:
            previous_hook(kind, value, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        _write_exception(
            f"Uncaught exception in thread {name}",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )
        if sys.stderr is not None:
            previous_thread_hook(args)

    excepthook._mm_diagnostics = True  # type: ignore[attr-defined]
    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def errors_log_path() -> Path | None:
    """Where :func:`install` is writing errors, or ``None`` before it has run."""
    return _errors_path


def log_exception(context: str, error: BaseException | None = None) -> None:
    """Record an error the app caught and recovered from.

    Called from an ``except`` block, *error* defaults to the exception being
    handled. Recovering from a failure is right; recovering *silently* is how the
    failure survives to the next release.
    """
    if error is None:
        error = sys.exc_info()[1]
    if error is None:
        log_message(context)
        return
    _write_exception(context, type(error), error, error.__traceback__)
    if sys.stderr is not None:
        traceback.print_exception(type(error), error, error.__traceback__)


def log_message(text: str, *, key: str | None = None) -> None:
    """Record one line (a Qt warning, say), counted rather than repeated forever."""
    signature = key or text
    if not _first_few(signature):
        return
    _append(f"[{_now()}] {text}\n")


# -- internals ----------------------------------------------------------------


def _write_exception(
    context: str, kind: type[BaseException], value: BaseException, tb: TracebackType | None
) -> None:
    frames = traceback.extract_tb(tb) if tb is not None else []
    where = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "?"
    signature = f"{kind.__name__}@{where}"
    count = _first_few(signature)
    if not count:
        return
    lines = "".join(traceback.format_exception(kind, value, tb))
    note = ""
    if count == FULL_REPEATS:
        note = f"(seen {FULL_REPEATS} times; further repeats of this one are not written)\n"
    _append(f"[{_now()}] {context}\n{lines}{note}\n")


def _first_few(signature: str) -> int:
    """How many times *signature* has now been seen, or 0 once past the limit."""
    with _lock:
        count = _repeats.get(signature, 0) + 1
        _repeats[signature] = count
    return count if count <= FULL_REPEATS else 0


def _append(text: str) -> None:
    path = _errors_path
    if path is None:
        return
    try:
        with _lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _reset_for_tests() -> None:
    """Forget the repeat counts (each test starts from a clean slate)."""
    with _lock:
        _repeats.clear()
