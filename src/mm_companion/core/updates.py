"""Is there a newer release than the one running?

The app's releases are GitHub Releases on the project repository, tagged
``v<version>`` with ``<version>`` the SemVer in ``mm_companion.__version__`` (the
release workflow refuses a tag that disagrees with it). So the check is one
request to GitHub's "latest release" endpoint — which already skips drafts and
pre-releases — and a comparison of that tag against the running version.

Pure Python and stdlib only, like the rest of ``core``: the launcher runs
:func:`check_for_update` on a worker thread and renders the answer. Every failure
— offline, rate-limited, a malformed reply — comes back as ``None``, because "we
could not tell" and "you are up to date" look the same to someone opening the app,
and neither is worth an error dialog.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from mm_companion import __version__

#: The repository whose releases are this app's releases.
REPOSITORY = "Mrzillka/MM-Companion"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"

#: Long enough for a slow connection, short enough that a dead one does not keep a
#: worker thread alive for the rest of the session.
DEFAULT_TIMEOUT = 5.0

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class ReleaseInfo:
    """A published release: its version, the page to read about it, its assets."""

    version: str
    tag: str
    page_url: str
    #: Asset file name → download URL (the installer is the one that matters).
    assets: dict[str, str]


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


def release_from_json(payload: dict) -> ReleaseInfo | None:
    """Read GitHub's release object; ``None`` if it is not a usable release."""
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        return None
    if payload.get("draft") or payload.get("prerelease"):
        return None
    assets = {
        asset["name"]: asset["browser_download_url"]
        for asset in payload.get("assets") or []
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and isinstance(asset.get("browser_download_url"), str)
    }
    page_url = payload.get("html_url")
    return ReleaseInfo(
        version=tag.lstrip("v"),
        tag=tag,
        page_url=page_url if isinstance(page_url, str) else RELEASES_PAGE_URL,
        assets=assets,
    )


def fetch_latest_release(timeout: float = DEFAULT_TIMEOUT) -> ReleaseInfo | None:
    """The newest published release, or ``None`` if it could not be fetched."""
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub rejects API requests with no User-Agent.
            "User-Agent": f"MM-Companion/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
