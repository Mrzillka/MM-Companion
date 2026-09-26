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
2. PyInstaller 6: `pip install "pyinstaller>=6,<7"` (the in-app update relies on
   6.x's one-folder layout to tell it from a one-file build).
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
workspace's `mods\` folder — either from a folder, or from a `.zip`, which is how
a mod normally ships. **The installer bundles no mods**, deliberately: a mod
versions on its own cadence, and one that arrived with an upgrade would be one the
user never chose. A mod's own saved state lives beside them in `mod_state\`, and
survives the mod being removed and reinstalled.

## In-app updates

The app checks GitHub's latest release at startup (unless turned off in Settings →
General), and an installed build updates itself: it downloads the release's
`MM-Companion-Setup-<version>.exe`, verifies it against the SHA-256 GitHub
publishes, and runs it with

```
/SILENT /NOCANCEL /SUPPRESSMSGBOXES /NORESTART /LOG=<workspace>\logs\update-<from>-to-<to>-<time>.log
/READYFILE=<path> /CANCELFILE=<path> /WAITPID=<pid>[,<pid>] /RELAUNCH=1
```

The last four are this project's own switches, read in `mm_companion.iss`:

| Switch | What Setup does with it |
| --- | --- |
| `/CANCELFILE` | If it exists when Setup starts, the app gave up waiting for the permission prompt: leave, touching nothing. Checked *before* the ready file is written. |
| `/READYFILE` | Written once Setup is running elevated — the app's cue to quit. |
| `/WAITPID` | Waited out (30 s each) before anything is replaced. One that outlives it means nothing is installed. |
| `/RELAUNCH=1` | Start the app again at the end, as the original user, **whether or not the install worked** — the relaunched app reads its own version to tell which, and shows the log on a failure. |

The silent run takes the existing Upgrade path, so it keeps the install folder and
the tasks (desktop shortcut, portable) chosen the first time. Every step it takes,
and every error, goes to the `/LOG` file in the workspace's `logs\` folder.

So a release must keep: **the asset name** `MM-Companion-Setup-<version>.exe`, the
**`v<version>` tag**, and those switches in the script. See
[the launcher notes](notes/workspace-and-files.md) for the app side.

**Trying it without publishing.** The app doing the updating must already have
this code, so it takes two builds of the branch: install one, then offer it the
other. Keep both versions just above the latest real release (say `0.7.11` and
`0.7.12`) — a test build installed as `9.9.9` would outrank every real release,
and the next real installer would only offer to Reinstall. Mind that the test
build outranks every real release *below* it too: after testing with `0.7.12`,
uninstall it before installing a real `0.7.2`, or release `0.8.0` next.

1. Set `__version__` to `0.7.11`, run `build.ps1`, and install that.
2. Set it to `0.7.12`, run `build.ps1` again, and serve the result with a
   hand-written release document:

   ```powershell
   cd installer\output
   $exe = "MM-Companion-Setup-0.7.12.exe"
   $sha = (Get-FileHash $exe).Hash.ToLower()
   $size = (Get-Item $exe).Length
   @"
   {"tag_name": "v0.7.12", "html_url": "http://127.0.0.1:8000/",
    "assets": [{"name": "$exe", "size": $size, "digest": "sha256:$sha",
                "browser_download_url": "http://127.0.0.1:8000/$exe"}]}
   "@ | Set-Content latest.json
   python -m http.server 8000
   ```
3. In another shell, start the *installed* app pointed at it:

   ```powershell
   $env:MM_COMPANION_UPDATE_FEED = "http://127.0.0.1:8000/latest.json"
   & "C:\Program Files\MM-Companion\MM-Companion.exe"
   ```

   The badge should offer v0.7.12; Update downloads it, asks for permission, and
   the app comes back as 0.7.12. Put `__version__` back before committing.

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
