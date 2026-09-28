"""The simple sheet: the character on one tight page, for playing rather than building.

The edit sheet is a workbench — every block a movable, resizable panel with its own
title bar, and every number an input. None of that is wanted at the table. The simple
sheet is the same character laid out the way a printed sheet is: boxes butted up
against each other with a hairline gap, a small heading on each, the values a player
reads mid-fight large and everything that qualifies them in small print.

It is **not a second copy of the sheet.** Two kinds of box sit on it
(:mod:`mm_companion.ui.simple.registry`):

* a *view* (:mod:`mm_companion.ui.simple.views`) — a small read-mostly widget over the
  shared model, for a block a player only reads. What a player *does* through one (a
  hero point, a condition, a roll) goes through the live section's own funnel, so the
  note in the roll history, the undo step and the GM's card all see it exactly as they
  would from the edit sheet;
* a *borrowed section* — the block's real widget, lent out of its
  :class:`~mm_companion.ui.block_frame.BlockFrame` for as long as the simple sheet is up
  and handed back after. The power cards, the gear, the roller and the notes are all
  play-time controls with a great deal of behaviour behind them, and borrowing them is
  what keeps every one of those behaviours — an array's live member, a Dynamic array's
  share dials, Extra Effort, a worn jacket — working here by construction rather than
  by a second implementation. A section with a quieter look for this page says so with
  ``set_simple(bool)``.

Nothing on this page is dragged or resized: its arrangement is a preset
(:mod:`mm_companion.ui.simple.layout`) — the fixed Standard one, or Custom, which *is*
the edit sheet's arrangement read through. Width still adapts (every box reflows) and
height scrolls, the same bargain as the edit page; the difference is that a row is
always exactly as tall as its tallest box and the boxes in it share that height, so
their borders line up the way ruled boxes on paper do.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGroupBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.blocks.bus import NOTIFICATIONS
from mm_companion.ui.layout_tree import Leaf, Node, Split
from mm_companion.ui.reflow import ReflowBox
from mm_companion.ui.sections.titled_section import set_simple_frame
from mm_companion.ui.simple import views as _views  # noqa: F401 - registers the base views
from mm_companion.ui.simple.layout import (
    PRESET_CUSTOM,
    PRESET_STANDARD,
    SimpleLayout,
    custom_layout,
    standard_layout,
    without_keys,
)
from mm_companion.ui.simple.registry import SimpleContext, simple_view
from mm_companion.ui.simple.style import heading_label
from mm_companion.ui.widgets import discard_widget, no_reentry

#: How wide the strip beside the page opens when the arrangement names no width.
DEFAULT_STRIP_EXTENT = 360
#: The most of the window the strip opens at, whatever width it asks for.
STRIP_SHARE = 0.4


class SimpleBox(QFrame):
    """One box of the simple sheet: a small heading over the block's content.

    Everything the edit sheet's frame carries — a title bar to drag it by, pin, pop-out
    and close buttons, an inner scroll area — is gone. What is left is a hairline border
    and the block's name in small capitals, which is all a box on paper has.

    It never reports a minimum *width* its content decides: a row divides its width by
    weight, and past what a box can reflow into, it clips, exactly as a block on the
    edit page does. Height is the content's, and the page scrolls.
    """

    def __init__(self, key: str, title: str, body: QWidget, *, heading: bool = True) -> None:
        super().__init__()
        self.key = key
        self.body = body
        self.setObjectName("simpleBox")
        radius = int(theme.metric("radius.card"))
        width = int(theme.metric("border.width"))
        self.setStyleSheet(
            f"#simpleBox {{ border: {width}px solid {theme.color('border.card')};"
            f" border-radius: {radius}px; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*theme.box("simple.margins"))
        layout.setSpacing(int(theme.metric("simple.spacing")))
        self.heading = heading_label(title) if heading else None
        if self.heading is not None:
            layout.addWidget(self.heading)
        layout.addWidget(body, stretch=1)
        body.show()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(int(theme.metric("block.min-extent")), super().minimumSizeHint().height())

    def release_body(self) -> QWidget:
        """Take the content back out (a borrowed section going home)."""
        body = self.body
        body.hide()
        self.layout().removeWidget(body)
        return body


class SimplePage(QWidget):
    """The rows of the page, rendered from a layout tree.

    A vertical split is a stack of rows; a horizontal one divides its row by weight
    (the Standard preset's shares, or the Custom one's remembered pixel sizes, which
    read as the same thing). A leaf holding several blocks — a tab group on the edit
    sheet — shows all of them, one under another: tabs are a way of saving room on a
    screen, and a sheet you print or glance at wants every box on it.
    """

    def __init__(self, parent: QWidget | None = None, *, fill: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("simplePage")
        # A strip hands its boxes all of its height (the roller's history wants it);
        # the page stacks its rows at their own heights and scrolls.
        self._fill = fill
        self._layout = QVBoxLayout(self)
        margin = int(theme.metric("simple.page-margin"))
        self._layout.setContentsMargins(margin, margin, margin, margin)
        self._layout.setSpacing(int(theme.metric("simple.gap")))
        self._content: QWidget | None = None

    def show_tree(self, node: Node | None, boxes: dict[str, SimpleBox]) -> None:
        """Lay *node* out over *boxes* (keyed by block), replacing what was there."""
        self.clear()
        if node is None:
            return
        self._content = render_node(node, boxes)
        if self._fill:
            self._layout.addWidget(self._content, stretch=1)
        else:
            self._layout.addWidget(self._content)
            self._layout.addStretch(1)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Nothing — the page's height is its height *at the width it has*.

        Qt builds a layout's minimum by summing its items' minimums, and a wrapped label's
        minimum is its height at its *narrowest* — so the Advantages box alone claimed
        three times the height its lines take at the width it really gets, and the scroll
        area, which never lets a page be shorter than its minimum, handed every row the
        difference as empty space. The scroll area asks ``heightForWidth`` at the
        viewport's width as well and takes the larger answer, so stating no minimum here
        leaves that — the honest number — deciding.
        """
        return QSize(0, 0)

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                discard_widget(widget)
        self._content = None

    def rows(self) -> list[QWidget]:
        """The row widgets, top to bottom — where a printed page may break."""
        if self._content is None:
            return []
        layout = self._content.layout()
        if not isinstance(layout, QVBoxLayout):
            return [self._content]
        return [
            layout.itemAt(i).widget()
            for i in range(layout.count())
            if layout.itemAt(i).widget() is not None
        ]


def render_node(node: Node, boxes: dict[str, SimpleBox]) -> QWidget:
    """A widget for *node*: a box, a stack of them, or a row/column of either."""
    gap = int(theme.metric("simple.gap"))
    if isinstance(node, Leaf):
        present = [boxes[key] for key in node.keys if key in boxes]
        if len(present) == 1:
            return present[0]
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(gap)
        for box in present:
            column.addWidget(box)
        host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return host
    children = [render_node(child, boxes) for child in node.children]
    if isinstance(node, Split) and node.horizontal:
        weights = node.usable_sizes()
        shares = [max(1, w) for w in weights] if weights else [1] * len(children)
        return SimpleRow(children, shares)
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(gap)
    for widget in children:
        layout.addWidget(widget)
    host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return host


def _comfortable_width(part: QWidget) -> int:
    """How wide *part* reads well at: its content's own say, or ``simple.box-min``."""
    body = part.body if isinstance(part, SimpleBox) else part
    stated = getattr(body, "simple_min_width", None)
    if callable(stated):
        stated = stated()
    return int(stated) if stated else int(theme.metric("simple.box-min"))


class SimpleRow(ReflowBox, QWidget):
    """Boxes side by side, sharing the row's width by weight — until that is too tight.

    A row of three on a narrow window left each box a sliver: the System block's
    heading cut off, its hero points spilling past its border. So a row stacks its
    boxes one under another once any of them would get less than ``simple.box-min``
    across, and puts them back side by side when the room returns. It is the edit
    sheet's own reflow (:class:`~mm_companion.ui.reflow.ReflowBox`), dead-band and
    re-entry guard included — stacking changes the page's height, which can bring a
    scrollbar in, which narrows the row back over the line.
    """

    def __init__(self, parts: list[QWidget], shares: list[int]) -> None:
        super().__init__()
        self._parts = parts
        self._shares = shares
        self.REFLOW_SPACING = int(theme.metric("simple.gap"))
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.REFLOW_SPACING)
        for part, share in zip(parts, shares, strict=True):
            layout.addWidget(part, stretch=share)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.init_reflow(row=True)

    def reflow_parts(self) -> list[QWidget]:
        return self._parts

    def apply_reflow(self, row: bool) -> None:
        layout = self.layout()
        layout.setDirection(
            QBoxLayout.Direction.LeftToRight if row else QBoxLayout.Direction.TopToBottom
        )
        # Stacked, a box is as tall as its content; the weights only ever divided width.
        for index, share in enumerate(self._shares):
            layout.setStretch(index, share if row else 0)

    def row_minimum_width(self) -> int:
        """The narrowest the row can be with every box still getting its comfortable width.

        A box's comfortable width is ``simple.box-min``, unless what it holds says
        otherwise (``simple_min_width``) — a portrait reads perfectly well at half of
        what a column of text needs, and holding its narrow share of the header row to
        the text's number stacked the header on an ordinary window.
        """
        total = sum(self._shares)
        needed = max(
            _comfortable_width(part) * total / share
            for part, share in zip(self._parts, self._shares, strict=True)
        )
        return round(needed) + self.REFLOW_SPACING * (len(self._parts) - 1)

    @no_reentry
    def _sync(self) -> None:
        self.sync_reflow()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self._sync()


def _is_empty(view: QWidget | None) -> bool:
    """Whether a view or a section says it has nothing to show (``is_empty()``).

    Opt-in, and only for a block whose emptiness is a fact about the character rather
    than a control: no powers, no gear, no advantages. The Conditions box is never
    empty in this sense — it is where a condition goes on — and nor is a live Notes
    block, which is where a player opens one.
    """
    ask = getattr(view, "is_empty", None)
    return bool(ask()) if callable(ask) else False


def dress_borrowed(section: QWidget, simple: bool) -> None:
    """Put a borrowed section into its simple look, or take it back out.

    A section that has a look of its own for this page says so with ``set_simple``;
    any other group box — the notes, the roller, a mod's block — at least loses the
    border it would otherwise draw inside its simple box.
    """
    setter = getattr(section, "set_simple", None)
    if callable(setter):
        setter(simple)
    elif isinstance(section, QGroupBox):
        set_simple_frame(section, simple)


class SimpleSheet(QWidget):
    """The play view of one :class:`~mm_companion.ui.character_sheet.CharacterSheet`.

    Built lazily by the sheet the first time it is asked for and kept after, so its
    subscriptions are made once. :meth:`activate` borrows and builds; :meth:`deactivate`
    gives everything back. Between the two the sheet's own page is hidden and every
    floated block window is off the screen (they show the edit look of blocks that are
    now here).
    """

    #: The arrangement was rebuilt — tests and the print path wait on it.
    rebuilt = Signal()

    def __init__(self, sheet, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sheet = sheet
        self._active = False
        self._preset = PRESET_STANDARD
        self._layout_model: SimpleLayout | None = None
        self._boxes: dict[str, SimpleBox] = {}
        self._views: dict[str, QWidget] = {}
        self._borrowed: dict[str, QWidget] = {}
        self._hidden_windows: list[QWidget] = []
        self._rebuild_pending = False

        self._page = SimplePage()
        self._scroll = QScrollArea()
        self._scroll.setObjectName("simpleScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._page)

        self._strip_page = SimplePage(fill=True)
        self._strip = QScrollArea()
        self._strip.setObjectName("simpleStrip")
        self._strip.setWidgetResizable(True)
        self._strip.setFrameShape(QFrame.Shape.NoFrame)
        self._strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._strip.setWidget(self._strip_page)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._scroll)
        self._splitter.addWidget(self._strip)
        self._strip_dragged = False
        self._placing = False
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self._strip_extent = DEFAULT_STRIP_EXTENT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._splitter)

        # One redraw per turn, whatever was published: every view reads the model
        # afresh, so what changed does not matter — only *that* something did.
        for topic in NOTIFICATIONS:
            sheet.bus.subscribe(topic, self._refresh_views, coalesce=True)
        canvas = sheet.canvas
        canvas.arrangement_changed.connect(self._on_arrangement_changed)
        canvas.block_added.connect(self._on_blocks_changed)
        canvas.block_removed.connect(self._on_blocks_changed)

    # -- state -----------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def preset(self) -> str:
        return self._preset

    @property
    def page(self) -> SimplePage:
        return self._page

    @property
    def scroll_area(self) -> QScrollArea:
        return self._scroll

    def layout_model(self) -> SimpleLayout | None:
        """The arrangement on screen, or ``None`` while the sheet is not up."""
        return self._layout_model

    def box(self, key: str) -> SimpleBox | None:
        return self._boxes.get(key)

    def view(self, key: str) -> QWidget | None:
        """The simple view drawn for *key*, or the borrowed section, or ``None``."""
        return self._views.get(key) or self._borrowed.get(key)

    def shown_keys(self) -> list[str]:
        return list(self._boxes)

    def set_preset(self, preset: str) -> None:
        """Switch between the Standard and Custom arrangements."""
        preset = preset if preset in (PRESET_STANDARD, PRESET_CUSTOM) else PRESET_STANDARD
        if preset == self._preset:
            return
        self._preset = preset
        if self._active:
            self._rebuild()

    # -- the arrangement -------------------------------------------------------

    def compute_layout(self) -> SimpleLayout:
        """The arrangement the current preset gives the sheet's current blocks."""
        keys = self._sheet.block_keys()
        if self._preset == PRESET_CUSTOM:
            return custom_layout(self._sheet.arrangement(), keys)
        return standard_layout(keys)

    def activate(self) -> None:
        """Borrow, build and show. Idempotent."""
        if self._active:
            return
        self._active = True
        self._hide_windows()
        self._build()

    def deactivate(self) -> None:
        """Give every borrowed section back and tear the page down. Idempotent."""
        if not self._active:
            return
        self._teardown()
        self._active = False
        self._show_windows()

    def _rebuild(self) -> None:
        self._rebuild_pending = False
        if not self._active:
            return
        self._teardown()
        self._hide_windows()
        self._build()

    def _build(self) -> None:
        model = self.compute_layout()
        for key in model.keys():
            box = self._make_box(key)
            if box is not None:
                self._boxes[key] = box
        # A block with nothing to show — a portrait never loaded, a character with no
        # gear — gives its room to its neighbours rather than standing there empty.
        model = without_keys(model, set(model.keys()) - set(self._boxes))
        self._layout_model = model
        self._page.show_tree(model.page, self._boxes)
        self._strip_page.show_tree(model.strip, self._boxes)
        self._place_strip(model)
        self.rebuilt.emit()

    def _teardown(self) -> None:
        for key, box in list(self._boxes.items()):
            if key not in self._borrowed:
                continue
            section = self._borrowed[key]
            # A block destroyed while the page was up (a Notes copy removed) took its
            # frame and section with it; there is nothing left to give back.
            if not (shiboken6.isValid(section) and key in self._sheet.block_keys()):
                continue
            box.release_body()
            self._return_section(key, section)
        self._borrowed.clear()
        self._page.clear()
        self._strip_page.clear()
        self._boxes.clear()
        self._views.clear()
        self._layout_model = None

    def _make_box(self, key: str) -> SimpleBox | None:
        """The box for *key*: its own view, or its section borrowed from its frame.

        ``None`` for a block with nothing to show (see :func:`_is_empty`) — asked of a
        section *before* it is lent, so an empty one never leaves its frame at all.
        """
        spec = simple_view(key)
        title = self._sheet.block_frame(key).base_title
        if spec.borrowed:
            if _is_empty(self._sheet.section(key)):
                return None
            section = self._borrow_section(key)
            self._borrowed[key] = section
            return SimpleBox(key, title, section, heading=spec.heading)
        view = spec.factory(self.context_for(key))
        if _is_empty(view):
            view.deleteLater()  # never parented, never shown: nothing to flash
            return None
        self._views[key] = view
        return SimpleBox(key, title, view, heading=spec.heading)

    def context_for(self, key: str) -> SimpleContext:
        return SimpleContext(
            self._sheet.data,
            self._sheet.character,
            self._sheet.section(key),
            npc=self._sheet.is_npc,
            key=key,
        )

    def _borrow_section(self, key: str) -> QWidget | None:
        frame = self._sheet.block_frame(key)
        section = frame.lend_section()
        dress_borrowed(section, True)
        return section

    def _return_section(self, key: str, section: QWidget) -> None:
        dress_borrowed(section, False)
        self._sheet.block_frame(key).take_back_section()

    def _place_strip(self, model: SimpleLayout) -> None:
        """Put the strip on the edge the layout names, at the width it asks for.

        The width is the edit sheet's own strip width for the Custom preset, and
        :data:`DEFAULT_STRIP_EXTENT` otherwise. It is *applied* once the splitter has a
        size of its own to divide (:meth:`_apply_strip_extent`): a splitter handed sizes
        before it is laid out divides nothing, and splits the difference later.
        """
        has_strip = model.strip is not None
        self._strip.setVisible(has_strip)
        vertical = model.edge in ("top", "bottom")
        self._splitter.setOrientation(
            Qt.Orientation.Vertical if vertical else Qt.Orientation.Horizontal
        )
        first = model.edge in ("left", "top")
        if (self._splitter.indexOf(self._strip) == 0) != first:
            self._splitter.insertWidget(0 if first else 1, self._strip)
        # The page takes whatever the window gains; the strip keeps its width.
        self._splitter.setStretchFactor(self._splitter.indexOf(self._scroll), 1)
        self._splitter.setStretchFactor(self._splitter.indexOf(self._strip), 0)
        extent = DEFAULT_STRIP_EXTENT
        if self._preset == PRESET_CUSTOM:
            region = self._sheet.arrangement().get("region") or {}
            stated = region.get("extent")
            if isinstance(stated, int) and not isinstance(stated, bool) and stated > 0:
                extent = stated
        self._strip_extent = extent
        self._apply_strip_extent()

    def _apply_strip_extent(self) -> None:
        """Size the strip: its asked-for width, but never more than a share of the window.

        Re-applied on every resize until the player drags the divider themselves — the
        first sizing happens before the window has its final width, and a strip sized
        against a half-built window stayed that narrow for good. Once dragged, the
        divider is theirs.
        """
        if self._strip_dragged or not self._strip.isVisibleTo(self):
            return
        vertical = self._splitter.orientation() == Qt.Orientation.Vertical
        total = self._splitter.height() if vertical else self._splitter.width()
        if total <= 0:
            return
        first = self._splitter.indexOf(self._strip) == 0
        # On a narrow window the page is what the player is reading; the roller
        # reflows into what is left.
        strip = min(self._strip_extent, round(total * STRIP_SHARE))
        sizes = [strip, total - strip] if first else [total - strip, strip]
        if self._splitter.sizes() != sizes:
            self._placing = True
            try:
                self._splitter.setSizes(sizes)
            finally:
                self._placing = False

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        if not self._placing:
            self._strip_dragged = True

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self._apply_strip_extent()

    # -- floated windows -------------------------------------------------------

    def _hide_windows(self) -> None:
        """Take every floated block window off the screen while the page is up.

        They show the *edit* look of blocks that are on this page now — and a borrowed
        one would show an empty frame — so none of them has anything to say here.
        """
        canvas = self._sheet.canvas
        for key in self._sheet.block_keys():
            window = canvas.block_window(key)
            if window is not None and window.isVisible():
                window.hide()
                self._hidden_windows.append(window)

    def _show_windows(self) -> None:
        windows, self._hidden_windows = self._hidden_windows, []
        for window in windows:
            try:
                window.show()
            except RuntimeError:  # the window went while the page was up
                continue

    def keep_windows_hidden(self) -> None:
        """Re-hide the floated windows after something else showed them (compact mode)."""
        if self._active:
            self._hide_windows()

    # -- following the sheet ---------------------------------------------------

    def _refresh_views(self) -> None:
        if not self._active:
            return
        for view in list(self._views.values()):
            refresh = getattr(view, "refresh", None)
            if callable(refresh):
                refresh()

    def refresh_now(self) -> None:
        """Redraw every view from the model at once (tests, and after a load)."""
        self._refresh_views()

    def _schedule_rebuild(self) -> None:
        if self._active and not self._rebuild_pending:
            self._rebuild_pending = True
            # Tied to this widget, so a window closed before the turn ends cancels it.
            QTimer.singleShot(0, self, self._rebuild)

    def _on_arrangement_changed(self) -> None:
        # The Custom preset *is* the edit sheet's arrangement, so a block reopened
        # from the View menu (or revealed by a roll it serves) has to appear here too.
        if self._active and self._preset == PRESET_CUSTOM:
            self._schedule_rebuild()

    def _on_blocks_changed(self, _key: str) -> None:
        self._schedule_rebuild()

    def reveal(self, key: str) -> None:
        """Make sure block *key* is on the page — a roll it serves was just asked for.

        The Standard preset shows every block already. The Custom one follows the
        edit sheet's own arrangement, where the sheet has just reopened it.
        """
        if self._active and key not in self._boxes:
            self._schedule_rebuild()
