"""The topic signal bus and the block descriptors' publish/subscribe tables.

The bus carries the sheet's cross-block reactivity. These tests cover the bus
mechanics (headless but for the coalescing ones, which need an owner to tie a
pending redraw to) and check that every base descriptor's ``publishes``/
``subscribes`` tables are internally consistent — each named signal and method
actually exists on the block it names, and the whole web reproduces the old
hand-wired fan-out. The end-to-end behaviour (moving an ability updates skills,
powers, the system readouts, …) is exercised by ``test_ui_wiring.py``.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from mm_companion.ui.blocks import block_descriptors
from mm_companion.ui.blocks.bus import (
    ABILITY_CHANGED,
    BONUS_REQUESTED,
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
    BONUS_REQUESTED,
}


@pytest.fixture
def qobject_owner():
    """Something for a coalescing bus to tie its timer to, and its Qt application.

    A ``QTimer`` needs an application to exist even to be constructed, and the owner
    is what stops a pending redraw outliving what it would redraw. The rest of this
    file is deliberately Qt-free; only the coalescing tests take this.
    """
    QApplication.instance() or QApplication([])
    owner = QObject()
    yield owner
    owner.deleteLater()


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


# -- coalescing subscribers --------------------------------------------------
#
# The two card trees rebuild themselves wholesale on ``facts-changed``, which every
# spin box raises on every step, so a rank dragged from 0 to 10 rebuilt them ten
# times and showed the tenth. A coalescing subscriber is armed by a publish and run
# once when the turn settles.


def test_a_coalescing_subscriber_is_not_run_by_the_publish(qobject_owner) -> None:
    bus = SignalBus(qobject_owner)
    runs: list[int] = []
    bus.subscribe("t", lambda: runs.append(1), coalesce=True)

    bus.publish("t")

    assert runs == []
    assert bus.pending


def test_a_burst_of_publishes_becomes_one_run(qobject_owner) -> None:
    bus = SignalBus(qobject_owner)
    runs: list[int] = []
    bus.subscribe("t", lambda: runs.append(1), coalesce=True)

    for _ in range(10):
        bus.publish("t")
    bus.flush()

    assert runs == [1]


def test_flushing_with_nothing_armed_does_nothing(qobject_owner) -> None:
    bus = SignalBus(qobject_owner)
    runs: list[int] = []
    bus.subscribe("t", lambda: runs.append(1), coalesce=True)

    bus.flush()

    assert runs == []
    assert not bus.pending


def test_an_ordinary_subscriber_beside_a_coalescing_one_still_runs_at_once(
    qobject_owner,
) -> None:
    bus = SignalBus(qobject_owner)
    order: list[str] = []
    bus.subscribe("t", lambda: order.append("now"))
    bus.subscribe("t", lambda: order.append("later"), coalesce=True)

    bus.publish("t")
    assert order == ["now"]

    bus.flush()
    assert order == ["now", "later"]


def test_a_blanket_republish_runs_everything_and_leaves_nothing_armed(
    qobject_owner,
) -> None:
    """A restore has to land whole and at once — see ``CharacterSheet.reseed``."""
    bus = SignalBus(qobject_owner)
    runs: list[int] = []
    bus.subscribe("a", lambda: runs.append(1), coalesce=True)

    bus.publish("a")  # armed by an earlier edit...
    bus.publish_all(["a"])  # ...and superseded by the restore

    assert runs == [1]
    assert not bus.pending


def test_a_handler_that_re_arms_itself_does_not_spin(qobject_owner) -> None:
    """A flush runs what was armed when it began, never what it arms while running.

    A block subscribing and publishing the same topic is one line of plausible mod
    code, and draining a live set would run it forever — on the Qt thread that is a
    frozen window with nothing on screen to explain it.
    """
    bus = SignalBus(qobject_owner)
    runs: list[int] = []

    def handler() -> None:
        runs.append(1)
        bus.publish("t")  # re-arms itself

    bus.subscribe("t", handler, coalesce=True)
    bus.publish("t")

    bus.flush()

    assert runs == [1]
    assert bus.pending  # left for the next turn, where the loop stays interruptible


def test_a_bus_with_no_owner_never_coalesces() -> None:
    """No owner means no widget lifetime to tie a pending redraw to, so run it now.

    The headless bus tests hold one of these, and so would a mod that built a bus of
    its own; answering "immediately" is the safe reading of a missing owner.
    """
    bus = SignalBus()
    runs: list[int] = []
    bus.subscribe("t", lambda: runs.append(1), coalesce=True)

    bus.publish("t")

    assert runs == [1]
    assert not bus.pending


# -- forgetting a destroyed block --------------------------------------------


class _Block:
    """Stands in for a section: what matters is that its handlers are bound."""

    def __init__(self, log: list[str], name: str) -> None:
        self._log = log
        self._name = name

    def refresh(self) -> None:
        self._log.append(self._name)

    def serve(self, payload) -> None:
        self._log.append(f"{self._name}:{payload}")


def test_forget_drops_a_destroyed_blocks_subscriptions() -> None:
    log: list[str] = []
    kept, gone = _Block(log, "kept"), _Block(log, "gone")
    bus = SignalBus()
    bus.subscribe("t", kept.refresh)
    bus.subscribe("t", gone.refresh)

    bus.forget(gone)
    bus.publish("t")

    assert log == ["kept"]


def test_forget_drops_the_payload_channel_too() -> None:
    log: list[str] = []
    gone = _Block(log, "gone")
    bus = SignalBus()
    bus.serve("r", gone.serve)

    bus.forget(gone)
    bus.publish_request("r", 1)

    assert log == []


def test_forget_disarms_a_pending_redraw(qobject_owner) -> None:
    """The half coalescing added: the call would otherwise land a turn later."""
    log: list[str] = []
    gone = _Block(log, "gone")
    bus = SignalBus(qobject_owner)
    bus.subscribe("t", gone.refresh, coalesce=True)
    bus.publish("t")
    assert bus.pending

    bus.forget(gone)
    bus.flush()

    assert log == []
    assert not bus.pending


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
