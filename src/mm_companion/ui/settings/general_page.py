"""The General settings page: preferences that are not about the look or the GM.

One setting so far — how the dice roller arranges itself, as a sheet block and as
the GM window's Rolls block alike. Normally it reflows to whatever room it is
given, but two of the shapes it can be in turned out to be worth choosing outright:
the compact one, which was only ever a way to fit the mini window, and the extended
one, which was only ever what GM Mode happened to be built as. So both are offered
everywhere rather than each staying where it came from.

Its change applies to every window already open rather than asking for a
relaunch: the page walks the open top-level windows for anything answering
``sync_dice_layout``, which is the same "reach the live windows" move the GM page
makes for its pinned strips, but duck-typed — so this module drags in neither the
sheet nor the GM window just to be shown.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui.settings.page import SettingsPage
from mm_companion.ui.widgets import BOLD_STYLE, muted_style

NOTE_TEXT = (
    "How the roll controls, the die and the history share the space the roller is "
    "given — in a sheet's Dice block, in GM Mode's Rolls block, and in either "
    "window's pinned strip."
)

#: The three shapes, in the order they are offered: the label, the layout id, and
#: the line under it. Adding a fourth is an entry here and one in
#: :data:`~mm_companion.core.storage.DICE_LAYOUTS`.
CHOICES: tuple[tuple[str, str, str], ...] = (
    (
        "Normal",
        storage.DICE_LAYOUT_AUTO,
        "Reflow to whatever room there is — a column in the narrow side strip, "
        "one row across a wide bottom one.",
    ),
    (
        "Compact",
        storage.DICE_LAYOUT_COMPACT,
        "The roll settings across the top, the quick rolls beside a smaller die, "
        "the history under both. The shape the mini roller uses, and the shortest.",
    ),
    (
        "Extended",
        storage.DICE_LAYOUT_EXTENDED,
        "The roll controls as a column on the left, the history filling the rest. "
        "The roomiest, and what GM Mode always used to look like.",
    ),
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

        title = QLabel("Dice roller layout")
        title.setStyleSheet(BOLD_STYLE)
        stack.addWidget(title)

        note = QLabel(NOTE_TEXT)
        note.setWordWrap(True)
        note.setStyleSheet(muted_style(italic=True))
        stack.addWidget(note)
        return panel

    def _build_dice_layout(self) -> QWidget:
        """One radio per shape, each over the line that says what it looks like.

        Three mutually exclusive arrangements that each need a sentence of
        explanation, which is what radio buttons are for — a combo would hide two
        of the three descriptions behind a click.
        """
        panel = QWidget()
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(0, 0, 0, 0)

        self._buttons = QButtonGroup(self)
        self._choices: dict[str, QRadioButton] = {}
        for label, layout, description in CHOICES:
            button = QRadioButton(label)
            self._buttons.addButton(button)
            self._choices[layout] = button
            stack.addWidget(button)

            note = QLabel(description)
            note.setWordWrap(True)
            note.setStyleSheet(muted_style(italic=True))
            note.setIndent(self._description_indent())
            stack.addWidget(note)
        # A "Saved." line left standing over a choice that has since moved is a lie
        # about the state of the settings file, so the line goes the moment the
        # answer changes.
        self._buttons.buttonToggled.connect(lambda *_: self._set_status(""))
        return panel

    def _description_indent(self) -> int:
        """Line each description up under its radio's label rather than its dot."""
        probe = QRadioButton()
        indent = probe.sizeHint().width()
        probe.deleteLater()
        return indent

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
        for layout, button in self._choices.items():
            if button.isChecked():
                return layout
        return storage.DICE_LAYOUT_AUTO

    def _load(self) -> None:
        self._saved = storage.dice_layout()
        self._choices[self._saved].setChecked(True)
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
