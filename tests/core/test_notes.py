"""The notes store and the model field that references it. No Qt."""

from __future__ import annotations

from pathlib import Path

from mm_companion.core import library, notes, storage
from mm_companion.core.character import Character, NotesState

# -- the workspace directory ---------------------------------------------------


def test_ensure_workspace_makes_the_notes_dir() -> None:
    workspace = storage.ensure_workspace()
    assert workspace.notes_dir.is_dir()
    assert workspace.notes_dir.name == storage.NOTES_DIRNAME


# -- files ---------------------------------------------------------------------


def test_a_new_note_is_a_slugged_file_seeded_with_its_title() -> None:
    ref = notes.create_note("Origin Story")
    assert ref == "origin-story.md"
    assert notes.read_note(ref) == "# Origin Story\n\n"
    assert notes.note_title(ref) == "Origin Story"


def test_two_notes_with_one_name_do_not_collide() -> None:
    first, second = notes.create_note("Log"), notes.create_note("Log")
    assert (first, second) == ("log.md", "log-2.md")


def test_a_note_writes_and_reads_back() -> None:
    ref = notes.create_note("Log")
    assert notes.write_note(ref, "# Session 3\n\nThe **bank** job.\n") is True
    assert notes.read_note(ref).startswith("# Session 3")
    assert notes.note_title(ref) == "Session 3"


def test_writing_leaves_no_temp_file_behind() -> None:
    # The write is atomic (temp + os.replace), which must not litter the dir the
    # picker lists — a stray .md.tmp would show up as a note.
    ref = notes.create_note("Log")
    notes.write_note(ref, "hello")
    assert sorted(p.name for p in notes.notes_dir().iterdir()) == [ref]


def test_a_missing_note_reads_as_empty_rather_than_raising() -> None:
    # A note deleted out from under an open tab must not take the sheet down.
    assert notes.read_note("gone.md") == ""
    assert notes.note_mtime("gone.md") is None
    assert notes.note_title("gone.md") == "Gone"


def test_a_title_only_counts_a_heading_that_opens_the_file() -> None:
    ref = notes.create_note("Log")
    notes.write_note(ref, "some prose first\n\n# Not The Title\n")
    assert notes.note_title(ref) == "Log"  # falls back to the filename, read as prose


def test_an_external_note_is_copied_into_the_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# Elsewhere\n", encoding="utf-8")

    ref = notes.store_note(str(outside))

    assert ref == "elsewhere.md"  # a bare filename, not the original path
    assert (notes.notes_dir() / ref).read_text(encoding="utf-8") == "# Elsewhere\n"
    outside.unlink()
    assert notes.read_note(ref) == "# Elsewhere\n"  # survives the original going


def test_storing_leaves_a_workspace_reference_alone() -> None:
    ref = notes.create_note("Log")
    assert notes.store_note(ref) == ref
    assert notes.store_note(str(notes.notes_dir() / ref)) == ref


def test_a_missing_source_is_returned_unchanged(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope.md")
    assert notes.store_note(missing) == missing


def test_rename_moves_the_file_and_leaves_the_heading_alone() -> None:
    ref = notes.create_note("Draft")
    notes.write_note(ref, "# Draft\n\nbody\n")

    new_ref = notes.rename_note(ref, "Final Cut")

    assert new_ref == "final-cut.md"
    assert notes.read_note(new_ref) == "# Draft\n\nbody\n"
    assert notes.read_note(ref) == ""


def test_delete_is_quiet_about_a_note_already_gone() -> None:
    ref = notes.create_note("Log")
    notes.delete_note(ref)
    notes.delete_note(ref)
    assert notes.list_notes() == []


def test_listing_is_most_recently_edited_first() -> None:
    first, second = notes.create_note("First"), notes.create_note("Second")
    notes.write_note(first, "# First\n\ntouched last\n")

    listed = notes.list_notes()

    assert [summary.ref for summary in listed] == [first, second]
    assert listed[0].title == "First"


def test_resolve_passes_an_absolute_path_through(tmp_path: Path) -> None:
    outside = tmp_path / "x.md"
    assert notes.resolve_note_path(str(outside)) == outside
    assert notes.resolve_note_path("x.md") == notes.notes_dir() / "x.md"
    assert notes.resolve_note_path("") is None


def test_slugify_takes_a_caller_supplied_fallback() -> None:
    # Shared with the character library, which wants a different word for a name
    # that slugs to nothing.
    assert library.slugify("!!!") == "character"
    assert library.slugify("!!!", "note") == "note"


# -- the model field -----------------------------------------------------------


def test_an_empty_notes_field_is_omitted_from_a_save() -> None:
    # A character saved before Notes existed has to round-trip byte-for-byte.
    character = Character()
    assert "notes" not in character.to_dict()

    character.notes["notes"] = NotesState()
    assert "notes" not in character.to_dict()  # an open-nothing block is not state


def test_open_notes_round_trip_through_a_save() -> None:
    character = Character()
    character.notes["notes"] = NotesState(files=["a.md", "b.md"], active="b.md")
    character.notes["notes#2"] = NotesState(files=["c.md"])

    rebuilt = Character.from_dict(character.to_dict())

    assert rebuilt.notes["notes"] == NotesState(files=["a.md", "b.md"], active="b.md")
    assert rebuilt.notes["notes#2"] == NotesState(files=["c.md"], active="")


def test_from_dict_tolerates_junk_in_the_notes_key() -> None:
    rebuilt = Character.from_dict({"notes": {"notes": "not a dict"}})
    assert rebuilt.notes["notes"] == NotesState()


def test_restore_refills_notes_in_place() -> None:
    # The Notes block holds `character.notes` and mutates it; rebinding the dict
    # here would desync every open block silently.
    character = Character()
    character.notes["notes"] = NotesState(files=["a.md"])
    held = character.notes
    saved = character.to_dict()

    character.restore(Character().to_dict())
    assert character.notes is held and character.notes == {}

    character.restore(saved)
    assert character.notes is held
    assert character.notes["notes"] == NotesState(files=["a.md"])


def test_a_note_is_not_deleted_with_the_character_that_referenced_it(tmp_path: Path) -> None:
    # Notes belong to no character: one can be open on two sheets, so deleting a
    # character must not take a file the other one is still using.
    ref = notes.create_note("Shared")
    character = Character()
    character.notes["notes"] = NotesState(files=[ref])
    path = library.save_character(character)

    library.delete_character(path)

    assert notes.read_note(ref).startswith("# Shared")
