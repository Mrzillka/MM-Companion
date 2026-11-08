"""Running the combat actions off the roller's Actions popup, for whoever is acting.

The attack maneuvers and the self actions (see :mod:`~mm_companion.core.rules.maneuvers`
and :mod:`~mm_companion.core.rules.self_actions`) are the same on every roller; what
differs is **who** takes them. The sheet's Dice block always acts as its own character.
The GM's Rolls block acts as the NPC picked for each action, so its actor changes from
one pick to the next. Everything that depends on who is acting is a
:class:`CombatActor`, and :class:`CombatRunner` does the rest: it starts the lane lines,
lists the actor's attacks for the picker, shifts the roll by the lines, and hands what
they leave behind to the actor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from PySide6.QtWidgets import QWidget

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.components import TONE_HINDRANCE
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    AttackChoice,
    RollSpec,
    action_reason,
    apply_actions,
    attack_choices,
    check_spec,
    lasting_conditions,
    line_fit,
    line_key,
    maneuver_action,
    maneuver_limit,
    recover_choices,
    self_action,
    self_action_changes,
    self_action_note,
    split_line_key,
)
from mm_companion.ui.action_menu import choose_from_menu
from mm_companion.ui.sections.conditions import condition_tone_token

#: The last line of Recover's menu: take the Defense bonus and shed nothing.
RECOVER_DEFENSE_ONLY = "Only catch your breath (the Defense bonus)"


def _no_land(_requests: tuple) -> None:
    """A placeholder :attr:`CombatActor.land` (nothing to put the conditions on)."""


@dataclass
class CombatActor:
    """Who takes the combat actions on one roller, and where what they do lands.

    :attr:`name` is ``""`` for a player's own sheet: its rolls carry no name, as they
    never did. The GM's NPC carries its name (numbered when two share one) in front of
    every label and as the spec's ``actor``, so each history card heads "Thug (GM)".
    :attr:`key` tells two actors apart (the NPC's file name; ``""`` on a sheet).

    :attr:`land` takes the ``condition-requested`` payload (``(id, amount)`` pairs or
    ``ConditionChange`` entries) and puts it on the actor. :attr:`throw` rolls a check
    the action makes as the actor (``load=False``) or loads it onto the chip
    (``load=True``); ``None`` uses the roller plainly.
    """

    character: Character
    name: str = ""
    key: str = ""
    minion: bool = False
    land: Callable[[tuple], None] = field(default=_no_land)
    throw: Callable[[RollSpec, bool], None] | None = None


class CombatRunner:
    """The combat actions on one roller panel (see the module docstring).

    *note* writes a line in the history (the host's own road: the table's log, or the
    private history). *parent* is the widget the choosers' menus belong to. The runner
    wires itself into *panel*: the attack picker's list, the lines' shift, and the
    ``linesRolled`` cue.
    """

    def __init__(
        self,
        data: GameData,
        panel,
        *,
        note: Callable[[str], None],
        parent: QWidget,
        actor: CombatActor | None = None,
    ) -> None:
        self._data = data
        self._panel = panel
        self._note = note
        self._parent = parent
        #: Who acts now. ``None`` on the GM's roller until an NPC is picked.
        self.actor: CombatActor | None = actor
        panel.set_attack_choices(self.attack_choices)
        panel.set_line_shift(self.shift)
        panel.linesRolled.connect(self.land_lasting)

    # -- who acts ------------------------------------------------------------

    def act_as(self, actor: CombatActor | None) -> None:
        """Have *actor* take the next actions. A different one drops the lines on the
        lane: they were the last actor's, and their limits read that one's numbers."""
        before = self.actor
        if before is not None and (actor is None or actor.key != before.key):
            self.drop_lines()
        self.actor = actor

    def drop_lines(self) -> None:
        """Take every action line off the lane."""
        for line in list(self._panel.action_lines()):
            self._panel.remove_action_line(line.key)

    def own(self, spec: RollSpec) -> RollSpec:
        """*spec* as the actor rolls it: its name in front of the label, and as the
        spec's ``actor``. Unchanged on a player's own sheet."""
        actor = self.actor
        if actor is None or not actor.name:
            return spec
        prefix = f"{actor.name}: "
        label = spec.label if spec.label.startswith(prefix) else f"{prefix}{spec.label}"
        return replace(spec, label=label, actor=actor.name)

    # -- what the panel asks for -------------------------------------------------

    def attack_choices(self) -> list[AttackChoice]:
        """The actor's attacks for the picker, each as the actor rolls it."""
        actor = self.actor
        if actor is None:
            return []
        return [
            replace(choice, spec=self.own(choice.spec))
            for choice in attack_choices(actor.character, self._data)
        ]

    def shift(self, spec: RollSpec, lines) -> RollSpec:
        """What the lines on the lane do to *spec*, for the actor (a Slam reads its
        speed, and its own save is the actor's)."""
        actor = self.actor
        shifted = apply_actions(
            spec, lines, self._data, actor.character if actor is not None else None
        )
        if shifted.then is not None:
            shifted = replace(shifted, then=self.own(shifted.then))
        return shifted

    def land_lasting(self, lines: object) -> None:
        """A roll with action lines on it settled: put on the actor what they leave."""
        if not isinstance(lines, tuple) or self.actor is None:
            return
        lasting = lasting_conditions(lines, self._data)
        if lasting:
            self.actor.land(tuple(lasting))

    def ensure_attack(self) -> bool:
        """Make sure one of the actor's attacks is on the chip, asking with the picker
        when none is (or the one there is somebody else's). ``False``: called off."""
        actor = self.actor
        loaded = self._panel.loaded_attack()
        if loaded is not None and (not actor or not actor.name or loaded.actor == actor.name):
            return True
        spec = self._panel.choose_attack()
        if spec is None:
            return False
        self._load(spec)
        return True

    def choose_attack(self) -> RollSpec | None:
        """One of the actor's attacks off the picker (loads nothing)."""
        return self._panel.choose_attack()

    def load(self, spec: RollSpec) -> None:
        """Put *spec* on the chip as the actor's (a minion's 20 no critical)."""
        self._load(spec)

    def _load(self, spec: RollSpec) -> None:
        minion = self.actor.minion if self.actor is not None else False
        self._panel.load_spec(spec, minion=minion)

    def _throw(self, spec: RollSpec, *, load: bool) -> None:
        spec = self.own(spec)
        actor = self.actor
        if actor is not None and actor.throw is not None:
            actor.throw(spec, load)
        elif load:
            # Built off the actor already: a second pass would add its number again.
            self._panel.load_spec(spec, localize=False)
        else:
            self._panel.roll_spec(spec)

    # -- the tiles ------------------------------------------------------------

    def start(self, kind: str) -> bool:
        """Run *kind* if it is an attack maneuver or a self action; whether it was."""
        if maneuver_action(self._data, kind) is not None:
            self.start_maneuver(kind)
            return True
        if self_action(self._data, kind) is not None:
            self.start_self_action(kind)
            return True
        return False

    def start_maneuver(self, action_id: str):
        """An attack maneuver's tile: a line on the lane, its slider over ``0..X_max``
        and starting at the maximum, for the attack loaded (the picker asks for one when
        none is). The roll it rides on is shifted by it, and what it leaves behind lands
        on the actor once that die has settled (:meth:`land_lasting`). Returns the line,
        or ``None`` when it was called off."""
        action = maneuver_action(self._data, action_id)
        if action is None or self.actor is None or not self.ensure_attack():
            return None
        panel = self._panel

        def limit(spec: RollSpec | None) -> tuple[int, str]:
            actor = self.actor
            others = {key: value for key, value in panel.active_lines() if key != action_id}
            found = maneuver_limit(
                self._data,
                action_id,
                spec,
                actor.character if actor is not None else None,
                others,
            )
            return found.maximum, found.reason

        lasting = self._data.condition_catalog().get(action.lasting) if action.lasting else None
        tone = condition_tone_token(lasting) if lasting is not None else "tint.better"
        return panel.start_action_line(
            action_id,
            action.name,
            limit=limit,
            needs_attack=True,
            tooltip=action.tooltip,
            tone=tone,
        )

    def start_self_action(self, action_id: str):
        """A self action's tile (Aim, Charge, Defend, Recover, Drop Prone, Stand, Finishing
        Attack, Catch — see :mod:`~mm_companion.core.rules.self_actions`).

        One with options asks which first, on a plain menu (Charge or Slam; Stand by a
        check or slowly). Then it rides on the next roll as a lane line, puts conditions
        on the actor at once (with a line in the history), or loads its check onto the
        chip. Returns the line it started, if any; ``None`` when it was called off or did
        its work at once.
        """
        action = self_action(self._data, action_id)
        actor = self.actor
        if action is None or actor is None:
            return None
        if action_reason(self._data, action, actor.character, gm=bool(actor.name)):
            return None
        panel = self._panel
        option = None
        if action.options:
            index = choose_from_menu(
                self._parent, [o.name for o in action.options], panel.menu_point()
            )
            if index is None:
                return None
            option = action.options[index]
        option_id = option.id if option is not None else ""
        title = option.name if option is not None else action.name
        effect = action.effect_for(option_id)
        if effect.line:
            return self._start_self_line(action, option_id, title, effect)
        changes = self_action_changes(effect, self._data)
        recovery = None
        if action.recovery is not None:
            choices = recover_choices(actor.character, self._data, action)
            if choices:
                captions = [c.caption for c in choices] + [RECOVER_DEFENSE_ONLY]
                index = choose_from_menu(self._parent, captions, panel.menu_point())
                if index is None:
                    return None
                if index < len(choices):
                    recovery = choices[index]
                    changes += recovery.changes
        if changes:
            actor.land(changes)
            name = actor.name or library.display_name(actor.character)
            self._note(self_action_note(self._data, name, title, changes))
        if recovery is not None and recovery.spec is not None:
            self._throw(recovery.spec, load=False)
        check = check_spec(self._data, actor.character, action, effect)
        if check is not None:
            self._throw(check, load=True)
        return None

    def _start_self_line(self, action, option_id: str, title: str, effect):
        """Put a self action that rides on the next roll on the lane, replacing the same
        action taken another way (a Slam replaces a plain Charge)."""
        if action.needs_attack and not self.ensure_attack():
            return None
        panel = self._panel
        key = line_key(action.id, option_id)
        for line in panel.action_lines():
            if line.key != key and split_line_key(line.key)[0] == action.id:
                panel.remove_action_line(line.key)
        tone = "tint.worse" if effect.tone == TONE_HINDRANCE else "tint.better"
        return panel.start_action_line(
            key,
            title,
            fixed=effect.line,
            needs_attack=action.needs_attack,
            tooltip=action.tooltip,
            tone=tone,
            fits=lambda spec: line_fit(self._data, key, spec),
        )
