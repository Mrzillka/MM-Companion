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
:class:`RollRequest`, :class:`NoteRequest`, :class:`RollPrompt`,
:class:`RemoveRollRequest`, :class:`Ping`.

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
#:
#: v6 added notes to the history: :class:`NoteRequest`, and the ``kind``/``text``
#: fields on a roll record. Additive again, and refused for the same reason — an
#: old server drops a note on the floor, and an old client renders one as a d20
#: that rolled zero.
#:
#: v7 added no message at all — it changed what silence *means*. :class:`Ping`
#: and :class:`Pong` existed from the start and nothing ever sent one; from v7
#: every client keeps its link warm, and both ends drop a peer that has said
#: nothing for :data:`~.net.PEER_TIMEOUT`. A v6 client never pings, so against a
#: v7 server it would join happily and then be reaped every ninety seconds — the
#: same "works until it suddenly doesn't" failure v5 was bumped for. Refusing at
#: the door also means a table cannot end up half-updated, which is what makes
#: the relay's stock idle timeout safe to trust again.
#:
#: v8 added :class:`RollPrompt` — asking the table for a roll rather than making
#: one — and the ``kind="request"`` record it becomes. Additive, and bumped for
#: the v6 reason word for word: an old server rejects the unknown message type
#: outright, and an old client renders a request as a d20 that rolled zero.
#:
#: v9 added the **scene**: :class:`SetScene` / :class:`SceneUpdate` and the
#: portrait pair beside them, plus ``Welcome.scene``. Additive once more, and
#: bumped for the reason the last three were — a v8 client joins happily, never
#: learns the message type exists, and shows an empty board through the whole
#: fight the rest of the table is watching. A silent wrong answer, refused at
#: the door.
PROTOCOL_VERSION = 9

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
#: A rename or delete without the session's gm token. Deliberately the same
#: answer as :data:`ERROR_UNKNOWN_SESSION` would give in prose, so a stranger
#: cannot use the difference to learn which session ids exist.
ERROR_NOT_OWNER = "not_owner"
#: Carried by the :class:`Kicked` a server sends as it shuts down. Nobody did
#: anything wrong — the table has closed. It matters because it is the one thing
#: that distinguishes "the GM ended the session" from "the GM's laptop went to
#: sleep": without it a client cannot tell them apart and spends its whole retry
#: window redialling a session that is deliberately gone.
REASON_SESSION_CLOSED = "session_closed"


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
class NoteRequest(Message):
    """A request to write a line in the shared history without rolling anything.

    What it says — "spent a hero point — 2 left" — is composed by the client and
    carried verbatim, the way ``RollRequest.label`` is: what is worth noting is a
    rules question, and the server has no rules in it. The text is length-capped
    on the way in and attributed to the seat that sent it, so a note can no more
    impersonate someone than a roll label can.
    """

    TYPE: ClassVar[str] = "note_request"

    text: str = ""


@_register
@dataclass(frozen=True)
class RollPrompt(Message):
    """A request that *somebody else* roll something: "Perception vs DC 15".

    The asker rolls nothing. The server writes it into the shared log as a
    ``kind="request"`` record and broadcasts it, and every client — the asker's
    own screen included — renders it with a button that rolls it on that
    character's sheet.

    It carries one thing: the serialized
    :class:`~mm_companion.core.rules.RollSpec` naming the trait and its
    difficulty, run through :func:`sanitize_spec` exactly as a roll's is. Which is
    the point of the shape — a request needs no field a roll did not already have,
    so nothing about the spec on the wire changes and this file goes on knowing
    nothing about traits.
    """

    TYPE: ClassVar[str] = "roll_prompt"

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
class SetScene(Message):
    """The GM's whole scene, replacing whatever the server held. GM only.

    The scene is the shared half of the GM's board: who is in this fight, in what
    order, and what state they are visibly in. Unlike :class:`SetNpcPaths` — the
    other thing a GM stores here — it **is** broadcast, which is the whole point
    of it.

    It is sent whole rather than as add/remove/reorder deltas because it is
    small, it changes for half a dozen unrelated reasons (a condition applied, an
    initiative rolled, a card dragged, a player joining), and a delta stream has
    to be replayed in order to mean anything. One authority, one payload, no
    reconciliation.

    ``entries`` is a list of scene entries (see :func:`sanitize_scene` for the
    shape the server keeps). ``sources`` is the GM's private map of an entry's
    opaque ``ref`` to what it actually is — ``"npc:<file name>"`` or
    ``"player:<id>"``. It is stored and handed back to the GM alone, never
    broadcast, for the reason ``SetNpcPaths`` is not broadcast: an NPC's file
    name is the GM's, and "TheTraitorIsMarcus.json" is not a thing to put on a
    player's screen. It is what lets a GM pick the session up on another machine
    and still have a scene that maps onto their own creatures.
    """

    TYPE: ClassVar[str] = "set_scene"

    entries: list[dict] = field(default_factory=list)
    sources: dict = field(default_factory=dict)


@_register
@dataclass(frozen=True)
class SetScenePortrait(Message):
    """One scene entry's thumbnail, stored and broadcast on its own. GM only.

    Pictures travel apart from :class:`SetScene` for two reasons, and both are
    load-bearing. A scene is re-sent every time anything on it changes — often,
    mid-fight — and re-sending a dozen portraits with it is the one thing that
    could make a relayed table expensive. And a dozen portraits in one message
    would blow :data:`MAX_MESSAGE_BYTES` outright, whereas a dozen messages of
    one portrait each cannot.

    So a portrait is sent **once**, when its entry joins the scene, and replayed
    one message at a time to a client that joins later. ``portrait`` is a base64
    JPEG capped at :data:`MAX_SCENE_PORTRAIT_CHARS`; an empty one clears it.
    """

    TYPE: ClassVar[str] = "set_scene_portrait"

    ref: str
    portrait: str = ""


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

    ``scene`` is the current scene and goes to **everyone** — it is the one thing
    here the whole table is meant to see. ``scene_sources`` is its GM-only half
    (see :class:`SetScene`) and is filled like ``npc_paths``. The scene's
    *portraits* are deliberately absent: they follow as individual
    :class:`ScenePortrait` messages, since a welcome already carrying a roster
    and a slice of history has no room for a dozen pictures.
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
    scene: list[dict] = field(default_factory=list)
    scene_sources: dict = field(default_factory=dict)


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
class SceneUpdate(Message):
    """The scene, as everyone at the table sees it. Broadcast on every change.

    The GM's :class:`SetScene` reaching everyone else, minus its GM-only
    ``sources`` half. A client renders this and nothing else — there is no
    merging to do, because the GM sends the whole board every time.
    """

    TYPE: ClassVar[str] = "scene_update"

    entries: list[dict] = field(default_factory=list)


@_register
@dataclass(frozen=True)
class ScenePortrait(Message):
    """One scene entry's thumbnail reaching the table (see :class:`SetScenePortrait`).

    Broadcast when the GM sends one, and replayed to a joining client one message
    per portrait just after its :class:`Welcome`. A ``ref`` the receiver has no
    entry for is kept anyway: the picture may simply have arrived before the
    scene that names it.
    """

    TYPE: ClassVar[str] = "scene_portrait"

    ref: str
    portrait: str = ""


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


# Bounds on a scene. Same reasoning as the spec bounds above and the same shape of
# answer — the scene is GM-supplied, is broadcast to every seat and is rendered as
# text and pictures on their screens, so it is checked into a known shape rather
# than trusted. Generous next to any real fight; they exist to stop a hostile
# client, not a legitimate one.
MAX_SCENE_ENTRIES = 24
MAX_SCENE_CONDITIONS = 12
MAX_SCENE_TEXT = 80
#: Hard cap on one entry's base64 thumbnail. Far smaller than a *sheet* portrait
#: (:data:`~mm_companion.ui.session_portrait.PORTRAIT_MAX_CHARS`) because a scene
#: card shows a thumbnail rather than a picture, and because a whole scene's worth
#: of them is stored per session and replayed to every joiner.
MAX_SCENE_PORTRAIT_CHARS = 8 * 1024

#: What a creature on the board is *to the table*. The one field on a scene entry
#: that is the GM's judgement rather than a reading off a model, and the only
#: reason it is public: telling friend from foe at a glance is most of what a
#: player needs the board for, and it is a thing only the GM knows.
#:
#: ``player`` is not a fourth choice a GM makes — it is what a player's own entry
#: always is, set where the entry is built and never offered in the menu.
DISPOSITION_ENEMY = "enemy"
DISPOSITION_FRIENDLY = "friendly"
DISPOSITION_NEUTRAL = "neutral"
DISPOSITION_PLAYER = "player"
#: Every value the wire will carry. An entry naming anything else is not dropped —
#: it simply arrives without a disposition and is drawn as the default, which is
#: the same thing a pre-disposition sender produces.
SCENE_DISPOSITIONS = frozenset(
    {DISPOSITION_ENEMY, DISPOSITION_FRIENDLY, DISPOSITION_NEUTRAL, DISPOSITION_PLAYER}
)


def sanitize_scene(raw: object) -> list[dict]:
    """Check a GM-supplied scene into a known shape, dropping what does not fit.

    Structural like :func:`sanitize_spec`, and for the same reason: the standalone
    server loads no game data, so a condition id here is an opaque string and this
    file has no opinion about whether it names anything.

    One entry is
    ``{"ref", "name", "player_id", "initiative", "disposition", "conditions"}``:

    - ``ref`` is the GM's opaque handle for this entry and is the only required
      field — an entry without one cannot be addressed by a
      :class:`ScenePortrait`, so it is dropped whole rather than kept unusable.
    - ``initiative`` is an ``int`` or absent. Absent means *not rolled yet*, which
      is a different thing from nought and sorts differently.
    - ``conditions`` are ``{"id", "parameter", "count"}`` dicts — the shape
      :meth:`~mm_companion.core.character.AppliedCondition.to_dict` writes, minus
      ``provenance``, which is a bookkeeping detail of the sender's own tracker.
    - ``disposition`` is one of :data:`SCENE_DISPOSITIONS` or absent. Absent is a
      legitimate answer rather than an error: it is what every sender older than
      the field produces, and what an entry the GM has not judged produces too.

    This field is **additive without a protocol bump**, unlike the four bumps
    before it. Each of those prevented an old peer answering *wrongly* — an empty
    board, a request rendered as a d20 that rolled nought, a client reaped for
    never pinging. An old peer here draws exactly what it draws today: the same
    board, correctly, without the colour. That is a smaller readout, not a wrong
    one, and it is not worth refusing a table at the door for.
    """

    if not isinstance(raw, list):
        return []
    entries: list[dict] = []
    seen: set[str] = set()
    for item in raw[:MAX_SCENE_ENTRIES]:
        if not isinstance(item, dict):
            continue
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref.strip() or ref in seen:
            continue
        seen.add(ref)
        entry: dict = {"ref": ref[:MAX_SCENE_TEXT]}
        for key in ("name", "player_id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                entry[key] = value[:MAX_SCENE_TEXT]
        initiative = item.get("initiative")
        if isinstance(initiative, int) and not isinstance(initiative, bool):
            entry["initiative"] = initiative
        disposition = item.get("disposition")
        if isinstance(disposition, str) and disposition in SCENE_DISPOSITIONS:
            entry["disposition"] = disposition
        conditions = _sanitize_scene_conditions(item.get("conditions"))
        if conditions:
            entry["conditions"] = conditions
        entries.append(entry)
    return entries


def _sanitize_scene_conditions(raw: object) -> list[dict]:
    """The ``conditions`` half of one scene entry, whitelisted key by key."""

    if not isinstance(raw, list):
        return []
    conditions: list[dict] = []
    for item in raw[:MAX_SCENE_CONDITIONS]:
        if not isinstance(item, dict):
            continue
        condition_id = item.get("id")
        if not isinstance(condition_id, str) or not condition_id:
            continue
        applied: dict = {"id": condition_id[:MAX_SCENE_TEXT]}
        parameter = item.get("parameter")
        if isinstance(parameter, str) and parameter:
            applied["parameter"] = parameter[:MAX_SCENE_TEXT]
        count = item.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 1:
            applied["count"] = count
        conditions.append(applied)
    return conditions


def sanitize_scene_portrait(raw: object) -> str:
    """A scene entry's thumbnail, or ``""`` for anything that is not one.

    Over-long is dropped rather than truncated: half a JPEG is not a smaller
    JPEG, and a card showing its placeholder is a better answer than one showing
    a broken image.
    """

    if not isinstance(raw, str) or len(raw) > MAX_SCENE_PORTRAIT_CHARS:
        return ""
    return raw


def sanitize_scene_portraits(raw: object) -> dict:
    """A whole ``ref`` → thumbnail map, each value checked by
    :func:`sanitize_scene_portrait`. An entry that fails simply loses its picture."""

    if not isinstance(raw, dict):
        return {}
    portraits: dict = {}
    for key, value in list(raw.items())[:MAX_SCENE_ENTRIES]:
        if not isinstance(key, str) or not key:
            continue
        portrait = sanitize_scene_portrait(value)
        if portrait:
            portraits[key[:MAX_SCENE_TEXT]] = portrait
    return portraits


def sanitize_scene_sources(raw: object) -> dict:
    """The GM's private ``ref`` → source map, capped and stringified.

    Never broadcast (see :class:`SetScene`), but stored on the server and handed
    back on a later welcome, so it is bounded like everything else that persists.
    """

    if not isinstance(raw, dict):
        return {}
    sources: dict = {}
    for key, value in list(raw.items())[:MAX_SCENE_ENTRIES]:
        if isinstance(key, str) and isinstance(value, str) and key and value:
            sources[key[:MAX_SCENE_TEXT]] = value[:MAX_SCENE_TEXT]
    return sources


# --------------------------------------------------------------------------
# The hub control plane
#
# A second, much smaller conversation: not with a session, but with the box that
# holds them all. Anyone running the app may open it and make a session — the
# server is a public utility, not one GM's private property.
#
# What that costs is a rule about *ownership*, and it is the whole design here:
#
#   Creating is open. Everything else needs the session's gm token, which the
#   create handed back and nobody else ever sees.
#
# So there is no way to enumerate other people's tables, and no way to rename or
# delete one you did not make. A GM's own list of sessions lives in their app,
# not on the server, because a server-side list is exactly the thing that would
# leak everyone's join codes to whoever asked.
#
# The operator's secret is the one exception, for the person paying for the box:
# it opens the full catalog so abandoned or abusive sessions can be cleaned up.
# --------------------------------------------------------------------------


@_register
@dataclass(frozen=True)
class ControlHello(Message):
    """Open the control channel; answered with :class:`ControlWelcome`.

    ``secret`` is **empty for everybody normally** — creating a session needs no
    credential. It carries the server's operator secret only for whoever runs the
    box, and a wrong one is refused rather than quietly downgraded, so an operator
    never believes they have powers they do not.
    """

    TYPE: ClassVar[str] = "control_hello"

    secret: str = ""
    protocol_version: int = PROTOCOL_VERSION
    app_version: str = ""


@_register
@dataclass(frozen=True)
class ControlWelcome(Message):
    """The channel is open. ``operator`` says whether the secret was accepted.

    ``sessions`` is the full catalog for an operator and **empty for everyone
    else** — an ordinary GM learns about their own sessions from the answers to
    their own requests, never from a list of the server's.
    """

    TYPE: ClassVar[str] = "control_welcome"

    operator: bool = False
    sessions: list[dict] = field(default_factory=list)


@_register
@dataclass(frozen=True)
class CreateSessionRequest(Message):
    """Make a new session. Needs no credential — anyone may host a game."""

    TYPE: ClassVar[str] = "create_session_request"

    name: str


@_register
@dataclass(frozen=True)
class DeleteSessionRequest(Message):
    """Stop a session and erase it, roll history and all.

    ``gm_token`` proves this is the session's own GM. An operator channel may
    leave it empty; anyone else without it is refused.
    """

    TYPE: ClassVar[str] = "delete_session_request"

    session_id: str
    gm_token: str = ""


@_register
@dataclass(frozen=True)
class RenameSessionRequest(Message):
    """Rename a session. Same ownership rule as deleting it."""

    TYPE: ClassVar[str] = "rename_session_request"

    session_id: str
    name: str
    gm_token: str = ""


@_register
@dataclass(frozen=True)
class SessionStatusRequest(Message):
    """Ask after one session this GM already knows the token for.

    How an app refreshes its own list: is the session still there, and who is in
    it? Answered with a :class:`SessionInfo` whose ``session`` is empty when the
    session is gone — which is also how a GM learns theirs was swept.
    """

    TYPE: ClassVar[str] = "session_status_request"

    session_id: str
    gm_token: str = ""


@_register
@dataclass(frozen=True)
class ListSessionsRequest(Message):
    """The whole catalog. **Operator only** — refused on an ordinary channel."""

    TYPE: ClassVar[str] = "list_sessions_request"


@_register
@dataclass(frozen=True)
class SessionInfo(Message):
    """One session — the answer to create, rename, delete and status alike.

    ``session`` carries ``id``, ``name``, ``join_code``, ``gm_token``,
    ``player_count``, ``roll_count``, ``connected`` and ``updated_at``, and is
    **empty** when the session no longer exists. The join code and the gm token
    are the two things a GM cannot derive for themselves, and they are handed out
    here and nowhere else.
    """

    TYPE: ClassVar[str] = "session_info"

    session: dict = field(default_factory=dict)


@_register
@dataclass(frozen=True)
class SessionCatalog(Message):
    """Every session on the hub. Only ever sent to an operator."""

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
