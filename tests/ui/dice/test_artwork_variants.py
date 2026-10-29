"""The artwork the theme picks: the variant registries and the ``assets`` tokens.

Two layers meet here and the split is the thing being pinned down. The registries
in :mod:`mm_companion.ui.svg_assets` own *what drawings exist*; a preset's
``assets`` map owns *which one this theme wants*, by id. A preset can therefore
never name a file, and a preset that names a variant the app has never heard of
falls back rather than raising in the middle of a paint.
"""

from __future__ import annotations

import json
from importlib.resources import files

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.ui import svg_assets, theme
from mm_companion.ui.theme import loader


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield tmp_path
    theme.reset()


def write_preset(root, name: str, body: dict) -> None:
    directory = root / "themes"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def _resources() -> list[str]:
    pips = [path for pair in svg_assets.HERO_POINT_VARIANTS.values() for path in pair]
    return list(svg_assets.DIE_VARIANTS.values()) + pips


# -- the registries -------------------------------------------------------------


@pytest.mark.parametrize("resource", _resources())
def test_every_variant_names_a_bundled_file(resource: str) -> None:
    assert files(svg_assets.RESOURCE_PACKAGE).joinpath(resource).is_file()


@pytest.mark.parametrize("resource", _resources())
def test_every_variant_actually_renders(resource: str, qapp: QApplication) -> None:
    """An unparseable SVG costs no error — it silently leaves the pixmap blank.

    ``QSvgRenderer`` reports a load failure through a return value nothing here
    checks, so "it did not raise" proves nothing. Comparing against a blank
    pixmap of the same size is what catches a drawing that never arrived.
    """
    blank = QPixmap(QSize(32, 32))
    blank.fill(Qt.GlobalColor.transparent)

    pixmap = svg_assets.svg_pixmap(resource, QSize(32, 32))

    assert not pixmap.isNull()
    assert pixmap.toImage() != blank.toImage()


def test_the_two_pips_of_a_pair_do_not_look_alike(qapp: QApplication) -> None:
    """A held point has to read as different from a spent one, in either pair."""
    for variant in svg_assets.HERO_POINT_VARIANTS:
        held = svg_assets.hero_point_pixmap(True, 26, variant=variant).toImage()
        spent = svg_assets.hero_point_pixmap(False, 26, variant=variant).toImage()
        assert held != spent, variant


def test_an_unknown_variant_falls_back_rather_than_raising() -> None:
    """These resolve inside a paint path, where a KeyError is a crash on screen."""
    assert svg_assets.die_resource("no-such-die") == svg_assets.DIE_VARIANTS[svg_assets.DEFAULT_DIE]
    assert (
        svg_assets.hero_point_resources(None)
        == svg_assets.HERO_POINT_VARIANTS[svg_assets.DEFAULT_HERO_POINT]
    )


# -- what the presets ask for ---------------------------------------------------


def test_every_bundled_preset_names_variants_that_exist() -> None:
    for preset in loader.available_themes().values():
        assert preset.assets["die"] in svg_assets.DIE_VARIANTS, preset.id
        assert preset.assets["hero-point"] in svg_assets.HERO_POINT_VARIANTS, preset.id


def test_classic_keeps_the_outlined_artwork_and_the_rest_take_the_medallion() -> None:
    presets = loader.available_themes()

    assert presets["classic"].assets == {"die": "classic", "hero-point": "classic"}
    for theme_id in ("slate-dark", "parchment-light", "crimson-gold"):
        assert presets[theme_id].assets["hero-point"] == "medallion", theme_id


def test_a_preset_inherits_the_artwork_it_does_not_restate(isolated_workspace) -> None:
    write_preset(isolated_workspace, "child", {"id": "child", "extends": "slate-dark"})
    theme.reset()

    assert loader.available_themes()["child"].assets["die"] == "gradient"


def test_a_preset_with_no_artwork_at_all_resolves_through_classic(
    isolated_workspace,
) -> None:
    """A snapshot saved before these tokens existed has no ``assets`` map.

    Those are written with no ``extends``, so nothing upstream can supply the
    value — the default-preset fallback in ``theme._lookup`` is the only thing
    standing between such a file and an ``UnknownToken`` on the user's machine.
    """
    write_preset(isolated_workspace, "old", {"id": "old", "name": "Old"})
    storage.update_settings(theme="old")
    theme.reset()

    assert theme.asset("die") == "classic"


# -- what the widgets draw ------------------------------------------------------


def _use(theme_id: str) -> None:
    storage.update_settings(theme=theme_id)
    theme.reset()


def test_the_die_drawn_follows_the_active_preset(qapp: QApplication) -> None:
    from mm_companion.ui.dice_roller import d20_pixmap

    _use("classic")
    outlined = d20_pixmap().toImage()
    _use("crimson-gold")
    shaded = d20_pixmap().toImage()

    assert outlined != shaded


def test_the_pips_drawn_follow_the_active_preset(qapp: QApplication) -> None:
    """Read inside ``_render``, not held from ``__init__``.

    A widget built before the switch and rebuilt after it has to pick the new
    artwork up — that rebuild is the only thing carrying the change, since a
    stylesheet cannot reach an icon already set on a button.
    """
    from mm_companion.ui.sections.system_info import HeroPointsWidget

    _use("classic")
    widget = HeroPointsWidget()
    widget.set_value(1)
    size = QSize(widget._pip_size, widget._pip_size)
    outlined = widget._buttons[0].icon().pixmap(size).toImage()

    _use("crimson-gold")
    widget = HeroPointsWidget()
    widget.set_value(1)
    medallion = widget._buttons[0].icon().pixmap(size).toImage()

    assert outlined != medallion
