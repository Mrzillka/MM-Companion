"""The generated theme-token form: one widget per value shape, and its two guards.

Driven against a synthetic theme carrying one token of each shape, so the dispatch
is tested rather than whichever shapes the bundled presets happen to use today.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QLineEdit,
    QSpinBox,
)

from mm_companion.core import storage
from mm_companion.ui import theme
from mm_companion.ui.settings.color_row import ColorRow, is_valid_color_value
from mm_companion.ui.settings.token_editor import TokenEditor
from mm_companion.ui.theme.loader import available_themes
from mm_companion.ui.theme.tokens import Chrome, Theme


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield tmp_path
    theme.reset()


SAMPLE = Theme(
    id="sample",
    name="Sample",
    chrome=Chrome(mode="system", focus_ring=True),
    colors={
        "accent": "#4488cf",
        "tint.worse": "#d15b5b",
        "border.card": "palette(mid)",
        "text.muted.rich": "gray",
        "_note": "a comment key, not a token",
    },
    metrics={
        "radius.card": 4,
        "opacity.inactive": 0.5,
        "card.margins": [8, 6, 8, 6],
        "column.skill.name": 100,
        "mystery.thing": {"nested": 1},
    },
    typography={"family": None, "size.terms": 8.0},
    blocks={},
)


@pytest.fixture
def editor(qapp):
    widget = TokenEditor()
    widget.load(SAMPLE)
    return widget


def rows(editor) -> dict[tuple[str, str], object]:
    """Every editing widget the form built, keyed by ``(group box, field label)``.

    Read off the form layouts rather than a registry, so the test sees the form a
    user would. Keyed by the enclosing box too because a bare label is not unique
    — ``border.card`` and ``radius.card`` both read "Card", each under its own
    heading.
    """
    from PySide6.QtWidgets import QFormLayout, QLabel

    found: dict[tuple[str, str], object] = {}
    for box in editor.findChildren(QGroupBox):
        form = box.layout()
        if not isinstance(form, QFormLayout):
            continue
        for index in range(form.rowCount()):
            label = form.itemAt(index, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(index, QFormLayout.ItemRole.FieldRole)
            if label is None or field is None:
                continue
            names = label.widget().findChildren(QLabel)
            if names:
                found[(box.title(), names[0].text())] = field.widget()
    return found


# -- the dispatch ---------------------------------------------------------------


def test_a_literal_colour_gets_a_swatch_row(editor) -> None:
    assert isinstance(rows(editor)[("Accent", "Accent")], ColorRow)


def test_a_palette_expression_is_editable_not_rejected(editor) -> None:
    """``palette(mid)`` is how Classic follows the OS; it must stay statable."""
    row = rows(editor)[("Borders", "Card")]

    assert isinstance(row, ColorRow)
    assert row.value() == "palette(mid)"
    assert is_valid_color_value("palette(mid)")
    assert is_valid_color_value("gray")
    assert not is_valid_color_value("mauve-ish")


def test_an_integer_metric_gets_a_spin_box(editor) -> None:
    spin = rows(editor)[("Corner radii", "Card")]

    assert isinstance(spin, QSpinBox)
    assert spin.value() == 4
    assert (spin.minimum(), spin.maximum()) == (0, 64)  # a radius, not a column width


def test_a_fraction_gets_a_fractional_spin_box_bounded_to_its_kind(editor) -> None:
    spin = rows(editor)[("Opacity", "Switched-off card")]

    assert isinstance(spin, QDoubleSpinBox)
    assert (spin.minimum(), spin.maximum()) == (0.0, 1.0)
    assert spin.value() == 0.5


def test_a_four_number_box_gets_four_spin_boxes(editor) -> None:
    holder = rows(editor)[("Power cards", "Margins")]

    spins = holder.findChildren(QSpinBox)
    assert [s.value() for s in spins] == [8, 6, 8, 6]


def test_a_null_font_family_reads_as_the_platform_default(editor) -> None:
    holder = rows(editor)[("Font family", "Font family")]

    default = holder.findChildren(QCheckBox)[0]
    assert default.isChecked()


def test_an_unfamiliar_shape_falls_back_to_json(editor) -> None:
    field = rows(editor)[("Other", "Thing")]

    assert isinstance(field, QLineEdit)
    assert field.text() == '{"nested": 1}'


def test_a_comment_key_gets_no_row(editor) -> None:
    assert not [label for _box, label in rows(editor) if label == "Note"]


def test_chrome_is_its_own_group(editor) -> None:
    titles = {box.title() for box in editor.findChildren(QGroupBox)}

    assert "Chrome" in titles
    assert "Semantic tints" in titles  # a named bucket kept its heading


# -- editing --------------------------------------------------------------------


def test_editing_a_colour_updates_the_draft_and_announces_it(editor) -> None:
    seen = []
    editor.changed.connect(lambda: seen.append(editor.draft().colors["accent"]))

    rows(editor)[("Accent", "Accent")].valueChanged.emit("#010203")

    assert seen == ["#010203"]
    assert editor.draft().colors["accent"] == "#010203"


def test_editing_a_metric_updates_the_draft(editor) -> None:
    spin = rows(editor)[("Column minimums", "Skill name")]

    spin.setValue(123)

    assert editor.draft().metrics["column.skill.name"] == 123


def test_a_box_writes_all_four_numbers_back(editor) -> None:
    spins = rows(editor)[("Power cards", "Margins")].findChildren(QSpinBox)

    spins[2].setValue(20)

    assert editor.draft().metrics["card.margins"] == [8, 6, 20, 6]


def test_seeding_the_form_does_not_count_as_an_edit(qapp) -> None:
    widget = TokenEditor()
    seen = []
    widget.changed.connect(lambda: seen.append(1))

    widget.load(SAMPLE)

    assert seen == []


# -- the guards -----------------------------------------------------------------


def test_a_washed_token_refuses_a_palette_expression(editor) -> None:
    """``theme.wash`` would raise on one, deep inside a card's paint path."""
    row = rows(editor)[("Semantic tints", "Worse")]
    seen = []
    editor.changed.connect(lambda: seen.append(1))

    row.valueChanged.emit("palette(mid)")

    assert seen == []  # not passed on
    assert editor.draft().colors["tint.worse"] == "#d15b5b"  # nor kept
    assert row.value() == "#d15b5b"  # nor left on screen


def test_an_illegible_tint_warns_but_is_accepted(editor) -> None:
    row = rows(editor)[("Semantic tints", "Worse")]

    row.valueChanged.emit("#efefef")  # near-white, on a light window

    assert editor.draft().colors["tint.worse"] == "#efefef"
    assert "below" in row._warning.text()


def test_a_legible_tint_carries_no_warning(editor) -> None:
    """Classic's own red clears the floor on a light *and* a dark window."""
    row = rows(editor)[("Semantic tints", "Worse")]
    row.valueChanged.emit("#efefef")

    row.valueChanged.emit("#d15b5b")

    assert row._warning.text() == ""


def test_flipping_to_styled_without_surfaces_asks_instead_of_crashing(editor) -> None:
    """qss._chrome_rules requires surface.* with no fallback; Classic has none."""
    asked = []
    editor.styledSurfacesNeeded.connect(lambda: asked.append(1))

    editor._set_chrome_mode("styled")

    assert asked == [1]
    assert editor.draft().chrome.mode == "system"


def test_flipping_to_styled_is_allowed_once_surfaces_exist(qapp) -> None:
    widget = TokenEditor()
    widget.load(available_themes()["slate-dark"])

    widget._set_chrome_mode("system")

    assert widget.draft().chrome.mode == "system"


# -- locking --------------------------------------------------------------------


def test_a_locked_form_shows_its_values_without_editing_them(editor) -> None:
    editor.set_locked(True)

    spin = rows(editor)[("Column minimums", "Skill name")]
    assert spin.isReadOnly()
    assert spin.isEnabled()  # still legible, not greyed out
    assert spin.value() == 100

    field, swatch = rows(editor)[("Accent", "Accent")].editable_widgets()
    assert field.isReadOnly()  # the hex stays readable
    assert not swatch.isEnabled()  # but the picker cannot be opened
