"""Is there a newer release than the one running — and installing it.

The app's releases are GitHub Releases on the project repository, tagged
``v<version>`` with ``<version>`` the SemVer in ``mm_companion.__version__`` (the
release workflow refuses a tag that disagrees with it), each carrying one asset,
the Inno Setup installer ``MM-Companion-Setup-<version>.exe``. So:

- **Checking** is one request to GitHub's "latest release" endpoint — which
  already skips drafts and pre-releases — and a comparison of that tag against the
  running version. Every failure (offline, rate-limited, a malformed reply) comes
  back as ``None``: "we could not tell" and "you are up to date" look the same to
  someone opening the app, and neither is worth an error dialog.
- **Updating** downloads that installer (checked against the size and SHA-256
  GitHub publishes for it) and runs it silently over the install this app was
  started from. The installer is told three things on its command line, all read by
  ``installer/mm_companion.iss``: ``/READYFILE`` — a file to create once Windows
  has let it elevate, which is the app's cue to quit; ``/WAITPID`` — the processes
  to wait out before it touches a file, since the running app holds its own exe
  open; and ``/RELAUNCH=1`` — start the app again when it is done.

Pure Python and stdlib only, like the rest of ``core``: the launcher runs these on
worker threads and renders the answers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mm_companion import __version__

#: The repository whose releases are this app's releases.
REPOSITORY = "Mrzillka/MM-Companion"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"

#: Points the check at another "latest release" document — a local server serving a
#: hand-made one is how the whole update path is tried out without publishing.
FEED_ENV_VAR = "MM_COMPANION_UPDATE_FEED"

#: Long enough for a slow connection, short enough that a dead one does not keep a
#: worker thread alive for the rest of the session.
DEFAULT_TIMEOUT = 5.0
#: Per read while downloading: a stalled transfer fails rather than hanging.
DOWNLOAD_TIMEOUT = 30.0
_CHUNK = 64 * 1024

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


class UpdateError(Exception):
    """The update could not be downloaded or started; the message says why."""


class UpdateCancelled(UpdateError):
    """The user stopped the download."""


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable file of a release."""

    name: str
    url: str
    size: int = 0
    #: Lower-case hex SHA-256, when GitHub published one.
    sha256: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    """A published release: its version, the page to read about it, its assets."""

    version: str
    tag: str
    page_url: str
    assets: dict[str, ReleaseAsset] = field(default_factory=dict)

    @property
    def installer(self) -> ReleaseAsset | None:
        """The Windows installer, if this release has one."""
        return self.assets.get(f"MM-Companion-Setup-{self.version}.exe")


def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v0.7.1"`` → ``(0, 7, 1)``; ``None`` for anything that is not a version.

    Trailing zeros are dropped so ``0.8`` and ``0.8.0`` compare equal, and anything
    after the numeric part (``-rc1``, ``+build``) is ignored — a pre-release never
    reaches us through the "latest" endpoint anyway.
    """
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """Whether version *candidate* is strictly later than *current*.

    An unparseable version on either side is never newer: offering an "update"
    we cannot order would risk offering a downgrade.
    """
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    return new > old


def _asset_from_json(entry: object) -> ReleaseAsset | None:
    if not isinstance(entry, dict):
        return None
    name, url = entry.get("name"), entry.get("browser_download_url")
    if not isinstance(name, str) or not isinstance(url, str):
        return None
    size = entry.get("size")
    digest = entry.get("digest")
    sha256 = ""
    if isinstance(digest, str) and digest.lower().startswith("sha256:"):
        sha256 = digest[len("sha256:") :].lower()
    return ReleaseAsset(
        name=name, url=url, size=size if isinstance(size, int) else 0, sha256=sha256
    )


def release_from_json(payload: dict) -> ReleaseInfo | None:
    """Read GitHub's release object; ``None`` if it is not a usable release."""
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None
    assets = [_asset_from_json(entry) for entry in payload.get("assets") or []]
    page_url = payload.get("html_url")
    return ReleaseInfo(
        version=tag.lstrip("v"),
        tag=tag,
        page_url=page_url if isinstance(page_url, str) else RELEASES_PAGE_URL,
        assets={asset.name: asset for asset in assets if asset is not None},
    )


def _request(url: str, accept: str) -> urllib.request.Request:
    # GitHub rejects API requests with no User-Agent.
    return urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": f"MM-Companion/{__version__}"}
    )


def fetch_latest_release(timeout: float = DEFAULT_TIMEOUT) -> ReleaseInfo | None:
    """The newest published release, or ``None`` if it could not be fetched."""
    url = os.environ.get(FEED_ENV_VAR) or LATEST_RELEASE_URL
    try:
        with urllib.request.urlopen(
            _request(url, "application/vnd.github+json"), timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        # OSError covers URLError/HTTPError and timeouts; ValueError bad JSON.
        return None
    if not isinstance(payload, dict):
        return None
    return release_from_json(payload)


def check_for_update(
    current: str = __version__, timeout: float = DEFAULT_TIMEOUT
) -> ReleaseInfo | None:
    """The latest release if it is newer than *current*, else ``None``."""
    release = fetch_latest_release(timeout)
    if release is None or not is_newer(release.version, current):
        return None
    return release


# -- installing ----------------------------------------------------------------


def install_dir() -> Path | None:
    """The folder the installer put this app in, or ``None`` if it did not.

    Only a frozen Windows build can be updated in place, and only one Inno Setup
    installed: its uninstaller (``unins000.exe``) sits beside the exe. A copy run
    from anywhere else — a portable exe carried off on a stick, or a checkout run
    from source — would have the installer upgrade the *registered* install rather
    than the one running, so it is sent to the release page instead.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    if not any(folder.glob("unins*.exe")):
        return None
    return folder


def can_self_update() -> bool:
    """Whether :func:`launch_installer` can update the running app in place."""
    return install_dir() is not None


def download_dir() -> Path:
    """Where installers are downloaded to; kept, so a retry need not fetch again."""
    return Path(tempfile.gettempdir()) / "MM-Companion-update"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_intact(path: Path, asset: ReleaseAsset) -> bool:
    """Whether *path* is exactly the file GitHub published as *asset*."""
    if asset.size and path.stat().st_size != asset.size:
        return False
    return not asset.sha256 or _sha256(path) == asset.sha256


def download_asset(
    asset: ReleaseAsset,
    dest_dir: Path | None = None,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download *asset* into *dest_dir* and return its path, verified.

    ``progress(done, total)`` is called as bytes arrive (*total* is 0 when the size
    is unknown). Setting *cancel* stops the transfer with :class:`UpdateCancelled`.
    The file is written under a ``.part`` name and only renamed once its size and
    SHA-256 match what GitHub published, so a half-finished or tampered download is
    never the one that gets run. An intact copy already on disk is reused.
    """
    folder = dest_dir or download_dir()
    target = folder / asset.name
    if target.is_file() and _is_intact(target, asset):
        if progress is not None:
            size = target.stat().st_size
            progress(size, size)
        return target

    partial = target.with_name(target.name + ".part")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (
            urllib.request.urlopen(
                _request(asset.url, "application/octet-stream"), timeout=DOWNLOAD_TIMEOUT
            ) as response,
            partial.open("wb") as out,
        ):
            total = asset.size or int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(_CHUNK):
                if cancel is not None and cancel.is_set():
                    raise UpdateCancelled("The download was cancelled.")
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except UpdateCancelled:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The download failed: {exc}") from exc

    if not _is_intact(partial, asset):
        partial.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer did not match the published one.")
    os.replace(partial, target)
    return target


def _processes_to_wait_for() -> list[int]:
    """This process, and the one-file bootloader above it if there is one.

    A one-file (portable) build runs as two processes: PyInstaller's bootloader,
    which holds the exe open, unpacks to a temp folder and starts the real app as
    its child. Its unpacked folder (``sys._MEIPASS``) is outside the exe's own
    folder, which is how it is told from a one-folder build — where the parent is
    whatever launched the app, and must not be waited for.
    """
    pids = [os.getpid()]
    bundle = getattr(sys, "_MEIPASS", None)
    exe_dir = Path(sys.executable).resolve().parent
    if bundle and not Path(bundle).resolve().is_relative_to(exe_dir):
        pids.append(os.getppid())
    return pids


@dataclass
class InstallerLaunch:
    """A started installer, which the app watches until it may quit."""

    process: subprocess.Popen
    ready_file: Path

    def is_ready(self) -> bool:
        """Windows let the installer elevate: the app should get out of its way."""
        return self.ready_file.exists()

    def has_given_up(self) -> bool:
        """The installer ended before it was ready — the permission prompt was declined."""
        return self.process.poll() is not None and not self.is_ready()


def launch_installer(installer: Path, *, relaunch: bool = True) -> InstallerLaunch:
    """Start *installer* silently over this install, and return it to watch.

    ``/SILENT`` shows only Inno Setup's own progress window — nothing to answer —
    and the upgrade path in the script picks the existing install folder and
    tasks. The installer asks for elevation itself (its manifest is
    ``asInvoker``), so the process started here is the unelevated one that stays
    alive for the whole install, and it exits early if the prompt is declined.
    """
    ready_file = installer.with_name("installer.ready")
    ready_file.unlink(missing_ok=True)
    args = [
        str(installer),
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LOG={installer.with_name('install.log')}",
        f"/READYFILE={ready_file}",
        "/WAITPID=" + ",".join(str(pid) for pid in _processes_to_wait_for()),
    ]
    if relaunch:
        args.append("/RELAUNCH=1")
    try:
        # Not in the app's working directory, which may be the folder being replaced.
        process = subprocess.Popen(args, cwd=installer.parent, close_fds=True)
    except OSError as exc:
        raise UpdateError(f"The installer could not be started: {exc}") from exc
    return InstallerLaunch(process=process, ready_file=ready_file)
