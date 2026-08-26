"""The flow of scene cards, and the drop target underneath it.

One widget on both screens for the reason :mod:`~mm_companion.ui.scene_card` is
one card: the board a player watches and the board a GM drives show the same
thing in the same order, and only the gestures differ. *gm* turns the drops and
the drags on.

**One MIME for two gestures.** Dragging an NPC card into the scene and dragging a
scene card along it are the same question — *put this reference at this index* —
so they carry the same :data:`~mm_companion.ui.scene_card.SCENE_MIME` payload and
land in the same handler. A ref the board already holds moves; one it does not
holds is an addition. Two formats would have meant two nearly-identical drop
paths that could drift apart, and no gain: the board has to look the dropped ref
up either way.

The drop *index* is computed against the rendered order, which is the same
``rolled first, then manual`` order the NPC block sorts by. What the owner does
with that index — and what it costs an entry's initiative to be dragged out of
the rolled zone — is the owner's business, not this widget's: it emits
:attr:`dropped` and lets the GM window apply the rule it already spells out for
its own cards.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mm_companion.core.data_loader import GameData
from mm_companion.ui import theme
from mm_companion.ui.card_drop import CardDropFlow
from mm_companion.ui.scene_card import SceneCard, entry_ref, order_scene
from mm_companion.ui.session_portrait import decode_portrait
from mm_companion.ui.widgets import muted_style, preserved_scroll

#: What an empty board says. Different on the two screens, because "nothing is
#: happening" and "you have not put anything here" are different facts and only
#: one of them is actionable.
NO_SCENE_GM = "No scene yet — click the 👁 on a card, or drag one here."
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
        self._placeholder_text = NO_SCENE_GM if gm else NOT_IN_SESSION

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xs")))

        self._empty = QLabel(self._placeholder_text)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(muted_style(italic=True))
        layout.addWidget(self._empty)

        # A drop target on both screens, and inert on a player's: the flow refuses
        # a payload nobody there can start. Cheaper than two widgets, and it is the
        # same board either way.
        self._flow_host = CardDropFlow("sceneBoard")
        self._flow = self._flow_host.flow
        self._flow_host.setAcceptDrops(gm)
        if gm:
            self._flow_host.dropped.connect(self.dropped)
        layout.addWidget(self._flow_host)

    # -- what the board is showing -----------------------------------------

    def set_scene(self, entries: list[dict]) -> None:
        """Show *entries*, rebuilt in the order :func:`order_scene` gives them."""
        self._entries = [dict(entry) for entry in entries if entry_ref(entry)]
        live = {entry_ref(entry) for entry in self._entries}
        self._manual = [ref for ref in self._manual if ref in live]
        self._manual += [ref for ref in live if ref not in self._manual]
        self._portraits = {ref: p for ref, p in self._portraits.items() if ref in live}
        self._rebuild()

    def set_manual_order(self, refs: list[str]) -> None:
        """Tell the board the GM's arrangement of the un-rolled entries."""
        self._manual = list(refs)
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
        self._empty.setText(text)

    def ordered_refs(self) -> list[str]:
        """The refs in rendered order — what the board actually reads top to bottom."""
        return [entry_ref(entry) for entry in order_scene(self._entries, self._manual)]

    def card(self, ref: str) -> SceneCard | None:
        """The card showing *ref*, or ``None``."""
        return self._cards.get(ref)

    def _rebuild(self) -> None:
        with preserved_scroll(self):
            self._flow_host.clear()
            self._cards = {}
            for entry in order_scene(self._entries, self._manual):
                ref = entry_ref(entry)
                card = SceneCard(
                    entry,
                    self._data,
                    gm=self._gm,
                    portrait=self._portraits.get(ref),  # type: ignore[arg-type]
                )
                if self._gm:
                    card.removeRequested.connect(self.removeRequested)
                    card.initiativeCleared.connect(self.initiativeCleared)
                self._cards[ref] = card
                self._flow_host.add_card(card)
            self._empty.setVisible(not self._entries)
            self._flow_host.setVisible(bool(self._entries))

    def set_locked(self, locked: bool) -> None:  # noqa: ARG002 - part of the Block protocol
        """A no-op: the board is a readout, so there is nothing to lock.

        Stated rather than omitted for the reason the Dice block states it — the
        block protocol asks for it, and a missing one is indistinguishable from an
        oversight.
        """
        return
