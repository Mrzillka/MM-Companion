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
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

#: Shedding the chrome takes a stylesheet, not ``setFrame``/``setButtonSymbols``
#: alone: a styled preset states a border, a radius and a padding on these widgets
#: from the application sheet, and that outranks both. The padding matters as much
#: as the border — the theme reserves a right-hand column for the arrows the
#: platform style draws (see :mod:`mm_companion.ui.theme.qss`), and a locked field
#: draws none, so it would otherwise sit its value off-centre against a gap.
#:
#: Deliberately widget-level rather than a ``[locked="true"]`` rule in the theme
#: QSS: Classic emits almost no sheet at all, so an application-level locked rule
#: would exist under some presets and not others — the very split being fixed.
#: They state no colour, only absences, so no theme token leaks into a widget.
_LOCKED_COMBO_STYLE = (
    "QComboBox { border: none; border-radius: 0px; background: transparent; padding: 0px; }"
    "QComboBox::drop-down { width: 0px; border: none; }"
)

_LOCKED_SPIN_STYLE = (
    "QAbstractSpinBox { border: none; border-radius: 0px; background: transparent;"
    " padding: 0px; }"
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
    ``QTextEdit``/``QPlainTextEdit``, ``QComboBox``); anything else is left
    untouched.
    """
    if isinstance(widget, QAbstractSpinBox):
        widget.setReadOnly(locked)
        widget.setFrame(not locked)
        _set_spin_buttons_hidden(widget, locked)
    elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
        _set_text_edit_locked(widget, locked)
    elif isinstance(widget, QLineEdit):
        widget.setReadOnly(locked)
        widget.setFrame(not locked)
    elif isinstance(widget, QComboBox):
        _set_combo_locked(widget, locked)


def _set_spin_buttons_hidden(spin: QAbstractSpinBox, hidden: bool) -> None:
    """Hide a spin box's up/down buttons while locked, restoring whatever style
    it was created with on unlock.

    ``setButtonSymbols`` stops the arrows being *drawn*; the stylesheet is what
    stops the theme still reserving room for them and a box around the whole field
    (see :data:`_LOCKED_SPIN_STYLE`). A box built with ``buttons=False`` keeps its
    own setting through the round trip — that is what ``_orig_button_symbols`` is
    for.
    """
    if hidden:
        if not hasattr(spin, "_orig_button_symbols"):
            spin._orig_button_symbols = spin.buttonSymbols()
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setStyleSheet(_LOCKED_SPIN_STYLE)
    elif hasattr(spin, "_orig_button_symbols"):
        spin.setButtonSymbols(spin._orig_button_symbols)
        del spin._orig_button_symbols
        spin.setStyleSheet("")


def _set_text_edit_locked(edit: QTextEdit | QPlainTextEdit, locked: bool) -> None:
    """Turn a multiline text box into plain wrapped text while locked.

    Read-only alone still draws the box's frame and input background; dropping the
    frame and clearing the background makes the description read as a plain label,
    matching the rest of the locked sheet.
    """
    edit.setReadOnly(locked)
    if locked:
        edit.setFrameShape(QFrame.Shape.NoFrame)
        edit.viewport().setAutoFillBackground(False)
        # The Qt base class name, not ``type(edit).__name__``: a stylesheet
        # selector matches subclasses, and a subclass's own name would not be a
        # selector the sheet's other rules were written against.
        selector = "QPlainTextEdit" if isinstance(edit, QPlainTextEdit) else "QTextEdit"
        edit.setStyleSheet(f"{selector} {{ background: transparent; }}")
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
