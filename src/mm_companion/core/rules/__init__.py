"""Character math and validation — the build-time rules engine.

Pure functions over a :class:`~..character.Character` plus the
:class:`~..data_loader.GameData` content. No PySide6, no widget state: derived values
(skill totals, resistances, defense class), point-cost accounting, Power Level
validation, the powers math, movement/size, and the condition resolver.

Everything is data-driven — costs and caps come from ``game_data.costs`` and the
trait lists, never hardcoded.

Split from a single module into subsystem submodules; every public name is
re-exported here so ``from mm_companion.core.rules import X`` keeps working
unchanged. Submodules form a dependency DAG: ``appliers`` → ``runtime``/``advantages``/
``conditions`` (base) → ``size`` → ``derived`` → ``trait_rates`` → ``powers_cost`` →
``costs`` → ``movement``/``powers_terms``/``damage`` → ``platforms`` → ``equipment`` →
``validation`` → ``subbuilds``/``extra_effort`` → ``rolls`` → ``pins``.
``defenses`` (what a target's Hardened or Impervious Toughness does to a Damage save)
sits on ``runtime``, ``accessories`` and ``powers_cost`` and under ``rolls``.
``targeting`` (one check against several targets' ladders) sits on ``damage``, whose
rung resolver it shares, and under ``rolls``; ``impress`` sits on ``rolls``.

``area`` (an area's shape, size and Dodge DC) sits below ``powers_terms``, which states
an area's size on the card; ``area_attack`` (resolving one against a scene) sits on
``rolls``, beside ``impress``. ``answering`` (a roll answered for a creature, and what
its spec's ladder does to it) sits on ``rolls``, and so does ``recovery`` (the check
that rolls a condition off, and what making it sheds).

``costs`` moved above ``powers_terms`` (it used to sit beside it) when Morph's Metamorph
needed a note saying what its alternate forms cost: the rules put that budget at "the
same point total as you" (p136), which is ``power_points_spent`` and lives in ``costs``.
``costs`` reaches nothing in ``powers_terms``, so the edge only goes the one way.

``trait_rates`` sits between ``derived`` and ``powers_cost`` for the same reason
``size`` sits below ``derived``: an Enhanced Trait costs *as its trait*, so the
powers cost path must reach the very rates the bought traits are charged at, and
those rates therefore cannot live in ``costs`` above it.

``subbuilds`` sits *below* ``validation`` rather than beside it because a nested
character is a whole build in its own right: checking a minion means walking its
powers tree, and ``leaf_powers`` — where that walk stops — is validation's.

``extra_effort`` sits beside ``subbuilds`` and for the same reason: it asks
``leaf_powers`` which effects a character could push, and asks ``powers_terms`` what an
effect's duration resolves to before deciding a Permanent one cannot be pushed at all.

``size`` sits below ``derived`` on purpose: the Size Table grants real trait
modifiers, so ``derived`` imports it, and it must therefore reach nothing that
reaches ``derived`` back.
"""

from .accessories import *  # noqa: F401,F403
from .advantages import *  # noqa: F401,F403
from .answering import *  # noqa: F401,F403
from .appliers import *  # noqa: F401,F403
from .area import *  # noqa: F401,F403
from .area_attack import *  # noqa: F401,F403
from .attacks import *  # noqa: F401,F403
from .build_cache import *  # noqa: F401,F403
from .conditions import *  # noqa: F401,F403
from .configurations import *  # noqa: F401,F403
from .costs import *  # noqa: F401,F403
from .damage import *  # noqa: F401,F403
from .defenses import *  # noqa: F401,F403
from .derived import *  # noqa: F401,F403
from .equipment import *  # noqa: F401,F403
from .extra_effort import *  # noqa: F401,F403
from .impress import *  # noqa: F401,F403
from .improvised import *  # noqa: F401,F403
from .minions import *  # noqa: F401,F403
from .movement import *  # noqa: F401,F403
from .pins import *  # noqa: F401,F403
from .platforms import *  # noqa: F401,F403
from .powers_cost import *  # noqa: F401,F403
from .powers_terms import *  # noqa: F401,F403
from .recovery import *  # noqa: F401,F403
from .rolls import *  # noqa: F401,F403
from .runtime import *  # noqa: F401,F403
from .size import *  # noqa: F401,F403
from .subbuilds import *  # noqa: F401,F403
from .targeting import *  # noqa: F401,F403
from .trait_rates import *  # noqa: F401,F403
from .validation import *  # noqa: F401,F403
