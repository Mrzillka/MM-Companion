"""The portable bootstrap redirects the workspace only for a flagged frozen build."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mm_companion.__main__ import _apply_portable_home
from mm_companion.core import storage


def _freeze(monkeypatch: pytest.MonkeyPatch, exe_dir: Path) -> None:
    """Pretend we are a PyInstaller build whose executable lives in *exe_dir*."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "MM-Companion.exe"))


def test_portable_flag_redirects_workspace_beside_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(storage.HOME_ENV_VAR, raising=False)
    _freeze(monkeypatch, tmp_path)
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")

    _apply_portable_home()

    assert os.environ[storage.HOME_ENV_VAR] == str(tmp_path / "data")


def test_no_flag_leaves_workspace_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(storage.HOME_ENV_VAR, raising=False)
    _freeze(monkeypatch, tmp_path)  # frozen, but no portable.flag beside the exe

    _apply_portable_home()

    assert storage.HOME_ENV_VAR not in os.environ


def test_not_frozen_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(storage.HOME_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MM-Companion.exe"))
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")

    _apply_portable_home()

    assert storage.HOME_ENV_VAR not in os.environ


def test_explicit_home_env_var_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "elsewhere"
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(explicit))
    _freeze(monkeypatch, tmp_path)
    (tmp_path / "portable.flag").write_text("", encoding="utf-8")

    _apply_portable_home()

    assert os.environ[storage.HOME_ENV_VAR] == str(explicit)


def test_workspace_startup_failure_is_reported_and_closes_splash(qapp, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox, QWidget

    from mm_companion import __main__ as entry

    splash = QWidget()
    reported = []
    monkeypatch.setattr(entry, "QApplication", lambda args: qapp)
    monkeypatch.setattr(entry, "_make_splash", lambda: splash)
    monkeypatch.setattr(entry, "_route_qt_messages", lambda: None)
    monkeypatch.setattr(entry.diagnostics, "install", lambda *a, **k: None)
    monkeypatch.setattr(entry.gc_guard, "install", lambda app: None)
    monkeypatch.setattr(entry, "_import_interface", lambda: SimpleNamespace())
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: reported.append(args))

    def refuse_workspace():
        raise PermissionError("workspace is read-only")

    monkeypatch.setattr(entry, "ensure_workspace", refuse_workspace)
    assert entry.main() == 1
    assert not splash.isVisible()
    assert len(reported) == 1
    assert "workspace is read-only" in reported[0][2]
