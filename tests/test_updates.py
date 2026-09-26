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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

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


def _frozen_at(monkeypatch: pytest.MonkeyPatch, folder: Path, registered: Path | None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MM-Companion.exe").write_bytes(b"")
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "executable", str(folder / "MM-Companion.exe"))
    monkeypatch.setattr(updates, "registered_install_dir", lambda: registered)


def test_the_registered_install_can_update_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "Program Files" / "MM-Companion"
    _frozen_at(monkeypatch, folder, registered=folder)
    (folder / "unins000.exe").write_bytes(b"")
    assert updates.install_dir() == folder.resolve()


def test_a_copy_without_an_uninstaller_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "stick"
    _frozen_at(monkeypatch, folder, registered=folder)
    assert updates.install_dir() is None


def test_a_second_install_the_registry_does_not_know_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The installer would upgrade the registered folder, not this one.
    folder = tmp_path / "second"
    _frozen_at(monkeypatch, folder, registered=tmp_path / "Program Files" / "MM-Companion")
    (folder / "unins000.exe").write_bytes(b"")
    assert updates.install_dir() is None


def test_a_one_folder_build_waits_only_for_itself() -> None:
    assert updates._processes_to_wait_for() == [os.getpid()]


@pytest.mark.skipif(os.name != "nt", reason="the process list is read through Win32")
def test_other_instances_finds_another_process_running_the_same_exe() -> None:
    import subprocess
    import sys

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        found = updates.other_instances(Path(sys.executable))
        assert other.pid in found
        assert os.getpid() not in found
    finally:
        other.kill()
        other.wait()


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def _launch(tmp_path: Path, process: _FakeProcess | None = None) -> updates.InstallerLaunch:
    return updates.InstallerLaunch(
        process=process or _FakeProcess(),
        ready_file=tmp_path / "installer.ready",
        cancel_file=tmp_path / "installer.cancel",
    )


def test_launch_installer_creates_the_folder_its_log_goes_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inno Setup gives up at once when it cannot open its /LOG, and a fresh
    # workspace has no logs folder: the first real update failed exactly so.
    monkeypatch.setattr(updates.subprocess, "Popen", lambda args, **kwargs: _FakeProcess())
    log = tmp_path / "workspace" / "logs" / "update.log"
    launch = updates.launch_installer(tmp_path / "setup.exe", log_file=log)
    assert log.parent.is_dir()
    assert launch.log_file == log


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
    (tmp_path / "installer.cancel").write_text("stale")
    log = tmp_path / "logs" / "update.log"

    launch = updates.launch_installer(installer, log_file=log)

    args = started[0]
    assert args[0] == str(installer)
    assert {"/SILENT", "/NOCANCEL", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"} <= set(args)
    assert f"/LOG={log}" in args
    assert f"/READYFILE={tmp_path / 'installer.ready'}" in args
    assert f"/CANCELFILE={tmp_path / 'installer.cancel'}" in args
    assert f"/WAITPID={os.getpid()}" in args
    # Files left by an earlier attempt must not count for this one.
    assert not launch.is_ready()
    assert not launch.cancel_file.exists()
    assert not launch.has_given_up()

    launch.ready_file.write_text("ready")
    assert launch.is_ready()


def test_an_installer_that_ends_unready_has_given_up(tmp_path: Path) -> None:
    process = _FakeProcess()
    launch = _launch(tmp_path, process)
    process.returncode = 1
    assert launch.has_given_up()


def test_withdrawing_in_time_leaves_the_cancel_file_for_the_installer(tmp_path: Path) -> None:
    launch = _launch(tmp_path)
    assert launch.withdraw(grace=0.05) is True
    assert launch.cancel_file.exists()


def test_withdrawing_too_late_says_so(tmp_path: Path) -> None:
    launch = _launch(tmp_path)
    launch.ready_file.write_text("ready")
    assert launch.withdraw(grace=0.05) is False


def test_an_installer_that_will_not_start_is_an_update_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def popen(args, **kwargs):  # noqa: ARG001
        raise OSError("blocked")

    monkeypatch.setattr(updates.subprocess, "Popen", popen)
    with pytest.raises(UpdateError, match="could not be started"):
        updates.launch_installer(tmp_path / "setup.exe")


# -- reporting back ----------------------------------------------------------------


def test_downloading_clears_older_installers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "MM-Companion-Setup-8.0.0.exe").write_bytes(b"old")
    (folder / "MM-Companion-Setup-8.5.0.exe.part").write_bytes(b"half")
    _serve(monkeypatch)
    updates.download_asset(_asset(), folder)
    assert sorted(path.name for path in folder.iterdir()) == ["MM-Companion-Setup-9.0.0.exe"]


def test_each_attempt_gets_its_own_log(monkeypatch: pytest.MonkeyPatch) -> None:
    stamps = iter(["20260926-175712", "20260926-182401"])
    monkeypatch.setattr(updates.time, "strftime", lambda fmt: next(stamps))
    first, retry = updates.update_log_file("9.0.0"), updates.update_log_file("9.0.0")
    assert first != retry
    assert first.name == f"update-{__version__}-to-9.0.0-20260926-175712.log"


def test_clearing_downloads_takes_the_handshake_leftovers_too(tmp_path: Path) -> None:
    for name in ("MM-Companion-Setup-9.0.0.exe", "installer.ready", "installer.cancel"):
        (tmp_path / name).write_text("x")
    updates.clear_downloads(folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_no_note_means_no_result() -> None:
    assert updates.take_update_result() is None


def test_coming_back_as_the_new_version_is_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "MM-Companion-Setup-9.0.0.exe").write_bytes(b"done with")
    monkeypatch.setattr(updates, "download_dir", lambda: downloads)
    log = updates.update_log_file("9.0.0")
    updates.record_pending_update("9.0.0", log)

    result = updates.take_update_result(current="9.0.0")

    assert result == updates.UpdateResult(
        succeeded=True, from_version=__version__, to_version="9.0.0", log_file=log
    )
    assert list(downloads.iterdir()) == []  # the installer is no longer needed
    assert updates.take_update_result(current="9.0.0") is None  # reported once


def test_coming_back_as_the_old_version_is_a_failure() -> None:
    log = updates.update_log_file("9.0.0")
    updates.record_pending_update("9.0.0", log)
    result = updates.take_update_result(current=__version__)
    assert result is not None
    assert result.succeeded is False
    assert result.log_file == log


def test_a_withdrawn_update_leaves_no_note() -> None:
    updates.record_pending_update("9.0.0", updates.update_log_file("9.0.0"))
    updates.discard_pending_update()
    assert updates.take_update_result() is None


def test_only_the_newest_logs_are_kept() -> None:
    logs = storage.get_workspace().logs_dir
    logs.mkdir(parents=True)
    for index in range(updates.KEPT_LOGS + 3):
        path = logs / f"update-0.0.{index}-to-0.0.{index + 1}.log"
        path.write_text("log")
        os.utime(path, (index, index))
    updates.record_pending_update("9.0.0", logs / "update.log")
    updates.take_update_result()
    kept = sorted(path.name for path in logs.glob("update-*.log"))
    assert len(kept) == updates.KEPT_LOGS
    assert "update-0.0.0-to-0.0.1.log" not in kept


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
    # Neither a session nor a second copy of the app, unless a test says so.
    monkeypatch.setattr(update_dialog, "_session_running", lambda: False)
    monkeypatch.setattr(updates, "other_instances", lambda: [])
    return calls


def _fake_download(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    def download(asset, dest_dir=None, *, progress=None, cancel=None):  # noqa: ARG001
        progress(50, 100)
        progress(100, 100)
        return path

    monkeypatch.setattr(updates, "download_asset", download)


def _dialog() -> UpdateDialog:
    return UpdateDialog(release_from_json(_release_json("v99.0.0")))


def _fake_launch(
    monkeypatch: pytest.MonkeyPatch, launch: updates.InstallerLaunch
) -> list[tuple[Path, Path | None]]:
    launched: list[tuple[Path, Path | None]] = []

    def launch_installer(path, *, log_file=None, relaunch=True):  # noqa: ARG001
        launched.append((path, log_file))
        return launch

    monkeypatch.setattr(updates, "launch_installer", launch_installer)
    return launched


def test_dialog_downloads_launches_and_quits_once_the_installer_is_ready(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    installer = tmp_path / "setup.exe"
    _fake_download(monkeypatch, installer)
    launch = _launch(tmp_path)
    launched = _fake_launch(monkeypatch, launch)
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: launched)

    # The installer logs into the workspace, where the next launch will look.
    assert launched == [(installer, dialog._log_file)]
    assert dialog._log_file.parent == storage.get_workspace().logs_dir
    assert dialog._log_file.name.startswith(f"update-{__version__}-to-99.0.0-")
    assert "permission" in dialog._detail.text()

    launch.ready_file.write_text("ready")
    _wait_for(qapp, lambda: quits)
    assert "reopen" in dialog._status.text()
    # The note for the next launch was left before quitting.
    assert updates.take_update_result(current="99.0.0").succeeded
    # Handed over: the app's quit may now close it like any other window.
    dialog.show()
    dialog.reject()
    assert dialog.isHidden()


def test_cancelling_while_windows_asks_withdraws_the_installer(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    launch = _launch(tmp_path)
    _fake_launch(monkeypatch, launch)
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)
    monkeypatch.setattr(updates, "WITHDRAW_GRACE", 0.05)

    dialog = _dialog()
    dialog.show()
    dialog.start()
    _wait_for(qapp, lambda: dialog._poll.isActive())
    dialog.reject()

    assert launch.cancel_file.exists()
    assert dialog.isHidden()
    assert quits == []
    assert updates.take_update_result() is None


def test_cancelling_too_late_hands_over_anyway(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    launch = _launch(tmp_path)
    _fake_launch(monkeypatch, launch)
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: dialog._poll.isActive())
    launch.ready_file.write_text("ready")  # the installer got there first
    dialog.reject()

    assert quits == [True]


def test_dialog_reports_a_declined_permission_prompt(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    process = _FakeProcess()
    _fake_launch(monkeypatch, _launch(tmp_path, process))
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: dialog._poll.isActive())
    process.returncode = 1
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())

    assert "cancelled" in dialog._status.text()
    assert "exit code 1" in dialog._status.text()
    assert dialog._log_button.isHidden()
    assert quits == []


def test_dialog_offers_the_log_of_an_installer_that_failed_before_installing(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    process = _FakeProcess()
    log = tmp_path / "update.log"
    launch = _launch(tmp_path, process)
    launch.log_file = log
    _fake_launch(monkeypatch, launch)
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: dialog._poll.isActive())
    log.write_text("Setup ran elevated, then stopped.")
    process.returncode = 3
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())

    assert "stopped before installing" in dialog._status.text()
    assert "exit code 3" in dialog._status.text()
    assert not dialog._log_button.isHidden()
    assert quits == []


def test_dialog_stops_when_a_window_will_not_close(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    launched = _fake_launch(monkeypatch, _launch(tmp_path))
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: False)

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())

    assert launched == []
    assert "kept open" in dialog._status.text()
    assert quits == []


def test_dialog_stops_while_another_copy_is_running(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quits: list[bool]
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    launched = _fake_launch(monkeypatch, _launch(tmp_path))
    closed: list[bool] = []
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: closed.append(True))
    monkeypatch.setattr(updates, "other_instances", lambda: [4242])

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())

    assert "Another MM-Companion window" in dialog._status.text()
    # Checked before anything of the user's was closed.
    assert closed == []
    assert launched == []


@pytest.mark.parametrize("answer", [False, True])
def test_a_running_session_is_only_ended_with_consent(
    answer: bool,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quits: list[bool],
) -> None:
    _fake_download(monkeypatch, tmp_path / "setup.exe")
    launched = _fake_launch(monkeypatch, _launch(tmp_path))
    monkeypatch.setattr(UpdateDialog, "_close_other_windows", lambda self: True)
    monkeypatch.setattr(update_dialog, "_session_running", lambda: True)
    monkeypatch.setattr(UpdateDialog, "_confirm_end_session", lambda self: answer)

    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: launched or not dialog._page_button.isHidden())

    assert bool(launched) is answer


def test_dialog_shows_a_failed_download(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def download(asset, dest_dir=None, *, progress=None, cancel=None):  # noqa: ARG001
        raise UpdateError("The download failed: no network")

    monkeypatch.setattr(updates, "download_asset", download)
    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())
    assert "no network" in dialog._status.text()


def test_dialog_shows_even_an_unexpected_download_error(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def download(asset, dest_dir=None, *, progress=None, cancel=None):  # noqa: ARG001
        raise KeyError("surprise")

    monkeypatch.setattr(updates, "download_asset", download)
    dialog = _dialog()
    dialog.start()
    _wait_for(qapp, lambda: not dialog._page_button.isHidden())
    assert "surprise" in dialog._status.text()


# -- closing the other windows ----------------------------------------------------


class _Window(QWidget):
    """A top-level window that may refuse to close, and records the order it was asked."""

    def __init__(self, log: list[str], name: str, *, refuse: bool = False, parent=None) -> None:
        super().__init__(parent)
        if parent is not None:
            self.setWindowFlag(Qt.WindowType.Window)
        self._log, self._name, self._refuse = log, name, refuse

    def closeEvent(self, event) -> None:  # noqa: N802
        self._log.append(self._name)
        if self._refuse:
            event.ignore()
        else:
            super().closeEvent(event)


def test_closing_skips_owned_windows_and_notices_a_refusal(qapp: QApplication) -> None:
    log: list[str] = []
    launcher = QWidget()
    dialog = UpdateDialog(release_from_json(_release_json("v99.0.0")), launcher)
    owner = _Window(log, "owner")
    popped_out = _Window(log, "popped-out block", refuse=True, parent=owner)
    for window in (launcher, owner, popped_out):
        window.show()

    assert dialog._close_other_windows() is True
    # The popped-out block is its owner's business; closing it directly would take
    # it off the sheet — and it refuses.
    assert "popped-out block" not in log
    assert "owner" in log

    stubborn = _Window(log, "unsaved sheet", refuse=True)
    stubborn.show()
    assert dialog._close_other_windows() is False
    stubborn._refuse = False
    stubborn.close()
    for window in (launcher, owner, popped_out):
        window.close()


def test_closing_suppresses_a_restart_a_window_asks_for(qapp: QApplication) -> None:
    from mm_companion.ui import app_restart

    launcher = QWidget()
    dialog = UpdateDialog(release_from_json(_release_json("v99.0.0")), launcher)
    try:
        dialog._close_other_windows()
        assert app_restart._suppressed is True
    finally:
        app_restart._suppressed = False


# -- the launcher after an update ---------------------------------------------------


def test_a_failed_update_is_reported_with_its_log(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = updates.update_log_file("99.0.0")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("Error: something went wrong")
    updates.record_pending_update("99.0.0", log)
    shown: list[str] = []
    monkeypatch.setattr(
        update_dialog.QMessageBox, "exec", lambda self: shown.append(self.informativeText())
    )

    result = update_dialog.report_update_result()

    assert result is not None and not result.succeeded
    assert str(log) in shown[0]


def test_a_successful_update_shows_on_the_badge(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "check_for_updates", lambda: False)
    monkeypatch.setattr(
        updates,
        "take_update_result",
        lambda: updates.UpdateResult(True, "0.0.1", __version__, None),
    )
    window = StartWindow()
    window.run_startup_checks()
    assert "updated from v0.0.1" in window._version_badge._label.text()


def test_startup_checks_respect_the_setting(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(VersionBadge, "check_for_update", lambda self: calls.append(True))
    window = StartWindow()

    storage.set_check_for_updates(False)
    window.run_startup_checks()
    assert calls == []

    storage.set_check_for_updates(True)
    window.run_startup_checks()
    assert calls == [True]


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
