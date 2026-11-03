"""The GM window's bookkeeping: the entries it keeps per NPC and per Scene slot,
and the small helpers that name and key them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
)

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.rules import (
    PinRef,
    RollSpec,
    parse_pins,
)
from mm_companion.core.session.protocol import (
    DISPOSITION_ENEMY,
    DISPOSITION_PLAYER,
    SCENE_DISPOSITIONS,
)
from mm_companion.ui.impress import (
    PARTY_NPC,
    ImpressParty,
)
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_group import GROUP_KIND
from mm_companion.ui.roll_history import AnswerContext

#: What the listening socket binds to. Every interface, so a player on the LAN
#: reaches it whichever adapter they come in on; a test overrides it to loopback.
BIND_ADDRESS = "0.0.0.0"


NO_PLAYERS = "Nobody has joined yet — send your players the join code (Session ▸ Copy join code)."


NO_NPCS = "No NPCs in this session yet — create one, or add one you have already written."


#: The two captions of the one collapse-all button. Which one it wears says what
#: clicking it will do *and* what the board currently looks like.
COLLAPSE_ALL = "Collapse all"


EXPAND_ALL = "Expand all"


#: How an entry's source is written in the GM's private ref → source map. A kind
#: and an identity, because the two kinds are told apart nowhere else: what a ref
#: points at is a file name or a player id, and those are not distinguishable by
#: looking at them.
SCENE_NPC = "npc"


SCENE_PLAYER = "player"


#: The history's answer menu, when there is nobody on the board to answer with.
NO_NPCS_TO_ANSWER = "No NPCs on the board"


#: Its last entry — the roll with nobody's number, which a right-click also gives.
AS_WRITTEN_CAPTION = "As written (nobody's number) — or right-click the button"


#: Marks a creature that already answered this card.
ANSWERED_MARK = "✓"


#: Marks a trained-only skill the creature has no training in. It still rolls.
UNTRAINED_MARK = "⚠"


#: Marks a minion a critical hit lands on with no save (PDF p197).
CRITICAL_MARK = "💥 Critical:"


@dataclass
class _Answering:
    """A roll an NPC is answering off the history, until it comes back."""

    #: The spec as the card offered it — the ladder it carries is what is applied.
    spec: RollSpec
    party: ImpressParty
    where: AnswerContext


@dataclass
class _Recovering:
    """An NPC's recovery check, rolled and not back yet."""

    #: The NPC's file name.
    source: str
    #: The spec as rolled — its label is what the result is matched on, and its
    #: ``recovers`` what a made check sheds.
    spec: RollSpec


@dataclass
class _NpcEntry:
    """One NPC in the session's cast, with its loaded model and runtime state.

    Keyed by file name. The model comes from disk (so a saved condition edit is
    reflected on the next refresh); ``initiative`` is transient — rolled at the
    table, not part of the character — so it lives here rather than in the file.
    """

    path: Path
    summary: library.CharacterSummary
    character: Character
    initiative: int | None = None
    card: NPCCard | None = None
    #: Whether this card shows its short form. Held here as well as in settings
    #: because :meth:`GMWindow._refresh_npcs` destroys and rebuilds every card —
    #: anything kept on the widget is lost the first time an initiative is rolled.
    collapsed: bool = False


def _is_player_ref(ref: str) -> bool:
    """Whether a dragged reference is a player's card — the only thing the Players
    block will take back, since a cancelled drag is all it accepts one for."""
    return ref.partition(":")[0] == SCENE_PLAYER


def _is_npc_ref(ref: str) -> bool:
    """Whether a dragged reference is an NPC's card — the only thing the cast can
    be reordered by. A player dragged here is not a thing that can be done, and a
    scene card carries an opaque ref that names no creature this grid holds."""
    return ref.partition(":")[0] in (SCENE_NPC, GROUP_KIND)


def _default_disposition(kind: str) -> str:
    """What a creature is taken to be until the GM says otherwise.

    A seat is a *player* and never anything else. An NPC starts an **enemy**,
    which is the safe way round rather than the tidy one: a board is mostly things
    to fight, so the default is the common case, and the mistake it can make is an
    ally drawn as a threat rather than a threat drawn as an ally.
    """
    return DISPOSITION_PLAYER if kind == SCENE_PLAYER else DISPOSITION_ENEMY


@dataclass
class _SceneEntry:
    """One place on the shared board, and what the GM knows it to be.

    The public half of this — a name, an initiative, some conditions — is derived
    fresh on every push, because it lives on the NPC's model or in the player's
    latest snapshot and would be a lie the moment either changed. What is held
    here is only what *cannot* be derived: which creature this place belongs to.

    ``ref`` is minted here and is deliberately opaque. It is the only thing about
    an entry that reaches a player, so it must say nothing: an NPC's file name can
    be a spoiler outright, and a scene is exactly where a GM would notice that too
    late.
    """

    ref: str
    kind: str  # SCENE_NPC | SCENE_PLAYER
    source: str  # the NPC's file name, or the player's public id
    #: What this creature is to the table. The one *public* field held here rather
    #: than derived, because it is the GM's judgement and there is nowhere else it
    #: could be read from — an NPC's model has no opinion about whose side it is on.
    disposition: str = DISPOSITION_ENEMY
    #: In the turn order without an initiative: the GM dragged this creature in
    #: among the rolled ones, which places it there without giving it a number. It
    #: keeps the place the drop gave it until it rolls, is cleared, or is dragged
    #: back out past the last numbered entry.
    placed: bool = False

    def wire_source(self) -> str:
        """How the private map records it: ``"npc:<file>"`` / ``"player:<id>"``."""
        return f"{self.kind}:{self.source}"

    @classmethod
    def from_wire_source(cls, ref: str, raw: str, disposition: str = "") -> _SceneEntry | None:
        """Rebuild from the map a welcome handed back, or ``None`` if unreadable."""
        kind, _, source = str(raw).partition(":")
        if kind not in (SCENE_NPC, SCENE_PLAYER) or not source:
            return None
        if disposition not in SCENE_DISPOSITIONS:
            disposition = DISPOSITION_PLAYER if kind == SCENE_PLAYER else DISPOSITION_ENEMY
        return cls(ref=ref, kind=kind, source=source, disposition=disposition)


def _next_copy_name(source_name: str, existing: set[str]) -> str:
    """The next free ``"<base>-<n>"`` for a copy of *source_name*.

    A trailing ``-<digits>`` on the source is stripped to find the base, so
    copying "Goon" gives "Goon-2" and copying "Goon-2" gives the next free
    "Goon-N" rather than "Goon-2-2". ``n`` starts at 2 and steps up until the
    name is free among *existing*.
    """
    match = re.match(r"^(.*?)-(\d+)$", source_name)
    base = match.group(1) if match else source_name
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _npc_key(file_name: str) -> str:
    """A pin-store key for an NPC. Its file name is its identity everywhere else."""
    return f"npc:{file_name}"


def _number_namesakes(parties: list[ImpressParty]) -> None:
    """Number the NPCs on the board that share a name: ``Thug (1)``, ``Thug (2)``.

    An action's table card has a row per creature, keyed by the name a line names, and
    its notices say who did what by name. Two identical minions would share one row
    and one line, the last roll's outcome standing for both. A player keeps their
    name, since their own app recognises a request meant for them by it.
    """
    counts: dict[str, int] = {}
    for party in parties:
        counts[party.name] = counts.get(party.name, 0) + 1
    seen: dict[str, int] = {}
    for party in parties:
        if party.kind == PARTY_NPC and counts[party.name] > 1:
            seen[party.name] = seen.get(party.name, 0) + 1
            party.name = f"{party.name} ({seen[party.name]})"


def _player_key(player_id: str) -> str:
    """A pin-store key for a seat.

    Keyed by the *public* player id, which is what survives a reconnect
    (:meth:`~mm_companion.core.session.model.SessionState.player_by_id_if_free`).
    A player who ends up in a fresh seat starts from the defaults again — pins are
    a GM's private scratch note, and the honest alternative is session state on
    the server for something no player should see.
    """
    return f"player:{player_id}"


def _load_pins() -> dict[str, list[PinRef]]:
    """Every card's saved pin strip, from settings."""
    raw = storage.load_settings().get("gm_pins", {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): parse_pins(value) for key, value in raw.items()}


def _wrapped(text: str) -> QLabel:
    """A word-wrapping, selectable label — advice is prose, and gets read."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label
