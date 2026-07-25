"""Workspace persistence for sessions, so one survives closing the app.

Pure Python, modelled on :mod:`mm_companion.core.library`. One directory per
session under the workspace ``sessions/`` dir::

    sessions/<id>/session.json   the session and its roster
    sessions/<id>/rolls.jsonl    the roll history, one JSON object per line

The history is split out and **appended** rather than kept inside
``session.json``: a busy table rolls constantly, and rewriting the whole file
per roll would grow quadratically and risk losing the lot to a crash mid-write.
:func:`load_session` stitches the two back together.

Session ids become directory names, and one can arrive from the network, so
every id is validated (:func:`is_valid_session_id`) before it is joined onto a
path — a caller cannot walk out of the sessions dir with an id like ``../..``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from mm_companion.core import storage

from .model import RollRecord, SessionState

SESSION_FILENAME = "session.json"
ROLLS_FILENAME = "rolls.jsonl"

_VALID_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


class SessionStoreError(Exception):
    """A session could not be read or written (bad id, missing files, bad JSON)."""


@dataclass(frozen=True)
class SessionSummary:
    """The minimum a "resume a session" list needs, plus the directory it loads from."""

    id: str
    name: str
    updated_at: str
    player_count: int
    roll_count: int
    path: Path


def is_valid_session_id(session_id: str) -> bool:
    """True when *session_id* is safe to use as a directory name."""
    return bool(_VALID_ID.match(session_id))


def _sessions_dir(workspace: storage.Workspace | None = None) -> Path:
    """The workspace directory holding sessions."""
    return (workspace or storage.get_workspace()).sessions_dir


def session_dir(session_id: str, workspace: storage.Workspace | None = None) -> Path:
    """The directory for one session; raises on an id that is not path-safe."""
    if not is_valid_session_id(session_id):
        raise SessionStoreError(f"invalid session id {session_id!r}")
    return _sessions_dir(workspace) / session_id


def save_session(
    state: SessionState,
    workspace: storage.Workspace | None = None,
    *,
    write_rolls: bool = False,
) -> Path:
    """Write *state* to its session directory and return that directory.

    Only the session and its roster are written; the roll log is left alone,
    because rolls land through :func:`append_roll`. Pass ``write_rolls=True`` to
    rewrite the log from ``state.rolls`` as well — for creating or importing a
    session, not for the per-roll path.
    """
    directory = session_dir(state.id, workspace)
    directory.mkdir(parents=True, exist_ok=True)
    payload = state.to_dict(include_rolls=False)
    (directory / SESSION_FILENAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    if write_rolls:
        lines = "".join(json.dumps(roll.to_dict()) + "\n" for roll in state.rolls)
        (directory / ROLLS_FILENAME).write_text(lines, encoding="utf-8")
    return directory


def append_roll(
    session_id: str, roll: RollRecord, workspace: storage.Workspace | None = None
) -> None:
    """Append one roll to the session's log — the only write on the rolling path."""
    directory = session_dir(session_id, workspace)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ROLLS_FILENAME).open("a", encoding="utf-8") as log:
        log.write(json.dumps(roll.to_dict()) + "\n")


def load_rolls(session_id: str, workspace: storage.Workspace | None = None) -> list[RollRecord]:
    """Read a session's roll log, skipping any line that fails to parse.

    An append-only log's last line can be a torn write if the app died mid-roll;
    losing that one roll is preferable to failing to open the session at all.
    """
    path = session_dir(session_id, workspace) / ROLLS_FILENAME
    if not path.is_file():
        return []
    rolls: list[RollRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rolls.append(RollRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            continue
    rolls.sort(key=lambda roll: roll.seq)
    return rolls


def load_session(session_id: str, workspace: storage.Workspace | None = None) -> SessionState:
    """Rebuild a session from disk, roll log included."""
    path = session_dir(session_id, workspace) / SESSION_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SessionStoreError(f"no session {session_id!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SessionStoreError(f"session {session_id!r} is corrupt: {exc}") from exc
    if not isinstance(raw, dict):
        raise SessionStoreError(f"session {session_id!r} is corrupt: not a JSON object")

    state = SessionState.from_dict(raw)
    state.rolls = load_rolls(session_id, workspace)
    # A slot's ``connected`` flag describes a live socket, which no longer exists
    # after a restart; everyone starts disconnected and reconnects on their own.
    for slot in state.players.values():
        slot.connected = False
    return state


def delete_session(session_id: str, workspace: storage.Workspace | None = None) -> None:
    """Remove a session directory and everything in it; a missing one is not an error."""
    shutil.rmtree(session_dir(session_id, workspace), ignore_errors=True)


def list_sessions(workspace: storage.Workspace | None = None) -> list[SessionSummary]:
    """Summarize every readable session in the workspace, most recently updated first.

    Unreadable or malformed session directories are skipped rather than raising,
    so one corrupt session cannot stop the others from being listed.
    """
    directory = _sessions_dir(workspace)
    if not directory.is_dir():
        return []

    summaries: list[SessionSummary] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or not is_valid_session_id(child.name):
            continue
        try:
            raw = json.loads((child / SESSION_FILENAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        state = SessionState.from_dict(raw)
        summaries.append(
            SessionSummary(
                id=state.id,
                name=state.name,
                updated_at=state.updated_at,
                player_count=len(state.players),
                roll_count=len(load_rolls(child.name, workspace)),
                path=child,
            )
        )
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return summaries
