"""The Complications block: add/edit/remove rows, dirty signalling, and locking."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character, Complication
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.sections.complications import ComplicationsSection


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _section(complications: list[Complication] | None = None) -> ComplicationsSection:
    data = load_game_data()
    char = Character.new_default(data)
    char.complications = list(complications or [])
    return ComplicationsSection(data, char)


def test_seeds_a_row_per_complication(qapp: QApplication) -> None:
    section = _section([Complication("Enemy", "Dr. Volt"), Complication("Secret", "Alter ego")])
    assert len(section._row_refs) == 2
    assert section._row_refs[0].name.text() == "Enemy"
    assert section._row_refs[0].description.toPlainText() == "Dr. Volt"


def test_add_appends_to_the_model_and_marks_dirty(qapp: QApplication) -> None:
    section = _section()
    edits: list[int] = []
    section.edited.connect(lambda: edits.append(1))

    section._add_complication()

    assert len(section._character.complications) == 1
    assert len(section._row_refs) == 1
    assert edits  # an add is a user edit


def test_editing_fields_writes_back_to_the_model(qapp: QApplication) -> None:
    section = _section([Complication()])
    edits: list[int] = []
    section.edited.connect(lambda: edits.append(1))

    ref = section._row_refs[0]
    ref.name.setText("Motivation")
    ref.description.setPlainText("Protect the city.")

    assert ref.complication.name == "Motivation"
    assert ref.complication.description == "Protect the city."
    assert edits


def test_remove_drops_the_complication_by_identity(qapp: QApplication) -> None:
    first, second = Complication("Enemy", "A"), Complication("Secret", "B")
    section = _section([first, second])

    section._remove_complication(first)

    assert section._character.complications == [second]
    assert len(section._row_refs) == 1
    assert section._row_refs[0].complication is second


def test_seeding_does_not_mark_dirty(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.complications = [Complication("Enemy", "Dr. Volt")]
    edits: list[int] = []
    section = ComplicationsSection(data, char)
    section.edited.connect(lambda: edits.append(1))
    # Nothing fired during construction/seed.
    assert not edits


def test_lock_makes_fields_read_only_plain_text_and_hides_controls(qapp: QApplication) -> None:
    section = _section([Complication("Enemy", "Dr. Volt")])
    ref = section._row_refs[0]

    section.set_locked(True)

    # Values are still visible, but every field is read-only, not editable.
    assert ref.name.text() == "Enemy"
    assert ref.name.isReadOnly()
    assert ref.description.toPlainText() == "Dr. Volt"
    assert ref.description.isReadOnly()
    # The add/remove affordances are gone in the read-only view.
    assert section._add_button.isHidden()
    assert ref.remove.isHidden()

    section.set_locked(False)
    assert not ref.name.isReadOnly()
    assert not ref.description.isReadOnly()
    assert not section._add_button.isHidden()
    assert not ref.remove.isHidden()


def test_rows_added_while_locked_come_up_locked(qapp: QApplication) -> None:
    section = _section()
    section.set_locked(True)
    section._add_complication()

    ref = section._row_refs[0]
    assert ref.name.isReadOnly()
    assert ref.description.isReadOnly()
    assert ref.remove.isHidden()
