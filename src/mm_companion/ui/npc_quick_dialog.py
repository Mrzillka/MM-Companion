"""The Quick NPC wizard: five numbers and a name, and there is a creature.

The GM's side of :func:`mm_companion.core.npc.quick_npc`. It asks for the only
things a mook is ever actually consulted about — what it hits with, how hard, and
how hard it is to hit and to hurt — and hands back a
:class:`~mm_companion.core.character.Character` that opens in the ordinary NPC
sheet. Everything past that is edited there; this is a starting point, not a
second kind of character.

Deliberately one page rather than a :class:`QWizard`: six fields do not need
paging, and a wizard the GM has to click Next through three times is slower than
the blank sheet it exists to replace.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.npc_names import random_npc_name
from mm_companion.ui.widgets import make_spin_box

#: The range the four stat boxes accept. Wide enough for anything from a rat to a
#: cosmic horror; an NPC is not held to a power level, so nothing narrower is right.
STAT_MIN, STAT_MAX = 0, 40

#: What the boxes start at — a competent PL 6-ish minion, the commonest thing a GM
#: reaches for. Every one of them is a plain default, not a rule.
DEFAULT_ATTACK = 6
DEFAULT_EFFECT = 6
DEFAULT_DEFENCE = 6
DEFAULT_TOUGHNESS = 6

HELP_TEXT = (
    "A creature from the numbers that get used: what it hits with, how hard it "
    "hits, and how hard it is to hit and to hurt. It comes out with a Damage and "
    "an Affliction power at that rank — open it afterwards to change anything."
)

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"

#: The same portrait box the NPC card shows, so the wizard previews what the roster
#: will. Kept here rather than imported from ``npc_card`` to avoid a UI module
#: depending on a sibling purely for a number.
PORTRAIT_SIZE = 96


@dataclass(frozen=True)
class QuickNPC:
    """What the wizard collected — the arguments of :func:`~mm_companion.core.npc.quick_npc`."""

    name: str
    attack: int
    effect: int
    defence: int
    toughness: int
    image_path: str | None


class QuickNPCDialog(QDialog):
    """Collect a quick NPC's numbers; :meth:`value` returns them after ``exec()``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick NPC")
        self._image_path: str | None = None

        layout = QVBoxLayout(self)
        help_label = QLabel(HELP_TEXT)
        help_label.setWordWrap(True)
        help_label.setEnabled(False)
        layout.addWidget(help_label)

        form = QFormLayout()
        form.addRow("Name:", self._build_name_row())
        form.addRow("Portrait:", self._build_portrait_row())

        self._attack = make_spin_box(STAT_MIN, STAT_MAX, value=DEFAULT_ATTACK)
        self._effect = make_spin_box(STAT_MIN, STAT_MAX, value=DEFAULT_EFFECT)
        self._defence = make_spin_box(STAT_MIN, STAT_MAX, value=DEFAULT_DEFENCE)
        self._toughness = make_spin_box(STAT_MIN, STAT_MAX, value=DEFAULT_TOUGHNESS)
        self._attack.setToolTip("The bonus both of its powers attack with.")
        self._effect.setToolTip("The rank of its Damage and Affliction — what sets the save DCs.")
        self._defence.setToolTip("How hard it is to hit. Dodge follows from it.")
        self._toughness.setToolTip("How hard it is to hurt.")
        form.addRow("Attack:", self._attack)
        form.addRow("Effect:", self._effect)
        form.addRow("Defence:", self._defence)
        form.addRow("Toughness:", self._toughness)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("Create NPC")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._name.textChanged.connect(self._sync_ok)
        self._sync_ok()

    # -- construction --------------------------------------------------------

    def _build_name_row(self) -> QWidget:
        """The name box, pre-filled, beside the button that offers another one.

        Pre-filled rather than blank because naming a mook is the one part of this
        that is not a decision — the GM either takes what is offered or types over
        it, and both are one action.
        """
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(int(theme.metric("space.sm")))
        self._name = QLineEdit(random_npc_name())
        line.addWidget(self._name, stretch=1)
        reroll = QPushButton("🎲")
        reroll.setToolTip("Suggest another name")
        reroll.setFixedWidth(int(theme.metric("column.roll-button")))
        reroll.clicked.connect(lambda: self._name.setText(random_npc_name(self._name.text())))
        line.addWidget(reroll)
        return row

    def _build_portrait_row(self) -> QWidget:
        """The portrait preview and its chooser.

        No bundled artwork ships yet, so the placeholder *is* the box — the same
        empty frame the NPC roster card shows. When portraits are added, a swatch
        picker goes beside this button and sets ``_image_path`` the same way.
        """
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(int(theme.metric("space.sm")))

        self._portrait = QLabel("No image")
        self._portrait.setFixedSize(PORTRAIT_SIZE, PORTRAIT_SIZE)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.setFrameShape(QLabel.Shape.Box)
        line.addWidget(self._portrait)

        choose = QPushButton("Choose image…")
        choose.clicked.connect(self._choose_image)
        line.addWidget(choose, alignment=Qt.AlignmentFlag.AlignTop)
        line.addStretch()
        return row

    # -- the portrait --------------------------------------------------------

    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select NPC image", "", IMAGE_FILTER)
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            # Same bargain the sheet's portrait strikes: a file that isn't an image
            # leaves the creature with the portrait it had rather than an empty frame.
            self._portrait.setText("Invalid image")
            return
        self._image_path = path
        self._portrait.setPixmap(
            pixmap.scaled(
                self._portrait.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # -- the result ----------------------------------------------------------

    def _sync_ok(self) -> None:
        """A nameless NPC cannot be saved — the file name comes from it."""
        self._ok_button.setEnabled(bool(self._name.text().strip()))

    def value(self) -> QuickNPC:
        """What was entered. Read after ``exec()`` returns ``Accepted``."""
        return QuickNPC(
            name=self._name.text().strip(),
            attack=self._attack.value(),
            effect=self._effect.value(),
            defence=self._defence.value(),
            toughness=self._toughness.value(),
            image_path=self._image_path,
        )
