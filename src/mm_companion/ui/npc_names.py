"""Suggested names for the Quick NPC wizard.

The names live in ``npc_names.json`` (loaded as package data, the same way
``block_sizes.json`` is) so the list can be extended without touching code.

Deliberately under ``ui/`` rather than ``data/``: this is a placeholder in a text
box, not game content. Nothing in ``core`` reads it, nothing is derived from the
SRD, and the OGL boundary that ``data/`` marks stays where it is.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from importlib.resources import files

RESOURCE_PACKAGE = "mm_companion.ui"
RESOURCE_NAME = "npc_names.json"

#: What the box says when the list can't be read. Never blank: the wizard's name
#: field is the one thing a GM must not have to stop and think about.
FALLBACK_NAME = "Nameless Thug"


@lru_cache(maxsize=1)
def npc_names() -> tuple[str, ...]:
    """Every suggested name, flattened across the file's groups.

    The groups are for whoever edits the file; the wizard draws from all of them,
    since a GM asking for "another one" wants a different name, not a different
    category. Order is the file's own, so a fresh install is at least predictable.
    """
    text = files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_text(encoding="utf-8")
    groups = json.loads(text).get("names", {})
    return tuple(name for group in groups.values() for name in group)


def random_npc_name(exclude: str = "") -> str:
    """A suggested name, never *exclude* (so re-rolling always visibly changes it)."""
    names = [name for name in npc_names() if name != exclude]
    if not names:
        return FALLBACK_NAME
    return random.choice(names)
