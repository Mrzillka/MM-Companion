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
#:
#: v5 added the GM over the wire: ``Hello.gm_token``, the snapshot forward, the
#: kick/rename/cast commands, and the hub's control plane. The *fields* are all
#: additive and would have decoded either way, which is exactly why this is
#: bumped by hand — an old client would connect happily and then find its GM
#: token ignored and its hidden rolls broadcast. Better refused at the door.
PROTOCOL_VERSION = 5

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
#: The hub is already holding as many sessions as it is configured to.
ERROR_HUB_FULL = "hub_full"
#: The named session is not on this hub (deleted, or a stale entry in a GM's list).
ERROR_UNKNOWN_SESSION = "unknown_session"


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

    ``gm_token`` claims the GM's seat rather than a player's. Empty for everyone
    at the table; a wrong one is refused outright rather than quietly seating the
    claimant as a player, because a GM who joined without their powers would only
    find out halfway through a fight.
    """

    TYPE: ClassVar[str] = "hello"

    token: str
    display_name: str
    protocol_version: int = PROTOCOL_VERSION
    app_version: str = ""
    mod_fingerprint: str = ""
    player_id: str = ""
    player_token: str = ""
    gm_token: str = ""


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

    ``label`` is free-form (``"Attack"``, ``"Athletics"``, …) and names the roll in
    everyone's history. ``hidden`` is honored only for the GM — a hidden roll is
    never broadcast.

    ``spec`` is the sheet's description of what is being rolled — a serialized
    :class:`~mm_companion.core.rules.RollSpec`: the save this attack will force, the
    degree ladder that save reads, the resistance it is. It travels because the roll
    it describes is *acted on by somebody else*: the target's player sees the attack
    land and clicks the save straight off its card. The server treats it as opaque
    data — it validates the shape (:func:`sanitize_spec`) and records it, and never
    interprets it, so the headless server needs no game rules loaded.
    """

    TYPE: ClassVar[str] = "roll_request"

    label: str = ""
    bonus: int = 0
    penalty: int = 0
    dc: int | None = None
    hidden: bool = False
    spec: dict | None = None


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
class KickRequest(Message):
    """A GM request to remove a player from the session outright.

    Unlike a disconnect this drops the slot, so the player does not reclaim their
    seat with their old token. Honored only for the GM.
    """

    TYPE: ClassVar[str] = "kick_request"

    player_id: str
    reason: str = ""


@_register
@dataclass(frozen=True)
class SetSessionName(Message):
    """A GM request to rename the session. Honored only for the GM."""

    TYPE: ClassVar[str] = "set_session_name"

    name: str


@_register
@dataclass(frozen=True)
class SetNpcPaths(Message):
    """A GM request to store the session's NPC cast list. Honored only for the GM.

    The paths are filenames under the GM's own workspace and mean nothing to
    anyone else, which is exactly why they are only ever stored and handed back
    to the GM — they are never broadcast.
    """

    TYPE: ClassVar[str] = "set_npc_paths"

    paths: list[str] = field(default_factory=list)


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

    ``is_gm`` tells the client which seat it got, and ``npc_paths`` is filled
    **only for the GM** — empty for every player. The cast list names files in
    the GM's own workspace; it is kept on the server so a GM picking the session
    up from another machine still has it, and it means nothing to anyone else.
    """

    TYPE: ClassVar[str] = "welcome"

    session_id: str
    session_name: str
    player_id: str
    player_token: str = ""
    protocol_version: int = PROTOCOL_VERSION
    roster: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    is_gm: bool = False
    npc_paths: list[str] = field(default_factory=list)


@_register
@dataclass(frozen=True)
class Roster(Message):
    """The full player roster, re-sent whenever it changes."""

    TYPE: ClassVar[str] = "roster"

    players: list[dict] = field(default_factory=list)


@_register
@dataclass(frozen=True)
class PlayerSnapshot(Message):
    """One player's character, forwarded to a **remote GM only**.

    The roster deliberately carries no characters — re-broadcasting the table's
    combined sheets on every change would blow past
    :data:`MAX_MESSAGE_BYTES`. A GM in the hosting process reads them straight off
    :class:`~.model.SessionState`; a GM dialled in over a socket cannot, so their
    connection alone is sent this. It goes to the GM seat and nowhere else: no
    player needs another player's sheet.
    """

    TYPE: ClassVar[str] = "player_snapshot"

    player_id: str
    character: dict


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
class SetHeroPoints(Message):
    """A GM command setting one player's hero-point total on their live sheet."""

    TYPE: ClassVar[str] = "set_hero_points"

    player_id: str
    value: int


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


# Bounds on a :attr:`RollRequest.spec`. It is client-supplied, gets broadcast to
# every other seat, and is rendered as text on their screens — so it is checked into
# a known shape here rather than trusted. These are generous next to anything the
# sheet actually builds (a two-link chain of short phrases); they exist to stop a
# hostile client, not to constrain a legitimate one.
MAX_SPEC_TEXT = 200
MAX_SPEC_OUTCOMES = 12
MAX_SPEC_DEPTH = 4

# Everything a spec may carry, and how each value is coerced. Anything else in the
# dict is dropped: a whitelist, so a field added to RollSpec later does not silently
# start crossing the wire unchecked.
_SPEC_TEXT_KEYS = ("label", "kind", "hint", "success_outcome", "trait_key")
_SPEC_INT_KEYS = ("modifier", "dc")


def sanitize_spec(raw: object, _depth: int = 0) -> dict | None:
    """Check a client-supplied roll spec into a known shape, or drop it.

    Deliberately *structural*, not semantic: this file knows nothing about traits,
    saves or degree ladders, and must not — the standalone server
    (``python -m mm_companion.server``) loads no game data. It caps the text, the
    ladder, and the depth of the ``follow_up`` chain, and passes the rest through for
    :meth:`~mm_companion.core.rules.RollSpec.from_dict` to make sense of at the other
    end. Returns ``None`` for anything it cannot make a spec of, which simply costs
    that roll its chain.
    """

    if not isinstance(raw, dict) or _depth >= MAX_SPEC_DEPTH:
        return None
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        return None

    spec: dict = {"label": label[:MAX_SPEC_TEXT]}
    for key in _SPEC_TEXT_KEYS[1:]:
        value = raw.get(key)
        if isinstance(value, str) and value:
            spec[key] = value[:MAX_SPEC_TEXT]
    for key in _SPEC_INT_KEYS:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            spec[key] = value
    if raw.get("rolled_by_target") is True:
        spec["rolled_by_target"] = True
    outcomes = raw.get("outcomes")
    if isinstance(outcomes, list):
        rungs = [o[:MAX_SPEC_TEXT] for o in outcomes[:MAX_SPEC_OUTCOMES] if isinstance(o, str)]
        if rungs:
            spec["outcomes"] = rungs
    follow_up = sanitize_spec(raw.get("follow_up"), _depth + 1)
    if follow_up is not None:
        spec["follow_up"] = follow_up
    return spec


# --------------------------------------------------------------------------
# The hub control plane
#
# A second, much smaller conversation: not with a session, but with the box that
# holds them all. It answers one question a session cannot — "which sessions are
# there, and make me a new one" — and it is the reason only a GM can create a
# session, because it is gated on a secret that lives on the server and is given
# to the GM alone. A player never speaks this vocabulary; they have a join code,
# which names one session and opens nothing else.
# --------------------------------------------------------------------------


@_register
@dataclass(frozen=True)
class AdminHello(Message):
    """Open the control channel. Answered with a :class:`SessionCatalog`.

    ``secret`` is the hub's admin secret, not any one session's token.
    """

    TYPE: ClassVar[str] = "admin_hello"

    secret: str
    protocol_version: int = PROTOCOL_VERSION
    app_version: str = ""


@_register
@dataclass(frozen=True)
class CreateSessionRequest(Message):
    """Ask the hub to create and start hosting a new session."""

    TYPE: ClassVar[str] = "create_session_request"

    name: str


@_register
@dataclass(frozen=True)
class DeleteSessionRequest(Message):
    """Ask the hub to stop a session and erase it, roll history and all."""

    TYPE: ClassVar[str] = "delete_session_request"

    session_id: str


@_register
@dataclass(frozen=True)
class RenameSessionRequest(Message):
    """Ask the hub to rename a session."""

    TYPE: ClassVar[str] = "rename_session_request"

    session_id: str
    name: str


@_register
@dataclass(frozen=True)
class ListSessionsRequest(Message):
    """Ask for the catalog again, without changing anything."""

    TYPE: ClassVar[str] = "list_sessions_request"


@_register
@dataclass(frozen=True)
class SessionCatalog(Message):
    """Every session on the hub — the answer to *every* control request.

    Returning the whole catalog after a create, a rename and a delete alike costs
    a few hundred bytes and removes a whole class of bug: there is no partial
    update to apply, so a GM's list cannot drift out of step with the server's.

    Each entry carries ``id``, ``name``, ``join_code``, ``gm_token``,
    ``player_count``, ``roll_count``, ``connected`` and ``updated_at``. The join
    code and the gm token are the two things a GM cannot derive for themselves,
    and this channel is the only place either is handed out.
    """

    TYPE: ClassVar[str] = "session_catalog"

    sessions: list[dict] = field(default_factory=list)


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
