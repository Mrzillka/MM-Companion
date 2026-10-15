"""The Dice block: the d20 roller, on the sheet rather than in its own window.

Rolling dice used to mean a separate top-level window opened from ``Tools ▸ Dice
Roller…``. It is a sheet block now, which means it gets everything the other
blocks get for free — drag to reorder, pop out into its own window, hide and
reopen from the View menu — and, most of the point, it can be **parked in the
pinned strip**, the one region that does not scroll with the page. Its descriptor
sets ``default_pinned``, so a fresh sheet opens with the die already beside the
page instead of scrolling away under it.

The block is a thin wrapper: all of the behaviour is
:class:`~mm_companion.ui.dice_roller.DiceRollerView` (the roll column plus the
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    AreaAttempt,
    ImpressAttempt,
    RollSpec,
    find_area_power,
    localize_spec,
    power_area_use,
    requested_roll_choices,
)
from mm_companion.core.session.protocol import sanitize_mod_payload
from mm_companion.ui import theme
from mm_companion.ui.action_ui import sets_up_own_actions
from mm_companion.ui.area import KIND_AREA_ATTACK, AreaDialog
from mm_companion.ui.dice_roller import DiceRollerView
from mm_companion.ui.impress import IMPRESS_ACTION, KIND_IMPRESS, ImpressDialog, new_action_id
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
        # Actions against other characters, when the ruleset has any.
        if data.impress.available:
            self.view.panel.set_actions([IMPRESS_ACTION])
        self.view.panel.actionRequested.connect(self.start_action)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

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

    def perform_roll(self, spec: object) -> None:
        """Roll what another block asked for — the ``roll-requested`` topic's handler.

        Anything that isn't a :class:`~mm_companion.core.rules.RollSpec` is ignored
        rather than raising: the payload comes off a bus a mod block can publish on
        too, and a bad payload should cost a roll, not the sheet.
        """
        if isinstance(spec, RollSpec):
            self.view.panel.roll_spec(spec)

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
        """Run one of the Action row's actions — the Impress button's handler.

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
            self._data, self._character, scene=scene, own_player_id=own, parent=self.window()
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(lambda d=dialog, b=bridge: self._send_impress(d, b))
        dialog.finished.connect(lambda _result, d=dialog: self._forget_impress(d))
        self._impress_dialog = dialog
        dialog.show()
        return dialog

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
        bridge = live_session()
        if bridge is not None and not sets_up_own_actions(bridge):
            self._send_bare_area(bridge, power)
            return None
        if self._area_dialog is not None:
            self._area_dialog.close()
        scene = bridge.scene() if bridge is not None else None
        own = bridge.own_player_id() if bridge is not None else ""
        dialog = AreaDialog(
            self._data,
            self._character,
            power,
            scene=scene,
            own_player_id=own,
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

    def _send_bare_area(self, bridge: SessionBridge, power) -> None:
        """Tell the GM this character uses *power* on an area, and leave the rest to them."""
        use = power_area_use(power, self._character, self._data)
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
        :meth:`~mm_companion.ui.dice_roller.DiceRollerView.release_roller`.

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
