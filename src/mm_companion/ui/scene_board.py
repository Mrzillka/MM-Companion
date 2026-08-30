"""The flow of scene cards, and the drop target underneath it.

One widget on both screens for the reason :mod:`~mm_companion.ui.scene_card` is
one card: the board a player watches and the board a GM drives show the same
thing in the same order, and only the gestures differ. *gm* turns the drops and
the drags on.

The drop itself belongs to :class:`~mm_companion.ui.card_drop.CardDropFlow`, which
all three boards share — dragging an NPC card *into* the scene and dragging a
scene card *along* it are the same question, so they are the same handler. What
this widget adds is only the pass-through: it re-raises :attr:`dropped` and lets
the GM window decide what an index means, since what it costs an entry's
initiative to be dragged out of the rolled zone is a rule about the *board*, not
about a flow of widgets.

Ordering is :func:`~mm_companion.ui.scene_card.order_scene` — rolled entries
first, then the GM's own arrangement. It is the **only** board that sorts by
initiative: the NPC grid used to as well, and two boards showing one ordering
meant the cast re-arranged itself under the GM's hands every time a mook rolled.
The cast is a cast now, and the turn order is here.

The board is one widget deep on purpose. The drop target fills it and is never
hidden, so the whole block takes a card — see
:meth:`~mm_companion.ui.card_drop.CardDropFlow.set_placeholder` for the bug that
forced that, which was that an empty board could not be given its first entry.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mm_companion.core.data_loader import GameData
from mm_companion.ui import theme
from mm_companion.ui.card_drop import CardDropFlow
from mm_companion.ui.scene_card import SceneCard, entry_ref, order_scene
from mm_companion.ui.session_portrait import decode_portrait
from mm_companion.ui.widgets import rebuilding

#: What an empty board says. Different on the two screens, because "nothing is
#: happening" and "you have not put anything here" are different facts and only
#: one of them is actionable.
NO_SCENE_GM = "No scene yet — drag a card here."
NO_SCENE_PLAYER = "The GM has not set a scene."
NOT_IN_SESSION = "Not in a session."


class SceneBoard(QWidget):
    """Every scene entry as a card, in order, with the GM's drops wired up."""

    #: ``(ref, index)`` — a card was dropped at *index* in the rendered order.
    #: Raised for both kinds of drop; the owner tells them apart by whether it
    #: knows the ref. GM only.
    dropped = Signal(str, int)
    #: ``ref`` — take this entry off the board. GM only.
    removeRequested = Signal(str)
    #: ``ref`` — put this entry back in the un-rolled zone. GM only.
    initiativeCleared = Signal(str)
    #: ``ref`` — roll this entry's initiative. GM only, and never a player's entry.
    initiativeRollRequested = Signal(str)
    #: ``(ref, disposition)`` — what this creature is to the table. GM only.
    dispositionChanged = Signal(str, str)

    def __init__(
        self,
        data: GameData,
        parent: QWidget | None = None,
        *,
        gm: bool = False,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._gm = gm
        #: The scene as last given, in wire order.
        self._entries: list[dict] = []
        #: The GM's own arrangement of the un-rolled entries, by ref.
        self._manual: list[str] = []
        #: Decoded thumbnails by ref. Kept here rather than on the cards because
        #: a card is destroyed and rebuilt on every scene update and a picture is
        #: not re-sent with one.
        self._portraits: dict[str, object] = {}
        self._cards: dict[str, SceneCard] = {}
        #: The reader's own seat, so their entry can say so. Empty on the GM's
        #: board, where it would never match anyway: a GM is not a player on their
        #: own roster, so no scene entry carries their id.
        self._own_id = ""
        self._placeholder_text = NO_SCENE_GM if gm else NOT_IN_SESSION

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xs")))

        # A drop target on both screens, and inert on a player's: the flow refuses
        # a payload nobody there can start. Cheaper than two widgets, and it is the
        # same board either way.
        #
        # It takes every pixel the board is given (``stretch=1``) and is **never
        # hidden**, including while the scene is empty. Both are the same rule:
        # what a GM aims a dragged card at is the Scene *block*, not the thin band
        # its cards happen to occupy — and a board with nothing on it is exactly
        # when they are aiming. The empty-state sentence is the flow's own
        # placeholder, so it sits inside the rectangle that accepts the drop
        # rather than beside it.
        self._flow_host = CardDropFlow("sceneBoard")
        self._flow = self._flow_host.flow
        self._flow_host.setAcceptDrops(gm)
        self._flow_host.set_placeholder(self._placeholder_text)
        if gm:
            self._flow_host.dropped.connect(self.dropped)
        layout.addWidget(self._flow_host, stretch=1)

    # -- what the board is showing -----------------------------------------

    def set_scene(self, entries: list[dict]) -> None:
        """Show *entries*, in the order :func:`order_scene` gives them.

        **A scene identical to the one on screen is not redrawn.** The GM re-sends the
        whole board whenever anything on it could have moved, which includes every
        snapshot every player pushes — so a board that redrew unconditionally cost
        every player at the table a full re-render each time one of them touched a
        spin box. The GM's own side guards its *sending*
        (:meth:`~mm_companion.ui.gm_window.GMWindow._push_scene`); this guards the
        *receiving*, so a chatty GM — or a mod — still costs a player nothing.
        """
        # One card per ref, and the first wins. Refs are unique where they are made,
        # but they arrive over the wire, and a repeated one would now mean the same
        # *card* twice in the flow — a layout Qt refuses to build and does not say so.
        wanted: list[dict] = []
        seen: set[str] = set()
        for entry in entries:
            ref = entry_ref(entry)
            if not ref or ref in seen:
                continue
            seen.add(ref)
            wanted.append(dict(entry))
        if wanted == self._entries:
            return
        self._entries = wanted
        live = {entry_ref(entry) for entry in self._entries}
        self._manual = [ref for ref in self._manual if ref in live]
        self._manual += [ref for ref in live if ref not in self._manual]
        self._portraits = {ref: p for ref, p in self._portraits.items() if ref in live}
        self._rebuild()

    def set_own_player_id(self, player_id: str) -> None:
        """Tell the board whose screen it is, so one card can say "(you)".

        Which seat is the reader's own is fixed when a card is built, so the cards
        cannot be reused across this one — they are dropped and the board drawn again
        from nothing. It costs nothing worth saving: this is answered once, at the join.
        """
        if player_id == self._own_id:
            return
        self._own_id = player_id
        self._cards = {}
        self._flow_host.clear()
        self._rebuild()

    def set_manual_order(self, refs: list[str]) -> None:
        """Tell the board the GM's arrangement of the un-rolled entries.

        Guarded like :meth:`set_scene`, and for a plainer reason: the GM's push sets
        the order and then the scene, so an unguarded one redrew the board twice for
        every change and twice more for every change that turned out not to be one.
        """
        wanted = list(refs)
        if wanted == self._manual:
            return
        self._manual = wanted
        self._rebuild()

    def set_portrait(self, ref: str, portrait: str) -> None:
        """Decode and show one entry's thumbnail; an empty *portrait* clears it.

        Decoded once here rather than on every rebuild: a scene is re-rendered
        whenever anything on it changes, and re-decoding a dozen JPEGs each time a
        GM ticks a condition would make the cheap message the expensive one.
        """
        pixmap = decode_portrait(portrait) if portrait else None
        if pixmap is None:
            self._portraits.pop(ref, None)
        else:
            self._portraits[ref] = pixmap
        card = self._cards.get(ref)
        if card is not None:
            card.set_portrait(pixmap)

    def set_placeholder(self, text: str) -> None:
        """What an empty board says. Shown only while there is nothing on it."""
        self._placeholder_text = text
        self._flow_host.set_placeholder(text)

    def placeholder_text(self) -> str:
        """The sentence an empty board is showing."""
        return self._flow_host.placeholder_text()

    def is_empty(self) -> bool:
        """Whether the board is showing the placeholder rather than cards."""
        return not self._entries

    def ordered_refs(self) -> list[str]:
        """The refs in rendered order — what the board actually reads top to bottom."""
        return [entry_ref(entry) for entry in order_scene(self._entries, self._manual)]

    def card(self, ref: str) -> SceneCard | None:
        """The card showing *ref*, or ``None``."""
        return self._cards.get(ref)

    def _rebuild(self) -> None:
        """Redraw the board, **keeping the card that is already showing each ref**.

        A card is only built for a creature the board has not seen; one that is still
        there is restated in place through :meth:`~mm_companion.ui.scene_card.SceneCard.
        set_entry`, which is what that method was always for. It matters because the
        board is redrawn whenever *anything* on it moves — one condition ticked on one
        mook used to destroy and rebuild every card on every screen at the table,
        each with its own stylesheet, chip flow and initiative badge.

        Reuse also keeps a card's signal connections, so they are made exactly once,
        where a rebuilt card had to be re-wired every time.

        The portrait is deliberately not re-applied to a reused card: it already has
        it, pictures arrive on their own messages, and re-setting it would undo the
        one thing :meth:`set_portrait` is careful about.
        """
        with rebuilding(self):
            ordered = order_scene(self._entries, self._manual)
            kept: dict[str, SceneCard] = {}
            for entry in ordered:
                ref = entry_ref(entry)
                card = self._cards.pop(ref, None)
                if card is None:
                    card = self._make_card(entry, ref)
                else:
                    card.set_entry(entry)
                kept[ref] = card
            # Whatever is left in ``self._cards`` has gone from the scene; handing the
            # flow only the kept ones is what discards them.
            self._cards = kept
            self._flow_host.set_cards([kept[entry_ref(entry)] for entry in ordered])

    def _make_card(self, entry: dict, ref: str) -> SceneCard:
        """One card for a ref the board has not shown before, wired to its signals."""
        card = SceneCard(
            entry,
            self._data,
            gm=self._gm,
            own=bool(self._own_id) and entry.get("player_id") == self._own_id,
            portrait=self._portraits.get(ref),  # type: ignore[arg-type]
        )
        if self._gm:
            card.removeRequested.connect(self.removeRequested)
            card.initiativeCleared.connect(self.initiativeCleared)
            card.initiativeRollRequested.connect(self.initiativeRollRequested)
            card.dispositionChanged.connect(self.dispositionChanged)
        return card

    def set_locked(self, locked: bool) -> None:  # noqa: ARG002 - part of the Block protocol
        """A no-op: the board is a readout, so there is nothing to lock.

        Stated rather than omitted for the reason the Dice block states it — the
        block protocol asks for it, and a missing one is indistinguishable from an
        oversight.
        """
        return
