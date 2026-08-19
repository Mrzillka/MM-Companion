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
``validation`` → ``rolls`` → ``pins``.

``costs`` moved above ``powers_terms`` (it used to sit beside it) when Morph's Metamorph
needed a note saying what its alternate forms cost: the rules put that budget at "the
same point total as you" (p136), which is ``power_points_spent`` and lives in ``costs``.
``costs`` reaches nothing in ``powers_terms``, so the edge only goes the one way.

``trait_rates`` sits between ``derived`` and ``powers_cost`` for the same reason
``size`` sits below ``derived``: an Enhanced Trait costs *as its trait*, so the
powers cost path must reach the very rates the bought traits are charged at, and
those rates therefore cannot live in ``costs`` above it.

``size`` sits below ``derived`` on purpose: the Size Table grants real trait
modifiers, so ``derived`` imports it, and it must therefore reach nothing that
reaches ``derived`` back.
"""

from .accessories import *  # noqa: F401,F403
from .advantages import *  # noqa: F401,F403
from .appliers import *  # noqa: F401,F403
from .conditions import *  # noqa: F401,F403
from .configurations import *  # noqa: F401,F403
from .costs import *  # noqa: F401,F403
from .damage import *  # noqa: F401,F403
from .derived import *  # noqa: F401,F403
from .equipment import *  # noqa: F401,F403
from .movement import *  # noqa: F401,F403
from .pins import *  # noqa: F401,F403
from .platforms import *  # noqa: F401,F403
from .powers_cost import *  # noqa: F401,F403
from .powers_terms import *  # noqa: F401,F403
from .rolls import *  # noqa: F401,F403
from .runtime import *  # noqa: F401,F403
from .size import *  # noqa: F401,F403
from .trait_rates import *  # noqa: F401,F403
from .validation import *  # noqa: F401,F403
