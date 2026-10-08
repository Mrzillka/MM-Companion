#!/usr/bin/env bash
# Build the shareable MM-Companion Linux release tarball.
#
# One command that:
#   1. reads the single-sourced version from mm_companion.__version__,
#   2. freezes the app with PyInstaller (one-folder, the same spec as Windows),
#   3. adds installer/linux/ (install.sh, the menu icon, INSTALL.txt) and the
#      licences beside the frozen app, and packs it as
#      installer/output/MM-Companion-<version>-linux-<arch>.tar.gz.
#
# Run from anywhere, inside the project's virtualenv:
#     bash installer/build-linux.sh
# Requires: pip install "pyinstaller>=6,<7". PYTHON overrides the interpreter.
#
# The frozen app carries its own Qt, but links against the build machine's glibc,
# so it runs only on distributions at least as new as the one it was built on.
# The release workflow builds on the oldest Ubuntu runner for that reason.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PYTHON=${PYTHON:-python3}

echo "==> Reading version from mm_companion.__version__"
VERSION=$("$PYTHON" -c "import mm_companion, sys; sys.stdout.write(mm_companion.__version__)")
[ -n "$VERSION" ] || { echo "Could not read mm_companion.__version__" >&2; exit 1; }
ARCH=$(uname -m)
NAME="MM-Companion-$VERSION-linux-$ARCH"
echo "    version = $VERSION, arch = $ARCH"

echo "==> Cleaning previous build artifacts"
rm -rf build dist installer/output

echo "==> PyInstaller: one-folder build"
unset MMC_ONEFILE
"$PYTHON" -m PyInstaller --noconfirm --clean installer/mm_companion.spec
APP="dist/MM-Companion"
[ -x "$APP/MM-Companion" ] || { echo "Expected $APP/MM-Companion was not produced." >&2; exit 1; }

echo "==> Assembling the release folder"
cp installer/linux/install.sh installer/linux/INSTALL.txt installer/linux/mm-companion.png "$APP/"
cp LICENSE LICENSE-CONTENT.md "$APP/"
chmod 755 "$APP/install.sh" "$APP/MM-Companion"
printf '%s\n' "$VERSION" >"$APP/VERSION"

echo "==> Packing"
mkdir -p installer/output
OUTPUT="installer/output/$NAME.tar.gz"
# The folder inside the archive keeps the plain name, so the steps in INSTALL.txt
# read the same for every version.
tar -C dist -czf "$OUTPUT" --owner=0 --group=0 MM-Companion
echo "==> Done: $OUTPUT"
