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

import ast
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ONEFILE = os.environ.get("MMC_ONEFILE") == "1"

# Paths inside a .spec resolve relative to the spec's own directory, so anchor
# everything at the repo root (the parent of installer/).
ROOT = Path(SPECPATH).parent
SRC = str(ROOT / "src")
ICON = str(ROOT / "src" / "mm_companion" / "ui" / "assets" / "mm.ico")

# Bundle exactly the package data the app reads at runtime — no tests, no dev
# tooling, no design. This list must stay a copy of the package-data globs in
# pyproject.toml: everything here is read through importlib.resources, so a file
# these globs miss is simply absent from the built app while working perfectly
# from a source checkout. That is how the theme presets came to be missing —
# "ui/*.json" does not match "ui/theme/themes/classic.json", and nothing failed
# until the installed app had no themes to load.
datas = collect_data_files(
    "mm_companion",
    includes=[
        "data/**/*.json",
        "data/**/*.yaml",
        "data/**/*.yml",
        "ui/*.json",
        "ui/theme/*.json",
        "ui/theme/themes/*.json",
        "ui/assets/*.ico",
        "ui/assets/*.png",
        "ui/assets/*.svg",
        "ui/assets/npc_icons/*.svg",
        "ui/assets/actions/*.svg",
    ],
)


def read_version(init_file):
    """The ``__version__`` string assigned in ``mm_companion/__init__.py``.

    Read from the source rather than imported, so the version stamped on the exe
    is the one in this checkout whatever ``mm_companion`` the build's Python would
    import (an editable install can point at another checkout).
    """
    tree = ast.parse(Path(init_file).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise SystemExit(f"No __version__ assignment found in {init_file}.")


def version_tuple(version):
    """``"0.9.4"`` -> ``(0, 9, 4, 0)``: the four numbers a Windows version holds.

    Only the leading dotted digits count, so a suffix such as ``1.0.0rc1`` still
    reads as ``(1, 0, 0, 0)``.
    """
    match = re.match(r"\d+(?:\.\d+)*", version)
    if not match:
        raise SystemExit(f"__version__ {version!r} does not start with a number.")
    parts = [int(p) for p in match.group(0).split(".")][:4]
    return tuple(parts + [0] * (4 - len(parts)))


def windows_version_info(version, exe_name):
    """The version resource stamped on a Windows exe (Properties > Details).

    SignPath requires the product name and version on every file it signs. Built
    here from ``__version__`` so a version bump needs no second edit.
    """
    from PyInstaller.utils.win32 import versioninfo as vi

    numbers = version_tuple(version)
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            vi.StringFileInfo(
                [
                    vi.StringTable(
                        "040904B0",  # U.S. English, Unicode
                        [
                            vi.StringStruct("ProductName", "MM-Companion"),
                            # Task Manager shows this as the process's name.
                            vi.StringStruct("FileDescription", "MM-Companion"),
                            vi.StringStruct("FileVersion", version),
                            vi.StringStruct("ProductVersion", version),
                            vi.StringStruct("InternalName", exe_name),
                            vi.StringStruct("OriginalFilename", f"{exe_name}.exe"),
                            vi.StringStruct("LegalCopyright", COPYRIGHT),
                        ],
                    )
                ]
            ),
            vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def read_copyright(license_file):
    """The ``Copyright (c) <year> <holder>`` line of the MIT licence."""
    for line in Path(license_file).read_text(encoding="utf-8").splitlines():
        if line.startswith("Copyright"):
            return line.strip()
    raise SystemExit(f"No Copyright line found in {license_file}.")


VERSION = read_version(ROOT / "src" / "mm_companion" / "__init__.py")
COPYRIGHT = read_copyright(ROOT / "LICENSE")


def exe_version(exe_name):
    """``version=`` for ``EXE``: a resource on Windows, nothing elsewhere.

    PyInstaller's versioninfo module needs ``pefile``, which it only installs on
    Windows, and the Linux build ignores a version resource anyway.
    """
    if sys.platform != "win32":
        return None
    return windows_version_info(VERSION, exe_name)


block_cipher = None

# UPX only on Windows: on Linux, packing Qt's shared libraries is a known way to
# get a build that will not start, and the tarball is compressed anyway.
UPX = sys.platform == "win32"

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
        upx=UPX,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
        version=exe_version("MM-Companion-portable"),
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
        upx=UPX,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
        version=exe_version("MM-Companion"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=UPX,
        upx_exclude=[],
        name="MM-Companion",
    )
