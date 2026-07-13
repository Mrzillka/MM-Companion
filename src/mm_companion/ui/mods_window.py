"""The Mod Manager window: view, enable, order, trust, add, and configure mods.

Reachable from the launcher ("Manage Mods") and a character sheet's
``Settings ▸ Mods…``. It drives the pure-``core`` seams in
:mod:`mm_companion.core.mods` (enable/trust/order/options/import); because mods
are read once at startup, applying a change requires an app **relaunch**, offered
on close only when something actually changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import mods, storage
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box

_MOD_ID_ROLE = Qt.ItemDataRole.UserRole


def restart_app() -> None:
    """Relaunch the app so mod changes take effect; abort if a window won't close.

    Closes every open window first so character sheets run their unsaved-change
    guards; if any window refuses (the user cancelled a Save) the relaunch is
    aborted and the app keeps running. Otherwise a fresh process is spawned via
    ``python -m mm_companion`` (the launch path that works however the app was
    started) and this one quits.
    """
    app = QApplication.instance()
    if app is None:
        return
    app.closeAllWindows()
    if any(w.isVisible() and w.isWindow() for w in app.topLevelWidgets()):
        return  # a window refused to close — stay running, changes apply next launch
    QProcess.startDetached(sys.executable, ["-m", "mm_companion"])
    app.quit()


class _ModOptionsDialog(QDialog):
    """A generated form over one mod's declared options; Save persists overrides."""

    def __init__(self, mod: mods.Mod, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Configure {mod.name}")
        self._mod = mod
        self._editors: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        values = mods.mod_option_values(mod.id, mod)
        for option in mod.options:
            editor = self._build_editor(option, values.get(option.id))
            if option.description:
                editor.setToolTip(option.description)
            self._editors[option.id] = editor
            form.addRow(option.label, editor)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_editor(self, option: mods.ModOption, value: object) -> QWidget:
        if option.type == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            return box
        if option.type == "number":
            spin = make_spin_box(-1_000_000, 1_000_000)
            try:
                spin.setValue(int(value))
            except (TypeError, ValueError):
                spin.setValue(0)
            return spin
        if option.type == "choice":
            combo = QComboBox()
            combo.addItems(list(option.choices))
            if value is not None and str(value) in option.choices:
                combo.setCurrentText(str(value))
            guard_wheel(combo)
            return combo
        line = QLineEdit()
        line.setText("" if value is None else str(value))
        return line

    def _current_values(self) -> dict:
        values: dict = {}
        for option in self._mod.options:
            editor = self._editors[option.id]
            if isinstance(editor, QCheckBox):
                values[option.id] = editor.isChecked()
            elif isinstance(editor, QComboBox):
                values[option.id] = editor.currentText()
            elif isinstance(editor, QLineEdit):
                values[option.id] = editor.text()
            else:  # spin box
                values[option.id] = editor.value()
        return values

    def _save(self) -> None:
        mods.set_mod_options(self._mod.id, self._current_values())
        self.accept()


class ModsWindow(QMainWindow):
    """Manage installed mods: enable/disable, reorder, trust code, add, configure."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mod Manager")
        self.resize(680, 460)

        self._mods: dict[str, mods.Mod] = {}
        self._dirty = False
        self._restart_on_close = False
        self._populating = False
        # Nested dialogs kept referenced so they aren't GC'd mid-interaction.
        self._child_dialogs: list[QWidget] = []

        central = QWidget()
        outer = QVBoxLayout(central)

        base = mods.base_mod()
        header = QLabel(f"Base ruleset: {base.name} (always on)")
        header.setEnabled(False)
        outer.addWidget(header)

        body = QHBoxLayout()
        body.addWidget(self._build_list(), stretch=1)
        body.addWidget(self._build_details(), stretch=1)
        outer.addLayout(body)

        outer.addLayout(self._build_bottom_bar())
        self.setCentralWidget(central)

        self._reload_mods()

    # -- construction --------------------------------------------------------

    def _build_list(self) -> QWidget:
        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.currentItemChanged.connect(lambda *_: self._refresh_details())
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        return self._list

    def _build_details(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self._detail_name = QLabel()
        self._detail_name.setStyleSheet("font-weight: bold;")
        self._detail_version = QLabel()
        self._detail_version.setEnabled(False)
        self._detail_description = QLabel()
        self._detail_description.setWordWrap(True)

        layout.addWidget(self._detail_name)
        layout.addWidget(self._detail_version)
        layout.addWidget(self._detail_description)

        self._trust_box = QCheckBox("Allow this mod to run code")
        self._trust_box.toggled.connect(self._on_trust_toggled)
        layout.addWidget(self._trust_box)

        self._configure_button = QPushButton("Configure…")
        self._configure_button.clicked.connect(self._configure_current)
        layout.addWidget(self._configure_button)

        layout.addStretch()
        return panel

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        add_button = QPushButton("Add Mod…")
        add_button.clicked.connect(self._add_mod)
        bar.addWidget(add_button)
        bar.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        bar.addWidget(close_button)
        return bar

    # -- population ----------------------------------------------------------

    def _reload_mods(self) -> None:
        """Rebuild the list from workspace discovery, honoring the saved order."""
        settings = storage.load_settings()
        enabled = set(settings.get("enabled_mods", []))
        order = list(settings.get("mod_order", []))

        discovered = mods.discover_workspace_mods()
        self._mods = {mod.id: mod for mod in discovered}
        ordered_ids = [mid for mid in order if mid in self._mods]
        ordered_ids += [mid for mid in self._mods if mid not in ordered_ids]

        self._populating = True
        self._list.clear()
        for mod_id in ordered_ids:
            mod = self._mods[mod_id]
            item = QListWidgetItem(mod.name)
            item.setData(_MOD_ID_ROLE, mod_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if mod_id in enabled else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        self._populating = False

        if self._list.count():
            self._list.setCurrentRow(0)
        self._refresh_details()

    def _current_mod(self) -> mods.Mod | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return self._mods.get(item.data(_MOD_ID_ROLE))

    def _refresh_details(self) -> None:
        mod = self._current_mod()
        if mod is None:
            self._detail_name.setText("No mods installed")
            self._detail_version.clear()
            self._detail_description.clear()
            self._trust_box.setVisible(False)
            self._configure_button.setVisible(False)
            return

        settings = storage.load_settings()
        enabled = mod.id in settings.get("enabled_mods", [])
        trusted = mod.id in settings.get("trusted_mods", [])

        self._detail_name.setText(mod.name)
        self._detail_version.setText(f"Version {mod.version}" if mod.version else "")
        self._detail_description.setText(mod.description or "No description provided.")

        self._trust_box.setVisible(bool(mod.python_module))
        self._trust_box.setEnabled(enabled)
        self._trust_box.blockSignals(True)
        self._trust_box.setChecked(trusted)
        self._trust_box.blockSignals(False)

        self._configure_button.setVisible(bool(mod.options))

    # -- interactions --------------------------------------------------------

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Enable/disable a mod when its checkbox is toggled (trust-prompt code mods)."""
        if self._populating:
            return
        mod = self._mods.get(item.data(_MOD_ID_ROLE))
        if mod is None:
            return
        enable = item.checkState() == Qt.CheckState.Checked
        if (
            enable
            and mod.python_module
            and mod.id not in storage.load_settings().get("trusted_mods", [])
        ):
            trust = self._prompt_trust(mod)
            mods.set_mod_enabled(mod.id, True)
            mods.set_mod_trusted(mod.id, trust)
        else:
            mods.set_mod_enabled(mod.id, enable)
        self._mark_dirty()
        if mod is self._current_mod():
            self._refresh_details()

    def _prompt_trust(self, mod: mods.Mod) -> bool:
        choice = QMessageBox.question(
            self,
            "Allow code to run?",
            f"“{mod.name}” can run code on your computer when the app starts.\n\n"
            "Only allow this for mods you trust. Enable data only otherwise.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return choice == QMessageBox.StandardButton.Yes

    def _on_trust_toggled(self, trusted: bool) -> None:
        mod = self._current_mod()
        if mod is None:
            return
        mods.set_mod_trusted(mod.id, trusted)
        self._mark_dirty()

    def _on_rows_moved(self, *_: object) -> None:
        if not self._populating:
            self._mark_dirty()

    def _configure_current(self) -> None:
        mod = self._current_mod()
        if mod is None or not mod.options:
            return
        dialog = _ModOptionsDialog(mod, self)
        self._child_dialogs.append(dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._mark_dirty()

    def _add_mod(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose a mod folder")
        if not directory:
            return
        try:
            mod = mods.import_mod_folder(Path(directory))
        except mods.ModImportError as exc:
            QMessageBox.warning(self, "Could not add mod", str(exc))
            return
        # Append the new mod to the end of the load order so it wins by default.
        order = list(storage.load_settings().get("mod_order", []))
        if mod.id not in order:
            mods.set_mod_order(order + [mod.id])
        self._mark_dirty()
        self._reload_mods()

    # -- change tracking / relaunch -----------------------------------------

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _list_order(self) -> list[str]:
        return [self._list.item(i).data(_MOD_ID_ROLE) for i in range(self._list.count())]

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Persist the drag order, and offer to relaunch when something changed."""
        # The drag order is a harmless preference even without a restart, so save it.
        mods.set_mod_order(self._list_order())
        if self._dirty and self._prompt_restart():
            self._restart_on_close = True
        super().closeEvent(event)
        if self._restart_on_close:
            # Defer so this window is fully closed before we tear the app down.
            QTimer.singleShot(0, restart_app)

    def _prompt_restart(self) -> bool:
        choice = QMessageBox.question(
            self,
            "Restart to apply changes?",
            "Mod changes take effect after a restart.\n\nRestart MM-Companion now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return choice == QMessageBox.StandardButton.Yes
