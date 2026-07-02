"""The character library: the seam the start window reads saved characters from.

Pure data and no PySide6 (respects ``ui -> core -> data``). Save/load does not
exist yet, so :func:`list_saved_characters` returns an empty list; once
characters are persisted this becomes the single place the UI depends on.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CharacterSummary:
    """The minimum a start-window card needs: an image, a name, a power level."""

    name: str
    power_level: int
    image_path: str | None = None


def list_saved_characters() -> list[CharacterSummary]:
    """Return summaries of every saved character, most useful for the launcher.

    Returns an empty list for now: there is no persistence yet. When save/load
    lands this will scan a user data directory of serialized
    :meth:`~mm_companion.core.character.Character.to_dict` JSON and build one
    :class:`CharacterSummary` per file. Keeping the UI behind this one function
    means wiring real storage later is a local change.
    """

    return []
