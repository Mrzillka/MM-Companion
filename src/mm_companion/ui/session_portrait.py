"""Carry a character portrait between session peers as a small base64 thumbnail.

The wire protocol strips ``image_path`` — a portrait path names a file on the
*sender's* disk and would resolve to the wrong picture (or nothing) on the
receiver's. So the picture travels instead as a downscaled, base64-encoded
thumbnail riding along in the snapshot dict under a ``portrait`` key.

This lives in ``ui/`` because turning a file into a thumbnail is Qt work (QImage),
and because it is a display concern, not a rule. It is deliberately kept **well
under** :data:`~mm_companion.core.session.protocol.MAX_MESSAGE_BYTES`: the
snapshot's other fields share the same 256 KiB message, so an oversized portrait
is dropped rather than allowed to fail the whole send.
"""

from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPixmap

from mm_companion.core import library

#: The longest side a transmitted portrait is scaled down to. Big enough for the
#: sheet's image block, small enough that the encoding is a few tens of KB.
PORTRAIT_MAX_PX = 256
#: JPEG quality for the thumbnail — a good size/quality trade for a photo portrait.
PORTRAIT_JPEG_QUALITY = 85
#: Hard ceiling on the base64 string. Kept far under the protocol's 256 KiB message
#: cap so the rest of the snapshot always fits; an image past this is simply not sent.
PORTRAIT_MAX_CHARS = 180 * 1024


def encode_portrait(image_path: str | None) -> str | None:
    """A base64 JPEG thumbnail of *image_path*, or ``None`` if there is nothing to send.

    *image_path* is a :class:`~mm_companion.core.character.Character` reference —
    a bare workspace filename or an absolute path — resolved the usual way. A
    missing/unreadable file, or an encoding that would blow
    :data:`PORTRAIT_MAX_CHARS`, yields ``None`` (the card falls back to its
    placeholder).
    """
    resolved = library.resolve_image_path(image_path)
    if not resolved:
        return None
    image = QImage(resolved)
    if image.isNull():
        return None
    if image.width() > PORTRAIT_MAX_PX or image.height() > PORTRAIT_MAX_PX:
        image = image.scaled(
            PORTRAIT_MAX_PX,
            PORTRAIT_MAX_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG", PORTRAIT_JPEG_QUALITY):
        return None
    encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
    if len(encoded) > PORTRAIT_MAX_CHARS:
        return None
    return encoded


def _decode_bytes(data: object) -> bytes | None:
    if not isinstance(data, str) or not data:
        return None
    try:
        return base64.b64decode(data.encode("ascii"), validate=True)
    except (ValueError, binascii.Error):
        return None


def decode_portrait(data: object) -> QPixmap | None:
    """Turn a received ``portrait`` payload back into a pixmap, or ``None`` if invalid."""
    raw = _decode_bytes(data)
    if raw is None:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(raw):
        return None
    return pixmap


def portrait_to_tempfile(data: object) -> str | None:
    """Write a received portrait to a temp JPEG and return its absolute path.

    Used to show a remote player's portrait on the GM's read-only *sheet*, whose
    image block reads a path: an absolute path is passed straight through by
    :func:`~mm_companion.core.library.resolve_image_path`. ``None`` for an invalid
    or absent payload.
    """
    raw = _decode_bytes(data)
    if raw is None:
        return None
    fd, name = tempfile.mkstemp(prefix="mm-portrait-", suffix=".jpg")
    try:
        Path(name).write_bytes(raw)
    finally:
        import os

        os.close(fd)
    return name
