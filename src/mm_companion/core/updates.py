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
  started from. The installer and ``installer/mm_companion.iss`` share a small
  handshake on its command line:

  ``/READYFILE``
      created once Windows has let the installer elevate — the app's cue to quit;
  ``/CANCELFILE``
      created by the app if the user gives up while Windows is still asking; an
      installer that finds it on starting leaves without touching anything;
  ``/WAITPID``
      the processes to wait out before a file is replaced, since the running app
      holds its own exe open;
  ``/RELAUNCH=1``
      start the app again when done — *whether or not the install worked*, so a
      failure is reported by the app rather than by the app silently vanishing.

- **Reporting**: before quitting, the app leaves a note in the workspace
  ``logs/`` folder naming the version it expects to come back as and the
  installer's log. The next launch reads it (:func:`take_update_result`): the
  running version says whether the install worked, and a failure points at the
  log.

Pure Python and stdlib only, like the rest of ``core``: the launcher runs these on
worker threads and renders the answers.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mm_companion import __version__
from mm_companion.core import storage

#: The repository whose releases are this app's releases.
REPOSITORY = "Mrzillka/MM-Companion"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"

#: The installer's fixed Inno Setup ``AppId`` (``installer/mm_companion.iss``) —
#: the registry key under which it records where it installed the app.
INSTALLER_APP_ID = "{4E9C2EF5-C7BD-400C-82E3-72F36FF6DF14}"

#: Points the check at another "latest release" document — a local server serving a
#: hand-made one is how the whole update path is tried out without publishing.
FEED_ENV_VAR = "MM_COMPANION_UPDATE_FEED"

#: Long enough for a slow connection, short enough that a dead one does not keep a
#: worker thread alive for the rest of the session.
DEFAULT_TIMEOUT = 5.0
#: Per read while downloading: a stalled transfer fails rather than hanging.
DOWNLOAD_TIMEOUT = 30.0
_CHUNK = 64 * 1024

#: After asking the installer to stand down, how long to give one that had already
#: got past that check to say so (it writes its ready file in the same breath).
WITHDRAW_GRACE = 1.0

#: How many installer logs to keep in the workspace; older ones are pruned.
KEPT_LOGS = 10
PENDING_FILENAME = "pending-update.json"

#: A failed request: ``OSError`` covers URLError/HTTPError, resets and timeouts;
#: ``HTTPException`` a server that broke off mid-reply; ``ValueError`` bad JSON.
_NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)

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
        return self.assets.get(installer_name(self.version))


def installer_name(version: str) -> str:
    """The file name the release workflow gives the installer for *version*."""
    return f"MM-Companion-Setup-{version}.exe"


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
    except _NETWORK_ERRORS:
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


# -- where the app is installed ---------------------------------------------------


def _same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def registered_install_dir() -> Path | None:
    """Where the installer recorded it put the app, per the registry; else ``None``.

    Machine-wide installs record under HKLM's 64-bit view, and an older per-user
    one under HKCU — the same two places, in the same order, the installer's own
    upgrade detection reads.
    """
    if sys.platform != "win32":
        return None
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INSTALLER_APP_ID}_is1"
    for hive, view in (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ):
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if isinstance(location, str) and location.strip():
            return Path(location.strip())
    return None


def install_dir() -> Path | None:
    """The folder of this app if an in-place update would update *it*, else ``None``.

    Only a frozen Windows build can be updated in place, and only one the installer
    put there: its uninstaller (``unins000.exe``) sits beside the exe, and the
    registry records this very folder as the install. The installer always upgrades
    the *registered* folder, so a copy run from anywhere else — a second install, a
    portable exe carried off on a stick, a checkout run from source — is sent to
    the release page instead of updating an app it is not.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    if not any(folder.glob("unins*.exe")):
        return None
    registered = registered_install_dir()
    if registered is None or not _same_folder(registered, folder):
        return None
    return folder


def can_self_update() -> bool:
    """Whether :func:`launch_installer` can update the running app in place."""
    return install_dir() is not None


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


def other_instances(exe: Path | None = None) -> list[int]:
    """Process ids running *exe* (default: this app's own), other than this one.

    A second copy of the app holds the same files open, and the installer only
    waits for the copy that asked for the update — so it has to be closed first.
    Windows only (``[]`` elsewhere); stdlib ``ctypes`` over the process list.
    """
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    target = os.path.normcase(str((exe or Path(sys.executable)).resolve()))
    own = set(_processes_to_wait_for())
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    process_query_limited_information = 0x1000

    pids = (wintypes.DWORD * 8192)()
    needed = wintypes.DWORD()
    if not kernel32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    found: list[int] = []
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if pid == 0 or pid in own:
            continue
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                if os.path.normcase(buffer.value) == target:
                    found.append(int(pid))
        finally:
            kernel32.CloseHandle(handle)
    return found


# -- downloading ---------------------------------------------------------------------


def download_dir() -> Path:
    """Where installers are downloaded to; kept, so a retry need not fetch again."""
    return Path(tempfile.gettempdir()) / "MM-Companion-update"


def clear_downloads(keep: str = "", folder: Path | None = None) -> None:
    """Delete downloaded installers (and half-downloads) except the one named *keep*.

    Each is ~90 MB, so one is kept at most: the one about to be run, or — once an
    update is known to have worked — none. The handshake files an earlier run left
    (``installer.ready``, ``installer.cancel``) go too.
    """
    folder = folder or download_dir()
    if not folder.is_dir():
        return
    leftovers = [*folder.glob("MM-Companion-Setup-*"), *folder.glob("installer.*")]
    for path in leftovers:
        if path.name != keep:
            try:
                path.unlink()
            except OSError:
                pass  # still open (a download in flight, an installer running)


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


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # held open by an overlapping attempt; the next one overwrites it


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
    never the one that gets run. An intact copy already on disk is reused; any other
    installer lying about from an earlier version is deleted.
    """
    folder = dest_dir or download_dir()
    target = folder / asset.name
    clear_downloads(keep=asset.name, folder=folder)
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
        _discard(partial)
        raise
    except _NETWORK_ERRORS as exc:
        _discard(partial)
        raise UpdateError(f"The download failed: {exc}") from exc

    if not _is_intact(partial, asset):
        _discard(partial)
        raise UpdateError("The downloaded installer did not match the published one.")
    os.replace(partial, target)
    return target


# -- running the installer -------------------------------------------------------------


@dataclass
class InstallerLaunch:
    """A started installer, which the app watches until it may quit."""

    process: subprocess.Popen
    ready_file: Path
    cancel_file: Path
    #: Where the installer was told to log — worth offering when it ends early.
    log_file: Path | None = None

    def is_ready(self) -> bool:
        """Windows let the installer elevate: the app should get out of its way."""
        return self.ready_file.exists()

    def has_given_up(self) -> bool:
        """The installer ended before it was ready.

        Usually the permission prompt was declined — but Setup failing before it
        got that far (a log it could not create, say) ends the same way, which is
        why :attr:`exit_code` and :attr:`log_file` are kept to tell them apart.
        """
        return self.process.poll() is not None and not self.is_ready()

    @property
    def exit_code(self) -> int | None:
        """The installer's exit code once it has ended, else ``None``."""
        return self.process.poll()

    def withdraw(self, grace: float = WITHDRAW_GRACE) -> bool:
        """Ask the installer to stand down; ``False`` if it is already past asking.

        The installer checks for the cancel file and only then writes its ready
        file, so once the cancel file exists, a ready file appearing within *grace*
        means it had already checked and is going ahead — the app must then quit
        after all — and none appearing means it will find the cancel file and leave.
        """
        if self.is_ready():
            return False
        self.cancel_file.write_text("cancel", encoding="utf-8")
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if self.is_ready():
                return False
            time.sleep(0.05)
        return not self.is_ready()


def launch_installer(
    installer: Path, *, log_file: Path | None = None, relaunch: bool = True
) -> InstallerLaunch:
    """Start *installer* silently over this install, and return it to watch.

    ``/SILENT`` shows only Inno Setup's own progress window, and ``/NOCANCEL`` takes
    its Cancel button away: by the time it shows, the app has quit, and a cancelled
    install is a half-replaced one. The upgrade path in the script keeps the
    existing install folder and tasks. The installer asks for elevation itself (its
    manifest is ``asInvoker``), so the process started here is the unelevated one
    that stays alive for the whole install, and it exits early if the prompt is
    declined.
    """
    ready_file = installer.with_name("installer.ready")
    cancel_file = installer.with_name("installer.cancel")
    for stale in (ready_file, cancel_file):
        stale.unlink(missing_ok=True)
    log_file = log_file or installer.with_name("install.log")
    try:
        # Inno Setup does not create the folder of its /LOG, and gives up at once —
        # before its ready file — when it cannot open it.
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateError(f"The installer's log could not be created: {exc}") from exc
    args = [
        str(installer),
        "/SILENT",
        "/NOCANCEL",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LOG={log_file}",
        f"/READYFILE={ready_file}",
        f"/CANCELFILE={cancel_file}",
        "/WAITPID=" + ",".join(str(pid) for pid in _processes_to_wait_for()),
    ]
    if relaunch:
        args.append("/RELAUNCH=1")
    try:
        # Not in the app's working directory, which may be the folder being replaced.
        process = subprocess.Popen(args, cwd=installer.parent, close_fds=True)
    except OSError as exc:
        raise UpdateError(f"The installer could not be started: {exc}") from exc
    return InstallerLaunch(
        process=process, ready_file=ready_file, cancel_file=cancel_file, log_file=log_file
    )


# -- reporting back ------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateResult:
    """How the last in-app update went, as seen by the app it relaunched."""

    succeeded: bool
    from_version: str
    to_version: str
    log_file: Path | None


def update_log_file(to_version: str, from_version: str = __version__) -> Path:
    """Where the installer should log this attempt at updating to *to_version*.

    Stamped with the time, so each attempt keeps its own log: a failed one is the
    log worth reading, and it must not be overwritten by the retry that follows.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"update-{from_version}-to-{to_version}-{stamp}.log"
    return storage.get_workspace().logs_dir / name


def _pending_file() -> Path:
    return storage.get_workspace().logs_dir / PENDING_FILENAME


def record_pending_update(to_version: str, log_file: Path) -> None:
    """Note, just before quitting, what the next launch should find itself to be."""
    path = _pending_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    note = {"from": __version__, "to": to_version, "log": str(log_file)}
    path.write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8")


def discard_pending_update() -> None:
    """Forget a noted update that did not, after all, hand over."""
    _pending_file().unlink(missing_ok=True)


def take_update_result(current: str = __version__) -> UpdateResult | None:
    """How the noted update went, and forget it; ``None`` if there was none.

    The running version is the verdict: an installer that finished relaunches the
    new app, and one that failed relaunches whatever is still there. After a
    success the downloaded installer is no longer needed and is deleted; old logs
    beyond :data:`KEPT_LOGS` are pruned either way.
    """
    path = _pending_file()
    try:
        note = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        path.unlink(missing_ok=True)
    if not isinstance(note, dict):
        return None
    to_version = str(note.get("to", ""))
    log = note.get("log")
    new, now = parse_version(to_version), parse_version(current)
    result = UpdateResult(
        succeeded=new is not None and new == now,
        from_version=str(note.get("from", "")),
        to_version=to_version,
        log_file=Path(log) if isinstance(log, str) and log else None,
    )
    if result.succeeded:
        clear_downloads()
    _prune_logs()
    return result


def _prune_logs() -> None:
    logs = sorted(
        storage.get_workspace().logs_dir.glob("update-*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in logs[KEPT_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass
