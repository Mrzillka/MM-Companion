"""The update check: version ordering, reading GitHub's reply, and the launcher badge."""

from __future__ import annotations

import io
import json
import time
import urllib.error
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion import __version__
from mm_companion.core import storage, updates
from mm_companion.core.updates import ReleaseInfo, is_newer, parse_version, release_from_json
from mm_companion.ui.start_window import StartWindow
from mm_companion.ui.version_badge import VersionBadge


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))


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
        assets={"MM-Companion-Setup-0.8.0.exe": "https://example.invalid/setup.exe"},
    )


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
