"""Put editable widgets into a read-only, label-like "locked" state.

Locking is a *view* mode. Unlike ``setEnabled(False)`` — which greys a control
out — a locked field keeps showing its value clearly but sheds its input chrome
(frame, spin buttons, dropdown arrow) so it reads like a plain label and cannot
be edited.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QLineEdit,
    QTextEdit,
    QWidget,
)

_LOCKED_COMBO_STYLE = (
    "QComboBox { border: none; background: transparent; }"
    "QComboBox::drop-down { width: 0px; border: none; }"
)


class _InteractionBlocker(QObject):
    """Event filter that swallows user-interaction events, so a widget shows its
    value normally but cannot be changed. Used for combo boxes, which have no
    read-only mode of their own.

    The wheel is deliberately *not* in the list. Swallowing it here would beat the
    wheel guard to the event (this filter is installed later, so Qt calls it first)
    and the page would simply stop scrolling wherever the pointer happened to cross a
    locked dropdown. Instead :func:`_set_combo_locked` makes the combo unfocusable,
    which is what the guard reads to decide the wheel belongs to the page.
    """

    _BLOCKED = frozenset(
        {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        }
    )

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        return event.type() in self._BLOCKED


def set_widget_locked(widget: QWidget, locked: bool) -> None:
    """Lock or unlock a single editable widget in place.

    Handles the input widgets the sheet uses (any spin box, ``QLineEdit``,
    ``QTextEdit``, ``QComboBox``); anything else is left untouched.
    """
    if isinstance(widget, QAbstractSpinBox):
        widget.setReadOnly(locked)
        widget.setFrame(not locked)
        _set_spin_buttons_hidden(widget, locked)
    elif isinstance(widget, QTextEdit):
        _set_text_edit_locked(widget, locked)
    elif isinstance(widget, QLineEdit):
        widget.setReadOnly(locked)
        widget.setFrame(not locked)
    elif isinstance(widget, QComboBox):
        _set_combo_locked(widget, locked)


def _set_spin_buttons_hidden(spin: QAbstractSpinBox, hidden: bool) -> None:
    """Hide a spin box's up/down buttons while locked, restoring whatever style
    it was created with on unlock."""
    if hidden:
        if not hasattr(spin, "_orig_button_symbols"):
            spin._orig_button_symbols = spin.buttonSymbols()
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    elif hasattr(spin, "_orig_button_symbols"):
        spin.setButtonSymbols(spin._orig_button_symbols)
        del spin._orig_button_symbols


def _set_text_edit_locked(edit: QTextEdit, locked: bool) -> None:
    """Turn a multiline text box into plain wrapped text while locked.

    Read-only alone still draws the box's frame and input background; dropping the
    frame and clearing the background makes the description read as a plain label,
    matching the rest of the locked sheet.
    """
    edit.setReadOnly(locked)
    if locked:
        edit.setFrameShape(QFrame.Shape.NoFrame)
        edit.viewport().setAutoFillBackground(False)
        edit.setStyleSheet("QTextEdit { background: transparent; }")
    else:
        edit.setFrameShape(QFrame.Shape.StyledPanel)
        edit.viewport().setAutoFillBackground(True)
        edit.setStyleSheet("")


def _set_combo_locked(combo: QComboBox, locked: bool) -> None:
    """Turn a combo box into a plain label while locked.

    Besides the interaction filter and the flattened style, a locked combo gives up
    its focus policy: it is a label now, so Tab should skip it, and — because the
    wheel guard only lets a *focused* widget consume the wheel — being unfocusable is
    what keeps the page scrolling when the pointer crosses it.
    """
    if locked:
        if not hasattr(combo, "_lock_blocker"):
            blocker = _InteractionBlocker(combo)
            combo._lock_blocker = blocker
            combo._lock_focus_policy = combo.focusPolicy()
            combo.installEventFilter(blocker)
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.setStyleSheet(_LOCKED_COMBO_STYLE)
    elif hasattr(combo, "_lock_blocker"):
        combo.removeEventFilter(combo._lock_blocker)
        combo._lock_blocker.deleteLater()
        del combo._lock_blocker
        combo.setFocusPolicy(combo._lock_focus_policy)
        del combo._lock_focus_policy
        combo.setStyleSheet("")
