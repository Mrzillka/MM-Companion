"""The Settings window: a list of pages on the left, the current one on the right.

One page so far. The window knows nothing about what any of them configure — it
picks names out of :data:`PAGES`, shows one at a time, and on close asks each in
turn whether it has unsaved work and whether what it did needs a relaunch. Adding
a second area of settings is an entry in that tuple.

Non-modal and opened with ``.show()``, like the Mod Manager: changing the look
while a character sheet is open beside it is the point, not an accident.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.app_restart import restart_app
from mm_companion.ui.settings.page import SettingsPage
from mm_companion.ui.settings.theme_page import ThemePage

#: Every page the window offers, in the order the left-hand list shows them.
PAGES: tuple[type[SettingsPage], ...] = (ThemePage,)

#: The window's own size. Not a token: a preset says how things *look*, not how
#: big a utility window opens, and the Mod Manager sets its own the same way.
DEFAULT_SIZE = (900, 640)


class SettingsWindow(QMainWindow):
    """Application settings, one page per area."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(*DEFAULT_SIZE)
        self._restart_on_close = False

        central = QWidget()
        outer = QVBoxLayout(central)

        body = QHBoxLayout()
        self._nav = QListWidget()
        self._nav.setFixedWidth(int(theme.metric("column.settings.nav")))
        self._stack = QStackedWidget()
        self._pages: list[SettingsPage] = []
        for page_class in PAGES:
            page = page_class(self)
            self._pages.append(page)
            self._nav.addItem(page.title)
            self._stack.addWidget(page)
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)

        body.addWidget(self._nav)
        body.addWidget(self._stack, stretch=1)
        outer.addLayout(body)
        outer.addLayout(self._build_bottom_bar())
        self.setCentralWidget(central)

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        bar.addWidget(close_button)
        return bar

    # -- closing ----------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Offer to keep unsaved work, always tear down what was only previewed.

        ``discard`` runs whether the page was saved or not: a page may be showing
        the user something that has no life outside this window — a live theme
        preview, which would otherwise keep dressing the app until it restarted.
        """
        for page in self._pages:
            if page.is_dirty() and self._prompt_save(page):
                page.save()
            page.discard()
        if any(page.needs_restart() for page in self._pages) and self._prompt_restart():
            self._restart_on_close = True
        super().closeEvent(event)
        if self._restart_on_close:
            # Deferred so this window is fully closed before the app tears down.
            QTimer.singleShot(0, restart_app)

    def _prompt_save(self, page: SettingsPage) -> bool:
        choice = QMessageBox.question(
            self,
            "Save changes?",
            f"{page.title} has unsaved changes.\n\nSave them before closing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return choice == QMessageBox.StandardButton.Yes

    def _prompt_restart(self) -> bool:
        choice = QMessageBox.question(
            self,
            "Restart to finish applying?",
            "The new look is applied. A few parts of the interface only pick it up "
            "after a restart.\n\nRestart MM-Companion now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return choice == QMessageBox.StandardButton.Yes
