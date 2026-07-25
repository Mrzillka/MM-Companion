"""The session wire protocol: what a client and a server say to each other.

Pure Python, no PySide6 and no game content. Every message is a small frozen
dataclass carrying only JSON-friendly values; :func:`encode` renders one as a
single UTF-8 line and :func:`decode` parses one back. The framing is
**newline-delimited JSON** — one message per line, never :mod:`pickle` — so a
hostile peer on the listening socket can at worst send a malformed line, which
:func:`decode` rejects with a :class:`ProtocolError` rather than executing
anything.

Every field is type-checked on the way in (see :func:`_coerce`): a message that
omits a required field, or supplies a string where a rank belongs, never reaches
the session logic.

Vocabulary (protocol v2):

**Client → server** — :class:`Hello`, :class:`CharacterSnapshot`,
:class:`RollRequest`, :class:`RemoveRollRequest`, :class:`Ping`.

**Server → client** — :class:`Welcome`, :class:`Roster`, :class:`RollAdded`,
:class:`RollRemoved`, :class:`ApplyCondition`, :class:`RemoveCondition`,
:class:`ErrorMessage`, :class:`Kicked`, :class:`Pong`.

A *hidden* roll is stored on the server with ``hidden: true`` and is never
broadcast at all, so there is nothing for a player client to peek at — only the
GM's own window renders it. Removing a roll (GM only) drops it from the log and
tells every client with a :class:`RollRemoved`; a hidden roll's removal is never
broadcast, matching how it was never added.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import ClassVar

#: Bumped whenever the message vocabulary changes incompatibly. A client whose
#: version differs from the server's is refused at the handshake with a readable
#: message instead of failing obscurely later.
PROTOCOL_VERSION = 2

#: Hard cap on one encoded message, including its trailing newline. A character
#: snapshot is the largest thing that legitimately travels (tens of KB); anything
#: past this is a bug or an attack, and is refused without being buffered further.
MAX_MESSAGE_BYTES = 256 * 1024

# Machine-readable reasons carried by :class:`ErrorMessage` / :class:`Kicked`, so
# the UI can phrase them itself rather than matching on prose.
ERROR_PROTOCOL_VERSION = "protocol_version"
ERROR_BAD_TOKEN = "bad_token"
ERROR_SESSION_FULL = "session_full"
ERROR_MALFORMED = "malformed"
ERROR_UNKNOWN_PLAYER = "unknown_player"
ERROR_RATE_LIMIT = "rate_limit"
#: A warning, not a refusal: the join succeeded but the two ends load different
#: mods, so condition and effect ids may not line up.
ERROR_MOD_SKEW = "mod_skew"


class ProtocolError(Exception):
    """A message could not be decoded, or violated the protocol's shape."""


def _coerce(owner: str, name: str, value: object, type_name: str) -> object:
    """Validate one field value against its annotated type, or raise.

    ``from __future__ import annotations`` leaves the annotation as source text,
    which is all this needs — the protocol deliberately uses only a handful of
    plain shapes (``str``, ``int``, ``bool``, ``dict``, ``list[...]`` and their
    ``| None`` variants) so validation stays a lookup rather than a type engine.
    """

    optional = "None" in type_name
    base = type_name.replace("| None", "").replace("None |", "").strip()

    if value is None:
        if optional:
            return None
        raise ProtocolError(f"{owner}.{name}: expected {base}, got null")

    def bad() -> ProtocolError:
        return ProtocolError(f"{owner}.{name}: expected {base}, got {type(value).__name__}")

    if base == "str":
        if not isinstance(value, str):
            raise bad()
        return value
    if base == "bool":
        if not isinstance(value, bool):
            raise bad()
        return value
    if base == "int":
        # JSON has no integer type of its own, and ``bool`` is an ``int`` subclass
        # in Python — neither a float nor ``true`` may pass as a rank.
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad()
        return value
    if base == "dict":
        if not isinstance(value, dict):
            raise bad()
        return dict(value)
    if base.startswith("list["):
        if not isinstance(value, list):
            raise bad()
        return list(value)
    raise ProtocolError(f"{owner}.{name}: unsupported field type {type_name!r}")


@dataclass(frozen=True)
class Message:
    """Base for every protocol message: a ``type`` tag plus flat JSON fields."""

    #: The wire tag; every concrete message overrides it and registers under it.
    TYPE: ClassVar[str] = ""

    def to_dict(self) -> dict:
        """The full envelope, ready for :func:`json.dumps`."""
        return {"type": self.TYPE, **asdict(self)}

    @classmethod
    def from_payload(cls, raw: dict) -> Message:
        """Rebuild from an envelope's fields, type-checking each one."""
        kwargs: dict[str, object] = {}
        for f in fields(cls):
            if f.name in raw:
                kwargs[f.name] = _coerce(cls.TYPE, f.name, raw[f.name], f.type)
            elif f.default is MISSING and f.default_factory is MISSING:
                raise ProtocolError(f"{cls.TYPE}: missing field {f.name!r}")
        return cls(**kwargs)


_REGISTRY: dict[str, type[Message]] = {}


def _register(cls: type[Message]) -> type[Message]:
    _REGISTRY[cls.TYPE] = cls
    return cls


# --------------------------------------------------------------------------
# Client -> server
# --------------------------------------------------------------------------


@_register
@dataclass(frozen=True)
class Hello(Message):
    """The client's opening message; the server answers :class:`Welcome` or errors.

    ``token`` is the session token carried by the join code. ``player_id`` and
    ``player_token`` are empty on a first join and echoed back from a previous
    :class:`Welcome` to reclaim the same roster slot on a reconnect.
    ``mod_fingerprint`` lets the server warn about mod skew (a GM running content
    the player lacks means condition and effect ids do not match).
    """

    TYPE: ClassVar[str] = "hello"

    token: str
    display_name: str
    protocol_version: int = PROTOCOL_VERSION
    app_version: str = ""
    mod_fingerprint: str = ""
    player_id: str = ""
    player_token: str = ""


@_register
@dataclass(frozen=True)
class CharacterSnapshot(Message):
    """The client's live character, pushed on join and on every change.

    ``character`` is :meth:`~mm_companion.core.character.Character.to_dict` output
    run through :func:`sanitize_snapshot`.
    """

    TYPE: ClassVar[str] = "character_snapshot"

    character: dict


@_register
@dataclass(frozen=True)
class RollRequest(Message):
    """A request to roll; the *server* resolves it so no client edits its numbers.

    ``label`` is free-form (``"Attack"``, ``"Athletics"``, …) so rolling from the
    character sheet can be added later without a protocol change. ``hidden`` is
    honored only for the GM — a hidden roll is never broadcast.
    """

    TYPE: ClassVar[str] = "roll_request"

    label: str = ""
    bonus: int = 0
    penalty: int = 0
    dc: int | None = None
    hidden: bool = False


@_register
@dataclass(frozen=True)
class RemoveRollRequest(Message):
    """A GM request to drop one roll from the shared log, by its ``seq``.

    Honored only for the GM (the server ignores it from a player); the removed
    roll is then announced to every client with :class:`RollRemoved`.
    """

    TYPE: ClassVar[str] = "remove_roll_request"

    seq: int


@_register
@dataclass(frozen=True)
class Ping(Message):
    """Keepalive; the server answers :class:`Pong` with the same ``nonce``."""

    TYPE: ClassVar[str] = "ping"

    nonce: int = 0


# --------------------------------------------------------------------------
# Server -> client
# --------------------------------------------------------------------------


@_register
@dataclass(frozen=True)
class Welcome(Message):
    """The handshake's answer: who you are, plus the current roster and history.

    ``roster`` entries carry neither a player's private token nor their character
    snapshot (characters stay on the server — see
    :meth:`~.model.PlayerSlot.roster_dict`). ``history`` is the recent slice of
    the visible roll log (:data:`~.server.WELCOME_HISTORY_ROLLS`) — hidden GM
    rolls are omitted here as well as from every later broadcast.
    """

    TYPE: ClassVar[str] = "welcome"

    session_id: str
    session_name: str
    player_id: str
    player_token: str = ""
    protocol_version: int = PROTOCOL_VERSION
    roster: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


@_register
@dataclass(frozen=True)
class Roster(Message):
    """The full player roster, re-sent whenever it changes."""

    TYPE: ClassVar[str] = "roster"

    players: list[dict] = field(default_factory=list)


@_register
@dataclass(frozen=True)
class RollAdded(Message):
    """One newly resolved roll, appended to everyone's history."""

    TYPE: ClassVar[str] = "roll_added"

    roll: dict


@_register
@dataclass(frozen=True)
class RollRemoved(Message):
    """One roll removed from the shared log, identified by its ``seq``."""

    TYPE: ClassVar[str] = "roll_removed"

    seq: int


@_register
@dataclass(frozen=True)
class ApplyCondition(Message):
    """A GM command putting a condition onto one player's live sheet."""

    TYPE: ClassVar[str] = "apply_condition"

    player_id: str
    condition_id: str
    parameter: str | None = None


@_register
@dataclass(frozen=True)
class RemoveCondition(Message):
    """A GM command taking a condition off one player's live sheet."""

    TYPE: ClassVar[str] = "remove_condition"

    player_id: str
    condition_id: str
    parameter: str | None = None


@_register
@dataclass(frozen=True)
class ErrorMessage(Message):
    """A refusal or a warning. ``code`` is one of the ``ERROR_*`` constants."""

    TYPE: ClassVar[str] = "error"

    code: str
    message: str = ""


@_register
@dataclass(frozen=True)
class Kicked(Message):
    """The server is dropping this connection; the client should not retry."""

    TYPE: ClassVar[str] = "kicked"

    reason: str = ""


@_register
@dataclass(frozen=True)
class Pong(Message):
    """The answer to a :class:`Ping`, echoing its ``nonce``."""

    TYPE: ClassVar[str] = "pong"

    nonce: int = 0


def message_types() -> dict[str, type[Message]]:
    """The ``type`` tag -> message class registry (a copy)."""
    return dict(_REGISTRY)


def encode(message: Message) -> bytes:
    """Render *message* as one newline-terminated UTF-8 JSON line.

    Raises :class:`ProtocolError` if the result exceeds
    :data:`MAX_MESSAGE_BYTES`, so an oversized snapshot is caught on the sending
    side rather than tripping the receiver's cap.
    """

    line = json.dumps(message.to_dict(), separators=(",", ":")).encode("utf-8") + b"\n"
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message of {len(line)} bytes exceeds the {MAX_MESSAGE_BYTES} cap")
    return line


def decode(line: bytes | str) -> Message:
    """Parse one framed line back into a message, validating every field.

    Raises :class:`ProtocolError` for an oversized line, malformed JSON, a
    non-object payload, a missing or unknown ``type``, or a field of the wrong
    shape.
    """

    if isinstance(line, str):
        raw_bytes = line.encode("utf-8")
    else:
        raw_bytes = bytes(line)
    if len(raw_bytes) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"line of {len(raw_bytes)} bytes exceeds the {MAX_MESSAGE_BYTES} cap")

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"expected a JSON object, got {type(payload).__name__}")

    tag = payload.get("type")
    if not isinstance(tag, str):
        raise ProtocolError("message has no 'type'")
    message_cls = _REGISTRY.get(tag)
    if message_cls is None:
        raise ProtocolError(f"unknown message type {tag!r}")
    return message_cls.from_payload(payload)


def sanitize_snapshot(character: dict) -> dict:
    """Strip anything from a character dict that must not travel between peers.

    Right now that is ``image_path``: it names a file on the *sender's* disk, and
    resolving it on the receiving end would read the receiver's own workspace and
    show the wrong picture. Portraits move later as a size-capped payload; until
    then a remote card shows a placeholder.
    """

    snapshot = dict(character)
    snapshot.pop("image_path", None)
    return snapshot
