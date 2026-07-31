"""The built app ships every data file the source checkout reads.

Everything under ``data/`` and the JSON under ``ui/`` is read through
``importlib.resources``, which resolves happily from a source tree whether or not
the packaging config knows about the file. So a glob that misses something fails
*only* in the built installer, and only at the moment a user opens the feature it
belongs to — the theme presets shipped missing this way, because ``ui/*.json``
does not match ``ui/theme/themes/classic.json``.

Two lists have to agree for a file to reach a user: the ``package-data`` globs in
``pyproject.toml`` (the wheel) and the ``collect_data_files`` includes in
``installer/mm_companion.spec`` (the Windows build). These tests hold them to each
other and to what is actually on disk. No Qt, no PyInstaller import — the spec is
read as source, since it is only executable inside PyInstaller.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SPEC = ROOT / "installer" / "mm_companion.spec"
PACKAGE = ROOT / "src" / "mm_companion"


def package_data_globs() -> list[str]:
    """The ``mm_companion = [...]`` package-data list from ``pyproject.toml``.

    Read with a regex rather than ``tomllib``, which is 3.11+ while this project
    supports 3.10 — and a TOML parser is not worth a dependency for one line.
    """
    match = re.search(r"^mm_companion = (\[[^\]]*\])", PYPROJECT.read_text("utf-8"), re.MULTILINE)
    assert match, "the package-data list is not where this test expects it"
    return ast.literal_eval(match.group(1))


def spec_globs() -> list[str]:
    """The ``includes=[...]`` argument of the spec's ``collect_data_files`` call."""
    tree = ast.parse(SPEC.read_text("utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "collect_data_files":
            continue
        for keyword in node.keywords:
            if keyword.arg == "includes":
                return ast.literal_eval(keyword.value)
    raise AssertionError("no collect_data_files(includes=...) call in the spec")


def test_the_installer_and_the_wheel_ship_the_same_files() -> None:
    assert sorted(spec_globs()) == sorted(package_data_globs())


def test_every_bundled_data_file_is_covered_by_a_glob() -> None:
    """Nothing readable at runtime is left out of the packaging globs.

    Where the test above catches the two lists drifting apart, this one catches a
    file neither list ever knew about — a new asset dropped in beside its module
    and read straight away, which works in the checkout and nowhere else.
    """
    # Expanded with Path.glob rather than Path.match: only glob gives "**" its
    # recursive meaning, and every one of these patterns leans on it.
    covered = {path for pattern in package_data_globs() for path in PACKAGE.glob(pattern)}
    on_disk = {
        path
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and path.suffix in {".json", ".yaml", ".yml", ".ico", ".png", ".svg"}
        and "__pycache__" not in path.parts
    }
    uncovered = sorted(str(path.relative_to(PACKAGE)) for path in on_disk - covered)
    assert uncovered == []
