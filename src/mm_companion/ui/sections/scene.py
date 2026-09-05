"""The Scene block: the GM's board, on a player's sheet.

Like the Dice block and unlike every other block here, this is **not a view over
the character**. It reads nothing off the build, writes nothing to it, and
publishes nothing on the bus — a scene update arriving mid-edit must never mark
the sheet dirty, which is the same bargain a roll makes. What it is a view over is
the session, so it follows :func:`~mm_companion.ui.session_bridge.live_session`
through the one hook the sheet already fans out for that,
:meth:`sync_session`.

It starts **pinned**, beside the roller, for the reason the roller does: the strip
is the one region that does not scroll with the page, and a turn order that has
scrolled away under the sheet is no use in the round it matters. A table that
never plays online can take it off the strip, or hide it, from the View menu.

The whole block is :class:`~mm_companion.ui.scene_board.SceneBoard` with a caption
and a session wire attached — the GM's own Scene block is the same board with the
drops turned on, which is why the board is not defined here.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.ui import theme
from mm_companion.ui.scene_board import NO_SCENE_PLAYER, NOT_IN_SESSION, SceneBoard
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.session_bridge import SessionBridge, live_session

TITLE = "Scene"


class SceneSection(TitledSection):
    """The table's turn order, live from the GM."""

    #: The board is given the block's whole height — its empty-state sentence is
    #: centred in the room, and a turn order that grows mid-round pushes into space
    #: the block already has (see
    #: :meth:`~mm_companion.ui.block_frame._InnerScroll.set_section`).
    fills_height = True

    def __init__(
        self,
        data: GameData,
        character: Character,  # noqa: ARG002 - the block protocol's shape; unused here
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        #: The bridge this block is currently following, so a second
        #: ``sync_session`` for the same session does not connect twice.
        self._bridge: SessionBridge | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*theme.box("group.margins"))
        layout.setSpacing(int(theme.metric("space.xs")))
        # Given the block's whole height rather than sat on top of a stretch: the
        # board's empty-state sentence is centred in the room it is given, and a
        # turn order that grows mid-round should push into space the block already
        # has rather than make the page reflow.
        self.board = SceneBoard(data, gm=False)
        layout.addWidget(self.board, stretch=1)

        self.set_block_title(TITLE)
        self.sync_session()

    # -- the session ------------------------------------------------------

    def sync_session(self) -> None:
        """Follow the session this app is in, or say there isn't one.

        Called by :meth:`~mm_companion.ui.character_sheet.CharacterSheet.sync_session`
        at both ends of a session, and once from the constructor for a block built
        while one is already running.

        It asks :func:`live_session` rather than the bridge's ``joined``, for the
        reason the roller does: ``joined`` follows the *socket*, so during a
        two-second Wi-Fi blip it is False while the session is very much still on,
        and a board that emptied itself for that would be worse than one that went
        briefly stale.
        """
        bridge = live_session()
        if bridge is self._bridge:
            if bridge is None:
                self._show_no_session()
            return
        self._bridge = bridge
        if bridge is None:
            self._show_no_session()
            return
        self.board.set_own_player_id(bridge.own_player_id())
        bridge.sceneChanged.connect(self._on_scene)
        bridge.scenePortrait.connect(self.board.set_portrait)
        # Seeded rather than waited for: a block built after the join would
        # otherwise sit empty until the GM next touched the board, which mid-fight
        # could be the whole fight.
        self.board.set_placeholder(NO_SCENE_PLAYER)
        self._on_scene(bridge.scene())
        for ref, portrait in bridge.scene_portraits().items():
            self.board.set_portrait(ref, portrait)

    def _show_no_session(self) -> None:
        self.board.set_placeholder(NOT_IN_SESSION)
        self.board.set_own_player_id("")
        self.board.set_scene([])

    def _on_scene(self, entries: object) -> None:
        """Show a scene the GM sent. A payload that is not a list is simply none."""
        self.board.set_scene(list(entries) if isinstance(entries, list) else [])

    # -- the block protocol ------------------------------------------------

    def set_locked(self, locked: bool) -> None:  # noqa: ARG002 - protocol shape
        """A no-op: there is nothing here to edit, locked or not.

        Stated rather than left off, like the Dice block's — the protocol asks for
        it, and a missing one reads as an oversight rather than a decision.
        """
        return
