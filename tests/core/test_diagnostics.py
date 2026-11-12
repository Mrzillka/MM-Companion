"""The errors log: what a windowed build writes down instead of a console."""

from __future__ import annotations

import sys
import threading

import pytest

from mm_companion.core import diagnostics


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / diagnostics.ERRORS_LOG
    monkeypatch.setattr(diagnostics, "_errors_path", path)
    diagnostics._reset_for_tests()
    return path


def _raise(message: str) -> None:
    raise ValueError(message)


def test_a_recovered_error_is_written_with_its_traceback(log) -> None:
    try:
        _raise("the page could not be drawn")
    except ValueError:
        diagnostics.log_exception("Rebuilding the page failed")

    text = log.read_text(encoding="utf-8")
    assert "Rebuilding the page failed" in text
    assert "ValueError: the page could not be drawn" in text
    assert "_raise" in text  # the frame it came from


def test_one_failure_repeating_does_not_fill_the_log(log) -> None:
    for _ in range(20):
        try:
            _raise("again")
        except ValueError:
            diagnostics.log_exception("A paint failed")

    text = log.read_text(encoding="utf-8")
    assert text.count("ValueError: again") == diagnostics.FULL_REPEATS
    assert "further repeats" in text


def test_a_message_is_counted_the_same_way(log) -> None:
    for _ in range(10):
        diagnostics.log_message("Qt Warning: something")

    assert log.read_text(encoding="utf-8").count("something") == diagnostics.FULL_REPEATS


def test_nothing_is_written_before_install(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics, "_errors_path", None)
    diagnostics.log_message("nowhere to go")  # must not raise


def test_install_catches_uncaught_errors_from_any_thread(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    monkeypatch.setattr(sys, "stderr", None)  # as in the windowed build
    monkeypatch.setattr(diagnostics, "_errors_path", None)
    diagnostics._reset_for_tests()

    diagnostics.install(tmp_path / "logs", version="9.9.9", crash_trace=False)
    try:
        _raise("from a slot")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    worker = threading.Thread(target=_raise, args=("from a worker",), name="worker")
    worker.start()
    worker.join()

    text = (tmp_path / "logs" / diagnostics.ERRORS_LOG).read_text(encoding="utf-8")
    assert "ValueError: from a slot" in text
    assert "Uncaught exception in thread worker" in text
    assert "ValueError: from a worker" in text


def test_a_large_log_is_rotated_when_the_app_starts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    monkeypatch.setattr(diagnostics, "_errors_path", None)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / diagnostics.ERRORS_LOG).write_text("x" * (diagnostics.MAX_LOG_BYTES + 1))

    diagnostics.install(logs, crash_trace=False)

    assert (logs / (diagnostics.ERRORS_LOG + ".1")).exists()
    assert not (logs / diagnostics.ERRORS_LOG).exists()
