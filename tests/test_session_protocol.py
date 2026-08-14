"""The session wire protocol: framing, round-tripping, and refusing bad input.

The server's listening socket is reachable from the internet, so ``decode`` is
the layer that has to be paranoid — every one of these "rejects" cases is a
line a hostile or mismatched peer could send.
"""

from __future__ import annotations

import json

import pytest

from mm_companion.core.session import protocol
from mm_companion.core.session.protocol import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    ApplyCondition,
    CharacterSnapshot,
    ControlHello,
    ControlWelcome,
    CreateSessionRequest,
    DeleteSessionRequest,
    ErrorMessage,
    Hello,
    Kicked,
    KickRequest,
    ListSessionsRequest,
    NoteRequest,
    Ping,
    PlayerSnapshot,
    Pong,
    ProtocolError,
    RemoveCondition,
    RemoveRollRequest,
    RenameSessionRequest,
    RollAdded,
    RollPrompt,
    RollRemoved,
    RollRequest,
    Roster,
    SessionCatalog,
    SessionInfo,
    SessionStatusRequest,
    SetHeroPoints,
    SetNpcPaths,
    SetSessionName,
    Welcome,
    decode,
    encode,
    sanitize_snapshot,
    sanitize_spec,
)

ROUND_TRIP_CASES = [
    Hello(token="abc", display_name="Alex", app_version="0.4.0", mod_fingerprint="base@1#0"),
    Hello(token="abc", display_name="Alex", player_id="p1", player_token="secret"),
    Hello(token="abc", display_name="Morgan", gm_token="gm-secret"),
    CharacterSnapshot(character={"power_level": 10, "abilities": {"str": 4}}),
    RollRequest(label="Athletics", bonus=6, penalty=2, dc=15, hidden=False),
    RollRequest(),  # a bare d20 with no DC
    NoteRequest(text="spent a hero point — 2 left"),
    RollPrompt(spec={"label": "Perception", "kind": "skill", "trait_key": "Perception", "dc": 15}),
    RollPrompt(),  # asking for nothing, which the server drops rather than records
    RemoveRollRequest(seq=3),
    KickRequest(player_id="p1", reason="afk"),
    SetSessionName(name="Friday Game"),
    SetNpcPaths(paths=["thug.json", "boss.json"]),
    Ping(nonce=7),
    Welcome(
        session_id="s1",
        session_name="Tuesday",
        player_id="p1",
        player_token="secret",
        roster=[{"player_id": "p1", "display_name": "Alex"}],
        history=[{"seq": 1, "die": 12}],
    ),
    Welcome(
        session_id="s1",
        session_name="Tuesday",
        player_id="gm",
        is_gm=True,
        npc_paths=["thug.json"],
    ),
    Roster(players=[{"player_id": "p1"}, {"player_id": "p2"}]),
    PlayerSnapshot(player_id="p1", character={"power_level": 10}),
    RollAdded(roll={"seq": 3, "die": 20, "degree": 2}),
    RollRemoved(seq=3),
    ApplyCondition(player_id="p1", condition_id="dazed", parameter="Strength"),
    RemoveCondition(player_id="p1", condition_id="dazed"),
    SetHeroPoints(player_id="p1", value=3),
    ErrorMessage(code=protocol.ERROR_BAD_TOKEN, message="wrong join code"),
    Kicked(reason="session closed"),
    Pong(nonce=7),
    # The hub control plane — a GM talking to the box, not to a session.
    ControlHello(),  # the ordinary case: no credential at all
    ControlHello(secret="operator-secret", app_version="0.4.0"),
    ControlWelcome(),
    ControlWelcome(operator=True, sessions=[{"id": "s1", "name": "Friday Game"}]),
    CreateSessionRequest(name="Friday Game"),
    DeleteSessionRequest(session_id="s1", gm_token="gm-secret"),
    RenameSessionRequest(session_id="s1", name="Saturday Game", gm_token="gm-secret"),
    SessionStatusRequest(session_id="s1", gm_token="gm-secret"),
    ListSessionsRequest(),
    SessionInfo(),  # the session is gone
    SessionInfo(session={"id": "s1", "name": "Friday Game", "join_code": "ABCDE"}),
    SessionCatalog(sessions=[{"id": "s1", "name": "Friday Game", "join_code": "ABCDE"}]),
]


@pytest.mark.parametrize("message", ROUND_TRIP_CASES, ids=lambda m: m.TYPE)
def test_every_message_round_trips(message: protocol.Message) -> None:
    assert decode(encode(message)) == message


def test_encoding_is_one_utf8_json_line() -> None:
    line = encode(Ping(nonce=1))

    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert json.loads(line.decode("utf-8")) == {"type": "ping", "nonce": 1}


def test_hello_defaults_to_the_current_protocol_version() -> None:
    assert Hello(token="t", display_name="A").protocol_version == PROTOCOL_VERSION


def test_the_protocol_version_is_the_one_the_keepalive_needs() -> None:
    """A deliberate tripwire, not a tautology.

    v7 was what stopped a mixed table over the keepalive: a v6 client never sends
    one, so a v7 server would reap it every ninety seconds. v8 adds
    :class:`RollPrompt`, which a v7 server rejects as an unknown type and a v7
    client would render as a d20 that rolled zero. Either way changing this number
    is a decision about who can still join, so it should not be possible to do by
    accident.
    """
    assert PROTOCOL_VERSION == 8


def test_every_registered_type_is_reachable_by_tag() -> None:
    registry = protocol.message_types()

    assert registry["hello"] is Hello
    assert {m.TYPE for m in ROUND_TRIP_CASES} == set(registry)


def test_decode_accepts_a_str_line_too() -> None:
    assert decode('{"type":"ping","nonce":4}') == Ping(nonce=4)


@pytest.mark.parametrize(
    "line",
    [
        b"not json at all",
        b"[1, 2, 3]",  # a JSON array, not an object
        b'"just a string"',
        b'{"nonce": 1}',  # no type
        b'{"type": 5}',  # type is not a string
        b'{"type": "not_a_real_message"}',
        b"\xff\xfe not utf-8",
    ],
)
def test_decode_rejects_malformed_lines(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode(line)


def test_decode_rejects_a_missing_required_field() -> None:
    with pytest.raises(ProtocolError, match="display_name"):
        decode(b'{"type": "hello", "token": "abc"}')


@pytest.mark.parametrize(
    "line",
    [
        b'{"type": "roll_request", "bonus": "lots"}',  # str where an int belongs
        b'{"type": "roll_request", "bonus": 2.5}',  # a float is not a rank
        b'{"type": "roll_request", "bonus": true}',  # nor is a bool
        b'{"type": "roll_request", "hidden": 1}',  # nor an int a bool
        b'{"type": "roll_request", "label": null}',  # a non-optional field is not nullable
        b'{"type": "character_snapshot", "character": []}',  # list where a dict belongs
        b'{"type": "roster", "players": {}}',  # dict where a list belongs
    ],
)
def test_decode_rejects_a_field_of_the_wrong_shape(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode(line)


def test_optional_fields_accept_null_and_omission() -> None:
    assert decode(b'{"type": "roll_request", "dc": null}') == RollRequest(dc=None)
    assert decode(
        b'{"type": "apply_condition", "player_id": "p", "condition_id": "c"}'
    ) == ApplyCondition(player_id="p", condition_id="c", parameter=None)


def test_unknown_extra_fields_are_ignored() -> None:
    # Forward compatibility: a newer peer's extra key must not break an older one.
    assert decode(b'{"type": "ping", "nonce": 2, "future_field": "?"}') == Ping(nonce=2)


def test_encode_refuses_an_oversized_message() -> None:
    huge = CharacterSnapshot(character={"blob": "x" * MAX_MESSAGE_BYTES})

    with pytest.raises(ProtocolError, match="exceeds"):
        encode(huge)


def test_decode_refuses_an_oversized_line() -> None:
    with pytest.raises(ProtocolError, match="exceeds"):
        decode(b"x" * (MAX_MESSAGE_BYTES + 1))


def test_sanitize_snapshot_drops_the_image_path() -> None:
    # An image path names a file on the *sender's* disk; resolving it on the
    # receiving end would read the receiver's own workspace.
    snapshot = sanitize_snapshot({"power_level": 10, "image_path": "C:/pics/hero.png"})

    assert snapshot == {"power_level": 10}


def test_sanitize_snapshot_does_not_mutate_its_input() -> None:
    original = {"image_path": "hero.png"}

    sanitize_snapshot(original)

    assert original == {"image_path": "hero.png"}


# -- roll specs ---------------------------------------------------------------
#
# A spec describes what is being rolled and travels so the *other* seats can act on
# it — the target's player clicks the save an attack forced. It is therefore
# client-supplied data that gets broadcast and rendered on everyone's screen, and
# this is the only place it is checked.


def test_a_spec_survives_with_its_chain_intact() -> None:
    spec = sanitize_spec(
        {
            "label": "7 vs. Defense",
            "modifier": 7,
            "follow_up": {"label": "Toughness vs. 18", "dc": 18, "outcomes": ["Dazed"]},
        }
    )

    assert spec["label"] == "7 vs. Defense"
    assert spec["follow_up"]["dc"] == 18
    assert spec["follow_up"]["outcomes"] == ["Dazed"]


def test_a_spec_without_a_label_is_not_a_spec() -> None:
    assert sanitize_spec({"modifier": 7}) is None
    assert sanitize_spec({"label": "   "}) is None
    assert sanitize_spec(None) is None
    assert sanitize_spec("a string") is None


def test_unknown_keys_are_dropped_rather_than_forwarded() -> None:
    # A whitelist, so a field added to RollSpec later cannot silently start crossing
    # the wire unchecked.
    spec = sanitize_spec({"label": "x", "surprise": {"deeply": "nested"}})

    assert spec == {"label": "x"}


def test_text_and_ladders_are_capped() -> None:
    spec = sanitize_spec(
        {
            "label": "L" * 5000,
            "hint": "H" * 5000,
            "outcomes": ["O" * 5000] * 500,
        }
    )

    assert len(spec["label"]) == protocol.MAX_SPEC_TEXT
    assert len(spec["hint"]) == protocol.MAX_SPEC_TEXT
    assert len(spec["outcomes"]) == protocol.MAX_SPEC_OUTCOMES
    assert len(spec["outcomes"][0]) == protocol.MAX_SPEC_TEXT


def test_a_deep_chain_is_cut_rather_than_followed() -> None:
    deep: dict = {"label": "bottom"}
    for _ in range(50):
        deep = {"label": "link", "follow_up": deep}

    spec = sanitize_spec(deep)

    depth = 0
    while spec is not None:
        depth += 1
        spec = spec.get("follow_up")
    assert depth == protocol.MAX_SPEC_DEPTH


def test_values_of_the_wrong_shape_are_dropped_not_coerced() -> None:
    spec = sanitize_spec(
        {
            "label": "x",
            "modifier": "lots",  # not a number
            "dc": True,  # a bool is not a DC, even though it is an int
            "outcomes": "Dazed",  # not a list
            "rolled_by_target": "yes",  # not a bool
        }
    )

    assert spec == {"label": "x"}
