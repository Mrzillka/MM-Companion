"""The Power Constructor: a brick-builder for assembling powers.

A standalone top-level window. The left side is a palette of draggable *bricks*
grouped into three tabs — **Effects**, **Extras**, **Flaws** — built by iterating
the loaded :class:`~mm_companion.core.data_loader.GameData`. The right side is the
power being built: a name, a description, a live power-point cost, and a canvas of
effect *cards*.

Interaction (all drag-and-drop):

- Drag an **Effect** brick onto the canvas → a new :class:`EffectCard` appears
  (one :class:`~mm_companion.core.powers.PowerEffectInstance`).
- Drag an **Extra** or **Flaw** brick onto a specific card → it attaches there as a
  chip (a modifier modifies one effect, per the M&M model).
- Once a second effect is on the canvas a :class:`PowerModeBar` appears, switching
  the power between **Independent**, **Linked**, and **Array** structures (§4). The
  structure lives on the :class:`~mm_companion.core.powers.Power`; the cards badge
  their role and the total recomputes from it (an array pays its base in full plus
  a flat point per alternate) — the modifier chips aren't touched.

The window owns a single :class:`~mm_companion.core.powers.Power` and mutates it;
costs always come from :mod:`mm_companion.core.rules`, never computed inline. A
**Save Power** button hands the finished power to the host section via
:attr:`PowerConstructorWindow.powerSaved` and closes the window.

Given the character's Power Level, the editor flags a power that breaks a PL cap
(:func:`~mm_companion.core.rules.power_pl_violations`) with a live warning. Whether
that merely warns or actually blocks the save is a single app-wide switch —
:func:`~mm_companion.core.storage.pl_enforcement` — so it can move to a settings
toggle later without touching this window.

This module was split into a package for navigability (the old single file topped
2600 lines); the public surface — :class:`PowerConstructorWindow` and the modding
seams below — is re-exported here so ``mm_companion.ui.power_constructor`` keeps
working as the one import point.
"""

from __future__ import annotations

from mm_companion.ui.power_constructor.bricks import BrickWidget
from mm_companion.ui.power_constructor.canvas import PowerCanvas, PowerModeBar
from mm_companion.ui.power_constructor.common import (
    _GROUP_HEADER,
    CONFIG_WIDGET_BUILDERS,
    STRENGTH_AMOUNT_MAX,
    ConfigWidgetBuilder,
    combat_focus_options,
    register_base_config_widgets,
)
from mm_companion.ui.power_constructor.effect_card import EffectCard
from mm_companion.ui.power_constructor.modifier_chip import ModifierChip, ModifierGroup
from mm_companion.ui.power_constructor.terms_view import PowerTermsView
from mm_companion.ui.power_constructor.window import PowerConstructorWindow

__all__ = [
    "BrickWidget",
    "CONFIG_WIDGET_BUILDERS",
    "ConfigWidgetBuilder",
    "EffectCard",
    "ModifierChip",
    "ModifierGroup",
    "PowerCanvas",
    "PowerConstructorWindow",
    "PowerModeBar",
    "PowerTermsView",
    "STRENGTH_AMOUNT_MAX",
    "_GROUP_HEADER",
    "combat_focus_options",
    "register_base_config_widgets",
]
