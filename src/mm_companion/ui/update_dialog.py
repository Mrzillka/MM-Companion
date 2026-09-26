"""The in-app update: download the installer, close up, hand over, and quit.

Opened by the launcher's Update button when :func:`core.updates.can_self_update`
says this is an installed build. It goes through these stages, each shown with the
app's own progress bar for as long as the app is still running:

1. **Downloading** the release's installer on a worker thread, with a real bar
   and a Cancel button.
2. **Checking it is safe to close**: a running session is only ended with the
   user's say-so, and a second copy of the app — which the installer would find
   holding the files — has to be closed by hand first.
3. **Closing** every other window through its own ``close()``, so an unsaved
   sheet asks Save/Discard/Cancel as it always does — and a Cancel there stops the
   update rather than losing the work.
4. **Waiting for permission**: the installer is started and asks Windows to
   elevate. The bar goes indeterminate until the installer says it is running, or
   ends having been refused, in which case the app stays open and says so. Cancel
   still works here: it asks the installer to stand down (see
   :meth:`~mm_companion.core.updates.InstallerLaunch.withdraw`).
5. **Quitting**, so the installer can replace the files, after leaving a note for
   the next launch. From here Inno Setup's own progress window takes over, and it
   starts the app again when it is done — which reads the note and says whether
   the update worked (:func:`report_update_result`).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion import __version__
from mm_companion.core import updates
from mm_companion.core.updates import InstallerLaunch, ReleaseInfo, UpdateCancelled, UpdateResult
from mm_companion.ui.app_restart import suppress_restart
from mm_companion.ui.widgets import muted_style, tinted_style

#: How often the waiting stage looks at the installer, in ms.
POLL_INTERVAL = 250
#: The bar's resolution: per-mille, so a byte count never overflows its int range.
_BAR_STEPS = 1000


def _quit_app() -> None:
    """Leave the event loop — a seam, so tests can watch for it.

    ``exit`` rather than ``quit``: in Qt 6 ``quit()`` first asks every window to
    close and gives up if one refuses. Every window that could object has already
    been closed.
    """
    QApplication.exit(0)


def _megabytes(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB"


def _session_running() -> bool:
    from mm_companion.ui.session_bridge import active_session

    return active_session() is not None


def _closing_order(window: QWidget) -> int:
    """Character sheets before everything else.

    GM Mode closes the NPC sheets it opened as part of its own close and does not
    pass on a refusal, so reaching an unsaved NPC sheet through GM Mode first would
    ask about it twice. Asked directly first, each sheet asks once.
    """
    from mm_companion.ui.main_window import MainWindow

    return 0 if isinstance(window, MainWindow) else 1


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
        self._log_file = updates.update_log_file(release.version)
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
        self._detail.setWordWrap(True)
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
        except Exception as exc:  # noqa: BLE001 — anything left unsaid freezes the bar
            self._emit("failed", str(exc) or type(exc).__name__)
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

    # -- stages 2-4: check, close up, start the installer ----------------------

    def _install(self, installer: Path) -> None:
        self._bar.setRange(0, _BAR_STEPS)
        self._bar.setValue(_BAR_STEPS)
        self._cancel_button.setEnabled(False)
        kept = "Click Update again when you are ready — the download is kept."

        if _session_running() and not self._confirm_end_session():
            self._show_failure(f"The update was put off. {kept}", tint="")
            return
        if updates.other_instances():
            self._show_failure(
                "Another MM-Companion window is open, and the installer cannot replace "
                f"files it is using. Close it first. {kept}"
            )
            return

        self._status.setText("Closing MM-Companion's windows…")
        if not self._close_other_windows():
            self._show_failure(f"The update was stopped because a window was kept open. {kept}")
            return

        try:
            self._launch = updates.launch_installer(installer, log_file=self._log_file)
        except updates.UpdateError as exc:
            self._show_failure(str(exc))
            return
        self._status.setText("Waiting for Windows to allow the installer…")
        self._detail.setText("Answer the permission prompt to continue.")
        self._bar.setRange(0, 0)
        self._cancel_button.setEnabled(True)
        self._poll.start()

    def _confirm_end_session(self) -> bool:
        choice = QMessageBox.question(
            self,
            "End the session?",
            "Updating closes GM Mode. A session hosted on this computer ends for "
            "everyone in it; one hosted on a server carries on without you.\n\n"
            "Close it and update now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return choice == QMessageBox.StandardButton.Yes

    def _close_other_windows(self) -> bool:
        """Close every open window but this one and the launcher behind it.

        Only windows of their own — no parent — are closed here. A block popped out
        of a sheet is a window too, but its owner's business: closing it directly
        takes the block *off the sheet* (and refuses the close), so it is left for
        the sheet it belongs to. A window refused if it is still showing afterwards,
        which is what an unsaved sheet does when its Cancel is chosen.
        """
        suppress_restart()
        keep = {self, self.parentWidget()}
        windows = [
            window
            for window in QApplication.topLevelWidgets()
            if window not in keep and window.parentWidget() is None
        ]
        for window in sorted(windows, key=_closing_order):
            if not window.isVisible():
                continue
            window.close()
            if window.isVisible():
                return False
        return True

    def _check_installer(self) -> None:
        launch = self._launch
        if launch is None:
            return
        if launch.is_ready():
            self._hand_over()
        elif launch.has_given_up():
            self._poll.stop()
            self._launch = None
            self._show_failure("The update was cancelled before it could install.")

    def _hand_over(self) -> None:
        """Stage 5: the installer is running and waiting for us to go."""
        self._poll.stop()
        self._handed_over = True
        updates.record_pending_update(self._release.version, self._log_file)
        self._status.setText("Installing — MM-Companion will reopen when it is done.")
        self._detail.setText("")
        _quit_app()

    # -- endings ----------------------------------------------------------------

    def _show_failure(self, message: str, *, tint: str = "tint.worse") -> None:
        self._bar.hide()
        self._status.setText(message)
        self._status.setStyleSheet(tinted_style(tint, bold=False) if tint else "")
        self._detail.setText("You can also download the installer yourself.")
        self._page_button.show()
        self._cancel_button.setText("Close")
        self._cancel_button.setEnabled(True)

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._release.page_url))

    def reject(self) -> None:
        """Cancel (or Close, or the title bar's ×): stop whatever is under way.

        While Windows is asking for permission, the installer is asked to stand
        down. If it has already got past asking, it is too late to stop: the app
        hands over and quits as it would have.
        """
        if self._handed_over:
            super().reject()
            return
        if self._launch is not None and not self._launch.has_given_up():
            self._poll.stop()
            if not self._launch.withdraw():
                self._hand_over()
                return
            self._launch = None
        self._cancel.set()
        super().reject()


def report_update_result(parent: QWidget | None = None) -> UpdateResult | None:
    """Say how the last in-app update went, if one was attempted; return it.

    A success needs no dialog — the launcher's version line says so. A failure
    does, because the app the user is looking at is the old one: it says what was
    meant to happen and offers the installer's log, which Inno Setup wrote to the
    workspace.
    """
    result = updates.take_update_result()
    if result is None or result.succeeded:
        return result
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("The update did not finish")
    box.setText(
        f"MM-Companion was not updated to v{result.to_version} — this is still " f"v{__version__}."
    )
    log = result.log_file
    open_log = None
    if log is not None and log.is_file():
        box.setInformativeText(f"The installer wrote down what went wrong in:\n{log}")
        open_log = box.addButton("Open log", QMessageBox.ButtonRole.ActionRole)
    else:
        box.setInformativeText("The installer left no log behind.")
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if open_log is not None and box.clickedButton() is open_log:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(log)))
    return result
