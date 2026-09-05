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

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import RollSpec, localize_spec, requested_roll_choices
from mm_companion.ui.dice_roller import DiceRollerView
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.session_bridge import live_session


class DiceSection(QGroupBox):
    """A d20 roller as a sheet block (see the module docstring)."""

    #: The roll history grows into the block, so the roller takes the height it is
    #: given (see :meth:`~mm_companion.ui.block_frame._InnerScroll.set_section`).
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

        self.view = DiceRollerView()
        # Every spec that reaches the roller — from the bus, or from a follow-up chip
        # on a history card — passes through here on its way in.
        self.view.panel.set_localizer(self._localize)
        # And the traits the Request row may ask the table for. Character-free on
        # purpose: a request is answered on somebody else's sheet.
        self.view.panel.set_roll_choices(requested_roll_choices(self._data))
        self.view.panel.rollRequested.connect(self.request_roll)
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
