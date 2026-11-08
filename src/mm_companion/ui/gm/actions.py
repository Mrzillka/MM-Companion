"""The host surface the Impress, area and aimed-attack controllers call back into."""

from __future__ import annotations

from pathlib import Path

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.rules import (
    RollSpec,
    apply_condition,
    decrement_condition,
)
from mm_companion.ui.action_ui import (
    publish_table_rules,
)
from mm_companion.ui.attack_targets import SceneTarget, scene_targets
from mm_companion.ui.gm.model import (
    SCENE_NPC,
    _number_namesakes,
)
from mm_companion.ui.impress import (
    PARTY_NPC,
    PARTY_PLAYER,
    ImpressParty,
)
from mm_companion.ui.sections.conditions import matching_condition


class GMActionsMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    # -- the Impress action's host surface (see ImpressHost) ------------------

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]:
        """Who may impress (the cast, and the seats) and who may be targeted (the board).

        Read fresh each time, off the same live models the board is drawn from — an
        NPC's own :class:`Character`, so a condition applied to it lands on the model
        the card and the file read, and a player's latest snapshot.
        """
        targets: list[ImpressParty] = []
        on_board: dict[str, str] = {}
        for entry in self._scene:
            if entry.kind == SCENE_NPC:
                npc = self._npc_state.get(entry.source)
                if npc is None:
                    continue
                party = ImpressParty(
                    entry.ref, npc.summary.name, PARTY_NPC, entry.source, npc.character
                )
            else:
                snapshot = self._snapshots.get(entry.source)
                party = ImpressParty(
                    entry.ref,
                    self._player_name(entry.source),
                    PARTY_PLAYER,
                    entry.source,
                    Character.from_dict(snapshot) if snapshot else None,
                )
            on_board[entry.wire_source()] = entry.ref
            targets.append(party)
        _number_namesakes(targets)
        named = {party.ref: party.name for party in targets}
        sources: list[ImpressParty] = []
        for name in self._ordered_npcs():
            npc = self._npc_state.get(name)
            if npc is None:
                continue
            ref = on_board.get(f"{SCENE_NPC}:{name}", f"{SCENE_NPC}:{name}")
            shown = named.get(ref, npc.summary.name)
            sources.append(ImpressParty(ref, shown, PARTY_NPC, name, npc.character))
        sources += [party for party in targets if party.kind == PARTY_PLAYER]
        return sources, targets

    def impress_apply(self, party: ImpressParty, changes: list[tuple[str, str | None]]) -> bool:
        """Put an impression's conditions on one creature.

        An NPC's go straight onto its model, through the path every other condition
        change takes (its card, an open sheet, the file, the board). A player's go to
        their app as commands, exactly as the GM's own "+" sends one.
        """
        if party.kind == PARTY_NPC:
            entry = self._npc_state.get(party.source)
            if entry is None:
                return False
            for condition_id, parameter in changes:
                apply_condition(entry.character, condition_id, self._data, parameter=parameter)
            self._after_npc_condition_change(entry, list(changes), applying=True)
            return True
        delivered = True
        for condition_id, parameter in changes:
            delivered = (
                bool(self._bridge.apply_condition(party.source, condition_id, parameter))
                and delivered
            )
        return delivered

    def action_remove(self, party: ImpressParty, changes: list[tuple[str, str | None]]) -> bool:
        """Take conditions off one creature — the twin of :meth:`impress_apply`.

        An NPC's come off its model through the path every NPC condition change takes;
        a player's go to their app as remove commands, as the GM's own "−" sends one.
        """
        if party.kind == PARTY_NPC:
            entry = self._npc_state.get(party.source)
            if entry is None:
                return False
            for condition_id, parameter in changes:
                applied = matching_condition(entry.character, condition_id, parameter)
                if applied is not None:
                    decrement_condition(entry.character, applied)
            self._after_npc_condition_change(entry, list(changes), applying=False)
            return True
        delivered = True
        for condition_id, parameter in changes:
            delivered = (
                bool(self._bridge.remove_condition(party.source, condition_id, parameter))
                and delivered
            )
        return delivered

    def action_request(self, spec: RollSpec) -> None:
        """Leave a request card for *spec* — the table's history, or this window's."""
        self._request_roll(spec)

    def impress_show_offline(self, record: dict) -> None:
        """One roll of an Impress made off the air, as an offline card."""
        self._offline_seq -= 1
        self._history.add_roll(
            {**record, "seq": self._offline_seq, "player_name": self._name_of_gm()}
        )

    def impress_notice(self, text: str, colour: str) -> None:
        self._show_notice(text, colour)

    # -- the actions' two extra host methods (see AreaHost, ImpressHost) ----------

    def action_scene(self) -> tuple[list[dict], dict]:
        """The board as an action window's scene copy draws it: the wire entries, and
        each one's decoded thumbnail by ref."""
        entries = self._scene_payload()
        portraits = {}
        for entry in entries:
            ref = str(entry.get("ref", ""))
            pixmap = self._scene_board.portrait(ref)
            if pixmap is not None:
                portraits[ref] = pixmap
        return entries, portraits

    def action_add_card(self, card) -> None:
        """Put an action's reminder — its Resolve button — in the GM's history."""
        self._history.add_card(card)

    def publish_table_rules(self) -> None:
        """Tell the table whether its players set up their own actions (a GM setting).

        Published on the session's shared state rather than asked for, so a player
        reads it the moment they click Impress — and a later joiner gets it in their
        Welcome. Off the air there is nobody to tell.
        """
        if self._bridge.in_session and self._bridge.is_gm:
            publish_table_rules(self._bridge)

    def roller_adjustment(self) -> int:
        """The roller's bonus minus penalty now — what an aimed attack's lone save is
        rolled with on the GM's behalf."""
        return self._roller.adjustment()

    # -- aimed attacks -----------------------------------------------------------

    def _aim_targets(self, skip_ref: str = "") -> list[SceneTarget]:
        """Everyone on the GM's own Scene an attack can be aimed at (offline too)."""
        return scene_targets(self._scene_payload(), skip_ref=skip_ref)

    def _aim_targets_for(self, name: str) -> list[SceneTarget]:
        """The targets an NPC's attack may be aimed at: the Scene, less itself."""
        entry = self._scene_entry_for(SCENE_NPC, name)
        return self._aim_targets(entry.ref if entry is not None else "")

    def _npc_name_for_window(self, window) -> str:
        """The cast entry an NPC sheet shows, by its file — ``""`` for one not in it."""
        path = Path(window.path) if window is not None and window.path else None
        if path is None:
            return ""
        return next((n for n, e in self._npc_state.items() if e.path == path), "")

    def _aim_targets_for_window(self, window) -> list[SceneTarget]:
        """The targets an NPC sheet's attack may be aimed at: the Scene, less itself."""
        name = self._npc_name_for_window(window)
        return self._aim_targets_for(name) if name else self._aim_targets()

    def _aim_from_card(self, name: str, payload: object) -> None:
        """An NPC card's pinned attack tile was aimed at someone."""
        entry = self._npc_state.get(name)
        if entry is None or not isinstance(payload, tuple) or len(payload) != 2:
            return
        spec, target = payload
        if not isinstance(spec, RollSpec) or not isinstance(target, SceneTarget):
            return
        self._follow_actor(name)
        source = self._npc_party(name)
        self._attack.aim_own(source, spec, target.ref, target.name)

    def _aim_from_sheet(self, window, payload: object) -> None:
        """An NPC sheet's attack line was aimed at someone (``attack-requested``)."""
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        spec, target = payload
        if not isinstance(spec, RollSpec) or not isinstance(target, SceneTarget):
            return
        name = self._npc_name_for_window(window)
        self._follow_actor(name)
        if name:
            source = self._npc_party(name)
        else:
            character = window.sheet.character if window is not None else None
            label = library.display_name(character) if character is not None else "An NPC"
            source = ImpressParty(f"sheet:{label}", label, PARTY_NPC, "", character)
        self._attack.aim_own(source, spec, target.ref, target.name)

    def _npc_party(self, name: str) -> ImpressParty:
        """An NPC of the cast as an acting party — off the board's own list when it is
        on the Scene (so its ref and numbered name match), else made up from the cast."""
        sources, _targets = self.impress_parties()
        found = next((p for p in sources if p.kind == PARTY_NPC and p.source == name), None)
        if found is not None:
            return found
        entry = self._npc_state[name]
        return ImpressParty(f"npc:{name}", entry.summary.name, PARTY_NPC, name, entry.character)

    def area_roll_attack(
        self, spec: RollSpec, attacker: Character | None = None, *, before=None
    ) -> None:
        """Roll an NPC's attack against the primary on this window's roller.

        A minion *attacker*'s natural 20 is no critical (PDF p25). *before* is the
        roller's (``roll_spec(before=)``): an aimed attack declares the spec the die is
        made of, the GM's maneuvers on it.
        """
        self._roller.roll_spec(spec, minion=self.rolls_as_minion(attacker), before=before)
