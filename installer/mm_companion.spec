# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MM-Companion.

Produces one of two layouts from a single spec, chosen by the ``MMC_ONEFILE``
environment variable (set by ``installer/build.ps1``):

* unset (default) -> one-folder build in ``dist/MM-Companion/`` (fast start; the
  installer copies this folder to the chosen install dir).
* ``MMC_ONEFILE=1`` -> single self-extracting ``dist/MM-Companion-portable.exe``
  used for the "Portable" install option.

All game data / UI JSON / the window icon are loaded at runtime through
``importlib.resources`` on the ``mm_companion`` package, so they must be bundled
explicitly here; PySide6's own hooks cover the Qt DLLs and plugins.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ONEFILE = os.environ.get("MMC_ONEFILE") == "1"

# Paths inside a .spec resolve relative to the spec's own directory, so anchor
# everything at the repo root (the parent of installer/).
ROOT = Path(SPECPATH).parent
SRC = str(ROOT / "src")
ICON = str(ROOT / "src" / "mm_companion" / "ui" / "assets" / "mm.ico")

# Bundle exactly the package data the app reads at runtime (mirrors the
# package-data globs in pyproject.toml) — no tests, no dev tooling, no design.
datas = collect_data_files(
    "mm_companion",
    includes=["data/**/*.json", "ui/*.json", "ui/assets/*.ico"],
)

block_cipher = None

a = Analysis(
    [str(ROOT / "src" / "mm_companion" / "__main__.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "black", "ruff", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="MM-Companion-portable",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="MM-Companion",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="MM-Companion",
    )
