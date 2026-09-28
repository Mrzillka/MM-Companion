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

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.blocks.bus import NOTIFICATIONS
from mm_companion.ui.layout_tree import Leaf, Node, Split
from mm_companion.ui.sections.titled_section import set_simple_frame
from mm_companion.ui.simple import views as _views  # noqa: F401 - registers the base views
from mm_companion.ui.simple.layout import (
    PRESET_CUSTOM,
    PRESET_STANDARD,
    SimpleLayout,
    custom_layout,
    standard_layout,
)
from mm_companion.ui.simple.registry import SimpleContext, simple_view
from mm_companion.ui.simple.style import heading_label
from mm_companion.ui.widgets import discard_widget

#: How wide the strip beside the page opens when the arrangement names no width.
DEFAULT_STRIP_EXTENT = 360


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
    host = QWidget()
    horizontal = isinstance(node, Split) and node.horizontal
    layout = QHBoxLayout(host) if horizontal else QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(gap)
    weights = node.usable_sizes()
    for index, child in enumerate(node.children):
        widget = render_node(child, boxes)
        if horizontal:
            weight = weights[index] if weights and weights[index] > 0 else 0
            layout.addWidget(widget, stretch=max(1, weight) if weights else 1)
        else:
            layout.addWidget(widget)
    host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return host


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
        self._layout_model = model
        for key in model.keys():
            box = self._make_box(key, live=True)
            if box is not None:
                self._boxes[key] = box
        self._page.show_tree(model.page, self._boxes)
        self._strip_page.show_tree(model.strip, self._boxes)
        self._place_strip(model)
        self.rebuilt.emit()

    def _teardown(self) -> None:
        for key, box in list(self._boxes.items()):
            if key in self._borrowed:
                section = box.release_body()
                self._return_section(key, section)
        self._borrowed.clear()
        self._page.clear()
        self._strip_page.clear()
        self._boxes.clear()
        self._views.clear()
        self._layout_model = None

    def _make_box(self, key: str, *, live: bool) -> SimpleBox | None:
        """The box for *key*: its own view, or its section borrowed from its frame."""
        spec = simple_view(key)
        title = self._sheet.block_frame(key).base_title
        if spec.borrowed:
            section = self._borrow_section(key)
            if section is None:
                return None
            self._borrowed[key] = section
            return SimpleBox(key, title, section, heading=spec.heading)
        view = spec.factory(self.context_for(key))
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
        self._strip_pending = has_strip
        self._apply_strip_extent()

    def _apply_strip_extent(self) -> None:
        if not getattr(self, "_strip_pending", False):
            return
        vertical = self._splitter.orientation() == Qt.Orientation.Vertical
        total = self._splitter.height() if vertical else self._splitter.width()
        if total <= self._strip_extent:
            return  # not laid out yet; resizeEvent comes back here
        first = self._splitter.indexOf(self._strip) == 0
        page = total - self._strip_extent
        self._splitter.setSizes([self._strip_extent, page] if first else [page, self._strip_extent])
        self._strip_pending = False

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
            QTimer.singleShot(0, self._rebuild)

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
