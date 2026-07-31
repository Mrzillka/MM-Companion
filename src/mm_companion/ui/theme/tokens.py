"""The design-token vocabulary a theme file speaks.

A :class:`Theme` is four flat maps of named tokens — ``colors``, ``metrics``,
``typography`` and ``blocks`` — plus a :class:`Chrome` record saying how much of
the widget look the theme takes over. Nothing here touches Qt: a theme is plain
data, parsed once and read by the UI through :mod:`mm_companion.ui.theme`.

Token names are flat dotted strings (``"tint.worse"``, ``"radius.card"``) rather
than nested objects, so a theme file stays a shallow JSON object that is easy to
diff and easy to override one key at a time.

Colour values are strings, and deliberately not all hex. Qt's stylesheet
``palette(role)`` function is how the *system* preset stays theme-aware without
naming a single surface colour — so ``"palette(mid)"`` is a legal token value
and any code deriving a translucent wash from a token has to cope with one (see
:func:`is_literal_color`).

Contrast
--------
The semantic tints (``tint.better``, ``tint.worse``, ``accent``, …) mostly style
bold or otherwise emphasised text, for which WCAG's AA threshold is **3.0:1**.
A preset whose ``chrome.mode`` is ``"system"`` cannot know what it is painting
on — the OS decides — so its tints must clear that bar against *both* a light
window (``#f0f0f0``) and a dark one (``#2d2d2d``). No single fixed hue can also
clear the 4.5:1 small-text threshold on both at once (light wants a darker
shade, dark a lighter one), so those tints are balanced for the best achievable
worst case and land at ~3.2:1. A ``"styled"`` preset *does* know its own
surfaces, declares them as ``surface.*`` tokens, and is held to the same 3.0:1
bar against those instead — where it can comfortably do better. ``tests/test_theme.py``
enforces this per preset; keep any retune inside that envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

#: ``chrome.mode`` values. ``system`` leaves every widget in the native platform
#: style and emits no chrome stylesheet; ``styled`` lets the theme dress the
#: window itself (see :mod:`mm_companion.ui.theme.qss`).
CHROME_SYSTEM = "system"
CHROME_STYLED = "styled"
CHROME_MODES = (CHROME_SYSTEM, CHROME_STYLED)

#: The two window shades a ``system`` preset's tints are measured against, since
#: it inherits whichever the OS is currently using.
SYSTEM_LIGHT_WINDOW = "#f0f0f0"
SYSTEM_DARK_WINDOW = "#2d2d2d"

#: Minimum contrast a semantic tint must clear against its background. WCAG AA
#: for large/bold text; see the module docstring for why not 4.5.
MIN_CONTRAST = 3.0


@dataclass(frozen=True)
class Chrome:
    """How much of the widget look this theme takes over."""

    #: ``"system"`` or ``"styled"`` — see :data:`CHROME_MODES`.
    mode: str = CHROME_SYSTEM
    #: Draw an explicit accent ring on the focused control. Additive to either
    #: mode: it is the only visible cue for the wheel guard's "this widget takes
    #: the wheel once focused" rule (see :mod:`mm_companion.ui.wheel_guard`).
    focus_ring: bool = True


@dataclass(frozen=True)
class Theme:
    """One parsed theme preset."""

    id: str
    name: str
    description: str = ""
    chrome: Chrome = field(default_factory=Chrome)
    colors: Mapping[str, str] = field(default_factory=dict)
    metrics: Mapping[str, int] = field(default_factory=dict)
    typography: Mapping[str, object] = field(default_factory=dict)
    #: Per-block size overrides layered over ``ui/block_sizes.json``; keyed by
    #: block id, each a subset of ``min_width``/``min_height``/``max_width``/
    #: ``max_height``. Usually empty — only a theme that changes the density
    #: needs to move a block's floor.
    blocks: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    #: Which bundled drawing the artwork is taken from — ``die`` and
    #: ``hero-point``, each naming a variant id owned by
    #: :mod:`mm_companion.ui.svg_assets`. A theme picks the *look*; it never names
    #: a file, so a hand-edited preset cannot point the renderer at one.
    assets: Mapping[str, str] = field(default_factory=dict)

    @property
    def styled(self) -> bool:
        """Whether this theme dresses the widgets itself rather than the OS."""
        return self.chrome.mode == CHROME_STYLED


#: The surfaces a ``styled`` preset's text is read against, in the order they are
#: measured. Only those it actually declares as literals count.
_STYLED_BACKDROPS = ("surface.window", "surface.block", "surface.card")


def measurement_backdrops(theme: Theme) -> tuple[str, ...]:
    """The background colours *theme*'s tints have to stay legible against.

    A ``system`` preset inherits the OS window colour and cannot know which it
    got, so it is held to the bar on a light *and* a dark one. A ``styled``
    preset declares its own surfaces and is measured against those instead.

    One definition because two callers need to agree: ``tests/test_theme.py``
    enforces the floor per preset, and the Settings window warns the user in the
    moment they pick a colour that would fail it.
    """
    if not theme.styled:
        return (SYSTEM_LIGHT_WINDOW, SYSTEM_DARK_WINDOW)
    return tuple(
        theme.colors[key]
        for key in _STYLED_BACKDROPS
        if is_literal_color(str(theme.colors.get(key, "")))
    )


class UnknownToken(KeyError):
    """A token was asked for that no theme in the chain defines.

    Its own class (rather than a bare ``KeyError``) so a typo'd token name in
    widget code reports as a theme problem and not as a dict miss somewhere in
    the middle of a layout.
    """

    def __init__(self, group: str, name: str, known: Mapping[str, object]) -> None:
        near = sorted(k for k in known if _looks_near(name, k))
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        super().__init__(f"No {group} token named {name!r} in the active theme.{hint}")


def _looks_near(name: str, candidate: str) -> bool:
    """Cheap did-you-mean: share a dotted segment, or one is a prefix of the other."""
    if name.startswith(candidate) or candidate.startswith(name):
        return True
    return bool(set(name.split(".")) & set(candidate.split(".")))


def is_literal_color(value: str) -> bool:
    """Whether *value* is a concrete ``#rrggbb`` rather than a ``palette(...)`` call.

    Only a literal can be decomposed into channels — a ``palette(mid)`` is
    resolved by Qt at paint time and has no rgb here. Callers deriving a wash or
    measuring contrast must check this first.
    """
    value = value.strip()
    return value.startswith("#") and len(value.lstrip("#")) in (3, 6)


def _channels(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) == 3:  # #abc shorthand
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgba(hex_color: str, alpha: float) -> str:
    """An ``rgba(r, g, b, a)`` string for *hex_color* at *alpha*.

    For the translucent *fills* behind a drop target or a highlighted card,
    which pair a solid border with a faint wash of the same hue. Deriving the
    wash from the token (rather than hardcoding its rgb) keeps the fill in step
    with the border whenever a tint is retuned — otherwise the two drift apart,
    as they silently did after an earlier contrast pass nudged every accent.
    """
    if not is_literal_color(hex_color):
        raise ValueError(
            f"Cannot derive a translucent wash from {hex_color!r} — only a literal "
            "#rrggbb has channels to work with. Give the theme a concrete colour "
            "for any token a wash is taken from."
        )
    r, g, b = _channels(hex_color)
    return f"rgba({r}, {g}, {b}, {alpha})"


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of *hex_color*, in 0..1."""

    def channel(raw: int) -> float:
        c = raw / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _channels(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two literal colours, from 1.0 to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)
