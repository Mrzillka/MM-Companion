"""The bundled NPC icons: what a quick NPC wears instead of a loaded picture.

Eight flat badges under ``ui/assets/npc_icons/`` — UI artwork, MIT like the d20, not
OGL content, so they live here rather than under ``data/``. The first four are the
faces of the Quick NPC presets (``system.json``'s ``quick_npc``); the rest are extra
looks the dialog's arrows step through.

A chosen icon reaches the creature as an ordinary portrait. :func:`store_icon`
renders it once into the workspace ``images/`` dir as a PNG and hands back the bare
filename, which is exactly what a saved character's ``image_path`` holds — so the
GM card, the sheet's image block, the scene thumbnail and the session portrait all
read it the way they read any picture, and none of them had to learn about icons.
A PNG rather than a copy of the SVG so that nothing downstream depends on Qt's SVG
image plugin, and one file per icon rather than per creature: ten goons share one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap

from mm_companion.core import storage
from mm_companion.ui.svg_assets import RESOURCE_PACKAGE, svg_pixmap


@dataclass(frozen=True)
class NPCIcon:
    """One bundled icon: its id, what the dialog calls it, and its resource path."""

    id: str
    label: str
    resource: str


#: Every icon, in the order the dialog's arrows step through them.
NPC_ICONS: tuple[NPCIcon, ...] = (
    NPCIcon("brute", "Brute", "assets/npc_icons/brute.svg"),
    NPCIcon("speed", "Speed", "assets/npc_icons/speed.svg"),
    NPCIcon("balance", "Balance", "assets/npc_icons/balance.svg"),
    NPCIcon("random", "Random", "assets/npc_icons/random.svg"),
    NPCIcon("blaster", "Blaster", "assets/npc_icons/blaster.svg"),
    NPCIcon("mystic", "Mystic", "assets/npc_icons/mystic.svg"),
    NPCIcon("machine", "Machine", "assets/npc_icons/machine.svg"),
    NPCIcon("beast", "Beast", "assets/npc_icons/beast.svg"),
)

#: The icon each Quick NPC preset puts on the creature, by preset id. A preset not
#: listed (a mod's) keeps whichever icon is showing.
PRESET_ICONS = {"brute": "brute", "speed": "speed", "balance": "balance", "random": "random"}

#: The side, in pixels, an icon is stored at. Large enough for the sheet's image
#: block, which scales a portrait up to fill whatever room it is given.
STORED_SIZE = 512

#: What a stored icon's file is called inside ``images/``. ``digest`` is a few
#: characters of the drawing's own hash, so a later version that redraws an icon
#: writes a new file rather than finding the old picture and keeping it forever.
STORED_NAME = "npc-icon-{id}-{digest}.png"


def icon(icon_id: str) -> NPCIcon:
    """The icon called *icon_id*, or the first one for an id that is not bundled."""
    return next((i for i in NPC_ICONS if i.id == icon_id), NPC_ICONS[0])


def step(icon_id: str, delta: int) -> str:
    """The id *delta* places along from *icon_id*, wrapping at both ends."""
    ids = [i.id for i in NPC_ICONS]
    index = ids.index(icon(icon_id).id)
    return ids[(index + delta) % len(ids)]


def icon_pixmap(icon_id: str, size: int, ratio: float = 1.0) -> QPixmap:
    """Icon *icon_id* drawn *size* logical pixels square, for a screen of *ratio*."""
    return svg_pixmap(icon(icon_id).resource, QSize(size, size), ratio)


@cache
def stored_name(icon_id: str) -> str:
    """The filename icon *icon_id* is stored under — fixed by what the drawing is."""
    chosen = icon(icon_id)
    # Line endings normalised: a Windows checkout writes the same drawing as CRLF,
    # and the same picture should not get two names on two machines.
    drawing = files(RESOURCE_PACKAGE).joinpath(chosen.resource).read_bytes()
    drawing = drawing.replace(b"\r\n", b"\n")
    return STORED_NAME.format(id=chosen.id, digest=hashlib.sha1(drawing).hexdigest()[:8])


def store_icon(icon_id: str) -> str | None:
    """Put icon *icon_id* in the workspace ``images/`` dir; return its bare filename.

    Rendered only the first time: every later creature wearing the same icon points
    at the same file. ``None`` when it cannot be written (a read-only or full disk):
    a creature with no portrait is better than one pointing at a file that is not
    there.
    """
    name = stored_name(icon_id)
    images = storage.get_workspace().images_dir
    target = images / name
    if target.is_file():
        return name
    try:
        images.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if not icon_pixmap(icon(icon_id).id, STORED_SIZE).save(str(target), "PNG"):
        return None
    return name
