---
name: build-installer
description: Bump the MM-Companion version and build a new shareable Windows installer (.exe) into installer/output/. Use when asked to create/build/make a new installer, cut a release, ship a new version, or bump the version — for a minor update (+1 last digit, 0.1.2->0.1.3) or a major update (+1 second digit, 0.1.2->0.2.0).
---

# Build a new MM-Companion installer

Bumps the app version and builds a fresh, shareable Windows installer named after
the new version, e.g. `installer/output/MM-Companion-Setup-0.1.3.exe`.

Driven by the committed **`build_installer.py`**, which bumps `__version__`, runs
the two-stage build (PyInstaller freezes the app, Inno Setup wraps it), verifies
the artifact, and — if the build fails — rolls the version back so the repo is
never left half-bumped. It is the only path; don't edit the version and run the
build by hand.

**Bump levels** (versions are 3-part SemVer `X.Y.Z`):

| level | effect | example |
| --- | --- | --- |
| `minor` | +1 to the **last** digit | `0.1.2` → `0.1.3` |
| `major` | +1 to the **second** digit, last reset to 0 | `0.1.2` → `0.2.0` |

**Ask the user which level** (minor or major) if they didn't say, then run the
driver. After it finishes, tell them which was done and the new version — the
driver prints exactly that (e.g. `MINOR update complete: 0.1.2 -> 0.1.3`).

All paths below are relative to the repo root. The driver lives at
`.claude/skills/build-installer/build_installer.py`.

## Prerequisites (one-time, on the build machine)

- The project venv with build tools:
  ```bash
  python -m pip install -e ".[dev]" pyinstaller
  ```
- **Inno Setup 6** (provides `ISCC.exe`). Install once:
  ```bash
  winget install --id JRSoftware.InnoSetup --accept-source-agreements --accept-package-agreements --silent
  ```
  It lands at `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`, which the build
  script auto-detects (it is **not** on PATH — that's expected).

## Run (agent path)

Run the driver with the **venv Python** and the chosen level. It passes its own
interpreter to the build, so the freeze always uses the right deps:

```bash
# preview the version change only — no files touched, no build:
python .claude/skills/build-installer/build_installer.py minor --dry-run
python .claude/skills/build-installer/build_installer.py major --dry-run

# do it for real (bump + full build, ~2-3 min):
python .claude/skills/build-installer/build_installer.py minor
python .claude/skills/build-installer/build_installer.py major
```

On success it prints a summary and leaves the installer at
`installer/output/MM-Companion-Setup-<new-version>.exe`, with `__version__`
updated in `src/mm_companion/__init__.py`. That version bump is **not committed** —
commit it if the user wants to keep the release; otherwise `git checkout
src/mm_companion/__init__.py` reverts it (but keep the installer if they want it).

`installer/output/` is **git-ignored** — do not commit the `.exe` (each ~80 MB
build would bloat history permanently). Publish the built installer as a
**GitHub Release asset** instead (`gh release create <tag> <path-to-exe>`).

Verified end-to-end this session: `minor` bumped `0.1.0 → 0.1.1`, produced a
78 MB `MM-Companion-Setup-0.1.1.exe`; a silent install of it
(`MM-Companion-Setup-0.1.1.exe /VERYSILENT /DIR=<dir>`) placed a runnable app,
wrote the `DisplayVersion=0.1.1` uninstall registry key, and the silent
uninstaller (`unins000.exe /VERYSILENT /DELDATA=0`) removed both.

## What the driver does

1. Reads `__version__` from `src/mm_companion/__init__.py` (the single source of
   truth; `pyproject.toml` derives its version from it).
2. Computes the new version for the chosen level and writes it back.
3. Runs `installer/build.ps1 -PythonExe <this python>` (PowerShell): cleans
   `build/ dist/ installer/output/`, freezes the app with PyInstaller twice
   (one-folder + one-file portable), then compiles the installer with Inno Setup.
4. Confirms `installer/output/MM-Companion-Setup-<new-version>.exe` exists and
   prints the summary. On any build failure it restores the original version.

## Gotchas

- **Run with the venv's Python.** The driver freezes the app with
  `sys.executable`; if you launch it with some other interpreter, PyInstaller
  bundles that environment's packages. On this machine plain `python` already
  resolves to the venv.
- **`ISCC.exe` is not on PATH** even after install — it lives under
  `C:\Program Files (x86)\Inno Setup 6\`. `build.ps1` probes that path; pass a
  custom one with `build.ps1 -Iscc <path>` if yours differs.
- **Full build is slow (~2-3 min)** — two PyInstaller passes plus LZMA
  compression of ~150 MB of Qt. Use `--dry-run` when you only need the number.
- **First digit (`X`) is never bumped** here — only `minor`/`major` are defined
  (per the project's versioning scheme). A `1.0.0` release is a manual edit.
- **The `.iss` uses fixed AppId `{4E9C2EF5-…}`** — never change it; it's how the
  installer recognizes a prior install across versions. See `docs/packaging.md`.
- **Testing an installer from Git Bash**: prefix `ISCC.exe`/`.exe` calls with
  `MSYS_NO_PATHCONV=1`, or arguments like `/DAppVersion=…` get mangled into
  Windows paths (`build.ps1` runs under PowerShell, so it's immune).

## Troubleshooting

- `No 3-part __version__ = "X.Y.Z" found` — `src/mm_companion/__init__.py` isn't a
  plain `X.Y.Z`; the driver only bumps 3-part SemVer.
- `Inno Setup compilation failed` / `ISCC.exe not found` — install Inno Setup 6
  (see Prerequisites) or pass `-Iscc`. The driver will have restored the version.
- `No module named PyInstaller` — `pip install pyinstaller` into the venv.
- Build succeeds but the `.exe` is missing — check `installer/build.ps1` output;
  the PyInstaller `dist/` names must match what the `.iss` `[Files]` section
  expects (`dist/MM-Companion/`, `dist/MM-Companion-portable.exe`).
