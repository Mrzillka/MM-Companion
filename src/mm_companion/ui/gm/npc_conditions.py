"""Conditions on NPCs: applying and removing them, damage, and recovery checks."""

from __future__ import annotations

from dataclasses import replace

from mm_companion.core import library
from mm_companion.core.character import AppliedCondition, Character, ShakeOff
from mm_companion.core.rules import (
    apply_condition,
    apply_condition_requests,
    apply_damage_step,
    apply_recovery,
    clear_temporary,
    clear_temporary_note,
    condition_check,
    condition_check_request,
    damage_step_summary,
    damage_steps,
    decrement_condition,
    held_shake_offs,
    minion_step,
    recovery_succeeded,
    same_origin,
    shake_off_made,
)
from mm_companion.ui import theme
from mm_companion.ui.gm.model import (
    SCENE_NPC,
    _NpcEntry,
    _Recovering,
)
from mm_companion.ui.impress import PARTY_NPC, ImpressParty
from mm_companion.ui.impress_gm import land_shake_failure
from mm_companion.ui.sections.conditions import matching_condition
from mm_companion.ui.undo import absorbing


class GMNpcConditionsMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    # -- NPC conditions -----------------------------------------------------

    def _apply_npc_condition(
        self, name: str, condition_id: str, parameter: object, amount: object = None
    ) -> None:
        """Apply a condition straight onto the NPC's local model, and persist it.

        *amount* sizes one that moves traits by an amount (All-Out Attack −3).
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        size = amount if isinstance(amount, int) and not isinstance(amount, bool) else None
        apply_condition(entry.character, condition_id, self._data, parameter=subject, amount=size)
        self._after_npc_condition_change(
            entry, [(condition_id, subject)], applying=True, amount=size
        )

    def _clear_npc_temporary(self, name: str) -> None:
        """Take every temporary condition off an NPC — its card's "Clear temporary".

        The start of the creature's turn, called by the GM since there is no turn
        clock. One line in the history names the creature and what went.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        removed = clear_temporary(entry.character)
        if not removed:
            return
        self._after_npc_condition_change(entry, [], applying=False, clearing=True)
        note = clear_temporary_note(removed, self._data)
        if note:
            self._post_note(f"{entry.summary.name}: {note}")

    def _land_npc_requests(self, name: str, requests: tuple) -> bool:
        """Put what a combat action asks for on an NPC — the GM's roller acting as it
        (All-Out Attack's −Defense once the die settles, Defend's Defending, a Surprise
        Attack's temporary Vulnerable). Read as the sheet reads it
        (:func:`~mm_companion.core.rules.apply_condition_requests`), so an open sheet
        replaying the same payload lands the same thing. Whether anything changed."""
        entry = self._npc_state.get(name)
        if entry is None or not apply_condition_requests(entry.character, requests, self._data):
            return False
        self._after_npc_condition_change(entry, [], applying=True, requests=requests)
        return True

    def _remove_npc_condition(self, name: str, condition_id: str, parameter: object) -> None:
        """Take one condition off the NPC's model again."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        applied = matching_condition(entry.character, condition_id, subject)
        if applied is not None:
            decrement_condition(entry.character, applied)
        self._after_npc_condition_change(entry, [(condition_id, subject)], applying=False)

    # -- recovery checks ----------------------------------------------------

    def _roll_npc_recovery(self, name: str, condition_id: str, parameter: object) -> None:
        """Roll an NPC's recovery check for one condition — the 🎲 on its chip.

        Rolled on this window's roller as the creature (its number in, its name on
        the card), and *held* until the result is back: what a made check does is
        applied by :meth:`_on_recovery_back`, on whichever path the roll returns by.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        applied = next(
            (
                c
                for c in entry.character.conditions
                if c.condition_id == condition_id
                and c.parameter == subject
                and c.provenance is None
            ),
            None,
        )
        if applied is None:
            return
        # Its recovery check, or the shake-off against what put it on: a made one of
        # those comes back through the same _on_recovery_back (its spec's shakes_off).
        spec = condition_check(entry.character, self._data, applied)
        if spec is None:
            return
        who = self._npc_display_name(name)
        rolled = replace(spec, label=f"{who}: {spec.label}", actor=who)
        # Set first, as _roll_as does: a hosted server can answer before roll_spec
        # returns, and a test's die does not tumble at all.
        self._recovering = _Recovering(name, rolled)
        if not self._roller.roll_spec(rolled):
            self._recovering = None

    def _npc_display_name(self, name: str) -> str:
        """What an NPC is called on the board — numbered if it shares a name there."""
        entry = self._scene_entry_for(SCENE_NPC, name)
        if entry is not None:
            _sources, targets = self.impress_parties()
            party = next((p for p in targets if p.ref == entry.ref), None)
            if party is not None:
                return party.name
        npc = self._npc_state.get(name)
        return npc.summary.name if npc is not None else name

    def _on_recovery_back(self, roll: object) -> None:
        """A roll of the GM's own is back: if it was a held recovery check, settle it.

        Matched on the label it was rolled under — which the server keeps, and which
        names the creature — so an unrelated roll landing first is not mistaken for
        it. A made check sheds the condition through the path every NPC condition
        change takes (the card, an open sheet, the file, the board); a failed one says
        so and leaves the creature as it was.
        """
        pending = self._recovering
        if pending is None or not isinstance(roll, dict):
            return
        if str(roll.get("label", "")) != pending.spec.label:
            return
        self._recovering = None
        entry = self._npc_state.get(pending.source)
        if entry is None:
            return
        who = pending.spec.actor or entry.summary.name
        shake = pending.spec.shakes_off
        if shake is not None:
            # Recover's check against an impression or an Affliction (PDF p206).
            degree = roll.get("degree")
            if not isinstance(degree, int) or degree < 1:
                # A failed one does what the Affliction's modifiers say (Fatal's Dying…).
                held = next(
                    (
                        (s, changes)
                        for s, changes in held_shake_offs(entry.character)
                        if same_origin(s, shake)
                    ),
                    None,
                )
                said = f"is still {shake.name}"
                if held is not None:
                    party = ImpressParty(
                        f"{SCENE_NPC}:{pending.source}",
                        who,
                        PARTY_NPC,
                        pending.source,
                        entry.character,
                    )
                    said = land_shake_failure(self, self._data, party, held[0], held[1])
                self._show_notice(f"{who} {said}.", theme.color("text.muted.rich"))
                return
            removed = shake_off_made(entry.character, shake)
            if removed:
                self._after_npc_condition_change(entry, removed, applying=False)
            self._show_notice(f"{who} shakes off {shake.name}.", theme.color("tint.better"))
            return
        condition = self._change_names(
            [(pending.spec.recovers, pending.spec.recovers_parameter or None)]
        )
        if not recovery_succeeded(self._data, pending.spec, roll.get("degree")):
            self._show_notice(f"{who} is still {condition}.", theme.color("text.muted.rich"))
            return
        added = self._shed_npc_recovery(
            entry, pending.spec.recovers, pending.spec.recovers_parameter or None
        )
        if added is None:
            return
        text = f"{who} recovered from {condition}"
        if added:
            text += f" — now {self._change_names(added)}"
        self._show_notice(f"{text}.", theme.color("tint.better"))

    def _shed_npc_recovery(
        self, entry: _NpcEntry, condition_id: str, parameter: str | None
    ) -> list[tuple[str, str | None]] | None:
        """Shed what a made recovery check sheds on an NPC, through the path every NPC
        condition change takes. What it turned into, or ``None`` if it was already gone."""
        removed, added = apply_recovery(entry.character, self._data, condition_id, parameter)
        if not removed:
            return None
        self._after_npc_condition_change(entry, removed, applying=False)
        if added:
            self._after_npc_condition_change(entry, added, applying=True)
        return added

    def turn_recover(
        self, party, condition_id: str, parameter: str | None
    ) -> list[tuple[str, str | None]] | None:
        """An NPC made an end-of-turn recovery check (the Impress controller's host)."""
        entry = self._npc_state.get(party.source)
        if entry is None:
            return None
        return self._shed_npc_recovery(entry, condition_id, parameter)

    def request_recovery(self, party, condition_id: str, parameter: str | None) -> None:
        """Ask a player for an end-of-turn recovery check (the Impress controller's host)."""
        self._request_player_recovery(party.source, condition_id, parameter)

    def _request_player_recovery(
        self, player_id: str, condition_id: str, parameter: object
    ) -> None:
        """Ask a player to roll their recovery check — the 🎲 on their chip here.

        A player rolls their own dice, so the GM's die leaves a **request** in the
        history addressed to them, carrying the condition it is for; their app takes
        the condition off when they make it (see
        :meth:`~mm_companion.ui.sections.dice.DiceSection.settle_recovery`).
        """
        subject = str(parameter) if parameter else None
        # Read off the GM's latest view of their sheet: a condition an impression or an
        # Affliction put on is shaken off by resisting it again, not by its recovery
        # check, and the request carries the record so their app takes it all off.
        snapshot = self._snapshots.get(player_id)
        character = Character.from_dict(snapshot) if snapshot else None
        applied = (
            matching_condition(character, condition_id, subject) if character is not None else None
        )
        if applied is None:
            applied = AppliedCondition(condition_id, subject)
        spec = condition_check_request(character, self._data, applied)
        if spec is None:
            return
        name = self._player_name(player_id)
        self._request_roll(replace(spec, label=f"{name}: {spec.label}", targets=(name,)))

    def _apply_npc_damage(self, name: str, step_index: int) -> None:
        """Walk a rung of the damage ladder onto an NPC.

        The GM clicked a degree of failure; what that *means* — which conditions,
        and whether an already-Dazed target is Stunned instead — is
        :mod:`mm_companion.core.rules.damage`'s answer, resolved once here against
        this creature's current state. The ids it decided on are then what gets
        replayed onto an open sheet, so the two copies of the character cannot
        disagree about an escalation each would otherwise resolve for itself.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        steps = damage_steps(self._data)
        step = next((s for s in steps if s.index == step_index), None)
        if step is None:
            return
        if entry.character.minion:
            # A minion stops at the ceiling: no rung walks it on to Dying.
            step = minion_step(step, self._data, self.minion_rules())
        # Read *before* the apply: the summary resolves escalation against the
        # creature's current state, so asking afterwards would describe what the
        # next click would do rather than what this one just did.
        landed = damage_step_summary(entry.character, step, self._data)
        applied = apply_damage_step(entry.character, step, self._data)
        self._after_npc_condition_change(
            entry, [(condition_id, None) for condition_id in applied], applying=True
        )
        self._show_notice(f"“{entry.summary.name}” — {landed}", theme.color("tint.worse"))

    def _after_npc_condition_change(
        self,
        entry: _NpcEntry,
        changes: list[tuple[str, str | None]],
        *,
        applying: bool,
        amount: int | None = None,
        clearing: bool = False,
        requests: tuple = (),
        shake_off: ShakeOff | None = None,
    ) -> None:
        """Restate the NPC's card and persist one or more condition changes.

        Unlike a player, an NPC is local — so the change is applied to the model
        here rather than sent over the wire. If a sheet for this NPC is open, route
        the same changes through its conditions block so the open sheet stays in
        sync and owns its own save; otherwise write the model to its file now —
        **once**, however many conditions a damage rung brought with it.

        *amount* rides along with an applied condition that has one; *clearing*
        replays a "Clear temporary" (with no *changes*) rather than ids; *requests*
        replays a combat action's ``condition-requested`` payload (temporary flags and
        amounts kept) through the open sheet's ``take_conditions``; *shake_off* rides
        along with applied ones, so the open sheet keeps how they come off.
        """
        window = self._window_for(entry.path)
        section = getattr(window.sheet, "conditions", None) if window is not None else None
        if section is not None:
            # Absorbed rather than recorded, and for a sharper reason than the
            # player's: the card's entry and the open sheet are two different
            # Character objects, kept in step by replaying the settled ids. An undo
            # on the sheet would roll one back and not the other — exactly the
            # disagreement the replay exists to prevent.
            with absorbing(window.sheet):
                if clearing:
                    # The window writes the history line, naming the creature.
                    section.clear_temporary_conditions(note=False)
                if requests:
                    section.take_conditions(requests)
                for condition_id, parameter in changes:
                    if applying:
                        section.apply_condition_by_id(
                            condition_id, parameter, amount=amount, shake_off=shake_off
                        )
                    else:
                        section.remove_condition_by_id(condition_id, parameter)
        else:
            try:
                library.save_character(entry.character, path=entry.path)
            except OSError:
                pass  # an unwritable workspace is not worth a dialog mid-session
        if entry.card is not None:
            entry.card.refresh_conditions()
            # And the pinned numbers, which a condition moves: a Vulnerable NPC's
            # Defense chip must read what the GM should actually use. The chips
            # resolve from the model, so this is only a matter of asking them to.
            # (A player's card gets this for free — a condition there comes back as
            # a fresh snapshot, which restates the whole card.)
            entry.card.pins.refresh()
        # And the table, if this creature is on the board: this is the only path
        # that changes conditions without going through _refresh_npcs (the GM's "+",
        # the right-click that sheds one, every rung of the damage ladder). Without
        # it the GM's card showed a dazed creature while the players' did not.
        if self._scene_entry_for(SCENE_NPC, entry.path.name) is not None:
            self._push_scene()
