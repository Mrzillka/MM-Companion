"""The Notes block: tabs, autosave, and the multi-instance machinery.

The merge and split gestures are driven through the canvas's own seams rather
than through synthetic mouse events — the same bargain
``tests/test_block_canvas.py`` strikes, and for the same reason: the arrangement
model is what has to be right, and a real drag adds nothing but flakiness.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core import notes
from mm_companion.core.character import Character, NotesState
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.notes.events import note_events


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_sheet(qapp: QApplication):
    """Laid-out sheets with no on-screen window (see tests/test_block_canvas.py)."""
    sheets: list[CharacterSheet] = []

    def _make(character: Character | None = None) -> CharacterSheet:
        sheet = CharacterSheet(load_game_data(), character)
        sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        sheet.resize(1000, 860)
        sheet.show()
        pump()
        sheets.append(sheet)
        return sheet

    yield _make
    for sheet in sheets:
        sheet.close()
        sheet.deleteLater()


@pytest.fixture
def two_notes():
    return notes.create_note("Origin"), notes.create_note("Session Log")


# -- tabs and the model --------------------------------------------------------


def test_opening_a_note_records_it_on_the_character(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()

    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)

    assert sheet.notes.open_refs() == (origin, log)
    assert sheet.character.notes["notes"] == NotesState(files=[origin, log], active=log)


def test_opening_a_tab_is_an_edit_but_typing_in_one_is_not(make_sheet, two_notes) -> None:
    # The whole split: which notes are open is character state, what is written
    # in them is not (it autosaves to its own file and is not undoable).
    origin, _log = two_notes
    sheet = make_sheet()
    edits: list[int] = []
    sheet.edited.connect(lambda: edits.append(1))

    sheet.notes.open_note(origin)
    assert len(edits) == 1

    sheet.notes._open[0].editor.source.setPlainText("# Origin\n\ntyped")
    sheet.notes.flush()
    assert len(edits) == 1


def test_a_note_already_open_is_not_opened_twice(make_sheet, two_notes) -> None:
    origin, _log = two_notes
    sheet = make_sheet()

    sheet.notes.open_note(origin)
    sheet.notes.open_note(origin)

    assert sheet.notes.open_refs() == (origin,)


def test_closing_a_tab_leaves_the_file_alone(make_sheet, two_notes) -> None:
    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)

    sheet.notes._close_ref(origin)

    assert sheet.notes.open_refs() == ()
    assert sheet.character.notes["notes"].files == []
    assert notes.read_note(origin).startswith("# Origin")  # closing is not deleting


def test_reordering_tabs_reorders_the_model(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)

    sheet.notes._tabs.tabBar().moveTab(0, 1)

    assert sheet.notes.open_refs() == (log, origin)
    assert sheet.character.notes["notes"].files == [log, origin]


def test_the_block_titles_itself_after_the_active_note(make_sheet, two_notes) -> None:
    origin, _log = two_notes
    sheet = make_sheet()

    assert sheet.notes.block_title() == "Notes"
    sheet.notes.open_note(origin)
    assert sheet.notes.block_title() == "Notes — Origin"
    assert sheet.block_frame("notes").title_bar.title_text() == "Notes — Origin"


def test_an_empty_block_shows_its_empty_state(make_sheet) -> None:
    # A Notes block is placed globally but filled per character, so a character
    # with nothing in it still has the block and has to say what it is.
    sheet = make_sheet()
    assert sheet.notes.open_refs() == ()
    assert sheet.notes._stack.currentWidget() is sheet.notes._empty


def test_a_single_note_shows_no_tab_bar(make_sheet, two_notes) -> None:
    # One tab is not a choice, and a strip of chrome that never changes is a row
    # of the block's height spent saying nothing.
    origin, log = two_notes
    sheet = make_sheet()

    sheet.notes.open_note(origin)
    assert not sheet.notes._tabs.tabBar().isVisibleTo(sheet.notes)

    sheet.notes.open_note(log)
    assert sheet.notes._tabs.tabBar().isVisibleTo(sheet.notes)

    sheet.notes._close_ref(log)
    assert not sheet.notes._tabs.tabBar().isVisibleTo(sheet.notes)


def test_close_takes_over_from_the_tab_x_when_the_bar_is_hidden(make_sheet, two_notes) -> None:
    # Hiding the bar takes the per-tab ✕ with it, so the one note left has to stay
    # closable some other way.
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)

    assert sheet.notes._close_button.isVisibleTo(sheet.notes)
    sheet.notes.open_note(log)
    assert not sheet.notes._close_button.isVisibleTo(sheet.notes)

    sheet.notes._close_ref(log)
    sheet.notes._close_button.click()

    assert sheet.notes.open_refs() == ()
    assert notes.read_note(origin).startswith("# Origin")  # still not a delete


def test_an_empty_block_offers_neither_tabs_nor_close(make_sheet) -> None:
    sheet = make_sheet()
    assert not sheet.notes._tabs.tabBar().isVisibleTo(sheet.notes)
    assert not sheet.notes._close_button.isVisibleTo(sheet.notes)


def test_the_preview_toggle_reads_as_the_action_it_offers(make_sheet, two_notes) -> None:
    # Its glyph is the state read-out, like the lock's and the compact button's.
    from mm_companion.ui.sections.notes import LABEL_EDIT, LABEL_PREVIEW

    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)

    assert sheet.notes._preview_button.text() == LABEL_PREVIEW
    sheet.notes._preview_button.setChecked(True)
    assert sheet.notes._preview_button.text() == LABEL_EDIT
    assert sheet.notes._open[0].editor.is_preview()

    sheet.notes._preview_button.setChecked(False)
    assert sheet.notes._preview_button.text() == LABEL_PREVIEW


def test_the_preview_toggle_does_not_move_when_it_flips(make_sheet, two_notes) -> None:
    # The two labels are different lengths and the button sits after a stretch, so
    # without a held width it would jump sideways out from under the cursor that
    # just clicked it.
    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    pump()
    before = sheet.notes._preview_button.geometry()

    sheet.notes._preview_button.setChecked(True)
    pump()

    assert sheet.notes._preview_button.geometry() == before


def test_a_locked_sheet_cannot_close_the_only_note(make_sheet, two_notes) -> None:
    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)

    sheet.notes.set_locked(True)
    assert not sheet.notes._close_button.isEnabled()

    sheet.notes.set_locked(False)
    assert sheet.notes._close_button.isEnabled()


# -- autosave ------------------------------------------------------------------


def test_flush_writes_the_editor_text_to_the_file(make_sheet, two_notes) -> None:
    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)

    sheet.notes._open[0].editor.source.setPlainText("# Origin\n\nBitten by a spider.\n")
    sheet.notes.flush()

    assert notes.read_note(origin) == "# Origin\n\nBitten by a spider.\n"


def test_flush_lands_an_edit_the_editor_is_still_debouncing(make_sheet, two_notes) -> None:
    # A flush provoked by something else (a tab change, the block hiding) lands
    # inside the editor's own 400 ms window as often as not, and those keystrokes
    # are exactly the ones that must not be lost.
    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    editor = sheet.notes._open[0].editor
    editor.source.setPlainText("half typed")
    assert editor._debounce.isActive()

    sheet.notes.flush()

    assert notes.read_note(origin) == "half typed"


def test_a_tab_re_reads_a_file_that_moved_underneath_it(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)

    notes.write_note(origin, "# Origin\n\nedited elsewhere\n")
    sheet.notes._open[0].mtime = 0  # as if another sheet had just written it
    sheet.notes._tabs.setCurrentIndex(0)

    assert "edited elsewhere" in sheet.notes._open[0].editor.text()


def test_unwritten_local_text_beats_a_stale_read(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)
    sheet.notes._open[0].editor.source.setPlainText("mine, still being typed")
    sheet.notes._open[0].editor.flush()  # queued, not yet written

    notes.write_note(origin, "theirs")
    sheet.notes._open[0].mtime = 0
    sheet.notes._refresh_from_disk(sheet.notes._open[0])

    assert sheet.notes._open[0].editor.text() == "mine, still being typed"


# -- reseed (the undo path) ----------------------------------------------------


def test_reseed_follows_the_model_and_keeps_the_editors_it_can(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)
    kept = sheet.notes._open[0].editor

    sheet.character.notes["notes"].files[:] = [origin]
    sheet.notes.reseed()

    assert sheet.notes.open_refs() == (origin,)
    # The same editor, so the caret, the scroll position and its undo stack all
    # survive an undo of some unrelated field elsewhere on the sheet.
    assert sheet.notes._open[0].editor is kept


def test_reseed_never_writes_the_model(make_sheet, two_notes) -> None:
    # It runs after an undo has already put a state back; a write here would
    # record a step nobody took.
    origin, _log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    before = json.dumps(sheet.character.to_dict(), sort_keys=True)

    sheet.reseed()

    assert json.dumps(sheet.character.to_dict(), sort_keys=True) == before


# -- multiple instances --------------------------------------------------------


def test_a_second_block_is_added_and_keeps_its_own_notes(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)

    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)

    assert key == "notes#2"
    assert key in sheet.block_keys()
    assert sheet.notes.open_refs() == (origin,)
    assert sheet.character.notes[key].files == [log]


def test_an_instance_inherits_the_templates_size(make_sheet) -> None:
    # block_sizes.json and a preset's `blocks` map are keyed by block, and a
    # per-instance key could never appear in either. It is the *recommendation*
    # that is inherited now — the size the copy opens at — since a block's
    # minimum no longer has anything to do with what is in it.
    sheet = make_sheet()
    key = sheet.add_block_instance("notes")
    template = sheet.block_frame("notes").recommended_size()
    assert sheet.block_frame(key).recommended_size() == template
    assert sheet.block_frame(key).sizeHint().width() >= 320


def test_a_removed_instance_takes_its_model_entry_with_it(make_sheet, two_notes) -> None:
    _origin, log = two_notes
    sheet = make_sheet()
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)

    sheet.remove_block_instance(key)

    assert key not in sheet.block_keys()
    assert key not in sheet.character.notes


def test_the_first_block_is_never_removed(make_sheet) -> None:
    # The template's own key is the block every sheet has and every saved layout
    # names; closing it is what the View menu's checkbox is for.
    sheet = make_sheet()
    sheet.remove_block_instance("notes")
    assert "notes" in sheet.block_keys()


def test_instance_numbers_fill_the_first_gap(make_sheet) -> None:
    sheet = make_sheet()
    second = sheet.add_block_instance("notes")
    third = sheet.add_block_instance("notes")
    assert (second, third) == ("notes#2", "notes#3")

    sheet.remove_block_instance(second)
    assert sheet.add_block_instance("notes") == "notes#2"


# -- persistence ---------------------------------------------------------------


def test_a_saved_layout_rebuilds_its_instances(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)
    saved = sheet.save_layout()

    assert json.loads(saved)["instances"] == [{"key": key, "title": "Notes 2"}]

    restored = make_sheet(Character.from_dict(sheet.character.to_dict()))
    assert key not in restored.block_keys()  # a fresh sheet starts with one
    assert restored.restore_layout(saved) is True
    assert key in restored.block_keys()
    assert restored._sections_by_key[key].open_refs() == (log,)


def test_a_layout_naming_an_unbuildable_instance_falls_back(make_sheet) -> None:
    sheet = make_sheet()
    model = json.loads(sheet.save_layout())
    model["instances"] = [{"key": "nosuchblock#2", "title": "Nope"}]
    model["page"]["children"].append({"type": "leaf", "keys": ["nosuchblock#2"]})

    assert sheet.restore_layout(json.dumps(model)) is False
    assert "nosuchblock#2" not in sheet.block_keys()


def test_restoring_a_layout_without_instances_destroys_the_extra_blocks(make_sheet) -> None:
    sheet = make_sheet()
    plain = sheet.save_layout()
    key = sheet.add_block_instance("notes")
    assert key in sheet.block_keys()

    assert sheet.restore_layout(plain) is True
    assert key not in sheet.block_keys()


# -- merging -------------------------------------------------------------------


def test_merging_two_blocks_makes_a_tab_group_of_them(make_sheet, two_notes) -> None:
    """Merging combines whole *blocks* now, not the notes inside them.

    It used to be Notes-specific: dropping one Notes block on another moved its
    notes across and destroyed the source. One rule for every block replaced it —
    any block dropped into any other makes a cell with a tab each — so two Notes
    blocks now stay two blocks, each keeping its own notes, sharing one cell.
    That is a real trade: a tab bar of blocks each with a tab bar of notes is more
    chrome than the old answer, and it is the price of the rule being uniform.
    """
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)

    sheet.canvas.merge_blocks(key, "notes")

    group = sheet.canvas.group_for("notes")
    assert group is not None
    assert group.keys == ["notes", key]
    # Both blocks are still blocks, and each still holds its own notes.
    assert key in sheet.block_keys()
    assert sheet.notes.open_refs() == (origin,)
    assert sheet._sections_by_key[key].open_refs() == (log,)


def test_the_block_that_was_dropped_is_the_tab_showing(make_sheet) -> None:
    sheet = make_sheet()

    sheet.canvas.merge_blocks("skills", "powers")

    group = sheet.canvas.group_for("powers")
    assert group.active_key() == "skills"


def test_any_block_merges_with_any_other(make_sheet) -> None:
    """The rule that replaced ``accepts_merge``. A block used to have to opt in,
    which is why only Notes ever merged; there is nothing left to opt into."""
    sheet = make_sheet()

    sheet.canvas.merge_blocks("skills", "abilities")

    assert sheet.canvas.group_for("abilities").keys == ["abilities", "skills"]
    assert not hasattr(sheet.notes, "accepts_merge")


def test_a_block_cannot_be_merged_into_itself(make_sheet) -> None:
    sheet = make_sheet()
    before = sheet.arrangement()["page"]

    sheet.canvas.merge_blocks("skills", "skills")

    assert sheet.arrangement()["page"] == before


def test_a_third_block_joins_an_existing_group(make_sheet) -> None:
    sheet = make_sheet()
    sheet.canvas.merge_blocks("skills", "powers")

    sheet.canvas.merge_blocks("equipment", "skills")

    assert sheet.canvas.group_for("powers").keys == ["powers", "skills", "equipment"]


def test_a_group_round_trips_through_a_saved_layout(make_sheet) -> None:
    sheet = make_sheet()
    sheet.canvas.merge_blocks("skills", "powers")
    blob = sheet.save_layout()
    sheet.reset_layout()
    assert sheet.canvas.group_for("powers") is None

    assert sheet.restore_layout(blob) is True

    assert sheet.canvas.group_for("powers").keys == ["powers", "skills"]


def test_which_tab_is_showing_round_trips_too(make_sheet) -> None:
    sheet = make_sheet()
    sheet.canvas.merge_blocks("skills", "powers")
    blob = sheet.save_layout()
    sheet.reset_layout()

    assert sheet.restore_layout(blob) is True

    assert sheet.canvas.group_for("powers").active_key() == "skills"


def test_a_group_survives_the_next_rebuild_of_the_page(make_sheet, qapp) -> None:
    """The crash this guards took the whole application down.

    A tab group is cached across a rebuild, and a group that is a whole row *is*
    the row widget. The sweep that sheds the previous render's rows recognised a
    lone ``BlockFrame`` and spared it, but not a group — so the very widget just
    handed back for reuse was deleted, and its members' frames went with it. The
    next thing to ask either frame a question died on a dangling C++ object, and
    a Python exception raised inside a Qt override does not print and carry on: it
    takes the process with it.
    """
    import shiboken6
    from PySide6.QtCore import QEvent

    sheet = make_sheet()
    sheet.canvas.merge_blocks("equipment", "powers")
    pump()
    group = sheet.canvas.group_for("powers")
    assert group in sheet.canvas._row_widgets, "the group is not a row; nothing is proved"

    # Any structural change at all rebuilds the rows.
    sheet.canvas.hide_block("complications")
    pump()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert shiboken6.isValid(group), "the reused group was destroyed"
    for key in ("powers", "equipment"):
        assert shiboken6.isValid(sheet.block_frame(key)), f"{key}'s frame went with it"
    assert sheet.canvas.group_for("powers").keys == ["powers", "equipment"]


def test_a_grouped_block_lends_its_title_bar_to_the_group(make_sheet) -> None:
    """Two rows of chrome for one cell is one too many, and the group's buttons
    act on whichever block is showing anyway."""
    sheet = make_sheet()

    sheet.canvas.merge_blocks("skills", "powers")

    assert sheet.block_frame("skills").title_bar.isVisibleTo(sheet.block_frame("skills")) is False


def test_dragging_a_tab_out_leaves_the_group_and_gives_it_a_cell(make_sheet) -> None:
    sheet = make_sheet()
    sheet.canvas.merge_blocks("skills", "powers")
    pump()

    sheet.canvas._set_page(lt_split_out(sheet, "skills"))
    sheet.canvas._relayout()
    pump()

    assert sheet.canvas.group_for("powers") is None
    # And it gets its own title bar back, because it is the same widget it always was.
    assert sheet.block_frame("skills").title_bar.isVisibleTo(sheet.block_frame("skills")) is True


def lt_split_out(sheet, key: str):
    from mm_companion.ui import layout_tree as lt

    return lt.split_out(sheet.canvas.page_tree(), key)


def test_a_group_of_one_collapses_back_into_a_plain_block(make_sheet) -> None:
    sheet = make_sheet()
    sheet.canvas.merge_blocks("skills", "powers")
    pump()

    sheet.canvas._set_page(lt_split_out(sheet, "skills"))
    sheet.canvas._relayout()
    pump()

    from mm_companion.ui.block_frame import BlockFrame

    assert isinstance(sheet.canvas._build_leaf(_leaf("powers")), BlockFrame)


def _leaf(*keys: str):
    from mm_companion.ui.layout_tree import Leaf

    return Leaf(tuple(keys))


def test_a_drop_in_the_middle_of_a_block_is_a_merge(make_sheet) -> None:
    sheet = make_sheet()
    key = sheet.add_block_instance("notes")
    pump()
    scroll_to(sheet, "notes")
    canvas = sheet.canvas
    canvas._drag_key = key

    assert canvas._merge_target("notes") == "notes"
    # Every block takes every merge now, so the only refusal left is a block
    # being dropped on itself.
    assert canvas._merge_target(key) is None


def lt_keys(sheet) -> list[str]:
    """Every block the page places, in reading order."""
    from mm_companion.ui import layout_tree as lt

    return lt.keys(sheet.canvas.page_tree())


def pump(rounds: int = 10) -> None:
    """Let the page finish laying itself out.

    One ``processEvents`` is not enough: the sheet's scroll area settles its
    canvas over several turns, and until it has, every block is crammed into the
    viewport's height and a hit test lands on the wrong row entirely.
    """
    qapp = QApplication.instance()
    for _ in range(rounds):
        qapp.processEvents()


def centre_of(sheet, key: str):
    """Where block *key* is on screen right now, in global coordinates.

    Asked of the frame itself rather than of its row: a block can be several
    splitters deep in the page now, so there is no one row that owns it.
    """
    frame = sheet.block_frame(key)
    return frame.mapToGlobal(frame.rect().center())


def scroll_to(sheet, key: str) -> None:
    """Bring block *key* into view, which a drop onto it requires.

    ``_hit_test`` bounds the whole gesture to the page's viewport, so a block
    scrolled off the bottom of a long sheet cannot be dropped on — which is the
    honest behaviour, and means a test has to scroll first exactly as a hand
    would.
    """
    canvas = sheet.canvas
    frame = sheet.block_frame(key)
    centre = canvas.mapFromGlobal(frame.mapToGlobal(frame.rect().center()))
    sheet.page_scroll_area().ensureVisible(centre.x(), centre.y(), 0, 200)
    pump()


def drag_over(sheet, source: str, target: str):
    """Pick *source* up by its title bar and hold it over the middle of *target*.

    Returns where the cursor ends up, for the caller to release at. Two things a
    naive version gets wrong. The target's centre is read **after** the drag has
    started: picking a block up floats it out of its row, the page reflows, and
    everything below moves up, so an aim taken beforehand points at where the
    block used to be. And both blocks have to be *visible* first — see
    :func:`scroll_to`.
    """
    canvas = sheet.canvas
    scroll_to(sheet, target)
    start = sheet.block_frame(source).title_bar.mapToGlobal(QPoint(4, 4))
    canvas.title_bar_pressed(source, start)
    canvas.title_bar_moved(source, start + QPoint(0, 60))  # past startDragDistance
    pump()

    centre = centre_of(sheet, target)
    canvas.title_bar_moved(source, centre)
    pump()
    return centre


def drop_onto(sheet, source: str, target: str) -> None:
    """Drag block *source* and drop it on the middle of *target*.

    Drives the canvas's real gesture — press, move, release — rather than calling
    the merge handler, because the bug this guards lived *between* them: the
    release used to hit-test after ``_end_drag`` had already cleared the drag key,
    so every drop came out an ordinary dock and the merge never fired.
    """
    centre = drag_over(sheet, source, target)
    sheet.canvas.title_bar_released(source, centre)
    pump()


def test_dropping_a_block_on_another_groups_them(make_sheet, two_notes) -> None:
    """The whole gesture, end to end, rather than the handler underneath it.

    The bug this guards lived *between* the press and the release: the drop used
    to hit-test after ``_end_drag`` had cleared the drag key, so every drop came
    out an ordinary dock and the merge never fired at all.
    """
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)
    pump()

    drop_onto(sheet, key, "notes")

    group = sheet.canvas.group_for("notes")
    assert group is not None and group.keys == ["notes", key]
    assert key in sheet.block_keys()  # both blocks survive; they just share a cell


def test_dropping_a_block_on_an_ordinary_one_groups_them_too(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)
    pump()

    drop_onto(sheet, key, "skills")

    # Every block takes a merge now, so a drop into the middle of Skills groups
    # them — where it used to be refused and fall back to an ordinary dock.
    assert key in sheet.block_keys()
    assert sheet._sections_by_key[key].open_refs() == (log,)
    assert sheet.canvas.group_for("skills").keys == ["skills", key]


def test_the_merge_highlight_goes_up_during_the_drag_and_comes_down_after(
    make_sheet, two_notes
) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)
    pump()

    canvas = sheet.canvas
    centre = drag_over(sheet, key, "notes")

    assert canvas._merge_hint == "notes"
    assert sheet.block_frame("notes")._merge_feedback.state == "accept"

    canvas.title_bar_released(key, centre)
    pump()
    assert canvas._merge_hint is None


# -- splitting -----------------------------------------------------------------


def test_dragging_a_tab_out_gives_it_a_floating_block(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)

    sheet.notes.splitRequested.emit(log, QPoint(500, 400))

    new_key = sheet._split_key
    assert new_key == "notes#2"
    assert sheet.notes.open_refs() == (origin,)
    assert sheet._sections_by_key[new_key].open_refs() == (log,)
    # Mid-drag, so it is floating and follows the cursor like any dragged block.
    assert sheet.canvas.block_window(new_key) is not None


def test_releasing_a_split_drag_docks_the_new_block(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    sheet.notes.open_note(log)
    sheet.notes.splitRequested.emit(log, QPoint(500, 400))
    new_key = sheet._split_key

    sheet.notes.splitReleased.emit(sheet.canvas.mapToGlobal(QPoint(400, 200)))

    assert sheet.canvas.block_window(new_key) is None
    assert new_key in lt_keys(sheet)
    assert sheet._split_key is None


def test_every_notes_block_can_be_split_from_not_just_the_copies(make_sheet, two_notes) -> None:
    # The split signals are wired in _wire_section, which both the block built at
    # startup and the copies made later go through — connecting only the copies
    # is exactly the bug where the first block's tabs could not be dragged out.
    origin, log = two_notes
    sheet = make_sheet()
    key = sheet.add_block_instance("notes")
    copy = sheet._sections_by_key[key]
    copy.open_note(origin)
    copy.open_note(log)

    copy.splitRequested.emit(log, QPoint(500, 400))

    assert sheet._split_key == "notes#3"
    assert sheet._sections_by_key["notes#3"].open_refs() == (log,)


# -- the file under the tab ----------------------------------------------------


def test_a_note_that_would_not_write_stays_dirty(make_sheet, two_notes, monkeypatch) -> None:
    """write_note answers rather than raising, and that answer has to be read.

    Dropping it declared the text written, cleared the dirty set, and left the next
    refresh free to overwrite the editor from a file that never got the paragraph.
    """
    origin, _log = two_notes
    sheet = make_sheet()
    block = sheet.notes
    block.open_note(origin)
    item = block._open[0]
    item.editor.set_text("something worth keeping")
    block._on_note_edited(item.editor)

    monkeypatch.setattr(notes, "write_note", lambda ref, text: False)
    block.flush()

    assert item.editor in block._dirty
    assert item.editor.text() == "something worth keeping"


def test_deleting_a_note_does_not_write_it_back(make_sheet, two_notes) -> None:
    """Closing flushes, so a delete used to re-create the file it had just removed.

    write_note builds the parent directory on the way, so nothing downstream stops
    it — the note came back from the dead with the last keystroke inside it.
    """
    origin, _log = two_notes
    sheet = make_sheet()
    block = sheet.notes
    block.open_note(origin)
    block._open[0].editor.set_text("typed just before the delete")
    block._on_note_edited(block._open[0].editor)

    notes.delete_note(origin)
    note_events().deleted.emit(origin)

    assert notes.resolve_note_path(origin) is not None
    assert not notes.resolve_note_path(origin).exists()
    assert origin not in sheet.character.notes["notes"].files


def test_a_delete_reaches_every_block_holding_the_note(make_sheet, two_notes) -> None:
    """The picker belongs to one block; the file belongs to the workspace.

    A second block with the same note open kept a ref to a file that was gone, and
    the next keystroke there wrote it back out — two blocks, silently disagreeing.
    """
    origin, _log = two_notes
    sheet = make_sheet()
    first = sheet.notes
    first.open_note(origin)
    second = sheet._sections_by_key[sheet.add_block_instance("notes")]
    second.open_note(origin)
    assert origin in second.open_refs()

    notes.delete_note(origin)
    note_events().deleted.emit(origin)

    assert origin not in first.open_refs()
    assert origin not in second.open_refs()


def test_a_rename_reaches_every_block_holding_the_note(make_sheet, two_notes) -> None:
    """Same reasoning as the delete: both holders have to follow the file."""
    origin, _log = two_notes
    sheet = make_sheet()
    first = sheet.notes
    first.open_note(origin)
    second = sheet._sections_by_key[sheet.add_block_instance("notes")]
    second.open_note(origin)

    new_ref = notes.rename_note(origin, "Beginnings")
    note_events().renamed.emit(origin, new_ref)

    assert first.open_refs() == (new_ref,)
    assert second.open_refs() == (new_ref,)
