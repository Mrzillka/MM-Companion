"""The Session settings page: the rules the open session plays by.

Every other page edits the workspace's settings, which are this app's and last until
they are changed. These are the **session's**: published on its shared state, stored
with it on the server, replayed to every seat that joins, and gone when it is. A new
session starts from the book (every switch on), and a GM who runs two tables can play
one by the book and the other without minions' criticals — each remembers its own.

So Save publishes rather than writes, and with no session open there is nothing to
edit: the page says the book's rules apply and greys its switches out. It is only
offered by the GM window, the one window that is a session's GM.

For now the page holds the **minion** rules (:class:`~mm_companion.core.rules.minions.
MinionRules`, PDF p25). Nothing about it is minion-specific beyond its one group, so a
later session setting is another group here and another key in the same entry
(``ui.action_ui.publish_minion_rules`` keeps what it does not set).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.rules import MINION_RULE_KEYS, MinionRules
from mm_companion.ui.action_ui import (
    TABLE_MOD_ID,
    TABLE_SESSION_KEY,
    publish_minion_rules,
    session_minion_rules,
)
from mm_companion.ui.settings.page import SettingsPage
from mm_companion.ui.widgets import muted_style, tinted_style, with_help

#: The page's left-hand caption, and what the GM window opens it by.
SESSION_PAGE_TITLE = "Session"

#: Said under the heading while there is no session to set anything for.
NO_SESSION_TEXT = "No session open: the book's rules apply."
#: And while there is one.
IN_SESSION_TEXT = (
    "These belong to the session that is open now. Another session keeps its own, "
    "and a new one starts from the book."
)

MINIONS_HINT = (
    "A minion is a minor character the rules make easier to defeat: thugs, "
    "bystanders, a horde (core rules, Minions). Make an NPC one with the Minion "
    "button on its card or its sheet; these switches decide which of the minion "
    "rules this table plays by."
)

#: Each switch's caption and explanation, by its MinionRules key.
RULE_TEXT: dict[str, tuple[str, str]] = {
    "no_added_success": (
        "A minion's natural 20 is no added success",
        "Minions can't score added successes, critical hits included, on a 20. Their "
        "natural 1 still costs them a degree.",
    ),
    "worst_degree": (
        "A minion that fails a save takes the worst outcome",
        "Whatever degree it failed by, a minion takes the last rung of the effect: a "
        "failed Damage save is Incapacitated. Its card's damage row becomes ✓ and ✗.",
    ),
    "stop_at_incapacitated": (
        "A minion's failed saves stop at Incapacitated",
        "No further failure walks a minion on to Dying or Dead: mark it dead by hand "
        "if the attacker wants it. Off, it takes the ladder like anyone.",
    ),
    "crit_auto_fails": (
        "A critical hit on a minion lands its worst, with no save",
        "Answering the save of a critical hit as a minion offers the effect's worst "
        "outcome instead of a roll, and an area's critical on a minion primary lands "
        "it unrolled.",
    ),
    "routine_attacks": (
        "Attacks on minions may be routine (take 10)",
        "An area's attack against a minion primary counts as a hit when 10 plus the "
        "attack would hit, whatever was rolled. Heroes can take 10 on any roll with "
        "the roller's Routine box either way.",
    ),
    "routine_impress": (
        "Impressing minions is a routine check at +5",
        "A hero's Impress against minions is not rolled for them: each minion resists "
        "10 + the check + 5. Against a mixed group the others still face the roll.",
    ),
    "show_players": (
        "Show players which creatures are minions",
        "A minion's card on the Scene says so on every seat. Off, only you see it.",
    ),
}


class SessionPage(SettingsPage):
    """The open session's own rules, published to it on Save.

    Built bound to a session bridge (the GM window's); without one — a test, or a
    window that is no session's GM — it behaves as if no session were open.
    """

    title = SESSION_PAGE_TITLE

    def __init__(self, parent: QWidget | None = None, *, bridge=None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._checks: dict[str, QCheckBox] = {}
        self._saved = MinionRules()

        column = QVBoxLayout(self)
        self._state = QLabel()
        self._state.setWordWrap(True)
        column.addWidget(self._state)
        column.addWidget(self._build_minions())
        buttons = QHBoxLayout()
        self._save_button = QPushButton("Save")
        self._save_button.setToolTip("Publish these to the session that is open now")
        self._save_button.clicked.connect(self.save)
        buttons.addWidget(self._save_button)
        buttons.addStretch()
        column.addLayout(buttons)
        self._status = QLabel()
        self._status.setWordWrap(True)
        column.addWidget(self._status)
        column.addStretch()

        self._load()
        if bridge is not None:
            # The session's state can move under an open page — a rejoin brings
            # its welcome, a second GM seat saves — and so can whether there is one.
            bridge.modStateChanged.connect(self._on_mod_state)
            for signal in ("connected", "disconnected", "started", "stopped"):
                getattr(bridge, signal).connect(self._on_session_changed)

    def _build_minions(self) -> QWidget:
        self._minions_box = QGroupBox("Minions")
        column = QVBoxLayout(self._minions_box)
        heading = QLabel("The minion rules this session plays by:")
        column.addWidget(with_help(heading, MINIONS_HINT))
        for key in MINION_RULE_KEYS:
            label, hint = RULE_TEXT.get(key, (key, ""))
            check = QCheckBox(label)
            check.toggled.connect(self._on_edited)
            self._checks[key] = check
            column.addWidget(with_help(check, hint) if hint else check)
        return self._minions_box

    # -- state ---------------------------------------------------------------

    def in_session(self) -> bool:
        """Whether there is a session open to set anything for."""
        return bool(self._bridge is not None and getattr(self._bridge, "in_session", False))

    def rules(self) -> MinionRules:
        """What the switches currently say."""
        return MinionRules(**{key: check.isChecked() for key, check in self._checks.items()})

    def check(self, key: str) -> QCheckBox:
        """One switch, by its :class:`MinionRules` key (for the tests)."""
        return self._checks[key]

    def _load(self) -> None:
        """Show what the open session plays by — the book's, with none open."""
        live = self.in_session()
        self._saved = session_minion_rules(self._bridge) if live else MinionRules()
        values = self._saved.to_dict()
        for key, check in self._checks.items():
            check.blockSignals(True)
            check.setChecked(bool(values.get(key, True)))
            check.blockSignals(False)
        self._minions_box.setEnabled(live)
        self._save_button.setEnabled(live)
        self._state.setText(IN_SESSION_TEXT if live else NO_SESSION_TEXT)
        self._state.setStyleSheet(muted_style(italic=True))

    def _on_mod_state(self, mod_id: str, key: str, _payload: object) -> None:
        if mod_id == TABLE_MOD_ID and key == TABLE_SESSION_KEY and not self.is_dirty():
            self._load()

    def _on_session_changed(self, *_args: object) -> None:
        # A page with edits keeps them while the session merely blips; one that
        # has none simply follows the table it is now looking at.
        if not self.is_dirty() or not self.in_session():
            self._load()

    def _on_edited(self, *_args: object) -> None:
        self._set_status("")

    # -- the page contract -----------------------------------------------------

    def is_dirty(self) -> bool:
        return self.in_session() and self.rules() != self._saved

    def save(self) -> None:
        """Publish the switches to the open session. Nothing is written locally."""
        if not self.in_session():
            return
        rules = self.rules()
        if publish_minion_rules(self._bridge, rules):
            self._saved = rules
            self._set_status("Saved for this session.")
        else:
            self._set_status("The session did not take these — try again.", bad=True)

    def _set_status(self, text: str, *, bad: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(tinted_style("tint.worse") if bad else muted_style(italic=True))
