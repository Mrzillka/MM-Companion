"""Whole-file writes keep the previous save intact on failure."""

from pathlib import Path

import pytest

from mm_companion.core import files


@pytest.mark.parametrize("stage", ["fsync", "replace"])
def test_failed_write_preserves_the_old_file_and_removes_its_temp(tmp_path, monkeypatch, stage):
    path = tmp_path / "saved.json"
    path.write_text("previous save", encoding="utf-8")

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(files.os, stage, fail)
    with pytest.raises(OSError, match="simulated disk failure"):
        files.atomic_write_text(path, "new save")

    assert path.read_text(encoding="utf-8") == "previous save"
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_replaces_a_file_with_utf8_text(tmp_path: Path):
    path = tmp_path / "saved.json"
    files.atomic_write_text(path, "Ана\nnew save")
    assert path.read_text(encoding="utf-8") == "Ана\nnew save"
    assert list(tmp_path.iterdir()) == [path]
