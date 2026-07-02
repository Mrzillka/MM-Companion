"""The character library: saving, loading, and listing characters on disk.

Pure data and no PySide6 (respects ``ui -> core -> data``). Characters are
persisted as one JSON file per character — the output of
:meth:`~mm_companion.core.character.Character.to_dict` — in the workspace
``characters/`` directory (see :mod:`.storage`). :func:`list_saved_characters`
is the single seam the start window reads from, so wiring or relocating storage
stays a local change here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from mm_companion.core import storage
from mm_companion.core.character import Character

CHARACTER_SUFFIX = ".json"

# Profile keys tried, in order, when deriving a character's display name.
_NAME_FIELDS = ("hero_name", "character_name")
UNNAMED = "Unnamed Character"


@dataclass(frozen=True)
class CharacterSummary:
    """The minimum a start-window card needs, plus the file it loads from."""

    name: str
    power_level: int
    image_path: str | None = None
    path: Path | None = None


def display_name(character: Character) -> str:
    """The best human name for a character: hero name, then character name."""
    for key in _NAME_FIELDS:
        value = str(character.profile.get(key, "")).strip()
        if value:
            return value
    return UNNAMED


def _slugify(name: str) -> str:
    """A filesystem-safe stem derived from a name (``"Iron Man" -> "iron-man"``)."""
    slug = re.sub(r"[^\w-]+", "-", name.strip().lower()).strip("-")
    return slug or "character"


def suggested_filename(character: Character) -> str:
    """A default filename for *character*, e.g. ``"iron-man.json"``."""
    return f"{_slugify(display_name(character))}{CHARACTER_SUFFIX}"


def _characters_dir() -> Path:
    """The workspace directory holding player characters."""
    return storage.get_workspace().characters_dir


def _unique_path(directory: Path, slug: str) -> Path:
    """A path under *directory* for *slug* that does not collide with a file."""
    candidate = directory / f"{slug}{CHARACTER_SUFFIX}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{slug}-{counter}{CHARACTER_SUFFIX}"
        counter += 1
    return candidate


def save_character(
    character: Character,
    *,
    path: Path | None = None,
    directory: Path | None = None,
) -> Path:
    """Write *character* to disk as JSON and return the file it was written to.

    With an explicit *path* the file is overwritten in place (a plain "Save").
    Otherwise a new, non-colliding filename is derived from the character's name
    inside *directory* (defaulting to the workspace ``characters/`` dir) — a
    first save or "Save As".
    """
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        directory = Path(directory) if directory is not None else _characters_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = _unique_path(directory, _slugify(display_name(character)))

    path.write_text(json.dumps(character.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_character(path: Path) -> Character:
    """Rebuild a :class:`Character` from a saved JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Character.from_dict(raw)


def delete_character(path: Path) -> None:
    """Remove a saved character file; a missing file is not an error."""
    Path(path).unlink(missing_ok=True)


def list_saved_characters(directory: Path | None = None) -> list[CharacterSummary]:
    """Return a summary of every saved character, for the launcher's library.

    Scans *directory* (defaulting to the workspace ``characters/`` dir) for
    ``*.json`` files, tolerating any that fail to parse, and returns one
    :class:`CharacterSummary` per readable file, sorted by name.
    """
    directory = Path(directory) if directory is not None else _characters_dir()
    if not directory.is_dir():
        return []

    summaries: list[CharacterSummary] = []
    for file in sorted(directory.glob(f"*{CHARACTER_SUFFIX}")):
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        character = Character.from_dict(raw)
        summaries.append(
            CharacterSummary(
                name=display_name(character),
                power_level=character.power_level,
                image_path=character.image_path,
                path=file,
            )
        )
    summaries.sort(key=lambda s: s.name.lower())
    return summaries
