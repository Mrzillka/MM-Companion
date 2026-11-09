"""The Dice block: the d20 roller, on the sheet rather than in its own window.

Rolling dice used to mean a separate top-level window opened from ``Tools ▸ Dice
Roller…``. It is a sheet block now, which means it gets everything the other
blocks get for free — drag to reorder, pop out into its own window, hide and
reopen from the View menu — and, most of the point, it can be **parked in the
pinned strip**, the one region that does not scroll with the page. Its descriptor
sets ``default_pinned``, so a fresh sheet opens with the die already beside the
page instead of scrolling away under it.

The block is a thin wrapper: all of the behaviour is
:class:`~mm_companion.ui.roller.view.DiceRollerView` (the roll column plus the
private history, swapped for the table's shared one in a session), stacked
vertically to suit a narrow strip.

Two things about it are deliberately unlike its neighbours:

* It is **barely** a view over the character. The roller drives
  :mod:`mm_companion.core.dice` directly and keeps its quick rolls in
  :mod:`mm_companion.core.storage`, so it reads nothing off the build and
  publishes nothing on the bus — a roll is not an edit and must never mark the
  sheet dirty. It does **serve** one topic: every other block sends what it wants
  rolled to :meth:`DiceSection.perform_roll` over the bus's payload channel, and
  that method is the one thing here that touches the character — it fills this
  sheet's own resistance into a save somebody else's attack asked for.
* :meth:`DiceSection.set_locked` does nothing. Rolling is a mid-play action, not
  a build edit, so it stays available in the locked read-only view — the same
  reasoning that keeps a power's on/off switch live there.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.data_loader.game_data import COMBAT_KIND_MANEUVER
from mm_companion.core.rules import (
    AreaAttempt,
    ImpressAttempt,
    RollSpec,
    action_reason,
    area_choices,
    area_key_index,
    find_area_power,
    localize_spec,
    power_area_use,
    recovery_succeeded,
    requested_roll_choices,
    self_action,
    targeted_action,
    targeted_check_spec,
)
from mm_companion.core.session.protocol import sanitize_mod_payload
from mm_companion.ui import theme
from mm_companion.ui.action_grid import GridSection
from mm_companion.ui.action_menu import (
    NEEDS_A_TABLE,
    NO_ALLY_AND_OPPONENT,
    NO_AREAS,
    NO_ATTACKS,
    NO_ONE_TO_TARGET,
    SKIP_TARGET,
    choose_from_menu,
    combat_menu,
)
from mm_companion.ui.action_ui import sets_up_own_actions
from mm_companion.ui.area import KIND_AREA_ATTACK, AreaDialog
from mm_companion.ui.attack_targets import (
    KIND_ATTACK,
    KIND_TARGETED,
    AttackDeclaration,
    SceneTarget,
    TargetedDeclaration,
    aimed_spec,
    session_targets,
    targeted_roll_spec,
    targeted_table_title,
)
from mm_companion.ui.impress import IMPRESS_ACTION, KIND_IMPRESS, ImpressDialog, new_action_id
from mm_companion.ui.roller.combat_runner import (
    CombatActor,
    CombatRunner,
)
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.session_bridge import SessionBridge, live_session
from mm_companion.ui.toasts import notify_window


class DiceSection(QGroupBox):
    """A d20 roller as a sheet block (see the module docstring)."""

    #: The roll history grows into the block, so the roller takes the height it is
    #: given (see :meth:`~mm_companion.ui.block_frame.InnerScroll.set_section`).
    #: Not derivable from this widget's own policy: a ``QGroupBox`` is ``Preferred``
    #: and it is the history *inside* it that wants the room.
    fills_height = True

    #: ``(condition_id, parameter)`` — a recovery check this roller threw came back
    #: good enough, so the condition it was for comes off. Raised on the bus's
    #: ``recovery-requested`` topic, which the Conditions block serves: a roll is not
    #: an edit, so the roller never touches the character itself.
    recovered = Signal(object)
    #: A power roll this block just threw that is a *use* of something — what it costs
    #: (Tiring, Distracting) and what it counts (Short-Term, Fades). Raised on
    #: ``use-spent`` for the Conditions and Powers blocks, for the reason
    #: :attr:`recovered` is: a roll is not an edit.
    useSpent = Signal(object)
    #: ``((condition_id, amount), …)`` — an attack rolled with maneuvers on it leaves
    #: these on the roller's own sheet (All-Out Attack's Defense penalty), hit or miss —
    #: or ``(ConditionChange, …)`` from a self action (Defend, Drop Prone, Stand).
    #: Raised on ``condition-requested`` for the Conditions block, for the reason
    #: :attr:`recovered` is: a roll is not an edit.
    conditionsRequested = Signal(object)

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)
        # Accepted for the uniform block contract and unused: the roller owns no
        # character state.
        self._data = data
        self._character = character
        #: The area window open on this sheet, if any — kept so it is not collected,
        #: and so a second area line raises a fresh one rather than stacking them.
        self._area_dialog: AreaDialog | None = None
        #: The same for the Impress dialog, which is modeless for the same reason.
        self._impress_dialog: ImpressDialog | None = None

        self.view = DiceRollerView()
        # Every spec that reaches the roller — from the bus, or from a follow-up chip
        # on a history card — passes through here on its way in.
        self.view.panel.set_localizer(self._localize)
        # And the traits the Request row may ask the table for. Character-free on
        # purpose: a request is answered on somebody else's sheet.
        self.view.panel.set_roll_choices(requested_roll_choices(self._data))
        self.view.panel.rollRequested.connect(self.request_roll)
        # A recovery check is settled when its result is in, on either path a roll
        # comes back by: the session's record, or the die this panel threw itself.
        self.view.panel.sessionRollRevealed.connect(self.settle_recovery)
        self.view.panel.localRoll.connect(self.settle_recovery)
        # The Actions popup: what this sheet runs, from the ruleset's menu.
        self.view.panel.set_action_menu(self.action_menu)
        self.view.panel.actionRequested.connect(self.start_action)
        # The maneuvers and self actions, always taken by this sheet's own character:
        # the attack picker's list, what the lines do to a roll, and where what they
        # leave behind lands (the Conditions block, over the bus).
        self.combat = CombatRunner(
            self._data,
            self.view.panel,
            note=lambda text: self.post_note(text),
            parent=self,
            actor=CombatActor(self._character, land=self.conditionsRequested.emit),
        )
        # A team attack's call to roll, joined in the session history: this sheet's
        # next attack is its entry.
        self.view.teamJoinRequested.connect(self.combat.join_team)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def action_menu(self, own: Mapping[str, str | None]) -> list[GridSection]:
        """The Actions popup's sections, for the roller to open (see
        :func:`~mm_companion.ui.action_menu.combat_menu`): the panel's own actions
        (*own*: Reroll) plus what a player's sheet runs."""
        offered = dict(own)
        if self._data.impress.available:
            offered[KIND_IMPRESS] = None
        attacks = None if self.view.panel.attack_choices() else NO_ATTACKS
        offered[KIND_ATTACK] = attacks
        for action in self._data.combat.actions:
            if action.kind == COMBAT_KIND_MANEUVER:
                offered[action.id] = attacks
            elif action.gm_runs:
                offered[action.id] = action.gm_runs
            elif self_action(self._data, action.id) is not None:
                reason = action_reason(self._data, action, self._character)
                offered[action.id] = reason or (attacks if action.needs_attack else None)
            elif targeted_action(self._data, action.id) is not None:
                offered[action.id] = self._targeted_reason(action) or (
                    attacks if action.needs_attack else None
                )
        if self._data.area.available:
            offered[KIND_AREA_ATTACK] = (
                None if area_choices(self._character, self._data) else NO_AREAS
            )
        return combat_menu(self._data, offered, gm=False, fallback=[IMPRESS_ACTION])

    @property
    def panel(self):
        """The roll column itself (settings, die, readout, quick rolls)."""
        return self.view.panel

    def _localize(self, spec: RollSpec) -> RollSpec:
        """Fill this character's own number into a spec that came from someone else.

        The save an attack forced knows which resistance it is but not the target's
        value for it — the attacker's sheet could not see that. Clicked on the
        target's sheet, this is where their number goes in. The one thing on this
        block that reads the character, and why it is given one.
        """
        return localize_spec(spec, self._character, self._data)

    def perform_roll(self, spec: object, *, before=None) -> None:
        """Roll what another block asked for — the ``roll-requested`` topic's handler.

        Anything that isn't a :class:`~mm_companion.core.rules.RollSpec` is ignored
        rather than raising: the payload comes off a bus a mod block can publish on
        too, and a bad payload should cost a roll, not the sheet.
        """
        if isinstance(spec, RollSpec):
            if self.view.panel.roll_spec(spec, before=before) and (spec.costs or spec.spends):
                # The die thrown off a card *is* using the power, so what using it
                # costs and counts lands now — once, here, and only for a die that was
                # really thrown, never for a click the tumbling roller refused.
                self.useSpent.emit(spec)

    def start_maneuver(self, action_id: str):
        """An attack maneuver's tile — see
        :meth:`~mm_companion.ui.roller.combat_runner.CombatRunner.start_maneuver`."""
        return self.combat.start_maneuver(action_id)

    def start_self_action(self, action_id: str):
        """A self action's tile — see
        :meth:`~mm_companion.ui.roller.combat_runner.CombatRunner.start_self_action`."""
        return self.combat.start_self_action(action_id)

    def aim_attack(self, payload: object) -> None:
        """Roll an attack *at* a creature on the Scene — ``attack-requested``'s handler.

        At a table the attack is declared to the GM first (``KIND_ATTACK``), then rolled
        here like any attack, its spec carrying the declaration's id and the target's
        name — so the GM's app ties the two together, judges the hit against the
        target's Defense, and resolves the save once the GM accepts. Off the air there
        is nobody to judge it, and it is simply rolled.
        """
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        spec, target = payload
        if not isinstance(spec, RollSpec) or not isinstance(target, SceneTarget):
            return
        bridge = live_session()
        if bridge is None:
            self.perform_roll(spec)
            return
        if self.view.panel.rolling:
            # The roller would refuse the die, and a declaration sent without one would
            # leave the GM a card waiting on a roll that never comes.
            return
        name = library.display_name(self._character)
        action_id = new_action_id()
        aimed = aimed_spec(spec, action_id, target.name, name)

        def declare(made_of: RollSpec | None) -> bool:
            # What is declared is what is rolled: the maneuvers on the lane move the
            # save the GM's app will ask for, so it must see the shifted spec.
            declared = made_of if made_of is not None else aimed
            declaration = AttackDeclaration(action_id, target.ref, target.name, declared, name)
            if bridge.send_action_request(KIND_ATTACK, action_id, declaration.to_dict()):
                return True
            notify_window(
                self.window(),
                "Could not reach the GM — the attack was not aimed.",
                theme.color("tint.worse"),
            )
            return False

        self.perform_roll(aimed, before=declare)

    def settle_recovery(self, roll: object) -> None:
        """A roll of this seat's is back: if it was a recovery check and it made it,
        say which condition comes off.

        Read off the roll itself — the record the session kept (its spec carries
        ``recovers``), or the local roll's own spec and result — rather than off
        something remembered when the die was thrown. That is what makes a check the
        GM *asked* for work too: the player clicked it on a request card, not on a
        chip, and the request is what carried the condition.
        """
        if not isinstance(roll, dict):
            return
        spec = roll.get("spec")
        if not isinstance(spec, RollSpec):
            spec = RollSpec.from_dict(spec)
        if spec is None or not spec.recovers:
            return
        if "result" in roll:
            result = roll.get("result")
            degree = getattr(result, "degree", None)
        else:
            degree = roll.get("degree")
        if recovery_succeeded(self._data, spec, degree):
            self.recovered.emit((spec.recovers, spec.recovers_parameter or None))

    def load_roll(self, spec: object) -> None:
        """Put what another block named in the chip, without throwing the die — the
        ``load-requested`` topic's handler.

        The same payload as :meth:`perform_roll` and the same tolerance of a bad one;
        the difference is only that the die stays still. It is what a *single* click
        on a stat line asks for, so the sliders can be nudged and the DC set before
        anything is rolled.
        """
        if isinstance(spec, RollSpec):
            self.view.panel.load_spec(spec)

    def add_bonus(self, amount: object) -> None:
        """Put a bonus into the roller's slider — the ``bonus-requested`` topic's handler.

        Extra Effort's "+2 bonus on a single check" (p21) is the one benefit that is a
        number on the *next roll* rather than on the build, and nothing tracks which roll
        that will be — so it goes where the player would otherwise have typed it, on top
        of whatever is already set. Clearing it is the same gesture it always was:
        dragging the slider back.

        Ignores a payload that is not a positive int, for the reason
        :meth:`perform_roll` ignores a bad spec — a mod block publishes here too.
        """
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return
        self.view.panel.add_bonus(amount)

    def post_note(self, text: object) -> None:
        """Write a line in the history — the ``note-requested`` topic's handler.

        Where it goes is the same question the block already answers for rolls: at
        a table it goes to the session, so the note lands in everyone's shared log
        (and the GM's window); off the air it goes in the private history beside
        one's own rolls.

        Ignores a payload that isn't a non-empty string, for the reason
        :meth:`perform_roll` ignores a bad spec — a mod block publishes here too.
        """
        if not isinstance(text, str) or not text:
            return
        bridge = live_session()
        if bridge is None or not bridge.post_note(text):
            self.view.add_local_note(text)

    def request_roll(self, spec: object) -> None:
        """Ask the table to roll something — the Request row's handler.

        Where it goes is the question :meth:`post_note` already answers, and the
        same answer: at a table it goes to the session, so the request lands in
        everyone's shared log with a button on it; off the air it goes in the
        private history, where the button still rolls it here.

        Nothing is rolled from this side. The asker's own card carries the same
        button as everyone else's — you are asking yourself too — and it is theirs
        to press or ignore.
        """
        if not isinstance(spec, RollSpec):
            return
        bridge = live_session()
        if bridge is None or not bridge.prompt_roll(spec.to_dict()):
            self.view.add_local_request(spec)

    def start_action(self, kind: str) -> ImpressDialog | None:
        """Run an action picked off the roller's Actions popup: Impress, an Attack, an
        Area (see :meth:`start_attack` and :meth:`start_area_tile` for those two).

        At a table the dialog only *declares* the impression: it goes to the GM, who
        sets the modifiers and rolls (see :mod:`mm_companion.ui.impress_gm`). A player
        only gets the dialog when both they and the GM want them to choose
        (:func:`~mm_companion.ui.action_ui.sets_up_own_actions`); otherwise the Impress
        goes to the GM **bare**, and the GM chooses. Off the air there is nobody to send
        it to, so the check is rolled here with the modifiers the dialog grew for it, and
        its card offers the resistance as a button — the "request only" way of running it.

        **Not modal**, for the reason the area dialog is not: targets are dragged in off
        the Scene block on this very window.
        """
        if kind == KIND_ATTACK:
            self.start_attack()
            return None
        if kind == KIND_AREA_ATTACK:
            self.start_area_tile()
            return None
        if targeted_action(self._data, kind) is not None:
            self.start_targeted(kind)
            return None
        if self.combat.start(kind):
            return None
        if kind != KIND_IMPRESS:
            return None
        bridge = live_session()
        if bridge is not None and not sets_up_own_actions(bridge):
            self._send_bare_impress(bridge)
            return None
        if self._impress_dialog is not None:
            self._impress_dialog.close()
        scene = bridge.scene() if bridge is not None else None
        own = bridge.own_player_id() if bridge is not None else ""
        dialog = ImpressDialog(
            self._data,
            self._character,
            scene=scene,
            own_player_id=own,
            portraits=_portraits(bridge),
            parent=self.window(),
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(lambda d=dialog, b=bridge: self._send_impress(d, b))
        dialog.finished.connect(lambda _result, d=dialog: self._forget_impress(d))
        self._impress_dialog = dialog
        dialog.show()
        return dialog

    def start_attack(self) -> None:
        """The Attack tile: pick an attack, then — at a table — optionally its target.

        The target menu's first line is :data:`~mm_companion.ui.action_menu.SKIP_TARGET`.
        A target rolls the attack *at* it, as a right-click on the attack line does
        (:meth:`aim_attack`: declared to the GM, who judges the hit). Skipping, or
        having no one to aim at, loads the attack onto the chip instead, and the DC box
        stands for the Defense: set it and click the die. An attack rolled with neither
        is written up with a warning (``dice_roller.NO_DC_WARNING``) — but it still rolls.
        """
        panel = self.view.panel
        spec = panel.choose_attack()
        if spec is None:
            return
        targets = session_targets(live_session())
        if targets:
            captions = [SKIP_TARGET] + [
                f"{t.name} (minion)" if t.minion else t.name for t in targets
            ]
            index = choose_from_menu(self, captions, panel.menu_point())
            if index is None:
                return
            if index > 0:
                self.aim_attack((spec, targets[index - 1]))
                return
        panel.load_spec(spec)

    @staticmethod
    def _targeted_reason(action) -> str | None:
        """Why a targeted action's tile is greyed on a sheet: no table to take it at, or
        nobody (Aid: not both an ally and an opponent) on the Scene."""
        targets = session_targets(live_session())
        if targets is None:
            return NEEDS_A_TABLE
        if action.targeted is not None and action.targeted.ally and len(targets) < 2:
            return NO_ALLY_AND_OPPONENT
        return None if targets else NO_ONE_TO_TARGET

    def start_targeted(self, action_id: str) -> bool:
        """A targeted action's tile — Aid, Feint, Trick (see
        :mod:`~mm_companion.ui.targeted_gm`): against whom off the Scene (Aid: the ally,
        then the opponent), which way, and — for an attack check — which attack. The
        action is declared to the GM (``KIND_TARGETED``) from the roll itself, and the
        check rolled here; the GM accepts it and resolves the rest. Whether it went."""
        action = targeted_action(self._data, action_id)
        bridge = live_session()
        targets = session_targets(bridge)
        if action is None or action.targeted is None or bridge is None or not targets:
            return False
        if self._targeted_reason(action) or self.view.panel.rolling:
            return False
        panel = self.view.panel
        point = panel.menu_point()
        ally = None
        if action.targeted.ally:
            index = choose_from_menu(self, [f"Aid {t.name}" for t in targets], point)
            if index is None:
                return False
            ally = targets[index]
            targets = [t for t in targets if t.ref != ally.ref]
        caption = "… against {}" if ally is not None else "{}"
        index = choose_from_menu(
            self,
            [caption.format(f"{t.name} (minion)" if t.minion else t.name) for t in targets],
            point,
        )
        if index is None:
            return False
        target = targets[index]
        option = ""
        if action.options:
            index = choose_from_menu(self, [o.name for o in action.options], point)
            if index is None:
                return False
            option = action.options[index].id
        attack = None
        if action.targeted.check is None:
            attack = panel.choose_attack()
            if attack is None:
                return False
        check = targeted_check_spec(self._data, action, option, self._character, attack)
        if check is None:
            return False
        name = library.display_name(self._character)
        action_id = new_action_id()
        title = targeted_table_title(
            action.name, name, target.name, ally.name if ally is not None else ""
        )
        spec = targeted_roll_spec(check, action_id, title, name)
        declaration = TargetedDeclaration(
            action_id,
            action.id,
            target.ref,
            target.name,
            spec,
            name,
            option,
            ally.ref if ally is not None else "",
            ally.name if ally is not None else "",
        )

        def declare(_made_of: RollSpec | None) -> bool:
            if bridge.send_action_request(KIND_TARGETED, action_id, declaration.to_dict()):
                return True
            notify_window(
                self.window(),
                f"Could not reach the GM — the {action.name.lower()} was not sent.",
                theme.color("tint.worse"),
            )
            return False

        self.perform_roll(spec, before=declare)
        return True

    def start_area_tile(self) -> AreaDialog | None:
        """The Area tile: the area this sheet's cards offer — straight away when there
        is one, off a menu when there are several — opened as the card's area line
        would (:meth:`start_area`)."""
        choices = area_choices(self._character, self._data)
        if not choices:
            return None
        index = 0
        if len(choices) > 1:
            index = choose_from_menu(
                self, [c.caption for c in choices], self.view.panel.menu_point()
            )
            if index is None:
                return None
        return self.start_area(choices[index].spec.trait_key)

    def _forget_impress(self, dialog: ImpressDialog) -> None:
        if self._impress_dialog is dialog:
            self._impress_dialog = None

    def _send_impress(self, dialog: ImpressDialog, bridge: SessionBridge | None) -> None:
        """The Impress dialog was accepted: declare it to the GM, or roll it here."""
        name = library.display_name(self._character)
        if bridge is not None:
            attempt = dialog.attempt(new_action_id(), source_name=name)
            if attempt is not None:
                self._declare_impress(bridge, attempt.action_id, attempt.to_dict())
            return
        spec = dialog.offline_spec(source_name=name)
        if spec is not None:
            self.view.panel.roll_spec(spec)

    def _send_bare_impress(self, bridge: SessionBridge) -> None:
        """Tell the GM this character impresses, and leave every choice to them."""
        attempt = ImpressAttempt(
            action_id=new_action_id(),
            impression="",
            skill_row="",
            skill_label="",
            skill_modifier=0,
            source_name=library.display_name(self._character),
        )
        self._declare_impress(bridge, attempt.action_id, attempt.to_dict())

    def _declare_impress(self, bridge: SessionBridge, action_id: str, payload: dict) -> None:
        if not bridge.send_action_request(KIND_IMPRESS, action_id, payload):
            notify_window(
                self.window(),
                "Could not reach the GM — the impression was not sent.",
                theme.color("tint.worse"),
            )
        elif not bridge.is_gm:
            notify_window(self.window(), "Impress sent to the GM.", theme.color("tint.better"))

    def start_area(self, power_id: object) -> AreaDialog | None:
        """Use a power on an area — the ``area-requested`` topic's handler.

        At a table the dialog declares it: the attempt goes to the GM, the history gets a
        line saying who is caught, and the attack against the primary (if any) is rolled
        here like any attack, carrying the attempt's id so the GM's window picks its
        total up. A player only gets the dialog when both they and the GM want them to
        choose; otherwise the area goes to the GM **bare** — the power, and nothing else
        — and the GM picks the shape, the primary and who is caught, asking for the
        attack when there is one. Off the air there is nobody to resolve it, so the
        attack is rolled and the history is left a request to dodge (see
        :mod:`mm_companion.ui.area`).

        **Not modal.** The dialog asks for scene cards to be dragged into it, off the
        Scene block on this very window, and a modal dialog swallows the press that
        would start that drag.
        """
        if not isinstance(power_id, str):
            return None
        power = find_area_power(self._character, self._data, power_id)
        if power is None:
            return None
        index = area_key_index(power_id)
        bridge = live_session()
        if bridge is not None and not sets_up_own_actions(bridge):
            self._send_bare_area(bridge, power, index)
            return None
        if self._area_dialog is not None:
            self._area_dialog.close()
        scene = bridge.scene() if bridge is not None else None
        own = bridge.own_player_id() if bridge is not None else ""
        dialog = AreaDialog(
            self._data,
            self._character,
            power,
            effect_index=index,
            scene=scene,
            own_player_id=own,
            portraits=_portraits(bridge),
            parent=self.window(),
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(lambda d=dialog, b=bridge: self._send_area(d, b))
        dialog.finished.connect(lambda _result, d=dialog: self._forget_area(d))
        self._area_dialog = dialog
        dialog.show()
        return dialog

    def _forget_area(self, dialog: AreaDialog) -> None:
        if self._area_dialog is dialog:
            self._area_dialog = None

    def _send_bare_area(self, bridge: SessionBridge, power, effect_index: int = 0) -> None:
        """Tell the GM this character uses *power* on an area, and leave the rest to them."""
        use = power_area_use(power, self._character, self._data, effect_index=effect_index)
        if use is None:
            return
        name = library.display_name(self._character)
        attempt = AreaAttempt(new_action_id(), use, source_name=name)
        if not self._declare_area(bridge, attempt):
            return
        bridge.post_note(f"{name} uses {use.power_name} on an area. The GM sets it up.")
        notify_window(self.window(), "Area attack sent to the GM.", theme.color("tint.better"))

    def _declare_area(self, bridge: SessionBridge, attempt: AreaAttempt) -> bool:
        payload = attempt.to_dict()
        if sanitize_mod_payload(payload) != payload:
            # The server would clip it on the way, and the GM would resolve a different
            # area from the one declared — or none at all.
            notify_window(
                self.window(),
                "This area attack is too large to send — it was not sent.",
                theme.color("tint.worse"),
            )
            return False
        if not bridge.send_action_request(KIND_AREA_ATTACK, attempt.action_id, payload):
            notify_window(
                self.window(),
                "Could not reach the GM — the area attack was not sent.",
                theme.color("tint.worse"),
            )
            return False
        return True

    def _send_area(self, dialog: AreaDialog, bridge: SessionBridge | None) -> None:
        """The area dialog was accepted: roll it here, or declare it to the GM."""
        name = library.display_name(self._character)
        action_id = new_action_id()
        attack = dialog.attack_spec(action_id, source_name=name)
        if bridge is None:
            heal = dialog.heal_spec(source_name=name)
            if heal is not None:
                self.view.panel.roll_spec(heal)
                return
            if attack is not None:
                self.view.panel.roll_spec(attack)
            for request in dialog.offline_requests(source_name=name):
                self.view.add_local_request(request)
            return
        attempt = dialog.attempt(action_id, source_name=name)
        if attempt is None or not self._declare_area(bridge, attempt):
            return
        bridge.post_note(dialog.note_text(source_name=name))
        if attack is not None:
            self.view.panel.roll_spec(attack)
        notify_window(self.window(), "Area attack sent to the GM.", theme.color("tint.better"))

    def sync_session(self) -> None:
        """Re-check whether there is a session, and show the matching history.

        The sheet fans this out (see
        :meth:`~mm_companion.ui.character_sheet.CharacterSheet.sync_session`) at
        both ends of a session, since this block is built with the sheet and is
        never re-shown when a player joins a table.
        """
        self.view.sync_session()

    def set_locked(self, locked: bool) -> None:
        """No-op: rolling is a play action, available in the read-only view too."""

    # -- compact mode --------------------------------------------------------

    def release_roller(self) -> tuple[QWidget, QWidget]:
        """Lend the roller out to a compact window — see
        :meth:`~mm_companion.ui.roller.view.DiceRollerView.release_roller`.

        This block keeps answering the bus while the roller is away: a stat row
        clicked on the sheet behind still loads into the mini window's chip,
        because ``self.view.panel`` is the very same panel wherever it is parented.
        """
        return self.view.release_roller()

    def restore_roller(self) -> None:
        """Take the roller back into the block."""
        self.view.restore_roller()

    def compact_anchor(self) -> QWidget:
        """What the shrink button floats over: the roller view, history and all.

        The *view* and not the panel, deliberately. The panel is the controls,
        and a button parked there would cost it a row of height in every window
        it appears in — including the pinned strip, where height is the scarce
        thing and where a shrink button of the panel's own already lived once.
        Over the view it lands on the history instead, which has pixels to spare.
        """
        return self.view

    def sync_dice_layout(self) -> None:
        """Re-read the roller's layout preference (the Settings page changed it)."""
        self.view.sync_dice_layout()


def _portraits(bridge) -> dict:
    """The scene's thumbnails, decoded, for an action dialog's copy of the board."""
    source = getattr(bridge, "scene_portraits", None)
    if source is None:
        return {}
    from mm_companion.ui.session_portrait import decode_portrait

    decoded = {}
    for ref, raw in source().items():
        pixmap = decode_portrait(raw) if raw else None
        if pixmap is not None:
            decoded[ref] = pixmap
    return decoded
