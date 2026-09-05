"""One character-sheet block: a section wrapped in a draggable frame.

Each block is a :class:`BlockFrame` — a title bar (the drag handle, plus pin,
float and close buttons) above one of the ``sections`` widgets, in a scroll area
of its own. That scroll area is the whole reason a block can be dragged to any
size: a :class:`QScrollArea` does not pass its child's minimum on, so the frame
is free to report a minimum of almost nothing and let the section reflow — and,
past what reflow can save, scroll. The page still scrolls as one page (see
:class:`~mm_companion.ui.block_canvas.BlockCanvas`); a block only scrolls inside
itself once it has been made smaller than it can adapt to.

A frame lives in one of three places: inside the canvas, inside the pinned strip
that doesn't scroll with the page (see
:class:`~mm_companion.ui.pinned_panel.PinnedPanel`), or — when floated out —
inside a :class:`BlockWindow` (a top-level window). Dragging the title bar runs
the same gesture wherever it is, driven by the canvas's drag controller, so
float-out, reorder, pin, and drag-back-to-dock are one interaction.

The frame is deliberately dumb: it forwards title-bar mouse events and button
clicks to a *controller* (the :class:`BlockCanvas`) and reports the size it would
like to be. All arrangement logic lives in the canvas.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.block_sizes import RecommendedSize
from mm_companion.ui.drop_feedback import DropFeedback
from mm_companion.ui.frameless import apply_window_flags, describe_on_top, size_grip_row
from mm_companion.ui.wheel_guard import has_scroll_range
from mm_companion.ui.widgets import ElidingLabel

#: How strongly a block dropped *into* is washed. Heavier than the default
#: 0.10 a small target gets, because this one has no outline to help it: the
#: mark is the fill alone, and it has to carry a whole block's worth of area.
MERGE_WASH = 0.24


class DragHost(Protocol):
    """What a :class:`TitleBar` needs from its controller (the canvas)."""

    def title_bar_pressed(self, key: str, global_pos: QPoint) -> None: ...
    def title_bar_moved(self, key: str, global_pos: QPoint) -> None: ...
    def title_bar_released(self, key: str, global_pos: QPoint) -> None: ...
    def request_float(self, key: str) -> None: ...
    def request_hide(self, key: str) -> None: ...
    def request_pin(self, key: str) -> None: ...
    def request_on_top(self, key: str, on_top: bool) -> None: ...
    def request_menu(self, key: str, global_pos: QPoint) -> None: ...


class TitleBar(QFrame):
    """A block's header: the drag handle plus pin, float and close buttons.

    Left-drag on the bar drives the canvas drag gesture; the buttons park the
    block in the pinned strip, pop it out into its own window, or hide it. Clicks
    on the buttons are consumed by them, so they never start a drag.

    The ``🖈`` button **means different things in different homes**, which is not a
    trick but the honest reading of one glyph: on the page or in the strip it pins
    the block *to the strip*, and in its own window — where the strip is not
    somewhere it can go without ceasing to be a window — it pins the window *above
    other applications*. Pinning is what the glyph says; what it pins to is
    whatever the block is currently beside. :meth:`set_floating` swaps the two.
    """

    def __init__(self, key: str, title: str, host: DragHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._host = host
        self.setObjectName("blockTitleBar")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(2)

        # An eliding label, so a block's width comes from its content and not from
        # the length of its caption: a section's live title grows ("Abilities — 24
        # PP"), and a plain label would make that string a minimum width — which
        # would quietly become the narrowest that block could ever be dragged.
        self._label = ElidingLabel(title)
        self._label.setObjectName("blockTitleLabel")
        layout.addWidget(self._label, stretch=1)

        # Whether this block is in a window of its own, which is what the pin
        # button means by "pin" — see the class docstring.
        self._floating = False
        self._pin_button = QToolButton()
        # U+1F588 black pushpin, not U+1F4CC: the plain symbol keeps the title bar
        # monochrome like its ↗ and ✕ neighbours, where the emoji pushpin would put
        # a colour glyph in every block's header.
        self._pin_button.setText("🖈")
        self._pin_button.setAutoRaise(True)
        self._pin_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._pin_button.clicked.connect(self._pin_clicked)
        layout.addWidget(self._pin_button)

        self._float_button = QToolButton()
        self._float_button.setText("↗")  # north-east arrow: pop out
        self._float_button.setAutoRaise(True)
        self._float_button.setToolTip("Pop this block out into its own window")
        self._float_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._float_button.clicked.connect(lambda: self._host.request_float(self._key))
        layout.addWidget(self._float_button)

        self._close_button = QToolButton()
        self._close_button.setText("✕")  # multiplication x: close/hide
        self._close_button.setAutoRaise(True)
        self._close_button.setToolTip("Hide this block (reopen from the View menu)")
        self._close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._close_button.clicked.connect(lambda: self._host.request_hide(self._key))
        layout.addWidget(self._close_button)

        # Last, once every button it dresses exists.
        self.set_floating(False)

    def set_floating(self, floating: bool, *, on_top: bool = False) -> None:
        """Say which home this block is in, which decides what its ``🖈`` pins.

        In a window of its own the pin is a **toggle** — the window stays above
        other applications, or it does not — so it is checkable there and a plain
        action everywhere else. The float button goes with it: a window already is
        popped out, and ``↗`` on it would do nothing.
        """
        self._floating = bool(floating)
        self._float_button.setVisible(not self._floating)
        self._pin_button.setCheckable(self._floating)
        if self._floating:
            self._pin_button.setChecked(bool(on_top))
            self._pin_button.setToolTip(describe_on_top(on_top))
            return
        self._pin_button.setChecked(False)
        self._pin_button.setToolTip(
            "Pin this block to the fixed strip (or drag it there); "
            "click again to send it back to the page"
        )

    def _pin_clicked(self) -> None:
        if self._floating:
            on_top = self._pin_button.isChecked()
            self.set_floating(True, on_top=on_top)  # refresh the tooltip
            self._host.request_on_top(self._key, on_top)
            return
        self._host.request_pin(self._key)

    def set_title(self, text: str) -> None:
        """Update the drag handle's caption (a section reports its live title here)."""
        self._label.setText(text)

    def title_text(self) -> str:
        """The caption in full, even when the bar is too narrow to show all of it."""
        return self._label.text()

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """Right-click: everything the bar can do, in words.

        The three buttons are the fast path and stay exactly as they are. This is
        for the rest — *Fit to content* has no button and never could have one,
        and the arrangement gestures were otherwise a 6px divider you had to find
        and a title bar you had to know was draggable.

        Asked of the host rather than built here, for the reason the whole class
        is dumb: what a block may do depends on where it is, and where it is, is
        the canvas's business.
        """
        self._host.request_menu(self._key, event.globalPos())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._host.title_bar_pressed(self._key, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._host.title_bar_moved(self._key, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._host.title_bar_released(self._key, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


def content_height(widget: QWidget, width: int) -> int:
    """How tall *widget* really needs to be at *width* pixels across.

    The honest answer to a question Qt's own sizing only ever asks obliquely.
    ``sizeHint`` reports the height at the widget's *preferred* width and
    ``minimumSizeHint`` reports it at no particular width at all — a ``QBoxLayout``
    builds the latter by summing its items' unwrapped hints — so both overstate a
    block full of wrapping content by however much that content wraps. Only
    ``heightForWidth`` knows the width, and only widgets that wrap offer it.

    Never *more* than the widget already claimed: this exists to correct an
    overstatement, and a page the user drags has no business inventing a minimum
    the content did not ask for.
    """
    claimed = widget.minimumSizeHint().height()
    if width > 0 and widget.hasHeightForWidth():
        wrapped = widget.heightForWidth(width)
        if wrapped > 0:
            return min(claimed, wrapped)
    return claimed


class _Slack(QWidget):
    """Nothing, that grows. A stretch for a layout that takes widgets and not items."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, 0)


def _claims_the_height(layout: QBoxLayout) -> bool:
    """Whether anything in *layout* has a use of its own for the block's surplus.

    Asked of the layout's **own items**, and never of ``layout.expandingDirections()``,
    which is the question this looks like and is not. A layout that has not overridden
    that method answers "both" — a ``QFormLayout`` does not override it — and the
    answer then *travels*: ``QWidgetItem`` folds a widget's own layout's expanding
    directions into the widget's, wherever the widget's policy is allowed to grow at
    all. So one form on one power card claimed the vertical, which made the card
    expansive, which made the list of cards expansive, which made the Powers section
    say it had somewhere deliberate to put a tall block's spare height. It had not:
    the surplus went down into the cards and came out as a gap between every line of
    every one of them, which is the exact fault the trailing stretch exists to cure —
    on the two blocks that most needed it, since they are the ones made of cards.

    A **stretch factor** and a widget's **own vertical policy** are the two ways of
    saying "give the extra to me" that a section actually means, both of them stated
    here rather than derived from something further down: the tables set
    ``Expanding`` on themselves, and a section that has already put a stretch in its
    own layout has answered the question by doing it.

    A hidden widget is passed over. Powers and Equipment both keep an empty-state
    label in the layout all the time and hide it once there is anything to show; a
    layout ignores it, and so does this.
    """
    for index in range(layout.count()):
        if layout.stretch(index) > 0:
            return True
        widget = layout.itemAt(index).widget()
        if widget is None or widget.isHidden():
            continue
        if widget.sizePolicy().expandingDirections() & Qt.Orientation.Vertical:
            return True
    return False


def _give_trailing_slack(section: QWidget) -> bool:
    """Put the block's spare height somewhere deliberate *inside* *section*.

    Returns whether it now has somewhere to put it, which is whether the section can
    be handed the block's whole height. Done here, once, rather than in each section:
    a mod ships a block too, and the rule that a section fills its block is the
    page's, not the section's.

    Three answers, and nothing on the sheet needs a fourth:

    * a section that states ``fills_height`` wants the room outright — a note's
      editor, the roller's history, the portrait, the turn order — and already knows
      where to put it. Opt-in rather than derived: a widget's own vertical policy
      says nothing about whether its *children* have a use for the height (the
      roller's ``QGroupBox`` is ``Preferred`` and it is the history inside it that
      wants the room);
    * a **vertical box** gets a trailing stretch, unless something in it already
      claims the height (:func:`_claims_the_height`) — a table that stretches its own
      rows, which has a better use for it than an empty band under it would;
    * a **form** gets a trailing row of :class:`_Slack`, since a ``QFormLayout``
      takes widgets rather than items. One expanding row is enough: Qt gives the
      surplus to whatever expands and leaves the rest at its hint, which is what
      stops a form's captions drifting apart down a tall block.

    Anything else keeps the wrapper (:func:`_wrap_top_aligned`), because a layout
    this cannot reach into would spread the surplus across its items.
    """
    if getattr(section, "fills_height", False):
        return True
    layout = section.layout()
    if isinstance(layout, QBoxLayout):
        if layout.direction() not in (
            QBoxLayout.Direction.TopToBottom,
            QBoxLayout.Direction.BottomToTop,
        ):
            return False
        if not _claims_the_height(layout):
            layout.addStretch(1)
        return True
    if isinstance(layout, QFormLayout):
        layout.addRow(_Slack())
        return True
    return False


def _wrap_top_aligned(section: QWidget) -> QWidget:
    """Hold *section* at its content height over a spacer, for a layout that cannot
    hold one of its own. See :meth:`_InnerScroll.set_section`."""
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(section)
    layout.addStretch(1)
    return body


class _InnerScroll(QScrollArea):
    """The scroll area a block's section lives in — the frame's release valve.

    Two properties matter, and neither is the default:

    * it resizes its widget and scrolls **vertically only**, which makes a block's
      behaviour one sentence: *width adapts, height scrolls*. Width has reflow —
      columns shed, forms wrap, cards re-deal — so a section is always given
      exactly the viewport's width and asked to cope; height has no such
      mechanism, so it scrolls. Every table in the app already refuses a
      horizontal bar for the same reason.

      It is also the safe arrangement, and that is not a small point. Two
      ``AsNeeded`` bars around content whose height depends on its width is a loop
      with a name: the horizontal bar appears, the viewport gets shorter, the
      content needs more height, the vertical bar appears, the viewport gets
      narrower, and round again — through nested scroll areas (a block holds
      sections that hold tables that are scroll areas themselves) that is a
      recursion Qt resolves down the C stack. Taking one axis away removes the
      cycle rather than damping it;
    * it **declines a wheel it has no use for**. A `QScrollArea` normally swallows
      every wheel event whether or not it can scroll, so a block with nothing to
      scroll would eat the gesture and the page under it would stop moving.
      Passing the event up when this block has no range on that axis keeps the
      page's own scroll working everywhere it used to.

      It does *not* pass one up because it has reached the end of its range. A
      block that scrolls owns the wheel for as long as the pointer is over it —
      see :mod:`mm_companion.ui.wheel_guard`, where the same test
      (:func:`~mm_companion.ui.wheel_guard.has_scroll_range`) decides which
      surface a wheel over a spin box or a table belongs to. One rule, asked from
      both sides; two spellings of it is how they drift apart.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("blockScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_section(self, section: QWidget) -> None:
        """Hold *section* in the viewport, filling it, with its slack at the bottom.

        A resizable page is the first thing that can hand a block **more** room than
        its content wants, and ``setWidgetResizable`` gives all of it to the section
        — which then has to put it somewhere. A ``QVBoxLayout`` with nothing
        expanding in it spreads the surplus *equally between its items*, so a Powers
        block dragged twice as tall as its cards did not grow a margin at the
        bottom: it grew a gap between every line on every card. Every block with a
        stacked layout had the same defect and it read as a rendering bug, because
        nothing on screen said which of the gaps was the slack.

        The first answer to that was to hold the section at its content height over
        a spacer *outside* it. It cured the gaps and bought a worse fault: a section
        is a ``QGroupBox`` and draws a **border**, so a block dragged taller than its
        cards showed that border stopping half way down with bare block underneath,
        which reads as the block having failed to draw rather than as slack. The
        section has to fill the block; the slack has to be *inside* it.

        So the spacer moves in. A section whose top-level layout is a vertical box
        gets a trailing stretch of its own (:func:`_give_trailing_slack`) and is then
        handed the whole viewport: the surplus lands under the last row, inside the
        border, which is what "empty space in this block" should look like. A section
        that already expands downwards — one that states ``fills_height`` (a note's
        editor, the roller's history, the portrait, the turn order), or one whose
        layout has something expanding in it (a table that stretches its rows) —
        needs no stretch and is handed the height directly. A form takes a trailing
        row of nothing instead, since it holds widgets rather than items. Only a
        layout none of that can reach keeps the old wrapper.
        """
        fills = _give_trailing_slack(section)
        self.setWidget(section if fills else _wrap_top_aligned(section))
        self._pin_content_height()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """A width change re-wraps the content, so re-take the minimum below."""
        super().resizeEvent(event)
        self._pin_content_height()

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self._pin_content_height()
        return super().eventFilter(watched, event)

    def _pin_content_height(self) -> None:
        """State what the content really needs at *this* width, as an explicit minimum.

        This is the line that stops a block growing a scrollbar over content that
        fits. Qt decides whether to scroll from ``qSmartMinSize`` — which is the
        widget's ``minimumSizeHint``, and a ``QBoxLayout`` builds that one by
        **summing its items' unwrapped hints**, with no height-for-width anywhere in
        it. Every card in the Powers block reports the height it would take at its
        own preferred width; the block is far wider than that, the cards wrap into
        fewer lines, and the honest total came out 30px under the number Qt was
        using. So the block was handed a viewport at the wrapped height, told its
        content needed the unwrapped one, and dutifully put a scrollbar on a block
        with room to spare.

        ``heightForWidth`` is the measurement that *does* know the width, and
        :func:`content_height` asks for it. Pinned as an explicit ``minimumHeight``
        because that is the one number ``qSmartMinSize`` takes over the hint. It can
        only ever be *lowered* below what the widget already claimed — the point is
        to correct an overstatement, never to refuse a size on the content's behalf,
        which is the standing rule for everything on a page the user drags.

        Guarded on the number having moved, which is what makes it safe to call from
        a resize: the bar appearing narrows the viewport, which wraps more text, which
        raises the minimum again — but only once, since the bar does not go away.
        """
        widget = self.widget()
        if widget is None:
            return
        width = self.viewport().width()
        if width <= 0:
            return
        height = content_height(widget, width)
        if height != widget.minimumHeight():
            widget.setMinimumHeight(height)

    def wheelEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if not has_scroll_range(self, event):
            event.ignore()  # nothing here to scroll; let the page have it
            return
        super().wheelEvent(event)
        # And **accept it**, whether or not that moved anything. This is the line
        # that actually keeps the gesture: ``QAbstractSlider`` ignores a wheel that
        # does not change its value, so a block already at its bottom handed the
        # event back still ignored — and Qt walks an ignored wheel up the parent
        # chain by itself, to the page, which scrolled. Declining to chain is not
        # something the check above can express, because the decision to pass the
        # event on is made *after* this, by Qt, on a flag nobody here had set.
        event.accept()


class BlockFrame(QFrame):
    """One block: a :class:`TitleBar` above a section, sized by the user.

    A frame used to report its whole content as its minimum, in both dimensions,
    and that minimum climbed all the way out through the row, the page and the
    pinned strip to hold the *window* open. It was the right answer while the page
    arranged itself: nothing could squash a block, because nothing was allowed to
    try. On a page the user drags, it is a refusal.

    So the section lives in a :class:`_InnerScroll` and the frame's minimum is a
    title bar plus ``block.min-extent``. Past that a block reflows as far as its
    section knows how, and then **scrolls inside itself** — the same trade a
    popped-out :class:`BlockWindow` and the mini roller have always made, and for
    the same reason: clipping is not the alternative to being small, scrolling is.

    The block's :class:`~mm_companion.ui.block_sizes.RecommendedSize` survives as
    exactly that — the size it opens at, and the size the divider's detent sticks
    at (see :mod:`mm_companion.ui.grid_handle`). It constrains nothing.
    """

    #: How many turns the inner layout may spend recovering after a re-homing.
    RELAYOUT_TRIES = 5
    RELAYOUT_MS = 16

    def __init__(
        self,
        key: str,
        title: str,
        section: QWidget,
        size: RecommendedSize,
        host: DragHost,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.title = title
        # The block's plain name, kept apart from `title` because a section may
        # replace the latter with a live, priced caption ("Abilities — 24 PP").
        # Anything naming the block rather than reporting on it (the View menu)
        # wants this one, which never goes stale.
        self.base_title = title
        self.section = section
        # Built on first use: only a block that can be merged into ever needs one.
        self._merge_feedback: DropFeedback | None = None
        # Likewise for the warning that a divider drag is about to close it.
        self._close_feedback: DropFeedback | None = None
        self._size = RecommendedSize()
        self.setObjectName("blockFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.title_bar = TitleBar(key, title, host, self)

        # Re-homing this block leaves its inner layout stale; see _refresh_layout.
        self._relayout_tries = 0
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.timeout.connect(self._refresh_layout)

        # The release valve for the whole minimum chain: a scroll area does not
        # pass its child's minimum on, so whatever the section says it needs, the
        # frame can still be dragged smaller and the content scrolls instead.
        self._scroll = _InnerScroll(self)
        self._scroll.set_section(section)
        # The last content height this frame told its row about — see
        # :meth:`_follow_content_height`. Seeded to a number no section reports, so
        # the first answer always counts as a change.
        self._content_height = -1
        section.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(self._scroll, stretch=1)

        # A section may own a live caption (its running point cost); show it in the
        # title bar rather than duplicating it inside the block. See TitledSection.
        title_changed = getattr(section, "titleChanged", None)
        if title_changed is not None:
            title_changed.connect(self._set_title)
        block_title = getattr(section, "block_title", None)
        if callable(block_title) and block_title():
            self._set_title(block_title())

        self._apply_size(size)

    def _set_title(self, text: str) -> None:
        """Reflect a section's live title in both the title bar and window title.

        A floated block's window title is snapshotted at float time, so it has to be
        refreshed here too or it keeps whatever point subtotal was current when the
        block was dragged out.
        """
        self.title = text
        self.title_bar.set_title(text)
        window = self.window()
        if isinstance(window, BlockWindow):
            window.setWindowTitle(text)

    def _apply_size(self, size: RecommendedSize) -> None:
        """Record what this block would *like* to be. Nothing here constrains it.

        There used to be a maximum here, and a ``Fixed`` horizontal policy for the
        blocks whose width it pinned — that was how the old page stopped Abilities
        and Resistances being stretched across a row. A page whose columns the user
        drags has no use for either: every block expands to fill the cell it was
        given, and how big that cell is, is the user's business.
        """
        self._size = size
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def recommended_size(self) -> RecommendedSize:
        """The size this block reads well at — the divider's detent asks for this."""
        return self._size

    def content_size_hint(self) -> QSize:
        """What the section would take if nothing constrained its height.

        The honest answer to "how big does this block want to be", used for *Fit to
        content* and as the opening size of a block that states no recommendation
        (Abilities and Resistances, whose tables measure their own real columns and
        rows rather than trusting a number in a config file). Asked of the section
        directly, since the frame's own hint is now bounded by the scroll area.

        **Down, at the width this block actually has.** ``sizeHint`` answers with
        the height the content would take at its own *preferred* width, which for a
        block of wrapping cards is a much narrower block than the one on screen and
        so a good deal taller than the truth. That number was the block's height,
        and the scroll area then measured the same content properly, found it
        shorter, and put a scrollbar between the two — a Powers block scrolling
        30px inside a frame with nothing in the bottom 30px of it. Falls back to
        the hint before this frame has a width to measure at.

        A section may also answer **`preferred_height`** and name a height that is
        not its content's, which is how a disclosure inside a block stops
        rearranging the page: Name & Details opens its Details group into the room
        the block already has and scrolls past that, rather than growing the row and
        pushing every row under it down the page. Deliberately separate from what
        the scroll area measures — that still sees the whole expanded content, which
        is what makes it scroll rather than clip.
        """
        hint = self.section.sizeHint()
        width = self._scroll.viewport().width()
        height = self._preferred_height(width)
        if height is None:
            height = hint.height()
            if width > 0 and self.section.hasHeightForWidth():
                wrapped = self.section.heightForWidth(width)
                if wrapped > 0:
                    height = wrapped
        chrome = self.title_bar.sizeHint().height() + 2 * self.frameWidth()
        return QSize(hint.width() + 2 * self.frameWidth(), height + chrome)

    def _preferred_height(self, width: int) -> int | None:
        """What the section says it would like to be tall, if it says anything."""
        ask = getattr(self.section, "preferred_height", None)
        if not callable(ask):
            return None
        height = ask(width)
        return None if height is None else int(height)

    def changeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """Re-run the block's own layout after it is re-homed (see :meth:`_refresh_layout`)."""
        if event.type() == QEvent.Type.ParentChange:
            self._relayout.start(0)
        super().changeEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        """Follow the section's own height, which nothing else can carry out of here.

        The scroll area is a **barrier**, deliberately: it is what stops the
        section's minimum climbing out through the row, the page and the window
        (see the class docstring). But this frame's :meth:`sizeHint` is the
        section's content height all the same, and Qt only re-asks a widget for its
        hint when something *invalidates the layout holding it* — which the scroll
        area now absorbs. So a section that grew taller told the viewport and
        nobody else: the row went on using the height it had cached, and the block
        kept the shape it had when the page last rebuilt.

        That is what a narrowed block looked like it was doing wrong. Its
        descriptions wrapped, its rows got taller, the table said so — and the row
        stayed exactly as tall as it was, so the extra content ended up scrolled
        out of sight inside a frame that had plenty of page under it.
        """
        if watched is self.section and event.type() == QEvent.Type.LayoutRequest:
            self._follow_content_height()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """A width change re-wraps the section, so re-ask how tall it now is.

        The belt to :meth:`eventFilter`'s braces: a section whose height moved
        without any layout of its own being invalidated (a table re-measuring its
        own wrapped rows in place) posts nothing at all.
        """
        super().resizeEvent(event)
        self._follow_content_height()

    def _follow_content_height(self) -> None:
        """Tell whoever holds this block that its content height has moved.

        Guarded on the height actually having changed, and that guard is what makes
        it safe to call from a resize: ``updateGeometry`` asks the row to lay out
        again, which resizes this frame, which is a resize event. The chain settles
        because the second pass finds the same number and stops. It can still climb
        once — a taller page can bring the page's scrollbar out, which narrows every
        block, which wraps more text — but that is monotonic and one-way, since the
        bar does not go away again.
        """
        height = self.content_size_hint().height()
        if height == self._content_height:
            return
        self._content_height = height
        self.updateGeometry()

    def _refresh_layout(self) -> None:
        """Recompute the block's inner layout, and again until it is not degenerate.

        A block moves between three hosts — a row on the page, a line of the pinned
        strip, and its own floating window — and on the way it is briefly given
        *zero height* by a container that has not been sized yet. Its layout
        activates against that and caches a geometry a few pixels **negative**; the
        block then reaches its real size while hidden, so no resize event follows,
        Qt has no reason to run the layout again, and the block draws as an empty
        framed box. Only nudging its size brought it back — which is why resizing
        the strip appeared to fix it.

        Re-activating on the turn *after* a re-homing catches it, once the container
        has handed out real geometry; if the layout still looks degenerate (the
        container needed more than one turn to settle), it tries again, bounded so
        it cannot spin.
        """
        layout = self.layout()
        if layout is None:
            return
        layout.invalidate()
        layout.activate()
        self.updateGeometry()
        degenerate = layout.geometry().height() <= 0 < self.height()
        if degenerate and self._relayout_tries < self.RELAYOUT_TRIES:
            self._relayout_tries += 1
            self._relayout.start(self.RELAYOUT_MS)
        else:
            self._relayout_tries = 0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The recommendation across, the content down — and the axes differ for a
        reason rather than an oversight.

        A block's **width** is *shared*: it and its neighbours divide one row
        between them, and what makes that division good is a stable declared
        preference rather than whatever happens to be typed into the block today.
        A Powers block with nine powers in it would otherwise take the row.

        A block's **height** is *taken*: an undragged row is exactly as tall as
        what is in it, and the page scrolls — which is what the sheet has always
        done, and what keeps adding a skill making the Skills block taller instead
        of making it scroll inside a height nobody chose.

        A block that states no recommendation (Abilities, Resistances) is sized
        across by its content too, because its table measures its own real columns
        and that beats a number a denser preset would make wrong.
        """
        content = self.content_size_hint()
        return QSize(self._size.width or content.width(), content.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """A title bar and ``block.min-extent``, and nothing about the content.

        This used to be ``max(content, the JSON floor)`` in both dimensions, which
        is what climbed out through the row, the page, the pinned strip and the
        window to hold the whole application open at the sum of every block's
        content. Reporting the content here is exactly what a user-resizable grid
        cannot do: a minimum is a refusal, and the answer to "this block is too
        small to read" has to be the user's to give.

        The section is in a scroll area, so this is safe rather than merely
        permissive — nothing is clipped, it scrolls. The floor that is left is
        about being able to *find* a block you squashed: a title bar you can still
        grab and drag back open.
        """
        extent = int(theme.metric("block.min-extent"))
        chrome = self.title_bar.minimumSizeHint().height() + 2 * self.frameWidth()
        return QSize(extent, chrome + extent)

    def set_vertical_fill(self, fill: bool) -> None:
        """Kept for callers; there is nothing left for it to do.

        A block used to be exactly as tall as its content, so a board with only a
        few blocks left a dead gap under the last one and the canvas flipped that
        one block to ``Expanding`` to soak it up. Every block expands now — how
        tall each row is, is a size in the arrangement and a divider the user can
        drag — so there is no leftover height for anyone to be given.
        """
        return

    def set_tabbed(self, tabbed: bool) -> None:
        """Lend this block's title bar to a tab group, or take it back.

        A block in a group is drawn under the group's tab bar and the group's
        buttons act on whichever member is showing, so a second row of chrome
        would say nothing the first does not. The bar is only *hidden* — the frame
        keeps it, keeps its live caption, and gets it straight back when the block
        is dragged out — because a block inside a group and outside one has to be
        the same widget or none of the rest of this holds.
        """
        self.title_bar.setVisible(not tabbed)
        self.updateGeometry()

    def set_merge_target(self, active: bool) -> None:
        """Dress the frame as the block a drop would merge *into*.

        The counterpart of the canvas's insert line, and deliberately a different
        kind of mark: a line says "the block lands here", a wash over a whole
        frame says "the block goes *in* here", which is what a merge does.
        Built lazily so a frame that is never a merge target — every one but a
        Notes block — costs nothing.
        """
        if self._merge_feedback is None:
            if not active:
                return
            # A wash and **no border**. Partly because it is the right mark — a
            # line says "the block lands here", a filled frame says "the block
            # goes *in* here" — but mostly because a stylesheet border changes
            # the frame's box, which relayouts the page *during the drag*: the
            # target would shift out from under the cursor the instant it lit up.
            self._merge_feedback = DropFeedback(
                self, "#blockFrame", radius="radius.card", border=False, wash=MERGE_WASH
            )
        if active:
            self._merge_feedback.show_accept()
        else:
            self._merge_feedback.clear()

    def set_closing(self, closing: bool) -> None:
        """Warn that letting go of the divider now would close this block.

        The counterpart of :meth:`set_merge_target`, in the other colour and for
        the other kind of drag. A block can be dragged to a sliver — that is the
        whole bargain of a grid the user owns — but a sliver is not something
        anybody can read or find again, so past ``grid.close-extent`` the honest
        reading of the gesture is that they are getting rid of it. Saying so
        while the mouse is still down is what makes that a choice rather than a
        surprise; nothing is destroyed either way, and Ctrl+Z puts it back.

        Its own feedback and not ``_merge_feedback``: the two states can never be
        up at once, but sharing one would mean a merge and a close taking each
        other's mark down, and ``DropFeedback.clear`` returns a target to *its*
        idle style, which is not something to guess at across two meanings.
        """
        if self._close_feedback is None:
            if not closing:
                return
            # Border and wash both: this one is not sitting inside another
            # highlighted target the way the merge wash is, and a warning wants
            # every bit of the weight it can carry.
            self._close_feedback = DropFeedback(self, "#blockFrame", radius="radius.card")
        if closing:
            # ``solo=False``: a row squashed to nothing warns on every block in
            # it, and the usual one-at-a-time discipline would leave only the
            # last of them marked. The canvas clears each of them on release.
            self._close_feedback.show_reject(sticky=True, solo=False)
        else:
            self._close_feedback.clear()

    def set_locked(self, locked: bool) -> None:
        """Forward read-only view mode to the section, and re-report the block's size.

        Locking is not only a costume change. A locked field sheds its border and its
        padding, and several blocks hide their editing entry points outright, so the
        block's own minimum really does move — mostly in height, since the width is
        held lock-invariant (see :mod:`mm_companion.ui.lock` and
        ``tests/test_lock_geometry.py``). Much of that chrome lives in widgets no
        layout ever sees — a table's cell widgets are index widgets, not layout items
        — so Qt's own invalidation stops before it reaches this frame. Saying so here
        is what makes the rest of the chain ask again: the row and the page's
        minimum, and for a pinned block the slot, the strip and the window.
        """
        self.section.set_locked(locked)
        self.updateGeometry()


class BlockWindow(QWidget):
    """A top-level window hosting a floated-out :class:`BlockFrame`.

    Owned by the sheet (so it closes with it and isn't garbage-collected) and
    flagged as a tool window. Its title bar reuses the same drag gesture, so the
    user can drag it back onto the sheet to re-dock. Closing it via the window
    chrome hides the block rather than losing it.

    This window used to wrap the frame in a scroll area of its own, because that
    was the only way a floated block could be dragged smaller than its content: a
    :class:`QScrollArea` does not pass its child's minimum on. The frame carries
    its own now (see :class:`_InnerScroll`) and every docked block makes the same
    bargain, so the window hosts the frame directly and a second scroll area would
    only mean two sets of scrollbars for one block. What is left here is the floor
    — ``float.min-width``/``float.min-height``, exactly as the mini roller's is
    ``compact.min-*`` (see :mod:`mm_companion.ui.compact`) — because a window
    someone shoved into a corner still needs to be findable.

    It is **frameless**, and that is a trade rather than a decoration: a popped-out
    block spends its life beside somebody else's application, where the OS title
    bar is most of what makes it read as a document rather than a tool. What the
    frame was doing is supplied instead by the block's own title bar (drag, and the
    ``✕`` that hides it) and a :class:`QSizeGrip` in the corner. The flags are set
    here, before the first ``show()``, because changing them on a visible window
    costs a hide/recreate cycle (see
    :func:`~mm_companion.ui.frameless.apply_window_flags`).
    """

    def __init__(self, key: str, host: DragHost, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self._key = key
        self._host = host
        self.setObjectName("blockWindow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

        self._frame: BlockFrame | None = None
        self._grip = size_grip_row(self)
        layout.addWidget(self._grip)
        self.setMinimumSize(
            int(theme.metric("float.min-width")), int(theme.metric("float.min-height"))
        )

    def set_on_top(self, on_top: bool) -> None:
        """Keep this window above other applications, or let it fall behind."""
        apply_window_flags(self, frameless=True, on_top=bool(on_top))

    def set_frame(self, frame: BlockFrame) -> None:
        """Host *frame*, giving the window the frame's title as its window title.

        The frame goes *above* the size grip, which is why the grip is added first
        and the frame inserted at 0 rather than appended: a grip that drifts into
        the middle of the window is not a grip.
        """
        self.setWindowTitle(frame.title)
        self._frame = frame
        self._layout.insertWidget(0, frame, stretch=1)

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Closing the window hides the block instead of destroying it."""
        self._host.request_hide(self._key)
        event.ignore()
