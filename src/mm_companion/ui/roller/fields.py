"""One labelled number on the roller: a box with steps either side of it, or a slider.

The roller has up to four of them — **Modifier** (or **Bonus** and **Penalty** in its
place, Settings ▸ General), and **DC** — and they are all the same kind of row
(the user's call, 2026-11-13): with step buttons chosen, ``[−5][−1] [box] [+1][+5]``;
with the slider chosen, ``[════slider════] [box]``. The DC used to keep its steps
under a slider setting, on the grounds that a slider is no way to hit 17; the user
asked for the slider there too, and the box beside it still takes a typed 17.

The box is **as wide as its widest value** and never stretches: a two-digit number
and its sign. Stretched, it took the slider's room and read as a text field.

A row only holds its widgets and keeps its slider and box agreeing;
:class:`~mm_companion.ui.roller.controls.RollControls` places them.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSlider, QSpinBox, QWidget


class FieldRow:
    """A label, its box, the slider it may have instead of steps, and its steps.

    The slider and the box follow each other; the slider reaches only as far as its
    own range, so a number typed past it parks the slider at its end.
    """

    def __init__(self, key: str, label: QLabel, box: QSpinBox, slider: QSlider) -> None:
        self.key = key
        self.label = label
        self.box = box
        self.slider = slider
        #: The step buttons, penalties then bonuses (the panel builds them).
        self.steps: list[QWidget] = []
        slider.valueChanged.connect(self._slider_moved)
        box.valueChanged.connect(self.sync_slider)
        self.sync_slider()

    def _slider_moved(self, value: int) -> None:
        if self.box.value() != value:
            self.box.setValue(value)

    def sync_slider(self, *_args) -> None:
        """Follow the box (a step, a typed number, a roll spending it), as far as the
        slider reaches."""
        value = max(self.slider.minimum(), min(self.slider.maximum(), self.box.value()))
        if self.slider.value() != value:
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)

    def widgets(self) -> list[QWidget]:
        """Everything the row owns, steps included."""
        return [self.label, self.box, self.slider, *self.steps]
