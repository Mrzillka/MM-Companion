"""A tiny topic signal bus for cross-block reactivity.

The character sheet used to wire its blocks together by name — a 70-line method
reaching into ``self.abilities``, ``self.skills``, … and connecting one block's
Qt signals straight to another block's slots. That made adding a block a
five-file edit and left no seam for a mod block to plug into.

The bus replaces those object-to-object connections with named **topics**. A
block *publishes* a topic (an edit happened here) and any block *subscribes* to
the topics it cares about; neither side names the other. A mod block joins the
web just by declaring which topics it publishes/subscribes on its
:class:`~mm_companion.ui.blocks.base.BlockDescriptor` — no
:class:`~mm_companion.ui.character_sheet.CharacterSheet` edit needed.

**All topics are argless.** Every base subscriber recomputes its view from the
shared :class:`~mm_companion.core.character.Character` model rather than from a
signal payload (e.g. the skills block's ``set_ability_value`` ignores its
arguments and just refreshes its totals from the model), so a topic only needs
to say *that* something changed, not *what*. This keeps dispatch trivial and
lets a Qt signal that carries arguments (``abilityChanged(str, int)``) publish an
argless topic — the publisher adapter simply drops the arguments.

The base topics and the exact block fan-out they reproduce:

======================  ====================================================
Topic                   Subscribers (what recomputes)
======================  ====================================================
``ability-changed``     skills totals; resistances re-seed their bases
``build-changed``       the sheet re-derives spent power points
``facts-changed``       the power cards re-derive from character facts
``derived-changed``     the system block's speed/initiative/size readouts
``enhancements-changed``  effective ability/resistance/skill totals
``caps-changed``        advantage rank caps + heroic budget (Power Level)
``condition-changed``   advantages struck through by a Debilitated condition
``edited``              the sheet marks the character dirty (unsaved changes)
======================  ====================================================

``build-changed`` and ``edited`` are the two sheet-level topics; the rest are
block-to-block. A runtime power toggle publishes the live-refresh topics
(``build-changed`` / ``enhancements-changed`` / ``derived-changed``) but
deliberately **not** ``edited`` — toggling a power on/off is not a persisted edit.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

# Base topic names. Blocks reference these strings in their descriptors; the
# constants exist so a typo is a NameError rather than a silently-dead topic.
ABILITY_CHANGED = "ability-changed"
BUILD_CHANGED = "build-changed"
FACTS_CHANGED = "facts-changed"
DERIVED_CHANGED = "derived-changed"
ENHANCEMENTS_CHANGED = "enhancements-changed"
CAPS_CHANGED = "caps-changed"
CONDITION_CHANGED = "condition-changed"
EDITED = "edited"

Handler = Callable[[], None]


class SignalBus:
    """An argless publish/subscribe bus keyed by topic string.

    Handlers fire in subscription order when their topic is published. Because
    every base handler is an idempotent view refresh that reads the shared model
    (and never writes it or re-publishes), the fire order does not affect the
    result — the bus makes no ordering guarantee beyond "subscription order".
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Call *handler* (with no arguments) whenever *topic* is published."""
        self._subscribers[topic].append(handler)

    def publish(self, topic: str) -> None:
        """Fire every handler subscribed to *topic*, in subscription order."""
        # Iterate a copy so a handler that (re)subscribes can't disturb the loop.
        for handler in list(self._subscribers.get(topic, ())):
            handler()

    def make_publisher(self, topic: str) -> Callable[..., None]:
        """A callable that publishes *topic*, swallowing any arguments.

        Suitable for ``qt_signal.connect(...)`` — a signal carrying a payload
        (``abilityChanged(str, int)``) still drives an argless topic.
        """
        return lambda *args: self.publish(topic)

    def topics(self) -> list[str]:
        """Every topic that currently has at least one subscriber."""
        return [topic for topic, handlers in self._subscribers.items() if handlers]
