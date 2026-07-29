"""The Themes page: pick a preset, or build one by tweaking every token.

Two kinds of change live here and they behave differently on purpose.

*Picking* a preset is saved the instant it is picked, the way the old Theme menu
did — "open Settings, click Slate Dark, close" stays a two-click operation.

*Editing* one is a draft until Save. Every keystroke previews live, because the
only honest way to show what ``radius.chip`` does is to wear it, but nothing
reaches ``settings.json`` or the workspace until asked. Closing the window
without saving takes the preview back down and leaves no trace.

Bundled presets are read-only — they live inside the install — so the page shows
them locked and offers Duplicate. Anything in the workspace is fully editable.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.settings.page import SettingsPage
from mm_companion.ui.settings.token_editor import TokenEditor, seed_styled_surfaces
from mm_companion.ui.theme import loader
from mm_companion.ui.theme.tokens import CHROME_STYLED, UnknownToken
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import BOLD_STYLE, muted_style, tinted_style

#: How long after the last keystroke the preview is applied. Rebuilding the
#: stylesheet and palette is not free and a spin-box drag fires per step, so the
#: edits are allowed to settle first — short enough to still feel immediate.
PREVIEW_DELAY_MS = 100

#: What the preset picker appends to each name, so the three cases are legible
#: without clicking into them.
_KIND_BUILT_IN = "built-in"
_KIND_CUSTOM = "custom"
_KIND_OVERRIDE = "overrides the built-in"


class ThemePage(SettingsPage):
    """Preset picker, header fields, and the generated token form under them."""

    title = "Themes"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dirty = False
        self._applied = False
        self._loading = False
        self._last_good = theme.active_theme()

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._preview_now)

        column = QVBoxLayout(self)
        column.addLayout(self._build_picker())
        column.addWidget(self._build_header())
        column.addWidget(self._build_form(), stretch=1)
        column.addLayout(self._build_actions())
        column.addWidget(self._build_status())

        self._reload_presets()

    # -- construction -----------------------------------------------------------

    def _build_picker(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Preset")
        label.setStyleSheet(BOLD_STYLE)
        row.addWidget(label)

        self._picker = QComboBox()
        self._picker.currentIndexChanged.connect(self._on_preset_picked)
        guard_wheel(self._picker)
        row.addWidget(self._picker, stretch=1)

        self._duplicate_button = QPushButton("Duplicate…")
        self._duplicate_button.clicked.connect(self._duplicate)
        row.addWidget(self._duplicate_button)

        self._delete_button = QPushButton("Delete")
        self._delete_button.clicked.connect(self._delete)
        row.addWidget(self._delete_button)
        return row

    def _build_header(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)

        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(muted_style(italic=True))
        column.addWidget(self._banner)

        row = QHBoxLayout()
        row.addWidget(QLabel("Name"))
        self._name_field = QLineEdit()
        self._name_field.editingFinished.connect(self._rename)
        row.addWidget(self._name_field, stretch=1)
        self._id_label = QLabel()
        self._id_label.setStyleSheet(muted_style())
        self._id_label.setToolTip("The filename this theme is stored under. Fixed once created.")
        row.addWidget(self._id_label)
        column.addLayout(row)
        return panel

    def _build_form(self) -> QWidget:
        self._editor = TokenEditor()
        self._editor.changed.connect(self._on_edited)
        self._editor.styledSurfacesNeeded.connect(self._offer_styled_surfaces)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._editor)
        return self._scroll

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self.save)
        row.addWidget(self._save_button)
        self._revert_button = QPushButton("Revert")
        self._revert_button.clicked.connect(self._revert)
        row.addWidget(self._revert_button)
        row.addStretch()
        return row

    def _build_status(self) -> QWidget:
        self._status = QLabel()
        self._status.setWordWrap(True)
        return self._status

    # -- the page contract ------------------------------------------------------

    def is_dirty(self) -> bool:
        return self._dirty

    def needs_restart(self) -> bool:
        """A live re-dress reaches everything the stylesheet styles, but no further.

        A power card builds its border in its constructor and only picks the new
        tokens up when its section is next rebuilt — the same bargain the Mod
        Manager strikes, and the reason a switch offers a relaunch rather than
        leaving half the window in the old look with no explanation.
        """
        return self._applied

    def save(self) -> None:
        """Write the draft into the workspace and make it the active preset."""
        draft = self._editor.draft()
        loader.save_workspace_theme(draft)
        theme.reset()
        theme.set_active_theme(draft.id, QApplication.instance())
        self._applied = True
        self._reload_presets(select=draft.id)
        self._set_status(f"Saved to {loader.workspace_theme_path(draft.id)}.")

    def discard(self) -> None:
        """Take any live preview back down. Always called as the window closes."""
        self._preview_timer.stop()
        theme.set_preview(None, QApplication.instance())

    # -- presets ----------------------------------------------------------------

    def _reload_presets(self, select: str | None = None) -> None:
        """Refill the picker from disk and show whichever preset is now active."""
        wanted = select or theme.active_theme().id
        self._loading = True
        try:
            self._picker.clear()
            presets = sorted(theme.available_themes().values(), key=lambda t: t.name.lower())
            for preset in presets:
                self._picker.addItem(f"{preset.name}  ({_kind_of(preset.id)})", preset.id)
                if preset.description:
                    self._picker.setItemData(
                        self._picker.count() - 1,
                        preset.description,
                        Qt.ItemDataRole.ToolTipRole,
                    )
            index = self._picker.findData(wanted)
            self._picker.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._loading = False
        self._load_current()

    def _on_preset_picked(self, _index: int) -> None:
        if self._loading:
            return
        theme_id = self._picker.currentData()
        if not theme_id or theme_id == theme.active_theme().id:
            return
        if self._dirty and not self._confirm_discard():
            self._reload_presets()  # put the picker back where it was
            return
        # Picking is a choice, not a draft: persist it the way the old menu did.
        theme.set_preview(None)
        theme.set_active_theme(theme_id, QApplication.instance())
        self._applied = True
        self._load_current()

    def _load_current(self) -> None:
        """Seed the form from whichever preset the picker now names."""
        theme_id = self._picker.currentData() or theme.active_theme().id
        preset = theme.available_themes()[theme_id]
        self._last_good = preset
        self._loading = True
        try:
            self._editor.load(preset)
            self._name_field.setText(preset.name)
            self._id_label.setText(theme_id)
        finally:
            self._loading = False
        self._dirty = False
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        """Reflect what can be done to the preset on screen: edit, delete, neither."""
        theme_id = self._picker.currentData() or ""
        editable = loader.is_workspace_theme(theme_id)
        shadow = loader.shadows_bundled(theme_id)

        self._editor.set_locked(not editable)
        # Through the shared helper, not setReadOnly: a locked field also sheds its
        # frame, so it reads as a label like every other locked field in the app.
        set_widget_locked(self._name_field, not editable)
        self._save_button.setEnabled(editable and self._dirty)
        self._revert_button.setEnabled(editable and self._dirty)
        self._delete_button.setEnabled(editable)
        self._delete_button.setText("Revert to built-in" if shadow else "Delete")

        if not editable:
            self._banner.setText(
                f"{self._name_field.text()} ships with MM-Companion and cannot be edited. "
                "Duplicate it to make one of your own."
            )
        elif shadow:
            self._banner.setText(
                "This replaces the built-in preset of the same name. "
                "Reverting brings the built-in one back."
            )
        else:
            self._banner.setText("Your own theme. Edits preview live and are saved on Save.")

    # -- editing ----------------------------------------------------------------

    def _on_edited(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._refresh_actions()
        self._preview_timer.start()

    def _rename(self) -> None:
        name = self._name_field.text().strip()
        if self._loading or not name or name == self._editor.draft().name:
            return
        self._editor.update_draft(name=name)

    def _preview_now(self) -> None:
        """Wear the draft. A draft that cannot be worn is reported, not raised.

        The stylesheet can legitimately refuse a draft — a styled preset missing a
        surface is the obvious way — and an exception here would surface as a
        traceback from a timer with nothing on screen to explain it.
        """
        draft = self._editor.draft()
        app = QApplication.instance()
        try:
            theme.set_preview(draft, app)
        except (UnknownToken, ValueError) as error:
            theme.set_preview(self._last_good, app)
            self._set_status(f"Could not apply that: {error}", bad=True)
            return
        self._last_good = draft
        self._applied = True
        self._set_status("Previewing unsaved changes.")

    def _revert(self) -> None:
        """Throw the draft away and go back to what is on disk."""
        self._preview_timer.stop()
        theme.set_preview(None, QApplication.instance())
        self._load_current()
        self._set_status("Reverted to the saved theme.")

    def _offer_styled_surfaces(self) -> None:
        """Styled chrome needs surfaces this theme has none of; offer to borrow some."""
        candidates = {
            preset.name: preset
            for preset in theme.available_themes().values()
            if preset.chrome.mode == CHROME_STYLED
        }
        if not candidates:  # pragma: no cover - two ship with the app
            self._set_status("No styled preset to take surface colours from.", bad=True)
            return
        chosen, ok = QInputDialog.getItem(
            self,
            "Styled chrome needs surfaces",
            "A styled theme dresses the window itself, so it has to state its own\n"
            "surface colours. Start from which preset?",
            sorted(candidates),
            0,
            False,
        )
        if not ok:
            return
        self._editor.load(seed_styled_surfaces(self._editor.draft(), candidates[chosen]))
        self._dirty = True
        self._refresh_actions()
        self._preview_timer.start()

    # -- creating and removing --------------------------------------------------

    def _duplicate(self) -> None:
        source = self._editor.draft()
        name, ok = QInputDialog.getText(
            self, "Duplicate theme", "Name for your copy:", text=f"{source.name} copy"
        )
        name = name.strip()
        if not ok or not name:
            return
        theme_id = loader.unique_theme_id(name)
        copy = replace(
            source,
            id=theme_id,
            name=name,
            description=f"Based on {source.name}.",
        )
        loader.save_workspace_theme(copy)
        theme.reset()
        theme.set_active_theme(theme_id, QApplication.instance())
        self._applied = True
        self._reload_presets(select=theme_id)
        self._set_status(f"Created {name}. It is yours to edit.")

    def _delete(self) -> None:
        theme_id = self._picker.currentData()
        if not theme_id or not loader.is_workspace_theme(theme_id):
            return
        shadow = loader.shadows_bundled(theme_id)
        question = (
            "Bring the built-in preset of the same name back, discarding your version?"
            if shadow
            else f"Delete {self._name_field.text()}? This cannot be undone."
        )
        if not _confirm(self, "Delete theme" if not shadow else "Revert to built-in", question):
            return
        theme.set_preview(None)
        loader.delete_workspace_theme(theme_id)
        theme.reset()
        # A deleted shadow leaves the built-in behind, so the id may still resolve.
        if theme_id not in theme.available_themes():
            theme.set_active_theme(loader.DEFAULT_THEME_ID, QApplication.instance())
        else:
            theme.set_active_theme(theme_id, QApplication.instance())
        self._applied = True
        self._reload_presets()
        self._set_status("Reverted to the built-in preset." if shadow else "Theme deleted.")

    # -- odds and ends ----------------------------------------------------------

    def _confirm_discard(self) -> bool:
        return _confirm(
            self,
            "Discard changes?",
            "This theme has unsaved changes. Switching away throws them out.",
        )

    def _set_status(self, text: str, *, bad: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(tinted_style("tint.worse") if bad else muted_style(italic=True))


def _kind_of(theme_id: str) -> str:
    if loader.shadows_bundled(theme_id):
        return _KIND_OVERRIDE
    if loader.is_workspace_theme(theme_id):
        return _KIND_CUSTOM
    return _KIND_BUILT_IN


def _confirm(parent: QWidget, title: str, question: str) -> bool:
    choice = QMessageBox.question(
        parent,
        title,
        question,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return choice == QMessageBox.StandardButton.Yes
