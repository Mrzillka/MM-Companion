"""The simple sheet: a play view that borrows the live blocks and gives them back.

What matters most is what it must *not* lose: every play-time control keeps working
on it (a power's switch, an array's live member, a Dynamic array's share dial, a worn
item, a hero point, a condition, a roll), every change it makes reaches the model
through the same funnel the edit sheet uses, and switching back puts the edit sheet
exactly as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QSpinBox, QWidget

from mm_companion.core import storage
from mm_companion.core.character import AdvantageSelection, Character, Complication
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    ModifierSelection,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules.equipment import build_item_from_entry
from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.cards import DraggableCard
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.sections.powers import _RankDial
from mm_companion.ui.simple.printing import PrintDocument, export_pdf, page_breaks
from mm_companion.ui.simple.sheet import SimpleBox
from mm_companion.ui.simple.style import TermLabel
from mm_companion.ui.simple.views import (
    AbilitiesView,
    ConditionsView,
    SkillsView,
    SystemView,
    skill_rows,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def _hero(data) -> Character:
    """A character with something in every block a player plays from."""
    char = Character.new_default(data)
    char.profile["hero_name"] = "Ghost"
    char.abilities["STR"] = 4
    char.abilities["AGL"] = 3
    char.skill_ranks["Acrobatics"] = 6
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    char.advantages.append(AdvantageSelection(name="Improved Initiative", rank=1))
    char.complications.append(Complication("Motivation", "Doing good."))
    char.characteristics["hero_points"] = 1
    char.equipment.append(build_item_from_entry(data.equipment_catalog()["leather_armor"], data))
    armor = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)])
    field = Power(name="Force Field", effects=[PowerEffectInstance("protection", rank=8)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=3)])
    field.dynamic = flight.dynamic = True
    char.powers.extend([armor, blast, PowerGroup(mode=STRUCTURE_ARRAY, children=[field, flight])])
    return char


def _settle(qapp: QApplication, sheet=None) -> None:
    for _ in range(3):
        qapp.processEvents()
    if sheet is not None:
        sheet.bus.flush()
        qapp.processEvents()


def _simple(qapp, data, char=None, *, locked: bool = True) -> CharacterSheet:
    sheet = CharacterSheet(data, char or _hero(data))
    sheet.set_locked(locked)
    sheet.resize(1100, 900)
    sheet.show()
    _settle(qapp)
    sheet.set_simple(True)
    _settle(qapp, sheet)
    return sheet


def _texts(widget: QWidget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel) if label.isVisibleTo(widget)]


# -- borrowing and giving back ------------------------------------------------------


def test_every_block_is_on_the_standard_sheet(qapp, data) -> None:
    """Every one — bar a portrait never loaded, whose room goes to the name beside it,
    and the Scene, which has nothing to show outside a session."""
    sheet = _simple(qapp, data)

    expected = set(sheet.block_keys()) - {"character_image", "scene"}
    assert sorted(sheet.simple_sheet.shown_keys()) == sorted(expected)


def test_a_loaded_portrait_is_on_the_sheet(qapp, data, tmp_path) -> None:
    from PySide6.QtGui import QColor, QPixmap

    picture = tmp_path / "face.png"
    pixmap = QPixmap(40, 50)
    pixmap.fill(QColor("#884422"))
    pixmap.save(str(picture))
    char = _hero(data)
    char.image_path = str(picture)
    sheet = _simple(qapp, data, char)

    assert "character_image" in sheet.simple_sheet.shown_keys()


def test_the_edit_page_is_hidden_while_the_simple_sheet_is_up(qapp, data) -> None:
    sheet = _simple(qapp, data)

    assert sheet.is_simple
    assert not sheet.board.isVisible()
    assert sheet.simple_sheet.isVisible()


def test_the_power_cards_are_the_live_section_borrowed(qapp, data) -> None:
    sheet = _simple(qapp, data)

    box = sheet.simple_sheet.box("powers")
    assert box is not None and box.body is sheet.powers
    assert sheet.block_frame("powers").is_lent()


def test_switching_back_returns_every_section_to_its_frame(qapp, data) -> None:
    sheet = _simple(qapp, data)
    sheet.set_simple(False)
    _settle(qapp, sheet)

    for key in sheet.block_keys():
        frame = sheet.block_frame(key)
        assert not frame.is_lent()
        assert frame.isAncestorOf(sheet.section(key)), key
    assert sheet.powers._simple is False
    assert sheet.board.isVisible()


def test_it_can_go_back_and_forth(qapp, data) -> None:
    sheet = _simple(qapp, data)
    for _ in range(3):
        sheet.set_simple(False)
        _settle(qapp, sheet)
        sheet.set_simple(True)
        _settle(qapp, sheet)

    assert sheet.simple_sheet.box("equipment").body is sheet.equipment


def test_the_simple_sheet_is_locked_and_the_lock_comes_back(qapp, data) -> None:
    sheet = _simple(qapp, data, locked=False)
    assert sheet.locked is True

    sheet.set_simple(False)
    assert sheet.locked is False


def test_nothing_it_does_to_switch_marks_the_sheet_edited(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    edits: list[int] = []
    sheet.edited.connect(lambda: edits.append(1))

    sheet.set_simple(True)
    _settle(qapp, sheet)
    sheet.set_simple(False)
    _settle(qapp, sheet)

    assert edits == []


# -- what the views leave out --------------------------------------------------------


def test_no_rank_is_an_input_on_the_simple_sheet(qapp, data) -> None:
    sheet = _simple(qapp, data)
    for key in ("abilities", "resistances", "skills", "system_info"):
        view = sheet.simple_sheet.view(key)
        assert view.findChildren(QSpinBox) == [], key


def test_no_point_cost_is_shown(qapp, data) -> None:
    sheet = _simple(qapp, data)
    page = sheet.simple_sheet.page

    assert not any(text.endswith(" PP") for text in _texts(page))
    assert not any(text.endswith(" EP") for text in _texts(page))


def test_the_abilities_show_their_effective_totals(qapp, data) -> None:
    sheet = _simple(qapp, data)
    view = sheet.simple_sheet.view("abilities")

    assert isinstance(view, AbilitiesView)
    assert view.boxes["STR"].value_text() == "4"


def test_only_trained_skills_are_listed_and_the_rest_are_small_print(qapp, data) -> None:
    sheet = _simple(qapp, data)
    trained, untrained = skill_rows(sheet.simple_sheet.context_for("skills"))

    assert ("Acrobatics", "Acrobatics") in trained
    assert all(row_id != "Acrobatics" for row_id, _ in untrained)
    view = sheet.simple_sheet.view("skills")
    assert isinstance(view, SkillsView)
    assert len(view.grid.items()) == len(trained)


def test_game_terms_are_small_print(qapp, data) -> None:
    """A power's Type / Range / Action / Duration read as one translucent italic line."""
    sheet = _simple(qapp, data)
    terms = [label for label in sheet.powers.findChildren(TermLabel) if "Instant" in label.text()]

    assert terms
    term = terms[0]
    assert term.font().italic()
    assert term.palette().color(QPalette.ColorRole.WindowText).alphaF() < 1.0


def test_a_blast_shows_its_attack_and_its_dc_as_big_numbers(qapp, data) -> None:
    sheet = _simple(qapp, data)
    texts = _texts(sheet.powers)

    assert any(text.startswith("DC ") for text in texts)
    assert any(text.startswith("+") for text in texts)


# -- play-time controls stay live --------------------------------------------------------


def test_a_power_still_switches_off_and_on(qapp, data) -> None:
    char = _hero(data)
    sheet = _simple(qapp, data, char)
    armor = char.powers[0]
    cards = [c for c in sheet.powers.findChildren(DraggableCard) if c.node_id == armor.id]

    assert cards and cards[0].is_clickable()
    cards[0].clicked.emit()
    assert sheet.powers._power_is_active(armor) is False


def test_an_array_member_can_be_made_the_live_one(qapp, data) -> None:
    char = _hero(data)
    sheet = _simple(qapp, data, char)
    group = char.powers[2]
    field, flight = group.children

    sheet.powers._on_card_clicked(flight, group, "select")
    assert group.active_child_id == flight.id


def test_a_dynamic_arrays_points_can_still_be_shared_out(qapp, data) -> None:
    char = _hero(data)
    sheet = _simple(qapp, data, char)
    group = char.powers[2]
    group.children[0].dynamic_points = 4
    sheet.powers._rebuild_list()
    _settle(qapp, sheet)
    dials = [
        dial
        for dial in sheet.powers.findChildren(_RankDial)
        if any("PP" in text for text in dial._labels.values())
    ]
    assert dials

    dial = dials[0]
    dial._slider.setValue(dial._slider.minimum())
    assert group.children[0].dynamic_points in (None, 0)


def test_an_item_can_still_be_worn_and_stowed(qapp, data) -> None:
    char = _hero(data)
    sheet = _simple(qapp, data, char)
    item = char.equipment[0]
    worn = item.worn
    cards = [c for c in sheet.equipment.findChildren(DraggableCard) if c.node_id == item.id]

    cards[0].clicked.emit()
    assert item.worn is (not worn)


def test_a_hero_point_spends_through_the_system_blocks_funnel(qapp, data) -> None:
    sheet = _simple(qapp, data)
    notes: list[str] = []
    sheet.system_info.noteRequested.connect(notes.append)
    view = sheet.simple_sheet.view("system_info")
    assert isinstance(view, SystemView)

    view.hero_points._on_click(1)

    assert sheet.character.characteristics["hero_points"] == 2
    assert sheet.system_info._hero_points.value() == 2
    assert notes, "a moved hero point writes its note into the roll history"


def test_a_failed_toughness_save_lands_its_rung(qapp, data) -> None:
    sheet = _simple(qapp, data)
    view = sheet.simple_sheet.view("conditions")
    assert isinstance(view, ConditionsView)

    view.damage.stepChosen.emit(2)
    _settle(qapp, sheet)

    assert sheet.character.conditions
    assert view._chips.count() == len(sheet.character.conditions)


def test_a_double_click_on_a_stat_box_rolls_it(qapp, data) -> None:
    sheet = _simple(qapp, data)
    rolled = []
    sheet.abilities.rollRequested.connect(rolled.append)
    box = sheet.simple_sheet.view("abilities").boxes["STR"]

    QTest.mouseDClick(box, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))

    assert rolled and rolled[0].modifier == 4


def test_a_roll_from_the_simple_sheet_lands_in_the_borrowed_roller(qapp, data) -> None:
    """The roller on the page is the Dice block itself, so the bus reaches it as ever."""
    sheet = _simple(qapp, data)
    assert sheet.simple_sheet.box("dice").body is sheet.dice
    box = sheet.simple_sheet.view("abilities").boxes["STR"]

    QTest.mouseDClick(box, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))

    assert "Strength" in sheet.dice.panel._readout.text()


def test_the_views_follow_an_edit_made_elsewhere(qapp, data) -> None:
    """A GM's command, an undo — anything that moves the model — reaches the page."""
    sheet = _simple(qapp, data)
    sheet.system_info.set_hero_points(4)
    _settle(qapp, sheet)

    assert sheet.simple_sheet.view("system_info").hero_points.value() == 4


def test_undo_reaches_the_simple_sheet(qapp, data) -> None:
    win = MainWindow(character=_hero(data), locked=True)
    win.show()
    _settle(qapp)
    win._simple_bar_action.trigger()
    _settle(qapp, win.sheet)
    view = win.sheet.simple_sheet.view("system_info")

    view.hero_points._on_click(1)
    win.sheet.undo.flush()
    assert win.sheet.character.characteristics["hero_points"] == 2
    win._router.undo()
    _settle(qapp, win.sheet)

    assert win.sheet.character.characteristics["hero_points"] == 1
    assert win.sheet.simple_sheet.view("system_info").hero_points.value() == 1


# -- presets -------------------------------------------------------------------------


def test_custom_follows_the_edit_sheets_arrangement(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    sheet.hide_block("complications")
    sheet.set_simple_preset("custom")
    sheet.set_simple(True)
    _settle(qapp, sheet)

    assert "complications" not in sheet.simple_sheet.shown_keys()
    page = sheet.arrangement()["page"]
    assert (
        lt.keys(sheet.simple_sheet.layout_model().page)[0]
        == lt.keys(lt.from_dict(page, set(sheet.block_keys())))[0]
    )


def test_standard_shows_a_block_the_edit_sheet_has_closed(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    sheet.hide_block("complications")
    sheet.set_simple(True)
    _settle(qapp, sheet)

    assert "complications" in sheet.simple_sheet.shown_keys()


def test_switching_preset_while_up_rebuilds_and_still_gives_back(qapp, data) -> None:
    sheet = _simple(qapp, data)
    sheet.set_simple_preset("custom")
    _settle(qapp, sheet)
    sheet.set_simple(False)
    _settle(qapp, sheet)

    assert all(not sheet.block_frame(key).is_lent() for key in sheet.block_keys())


# -- the window ----------------------------------------------------------------------


def test_the_bar_switch_stands_the_lock_down(qapp, data) -> None:
    win = MainWindow(character=_hero(data), locked=True)
    win.show()
    _settle(qapp)

    win._simple_bar_action.trigger()
    _settle(qapp, win.sheet)
    assert win.sheet.is_simple
    assert not win._lock_action.isEnabled()
    assert win._simple_action.isChecked()

    win._simple_bar_action.trigger()
    _settle(qapp, win.sheet)
    assert not win.sheet.is_simple
    assert win._lock_action.isEnabled()


def test_the_preset_is_remembered(qapp, data) -> None:
    win = MainWindow(character=_hero(data), locked=True)
    win._preset_actions["custom"].trigger()

    assert storage.simple_sheet_preset() == "custom"
    again = MainWindow(character=_hero(data), locked=True)
    assert again.sheet.simple_preset == "custom"
    assert again._preset_actions["custom"].isChecked()


def test_a_floated_block_window_leaves_the_screen_and_comes_back(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    sheet.float_block("complications")
    _settle(qapp)
    window = sheet.canvas.block_window("complications")
    assert window is not None and window.isVisible()

    sheet.set_simple(True)
    _settle(qapp, sheet)
    assert not window.isVisible()
    sheet.set_simple(False)
    _settle(qapp, sheet)
    assert window.isVisible()


def test_an_npc_has_no_hero_points_and_an_estimated_level(qapp, data) -> None:
    win = NPCWindow(character=_hero(data))
    win.show()
    _settle(qapp)
    win.sheet.set_simple(True)
    _settle(qapp, win.sheet)
    view = win.sheet.simple_sheet.view("system_info")

    assert not view._hero_row.isVisibleTo(view)
    assert view.level.value_text().startswith("~")


class _ParentlessShows(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[str] = []

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget):
            if obj.parentWidget() is None and not obj.isWindow():
                self.hits.append(type(obj).__name__)
            elif obj.parentWidget() is None and type(obj).__name__ not in (
                "CharacterSheet",
                "QMenu",
            ):
                self.hits.append(type(obj).__name__)
        return False


def test_switching_flashes_no_window(qapp, data) -> None:
    """Borrowing a section hides it before it leaves its frame — see ``lend_section``."""
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    watch = _ParentlessShows()
    qapp.installEventFilter(watch)
    try:
        sheet.set_simple(True)
        _settle(qapp, sheet)
        sheet.set_simple(False)
        _settle(qapp, sheet)
    finally:
        qapp.removeEventFilter(watch)

    assert watch.hits == []


# -- printing ------------------------------------------------------------------------


def test_a_pdf_can_be_written(qapp, data, tmp_path) -> None:
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    path = tmp_path / "ghost.pdf"

    result = export_pdf(sheet, path)

    assert result.pages >= 1
    assert path.read_bytes().startswith(b"%PDF")


def test_printing_leaves_the_sheet_alone(qapp, data, tmp_path) -> None:
    """The print is a copy: nothing on screen is borrowed, moved or re-dressed for it."""
    sheet = _simple(qapp, data)
    width = sheet.powers.width()

    export_pdf(sheet, tmp_path / "ghost.pdf")
    _settle(qapp, sheet)

    assert sheet.simple_sheet.box("powers").body is sheet.powers
    assert sheet.powers.width() == width


def test_the_print_leaves_out_the_roller(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    document = PrintDocument(sheet)
    try:
        assert "dice" not in document.boxes
        assert "scene" not in document.boxes
        assert "powers" in document.boxes
    finally:
        document.close()


@pytest.fixture
def dark(qapp):
    """Slate Dark for one test — installing a palette *and* an application stylesheet,
    which is the combination that stops Qt passing a parent's palette down."""
    theme.set_active_theme("slate-dark", qapp)
    yield
    theme.set_active_theme("classic", qapp)


def test_the_print_is_black_on_white_whatever_the_theme(qapp, data, dark) -> None:
    """A dark preset printed as it looks is a page of ink; every widget gets paper."""
    sheet = CharacterSheet(data, _hero(data))
    document = PrintDocument(sheet)
    try:
        document.lay_out()
        labels = [
            label
            for label in document.root.findChildren(QLabel)
            if label.isVisibleTo(document.root) and label.isEnabled()
        ]
        assert labels
        for label in labels:
            colour = label.palette().color(QPalette.ColorRole.WindowText)
            assert colour.lightness() < 60, label.text()
        terms = [t for t in document.root.findChildren(TermLabel) if t.isVisibleTo(document.root)]
        assert terms
        assert all(t.palette().color(QPalette.ColorRole.WindowText).alphaF() < 1 for t in terms)
    finally:
        document.close()


def test_a_page_never_ends_inside_a_line(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    document = PrintDocument(sheet)
    try:
        height = document.lay_out()
        bands = page_breaks(document.page, height, 300)
        assert bands[0][0] == 0 and bands[-1][1] == height
        for (_, end), (start, _) in zip(bands, bands[1:], strict=False):
            assert end == start
        cuts = [end for _, end in bands[:-1]]
        for label in document.page.findChildren(QLabel):
            if not label.isVisibleTo(document.page) or label.height() > 300:
                continue
            top = label.mapTo(document.page, QPoint(0, 0)).y()
            for cut in cuts:
                assert not top < cut < top + label.height(), label.text()
    finally:
        document.close()


def test_the_heading_of_a_box_is_never_the_last_thing_on_a_page(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    document = PrintDocument(sheet)
    try:
        height = document.lay_out()
        cuts = [end for _, end in page_breaks(document.page, height, 400)[:-1]]
        for box in document.page.findChildren(SimpleBox):
            if box.heading is None:
                continue
            below = box.heading.mapTo(document.page, QPoint(0, 0)).y() + box.heading.height()
            for cut in cuts:
                assert not below <= cut <= below + box.heading.height(), box.key
    finally:
        document.close()


def test_compact_mode_borrows_the_roller_and_gives_it_back_to_the_simple_sheet(qapp, data) -> None:
    win = MainWindow(character=_hero(data), locked=True)
    win.resize(1100, 800)
    win.show()
    _settle(qapp)
    win._simple_bar_action.trigger()
    _settle(qapp, win.sheet)
    sheet = win.sheet

    win._compact.enter()
    _settle(qapp)
    assert win._compact.page.isAncestorOf(sheet.dice.panel)
    win._compact.leave()
    _settle(qapp)

    assert sheet.dice.isAncestorOf(sheet.dice.panel)
    assert sheet.simple_sheet.box("dice").isAncestorOf(sheet.dice)
    assert sheet.simple_sheet.isVisible() and not sheet.board.isVisible()


def test_a_row_stacks_its_boxes_when_they_would_be_slivers(qapp, data) -> None:
    from mm_companion.ui.simple.sheet import SimpleRow

    sheet = _simple(qapp, data)
    top = sheet.simple_sheet.box("base_info").parentWidget()
    assert isinstance(top, SimpleRow)
    assert top.is_row

    sheet.resize(520, 900)
    _settle(qapp, sheet)
    assert not top.is_row

    sheet.resize(1400, 900)
    _settle(qapp, sheet)
    assert top.is_row


def test_the_speed_line_flips_its_units(qapp, data) -> None:
    sheet = _simple(qapp, data)
    view = sheet.simple_sheet.view("system_info")
    before = view.speed.text()

    view.speed.clicked.emit()

    assert view.speed.text() != before
    assert "km" in view.speed.text()


def test_the_custom_preset_settles_rather_than_rebuilding_itself(qapp, data) -> None:
    """Custom reads the canvas's arrangement and rebuilds when it changes; reading it
    must not count as a change, or the page would rebuild itself every turn."""
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    sheet.set_simple_preset("custom")
    sheet.set_simple(True)
    _settle(qapp, sheet)
    rebuilds: list[int] = []
    sheet.simple_sheet.rebuilt.connect(lambda: rebuilds.append(1))

    for _ in range(10):
        qapp.processEvents()

    assert rebuilds == []


def test_reopening_a_closed_block_reaches_the_custom_preset(qapp, data) -> None:
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    sheet.hide_block("complications")
    sheet.set_simple_preset("custom")
    sheet.set_simple(True)
    _settle(qapp, sheet)
    assert "complications" not in sheet.simple_sheet.shown_keys()

    sheet.show_block("complications")
    _settle(qapp, sheet)

    assert "complications" in sheet.simple_sheet.shown_keys()


def test_a_block_with_nothing_in_it_gives_its_room_away(qapp, data) -> None:
    """No powers, no gear, no advantages: no box saying "none" on the sheet or on paper.

    The Conditions box stays — it is where a condition goes on — and so does the live
    Notes block, which is where a player opens one.
    """
    sheet = _simple(qapp, data, Character.new_default(data))
    shown = sheet.simple_sheet.shown_keys()

    for key in ("powers", "equipment", "advantages", "complications"):
        assert key not in shown, key
    assert "conditions" in shown and "notes" in shown
    assert not sheet.block_frame("powers").is_lent()

    document = PrintDocument(sheet)
    try:
        assert "powers" not in document.boxes
        assert "notes" not in document.boxes
    finally:
        document.close()


def test_a_note_can_be_written_on_the_simple_sheet(qapp, data) -> None:
    """The one thing a player writes during play: the lock is off here, so notes are too."""
    from mm_companion.core import notes as store

    ref = store.create_note("Session")
    sheet = CharacterSheet(data, _hero(data))
    sheet.show()
    _settle(qapp)
    sheet.notes.open_note(ref)
    sheet.set_locked(True)
    editor = sheet.notes._open[0].editor
    assert editor.source.isReadOnly()

    sheet.set_simple(True)
    _settle(qapp, sheet)
    assert not editor.source.isReadOnly()

    sheet.set_simple(False)
    _settle(qapp, sheet)
    assert editor.source.isReadOnly()


def _real_double_click(qapp, widget) -> None:
    """What a real double-click delivers: press, release, double-click, release.

    ``QTest.mouseDClick`` on this platform sends the double-click alone, which is
    exactly the sequence that hid this bug.
    """
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    local = QPointF(5, 5)
    button, no_mods = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    for kind, held in (
        (QEvent.Type.MouseButtonPress, button),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
        (QEvent.Type.MouseButtonDblClick, button),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        global_pos = QPointF(widget.mapToGlobal(local.toPoint()))
        event = QMouseEvent(kind, local, global_pos, button, held, no_mods)
        QApplication.sendEvent(widget, event)
        qapp.processEvents()


@pytest.fixture
def instant_die(monkeypatch):
    from mm_companion.ui import dice_roller

    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 11)


def test_a_double_click_rolls_and_leaves_the_chip_empty(qapp, data, instant_die) -> None:
    """The roll lands on the double-click, and the release that ends the gesture must
    not load the chip straight back — the roller was left armed with every stat a
    player double-clicked on the simple sheet."""
    sheet = _simple(qapp, data)
    box = sheet.simple_sheet.view("abilities").boxes["STR"]
    rolls: list = []
    sheet.abilities.rollRequested.connect(rolls.append)

    _real_double_click(qapp, box)
    _settle(qapp)

    assert len(rolls) == 1
    assert "Strength" in sheet.dice.panel._readout.text()
    assert sheet.dice.panel.current_spec() is None


def test_a_single_click_still_loads_the_chip(qapp, data) -> None:
    sheet = _simple(qapp, data)
    box = sheet.simple_sheet.view("abilities").boxes["STR"]

    QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    _settle(qapp)

    spec = sheet.dice.panel.current_spec()
    assert spec is not None and spec.label == "Strength"


def test_the_edit_sheets_initiative_is_cured_too(qapp, data, instant_die) -> None:
    """The edit sheet's Initiative readout goes through the same click helper."""
    sheet = CharacterSheet(data, _hero(data))
    sheet.set_locked(True)
    sheet.show()
    _settle(qapp)

    _real_double_click(qapp, sheet.system_info._initiative)
    _settle(qapp)

    assert "Initiative" in sheet.dice.panel._readout.text()
    assert sheet.dice.panel.current_spec() is None


def test_a_gm_can_pin_from_the_simple_sheet(qapp, data) -> None:
    """A sheet opened from a GM card offers the same right-click the edit rows do."""
    from mm_companion.core.rules import PIN_ABILITY, PIN_INITIATIVE, PIN_SKILL, PinRef
    from mm_companion.ui.simple.widgets import pin_menu

    sheet = _simple(qapp, data)
    abilities = sheet.simple_sheet.view("abilities")
    assert pin_menu(abilities.boxes["STR"], abilities.context) is None  # no card, no menu

    sheet.set_pin_target(True)
    pinned: list = []
    for section in (sheet.abilities, sheet.skills, sheet.system_info):
        section.pinRequested.connect(pinned.append)
    skills = sheet.simple_sheet.view("skills")
    system = sheet.simple_sheet.view("system_info")
    for widget, view in (
        (abilities.boxes["STR"], abilities),
        (skills.grid.items()[0], skills),
        (system.initiative, system),
    ):
        menu = pin_menu(widget, view.context)
        assert menu is not None
        menu.actions()[0].trigger()

    assert pinned[0] == PinRef(PIN_ABILITY, "STR")
    assert pinned[1] == PinRef(PIN_SKILL, "Acrobatics")
    assert pinned[2] == PinRef(PIN_INITIATIVE)


def test_an_already_pinned_row_offers_unpin(qapp, data) -> None:
    from mm_companion.core.rules import PIN_ABILITY, PinRef
    from mm_companion.ui.simple.widgets import pin_menu

    sheet = _simple(qapp, data)
    sheet.set_pin_target(True)
    sheet.set_pinned([PinRef(PIN_ABILITY, "STR")])
    unpinned: list = []
    sheet.abilities.unpinRequested.connect(unpinned.append)
    view = sheet.simple_sheet.view("abilities")

    pin_menu(view.boxes["STR"], view.context).actions()[0].trigger()

    assert unpinned == [PinRef(PIN_ABILITY, "STR")]


def test_the_scene_is_left_off_outside_a_session(qapp, data) -> None:
    sheet = _simple(qapp, data)

    assert "scene" not in sheet.simple_sheet.shown_keys()
    assert "dice" in sheet.simple_sheet.shown_keys()
    assert not sheet.block_frame("scene").is_lent()


def test_the_scene_comes_onto_the_page_when_a_session_starts(qapp, data) -> None:
    from mm_companion.core.session.model import new_session
    from mm_companion.ui.session_bridge import SessionBridge, set_active_session

    sheet = _simple(qapp, data)
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    try:
        sheet.sync_session()
        _settle(qapp, sheet)
        assert "scene" in sheet.simple_sheet.shown_keys()
    finally:
        set_active_session(None)
        bridge.stop()
    sheet.sync_session()
    _settle(qapp, sheet)
    assert "scene" not in sheet.simple_sheet.shown_keys()


def test_a_theme_switch_redraws_the_simple_sheet(qapp, data) -> None:
    sheet = _simple(qapp, data)
    before = sheet.simple_sheet.box("abilities").heading.styleSheet()
    try:
        theme.set_active_theme("crimson-gold", qapp)
        _settle(qapp, sheet)
        after = sheet.simple_sheet.box("abilities").heading.styleSheet()
    finally:
        theme.set_active_theme("classic", qapp)
        _settle(qapp, sheet)

    assert after != before
    assert theme.color("accent") not in after  # crimson's accent, read at redraw


def test_the_simple_sheet_does_not_redraw_itself_while_idle(qapp, data) -> None:
    sheet = _simple(qapp, data)
    rebuilds: list[int] = []
    sheet.simple_sheet.rebuilt.connect(lambda: rebuilds.append(1))

    for _ in range(10):
        qapp.processEvents()

    assert rebuilds == []


def test_an_npcs_standard_sheet_leaves_off_what_an_npc_opens_without(qapp, data) -> None:
    win = NPCWindow(character=_hero(data))
    win.show()
    _settle(qapp)
    win.sheet.set_simple(True)
    _settle(qapp, win.sheet)
    shown = win.sheet.simple_sheet.shown_keys()

    assert "dice" not in shown and "complications" not in shown
    assert "powers" in shown


def test_the_print_choice_defaults_to_what_belongs_on_paper(qapp, data) -> None:
    from mm_companion.ui.simple.print_dialog import PrintChoiceDialog

    sheet = CharacterSheet(data, _hero(data))
    dialog = PrintChoiceDialog(sheet)
    keys = dialog.keys()

    assert "powers" in keys and "notes" in keys
    assert "dice" not in keys


def test_the_print_choice_is_remembered_and_honoured(qapp, data) -> None:
    from mm_companion.ui.simple.print_dialog import PrintChoiceDialog

    sheet = CharacterSheet(data, _hero(data))
    dialog = PrintChoiceDialog(sheet)
    dialog._boxes["skills"].setChecked(False)
    dialog._boxes["dice"].setChecked(True)
    dialog.accept()

    assert storage.simple_print_choices() == {
        **{key: key in dialog.keys() for key in dialog._boxes},
    }
    document = PrintDocument(sheet)
    try:
        assert "skills" not in document.boxes
        assert "dice" in document.boxes
    finally:
        document.close()
    again = PrintChoiceDialog(sheet)
    assert not again._boxes["skills"].isChecked() and again._boxes["dice"].isChecked()


def test_nothing_ticked_is_nothing_to_print(qapp, data) -> None:
    from PySide6.QtWidgets import QDialogButtonBox

    from mm_companion.ui.simple.print_dialog import PrintChoiceDialog

    dialog = PrintChoiceDialog(CharacterSheet(data, _hero(data)))
    dialog._set_all(False)

    assert not dialog._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_an_export_prints_only_the_blocks_it_was_given(qapp, data, tmp_path) -> None:
    sheet = CharacterSheet(data, _hero(data))
    document = PrintDocument(sheet, keys=["base_info", "abilities"])
    try:
        assert set(document.boxes) == {"base_info", "abilities"}
    finally:
        document.close()
    assert export_pdf(sheet, tmp_path / "two.pdf", ["base_info", "abilities"]).pages == 1


def test_a_pdfs_text_is_real_text(qapp, data, tmp_path) -> None:
    """Searchable and selectable: the words go onto the page as text, not as pixels."""
    from PySide6.QtPdf import QPdfDocument

    sheet = CharacterSheet(data, _hero(data))
    path = tmp_path / "ghost.pdf"
    export_pdf(sheet, path)
    document = QPdfDocument(None)
    document.load(str(path))
    text = " ".join(document.getAllText(page).text() for page in range(document.pageCount()))

    for words in ("Ghost", "Acrobatics", "Armor", "Motivation"):
        assert words in text, words


def test_the_picture_under_the_text_has_no_words_in_it(qapp, data) -> None:
    """Drawn twice — once as pixels, once as text — would print every word doubled."""
    from PySide6.QtGui import QColor

    document = PrintDocument(CharacterSheet(data, _hero(data)))
    try:
        document.lay_out()
        name = next(label for label in document.text_labels() if QLabel.text(label) == "Ghost")
        top = name.mapTo(document.page, QPoint(0, 0)).y()
        band = document.band_image(top, top + name.height(), 96, with_text=False)
        left = name.mapTo(document.page, QPoint(0, 0)).x()
        inked = [
            (x, y)
            for x in range(left, left + name.width())
            for y in range(name.height())
            if band.pixelColor(x, y).lightness() < 128
        ]
        assert inked == []
        assert band.pixelColor(0, 0) != QColor()  # it is still a real picture
    finally:
        document.close()


def test_a_stylesheet_colour_reaches_the_printed_text() -> None:
    from PySide6.QtGui import QColor

    from mm_companion.ui.simple.printing import _sheet_text_style

    label = QLabel("Heading")
    label.setStyleSheet("color: #3366aa; font-weight: bold;")
    colour, bold, italic = _sheet_text_style(label)
    assert colour == QColor("#3366aa") and bold and not italic

    label.setStyleSheet("background-color: #ff0000; font-style: italic;")
    colour, bold, italic = _sheet_text_style(label)
    assert colour is None and italic  # a background is not the text's colour

    label.setStyleSheet("color: rgba(10, 20, 30, 0.5);")
    colour, _bold, _italic = _sheet_text_style(label)
    assert colour.red() == 10 and abs(colour.alphaF() - 0.5) < 0.01


def test_the_print_choice_names_blocks_as_they_are_titled(qapp, data) -> None:
    from mm_companion.ui.simple.print_dialog import PrintChoiceDialog

    dialog = PrintChoiceDialog(CharacterSheet(data, _hero(data)))
    box = dialog._boxes["base_info"]

    assert box.text().replace("&&", "&") == "Name & Details"
    assert "&&" in box.text()  # not a shortcut marker eating the ampersand


# -- review fixes -----------------------------------------------------------------


def test_opening_straight_into_the_simple_sheet_keeps_the_saved_layout(qapp, data) -> None:
    """A sheet whose page was never laid out must not save its dividers' slivers.

    Opened into the simple sheet (Settings > General), the edit page is hidden before
    it is ever shown, and its splitters report sizes squeezed into their default
    geometry. Saved as they read, a first row of 100 : 600 : 122 came back 24 : 32 : 24.
    """
    import json

    first = CharacterSheet(data, _hero(data))
    model = first.arrangement()
    page = lt.from_dict(model["page"], set(first.canvas.block_keys()))
    row = lt.at(page, (0,))
    sizes = [100, 600, 122][: len(row.children)]
    model["page"] = lt.to_dict(lt.set_sizes(page, (0,), sizes))

    sheet = CharacterSheet(data, _hero(data))
    assert sheet.restore_layout(json.dumps(model))
    sheet.set_locked(True)
    sheet.set_simple(True)  # before the window is ever shown, as MainWindow does
    sheet.resize(1100, 900)
    sheet.show()
    _settle(qapp, sheet)

    saved = json.loads(sheet.save_layout())
    assert lt.at(lt.from_dict(saved["page"], set(sheet.canvas.block_keys())), (0,)).sizes == (
        tuple(sizes)
    )


def test_a_divider_dragged_before_switching_is_kept(qapp, data) -> None:
    """On the way into the simple sheet the page's live sizes are taken in once."""
    sheet = CharacterSheet(data, _hero(data))
    sheet.resize(1100, 900)
    sheet.show()
    _settle(qapp, sheet)
    splitter = sheet.canvas._row_widgets[0]
    total = sum(splitter.sizes())
    wanted = [total // 2] + [
        (total - total // 2) // (splitter.count() - 1) for _ in range(splitter.count() - 1)
    ]
    splitter.setSizes(wanted)
    moved = splitter.sizes()

    sheet.set_simple(True)
    _settle(qapp, sheet)
    page = lt.as_page(lt.from_dict(sheet.arrangement()["page"], set(sheet.canvas.block_keys())))

    assert list(lt.at(page, (0,)).sizes) == moved


def test_switching_rebuilds_the_power_cards_once_each_way(qapp, data, monkeypatch) -> None:
    """Locking and dressing each rebuild every card; a switch holds them into one."""
    from mm_companion.ui.sections import powers as powers_module

    real = powers_module.rebuilding
    count = {"n": 0}

    def counting(widget):
        if isinstance(widget, powers_module.PowersSection):
            count["n"] += 1
        return real(widget)

    sheet = CharacterSheet(data, _hero(data))
    sheet.set_locked(False)
    sheet.resize(1100, 900)
    sheet.show()
    _settle(qapp, sheet)
    monkeypatch.setattr(powers_module, "rebuilding", counting)

    sheet.set_simple(True)
    assert count["n"] == 1
    count["n"] = 0
    sheet.set_simple(False)
    assert count["n"] == 1


def test_a_pdf_that_cannot_be_written_says_so(qapp, data, tmp_path) -> None:
    """A folder that does not exist stands in for a file another program has locked."""
    result = export_pdf(CharacterSheet(data, _hero(data)), tmp_path / "missing" / "x.pdf")

    assert result.failed


def test_a_written_pdf_is_not_a_failure(qapp, data, tmp_path) -> None:
    result = export_pdf(CharacterSheet(data, _hero(data)), tmp_path / "ok.pdf")

    assert not result.failed and result.pages >= 1


def test_a_print_started_inside_a_print_is_refused(qapp, data, tmp_path, monkeypatch) -> None:
    """Laying the copy out lets queued events run; a second print among them must not
    start on a printer the first is still painting."""
    from mm_companion.ui.simple import printing

    sheet = CharacterSheet(data, _hero(data))
    nested = []
    real_lay_out = PrintDocument.lay_out

    def lay_out(self):
        nested.append(printing.export_pdf(sheet, tmp_path / "inner.pdf"))
        return real_lay_out(self)

    monkeypatch.setattr(PrintDocument, "lay_out", lay_out)
    outer = printing.export_pdf(sheet, tmp_path / "outer.pdf")

    assert not outer.failed
    assert nested and nested[0].failed
    assert not printing._painting


def test_two_notes_blocks_remember_their_own_print_choice(qapp, data) -> None:
    from mm_companion.ui.simple.print_dialog import PrintChoiceDialog

    sheet = CharacterSheet(data, _hero(data))
    second = sheet.add_block_instance("notes")
    dialog = PrintChoiceDialog(sheet)
    dialog._boxes["notes"].setChecked(True)
    dialog._boxes[second].setChecked(False)
    dialog.accept()

    again = PrintChoiceDialog(sheet)
    assert again._boxes["notes"].isChecked()
    assert not again._boxes[second].isChecked()


def test_a_third_notes_block_follows_the_choice_for_notes(qapp, data) -> None:
    from mm_companion.ui.simple.printing import prints_by_default

    storage.set_simple_print_choices({"notes": False})

    assert not prints_by_default("notes#3")


class _BareConditions(QObject):
    """A mod's Conditions block that offers none of our funnels."""


def test_a_conditions_view_over_a_section_without_the_funnels_still_draws(qapp, data) -> None:
    from mm_companion.core.character import AppliedCondition
    from mm_companion.ui.simple.registry import SimpleContext

    char = _hero(data)
    char.conditions.append(AppliedCondition("confused"))
    view = ConditionsView(SimpleContext(data, char, _BareConditions(), key="conditions"))

    assert "Confused" in " ".join(_texts(view))
    from PySide6.QtWidgets import QToolButton

    assert not [b for b in view.findChildren(QToolButton) if b.text() == "🎲"]
    view._on_damage(1)  # no apply_damage_step: nothing happens, nothing raises


def test_the_conditions_view_skips_a_redraw_that_changes_nothing(qapp, data) -> None:
    from mm_companion.core.character import AppliedCondition

    char = _hero(data)
    char.conditions.append(AppliedCondition("dazed"))
    sheet = _simple(qapp, data, char)
    view = next(iter(sheet.simple_sheet.findChildren(ConditionsView)))
    chips = view._chips_host.findChildren(QWidget, "simpleCondition")

    view.refresh()
    assert view._chips_host.findChildren(QWidget, "simpleCondition") == chips

    char.conditions.append(AppliedCondition("prone"))
    view.refresh()
    assert len(view._chips_host.findChildren(QWidget, "simpleCondition")) == 2


def test_the_print_colours_are_theme_tokens(qapp) -> None:
    from mm_companion.ui.simple.printing import paper_palette

    palette = paper_palette()
    assert palette.color(QPalette.ColorRole.Window).name() == theme.color("paper.background")
    assert palette.color(QPalette.ColorRole.WindowText).name() == theme.color("paper.ink")
