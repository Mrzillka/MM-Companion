"""Character math and validation — the build-time rules engine.

Pure functions over a :class:`~..character.Character` plus the
:class:`~..data_loader.GameData` content. No PySide6, no widget state: derived values
(skill totals, resistances, defense class), point-cost accounting, Power Level
validation, the powers math, movement/size, and the condition resolver.

Everything is data-driven — costs and caps come from ``game_data.costs`` and the
trait lists, never hardcoded.

Split from a single module into subsystem submodules; every public name is
re-exported here so ``from mm_companion.core.rules import X`` keeps working
unchanged. Submodules form a dependency DAG: ``runtime``/``advantages``/``conditions``
(base) → ``derived`` → ``powers_cost`` → ``costs``/``movement``/``powers_terms`` →
``validation``.
"""

from .advantages import *  # noqa: F401,F403
from .conditions import *  # noqa: F401,F403
from .costs import *  # noqa: F401,F403
from .derived import *  # noqa: F401,F403
from .movement import *  # noqa: F401,F403
from .powers_cost import *  # noqa: F401,F403
from .powers_terms import *  # noqa: F401,F403
from .runtime import *  # noqa: F401,F403
from .validation import *  # noqa: F401,F403
