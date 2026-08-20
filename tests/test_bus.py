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
    COST_RATES_CHANGED,
    DERIVED_CHANGED,
    EDITED,
    ENHANCEMENTS_CHANGED,
    FACTS_CHANGED,
    HERO_POINT_REQUESTED,
    LOAD_REQUESTED,
    NOTE_REQUESTED,
    PIN_REQUESTED,
    ROLL_REQUESTED,
    UNPIN_REQUESTED,
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
    COST_RATES_CHANGED,
    EDITED,
}

REQUEST_TOPICS = {
    ROLL_REQUESTED,
    LOAD_REQUESTED,
    NOTE_REQUESTED,
    PIN_REQUESTED,
    UNPIN_REQUESTED,
    HERO_POINT_REQUESTED,
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


# -- the request (payload) channel -------------------------------------------


def test_a_request_carries_its_payload_to_every_server() -> None:
    bus = SignalBus()
    seen: list[object] = []
    bus.serve("t", seen.append)
    bus.serve("t", seen.append)
    bus.publish_request("t", "roll me")
    assert seen == ["roll me", "roll me"]


def test_make_requester_forwards_the_signals_first_argument() -> None:
    bus = SignalBus()
    seen: list[object] = []
    bus.serve(ROLL_REQUESTED, seen.append)
    bus.make_requester(ROLL_REQUESTED)("a spec")
    assert seen == ["a spec"]


def test_the_two_channels_do_not_cross() -> None:
    # An argless subscriber must never be handed a payload, and a server must never
    # be woken by a plain notification — that separation is the whole point of the
    # second channel.
    bus = SignalBus()
    fired: list[str] = []
    bus.subscribe("shared", lambda: fired.append("notified"))
    bus.serve("shared", lambda payload: fired.append(f"served {payload}"))

    bus.publish("shared")
    assert fired == ["notified"]

    bus.publish_request("shared", 7)
    assert fired == ["notified", "served 7"]


# -- descriptor pub/sub contract ---------------------------------------------


def test_descriptor_signals_and_methods_exist_on_their_blocks() -> None:
    # A descriptor's factory is the block class; every published signal name and
    # every subscribed method name must be a real attribute on that class.
    for descriptor in block_descriptors():
        block = descriptor.factory
        for signal_name in (*descriptor.publishes, *descriptor.requests):
            assert hasattr(block, signal_name), f"{descriptor.key}.{signal_name} missing"
        for method_name in (*descriptor.subscribes.values(), *descriptor.serves.values()):
            assert hasattr(block, method_name), f"{descriptor.key}.{method_name} missing"


def test_descriptors_only_use_known_topics() -> None:
    for descriptor in block_descriptors():
        for topics in descriptor.publishes.values():
            assert set(topics) <= BASE_TOPICS
        assert set(descriptor.subscribes) <= BASE_TOPICS
        for topics in descriptor.requests.values():
            assert set(topics) <= REQUEST_TOPICS
        assert set(descriptor.serves) <= REQUEST_TOPICS


def test_every_requested_topic_is_served_and_vice_versa() -> None:
    # The same dead-wiring check the notification channel gets: a request nobody
    # answers, or a server nobody asks, is a mistake either way.
    requested: set[str] = set()
    # The sheet serves the two pin topics itself — a pin's destination is a GM
    # card, which is outside the sheet entirely, so no block can answer them.
    # Seeded here for the same reason BUILD_CHANGED/EDITED are seeded below;
    # test_roll_routing.py checks the sheet really does serve them.
    served: set[str] = {PIN_REQUESTED, UNPIN_REQUESTED}
    for descriptor in block_descriptors():
        for topics in descriptor.requests.values():
            requested.update(topics)
        served.update(descriptor.serves)
    assert requested == REQUEST_TOPICS
    assert served == REQUEST_TOPICS


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
