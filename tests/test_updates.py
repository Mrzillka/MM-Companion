"""The update: version ordering, GitHub's reply, downloading, launching, and the UI."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion import __version__
from mm_companion.core import storage, updates
from mm_companion.core.updates import (
    ReleaseAsset,
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    is_newer,
    parse_version,
    release_from_json,
)
from mm_companion.ui import update_dialog
from mm_companion.ui.start_window import StartWindow
from mm_companion.ui.update_dialog import UpdateDialog
from mm_companion.ui.version_badge import VersionBadge

PAYLOAD = b"not really an installer" * 1000
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.delenv(updates.FEED_ENV_VAR, raising=False)


def _release_json(tag: str = "v9.0.0", **extra: object) -> dict:
    payload = {
        "tag_name": tag,
        "html_url": f"https://github.com/Mrzillka/MM-Companion/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": f"MM-Companion-Setup-{tag.lstrip('v')}.exe",
                "browser_download_url": "https://example.invalid/setup.exe",
                "size": len(PAYLOAD),
                "digest": f"sha256:{PAYLOAD_SHA}",
            }
        ],
    }
    payload.update(extra)
    return payload


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0.7.1", (0, 7, 1)),
        ("v0.7.1", (0, 7, 1)),
        ("v0.8.0", (0, 8)),
        ("0.8", (0, 8)),
        ("1.2.3-rc1", (1, 2, 3)),
        ("latest", None),
        ("", None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, ...] | None) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.7.2", "0.7.1", True),
        ("0.10.0", "0.9.9", True),  # numeric, not lexical
        ("1.0", "0.99.99", True),
        ("0.7.1", "0.7.1", False),
        ("0.8", "0.8.0", False),
        ("0.7.0", "0.7.1", False),
        ("garbage", "0.7.1", False),
        ("0.8.0", "garbage", False),
    ],
)
def test_is_newer(candidate: str, current: str, expected: bool) -> None:
    assert is_newer(candidate, current) is expected


def test_release_from_json_reads_the_tag_page_and_assets() -> None:
    release = release_from_json(_release_json("v0.8.0"))
    assert release == ReleaseInfo(
        version="0.8.0",
        tag="v0.8.0",
        page_url="https://github.com/Mrzillka/MM-Companion/releases/tag/v0.8.0",
        assets={
            "MM-Companion-Setup-0.8.0.exe": ReleaseAsset(
                name="MM-Companion-Setup-0.8.0.exe",
                url="https://example.invalid/setup.exe",
                size=len(PAYLOAD),
                sha256=PAYLOAD_SHA,
            )
        },
    )
    assert release.installer is release.assets["MM-Companion-Setup-0.8.0.exe"]


def test_a_release_without_its_installer_has_none() -> None:
    release = release_from_json(_release_json("v0.8.0", assets=[]))
    assert release is not None
    assert release.installer is None


@pytest.mark.parametrize(
    "payload",
    [
        _release_json(draft=True),
        _release_json(prerelease=True),
        _release_json(tag="nightly"),
        {"message": "Not Found"},
    ],
)
def test_release_from_json_rejects_what_is_not_a_release(payload: dict) -> None:
    assert release_from_json(payload) is None


def _fake_urlopen(payload: object):
    def urlopen(request, timeout):  # noqa: ARG001 (signature of the real one)
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    return urlopen


def test_check_for_update_offers_a_newer_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates.urllib.request, "urlopen", _fake_urlopen(_release_json("v99.0.0")))
    release = updates.check_for_update(current="0.7.1")
    assert release is not None
    assert release.version == "99.0.0"


def test_check_for_update_is_quiet_when_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", _fake_urlopen(_release_json(f"v{__version__}"))
    )
    assert updates.check_for_update() is None


def test_check_for_update_is_quiet_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(request, timeout):  # noqa: ARG001
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.check_for_update() is None


def test_check_for_update_is_quiet_on_a_malformed_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    def garbage(request, timeout):  # noqa: ARG001
        return io.BytesIO(b"<html>rate limited</html>")

    monkeypatch.setattr(updates.urllib.request, "urlopen", garbage)
    assert updates.check_for_update() is None


# -- the launcher badge ------------------------------------------------------


def test_launcher_title_no_longer_carries_the_version(qapp: QApplication) -> None:
    window = StartWindow()
    assert window.windowTitle() == "MM-Companion"
    assert window._version_badge._label.text() == f"v{__version__}"
    assert window._version_badge._update_button.isHidden()


def test_building_the_launcher_does_not_check_for_updates(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(updates, "check_for_update", lambda *a, **k: calls.append(a))
    StartWindow()
    assert calls == []


def test_badge_offers_the_update_it_is_told_about(qapp: QApplication) -> None:
    badge = VersionBadge()
    release = release_from_json(_release_json("v99.0.0"))
    badge.show_update(release)

    assert badge.release is release
    assert "v99.0.0 available" in badge._label.text()
    assert not badge._update_button.isHidden()


def test_badge_worker_delivers_a_found_update_on_the_gui_thread(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = release_from_json(_release_json("v99.0.0"))
    monkeypatch.setattr(updates, "check_for_update", lambda *a, **k: release)
    badge = VersionBadge()

    badge.check_for_update()
    deadline = 200
    while badge.release is None and deadline:
        qapp.processEvents()
        deadline -= 1
        time.sleep(0.01)

    assert badge.release is release
    assert not badge._update_button.isHidden()


def test_the_feed_can_be_pointed_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def urlopen(request, timeout):  # noqa: ARG001
        seen.append(request.full_url)
        return io.BytesIO(json.dumps(_release_json("v99.0.0")).encode("utf-8"))

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv(updates.FEED_ENV_VAR, "http://127.0.0.1:8000/latest.json")
    assert updates.check_for_update() is not None
    assert seen == ["http://127.0.0.1:8000/latest.json"]


# -- downloading ---------------------------------------------------------------


class _Response(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _serve(monkeypatch: pytest.MonkeyPatch, data: bytes = PAYLOAD) -> list[str]:
    fetched: list[str] = []

    def urlopen(request, timeout):  # noqa: ARG001
        fetched.append(request.full_url)
        return _Response(data)

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    return fetched


def _asset(**changes: object) -> ReleaseAsset:
    fields = {
        "name": "MM-Companion-Setup-9.0.0.exe",
        "url": "https://example.invalid/setup.exe",
        "size": len(PAYLOAD),
        "sha256": PAYLOAD_SHA,
    }
    fields.update(changes)
    return ReleaseAsset(**fields)


def test_download_writes_the_verified_file_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch)
    seen: list[tuple[int, int]] = []
    path = updates.download_asset(_asset(), tmp_path, progress=lambda d, t: seen.append((d, t)))

    assert path == tmp_path / "MM-Companion-Setup-9.0.0.exe"
    assert path.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not list(tmp_path.glob("*.part"))


def test_an_intact_earlier_download_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "MM-Companion-Setup-9.0.0.exe").write_bytes(PAYLOAD)
    fetched = _serve(monkeypatch)
    updates.download_asset(_asset(), tmp_path)
    assert fetched == []


@pytest.mark.parametrize(
    "asset",
    [_asset(sha256="0" * 64), _asset(size=len(PAYLOAD) + 1)],
    ids=["wrong hash", "wrong size"],
)
def test_a_download_that_does_not_match_is_thrown_away(
    asset: ReleaseAsset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch)
    with pytest.raises(UpdateError):
        updates.download_asset(asset, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_is_an_update_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(request, timeout):  # noqa: ARG001
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    with pytest.raises(UpdateError, match="download failed"):
        updates.download_asset(_asset(), tmp_path)


def test_a_cancelled_download_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UpdateCancelled):
        updates.download_asset(_asset(), tmp_path, cancel=cancel)
    assert list(tmp_path.iterdir()) == []


# -- launching -----------------------------------------------------------------


def test_a_source_checkout_cannot_update_itself() -> None:
    assert updates.install_dir() is None
    assert updates.can_self_update() is False


def test_a_one_folder_build_waits_only_for_itself() -> None:
    assert updates._processes_to_wait_for() == [os.getpid()]


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def test_launch_installer_runs_it_silently_and_asks_for_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []

    def popen(args, **kwargs):  # noqa: ARG001
        started.append(args)
        return _FakeProcess()

    monkeypatch.setattr(updates.subprocess, "Popen", popen)
    installer = tmp_path / "MM-Companion-Setup-9.0.0.exe"
    (tmp_path / "installer.ready").write_text("stale")

    launch = updates.launch_installer(installer)

    args = started[0]
    assert args[0] == str(installer)
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"} <= set(args)
    assert f"/READYFILE={tmp_path / 'installer.ready'}" in args
    assert f"/WAITPID={os.getpid()}" in args
    # A ready file left by an earlier attempt must not count for this one.
    assert not launch.is_ready()
    assert not launch.has_given_up()

    launch.ready_file.write_text("ready")
    assert launch.is_ready()


def test_an_installer_that_ends_unready_has_given_up(tmp_path: Path) -> None:
    process = _FakeProcess()
    launch = updates.InstallerLaunch(process=process, ready_file=tmp_path / "installer.ready")
    process.returncode = 1
    assert launch.has_given_up()


def test_an_installer_that_will_not_start_is_an_update_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def popen(args, **kwargs):  # noqa: ARG001
        raise OSError("blocked")

    monkeypatch.setattr(updates.subprocess, "Popen", popen)
    with pytest.raises(UpdateError, match="could not be started"):
        updates.launch_installer(tmp_path / "setup.exe")


# -- the update dialog ---------------------------------------------------------


def _wait_for(qapp: QApplication, condition) -> None:
    for _ in range(300):
        if condition():
            return
        qapp.processEvents()
        time.sleep(0.01)
    raise AssertionError("condition never came true")


@pytest.fixture
def quits(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []
    monkeypatch.setattr(update_dialog, "_quit_app", lambda: calls.append(True))
    return calls


def _fake_download(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    def download(asset, dest_dir=None, *, progress=None, cancel=None):  # noqa: ARG001
        progress(50, 100)
        progress(100, 100)
        return path

    monkeypatch.setattr(updates, "download_asset", download)


def test_dialog_downloads_launches_and_quits_once_the_installer_is_ready(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    installer = tmp_path / "setup.exe"
    _fake_download(monkeypatch, installer)
    launch = updates.InstallerLaunch(process=_FakeProcess(), ready_file=tmp_path / "ready")
    launched: list[Path] = []
    monkeypatch.setattr(updates, "launch_installer", lambda path: launched.append(path) or launch)
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)

    dialog = UpdateDialog(release_from_json(_release_json("v99.0.0")))
    dialog.start()
    _wait_for(qapp, lambda: launched)

    assert launched == [installer]
    assert "permission" in dialog._detail.text()
    # While Windows is asking, the dialog cannot be dismissed out from under it.
    dialog.reject()
    assert quits == []

    launch.ready_file.write_text("ready")
    _wait_for(qapp, lambda: quits)
    assert "reopen" in dialog._status.text()
    # Handed over: the app's quit may now close it like any other window.
    dialog.show()
    dialog.reject()
    assert dialog.isHidden()


def test_dialog_reports_a_declined_permission_prompt(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    process = _FakeProcess()
    launch = updates.InstallerLaunch(process=process, ready_file=tmp_path / "ready")
    monkeypatch.setattr(updates, "launch_installer", lambda path: launch)
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)

    dialog = UpdateDialog(release_from_json(_release_json("v99.0.0")))
    dialog.start()
    _wait_for(qapp, lambda: dialog._poll.isActive())
    process.returncode = 1
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())

    assert "cancelled" in dialog._status.text()
    assert quits == []


def test_dialog_stops_when_a_window_will_not_close(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    launched: list[Path] = []
    monkeypatch.setattr(updates, "launch_installer", lambda path: launched.append(path))
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: False)

    dialog = UpdateDialog(release_from_json(_release_json("v99.0.0")))
    dialog.start()
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())

    assert launched == []
    assert "kept open" in dialog._status.text()
    assert quits == []


def test_dialog_shows_a_failed_download(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def download(asset, dest_dir=None, *, progress=None, cancel=None):  # noqa: ARG001
        raise UpdateError("The download failed: no network")

    monkeypatch.setattr(updates, "download_asset", download)
    dialog = UpdateDialog(release_from_json(_release_json("v99.0.0")))
    dialog.start()
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())
    assert "no network" in dialog._status.text()


def test_the_badge_sends_a_source_checkout_to_the_release_page(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mm_companion.ui import version_badge

    opened: list[str] = []
    monkeypatch.setattr(
        version_badge.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    badge = VersionBadge()
    badge.show_update(release_from_json(_release_json("v99.0.0")))
    badge._open_update()
    assert opened == ["https://github.com/Mrzillka/MM-Companion/releases/tag/v99.0.0"]
