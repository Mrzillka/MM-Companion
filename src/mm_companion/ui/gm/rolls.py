"""Rolling for the GM's creatures and answering the shared history's save chips and
requests with them, and the session's minion rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (
    QMenu,
)

from mm_companion.core import library, storage
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.rules import (
    MinionRules,
    RollSpec,
    answer_reading,
    answer_rung,
    condition_changes,
    critical_answer,
    is_minion,
    localize_spec,
    minion_save_spec,
    resolve_rung,
    rung_caption,
)
from mm_companion.core.session.model import (
    KIND_REQUEST,
)
from mm_companion.ui import theme
from mm_companion.ui.action_grid import GridSection
from mm_companion.ui.action_menu import combat_menu
from mm_companion.ui.action_ui import (
    TABLE_MOD_ID,
    TABLE_SESSION_KEY,
    session_minion_rules,
)
from mm_companion.ui.area import AREA_ACTION, KIND_AREA_ATTACK
from mm_companion.ui.gm.model import (
    ANSWERED_MARK,
    AS_WRITTEN_CAPTION,
    CRITICAL_MARK,
    NO_NPCS_TO_ANSWER,
    UNTRAINED_MARK,
    _Answering,
)
from mm_companion.ui.group_check import MODE_GROUP, MODE_TEAM
from mm_companion.ui.impress import (
    IMPRESS_ACTION,
    KIND_IMPRESS,
    PARTY_NPC,
    ImpressParty,
)
from mm_companion.ui.roll_history import AnswerContext
from mm_companion.ui.scene_card import CARD_AREA, CARD_END_TURN, CARD_IMPRESS
from mm_companion.ui.sections.conditions import condition_display_name
from mm_companion.ui.undo import absorbing

#: The Actions popup's Team attack tile: a call to roll anyone joins (ui/team_call.py).
TEAM_ATTACK = "team_attack"
#: The group tiles (``actions.json``), and the group window each opens.
TEAM_CHECK = "team_check"
GROUP_CHECK = "group_check"
GROUP_ACTIONS = (TEAM_CHECK, GROUP_CHECK)

#: Why the group tiles are greyed out on a board with no group.
NO_NPC_GROUPS = (
    "Make an NPC group first: drop one NPC card onto another, or make several with Quick NPC."
)


class GMRollsMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

    def _on_history_roll(self, spec: object, context: object = None) -> None:
        """A save chip or a request card clicked in the GM's history.

        A spec that names a trait is answered with *someone's* number, and here that is
        an NPC's: a menu of the creatures nobody plays on the board (those it names
        first, then the board's order), each with its picture and the number it would
        roll, rolls it with that one's own number under its name. A creature that
        already answered this card is ticked, and a trained-only skill it lacks is
        flagged — both still roll. "As written" (or a right-click on the button) rolls
        it with nobody's.
        """
        if not isinstance(spec, RollSpec):
            return
        where = context if isinstance(context, AnswerContext) else AnswerContext()
        if not spec.trait_key:
            self._roller.roll_spec(spec)
            return
        _sources, targets = self.impress_parties()
        npcs = [p for p in targets if p.kind == PARTY_NPC and p.character is not None]
        named = set(spec.targets)
        npcs.sort(key=lambda p: p.name not in named)
        answered = self._answered.get(where.seq, set()) if where.seq is not None else set()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if not npcs:
            menu.addAction(NO_NPCS_TO_ANSWER).setEnabled(False)
        rules = self.minion_rules()
        for party in npcs:
            crit = (
                critical_answer(spec.ladder, party.character, self._data, rules)
                if where.critical and party.minion and spec.ladder is not None
                else None
            )
            if crit is not None:
                # A critical hit on a minion is the worst outcome with no save at all
                # (PDF p197): offered as what it is, not as a roll.
                caption = rung_caption(crit, self._data) or "the worst outcome"
                action = menu.addAction(
                    f"{party.name} — {CRITICAL_MARK} {caption}, no save",
                    lambda p=party, r=crit: self._crit_minion(p, r, where),
                )
                action.setToolTip("A critical hit on a minion lands its worst, unrolled.")
                portrait = self._scene_board.portrait(party.ref)
                if portrait is not None:
                    action.setIcon(QIcon(portrait))
                continue
            reading = answer_reading(spec, party.character, self._data)
            text = party.name
            tips = []
            if reading is not None:
                text = f"{text} — {reading.label} {reading.modifier:+d}"
                if not reading.usable:
                    text = f"{text} {UNTRAINED_MARK}"
                    tips.append(f"{reading.label} can't be used untrained; it rolls anyway.")
            if party.name in answered:
                text = f"{ANSWERED_MARK} {text}"
                tips.append("Already rolled for this one.")
            action = menu.addAction(text, lambda p=party: self._roll_as(spec, p, where))
            if tips:
                action.setToolTip(" ".join(tips))
            portrait = self._scene_board.portrait(party.ref)
            if portrait is not None:
                action.setIcon(QIcon(portrait))
        menu.addSeparator()
        menu.addAction(AS_WRITTEN_CAPTION, lambda: self._roller.roll_spec(spec))
        menu.exec(QCursor.pos())

    def _roll_as(
        self, spec: RollSpec, party: ImpressParty, where: AnswerContext | None = None
    ) -> None:
        """Roll *spec* as *party* rolls it: their own number in, their name on it.

        The roller's own adjustment and Hidden still apply. Once the roll is back,
        :meth:`_on_answer_back` puts what a failed save did on the creature.
        """
        where = where or AnswerContext()
        own = localize_spec(spec, party.character, self._data)
        rolled = replace(
            own,
            label=f"{party.name}: {own.label}",
            targets=(party.name,),
            trait_key="",
            actor=party.name,
        )
        if party.minion:
            # Every seat's card then reads the worst rung, which is what lands.
            rolled = minion_save_spec(rolled, self.minion_rules())
        # Set first: the roll can come back before roll_spec returns (a hosted
        # server answers in-process, and a test's die does not tumble).
        self._answering = _Answering(spec, party, where)
        if not self._roller.roll_spec(rolled, minion=self.rolls_as_minion(party.character)):
            self._answering = None
            return
        if where.seq is not None:
            self._answered.setdefault(where.seq, set()).add(party.name)

    def _on_answer_back(self, roll: object) -> None:
        """A roll of the GM's own is back: if an NPC answered with it, apply its rung."""
        answering = self._answering
        if answering is None or not isinstance(roll, dict):
            return
        spec = RollSpec.from_dict(roll.get("spec"))
        name = answering.party.name
        actor = spec.actor if spec is not None else ""
        if actor != name and not str(roll.get("label", "")).startswith(f"{name}: "):
            return
        self._answering = None
        self._apply_answer(answering, roll)

    def _apply_answer(self, answering: _Answering, roll: dict) -> None:
        """Put what the NPC's roll did on it — if the GM's setting says to.

        The ladder is the one the spec carries (:class:`SpecLadder`), the rung resolved
        against the creature as it stands, and what actually changed is kept so the
        card's ↶ can take it back off.
        """
        degree = roll.get("degree")
        ladder = answering.spec.ladder
        if degree is None or ladder is None:
            return
        if not storage.gm_auto_apply().get(ladder.kind, True):
            return
        party = answering.party
        entry = self._npc_state.get(party.source)
        if entry is None:
            return
        rung = answer_rung(ladder, int(degree), entry.character, self._data, self.minion_rules())
        if rung is None:
            return
        self._land_answer(party, rung, answering.where.source, roll.get("seq"))

    def _land_answer(self, party: ImpressParty, rung, source: str, seq: object) -> None:
        """Put *rung* on an NPC, say so, and hang a ↶ on the card *seq* names."""
        entry = self._npc_state.get(party.source)
        if entry is None:
            return
        before = deepcopy(entry.character.conditions)
        changes = resolve_rung(entry.character, rung, self._data, source=source)
        if not changes:
            return
        self.impress_apply(party, list(changes))
        added, removed = condition_changes(before, entry.character.conditions)
        if not added and not removed:
            return
        names = self._change_names(changes)
        self._show_notice(f"{party.name}: {names}", theme.color("tint.worse"))
        if isinstance(seq, int):
            self._history.set_undo(
                seq,
                f"Take {names} back off {party.name}",
                lambda: self._undo_answer(seq, party, added, removed, names),
            )

    def _crit_minion(self, party: ImpressParty, rung, where: AnswerContext) -> None:
        """A critical hit on a minion: its worst, with no resistance check (PDF p197).

        Nothing is rolled, so the history gets a line saying what happened, and the
        ↶ that takes it back hangs on the attack's own card.
        """
        caption = rung_caption(rung, self._data) or "the worst outcome"
        self._post_note(f"{party.name}: critical hit on a minion — {caption}, no save")
        self._land_answer(party, rung, where.source, where.seq)
        if where.seq is not None:
            self._answered.setdefault(where.seq, set()).add(party.name)

    def _undo_answer(
        self,
        seq: int,
        party: ImpressParty,
        added: list[tuple[str, str | None]],
        removed: list[tuple[str, str | None]],
        names: str,
    ) -> None:
        """Take back what an NPC's roll applied: off what it added, on what it replaced."""
        self._history.set_undo(seq, "", None)
        if party.source not in self._npc_state:
            return
        if added:
            self.action_remove(party, added)
        if removed:
            self.impress_apply(party, removed)
        self._show_notice(f"{party.name}: took back {names}", theme.color("text.muted.rich"))

    def _change_names(self, changes) -> str:
        """``(id, parameter)`` pairs as the chips would name them."""
        catalog = self._data.condition_catalog()
        return ", ".join(
            condition_display_name(AppliedCondition(cid, parameter), catalog.get(cid))
            for cid, parameter in changes
        )

    def action_menu(self, own: Mapping[str, str | None]) -> list[GridSection]:
        """The Actions popup's sections on the Rolls block's roller (see
        :func:`~mm_companion.ui.action_menu.combat_menu`): the panel's own actions
        (*own*: Reroll), Impress and Area, the group actions, which need a group, and
        the combat actions an NPC takes (:meth:`combat_offered`)."""
        offered = dict(own)
        offered.update(self.combat_offered())
        if self._data.impress.available:
            offered[KIND_IMPRESS] = None
        if self._data.area.available:
            offered[KIND_AREA_ATTACK] = None
        groups = None if self._npc_groups else NO_NPC_GROUPS
        for key in GROUP_ACTIONS:
            offered[key] = groups
        offered[TEAM_ATTACK] = self._team_calls.reason()
        return combat_menu(self._data, offered, gm=True, fallback=[IMPRESS_ACTION, AREA_ACTION])

    def _on_roller_action(self, kind: str) -> None:
        if self._gm_combat_action(kind):
            return
        if kind == KIND_IMPRESS:
            self._impress.open_new()
        elif kind == KIND_AREA_ATTACK:
            self._area.open_new()
        elif kind == TEAM_ATTACK:
            self._team_calls.open_new(self._aim_targets())
        elif kind in GROUP_ACTIONS:
            group_id = self._choose_group()
            if group_id is None:
                return
            self._group_check(group_id, MODE_TEAM if kind == TEAM_CHECK else MODE_GROUP)

    def _choose_group(self) -> str | None:
        """Which NPC group acts: the only one there is, or the one picked off a menu of
        them at the pointer (each with its member count)."""
        groups = list(self._npc_groups)
        if len(groups) <= 1:
            return groups[0].id if groups else None
        menu = QMenu(self)
        for group in groups:
            menu.addAction(f"{group.name} ({len(group.members)})").setData(group.id)
        chosen = menu.exec(QCursor.pos())
        return chosen.data() if chosen is not None else None

    def _on_scene_action(self, ref: str, action: str) -> None:
        """A creature's card on the board, right-clicked: end its turn, or have it act."""
        _sources, targets = self.impress_parties()
        party = next((p for p in targets if p.ref == ref), None)
        if party is None:
            return
        if action == CARD_END_TURN:
            self._impress.end_turn(party)
        elif action == CARD_IMPRESS:
            self._impress.open_new(source_key=party.source)
        elif action == CARD_AREA:
            self._area.start_for(party)

    # -- minions -------------------------------------------------------------

    def minion_rules(self) -> MinionRules:
        """The minion rules the open session plays by (the book's, with none open)."""
        return session_minion_rules(self._bridge)

    def rolls_as_minion(self, character: Character | None) -> bool:
        """Whether a roll made as *character* is a minion's, whose natural 20 is no
        added success — only at a table playing that rule."""
        return is_minion(character) and self.minion_rules().no_added_success

    def _on_mod_state(self, mod_id: str, key: str, _payload: object) -> None:
        """The session's shared state moved: restate what reads its minion rules."""
        if mod_id == TABLE_MOD_ID and key == TABLE_SESSION_KEY:
            self._apply_minion_rules()

    def _apply_minion_rules(self) -> None:
        """Tell every card the session's minion rules, and re-push the board.

        The damage row's shape and whether the table sees a minion's badge both depend
        on them, so a GM who changes the rules sees both follow straight away.
        """
        rules = self.minion_rules()
        for entry in self._npc_state.values():
            if entry.card is not None:
                entry.card.set_minion_rules(rules)
        self._push_scene()

    def _set_npc_minion(self, name: str, minion: bool) -> None:
        """Make an NPC a minion, or a full NPC again (its card's toggle or menu).

        The flag is the creature's own, saved in its file like any edit, so every copy
        and every later session sees it. It goes down the road a condition change
        takes: replayed onto an open sheet (absorbed, so its undo cannot split the two
        copies apart) or written to the file now.
        """
        what = "a minion" if minion else "a full NPC"
        self._set_npc_flag(name, "minion", minion, what)

    def _set_npc_summoned(self, name: str, summoned: bool) -> None:
        """Tag an NPC as summoned, or take the tag off (its card's menu)."""
        what = "tagged as summoned" if summoned else "no longer tagged as summoned"
        self._set_npc_flag(name, "summoned", summoned, what)

    def _set_npc_flag(self, name: str, attribute: str, value: bool, what: str) -> None:
        """Set one of an NPC's own flags (``minion``, ``summoned``) from its card.

        The flag is the creature's own, saved in its file like any edit, so every copy
        and every later session sees it. It goes down the road a condition change
        takes: replayed onto an open sheet (absorbed, so its undo cannot split the two
        copies apart) or written to the file now.
        """
        entry = self._npc_state.get(name)
        if entry is None or getattr(entry.character, attribute) == value:
            return
        setattr(entry.character, attribute, value)
        window = self._window_for(entry.path)
        if window is not None:
            with absorbing(window.sheet):
                setattr(window.sheet.character, attribute, value)
                window.sheet.system_info.reseed()
        else:
            try:
                library.save_character(entry.character, path=entry.path)
            except OSError:
                pass  # an unwritable workspace is not worth a dialog mid-session
        if entry.card is not None:
            entry.card.refresh_minion()
        self._push_scene()
        self._show_notice(f"“{entry.summary.name}” is now {what}.", theme.color("accent"))

    def _npc_roll(self, name: str, spec: object, *, load: bool = False) -> None:
        """A pinned tile on an NPC's card: roll (or load) it as that creature rolls."""
        if not isinstance(spec, RollSpec):
            return
        self._follow_actor(name)
        entry = self._npc_state.get(name)
        minion = self.rolls_as_minion(entry.character if entry is not None else None)
        if load:
            self._roller.load_spec(spec, minion=minion)
        else:
            self._roller.roll_spec(spec, minion=minion)

    def _request_roll(self, spec: object) -> None:
        """Ask the table to roll something — the Request row's handler.

        The twin of :meth:`~mm_companion.ui.sections.dice.DiceSection.request_roll`,
        and it has to be written out here for the reason
        :meth:`_show_offline_roll` does: this window owns its history, so the
        view's own off-air fallback stands down and there would otherwise be no
        card at all before hosting starts — a button that silently does nothing.
        """
        if not isinstance(spec, RollSpec):
            return
        if self._bridge.prompt_roll(spec.to_dict()):
            return
        self._offline_seq -= 1
        self._history.add_roll(
            {
                "seq": self._offline_seq,
                "player_name": self._name_of_gm(),
                "die": 0,
                "kind": KIND_REQUEST,
                "label": spec.label,
                "dc": spec.dc,
                "spec": spec.to_dict(),
            }
        )
