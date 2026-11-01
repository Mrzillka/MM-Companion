"""The Scene: who is on the board, its payload for the players, and initiative."""

from __future__ import annotations

from copy import deepcopy

from mm_companion.core.character import Character
from mm_companion.core.dice import roll_d20
from mm_companion.core.rules import (
    KIND_INITIATIVE,
    RollSpec,
    can_recover,
    initiative_modifier,
    requested_roll_choices,
)
from mm_companion.core.session.model import (
    KIND_ROLL,
    new_id,
)
from mm_companion.core.session.protocol import (
    DISPOSITION_PLAYER,
    MAX_SCENE_ENTRIES,
    SCENE_DISPOSITIONS,
)
from mm_companion.ui import theme
from mm_companion.ui.gm.model import (
    SCENE_NPC,
    SCENE_PLAYER,
    _default_disposition,
    _SceneEntry,
)
from mm_companion.ui.sections.conditions import matching_condition
from mm_companion.ui.session_portrait import encode_scene_portrait, shrink_portrait


class GMSceneMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    # -- the scene ---------------------------------------------------------
    #
    # The GM authors the board and the server only stores and rebroadcasts it, so
    # everything below is about deriving one payload correctly and often. It is
    # derived rather than kept because every field on it lives somewhere else and
    # is live there: an NPC's conditions are on its model, a player's are in their
    # last snapshot, and a copy of either would be right exactly once.

    def _scene_entry(self, ref: str) -> _SceneEntry | None:
        """The entry answering to *ref*, or ``None``."""
        return next((entry for entry in self._scene if entry.ref == ref), None)

    def _scene_entry_for(self, kind: str, source: str) -> _SceneEntry | None:
        """The entry standing for one creature, or ``None`` if it is not on the board."""
        return next((e for e in self._scene if e.kind == kind and e.source == source), None)

    def _set_in_scene(self, kind: str, source: str, on: bool) -> None:
        """Put one creature on the board, or take it off. The one way in.

        Adding appends: a creature nobody has rolled for sorts into the un-rolled
        zone, and the end of it is the only honest place for something no one has
        said anything about yet.
        """
        entry = self._scene_entry_for(kind, source)
        if on and entry is None:
            self._scene.append(
                _SceneEntry(
                    ref=new_id(8), kind=kind, source=source, disposition=_default_disposition(kind)
                )
            )
        elif not on and entry is not None:
            self._scene.remove(entry)
            if kind == SCENE_PLAYER:
                self._player_initiative.pop(source, None)
        else:
            return
        self._push_scene()

    def _set_disposition(self, ref: str, disposition: str) -> None:
        """Record what the GM says a creature is, and tell the table."""
        entry = self._scene_entry(ref)
        if entry is None or disposition not in SCENE_DISPOSITIONS:
            return
        # A seat is a player. The card offers a GM no way to say otherwise, and
        # this is the second lock on the same door — the signal is public.
        if entry.kind == SCENE_PLAYER or disposition == DISPOSITION_PLAYER:
            return
        if entry.disposition == disposition:
            return
        entry.disposition = disposition
        self._push_scene()

    def _restore_scene(self) -> None:
        """Put back the board this session was last left holding.

        ``scene_sources`` is written on every push, persisted with the session and
        handed back in the GM's own ``Welcome`` — *for this*, exactly as
        ``npc_paths`` is. It had no reader until now, so a GM who closed the app
        mid-fight came back to a full cast and an empty board, and the first push
        after that wrote the empty one over the stored one: the fight was not just
        forgotten but deleted, while any player still connected watched it vanish.

        Rebuilt in the order the **public** scene was stored in, since that is the
        list ``_scene_payload`` wrote and so the one the refs are ordered by; the
        private map only says which creature each ref stood for. The rolled
        initiatives come back with it, because a turn order without its numbers is
        most of the way to no turn order at all.

        **Players too**, with their rolled numbers. A disconnected seat keeps its
        place on the board, and a GM restarting the app is the same thing from the
        other side; the first roster then drops any seat that has left the table for
        good, which is the rule ``_sync_scene_players`` keeps. Each entry's place in
        the turn order comes back as well (``turn``), so a creature the GM had slotted
        in by hand is still slotted in.

        Called once, after :meth:`_refresh_npcs` has built the cast, so a creature
        whose file was deleted between sessions is simply not found and its place
        is dropped rather than restored as a card that stands for nothing.
        """
        sources = dict(self._state.scene_sources)
        if not sources or self._scene:
            return
        stored = {
            str(item.get("ref", "")): item
            for item in self._state.scene
            if isinstance(item, dict) and item.get("ref")
        }
        # The public list first (it carries the order), then anything the private
        # map knows that the public one somehow does not.
        refs = [ref for ref in stored if ref in sources]
        refs += [ref for ref in sources if ref not in stored]

        restored: list[_SceneEntry] = []
        for ref in refs:
            item = stored.get(ref) or {}
            raw = item.get("disposition")
            entry = _SceneEntry.from_wire_source(
                ref, sources[ref], raw if isinstance(raw, str) else ""
            )
            if entry is None:
                continue
            initiative = item.get("initiative")
            rolled = isinstance(initiative, int) and not isinstance(initiative, bool)
            if entry.kind == SCENE_NPC:
                npc = self._npc_state.get(entry.source)
                if npc is None:
                    continue
                if rolled:
                    npc.initiative = initiative
            elif rolled:
                self._player_initiative[entry.source] = initiative  # type: ignore[assignment]
            turn = item.get("turn")
            entry.placed = (
                not rolled and isinstance(turn, int) and not isinstance(turn, bool) and turn > 0
            )
            restored.append(entry)
        if not restored:
            return
        self._scene = restored
        if not any(isinstance(item.get("turn"), int) for item in stored.values()):
            # Saved before turns were numbered, when the list was the GM's
            # arrangement and the board sorted the rolled ones by their numbers: put
            # them in that order at the head, which is what the board showed.
            rolled = [e for e in restored if self._entry_initiative(e) is not None]
            rolled.sort(key=lambda e: self._entry_initiative(e), reverse=True)  # type: ignore[arg-type,return-value]
            self._scene = rolled + [e for e in restored if e not in rolled]
        self._push_scene()

    def _warn_if_scene_is_truncated(self, entries: list[dict]) -> None:
        """Say so when the board has more on it than the wire will carry.

        ``sanitize_scene`` keeps the first :data:`MAX_SCENE_ENTRIES` and drops the
        rest, silently and by *insertion* order — which is not the order the board
        reads in, so what falls off the end can be a creature at the top of the
        initiative. The GM's own board shows all of it either way, so without this
        the two screens disagree and only the players can tell.

        Given the payload the push already built rather than building a second
        one: this runs on every push, and a push happens on every condition tick.
        """
        if not self._bridge.in_session:
            return
        extra = len(entries) - MAX_SCENE_ENTRIES
        if extra <= 0:
            return
        self._show_notice(
            f"The Scene is full — {extra} more "
            f"{'creature is' if extra == 1 else 'creatures are'} on your board than "
            f"your players can see (the limit is {MAX_SCENE_ENTRIES}).",
            theme.color("tint.warning"),
        )

    def _remove_scene_condition(self, ref: str, condition_id: str, parameter: object) -> None:
        """A scene card's chip, right-clicked: take that condition off its creature.

        The same path as the creature's own card — the NPC's model, or a command to
        the player's app — so the board and the card cannot disagree about it.
        """
        entry = self._scene_entry(ref)
        if entry is None:
            return
        if entry.kind == SCENE_NPC:
            self._remove_npc_condition(entry.source, condition_id, parameter)
        else:
            self._remove_condition(entry.source, condition_id, parameter)

    def _roll_scene_recovery(self, ref: str, condition_id: str, parameter: object) -> None:
        """A scene card's 🎲: roll an NPC's recovery check, or ask a player for theirs."""
        entry = self._scene_entry(ref)
        if entry is None:
            return
        if entry.kind == SCENE_NPC:
            self._roll_npc_recovery(entry.source, condition_id, parameter)
        else:
            self._request_player_recovery(entry.source, condition_id, parameter)

    def _scene_can_recover(self, ref: str, condition_id: str, parameter: object) -> bool:
        """Whether a scene card's chip offers a 🎲 — read off the creature behind it."""
        entry = self._scene_entry(ref)
        if entry is None:
            return False
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            character = npc.character if npc is not None else None
        else:
            snapshot = self._snapshots.get(entry.source)
            character = Character.from_dict(snapshot) if snapshot else None
        if character is None:
            return False
        applied = matching_condition(character, condition_id, parameter or None)
        return applied is not None and can_recover(character, self._data, applied)

    def _remove_from_scene(self, ref: str) -> None:
        """Take one entry off the board, by ref (a scene card's right-click)."""
        entry = self._scene_entry(ref)
        if entry is not None:
            self._set_in_scene(entry.kind, entry.source, False)

    def _drop_on_scene(self, ref: str, index: int) -> None:
        """A card was dropped at *index* — a move if we know the ref, else an add.

        **The dragged card is the only one that changes.** It lands exactly where it
        was dropped and gives up the initiative it rolled, since the GM has just
        overruled that number; every other card keeps its number and its place. It
        used to clear a rolled neighbour too, because the board sorted by initiative
        and an un-rolled card could not otherwise sit in front of a rolled one — which
        punished the creature the GM was *not* touching.

        The board can say it now because the order is the GM's list and the numbers
        on the cards are **turns**, not initiatives. Dropped in among the numbered
        cards — anywhere up to just after the last of them — the card is *placed*:
        it has no initiative, but it has a turn, and every card after it moves down
        one. Dropped further down, among the creatures nobody has rolled for, it is
        only rearranged.
        """
        entry = self._scene_entry(ref) or self._adopt_dropped(ref)
        if entry is None:
            return
        # The index was measured on the board as drawn, which can be shorter than the
        # GM's list (a seat with no card yet is not drawn), so it is turned into "in
        # front of that card" and found again in the list.
        order = self._scene_board.ordered_refs()
        # A card put back where it already was is not a move, and must not cost the
        # roll it is carrying: a GM who nudged a rolled card and let go would
        # silently lose it.
        if self._is_same_slot(entry.ref, index, order):
            return
        # The index is a gap in the board *with* the dragged card still in it, so a
        # card dragged down the board lands one earlier once it has been lifted out.
        if entry.ref in order and order.index(entry.ref) < index:
            index -= 1
        drawn = [r for r in order if r != entry.ref]
        before = drawn[index] if 0 <= index < len(drawn) else ""
        self._clear_entry_initiative(entry)
        rest = [e for e in self._scene if e is not entry]
        index = next((i for i, e in enumerate(rest) if e.ref == before), len(rest))
        numbered = sum(1 for e in rest if self._in_turn_order(e))
        entry.placed = numbered > 0 and index <= numbered
        rest.insert(index, entry)
        self._scene = rest
        self._push_scene()

    def _in_turn_order(self, entry: _SceneEntry) -> bool:
        """Whether *entry* has a turn: it rolled, or the GM dragged it in among them."""
        return entry.placed or self._entry_initiative(entry) is not None

    def _seat_by_initiative(self, entry: _SceneEntry) -> None:
        """Move an entry that has just rolled to its place in the turn order.

        In front of the first *rolled* entry that rolled lower (a tie keeps the one
        already there first), or after the last numbered entry if none did. Placed
        entries are not compared — they have no number — so they keep their places
        among the rolled ones and the newcomer simply goes round them.
        """
        value = self._entry_initiative(entry)
        if value is None or entry not in self._scene:
            return
        entry.placed = False
        rest = [e for e in self._scene if e is not entry]
        index = sum(1 for e in rest if self._in_turn_order(e))
        for position, other in enumerate(rest):
            other_value = self._entry_initiative(other)
            if other_value is not None and other_value < value:
                index = position
                break
        rest.insert(index, entry)
        self._scene = rest

    @staticmethod
    def _is_same_slot(ref: str, index: int, order: list[str]) -> bool:
        """Whether dropping *ref* at *index* would leave the board as it is.

        The index is a *gap* between cards, so the two gaps either side of a card
        both name its own place — which is why this is not a plain equality.
        """
        if ref not in order:
            return False
        current = order.index(ref)
        return index in (current, current + 1)

    def _out_of_turn_order(self, entry: _SceneEntry) -> None:
        """Take an entry out of the turn order: no number, no place, first in the tail.

        First among the un-numbered rather than last, so a GM clearing a roll by
        mistake finds the card where they were looking — just under the numbers.
        """
        self._clear_entry_initiative(entry)
        entry.placed = False
        rest = [e for e in self._scene if e is not entry]
        index = sum(1 for e in rest if self._in_turn_order(e))
        rest.insert(index, entry)
        self._scene = rest

    def _adopt_dropped(self, ref: str) -> _SceneEntry | None:
        """Make an entry for a card dragged in from one of the rosters.

        Such a card carries its *own* identity (``"npc:<file>"`` /
        ``"player:<id>"``) rather than a scene ref, which is how one drop handler
        serves both gestures: a ref the board already knows is a move, and this is
        everything else. A ref naming a creature this window has never heard of is
        refused rather than invented — it did not come from either roster.
        """
        kind, _, source = ref.partition(":")
        known = (kind == SCENE_NPC and source in self._npc_state) or (
            kind == SCENE_PLAYER and source in self._cards
        )
        if not known:
            return None
        existing = self._scene_entry_for(kind, source)
        if existing is not None:
            return existing
        entry = _SceneEntry(
            ref=new_id(8), kind=kind, source=source, disposition=_default_disposition(kind)
        )
        self._scene.append(entry)
        return entry

    def _entry_initiative(self, entry: _SceneEntry) -> int | None:
        """One entry's rolled initiative, read from wherever that number lives."""
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            return None if npc is None else npc.initiative
        return self._player_initiative.get(entry.source)

    def _clear_entry_initiative(self, entry: _SceneEntry) -> None:
        """Put one entry back in the un-rolled zone, wherever its number lives."""
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            if npc is not None and npc.initiative is not None:
                npc.initiative = None
        else:
            self._player_initiative.pop(entry.source, None)

    def _clear_scene_initiative(self, ref: str) -> None:
        """A scene card's right-click: take this entry out of the turn order.

        A push and nothing more: the cast's own grid no longer sorts by the number,
        so there is no second board to keep in step.
        """
        entry = self._scene_entry(ref)
        if entry is None:
            return
        self._out_of_turn_order(entry)
        self._push_scene()

    # -- publishing --------------------------------------------------------

    def _scene_payload(self) -> list[dict]:
        """The board as it goes on the wire, derived fresh from the live models.

        A **name, an initiative and the conditions**, and nothing else. There is no
        filtering step here that could be got wrong later: the other fields are
        simply never read, and what is read is checked again by ``sanitize_scene``
        on the way through the server.
        """
        payload: list[dict] = []
        turn = 0
        for entry in self._scene:
            if entry.kind == SCENE_NPC:
                npc = self._npc_state.get(entry.source)
                if npc is None:
                    continue
                item: dict = {
                    "ref": entry.ref,
                    "name": npc.summary.name,
                    "disposition": entry.disposition,
                }
                # The GM's own board always shows it; the wire only when the session
                # lets the players see it (see _push_scene).
                if npc.character.minion:
                    item["minion"] = True
                if npc.character.summoned:
                    item["summoned"] = True
                conditions = [c.to_dict() for c in npc.character.conditions]
            else:
                if entry.source not in self._cards:
                    continue
                item = {
                    "ref": entry.ref,
                    "name": self._player_name(entry.source),
                    "player_id": entry.source,
                    "disposition": DISPOSITION_PLAYER,
                }
                raw = (self._snapshots.get(entry.source) or {}).get("conditions")
                conditions = (
                    [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
                )
            initiative = self._entry_initiative(entry)
            if initiative is not None:
                item["initiative"] = initiative
            # The turn order, counted down the board: the numbered entries are kept
            # at its head (see _drop_on_scene), so counting them in list order *is*
            # the order they act in.
            if self._in_turn_order(entry):
                turn += 1
                item["turn"] = turn
            if conditions:
                # ``provenance`` is a detail of the sender's own tracker — which
                # umbrella bundled this condition — and means nothing on a card.
                item["conditions"] = [
                    {k: v for k, v in c.items() if k != "provenance"} for c in conditions
                ]
            payload.append(item)
        return payload

    def _push_scene(self) -> None:
        """Publish the board, restate the GM's own copy, and send any new pictures.

        Called from every mutation *and* from everywhere a creature's visible state
        can change under it — a condition, a damage step, a snapshot — since those
        are the changes a player is watching the board for.

        **A push that would say nothing does nothing**, and that guard is what makes
        it affordable to call from all of those places. One player editing a stat
        arrived here twice — once from their snapshot and once from the roster
        broadcast the same snapshot triggered — and each time redrew the GM's whole
        board and sent the whole scene to every client, whose own board redrew in
        turn. None of that is wrong; almost all of it is about a number the board
        never showed.

        The signature is everything the guarded half reads: the payload, the GM's own
        arrangement, and whether there is a table to send to — so a scene built
        *before* hosting starts is still broadcast when it does. What the cards are
        told, and what pictures are sent, is settled outside it.
        """
        entries = self._scene_payload()
        order = [e.ref for e in self._scene]
        shown = self.minion_rules().show_players
        signature = (entries, order, self._bridge.in_session, shown)
        if signature != self._pushed_scene:
            self._pushed_scene = deepcopy(signature)
            self._scene_board.set_manual_order(order)
            self._scene_board.set_scene(entries)
            if self._bridge.in_session:
                wire = (
                    entries
                    if shown
                    else [{k: v for k, v in e.items() if k != "minion"} for e in entries]
                )
                self._bridge.set_scene(wire, {e.ref: e.wire_source() for e in self._scene})
            self._warn_if_scene_is_truncated(entries)
        # Both of these live outside the guard, because neither is about the payload.
        #
        # A card is told where it stands every time, because the *cards* can be new
        # when the scene is not: ``_refresh_npcs`` destroys and rebuilds every NPC
        # card, a fresh one starts off the board, and a roster brings new player
        # cards whose 👁 has never been shown or hidden. Behind the guard, a cast
        # reloaded mid-fight came back with every card claiming its creature was not
        # in the scene. It is a loop over the cards setting two flags, so it is not
        # what a push costs.
        self._refresh_scene_state()
        # A picture travels on its own message and appears in no payload, so a player
        # who changed only their portrait moves nothing above. This carries its own
        # change check.
        self._push_scene_portraits()

    def _push_scene_portraits(self) -> None:
        """Send a thumbnail for anything on the board that has not had one sent.

        Keyed by the encoded payload rather than by a "sent" flag, so a player who
        changes their portrait mid-session gets a new one — and a scene re-pushed
        thirty times through a fight sends none of them again.
        """
        live = {entry.ref for entry in self._scene}
        for ref in [r for r in self._scene_portraits if r not in live]:
            del self._scene_portraits[ref]
        for ref in [r for r in self._portrait_encodings if r not in live]:
            del self._portrait_encodings[ref]
        for entry in self._scene:
            portrait = self._scene_portrait_for(entry)
            if self._scene_portraits.get(entry.ref) == portrait:
                continue
            self._scene_portraits[entry.ref] = portrait
            # The GM's own board takes it directly; the wire only when hosting.
            self._scene_board.set_portrait(entry.ref, portrait)
            if self._bridge.in_session:
                self._bridge.set_scene_portrait(entry.ref, portrait)

    def _scene_portrait_for(self, entry: _SceneEntry) -> str:
        """One entry's scene-sized thumbnail, or ``""`` when it has no picture.

        The two kinds come from different places and neither can be passed straight
        on: an NPC's picture is a file this app can read, and a player's has already
        been shrunk once for their *sheet* portrait — still big enough that a
        board's worth would be stored per session and replayed to every joiner.

        Encoded at most once per picture. Both roads through here are expensive — a
        file read or a base64 decode, then a scale and a JPEG re-encode — and this
        runs for **every** entry on **every** push. ``_scene_portraits`` below only
        suppresses the *send*, so before this cache a board of a dozen creatures was
        re-encoded a dozen pictures' worth every time a player touched a spin box.
        Keyed by the ref and holding the input it was made from, like
        :meth:`~mm_companion.ui.session_player.SnapshotPusher._portrait_payload`: a
        player who changes their picture mid-session still gets a new one.
        """
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            source = "" if npc is None else (npc.summary.image_path or "")
            encode = encode_scene_portrait
        else:
            source = (self._snapshots.get(entry.source) or {}).get("portrait") or ""
            encode = shrink_portrait
        cached = self._portrait_encodings.get(entry.ref)
        if cached is not None and cached[0] == source:
            return cached[1]
        # Both encoders answer "" for a falsy input, so the short-circuit changes
        # nothing but the work not done.
        encoded = encode(source) if source else ""
        self._portrait_encodings[entry.ref] = (source, encoded)
        return encoded

    def _refresh_scene_state(self) -> None:
        """Tell every roster card whether its creature is on the board.

        Silently, like :meth:`NPCCard.set_collapsed`: a card being told what the
        window already decided must not come back as a fresh request.

        The two cards do different things with the answer. A **player's** card
        draws it as a 👁, shown only while ``gm_scene_auto_players`` is off —
        the one case where a seat sits out of a session it has joined. An **NPC's**
        draws nothing; it only needs to know which way round to word the menu item
        that puts it on the board or takes it off.
        """
        on_board = {(e.kind, e.source) for e in self._scene}
        for name, npc in self._npc_state.items():
            if npc.card is not None:
                npc.card.set_in_scene((SCENE_NPC, name) in on_board)
        for player_id, card in self._cards.items():
            card.set_scene_controls(not self._scene_auto_players)
            card.set_in_scene((SCENE_PLAYER, player_id) in on_board)

    # -- initiative --------------------------------------------------------

    def _roll_scene_initiative(self) -> None:
        """Roll for every NPC on the board, and ask every player for theirs.

        The NPC rolls stay **local**, as the initiative badge's own docstring
        insists: a dozen mook rolls in the shared log would bury the one line the
        table is waiting for, and what the players need is the *result*, which is
        the board. The players are asked through the request the log already has —
        each client's ``localize_spec`` fills in their own modifier, so the number
        that comes back is theirs rather than one this window guessed at.
        """
        for entry in list(self._scene):
            if entry.kind != SCENE_NPC:
                continue
            npc = self._npc_state.get(entry.source)
            if npc is None:
                continue
            npc.initiative = roll_d20() + initiative_modifier(npc.character, self._data)
            self._seat_by_initiative(entry)
        # A push and no rebuild: the cast's order does not depend on initiative any
        # more, so the twelve cards this used to destroy and remake — losing every
        # hover and scroll position with them — stay exactly where they are.
        self._push_scene()
        self._ask_players_for_initiative()

    def _roll_entry_initiative(self, ref: str) -> None:
        """Roll one scene entry's initiative — its card's badge was clicked.

        NPCs only, which the badge itself already enforces: a player's number is
        theirs to roll, and it arrives here off the shared roll log. Local like
        every other NPC roll, for the reason :meth:`_roll_scene_initiative` gives.
        """
        entry = self._scene_entry(ref)
        if entry is None or entry.kind != SCENE_NPC:
            return
        npc = self._npc_state.get(entry.source)
        if npc is None:
            return
        npc.initiative = roll_d20() + initiative_modifier(npc.character, self._data)
        self._seat_by_initiative(entry)
        self._push_scene()

    def _ask_players_for_initiative(self) -> None:
        """Put one "roll initiative" request in the shared log, for everyone.

        One request rather than one per seat: the log's request card is already
        addressed to the table, and a player who is not in this fight can simply
        not click it — a better answer than making the GM say who is.
        """
        spec = self._initiative_request_spec()
        if spec is not None and self._bridge.in_session:
            self._request_roll(spec)

    def _initiative_request_spec(self) -> RollSpec | None:
        """The character-free Initiative template, from the list requests use.

        Taken from :func:`requested_roll_choices` rather than built here so it is
        the same object a GM asking by hand would send: one shape of request,
        however it was asked for.
        """
        for group in requested_roll_choices(self._data):
            for value in group.values:
                if value.spec is not None and value.spec.kind == KIND_INITIATIVE:
                    return value.spec
        return None

    def _on_roll_added(self, roll: object) -> None:
        """Watch the shared log for a player's initiative and put it on the board.

        No new message for this: a player's roll already reaches every seat
        carrying the spec that describes it, so the only thing needed is to notice
        the ones that say ``initiative``. That catches both routes at once — a
        player answering the request card, and a player rolling Initiative off
        their own sheet — because the two produce the same record.
        """
        if not isinstance(roll, dict) or roll.get("kind", KIND_ROLL) != KIND_ROLL:
            return
        spec = roll.get("spec")
        if not isinstance(spec, dict) or spec.get("kind") != KIND_INITIATIVE:
            return
        entry = self._scene_entry_for(SCENE_PLAYER, str(roll.get("player_id", "")))
        if entry is None:
            return
        # ``RollRecord.to_dict`` writes the parts, not the sum.
        self._player_initiative[entry.source] = (
            int(roll.get("die", 0)) + int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
        )
        self._seat_by_initiative(entry)
        self._push_scene()

    def _new_scene(self) -> None:
        """Clear the board and every initiative on it.

        Reached only through :class:`~mm_companion.ui.widgets.ConfirmButton`'s
        second click, which is the confirmation — there is no dialog. With players
        joining the board automatically, the new one starts with every player who
        is **connected** — and only them: a seat whose player is away is not in this
        fight, and seating it would be the app guessing.
        """
        for entry in list(self._scene):
            self._clear_entry_initiative(entry)
        self._player_initiative.clear()
        self._scene = []
        if self._scene_auto_players:
            for player_id, card in self._cards.items():
                if card.connected:
                    self._scene.append(
                        _SceneEntry(
                            ref=new_id(8),
                            kind=SCENE_PLAYER,
                            source=player_id,
                            disposition=DISPOSITION_PLAYER,
                        )
                    )
        self._push_scene()
