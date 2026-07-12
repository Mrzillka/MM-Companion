"""The topic signal bus and the block descriptors' publish/subscribe tables.

The bus carries the sheet's cross-block reactivity. These tests cover the bus
mechanics (headless, no Qt) and check that every base descriptor's ``publishes``/
``subscribes`` tables are internally consistent — each named signal and method
actually exists on the block it names, and the whole web reproduces the old
hand-wired fan-out. The end-to-end behaviour (moving an ability updates skills,
powers, the system readouts, …) is exercised by ``test_ui_wiring.py``.
"""

from __future__ import annotations

from mm_companion.ui.blocks import block_descriptors
from mm_companion.ui.blocks.bus import (
    ABILITY_CHANGED,
    BUILD_CHANGED,
    CAPS_CHANGED,
    CONDITION_CHANGED,
    DERIVED_CHANGED,
    EDITED,
    ENHANCEMENTS_CHANGED,
    FACTS_CHANGED,
    SignalBus,
)

BASE_TOPICS = {
    ABILITY_CHANGED,
    BUILD_CHANGED,
    FACTS_CHANGED,
    DERIVED_CHANGED,
    ENHANCEMENTS_CHANGED,
    CAPS_CHANGED,
    CONDITION_CHANGED,
    EDITED,
}


# -- bus mechanics -----------------------------------------------------------


def test_publish_fires_every_subscriber_in_order() -> None:
    bus = SignalBus()
    fired: list[str] = []
    bus.subscribe("t", lambda: fired.append("a"))
    bus.subscribe("t", lambda: fired.append("b"))
    bus.publish("t")
    assert fired == ["a", "b"]


def test_publish_of_an_unsubscribed_topic_is_a_no_op() -> None:
    bus = SignalBus()
    bus.publish("nobody-listens")  # must not raise


def test_subscribers_are_isolated_per_topic() -> None:
    bus = SignalBus()
    hits: list[str] = []
    bus.subscribe("x", lambda: hits.append("x"))
    bus.subscribe("y", lambda: hits.append("y"))
    bus.publish("y")
    assert hits == ["y"]


def test_make_publisher_swallows_signal_arguments() -> None:
    bus = SignalBus()
    fired: list[int] = []
    bus.subscribe(ABILITY_CHANGED, lambda: fired.append(1))
    publisher = bus.make_publisher(ABILITY_CHANGED)
    publisher("STR", 4)  # a Qt abilityChanged(str, int) payload — dropped
    assert fired == [1]


def test_topics_lists_only_subscribed_topics() -> None:
    bus = SignalBus()
    bus.subscribe("live", lambda: None)
    assert bus.topics() == ["live"]


# -- descriptor pub/sub contract ---------------------------------------------


def test_descriptor_signals_and_methods_exist_on_their_blocks() -> None:
    # A descriptor's factory is the block class; every published signal name and
    # every subscribed method name must be a real attribute on that class.
    for descriptor in block_descriptors():
        block = descriptor.factory
        for signal_name in descriptor.publishes:
            assert hasattr(block, signal_name), f"{descriptor.key}.{signal_name} missing"
        for method_name in descriptor.subscribes.values():
            assert hasattr(block, method_name), f"{descriptor.key}.{method_name} missing"


def test_descriptors_only_use_known_topics() -> None:
    for descriptor in block_descriptors():
        for topics in descriptor.publishes.values():
            assert set(topics) <= BASE_TOPICS
        assert set(descriptor.subscribes) <= BASE_TOPICS


def test_every_published_topic_has_a_subscriber_and_vice_versa() -> None:
    # The sheet itself subscribes BUILD_CHANGED and EDITED; every other topic must
    # be matched by a block on both ends, or it is dead wiring.
    published: set[str] = set()
    subscribed: set[str] = {BUILD_CHANGED, EDITED}
    for descriptor in block_descriptors():
        for topics in descriptor.publishes.values():
            published.update(topics)
        subscribed.update(descriptor.subscribes)
    assert published == BASE_TOPICS
    assert subscribed == BASE_TOPICS
