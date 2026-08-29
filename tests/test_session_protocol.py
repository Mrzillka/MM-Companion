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
    SCENE_DISPOSITIONS,
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
    ModNote,
    ModRequest,
    ModStateUpdate,
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
    ScenePortrait,
    SceneUpdate,
    SessionCatalog,
    SessionInfo,
    SessionStatusRequest,
    SetHeroPoints,
    SetModState,
    SetNpcPaths,
    SetScene,
    SetScenePortrait,
    SetSessionName,
    Welcome,
    decode,
    encode,
    sanitize_mod_id,
    sanitize_mod_payload,
    sanitize_mod_state,
    sanitize_scene,
    sanitize_scene_portrait,
    sanitize_scene_sources,
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
    SetScene(
        entries=[{"ref": "e1", "name": "Thug", "initiative": 14}],
        sources={"e1": "npc:thug.json"},
    ),
    SetScene(),  # a scene the GM has just emptied
    SetScenePortrait(ref="e1", portrait="AAAA"),
    SetModState(mod_id="timers", key="t1", payload={"kind": "timer", "remaining": 90}),
    SetModState(mod_id="timers", key="t1"),  # the deletion a share toggle sends
    ModNote(mod_id="timers", text="Timer Bomb finished"),
    ModRequest(mod_id="timers", topic="nudge", payload={"id": "t1"}),
    ModRequest(mod_id="timers", topic="nudge", player_id="p1"),  # as forwarded
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
        scene=[{"ref": "e1", "name": "Thug"}],
        scene_sources={"e1": "npc:thug.json"},
        mod_state={"timers": {"t1": {"kind": "timer"}}},
    ),
    Roster(players=[{"player_id": "p1"}, {"player_id": "p2"}]),
    PlayerSnapshot(player_id="p1", character={"power_level": 10}),
    RollAdded(roll={"seq": 3, "die": 20, "degree": 2}),
    ModStateUpdate(mod_id="timers", key="t1", payload={"kind": "counter", "filled": [0, 2]}),
    ModStateUpdate(mod_id="timers", key="t1"),  # the entry is gone, not empty
    RollRemoved(seq=3),
    SceneUpdate(entries=[{"ref": "e1", "name": "Thug", "initiative": 14}]),
    ScenePortrait(ref="e1", portrait="AAAA"),
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
    client would render as a d20 that rolled zero. v9 adds the scene, whose
    failure is quieter and worse: a v8 client joins, never learns the message
    type exists, and shows an empty board for the whole fight. v10 opens the wire
    to mods, and fails the same quiet way: ``_handle`` has no ``else``, so a v9
    server drops a ``set_mod_state`` without a word and the GM runs the fight
    believing the table can see their timers. Either way changing this number is a
    decision about who can still join, so it should not be possible to do by
    accident.
    """
    assert PROTOCOL_VERSION == 10


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


# -- the scene -------------------------------------------------------------
#
# The scene is GM-supplied, is broadcast to every seat and is rendered as text
# and pictures on their screens, so it gets the same paranoia the spec does.


def test_a_scene_entry_keeps_only_the_fields_the_wire_knows() -> None:
    scene = sanitize_scene(
        [
            {
                "ref": "e1",
                "name": "Thug",
                "player_id": "p1",
                "initiative": 14,
                "conditions": [{"id": "hit", "count": 3, "provenance": "incapacitated"}],
                "toughness": 8,  # not a thing a player is allowed to read off a card
            }
        ]
    )

    assert scene == [
        {
            "ref": "e1",
            "name": "Thug",
            "player_id": "p1",
            "initiative": 14,
            "conditions": [{"id": "hit", "count": 3}],
        }
    ]


def test_an_entry_without_a_ref_is_dropped_whole() -> None:
    """A ref is how a portrait finds its entry, so an entry without one is unusable."""
    assert sanitize_scene([{"name": "Nobody"}, {"ref": "e1"}]) == [{"ref": "e1"}]


def test_a_repeated_ref_is_dropped() -> None:
    """Two entries answering to one ref would share a portrait and a place in the order."""
    scene = sanitize_scene([{"ref": "e1", "name": "First"}, {"ref": "e1", "name": "Second"}])

    assert scene == [{"ref": "e1", "name": "First"}]


def test_an_unrolled_initiative_is_absent_rather_than_zero() -> None:
    """Not rolled yet and rolled a nought sort differently, so they must not collapse."""
    assert sanitize_scene([{"ref": "e1", "initiative": None}]) == [{"ref": "e1"}]
    assert sanitize_scene([{"ref": "e1", "initiative": 0}]) == [{"ref": "e1", "initiative": 0}]
    # A bool is an int, and is not an initiative.
    assert sanitize_scene([{"ref": "e1", "initiative": True}]) == [{"ref": "e1"}]


def test_a_scene_is_capped_at_a_sensible_number_of_entries() -> None:
    huge = [{"ref": f"e{n}"} for n in range(protocol.MAX_SCENE_ENTRIES * 3)]

    assert len(sanitize_scene(huge)) == protocol.MAX_SCENE_ENTRIES


def test_anything_that_is_not_a_list_of_dicts_is_simply_no_scene() -> None:
    assert sanitize_scene("a fight") == []
    assert sanitize_scene(None) == []
    assert sanitize_scene(["Thug", 3]) == []


def test_an_oversized_portrait_is_dropped_rather_than_truncated() -> None:
    """Half a JPEG is not a smaller JPEG — a placeholder beats a broken image."""
    assert sanitize_scene_portrait("A" * (protocol.MAX_SCENE_PORTRAIT_CHARS + 1)) == ""
    assert sanitize_scene_portrait("A" * 16) == "A" * 16
    assert sanitize_scene_portrait(None) == ""


def test_a_scene_entry_carries_a_known_disposition_and_no_other() -> None:
    """Public on purpose — telling friend from foe is most of what a player needs
    the board for, and it is a thing only the GM knows."""
    entries = sanitize_scene(
        [
            {"ref": "a", "disposition": "friendly"},
            {"ref": "b", "disposition": "warlord"},
            {"ref": "c", "disposition": 7},
            {"ref": "d"},
        ]
    )

    assert entries[0]["disposition"] == "friendly"
    # Not an error, and not dropped: an unknown or absent value is exactly what a
    # sender older than the field produces, and it renders as the default.
    assert [e for e in entries if "disposition" not in e] == [
        {"ref": "b"},
        {"ref": "c"},
        {"ref": "d"},
    ]


def test_every_disposition_the_ui_offers_survives_the_wire() -> None:
    entries = sanitize_scene(
        [{"ref": value, "disposition": value} for value in sorted(SCENE_DISPOSITIONS)]
    )

    assert {e["disposition"] for e in entries} == set(SCENE_DISPOSITIONS)


def test_scene_sources_keep_only_string_pairs() -> None:
    sources = sanitize_scene_sources({"e1": "npc:thug.json", 2: "npc:x", "e3": None, "": "npc:y"})

    assert sources == {"e1": "npc:thug.json"}


# --------------------------------------------------------------------------
# The mod channel
#
# What is bounded here is the payload's *shape* rather than its fields. Every
# other sanitizer in this file whitelists keys it knows; a mod payload by
# definition has none this file has heard of, so the tests are about depth,
# width, size and which JSON types may appear.
# --------------------------------------------------------------------------


def test_a_mod_id_may_not_be_a_path() -> None:
    """It becomes a dict key here and a *filename* in ``core.storage``.

    Refusing the shape once, at the door, is what means no later caller has to
    remember that it arrived over a socket.
    """
    assert sanitize_mod_id("timers") == "timers"
    assert sanitize_mod_id("my.mod-2_x") == "my.mod-2_x"

    assert sanitize_mod_id("../../etc/passwd") == ""
    assert sanitize_mod_id("a/b") == ""
    assert sanitize_mod_id("a\b") == ""
    assert sanitize_mod_id("") == ""
    assert sanitize_mod_id("   ") == ""
    assert sanitize_mod_id("x" * (protocol.MAX_MOD_ID + 1)) == ""
    assert sanitize_mod_id(7) == ""

    # A dot is legal *inside* an id, which is why the charset alone was not
    # enough: ".." passed it and became a file called "...json". Contained rather
    # than dangerous, but nonsense — so an id must start with a letter or digit,
    # which also rules out hidden-file names and leading dashes.
    assert sanitize_mod_id("..") == ""
    assert sanitize_mod_id(".") == ""
    assert sanitize_mod_id(".hidden") == ""
    assert sanitize_mod_id("-dash") == ""


def test_a_mod_payload_keeps_plain_json_and_drops_everything_else() -> None:
    payload = sanitize_mod_payload(
        {
            "kind": "timer",
            "remaining": 97,
            "stamped_at": 1756468800.5,
            "running": True,
            "filled": [0, 1, 4],
            "cleared": None,
            "callback": print,  # not JSON, and not something to carry
        }
    )

    assert payload == {
        "kind": "timer",
        "remaining": 97,
        "stamped_at": 1756468800.5,
        "running": True,
        "filled": [0, 1, 4],
        "cleared": None,
    }


def test_a_float_survives_here_and_a_nan_does_not() -> None:
    """``float`` is admitted in this sanitizer and no other.

    Everywhere else in this file a number is a rank or a modifier, where a float
    is a bug. A mod state routinely carries a wall-clock stamp, and rounding that
    to a whole second would cost the precision that keeps two screens' countdowns
    agreeing. The infinities and NaN still go: ``json.dumps`` renders them as bare
    words no other JSON parser accepts, so a payload carrying one would encode
    here and fail to decode at the far end.
    """
    payload = sanitize_mod_payload(
        {"ok": 1.5, "nan": float("nan"), "inf": float("inf"), "ninf": float("-inf")}
    )

    assert payload == {"ok": 1.5}


def test_a_payload_that_is_not_an_object_is_no_payload() -> None:
    assert sanitize_mod_payload([1, 2, 3]) is None
    assert sanitize_mod_payload("timer") is None
    assert sanitize_mod_payload(None) is None
    assert sanitize_mod_payload(7) is None


def test_a_deep_payload_is_cut_rather_than_followed() -> None:
    deep: dict = {"bottom": 1}
    for _ in range(50):
        deep = {"down": deep}

    payload = sanitize_mod_payload(deep)

    depth = 0
    node = payload
    while isinstance(node, dict) and "down" in node:
        depth += 1
        node = node["down"]
    assert depth == protocol.MAX_MOD_DEPTH


def test_a_payload_is_capped_by_width_text_and_encoded_size() -> None:
    wide = sanitize_mod_payload({f"k{i}": i for i in range(protocol.MAX_MOD_ITEMS * 4)})
    assert len(wide) == protocol.MAX_MOD_ITEMS

    long_list = sanitize_mod_payload({"xs": list(range(protocol.MAX_MOD_ITEMS * 4))})
    assert len(long_list["xs"]) == protocol.MAX_MOD_ITEMS

    text = sanitize_mod_payload({"label": "L" * 5000})
    assert len(text["label"]) == protocol.MAX_MOD_TEXT

    # Over the encoded cap is dropped whole rather than trimmed: half a payload
    # is not a smaller payload, and a mod showing nothing beats one showing a
    # mangled half of something.
    huge = {str(i): "y" * protocol.MAX_MOD_TEXT for i in range(protocol.MAX_MOD_ITEMS)}
    assert sanitize_mod_payload(huge) is None


def test_mod_state_drops_what_it_cannot_keep_and_counts_what_it_can() -> None:
    state = sanitize_mod_state(
        {
            "timers": {"t1": {"x": 1}, "t2": "not a payload"},
            "../evil": {"t1": {"x": 1}},
            "empty": {"t1": "not a payload"},
            "wrong": "not a mapping",
        }
    )

    # A mod whose entries all fail is dropped rather than kept empty, so the cap
    # counts mods that actually hold something.
    assert state == {"timers": {"t1": {"x": 1}}}


def test_mod_state_is_bounded_in_both_directions() -> None:
    too_many_keys = {f"k{i}": {"i": i} for i in range(protocol.MAX_MOD_KEYS * 3)}
    state = sanitize_mod_state({"timers": too_many_keys})
    assert len(state["timers"]) == protocol.MAX_MOD_KEYS

    too_many_mods = {f"mod{i}": {"k": {"i": i}} for i in range(protocol.MAX_MOD_IDS * 3)}
    state = sanitize_mod_state(too_many_mods)
    assert len(state) == protocol.MAX_MOD_IDS


def test_a_whole_mod_state_fits_in_one_welcome() -> None:
    """The bound that makes ``Welcome.mod_state`` safe to send whole.

    The scene's portraits are *not* in the welcome precisely because a dozen of
    them can aggregate past the message cap. Mod state is in it because it cannot
    — but that is true because of :data:`MAX_MOD_STATE_CHARS`, **not** because of
    the per-entry caps. Those multiply out to 2 MiB, eight times the message cap,
    which is exactly the assumption this test was written to check and which it
    caught being wrong.

    So the aggregate cap is the guarantee, and this keeps it honest against the
    message cap if anyone retunes either.
    """
    assert protocol.MAX_MOD_STATE_CHARS < MAX_MESSAGE_BYTES

    # And the per-entry caps on their own emphatically do not give it, which is
    # why the aggregate one exists at all.
    per_entry = protocol.MAX_MOD_IDS * protocol.MAX_MOD_KEYS * protocol.MAX_MOD_PAYLOAD_CHARS
    assert per_entry > MAX_MESSAGE_BYTES


def test_mod_state_is_clamped_to_the_aggregate_cap() -> None:
    """A full map is trimmed at the tail rather than emptied.

    A session file that has somehow grown past the cap should come back mostly
    intact — the alternative is a GM opening a campaign to a blank board.
    """
    fat = {"x": "y" * (protocol.MAX_MOD_TEXT - 1)}
    raw = {f"mod{i}": {f"k{j}": dict(fat) for j in range(protocol.MAX_MOD_KEYS)} for i in range(8)}

    state = sanitize_mod_state(raw)

    assert protocol.mod_state_chars(state) <= protocol.MAX_MOD_STATE_CHARS
    assert state, "the cap should trim the tail, not empty the map"
