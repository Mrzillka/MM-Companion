"""The reusable grid popup (``ui/action_grid.py``) and the action icons it draws."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from mm_companion.ui import svg_assets, theme
from mm_companion.ui.action_grid import ActionGridPopup, GridItem, GridSection
from mm_companion.ui.svg_assets import action_icon_resource, tinted_svg_pixmap


def _sections(count: int = 8, disabled: str = "") -> list[GridSection]:
    first = tuple(
        GridItem(f"a{i}", f"Tile {i}", "aid", f"Tip {i}", enabled=f"a{i}" != disabled)
        for i in range(count)
    )
    second = (GridItem("b0", "Other", "reroll", "Tip"),)
    return [GridSection("First", first), GridSection("Second", second), GridSection("Empty")]


def _popup(qapp, sections=None) -> tuple[QWidget, ActionGridPopup]:
    anchor = QWidget()
    anchor.resize(200, 30)
    anchor.show()
    popup = ActionGridPopup(sections or _sections(), anchor)
    return anchor, popup


def test_it_builds_a_tile_per_item_and_skips_an_empty_section(qapp) -> None:
    _anchor, popup = _popup(qapp)
    assert [t.key for t in popup.tiles] == [f"a{i}" for i in range(8)] + ["b0"]
    side = int(theme.metric("action-grid.tile"))
    assert all(t.size() == QSize(side, side) for t in popup.tiles)
    assert [t.text() for t in popup._titles] == ["First", "Second"]


def test_a_disabled_tile_keeps_its_tooltip(qapp) -> None:
    _anchor, popup = _popup(qapp, _sections(disabled="a2"))
    tile = popup.tile("a2")
    assert not tile.isEnabled()
    assert tile.toolTip() == "Tip 2"


def test_a_long_caption_is_elided_to_the_tile(qapp) -> None:
    long = GridSection("S", (GridItem("x", "An exceedingly long caption indeed", "aid"),))
    _anchor, popup = _popup(qapp, [long])
    tile = popup.tile("x")
    assert tile.text().endswith("…")
    assert tile.accessibleName() == "An exceedingly long caption indeed"


def test_a_pick_closes_the_popup_then_reports_the_key(qapp) -> None:
    anchor, popup = _popup(qapp)
    popup.show_under(anchor)
    seen: list[tuple[str, bool]] = []
    popup.picked.connect(lambda key: seen.append((key, popup.isVisible())))
    popup.tile("a3").click()
    assert seen == [("a3", False)]


def test_escape_closes_it_and_enter_picks_the_focused_tile(qapp) -> None:
    anchor, popup = _popup(qapp)
    popup.show_under(anchor)
    seen: list[str] = []
    popup.picked.connect(seen.append)
    popup.tile("a1").setFocus()
    QTest.keyClick(popup, Qt.Key.Key_Return)
    assert seen == ["a1"]

    anchor, popup = _popup(qapp)
    popup.show_under(anchor)
    QTest.keyClick(popup, Qt.Key.Key_Escape)
    assert not popup.isVisible()


def test_the_arrow_keys_walk_the_grid_and_skip_disabled_tiles(qapp) -> None:
    anchor, popup = _popup(qapp, _sections(disabled="a1"))
    popup.show_under(anchor)
    columns = int(theme.metric("action-grid.columns"))
    start = popup.tile("a0")
    assert popup._neighbour(start, 1, 0) is popup.tile("a2")  # a1 is greyed out
    assert popup._neighbour(start, -1, 0) is None
    below = popup._neighbour(start, 0, 1)
    assert below is popup.tile(f"a{columns}")
    assert popup._neighbour(below, 0, -1) is start
    # Down from the last row of one section lands in the next.
    last = popup.tile("a7")
    assert popup._neighbour(last, 0, 1) is popup.tile("b0")


def test_the_column_count_follows_the_room(qapp) -> None:
    _anchor, popup = _popup(qapp)
    side = int(theme.metric("action-grid.tile"))
    gap = int(theme.metric("action-grid.gap"))
    columns = int(theme.metric("action-grid.columns"))
    assert popup.grid_width(10_000) == columns * side + (columns - 1) * gap
    assert popup.grid_width(2 * side + gap) == 2 * side + gap
    assert popup.grid_width(1) == side  # never fewer than one
    # A popup with fewer tiles than columns is only as wide as its tiles.
    _anchor, small = _popup(qapp, [GridSection("S", (GridItem("x", "X"), GridItem("y", "Y")))])
    assert small.grid_width(10_000) == 2 * side + gap


def test_it_scrolls_rather_than_outgrow_a_short_screen(qapp) -> None:
    _anchor, popup = _popup(qapp, _sections(count=30))
    popup.fit_to(QRect(0, 0, 2000, 2000))
    tall = popup.height()
    popup.fit_to(QRect(0, 0, 2000, 300))
    assert popup.height() <= 300 < tall


def test_it_opens_on_screen_under_its_anchor(qapp) -> None:
    anchor, popup = _popup(qapp)
    popup.show_under(anchor)
    screen = anchor.screen().availableGeometry()
    assert screen.contains(popup.frameGeometry().topLeft())
    assert popup.parentWidget() is anchor


class _ParentlessShows(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[str] = []

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget):
            if obj.parentWidget() is None and type(obj).__module__.startswith("mm_companion"):
                self.hits.append(type(obj).__name__)
        return False


def test_opening_it_shows_no_parentless_window(qapp) -> None:
    anchor = QWidget()
    anchor.show()
    watch = _ParentlessShows()
    qapp.installEventFilter(watch)
    try:
        ActionGridPopup(_sections(), anchor).show_under(anchor)
    finally:
        qapp.removeEventFilter(watch)
    assert watch.hits == []


def test_every_action_in_the_data_has_its_own_drawing(qapp, data) -> None:
    for action in data.combat.actions:
        resource = action_icon_resource(action.icon)
        assert resource.endswith(f"/{action.icon}.svg"), action.id


def test_an_unknown_or_path_like_icon_key_draws_the_generic_one(qapp) -> None:
    generic = f"{svg_assets.ACTION_ICON_DIR}/{svg_assets.GENERIC_ACTION_ICON}.svg"
    for key in ("no_such_icon", "../D20_classic", "", "a/b", "aid.svg"):
        assert action_icon_resource(key) == generic, key


def test_an_icon_is_painted_in_the_colour_asked(qapp) -> None:
    pixmap = tinted_svg_pixmap(action_icon_resource("power"), QSize(24, 24), 1.0, "#ff0000")
    image = pixmap.toImage()
    inked = [
        QColor(image.pixel(x, y))
        for x in range(image.width())
        for y in range(image.height())
        if QColor.fromRgba(image.pixel(x, y)).alpha() > 200
    ]
    assert inked, "the drawing painted nothing"
    assert all(c.red() > 200 and c.green() < 50 and c.blue() < 50 for c in inked)
