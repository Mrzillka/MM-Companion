"""The notes library: markdown files in the workspace ``notes/`` directory.

Pure data and no PySide6 (respects ``ui -> core -> data``). A note is an
ordinary ``.md`` file — nothing about it is app-specific, so it can be edited in
any text editor and read by any markdown tool — and this module is the single
seam the Notes block reads and writes them through.

Deliberately unlike :mod:`.library`, in one way that shapes everything else:
**a note belongs to no character**. A character's file records only which notes
its sheet has open (see :attr:`~mm_companion.core.character.Character.notes`),
so the same note can be open on two sheets at once, and deleting a character
leaves its notes alone. That also means nothing here garbage-collects an
orphaned note, exactly as nothing prunes an unreferenced image from ``images/``:
the file is the user's, and the app is not the only thing that may be using it.

References are stored the way an image reference is (see
:func:`~mm_companion.core.library.resolve_image_path`): a bare filename inside
``notes/``, resolved on demand. An absolute path passes through unchanged, which
is what a note being imported looks like before it is copied in.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from mm_companion.core import storage
from mm_companion.core.library import slugify, unique_path

NOTE_SUFFIX = ".md"

#: What a brand-new note starts with, so the file is never zero bytes and the
#: first heading (which is also its title) is already there to type over.
NEW_NOTE_TEMPLATE = "# {title}\n\n"

UNTITLED = "Untitled Note"

# The first ATX heading in a file is taken as its title, so renaming a note in
# the editor and renaming it in the picker are the same act.
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class NoteSummary:
    """The minimum a picker row needs, plus the reference that opens it."""

    ref: str
    title: str
    modified: float


def notes_dir() -> Path:
    """The workspace directory holding notes."""
    return storage.get_workspace().notes_dir


def resolve_note_path(ref: str | None) -> Path | None:
    """Turn a stored note reference into a path, or ``None`` when there is none.

    A bare filename resolves against ``notes/``; an absolute path is returned
    unchanged (a note being imported, before :func:`store_note` copies it in).
    Existence is deliberately **not** checked — the same bargain
    :func:`~mm_companion.core.library.resolve_image_path` strikes — so a
    reference whose file has been deleted resolves to a path that isn't there
    and the block renders it as missing rather than this raising.
    """
    if not ref:
        return None
    path = Path(ref)
    return path if path.is_absolute() else notes_dir() / path


def store_note(ref: str | None) -> str | None:
    """Copy an external ``.md`` into the workspace and return its bare filename.

    The twin of :func:`~mm_companion.core.library._store_image`, and for the same
    reason: a note referenced by a character has to survive the original file
    being moved. A reference that is already workspace-relative (or already in
    ``notes/``) is left alone, and a missing source is returned unchanged rather
    than raising.
    """
    if not ref:
        return ref
    source = Path(ref)
    if not source.is_absolute():
        return ref  # already a workspace-relative filename
    target_dir = notes_dir()
    if source.parent == target_dir:
        return source.name  # already stored in the workspace
    if not source.is_file():
        return ref  # source is gone; keep the reference we were given
    target_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(target_dir, source.name)
    shutil.copy2(source, target)
    return target.name


def read_note(ref: str | None) -> str:
    """The text of note *ref*, or ``""`` when it is missing or unreadable.

    Never raises: a note whose file has been deleted out from under an open tab
    reads as empty, which the block can show, where an exception would take the
    whole sheet down.
    """
    path = resolve_note_path(ref)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write_note(ref: str | None, text: str) -> bool:
    """Write *text* to note *ref*; returns whether it landed.

    Atomic — written to a temp file in the same directory and then
    :func:`os.replace`\\ d — because this runs on an autosave timer while the
    user is typing, and a crash mid-write must not leave a half-written note
    where a whole one used to be.
    """
    path = resolve_note_path(ref)
    if path is None:
        return False
    temp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        return False
    return True


def note_mtime(ref: str | None) -> float | None:
    """When note *ref* was last written, or ``None`` if it isn't there.

    The cheap guard against two sheets holding the same note: an editor
    comparing this against what it read can tell that the file moved under it.
    """
    path = resolve_note_path(ref)
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def note_title(ref: str | None) -> str:
    """The title of note *ref*: its first heading, else its filename."""
    return _title_from(read_note(ref), ref)


def _title_from(text: str, ref: str | None) -> str:
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match and match.group(1):
            return match.group(1)
        if line.strip():
            break  # a heading only counts as the title when it opens the file
    if ref:
        # The filename, read back as prose. Capitalised only at the front: a slug
        # is lower-cased, and title-casing the whole thing would turn "npcs" into
        # "Npcs" and mangle every name the user actually chose.
        stem = Path(ref).stem.replace("-", " ").replace("_", " ").strip()
        return (stem[:1].upper() + stem[1:]) if stem else UNTITLED
    return UNTITLED


def create_note(title: str = "") -> str:
    """Create a new note file and return its bare filename."""
    name = title.strip() or UNTITLED
    target_dir = notes_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(target_dir, f"{slugify(name, 'note')}{NOTE_SUFFIX}")
    path.write_text(NEW_NOTE_TEMPLATE.format(title=name), encoding="utf-8")
    return path.name


def rename_note(ref: str, title: str) -> str:
    """Rename note *ref*'s file after *title*; returns the new reference.

    Only the **file** is renamed — the heading inside it is the user's text and
    is left alone, since the two are edited in different places and the app
    rewriting a note's first line is not what "rename" asked for. A reference
    that cannot be renamed (a missing file, a name collision that survives
    :func:`unique_path`) is returned unchanged.
    """
    path = resolve_note_path(ref)
    name = title.strip()
    if path is None or not name or not path.is_file():
        return ref
    target = unique_path(path.parent, f"{slugify(name, 'note')}{NOTE_SUFFIX}")
    if target == path:
        return ref
    try:
        path.rename(target)
    except OSError:
        return ref
    return target.name if not Path(ref).is_absolute() else str(target)


def delete_note(ref: str) -> None:
    """Delete note *ref*'s file (no error when it is already gone)."""
    path = resolve_note_path(ref)
    if path is not None:
        path.unlink(missing_ok=True)


def list_notes() -> list[NoteSummary]:
    """Every note in the workspace, most recently edited first."""
    directory = notes_dir()
    summaries: list[NoteSummary] = []
    try:
        entries = sorted(directory.glob(f"*{NOTE_SUFFIX}"))
    except OSError:
        return []
    for path in entries:
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        summaries.append(
            NoteSummary(path.name, _title_from(read_note(path.name), path.name), modified)
        )
    summaries.sort(key=lambda summary: (-summary.modified, summary.title.lower()))
    return summaries
