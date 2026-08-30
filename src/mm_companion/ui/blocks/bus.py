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

**A subscriber may be coalescing.** Most run the moment their topic is published.
The two card trees do not: they rebuild themselves wholesale, and every spin box on
the sheet publishes ``facts-changed`` on every step, so a rank dragged through ten
values rebuilt them ten times over and showed the tenth. Those are subscribed with
``coalesce=True`` (see :meth:`SignalBus.subscribe`), which arms the handler and runs
it once when the turn settles. It is legal only because of the rule below — every
handler is an idempotent redraw from the shared model — and it means a caller that
reads such a block's widgets synchronously must :meth:`SignalBus.flush` first.

**All topics are argless.** Every base subscriber recomputes its view from the
shared :class:`~mm_companion.core.character.Character` model rather than from a
signal payload (e.g. the skills block's ``refresh_totals`` takes no arguments and
just recomputes from the model), so a topic only needs
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
``cost-rates-changed``  every priced block re-titles its PP subtotal
``edited``              the sheet marks the character dirty (unsaved changes)
======================  ====================================================

``build-changed`` and ``edited`` are the two sheet-level topics; the rest are
block-to-block. A runtime power toggle publishes the live-refresh topics
(``build-changed`` / ``enhancements-changed`` / ``derived-changed``) but
deliberately **not** ``edited`` — toggling a power on/off is not a persisted edit.

**Requests are the second channel, and they do carry a payload.** Everything above
is a notification: something changed, go and re-read the model. A *request* is the
opposite — one block asking another to do a specific thing, which needs saying
*what* ("roll Athletics +9"). Those go through :meth:`SignalBus.publish_request`
and its own subscriber list, so the argless contract above stays exactly as strict
as it was; a handler on one channel can never be fed the other's arguments. Blocks
declare them on their descriptor as ``requests`` (this block asks) and ``serves``
(this block answers), mirroring ``publishes``/``subscribes``.

======================  ====================================================
Request topic           Payload / server
======================  ====================================================
``roll-requested``      a :class:`~mm_companion.core.rules.RollSpec`; the Dice
                        block rolls it
``load-requested``      the same, but the Dice block only *shows* it in its
                        chip — what a single click on a stat line asks for,
                        leaving the die to a double-click
``note-requested``      a sentence (``str``); the Dice block writes it in the
                        history — the table's shared one, or the private one
                        off the air
``pin-requested``       a :class:`~mm_companion.core.rules.pins.PinRef`; served
                        by the **sheet**, not a block — a GM right-clicking a
                        stat row wants it on the card this sheet was opened
                        from, which is outside the sheet entirely
``unpin-requested``     the same, the other way. Two topics rather than one
                        toggling payload: a block knows which of the two it is
                        offering (it is told what is already pinned), and a
                        toggle whose menu label can go stale is the kind of
                        clever that misfires
``hero-point-requested``  a delta (``int``); the System block moves the pips by
                        it, through the one funnel that also writes the note
======================  ====================================================

The last exists because **Extra Effort is paid for in another block's currency**
(:mod:`mm_companion.ui.extra_effort`): shrugging the fatigue off is a Heroic Feat, which
costs a hero point, and the pips are the System block's — as is the sentence a moved
point writes into the history. Its *other* price, the fatigue itself, needs no topic of
its own: a condition is applied to the shared model by the core resolver, and the
Conditions block re-renders off ``condition-changed`` like any other subscriber.

A request normally *reveals* the block that serves it (a roll is no use happening
off screen), but a request raised as the **side effect** of another action must
not — clicking a hero point should not throw the Dice block open. Those topics are
listed in :data:`QUIET_REQUESTS`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from mm_companion.core.rules import stable_build

# Base topic names. Blocks reference these strings in their descriptors; the
# constants exist so a typo is a NameError rather than a silently-dead topic.
ABILITY_CHANGED = "ability-changed"
BUILD_CHANGED = "build-changed"
FACTS_CHANGED = "facts-changed"
DERIVED_CHANGED = "derived-changed"
ENHANCEMENTS_CHANGED = "enhancements-changed"
CAPS_CHANGED = "caps-changed"
CONDITION_CHANGED = "condition-changed"
COST_RATES_CHANGED = "cost-rates-changed"
EDITED = "edited"

#: Every notification topic, in declaration order. ``EDITED`` is one of them, and is
#: the odd one out: the other eight say *what to recompute*, while ``EDITED`` says
#: *the user changed something*.
NOTIFICATIONS = (
    ABILITY_CHANGED,
    BUILD_CHANGED,
    FACTS_CHANGED,
    DERIVED_CHANGED,
    ENHANCEMENTS_CHANGED,
    CAPS_CHANGED,
    CONDITION_CHANGED,
    COST_RATES_CHANGED,
    EDITED,
)

#: What a *restore* publishes: every notification topic except ``EDITED``. Putting an
#: earlier state back has to drive every recompute a normal edit would — the sheet
#: reads the model, so one blanket republish restates it — but it is not itself an
#: edit and must never mark the sheet dirty (see ``CharacterSheet.reseed``).
#:
#: ``BUILD_CHANGED`` and ``CONDITION_CHANGED`` are in deliberately: the session's
#: snapshot pusher subscribes to them, so an undone state reaches the GM's card on
#: the same terms any other change does.
RESEED_TOPICS = tuple(t for t in NOTIFICATIONS if t != EDITED)

# Request topics (the payload channel — see the module docstring).
ROLL_REQUESTED = "roll-requested"
LOAD_REQUESTED = "load-requested"
NOTE_REQUESTED = "note-requested"
PIN_REQUESTED = "pin-requested"
UNPIN_REQUESTED = "unpin-requested"
HERO_POINT_REQUESTED = "hero-point-requested"
BONUS_REQUESTED = "bonus-requested"

#: Request topics whose server is **not** brought into view when they are raised.
#: A note is a side effect of an edit somewhere else on the sheet, so reopening a
#: closed Dice block for one would be the app grabbing the screen unasked; the
#: note still reaches the session either way.
#:
#: ``load-requested`` is deliberately **not** here, even though it is the quieter of
#: the two roll topics: someone who clicks a stat line has asked to see it loaded, so
#: loading it into a closed block is exactly the "the app ignored my click" failure
#: revealing the server exists to prevent.
#: The two pin topics are quiet for a different reason from a note: nothing in
#: the sheet serves them at all (see the table above), so there is no block to
#: reveal — and hunting for one would be the sheet answering a question about
#: itself.
#:
#: The hero-point topic is quiet for the note's reason: a point spent to shrug off Extra
#: Effort's fatigue is the *price* of something the user did in another block, and
#: throwing the System block open over it is the app grabbing the screen unasked. The
#: roll history says what moved either way.
#:
#: ``bonus-requested`` is deliberately **not** quiet, for ``load-requested``'s reason: a
#: player who has just spent a rung of fatigue on "+2 on a single check" is about to
#: roll, and dropping the bonus into a Dice block they cannot see would be the app
#: charging them for something they never got.
QUIET_REQUESTS = frozenset({NOTE_REQUESTED, PIN_REQUESTED, UNPIN_REQUESTED, HERO_POINT_REQUESTED})

Handler = Callable[[], None]
RequestHandler = Callable[[object], None]


def _refresh(handler: Handler) -> None:
    """Run one subscriber inside a :func:`~mm_companion.core.rules.stable_build` scope.

    A subscriber is a view refresh — it reads the shared character and redraws — and a
    single one can ask the rules layer for the same derived numbers dozens of times
    over (the Skills block asks three questions per row, each of which gathers the
    whole build). Inside the scope that gather happens once. See
    :mod:`mm_companion.core.rules.build_cache` for why the scope, rather than a plain
    cache, is what makes this safe.

    **Per handler, not per fan-out.** Wrapping the whole loop would be cheaper still,
    but a scope is a promise that the model does not change inside it, and a handler
    is allowed to write the model — ``PowersSection`` normalizes its arrays before
    drawing. One scope each means such a handler can only ever mislead itself, and it
    has :func:`~mm_companion.core.rules.invalidate_build_cache` for that.
    """

    with stable_build():
        handler()


class SignalBus:
    """An argless publish/subscribe bus keyed by topic string.

    Handlers fire in subscription order when their topic is published. Because
    every base handler is an idempotent view refresh that reads the shared model
    (and never writes it or re-publishes), the fire order does not affect the
    result — the bus makes no ordering guarantee beyond "subscription order".
    """

    def __init__(self, owner: QObject | None = None) -> None:
        #: What the coalescing timer is parented to, so a pending redraw cannot
        #: outlive the thing it would redraw. A sheet closed with one armed used to
        #: leave a zero-delay timer holding bound methods of its torn-down sections,
        #: which fires into deleted C++ objects on somebody else's next event loop
        #: turn. ``None`` is legal and means the bus never coalesces — which is how
        #: the headless bus tests use it.
        self._owner = owner
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._servers: dict[str, list[RequestHandler]] = defaultdict(list)
        #: Coalescing subscribers, and the ones currently armed. A ``dict`` for the
        #: armed set because it keeps subscription order, which is the only order
        #: this bus has ever promised.
        self._coalescing: set[Handler] = set()
        self._armed: dict[Handler, None] = {}
        self._timer: QTimer | None = None

    def subscribe(self, topic: str, handler: Handler, *, coalesce: bool = False) -> None:
        """Call *handler* (with no arguments) whenever *topic* is published.

        With *coalesce*, a publish only **arms** the handler and it runs once when the
        turn settles (or at the next :meth:`flush`). For a handler expensive enough
        that running it per publish is what makes an edit feel slow: the two card
        trees rebuild themselves wholesale, and a spin box dragged through ten ranks
        raised ten of those, of which only the last was ever seen.

        What a coalescing block owes in exchange: its handler must be an **idempotent
        redraw from the model** — which every subscriber already promises (see the
        module docstring) — and anything reading the block's *widgets* in the same
        breath as the edit has to :meth:`flush` first. Nothing in the app does: what
        is saved, pushed to a session and recorded for undo is the model, not the
        widgets, and the event loop flushes a moment later either way. Tests do.
        """
        self._subscribers[topic].append(handler)
        if coalesce and self._owner is not None:
            self._coalescing.add(handler)

    def publish(self, topic: str) -> None:
        """Fire every handler subscribed to *topic*, in subscription order.

        A coalescing subscriber is armed instead (see :meth:`subscribe`).
        """
        # Iterate a copy so a handler that (re)subscribes can't disturb the loop.
        for handler in list(self._subscribers.get(topic, ())):
            if handler in self._coalescing:
                self._arm(handler)
            else:
                _refresh(handler)

    def publish_all(self, topics) -> None:
        """Fire every handler subscribed to any of *topics*, each **at most once**.

        Legal exactly because of the contract above — every handler is an idempotent
        view refresh over the shared model — and worth having because several
        handlers are subscribed to more than one topic. ``PowersSection.refresh`` and
        ``EquipmentSection.refresh`` each answer to two, and each rebuilds a whole
        card tree; publishing the topics one at a time does that twice. Bound methods
        compare and hash on ``(__self__, __func__)``, so the dedup catches them.
        """
        handlers = [h for topic in topics for h in self._subscribers.get(topic, ())]
        for handler in dict.fromkeys(handlers):
            _refresh(handler)
        # Nothing is left armed by a blanket republish: it is the one publish that
        # already runs every handler exactly once, so deferring any of them would
        # only be a second run of what just happened. (``_refresh`` above ran the
        # coalescing ones directly; this clears anything an earlier edit had armed,
        # since it has just been superseded.)
        self._armed.clear()

    def _arm(self, handler: Handler) -> None:
        """Note that *handler* has work to do, and make sure the turn will end in one.

        A single shared timer rather than one per handler: the armed handlers run in
        subscription order, which is the only order this bus promises, and one timer
        cannot fire them in another.
        """
        self._armed[handler] = None
        if self._timer is None:
            self._timer = QTimer(self._owner)
            self._timer.setSingleShot(True)
            self._timer.setInterval(0)
            self._timer.timeout.connect(self.flush)
        self._timer.start()

    def flush(self) -> None:
        """Run every handler armed **when the flush began**, in subscription order.

        Called by the bus's own timer when the turn settles, and directly by anything
        that is about to *read* a block whose redraw may still be pending. Safe to
        call at any time and cheap when nothing is armed.

        The armed set is taken once, up front, rather than drained as it goes. A
        handler is free to publish while it runs, and a handler that (directly or
        around a loop) re-raises a topic it is subscribed to would otherwise re-arm
        itself into the very loop that is running it — a flush that never returns,
        which on the Qt thread is a frozen window with nothing on screen to say why.
        Nothing in the base set does that (a card tree's ``refresh`` only reads the
        model, which is the same promise that makes coalescing legal at all), but a
        mod block subscribing and publishing the same topic is one line of plausible
        code. Deferring it to the next turn keeps the app answering.
        """
        if self._timer is not None:
            self._timer.stop()
        armed, self._armed = self._armed, {}
        for handler in armed:
            _refresh(handler)

    @property
    def pending(self) -> bool:
        """Whether any coalescing handler is armed and waiting."""
        return bool(self._armed)

    def forget(self, owner: object) -> None:
        """Drop every subscription, server and armed redraw belonging to *owner*.

        For a block the sheet has **destroyed** — a Notes instance closed from the
        View menu, or any multi-instance block a mod registers. A handler is a bound
        method, so it keeps its section alive on the Python side while the widget's
        C++ half has already gone; calling it then raises from inside whatever
        happened to publish next. Coalescing widened that from "the next publish" to
        "some later turn, with nothing on the stack to say who armed it", which is
        what makes it worth closing rather than noting.

        Matched by identity of the bound method's ``__self__``, so a block's handlers
        go and nobody else's do.
        """
        gone = [h for h in self._armed if getattr(h, "__self__", None) is owner]
        for handler in gone:
            del self._armed[handler]
        for handlers in self._subscribers.values():
            handlers[:] = [h for h in handlers if getattr(h, "__self__", None) is not owner]
        for servers in self._servers.values():
            servers[:] = [h for h in servers if getattr(h, "__self__", None) is not owner]
        self._coalescing = {
            h for h in self._coalescing if getattr(h, "__self__", None) is not owner
        }

    def make_publisher(self, topic: str) -> Callable[..., None]:
        """A callable that publishes *topic*, swallowing any arguments.

        Suitable for ``qt_signal.connect(...)`` — a signal carrying a payload
        (``abilityChanged(str, int)``) still drives an argless topic.
        """
        return lambda *args: self.publish(topic)

    def topics(self) -> list[str]:
        """Every topic that currently has at least one subscriber."""
        return [topic for topic, handlers in self._subscribers.items() if handlers]

    # -- requests (the payload channel) --------------------------------------

    def serve(self, topic: str, handler: RequestHandler) -> None:
        """Call *handler* with the payload whenever *topic* is requested."""
        self._servers[topic].append(handler)

    def publish_request(self, topic: str, payload: object = None) -> None:
        """Hand *payload* to every handler serving *topic*, in subscription order."""
        for handler in list(self._servers.get(topic, ())):
            handler(payload)

    def make_requester(self, topic: str) -> Callable[..., None]:
        """A callable that requests *topic*, passing its first argument as the payload.

        Suitable for ``qt_signal.connect(...)`` where the signal carries exactly the
        payload — ``rollRequested(object)``. An argless signal requests with ``None``.
        """
        return lambda *args: self.publish_request(topic, args[0] if args else None)

    def request_topics(self) -> list[str]:
        """Every request topic that currently has at least one server."""
        return [topic for topic, handlers in self._servers.items() if handlers]
