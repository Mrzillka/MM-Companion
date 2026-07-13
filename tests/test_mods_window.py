"""GUI smoke tests for the Mod Manager window (headless / offscreen).

Thin coverage that the window builds, lists discovered mods, and drives the core
seams (enable writes settings + marks dirty; the details panel reflects a mod's
code/options). The seams themselves are covered in ``test_mod_manager.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from mm_companion.core import mods, storage
from mm_companion.ui.mods_window import ModsWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def _write_mod(manifest: dict) -> None:
    dest = storage.ensure_workspace().mods_dir / manifest["id"]
    dest.mkdir(parents=True)
    (dest / mods.MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _item(window: ModsWindow, mod_id: str):
    for i in range(window._list.count()):
        item = window._list.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == mod_id:
            return item
    raise AssertionError(f"no row for {mod_id}")


def test_window_lists_discovered_mods(qapp: QApplication) -> None:
    _write_mod({"id": "alpha", "name": "Alpha"})
    _write_mod({"id": "beta", "name": "Beta"})
    window = ModsWindow()
    assert window._list.count() == 2
    assert {window._list.item(i).text() for i in range(2)} == {"Alpha", "Beta"}


def test_ticking_enables_and_marks_dirty(qapp: QApplication) -> None:
    _write_mod({"id": "alpha", "name": "Alpha"})
    window = ModsWindow()
    _item(window, "alpha").setCheckState(Qt.CheckState.Checked)
    assert storage.load_settings()["enabled_mods"] == ["alpha"]
    assert window._dirty is True


def test_details_reflect_code_and_options(qapp: QApplication) -> None:
    _write_mod({"id": "plain", "name": "Plain"})
    _write_mod({"id": "coder", "name": "Coder", "python_module": "x"})
    _write_mod({"id": "opted", "name": "Opted", "options": [{"id": "o", "type": "bool"}]})
    window = ModsWindow()
    window.show()  # visibility only resolves once the top-level is shown

    window._list.setCurrentItem(_item(window, "coder"))
    assert window._trust_box.isVisible()
    assert not window._configure_button.isVisible()

    window._list.setCurrentItem(_item(window, "opted"))
    assert window._configure_button.isVisible()
    assert not window._trust_box.isVisible()

    window._list.setCurrentItem(_item(window, "plain"))
    assert not window._trust_box.isVisible()
    assert not window._configure_button.isVisible()


def test_remove_button_deletes_selected_mod(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_mod({"id": "alpha", "name": "Alpha"})
    _write_mod({"id": "beta", "name": "Beta"})
    # Confirm the deletion and swallow the "removed" notice without blocking.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window = ModsWindow()
    window._list.setCurrentItem(_item(window, "alpha"))
    window._remove_current()

    assert not (storage.get_workspace().mods_dir / "alpha").exists()
    assert {window._list.item(i).text() for i in range(window._list.count())} == {"Beta"}
    assert window._dirty is True


def test_remove_button_cancelled_keeps_mod(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_mod({"id": "alpha", "name": "Alpha"})
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    window = ModsWindow()
    window._list.setCurrentItem(_item(window, "alpha"))
    window._remove_current()
    assert (storage.get_workspace().mods_dir / "alpha").exists()


def test_reorder_persists_on_close(qapp: QApplication) -> None:
    _write_mod({"id": "alpha", "name": "Alpha"})
    _write_mod({"id": "beta", "name": "Beta"})
    window = ModsWindow()
    # Simulate a drag: rebuild the visible order beta-first, then close.
    storage.update_settings(mod_order=["beta", "alpha"])
    window._reload_mods()
    window.close()
    assert storage.load_settings()["mod_order"] == ["beta", "alpha"]
