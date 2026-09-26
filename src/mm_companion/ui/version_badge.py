"""The launcher's version line, and the update button it grows when there is one.

Shows the running version in small muted print. :meth:`VersionBadge.check_for_update`
asks :mod:`mm_companion.core.updates` on a worker thread — the request can take
seconds on a bad connection and the launcher must not freeze for it — and when a
newer release exists the line says so and an Update button appears beside it.

The check is started by whoever shows the launcher (``__main__``), never by the
constructor, so a test that builds a ``StartWindow`` never touches the network.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from mm_companion import __version__
from mm_companion.core import updates
from mm_companion.core.updates import ReleaseInfo
from mm_companion.ui import theme
from mm_companion.ui.widgets import muted_style, tinted_style


class VersionBadge(QWidget):
    """``v0.7.1`` — or ``v0.7.1 · v0.8.0 available [Update]``."""

    #: Emitted from the worker thread with the newer :class:`ReleaseInfo`, and
    #: delivered on the GUI thread (the badge lives there, so Qt queues it).
    updateFound = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._release: ReleaseInfo | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(f"v{__version__}")
        font = self._label.font()
        font.setPointSizeF(theme.font_size("size.version"))
        self._label.setFont(font)
        self._label.setStyleSheet(muted_style())
        layout.addWidget(self._label)

        self._update_button = QPushButton("Update")
        self._update_button.setToolTip("Open the download page for the new version")
        self._update_button.clicked.connect(self._open_update)
        layout.addWidget(self._update_button)
        # Parented by the layout above before it is hidden, so it never flashes.
        self._update_button.hide()

        layout.addStretch()

        self.updateFound.connect(self.show_update)

    @property
    def release(self) -> ReleaseInfo | None:
        """The newer release on offer, once one has been found."""
        return self._release

    def check_for_update(self) -> None:
        """Ask GitHub for the latest release in the background."""
        threading.Thread(target=self._check_worker, name="update-check", daemon=True).start()

    def _check_worker(self) -> None:
        release = updates.check_for_update()
        if release is None:
            return
        try:
            self.updateFound.emit(release)
        except RuntimeError:
            # The launcher closed while the request was in flight.
            pass

    def show_update(self, release: ReleaseInfo) -> None:
        """Say a newer version exists and offer the button to get it."""
        self._release = release
        self._label.setText(f"v{__version__} · v{release.version} available")
        self._label.setStyleSheet(tinted_style("tint.better", bold=False))
        self._label.setToolTip(f"You are running v{__version__}")
        self._update_button.show()

    def _open_update(self) -> None:
        if self._release is not None:
            QDesktopServices.openUrl(QUrl(self._release.page_url))
