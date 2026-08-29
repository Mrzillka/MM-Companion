"""The session state model: the roster, the roll log, and the session itself.

Plain mutable data in the same idiom as
:mod:`mm_companion.core.character` — ``to_dict``/``from_dict`` on every record,
no PySide6, and no rules math (resolution lives in :mod:`mm_companion.core.dice`,
persistence in :mod:`.store`). The server owns one :class:`SessionState` and
mutates it as messages arrive; the store writes it back out.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mm_companion.core.dice import CheckResult
from mm_companion.core.session.protocol import (
    MAX_MOD_IDS,
    MAX_MOD_KEY,
    MAX_MOD_KEYS,
    MAX_MOD_STATE_CHARS,
    mod_state_chars,
    sanitize_mod_id,
    sanitize_mod_payload,
    sanitize_mod_state,
    sanitize_scene,
    sanitize_scene_portraits,
    sanitize_scene_sources,
)


def utc_now() -> str:
    """The current UTC time as an ISO-8601 string — the timestamp form used here."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(length: int = 12) -> str:
    """A short, filesystem-safe random id (session and player ids)."""
    return uuid.uuid4().hex[:length]


def new_token() -> str:
    """A secret bearer token: the session's join secret, or a player's slot claim."""
    return secrets.token_urlsafe(16)


def tokens_match(presented: str, expected: str) -> bool:
    """Constant-time bearer-token equality.

    Compared as UTF-8 bytes because :func:`secrets.compare_digest` raises
    ``TypeError`` for non-ASCII ``str`` — and *presented* arrives off the wire,
    where a hostile peer may put anything in a token field. A mismatch of any
    kind must come out as ``False``, never as an exception in the handler thread.
    """
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


@dataclass
class PlayerSlot:
    """One seat in the session — a connected player, or one who has dropped.

    A slot outlives its connection: when a player disconnects the slot stays,
    carrying their last character snapshot, so a reconnect (proved by
    ``token``) resumes the same card rather than creating a second one.

    ``character`` is a sanitized :meth:`~mm_companion.core.character.Character.to_dict`
    (see :func:`~.protocol.sanitize_snapshot`), empty until the first snapshot lands.
    ``is_gm`` marks the host's own slot.
    """

    player_id: str
    display_name: str
    token: str = ""
    character: dict = field(default_factory=dict)
    connected: bool = False
    is_gm: bool = False
    joined_at: str = field(default_factory=utc_now)
    last_seen: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        """Full serialization, including the private ``token`` (for the store only)."""
        return {
            "player_id": self.player_id,
            "display_name": self.display_name,
            "token": self.token,
            "character": dict(self.character),
            "connected": self.connected,
            "is_gm": self.is_gm,
            "joined_at": self.joined_at,
            "last_seen": self.last_seen,
        }

    def public_dict(self) -> dict:
        """The slot without its secret — event payloads on the host's own side."""
        data = self.to_dict()
        data.pop("token", None)
        return data

    def roster_dict(self) -> dict:
        """What a roster entry looks like on the wire: no token, no character.

        The roster is re-broadcast to everyone on every join and every snapshot,
        so embedding each player's full character would make every broadcast
        carry the table's combined sheet size — enough, at a full table, to trip
        :data:`~.protocol.MAX_MESSAGE_BYTES` and break the session. Characters
        stay on the server; the GM reads them via snapshot events, and no player
        client needs another player's sheet.
        """
        data = self.public_dict()
        data.pop("character", None)
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> PlayerSlot:
        return cls(
            player_id=str(raw.get("player_id", "")),
            display_name=str(raw.get("display_name", "")),
            token=str(raw.get("token", "")),
            character=dict(raw.get("character", {})),
            connected=bool(raw.get("connected", False)),
            is_gm=bool(raw.get("is_gm", False)),
            joined_at=str(raw.get("joined_at", "")) or utc_now(),
            last_seen=str(raw.get("last_seen", "")) or utc_now(),
        )


#: A record of a roll — the ordinary entry, with a die and a grade.
KIND_ROLL = "roll"
#: A record of something that merely *happened* at the table and is worth writing
#: down: a hero point spent, a hero point granted. It carries ``text`` and none of
#: the dice fields, and the server composes none of it (see :meth:`SessionState.record_note`).
KIND_NOTE = "note"
#: A roll somebody **asked** for and nobody has made: "everyone roll Perception vs
#: DC 15". Like a note it has no die and no grade; unlike one it carries a ``spec``,
#: which is what puts a 🎲 button on every client's card. Whoever clicks it
#: rolls it on their own sheet — the asker's screen included (see
#: :meth:`SessionState.record_request`).
KIND_REQUEST = "request"
#: A line a **mod** wrote: "Timer *Bomb* finished". Like a note it carries only
#: ``text`` and no dice, and unlike one it names the mod that wrote it in
#: ``mod_id`` — which is what lets a client that has that mod render the line its
#: own way while one that does not still reads the plain sentence. Written by the
#: GM's mod alone, so a countdown a dozen screens are watching says so once (see
#: :meth:`SessionState.record_mod_note`).
KIND_MOD = "mod"


@dataclass(frozen=True)
class RollRecord:
    """One entry in the shared history: a resolved roll, a note, or a request.

    ``kind`` says which (:data:`KIND_ROLL`, :data:`KIND_NOTE`, :data:`KIND_REQUEST`
    or :data:`KIND_MOD`). One record type covers all four because the history
    *is* the log — seq-numbered, appended to
    ``rolls.jsonl``, replayed to a late joiner, strikeable by the GM — and a note
    wants every one of those. A note leaves the dice fields at their defaults and
    carries its sentence in ``text``; a roll leaves ``text`` empty; a request
    leaves both the die and the grade alone and says what it asks for in ``label``,
    ``dc`` and ``spec``.

    A roll is resolved *server-side* (:func:`~mm_companion.core.dice.resolve_check`)
    and then broadcast, so the numbers are the server's, not a client's claim.
    ``dc`` is ``None`` for a bare d20 with no target, in which case ``degree`` is
    ``None`` too — there is nothing to grade against.

    ``hidden`` marks a GM roll that is stored but never broadcast; ``seq`` is the
    session-wide ordering, assigned by :meth:`SessionState.record_roll`.

    ``spec`` is the sheet's description of the roll (a serialized
    :class:`~mm_companion.core.rules.RollSpec`), carried verbatim from the request
    and validated on the way in by
    :func:`~mm_companion.core.session.protocol.sanitize_spec`. It is what lets
    *another* player's screen offer the save an attack forced, and read the same
    outcome off the same ladder. Opaque here — this module never interprets it.

    ``mod_id`` names the mod behind a :data:`KIND_MOD` line and is empty for
    everything else. It is a field of its own rather than something parsed back
    out of ``label`` because a reader has to be able to tell "a mod I have wrote
    this" from "a mod I do not have wrote this" *before* deciding how to draw it,
    and a prose label cannot answer that.
    """

    seq: int
    player_id: str
    player_name: str
    die: int
    bonus: int = 0
    penalty: int = 0
    dc: int | None = None
    degree: int | None = None
    critical: bool = False
    label: str = ""
    hidden: bool = False
    spec: dict | None = None
    kind: str = KIND_ROLL
    text: str = ""
    mod_id: str = ""
    timestamp: str = field(default_factory=utc_now)

    @property
    def modifier(self) -> int:
        """The net modifier applied to the die."""
        return self.bonus - self.penalty

    @property
    def total(self) -> int:
        """The check total — die plus the net modifier."""
        return self.die + self.modifier

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "die": self.die,
            "bonus": self.bonus,
            "penalty": self.penalty,
            "dc": self.dc,
            "degree": self.degree,
            "critical": self.critical,
            "label": self.label,
            "hidden": self.hidden,
            "spec": self.spec,
            "kind": self.kind,
            "text": self.text,
            "mod_id": self.mod_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> RollRecord:
        dc = raw.get("dc")
        degree = raw.get("degree")
        return cls(
            seq=int(raw.get("seq", 0)),
            player_id=str(raw.get("player_id", "")),
            player_name=str(raw.get("player_name", "")),
            die=int(raw.get("die", 0)),
            bonus=int(raw.get("bonus", 0)),
            penalty=int(raw.get("penalty", 0)),
            dc=None if dc is None else int(dc),
            degree=None if degree is None else int(degree),
            critical=bool(raw.get("critical", False)),
            label=str(raw.get("label", "")),
            hidden=bool(raw.get("hidden", False)),
            spec=raw.get("spec") if isinstance(raw.get("spec"), dict) else None,
            # A line written to rolls.jsonl before notes existed is a roll.
            kind=str(raw.get("kind", "")) or KIND_ROLL,
            text=str(raw.get("text", "")),
            mod_id=str(raw.get("mod_id", "")),
            timestamp=str(raw.get("timestamp", "")) or utc_now(),
        )

    @classmethod
    def from_check(
        cls,
        *,
        seq: int,
        player_id: str,
        player_name: str,
        die: int,
        bonus: int = 0,
        penalty: int = 0,
        result: CheckResult | None = None,
        label: str = "",
        hidden: bool = False,
        spec: dict | None = None,
    ) -> RollRecord:
        """Build a record from a resolved check (``None`` when no DC was set)."""
        return cls(
            seq=seq,
            player_id=player_id,
            player_name=player_name,
            die=die,
            bonus=bonus,
            penalty=penalty,
            dc=None if result is None else result.dc,
            degree=None if result is None else result.degree,
            critical=die in (1, 20) if result is None else result.critical,
            label=label,
            hidden=hidden,
            spec=spec,
        )


@dataclass
class SessionState:
    """Everything one online session consists of.

    The host token is the join secret carried by the join code; ``players`` is
    keyed by player id; ``npc_paths`` are filenames under the workspace
    ``gm_characters/`` dir (GM-only, never sent to a player); ``rolls`` is the
    full history *including* hidden GM rolls, which are filtered out on the way
    to the wire by :meth:`visible_rolls`.

    The **gm token** is the second, quieter secret. Where the host token opens a
    seat to anyone holding the join code, this one claims *the GM's* seat, with
    the powers that go with it — a hidden roll, a struck roll, a condition laid
    on a player. It is deliberately not in the join code: everyone at the table
    has that. It goes to one person, once, and it is what lets the GM drive a
    session hosted on a machine that is not theirs.
    """

    id: str = field(default_factory=new_id)
    name: str = "Session"
    host_token: str = field(default_factory=new_token)
    gm_token: str = field(default_factory=new_token)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    players: dict[str, PlayerSlot] = field(default_factory=dict)
    npc_paths: list[str] = field(default_factory=list)
    rolls: list[RollRecord] = field(default_factory=list)
    #: The shared board: who is in this fight, in what order, and what state they
    #: are visibly in. Authored whole by the GM
    #: (:class:`~.protocol.SetScene`), stored here so a table hosted on a server
    #: survives the GM's laptop, and broadcast to everyone — the one thing in this
    #: object that is *meant* to be seen by the table.
    scene: list[dict] = field(default_factory=list)
    #: The GM's private half of the scene: an entry's opaque ``ref`` to what it
    #: actually is (``"npc:<file name>"`` / ``"player:<id>"``). Handed back to the
    #: GM alone, exactly like :attr:`npc_paths` and for the same reason — an NPC's
    #: file name is the GM's business, and can be a spoiler outright.
    scene_sources: dict[str, str] = field(default_factory=dict)
    #: One base64 thumbnail per scene entry, by ``ref``. Kept apart from
    #: :attr:`scene` because the scene is re-sent on every change and these are
    #: not: see :class:`~.protocol.SetScenePortrait`.
    scene_portraits: dict[str, str] = field(default_factory=dict)
    #: Every mod's shared state, as ``{mod_id: {key: payload}}``. Authored by the
    #: GM (:class:`~.protocol.SetModState`), stored here so a table hosted on a
    #: server survives the GM's laptop, and handed whole to every joiner. Opaque:
    #: this module knows what shape a payload is and nothing whatever about what
    #: it means.
    mod_state: dict[str, dict[str, dict]] = field(default_factory=dict)

    # -- roster ------------------------------------------------------------

    def add_player(
        self,
        display_name: str,
        *,
        player_id: str | None = None,
        token: str | None = None,
        is_gm: bool = False,
    ) -> PlayerSlot:
        """Create and register a new slot, returning it."""
        slot = PlayerSlot(
            player_id=player_id or new_id(8),
            display_name=display_name,
            token=token or new_token(),
            is_gm=is_gm,
        )
        self.players[slot.player_id] = slot
        self.touch()
        return slot

    def remove_player(self, player_id: str) -> PlayerSlot | None:
        """Drop a slot entirely (a kick, not a disconnect); returns what was removed."""
        slot = self.players.pop(player_id, None)
        if slot is not None:
            self.touch()
        return slot

    def player_by_token(self, token: str) -> PlayerSlot | None:
        """The slot a returning client's ``player_token`` claims, if it is still valid."""
        if not token:
            return None
        for slot in self.players.values():
            if slot.token and tokens_match(token, slot.token):
                return slot
        return None

    def player_by_id_if_free(self, player_id: str) -> PlayerSlot | None:
        """The *empty* seat a returning client's ``player_id`` names, if any.

        The fallback for a client that knows which seat it had but no longer has
        the token for it — settings cleared, a different machine, a join code
        pasted by hand. Without it that client is given a brand new slot and the
        GM's board grows a second card while the first sits there greyed out.

        Deliberately weaker than :meth:`player_by_token`, and fenced accordingly.
        A ``player_id`` is *public* — it rides in :meth:`PlayerSlot.roster_dict`
        to every seat at the table — so this returns a slot only when it is
        **not currently connected** and **not the GM's**. What that leaves open
        is a table-mate claiming someone's seat while they are offline, which
        costs them a name on the board and nothing else: no character reaches a
        player (snapshots go to the GM's connection alone), a live seat can never
        be taken, and hidden rolls still need the GM token. Everyone who could
        try this already holds the join code and is sitting at the table.

        Matching on ``display_name`` was considered and rejected: two real
        players called "Sam" would silently become one seat, which is a worse bug
        than the duplicate card it would fix.
        """
        if not player_id:
            return None
        slot = self.players.get(player_id)
        if slot is None or slot.connected or slot.is_gm:
            return None
        return slot

    def roster(self) -> list[dict]:
        """The roster as it goes on the wire, in join order — see :meth:`PlayerSlot.roster_dict`."""
        return [slot.roster_dict() for slot in self.players.values()]

    def set_snapshot(self, player_id: str, character: dict) -> bool:
        """Store a player's latest character snapshot; False if the slot is unknown."""
        slot = self.players.get(player_id)
        if slot is None:
            return False
        slot.character = dict(character)
        slot.last_seen = utc_now()
        self.touch()
        return True

    # -- rolls -------------------------------------------------------------

    def next_seq(self) -> int:
        """The sequence number the next roll will take."""
        return (self.rolls[-1].seq + 1) if self.rolls else 1

    def record_roll(
        self,
        *,
        player_id: str,
        player_name: str,
        die: int,
        bonus: int = 0,
        penalty: int = 0,
        result: CheckResult | None = None,
        label: str = "",
        hidden: bool = False,
        spec: dict | None = None,
    ) -> RollRecord:
        """Build a :class:`RollRecord` from a resolved check and append it.

        The sequence number is assigned here, so the history is strictly ordered
        no matter which connection the roll came in on.
        """
        record = RollRecord.from_check(
            seq=self.next_seq(),
            player_id=player_id,
            player_name=player_name,
            die=die,
            bonus=bonus,
            penalty=penalty,
            result=result,
            label=label,
            hidden=hidden,
            spec=spec,
        )
        self.rolls.append(record)
        self.touch()
        return record

    def record_note(self, *, player_id: str, player_name: str, text: str) -> RollRecord:
        """Append a note — something that happened, rather than something rolled.

        Takes a sequence number from the same counter the rolls do, so the log
        stays one ordered stream and a note can be struck like any other line.
        *text* is composed by the client and stored verbatim: what counts as worth
        writing down is a rules question, and this layer has no rules in it.
        """
        record = RollRecord(
            seq=self.next_seq(),
            player_id=player_id,
            player_name=player_name,
            die=0,
            kind=KIND_NOTE,
            text=text,
        )
        self.rolls.append(record)
        self.touch()
        return record

    def record_mod_note(
        self, *, player_id: str, player_name: str, mod_id: str, text: str
    ) -> RollRecord:
        """Append a line a mod wrote. The fourth kind, and the thinnest.

        Deliberately :meth:`record_note` plus one field. A mod's line is a note in
        every respect that matters to the log — it is seq-numbered, replayed to a
        joiner, and strikeable — so giving it its own record type would have bought
        nothing and cost every reader a second shape to handle. What it does need
        is to say *which* mod wrote it, so a client holding that mod can draw the
        line its own way and one that does not can still read it.
        """
        record = RollRecord(
            seq=self.next_seq(),
            player_id=player_id,
            player_name=player_name,
            die=0,
            kind=KIND_MOD,
            text=text,
            mod_id=mod_id,
        )
        self.rolls.append(record)
        self.touch()
        return record

    # -- mod state ---------------------------------------------------------

    def set_mod_state(self, mod_id: str, key: str, payload: object) -> dict | None:
        """Store one mod's entry, or drop it; return what was stored (``None`` if gone).

        The caps are enforced here rather than at the door because this is where
        the *accumulated* state lives: one message is small by
        :func:`~.protocol.sanitize_mod_payload`, and what has to stay bounded is
        the pile of them that goes out in every :class:`~.protocol.Welcome`.

        A new key past :data:`~.protocol.MAX_MOD_KEYS`, a new mod past
        :data:`~.protocol.MAX_MOD_IDS`, or anything that would push the whole map
        past :data:`~.protocol.MAX_MOD_STATE_CHARS`, is **refused rather than
        evicting an older one**. Evicting would make a mod's own state silently
        lossy in a way it could not detect, and the mod that lost an entry would
        not be the one that overran the cap.

        The last of those three is the one that keeps :attr:`~.protocol.Welcome`
        sendable: the per-entry caps multiply out to eight times
        :data:`~.protocol.MAX_MESSAGE_BYTES`, so without an aggregate bound a
        session could accumulate state its own welcome could not encode — and the
        failure would land on the next player to join, not on whoever filled it.

        A ``payload`` that sanitizes to ``None`` deletes the key — see
        :func:`~.protocol.sanitize_mod_payload` for why a rejected payload and a
        deliberate deletion deliberately look the same.
        """
        checked_id = sanitize_mod_id(mod_id)
        if not checked_id or not isinstance(key, str) or not key.strip():
            return None
        checked_key = key[:MAX_MOD_KEY]
        entries = self.mod_state.get(checked_id)
        checked = sanitize_mod_payload(payload)

        if checked is None:
            if entries is not None and entries.pop(checked_key, None) is not None:
                if not entries:
                    # An empty mod is dropped rather than kept, so the MAX_MOD_IDS
                    # cap counts mods that actually hold something.
                    del self.mod_state[checked_id]
                self.touch()
            return None

        if entries is None:
            if len(self.mod_state) >= MAX_MOD_IDS:
                return None
            entries = self.mod_state.setdefault(checked_id, {})
        if checked_key not in entries and len(entries) >= MAX_MOD_KEYS:
            return None

        # Measured against the map as it *would* be, and rolled back if it does
        # not fit, so an overlarge write leaves the previous value in place rather
        # than a hole. Overwriting a key with something smaller therefore always
        # works, even when the map is already at the cap.
        previous = entries.get(checked_key)
        entries[checked_key] = checked
        if mod_state_chars(self.mod_state) > MAX_MOD_STATE_CHARS:
            if previous is None:
                del entries[checked_key]
                if not entries:
                    del self.mod_state[checked_id]
            else:
                entries[checked_key] = previous
            return None

        self.touch()
        return checked

    def record_request(
        self, *, player_id: str, player_name: str, label: str, dc: int | None, spec: dict | None
    ) -> RollRecord:
        """Append a request — a roll asked for, which nobody has made yet.

        The third kind of entry, and it needs no new field on
        :class:`RollRecord`: the trait's name goes in ``label``, the difficulty in
        ``dc`` and the descriptor in ``spec``, all of which a roll already carries.
        ``die`` stays 0, as a note's does, because nothing was thrown — every
        client renders the button and whoever presses it rolls a record of their
        own.

        Takes its sequence number from the same counter, for the reason
        :meth:`record_note` does: one ordered stream, and the GM can strike it.
        """
        record = RollRecord(
            seq=self.next_seq(),
            player_id=player_id,
            player_name=player_name,
            die=0,
            dc=dc,
            label=label,
            kind=KIND_REQUEST,
            spec=spec,
        )
        self.rolls.append(record)
        self.touch()
        return record

    def visible_rolls(self) -> list[RollRecord]:
        """The history minus hidden GM rolls — what a player client may see."""
        return [roll for roll in self.rolls if not roll.hidden]

    def remove_roll(self, seq: int) -> RollRecord | None:
        """Drop the roll with this sequence number; return it, or ``None`` if absent.

        Sequence numbers are never reused (:meth:`next_seq` reads the last roll's),
        so a removed roll leaves a gap rather than renumbering the log.
        """
        for index, roll in enumerate(self.rolls):
            if roll.seq == seq:
                del self.rolls[index]
                self.touch()
                return roll
        return None

    # -- housekeeping ------------------------------------------------------

    def touch(self) -> None:
        """Stamp the session as modified now."""
        self.updated_at = utc_now()

    def to_dict(self, *, include_rolls: bool = True) -> dict:
        """Serialize the session. The store omits rolls — they have their own log."""
        data: dict = {
            "id": self.id,
            "name": self.name,
            "host_token": self.host_token,
            "gm_token": self.gm_token,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "players": [slot.to_dict() for slot in self.players.values()],
            "npc_paths": list(self.npc_paths),
            "scene": [dict(entry) for entry in self.scene],
            "scene_sources": dict(self.scene_sources),
            "scene_portraits": dict(self.scene_portraits),
            "mod_state": {mod: dict(entries) for mod, entries in self.mod_state.items()},
        }
        if include_rolls:
            data["rolls"] = [roll.to_dict() for roll in self.rolls]
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> SessionState:
        players = [PlayerSlot.from_dict(p) for p in raw.get("players", [])]
        return cls(
            id=str(raw.get("id", "")) or new_id(),
            name=str(raw.get("name", "Session")),
            host_token=str(raw.get("host_token", "")) or new_token(),
            # A session written before gm tokens existed simply gets one now. It
            # cannot be recovered from anywhere, so minting is the only option;
            # the GM reads the new one off the server the next time they connect.
            gm_token=str(raw.get("gm_token", "")) or new_token(),
            created_at=str(raw.get("created_at", "")) or utc_now(),
            updated_at=str(raw.get("updated_at", "")) or utc_now(),
            players={slot.player_id: slot for slot in players},
            npc_paths=[str(p) for p in raw.get("npc_paths", [])],
            # Sanitized rather than trusted on the way back in for the reason it was
            # sanitized on the way over the wire: this file is not the authority on
            # the shape, and a session written by a newer build should degrade to
            # what this one understands rather than carry a surprise into a
            # broadcast.
            scene=sanitize_scene(raw.get("scene")),
            scene_sources=sanitize_scene_sources(raw.get("scene_sources")),
            scene_portraits=sanitize_scene_portraits(raw.get("scene_portraits")),
            mod_state=sanitize_mod_state(raw.get("mod_state")),
            rolls=[RollRecord.from_dict(r) for r in raw.get("rolls", [])],
        )


def new_session(name: str = "Session") -> SessionState:
    """A fresh session with a new id and a new host token."""
    return SessionState(name=name)
