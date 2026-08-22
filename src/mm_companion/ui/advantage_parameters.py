"""Resolving an advantage's ``parameter`` spec into the choices a picker offers.

An advantage may attach to a *subject* — Improved Critical's attack, Skill Mastery's
skill, Alternate Initiative's mental ability — described by a
:class:`~mm_companion.core.data_loader.ParameterSpec` and stored on the selection as
:attr:`~mm_companion.core.character.AdvantageSelection.parameter`. Turning that spec into
a list of options is not the same job as *asking* for one, and two places now ask: the
Advantages block, where the advantage is bought, and the Power Constructor's trait
picker, where an Enhanced Trait grants it. Both read the answer from here, so an
advantage cannot offer one set of subjects when bought and another when granted.

Presentation only — no rules math, and every list comes from the game data or the live
build rather than from anything written down here.
"""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, ParameterSpec
from mm_companion.core.powers import PowerGroup

#: ``optionsFrom`` sources drawn from the character rather than the catalog. Named so a
#: caller with no character in hand knows which specs it cannot fully answer.
CHARACTER_SOURCES = ("powers",)


def power_names(character: Character | None) -> list[str]:
    """Every named leaf power on the character, descending array/linked groups."""

    names: list[str] = []
    if character is None:
        return names

    def walk(nodes) -> None:
        for node in nodes:
            if isinstance(node, PowerGroup):
                walk(node.children)
            elif node.name:
                names.append(node.name)

    walk(character.powers)
    return names


def parameter_options(
    spec: ParameterSpec, game_data: GameData, character: Character | None = None
) -> list[tuple[str, str]]:
    """Resolve a choice spec to ``(stored value, display label)`` pairs.

    A dynamic ``options_from`` source draws from the live build —
    ``"skills"``/``"abilities"`` from the game data (abilities store their key but
    display their name), ``"powers"`` from the character's own powers. When the spec
    *also* lists ``options``, those restrict the source to that subset in the given order
    (e.g. Alternate Initiative offers only INT/AWE/PRE, not every ability). Without a
    source, a fixed ``options`` list maps each value to itself.

    ``character`` may be ``None`` — the standalone Power Constructor has no build to draw
    from — in which case a character-sourced spec resolves to an empty list rather than
    inventing options nobody owns.
    """

    if spec.options_from == "skills":
        source = [(s.name, s.name) for s in game_data.skills]
    elif spec.options_from == "abilities":
        source = [(a.key, a.name) for a in game_data.abilities]
    elif spec.options_from == "powers":
        source = [(name, name) for name in power_names(character)]
    else:
        return [(option, option) for option in spec.options]
    if spec.options:
        labels = dict(source)
        return [(value, labels.get(value, value)) for value in spec.options]
    return source


def parameter_display(spec: ParameterSpec | None, parameter: str, game_data: GameData) -> str:
    """A stored subject as it is shown — an ability key resolves to its name.

    Everything else is already the text the player chose or typed, and passes through.
    """

    if spec is not None and spec.options_from == "abilities":
        return next((a.name for a in game_data.abilities if a.key == parameter), parameter)
    return parameter
