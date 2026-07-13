# Packaging: building the Windows installer

This produces a single shareable `MM-Companion-Setup-<version>.exe` that installs
the app on a Windows PC with no Python required. The app itself is frozen with
[PyInstaller]; the installer is built with [Inno Setup].

Everything lives under `installer/`:

| File | Purpose |
| --- | --- |
| `mm_companion.spec` | PyInstaller spec — freezes the app (bundles game data + `mm.ico`). |
| `mm_companion.iss` | Inno Setup script — the installer UI and logic. |
| `build.ps1` | One command that runs PyInstaller (twice) then Inno Setup. |

## Prerequisites (build machine only)

1. A working project virtualenv: `pip install -e ".[dev]"`.
2. PyInstaller: `pip install pyinstaller`.
3. [Inno Setup 6](https://jrsoftware.org/isdl.php) (installs `ISCC.exe`, usually
   at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`).

## Build

From the repo root, inside the venv:

```powershell
pwsh installer\build.ps1
```

The script:

1. reads the version from `mm_companion.__version__`,
2. builds `dist\MM-Companion\` (one-folder, the default install) and
   `dist\MM-Companion-portable.exe` (one-file, for the Portable option),
3. compiles `installer\output\MM-Companion-Setup-<version>.exe` — the file to
   share.

If Inno Setup is installed somewhere unusual, pass its path:
`pwsh installer\build.ps1 -Iscc "D:\Tools\Inno Setup 6\ISCC.exe"`.

## What the installer does

- **Fresh machine** — the user picks an install directory, can tick a desktop
  shortcut, and can tick a **Portable** install (a single exe that keeps its
  workspace next to itself instead of in `%APPDATA%`).
- **Already installed** (detected via the registry uninstall key) — a page
  offers **Upgrade / Reinstall / Remove**. **Upgrade** only appears when the
  installed version is older than the installer's. Upgrade/Reinstall reuse the
  existing install directory.
- **Remove** — runs the app's uninstaller. A checkbox on that page (and a prompt
  in the standalone Programs-&-Features uninstaller) additionally deletes the
  user workspace at `%APPDATA%\MM-Companion` (characters, mods, settings).

User data (settings, saved characters, installed mods) always lives in the
per-user workspace — `%APPDATA%\MM-Companion` for a normal install, or a `data\`
folder beside the exe for a Portable install — so it is never overwritten by an
upgrade. Mods are installed at runtime through the in-app Mod Manager into that
workspace's `mods\` folder.

## Cutting a release

1. Bump `__version__` in `src/mm_companion/__init__.py` (this is the single
   source of truth — `pyproject.toml` derives its version from it).
2. Re-run `pwsh installer\build.ps1`.

Versions are stored as SemVer so the upgrade check orders them correctly. Map
your shorthand accordingly:

| shorthand | stored |
| --- | --- |
| 0.1 | 0.1.0 |
| 0.11 | 0.1.1 |
| 0.12 | 0.1.2 |
| 0.2 | 0.2.0 |

The fixed `AppId` GUID in `mm_companion.iss` must **never** change — it is how
the installer recognizes a prior installation across releases.

[PyInstaller]: https://pyinstaller.org/
[Inno Setup]: https://jrsoftware.org/isinfo.php
