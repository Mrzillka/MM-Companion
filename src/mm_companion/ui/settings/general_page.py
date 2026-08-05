"""The General settings page: preferences that are not about the look or the GM.

One setting so far — how the dice roller arranges itself as an ordinary sheet
block. It exists because the arrangement compact mode uses (the roll settings
across the top, the quick rolls beside a smaller die) turned out to be a good
roller in its own right and not merely a way to fit a mini window, so it is
offered everywhere rather than only there.

Its change applies to every window already open rather than asking for a
relaunch: the page walks the open top-level windows for anything answering
``sync_dice_layout``, which is the same "reach the live windows" move the GM page
makes for its pinned strips, but duck-typed — so this module drags in neither the
sheet nor the GM window just to be shown.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui.settings.page import SettingsPage
from mm_companion.ui.widgets import BOLD_STYLE, muted_style

NOTE_TEXT = (
    "The dice roller normally reflows to whatever room it is given — a column in "
    "the narrow side strip, one row across a wide bottom one. Tick this to pin it "
    "to the compact arrangement instead, wherever it is: the roll settings across "
    "the top, the quick rolls beside a smaller die, the history under both. It is "
    "the shape the mini roller uses, and it is the shortest one."
)


class GeneralPage(SettingsPage):
    """Preferences that belong to no other page."""

    title = "General"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        column = QVBoxLayout(self)
        column.addWidget(self._build_heading())
        column.addWidget(self._build_dice_layout())
        column.addStretch()
        column.addLayout(self._build_actions())
        column.addWidget(self._build_status())

        self._load()

    # -- construction ------------------------------------------------------------

    def _build_heading(self) -> QWidget:
        panel = QWidget()
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Dice roller")
        title.setStyleSheet(BOLD_STYLE)
        stack.addWidget(title)

        note = QLabel(NOTE_TEXT)
        note.setWordWrap(True)
        note.setStyleSheet(muted_style(italic=True))
        stack.addWidget(note)
        return panel

    def _build_dice_layout(self) -> QWidget:
        self._compact_check = QCheckBox("Always use the compact roller layout")
        # A "Saved." line left standing over a box that has since been ticked again
        # is a lie about the state of the settings file, so the line goes the
        # moment the answer changes.
        self._compact_check.toggled.connect(lambda _checked: self._set_status(""))
        return self._compact_check

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self.save)
        row.addWidget(self._save_button)
        row.addStretch()
        return row

    def _build_status(self) -> QWidget:
        self._status = QLabel()
        self._status.setWordWrap(True)
        return self._status

    # -- the SettingsPage contract -----------------------------------------------

    def is_dirty(self) -> bool:
        return self._chosen() != self._saved

    def save(self) -> None:
        """Write the preference, and reshape every roller already on screen."""
        storage.set_dice_layout(self._chosen())
        self._saved = self._chosen()
        for window in _open_windows():
            window.sync_dice_layout()
        self._set_status("Saved.")

    def discard(self) -> None:
        self._load()

    # -- state -------------------------------------------------------------------

    def _chosen(self) -> str:
        return (
            storage.DICE_LAYOUT_COMPACT
            if self._compact_check.isChecked()
            else storage.DICE_LAYOUT_AUTO
        )

    def _load(self) -> None:
        self._saved = storage.dice_layout()
        self._compact_check.setChecked(self._saved == storage.DICE_LAYOUT_COMPACT)
        self._set_status("")

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(muted_style(italic=True))


def _open_windows() -> list:
    """Every open window that carries a dice roller it could reshape.

    Duck-typed rather than by class, so a window this module has never heard of —
    a mod's, a later one of ours — joins on the same terms, and so that showing the
    Settings window imports neither the character sheet nor the GM window.
    """
    app = QApplication.instance()
    if app is None:
        return []
    return [w for w in app.topLevelWidgets() if callable(getattr(w, "sync_dice_layout", None))]
