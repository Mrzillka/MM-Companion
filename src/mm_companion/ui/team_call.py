"""A team attack as a **call to roll**: the GM sends it, anyone joins, the GM resolves.

Decision 15 of the combat maneuvers plan. The GM's Team attack tile opens
:class:`TeamCallWindow` — the target and one bonus or penalty — and **Send** posts the
call as a request record (:func:`~mm_companion.core.rules.team_call_spec`). Every
screen's history draws it as a table with a **Join** button
(:class:`~mm_companion.ui.roll_history.GroupTableCard`). Joining arms the presser's
roller, and the next attack they roll — their own roller, their own maneuvers — is
stamped as an entry, so it fills a row of that table everywhere. The GM joins for an
NPC the same way, picking which one.

:class:`TeamCallController` gathers the entries off the shared feed (one per attacker:
a later roll, a re-roll included, replaces the earlier one) and keeps the GM's
:class:`~mm_companion.ui.roll_history.ActionCard` up to date: each entry judged against
the target's Defense in play, and what the team would come to if resolved now.
**Resolve** strikes the call (the Join buttons go; the rows stay) and hands the entries
to :meth:`~mm_companion.ui.attack_gm.AttackController.resolve_team`, which combines
them and rolls or asks the target's save on the aimed-attack road. **Call off** strikes
it and does nothing else. The NPC-group window (``ui/team_attack.py``) is still there,
off a group's ☰ menu: the squad rolled at once, with no maneuvers.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    MinionRules,
    RollSpec,
    TeamEntry,
    combine_team_attack,
    defense_class_in_play,
    is_team_call,
    team_call_spec,
    team_entry_option,
    team_fits,
)
from mm_companion.core.session.model import KIND_REQUEST, KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.action_run import total_of
from mm_companion.ui.action_ui import ADJUST_LIMIT
from mm_companion.ui.attack_gm import AttackController, team_summary
from mm_companion.ui.attack_targets import SceneTarget
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty, new_action_id
from mm_companion.ui.roll_history import ActionCard
from mm_companion.ui.widgets import guard_wheel, make_signed_spin_box

RESOLVE_CAPTION = "Resolve"
CALL_OFF_CAPTION = "Call off"
#: Why the GM's Team attack tile is greyed off the air: nobody could answer the call.
NO_SESSION_FOR_CALL = (
    "Start or join a session first: a team attack is a call the table answers. "
    "Off the air, an NPC group's ☰ menu still has Team attack."
)
#: Why it is greyed with nobody on the Scene.
NO_TARGET_FOR_CALL = "Put someone on the Scene first: a team attack is called against them."
#: The card's status while nobody has joined.
NOBODY_YET = "Waiting for attacks — Join on the call's table, then roll."


class TeamCallHost(Protocol):
    """What the controller needs from the GM window."""

    def impress_parties(self) -> tuple[list[ImpressParty], list[ImpressParty]]: ...

    def impress_notice(self, text: str, colour: str) -> None: ...

    def action_add_card(self, card: ActionCard) -> None: ...

    def minion_rules(self) -> MinionRules: ...


@dataclass
class _Entry:
    """One attacker's part: who, and the attack roll that counts for them."""

    party: ImpressParty
    record: dict


@dataclass
class TeamCall:
    """One call to roll the GM has sent and not settled."""

    spec: RollSpec
    target_ref: str
    target_name: str
    card: ActionCard | None = None
    #: By ``(seat, the creature it rolled as)``, in the order they first joined.
    entries: dict[tuple[str, str], _Entry] = field(default_factory=dict)
    #: The request record's ``seq``, once it has come back from the session.
    seq: int | None = None
    resolving: bool = False
    done: bool = False

    @property
    def action_id(self) -> str:
        return self.spec.action_id


class TeamCallWindow(QDialog):
    """Set up a call: the target, and a bonus (+) or penalty (-) on every attack."""

    #: ``(target ref, adjust)`` — Send was pressed.
    sent = Signal(str, int)

    def __init__(self, targets: list[SceneTarget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Team attack")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._target = QComboBox()
        guard_wheel(self._target)
        for target in targets:
            text = f"{target.name} (minion)" if target.minion else target.name
            self._target.addItem(text, target.ref)
        form.addRow("Target:", self._target)
        self._adjust = make_signed_spin_box(ADJUST_LIMIT)
        self._adjust.setToolTip("A bonus (+) or penalty (-) on every attack that joins")
        form.addRow("Bonus/Penalty:", self._adjust)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        send = buttons.button(QDialogButtonBox.StandardButton.Ok)
        send.setText("Send")
        send.setToolTip("Post the call in everyone's history: each attacker joins, then rolls")
        send.setEnabled(self._target.count() > 0)
        buttons.accepted.connect(self._send)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_choice(self, ref: str, adjust: int) -> None:
        """Pick the target and the adjustment (for the tests)."""
        self._target.setCurrentIndex(max(0, self._target.findData(ref)))
        self._adjust.setValue(adjust)

    def _send(self) -> None:
        ref = str(self._target.currentData() or "")
        if not ref:
            return
        self.sent.emit(ref, int(self._adjust.value()))
        self.accept()


class TeamCallController(QObject):
    """Every team attack call the GM window has sent (see the module docstring)."""

    def __init__(
        self,
        data: GameData,
        bridge,
        host: TeamCallHost,
        attack: AttackController,
        parent_window: QWidget,
    ) -> None:
        super().__init__(parent_window)
        self._data = data
        self._bridge = bridge
        self._host = host
        self._attack = attack
        self._parent_window = parent_window
        self._calls: dict[str, TeamCall] = {}
        #: The setup window, while it is open (kept so it is not collected).
        self.window: TeamCallWindow | None = None
        bridge.rollAdded.connect(self.take_roll)
        bridge.rollRemoved.connect(self._on_removed)

    @property
    def calls(self) -> dict[str, TeamCall]:
        return dict(self._calls)

    def open_call(self, action_id: str) -> TeamCall | None:
        """The call *action_id* while it still takes entries, else ``None``."""
        call = self._calls.get(action_id)
        return call if call is not None and not call.done and not call.resolving else None

    def reason(self) -> str | None:
        """Why the Team attack tile is greyed now, or ``None`` when it is live."""
        if not self._bridge.in_session:
            return NO_SESSION_FOR_CALL
        _sources, targets = self._host.impress_parties()
        return None if targets else NO_TARGET_FOR_CALL

    # -- sending ---------------------------------------------------------------

    def open_new(self, targets: list[SceneTarget]) -> TeamCallWindow | None:
        """The Team attack tile: the setup window, over the GM's own Scene."""
        reason = self.reason()
        if reason:
            self._host.impress_notice(reason, theme.color("text.muted.rich"))
            return None
        window = TeamCallWindow(targets, self._parent_window)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.sent.connect(self.send)
        window.finished.connect(lambda _r, w=window: self._forget_window(w))
        self.window = window
        window.show()
        return window

    def _forget_window(self, window: TeamCallWindow) -> None:
        if self.window is window:
            self.window = None

    def send(self, target_ref: str, adjust: int) -> TeamCall | None:
        """Post the call at *target_ref*, *adjust* on every attack that joins."""
        _sources, targets = self._host.impress_parties()
        target = next((p for p in targets if p.ref == target_ref), None)
        if target is None:
            self._host.impress_notice(
                "That creature is not on the Scene any more.", theme.color("tint.worse")
            )
            return None
        spec = team_call_spec(new_action_id(), target.name, adjust)
        self._attack.ignore(spec.action_id)
        call = TeamCall(spec, target.ref, target.name)
        # Registered before it goes: a hosting server raises the record inside the call.
        self._calls[spec.action_id] = call
        if not self._bridge.prompt_roll(spec.to_dict()):
            del self._calls[spec.action_id]
            self._host.impress_notice(
                "The call did not reach the session — nothing was sent.",
                theme.color("tint.worse"),
            )
            return None
        card = ActionCard(spec.action_id, "", RESOLVE_CAPTION, second=CALL_OFF_CAPTION)
        card.actionRequested.connect(self.resolve)
        card.secondRequested.connect(self.call_off)
        call.card = card
        self._describe(call)
        self._host.action_add_card(card)
        return call

    # -- gathering -------------------------------------------------------------

    def take_roll(self, roll: object) -> None:
        """A record came in: a call's own request (its ``seq``), or an attack joining."""
        if not isinstance(roll, dict) or roll.get("group"):
            return
        raw = roll.get("spec")
        spec = raw if isinstance(raw, RollSpec) else RollSpec.from_dict(raw)
        if spec is None or not spec.action_id:
            return
        call = self._calls.get(spec.action_id)
        if call is None:
            return
        kind = roll.get("kind", KIND_ROLL)
        if kind == KIND_REQUEST and is_team_call(spec):
            seq = roll.get("seq")
            call.seq = seq if isinstance(seq, int) else call.seq
            return
        if kind != KIND_ROLL or spec.follow_up is None or call.done or call.resolving:
            return
        party = self._party_for(roll, spec)
        if party.ref == call.target_ref:
            return  # The target cannot join the attack on itself.
        call.entries[(str(roll.get("player_id", "")), spec.actor)] = _Entry(party, dict(roll))
        self._describe(call)

    def _party_for(self, roll: dict, spec: RollSpec) -> ImpressParty:
        """Who rolled *roll*: the NPC it was made as, or the seat's player."""
        sources, _targets = self._host.impress_parties()
        player_id = str(roll.get("player_id", ""))
        if spec.actor:
            found = next((p for p in sources if p.kind == PARTY_NPC and p.name == spec.actor), None)
            if found is not None:
                return found
        else:
            found = next(
                (p for p in sources if p.kind == PARTY_PLAYER and p.source == player_id), None
            )
            if found is not None:
                return found
        name = spec.actor or str(roll.get("player_name", "")) or "Someone"
        return ImpressParty(f"player:{player_id}", name, PARTY_PLAYER, player_id)

    def _on_removed(self, seq: int) -> None:
        """A record was struck: the call itself (the GM's ✕ on its table), or an entry."""
        for call in list(self._calls.values()):
            if call.done or call.resolving:
                continue
            if call.seq == seq:
                self._finish(call, "Called off — nothing happens.", strike=False)
                return
            for key, entry in list(call.entries.items()):
                if entry.record.get("seq") == seq:
                    del call.entries[key]
                    self._describe(call)
                    return

    # -- where it stands -------------------------------------------------------------

    def _target(self, call: TeamCall) -> ImpressParty | None:
        _sources, targets = self._host.impress_parties()
        return next((p for p in targets if p.ref == call.target_ref), None)

    def _members(self, call: TeamCall):
        """``(entries, options, left out)``: every entry with what it brings, and which
        of them cannot combine (by index, with why)."""
        entries = list(call.entries.items())
        options = [
            team_entry_option(self._spec_of(e.record), str(i)) for i, (_k, e) in enumerate(entries)
        ]
        kept = [(e, o) for (_k, e), o in zip(entries, options, strict=True) if o is not None]
        fits = team_fits([o for _e, o in kept], self._data)
        return kept, fits

    @staticmethod
    def _spec_of(record: dict) -> RollSpec | None:
        raw = record.get("spec")
        return raw if isinstance(raw, RollSpec) else RollSpec.from_dict(raw)

    def _describe(self, call: TeamCall) -> None:
        """The card: the call, each entry as it would be judged now, and the outcome."""
        card = call.card
        if card is None:
            return
        head = f"<b>{html.escape(call.spec.label)}</b>"
        kept, fits = self._members(call)
        if not kept:
            card.set_text(head)
            card.set_status(NOBODY_YET, button=RESOLVE_CAPTION)
            card.button.setEnabled(False)
            return
        target = self._target(call)
        lines = [head]
        judged: list[TeamEntry] = []
        counted: list[tuple[_Entry, object]] = []
        dc = None
        if target is not None and target.character is not None:
            dc = defense_class_in_play(target.character, self._data)
        for index, (entry, option) in enumerate(kept):
            text = f"{html.escape(entry.party.name)}: {total_of(entry.record)}"
            if index in fits:
                lines.append(f"{text} — left out ({html.escape(fits[index])})")
                continue
            rank = f"rank {option.rank}" if option.rank else ""
            if dc is None:
                lines.append(f"{text}" + (f" ({rank})" if rank else ""))
                continue
            verdict = self._attack.judge_entry(entry.party, entry.record, target, dc, option.attack)
            mark = "critical hit" if verdict.critical else ("hit" if verdict.hit else "missed")
            lines.append(f"{text} vs DC {dc}: {mark}" + (f" ({rank})" if rank else ""))
            degree = max(verdict.degree, 1) if verdict.hit else verdict.degree
            judged.append(
                TeamEntry(entry.party.name, option.rank, verdict.hit, degree, verdict.critical)
            )
            counted.append((entry, option))
        card.set_text("<br>".join(lines))
        card.button.setEnabled(True)
        if dc is None:
            status = (
                f"{call.target_name} is not on the Scene any more."
                if target is None
                else f"{call.target_name}'s sheet has not reached this table yet."
            )
        else:
            result = combine_team_attack(judged, self._data)
            if result.any_hit:
                entry, option = counted[result.primary]
                status = "Now: " + team_summary(entry.party.name, option.rank, result)
            else:
                status = "Now: every attack misses."
        card.set_status(status, button=RESOLVE_CAPTION)

    # -- settling --------------------------------------------------------------------

    def resolve(self, action_id: str) -> None:
        """The GM's Resolve: strike the call, combine the entries, and land the save."""
        call = self.open_call(action_id)
        if call is None:
            return
        target = self._target(call)
        if target is None or target.character is None:
            self._describe(call)
            return
        kept, fits = self._members(call)
        members = [(entry.party, option) for i, (entry, option) in enumerate(kept) if i not in fits]
        records = [entry.record for i, (entry, _o) in enumerate(kept) if i not in fits]
        if not members:
            self._describe(call)
            return
        call.resolving = True
        self._strike(call)
        if call.card is not None:
            call.card.button.setEnabled(False)
            if call.card.second_button is not None:
                call.card.second_button.setEnabled(False)
            call.card.set_status("Resolving…")
        self._attack.resolve_team(
            call.action_id,
            members,
            records,
            target,
            title=call.spec.title,
            on_done=lambda text, c=call: self._finish(c, text, strike=False),
        )

    def call_off(self, action_id: str) -> None:
        """The GM's Call off: strike the call, and nothing happens."""
        call = self.open_call(action_id)
        if call is not None:
            self._finish(call, "Called off — nothing happens.")

    def _strike(self, call: TeamCall) -> None:
        """Take the call's request out of everyone's history (its Join buttons go)."""
        if call.seq is not None:
            self._bridge.remove_roll(call.seq)

    def _finish(self, call: TeamCall, text: str, *, strike: bool = True) -> None:
        call.done = True
        call.resolving = False
        if strike:
            self._strike(call)
        if call.card is not None:
            call.card.set_done(text)
