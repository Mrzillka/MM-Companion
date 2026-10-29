"""Fixtures every test shares — and none of them needs Qt.

This file pins the Qt platform for the whole run, because that has to happen before
anything imports PySide6, but it never imports PySide6 itself: ``tests/core``,
``tests/session`` and ``tests/app`` run without Qt loaded at all. Everything about
widgets lives in ``tests/ui/conftest.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


def _offscreen_platform() -> str:
    """Qt's offscreen platform on the one 1280x1024 screen ``qt/screens.json`` describes.

    The path is relative because Qt splits the platform string on ``:``, which an
    absolute Windows path's drive letter would cut in half (Qt crashes on it). Qt
    reads it when the QApplication is made, from the directory pytest runs in.
    """
    try:
        config = os.path.relpath(Path(__file__).parent / "qt" / "screens.json")
    except ValueError:  # pragma: no cover - working directory on another drive
        return "offscreen"
    return f"offscreen:configfile={Path(config).as_posix()}"


# The same Qt on every machine, set before any test module imports PySide6. The
# offscreen platform has no window manager, so no window appears on the developer's
# desktop and none is clamped to the size of their monitor; the screen it pretends
# to have is the one CI's xvfb used to. Its font database reads only QT_QPA_FONTDIR,
# which holds the DejaVu files CI's runner used to get from its system — so every
# text width a test measures is the same here as on CI. Set QT_QPA_PLATFORM yourself
# to watch a test run on a real platform; the pins then mostly stop applying.
os.environ.setdefault("QT_QPA_PLATFORM", _offscreen_platform())
os.environ.setdefault("QT_QPA_FONTDIR", str(Path(__file__).parent / "qt" / "fonts"))

import pytest

from mm_companion.core import storage
from mm_companion.core.session.relay import RELAY_SCHEME_PLAIN
from mm_companion.relay import RelayServer


@dataclass
class RelayBox:
    """A real relay on loopback, for tests about hosting through one."""

    server: RelayServer
    base: str


@pytest.fixture
def relay_box():
    """A plaintext relay on an ephemeral loopback port, running in a thread.

    Plaintext because a TLS relay needs a certificate; the encrypted path is
    covered end to end in ``test_session_relay.py``.
    """
    server = RelayServer("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.address
    yield RelayBox(server, f"{RELAY_SCHEME_PLAIN}://{host}:{port}")
    server.stop()
    thread.join(timeout=5.0)


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """Give every test a throwaway workspace.

    Otherwise a test reads — and writes — the developer's real workspace. Both
    directions bite: ``library.save_character`` leaves files in their actual
    ``characters/`` dir, and a preset they happen to have chosen decides what
    colour the sheet paints a penalty, so an assertion about a tint passes or
    fails depending on who runs it. Neither shows up on CI, where the workspace
    is always empty, which is exactly what makes it worth pinning here. (The
    theme cache that has to follow the workspace is reset in ``tests/ui``.)

    A test file wanting its own workspace still sets the variable itself; a
    module-level autouse fixture runs after this one and wins. This is the floor.
    """
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path / "workspace"))
    yield


def _git_for_windows_openssl() -> str | None:
    """The openssl Git for Windows ships, which a Windows box rarely has on PATH."""
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parent.parent  # …/Git/cmd/git.exe or …/Git/mingw64/bin/git.exe
    for candidate in (root / "usr/bin/openssl.exe", root.parent / "usr/bin/openssl.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory):
    """A throwaway self-signed certificate for ``localhost``.

    Generated rather than checked in — a private key in the repository is a
    liability, and a checked-in certificate expires. Skips where openssl is not
    installed; CI has it.
    """
    openssl = shutil.which("openssl") or _git_for_windows_openssl()
    if openssl is None:  # pragma: no cover - depends on the machine
        pytest.skip("openssl is not installed")
    directory = tmp_path_factory.mktemp("relay-tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    result = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "3650",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        capture_output=True,
    )
    if result.returncode != 0:  # pragma: no cover - depends on the machine
        pytest.skip(f"openssl could not make a certificate: {result.stderr.decode()[:200]}")
    return cert, key
