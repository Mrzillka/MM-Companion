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
