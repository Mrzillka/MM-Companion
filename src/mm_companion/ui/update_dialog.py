"""The in-app update: download the installer, close up, hand over, and quit.

Opened by the launcher's Update button when :func:`core.updates.can_self_update`
says this is an installed build. It goes through four stages, each shown with the
app's own progress bar for as long as the app is still running:

1. **Downloading** the release's installer on a worker thread, with a real bar
   and a Cancel button.
2. **Closing** every other window through its own ``close()``, so an unsaved
   sheet asks Save/Discard/Cancel as it always does — and a Cancel there stops the
   update rather than losing the work.
3. **Waiting for permission**: the installer is started and asks Windows to
   elevate. The bar goes indeterminate until the installer says it is running,
   or ends having been refused, in which case the app stays open and says so.
4. **Quitting**, so the installer can replace the files. From here Inno Setup's
   own progress window takes over, and it starts the app again when it is done.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion import __version__
from mm_companion.core import updates
from mm_companion.core.updates import InstallerLaunch, ReleaseInfo, UpdateCancelled, UpdateError
from mm_companion.ui.widgets import muted_style, tinted_style

#: How often the waiting stage looks at the installer, in ms.
POLL_INTERVAL = 250
#: The bar's resolution: per-mille, so a byte count never overflows its int range.
_BAR_STEPS = 1000


def _quit_app() -> None:
    """Leave the event loop — a seam, so tests can watch for it.

    ``exit`` rather than ``quit``: in Qt 6 ``quit()`` first asks every window to
    close and gives up if one refuses — and this dialog refuses while the installer
    waits on it. Every window that could object has already been closed.
    """
    QApplication.exit(0)


def _megabytes(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB"


class UpdateDialog(QDialog):
    """Downloads and starts the installer for *release*, then quits the app."""

    #: From the download thread; delivered on the GUI thread, where the dialog lives.
    progressed = Signal(object, object)
    downloaded = Signal(object)
    failed = Signal(str)

    def __init__(self, release: ReleaseInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._release = release
        self._cancel = threading.Event()
        self._launch: InstallerLaunch | None = None
        # Set once the installer is running and the app is on its way out.
        self._handed_over = False
        self.setWindowTitle("Update MM-Companion")
        self.setModal(True)

        layout = QVBoxLayout(self)
        self._status = QLabel(f"Downloading MM-Companion {release.version}…")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, _BAR_STEPS)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)

        self._detail = QLabel(f"From v{__version__} to v{release.version}")
        self._detail.setStyleSheet(muted_style())
        layout.addWidget(self._detail)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._page_button = QPushButton("Open download page")
        self._page_button.clicked.connect(self._open_page)
        buttons.addWidget(self._page_button)
        self._page_button.hide()
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self._cancel_button)
        layout.addLayout(buttons)

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_INTERVAL)
        self._poll.timeout.connect(self._check_installer)

        # Wide enough that the status reads as one line; a preference, not a minimum,
        # and in characters so it follows the theme's font.
        self.resize(self.fontMetrics().averageCharWidth() * 60, self.sizeHint().height())

        self.progressed.connect(self._show_progress)
        self.downloaded.connect(self._install)
        self.failed.connect(self._show_failure)

    # -- stage 1: download --------------------------------------------------

    def start(self) -> None:
        """Begin the download; everything after it follows on its own."""
        asset = self._release.installer
        if asset is None:
            self._show_failure("This release has no installer to download.")
            return
        threading.Thread(
            target=self._download_worker, args=(asset,), name="update-download", daemon=True
        ).start()

    def _download_worker(self, asset: updates.ReleaseAsset) -> None:
        try:
            path = updates.download_asset(asset, progress=self._emit_progress, cancel=self._cancel)
        except UpdateCancelled:
            return
        except UpdateError as exc:
            self._emit("failed", str(exc))
            return
        self._emit("downloaded", path)

    def _emit_progress(self, done: int, total: int) -> None:
        self._emit("progressed", done, total)

    def _emit(self, name: str, *args: object) -> None:
        try:
            getattr(self, name).emit(*args)
        except RuntimeError:
            # The dialog was closed and destroyed while the thread was working.
            pass

    def _show_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._bar.setRange(0, 0)
            self._detail.setText(f"{_megabytes(done)} downloaded")
            return
        self._bar.setRange(0, _BAR_STEPS)
        self._bar.setValue(min(_BAR_STEPS, done * _BAR_STEPS // total))
        self._detail.setText(f"{_megabytes(done)} of {_megabytes(total)}")

    # -- stages 2-3: close up, start the installer ---------------------------

    def _install(self, installer: Path) -> None:
        self._bar.setRange(0, _BAR_STEPS)
        self._bar.setValue(_BAR_STEPS)
        self._status.setText("Closing MM-Companion's windows…")
        self._cancel_button.setEnabled(False)
        if not self._close_other_windows():
            self._show_failure(
                "The update was stopped because a window was kept open. "
                "Click Update again when you are ready — the download is kept."
            )
            return

        try:
            self._launch = updates.launch_installer(installer)
        except UpdateError as exc:
            self._show_failure(str(exc))
            return
        self._status.setText("Waiting for Windows to allow the installer…")
        self._detail.setText("Answer the permission prompt to continue.")
        self._bar.setRange(0, 0)
        self._poll.start()

    def _close_other_windows(self) -> bool:
        """Close every visible window but this one and the launcher behind it.

        Each goes through its own ``close()``, so its unsaved-changes prompt runs;
        ``False`` as soon as one refuses. Visibility is re-checked per window,
        since closing one (a GM window, say) can take others with it.
        """
        keep = {self, self.parentWidget()}
        for window in list(QApplication.topLevelWidgets()):
            if window in keep or not window.isVisible():
                continue
            if not window.close():
                return False
        return True

    def _check_installer(self) -> None:
        launch = self._launch
        if launch is None:
            return
        if launch.is_ready():
            # stage 4: the installer is elevated and waiting for us to go.
            self._poll.stop()
            self._handed_over = True
            self._status.setText("Installing — MM-Companion will reopen when it is done.")
            self._detail.setText("")
            _quit_app()
        elif launch.has_given_up():
            self._poll.stop()
            self._show_failure("The update was cancelled before it could install.")

    # -- endings ----------------------------------------------------------------

    def _show_failure(self, message: str) -> None:
        self._bar.hide()
        self._status.setText(message)
        self._status.setStyleSheet(tinted_style("tint.worse", bold=False))
        self._detail.setText("You can also download the installer yourself.")
        self._page_button.show()
        self._cancel_button.setText("Close")
        self._cancel_button.setEnabled(True)

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._release.page_url))

    def reject(self) -> None:
        """Cancel (or Close, or the title bar's ×): stop any download under way."""
        waiting = self._launch is not None and not self._launch.has_given_up()
        if waiting and not self._handed_over:
            # The installer is already asking for permission. Closing now would stop
            # the app watching for its answer, and the installer would find the app
            # it is waiting for still running. Only the prompt decides.
            return
        self._cancel.set()
        super().reject()
