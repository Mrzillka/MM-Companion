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
    # per-instance key could never appear in either.
    sheet = make_sheet()
    key = sheet.add_block_instance("notes")
    assert sheet.block_frame(key).minimumSizeHint().width() >= 320


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
    model["rows"].append(["nosuchblock#2"])

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


def test_merging_moves_every_tab_and_drops_the_source(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)

    sheet._on_merge_requested(key, "notes")

    assert sheet.notes.open_refs() == (origin, log)
    assert key not in sheet.block_keys()
    assert sheet.character.notes["notes"].files == [origin, log]


def test_merging_into_a_copy_empties_the_first_block_but_keeps_it(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)

    sheet._on_merge_requested("notes", key)

    assert "notes" in sheet.block_keys()
    assert sheet.notes.open_refs() == ()
    assert sheet._sections_by_key[key].open_refs() == (log, origin)


def test_only_notes_blocks_accept_a_merge(make_sheet) -> None:
    sheet = make_sheet()
    assert sheet.notes.accepts_merge("notes#2") is True
    assert sheet.notes.accepts_merge("skills") is False
    # Every other block simply has no opinion, which is what keeps their drags
    # exactly as they were.
    assert not hasattr(sheet.skills, "accepts_merge")


def test_a_drop_onto_an_ordinary_block_is_a_plain_slot(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    canvas._drag_key = "notes"
    row = canvas._row_widgets[1]  # abilities | resistances

    slot = canvas._merge_target(row, row.geometry().center())

    assert slot is None


def test_a_drop_in_the_middle_of_a_notes_block_is_a_merge(make_sheet) -> None:
    sheet = make_sheet()
    key = sheet.add_block_instance("notes")
    pump()
    scroll_to(sheet, "notes")
    canvas = sheet.canvas
    canvas._drag_key = key
    row = next(r for r in canvas._row_widgets if any(f.key == "notes" for f in r.frames()))
    frame = next(f for f in row.frames() if f.key == "notes")

    centre = row.mapToParent(frame.geometry().center())

    assert canvas._merge_target(row, centre) == "notes"
    # …and the outer band is still an ordinary insert, so a block can always be
    # placed *beside* another rather than into it.
    edge = row.mapToParent(frame.geometry().topLeft()) + QPoint(2, 2)
    assert canvas._merge_target(row, edge) is None


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
    """Where block *key* is on screen right now, in global coordinates."""
    canvas = sheet.canvas
    frame = sheet.block_frame(key)
    row = next(r for r in canvas._row_widgets if frame in r.frames())
    return canvas.mapToGlobal(row.mapToParent(frame.geometry().center()))


def scroll_to(sheet, key: str) -> None:
    """Bring block *key* into view, which a drop onto it requires.

    ``_hit_test`` bounds the whole gesture to the page's viewport, so a block
    scrolled off the bottom of a long sheet cannot be dropped on — which is the
    honest behaviour, and means a test has to scroll first exactly as a hand
    would.
    """
    frame = sheet.block_frame(key)
    row = next(r for r in sheet.canvas._row_widgets if frame in r.frames())
    centre = row.mapToParent(frame.geometry().center())
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


def test_dropping_a_notes_block_on_another_merges_them(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)
    pump()

    drop_onto(sheet, key, "notes")

    assert key not in sheet.block_keys()
    assert sheet.notes.open_refs() == (origin, log)


def test_dropping_a_notes_block_on_an_ordinary_one_just_docks(make_sheet, two_notes) -> None:
    origin, log = two_notes
    sheet = make_sheet()
    sheet.notes.open_note(origin)
    key = sheet.add_block_instance("notes")
    sheet._sections_by_key[key].open_note(log)
    pump()

    drop_onto(sheet, key, "skills")

    assert key in sheet.block_keys()  # still its own block
    assert sheet._sections_by_key[key].open_refs() == (log,)


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
    assert any(new_key in row for row in sheet.arrangement()["rows"])
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
