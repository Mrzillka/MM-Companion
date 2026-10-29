"""A portrait travels as a small base64 thumbnail, well under the message cap.

The wire strips ``image_path`` (a path is meaningless on another machine), so the
picture rides along as a downscaled base64 payload instead. These tests pin the
two things that matter: it is small enough to share the snapshot's 256 KiB
message, and it round-trips back to a real image on the far end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.session.protocol import (
    MAX_MESSAGE_BYTES,
    CharacterSnapshot,
    encode,
    sanitize_snapshot,
)
from mm_companion.ui.session_portrait import (
    PORTRAIT_MAX_PX,
    decode_portrait,
    encode_portrait,
    portrait_to_tempfile,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def a_portrait_file(tmp_path: Path, size: int = 800) -> str:
    """A big square PNG on disk, so encoding has something to downscale."""
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(0xFF3366CC)
    path = tmp_path / "hero.png"
    assert image.save(str(path), "PNG")
    return str(path)


def test_encode_downscales_and_round_trips(qapp: QApplication, tmp_path: Path) -> None:
    encoded = encode_portrait(a_portrait_file(tmp_path))
    assert isinstance(encoded, str) and encoded

    pixmap = decode_portrait(encoded)
    assert pixmap is not None
    assert pixmap.width() <= PORTRAIT_MAX_PX and pixmap.height() <= PORTRAIT_MAX_PX


def test_encode_returns_none_without_an_image(qapp: QApplication) -> None:
    assert encode_portrait(None) is None
    assert encode_portrait("/no/such/file.png") is None


def test_decode_rejects_garbage(qapp: QApplication) -> None:
    assert decode_portrait("not base64!!!") is None
    assert decode_portrait("") is None
    assert decode_portrait(None) is None


def test_a_snapshot_with_a_portrait_fits_the_message_cap(
    qapp: QApplication, tmp_path: Path
) -> None:
    character = Character.new_default(load_game_data())
    snapshot = character.to_dict()
    snapshot["portrait"] = encode_portrait(a_portrait_file(tmp_path))

    # sanitize keeps the portrait (it only strips image_path)...
    sanitized = sanitize_snapshot(snapshot)
    assert "portrait" in sanitized
    assert "image_path" not in sanitized
    # ...and the whole framed message stays comfortably under the protocol's cap.
    line = encode(CharacterSnapshot(character=sanitized))
    assert len(line) < MAX_MESSAGE_BYTES


def test_portrait_to_tempfile_writes_a_readable_image(qapp: QApplication, tmp_path: Path) -> None:
    encoded = encode_portrait(a_portrait_file(tmp_path))
    path = portrait_to_tempfile(encoded)
    assert path is not None
    try:
        assert not QImage(path).isNull()
    finally:
        Path(path).unlink()
