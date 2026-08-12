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
        qapp.processEvents()
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
    qapp = QApplication.instance()
    qapp.processEvents()
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
