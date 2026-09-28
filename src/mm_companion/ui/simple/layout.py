"""Where the simple sheet puts its boxes — the two presets, as pure data.

The simple sheet has two arrangements, and neither is edited *on* it:

* **Standard** is fixed: the one layout this module states, modelled on a printed
  character sheet — who the character is across the top, the stat blocks under it,
  skills beside advantages, then the long lists (powers, gear, notes) each a full row.
  A block the sheet does not have is simply left out, and one it does not know (a mod's)
  gets a row of its own at the end, so the preset never loses anything.
* **Custom** is whatever the player arranged on the edit sheet: the same tree the
  canvas keeps (:mod:`mm_companion.ui.layout_tree`), read rather than copied, so moving
  a block in edit mode *is* how the custom preset is changed. A closed block stays
  closed; a floated one, which has no place on a page, gets a row at the end.

Both come out as a :class:`SimpleLayout`: the page (a vertical split of rows) and the
strip beside it (where the roller lives, on the edge the player put it). Pure functions
over frozen dataclasses, so the arrangement is tested without a widget.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from mm_companion.core.storage import (
    SIMPLE_PRESET_CUSTOM,
    SIMPLE_PRESET_STANDARD,
    SIMPLE_PRESETS,
)
from mm_companion.ui import layout_tree as lt
from mm_companion.ui.blocks.base import instance_template
from mm_companion.ui.layout_tree import HORIZONTAL, VERTICAL, Leaf, Node, Split

#: The two presets, by the id the settings file stores (``core.storage`` owns the words).
PRESET_STANDARD = SIMPLE_PRESET_STANDARD
PRESET_CUSTOM = SIMPLE_PRESET_CUSTOM
PRESETS = SIMPLE_PRESETS

#: The edges the strip may sit on (the same four the edit sheet's pinned strip uses).
EDGES = ("left", "right", "top", "bottom")


@dataclass(frozen=True)
class SimpleLayout:
    """Where every shown block goes: rows on the page, and a strip beside it."""

    page: Split
    strip: Node | None = None
    edge: str = "right"

    def keys(self) -> list[str]:
        """Every block this layout shows, page first."""
        return lt.keys(self.page) + lt.keys(self.strip)


def _row(*keys: str, weights: Sequence[int] = ()) -> Split:
    return Split(HORIZONTAL, tuple(Leaf((key,)) for key in keys), tuple(weights))


#: The Standard preset, before it meets a real block set. Weights are shares of the
#: row's width, not pixels: the renderer reads a horizontal split's sizes as stretch.
STANDARD_PAGE = Split(
    VERTICAL,
    (
        _row("character_image", "base_info", "system_info", weights=(2, 5, 4)),
        _row("abilities", "resistances", weights=(7, 5)),
        Leaf(("conditions",)),
        Split(
            HORIZONTAL,
            (
                Leaf(("skills",)),
                Split(VERTICAL, (Leaf(("advantages",)), Leaf(("complications",)))),
            ),
            (1, 1),
        ),
        Leaf(("powers",)),
        Leaf(("equipment",)),
        Leaf(("notes",)),
    ),
)
STANDARD_STRIP = Split(VERTICAL, (Leaf(("dice",)), Leaf(("scene",))))
STANDARD_EDGE = "right"


def _keep(node: Node | None, wanted: set[str]) -> Node | None:
    """*node* with every key not in *wanted* removed, and emptied splits collapsed.

    A split that loses children keeps its weights for the survivors — a missing
    portrait must not hand the rest of the row a proportion nobody chose.
    """
    if node is None:
        return None
    if isinstance(node, Leaf):
        kept = tuple(key for key in node.keys if key in wanted)
        return Leaf(kept) if kept else None
    children: list[Node] = []
    sizes: list[int] = []
    usable = node.usable_sizes()
    for index, child in enumerate(node.children):
        kept = _keep(child, wanted)
        if kept is None:
            continue
        children.append(kept)
        if usable:
            sizes.append(usable[index])
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    return Split(node.orientation, tuple(children), tuple(sizes))


def _with_instances(node: Node | None, keys: Iterable[str]) -> Node | None:
    """Put every extra instance of a multi block (``notes#2``) beside its template."""
    extras: dict[str, list[str]] = {}
    for key in keys:
        template = instance_template(key)
        if template != key:
            extras.setdefault(template, []).append(key)
    if node is None or not extras:
        return node

    def walk(current: Node) -> Node:
        if isinstance(current, Leaf):
            added = tuple(k for key in current.keys for k in extras.get(key, []))
            return Leaf(current.keys + added) if added else current
        return Split(current.orientation, tuple(walk(c) for c in current.children), current.sizes)

    return walk(node)


def _as_page(node: Node | None) -> Split:
    if node is None:
        return Split(VERTICAL, ())
    if isinstance(node, Split) and node.orientation == VERTICAL:
        return node
    return Split(VERTICAL, (node,))


def _append_rows(page: Split, keys: Iterable[str]) -> Split:
    rows = tuple(Leaf((key,)) for key in keys)
    return Split(VERTICAL, page.children + rows) if rows else page


def standard_layout(keys: Sequence[str]) -> SimpleLayout:
    """The Standard preset over the blocks *keys* names (every block the sheet has)."""
    wanted = set(keys)
    page = _as_page(_keep(_with_instances(STANDARD_PAGE, keys), wanted))
    strip = _keep(STANDARD_STRIP, wanted)
    placed = set(lt.keys(page)) | set(lt.keys(strip))
    page = _append_rows(page, [key for key in keys if key not in placed])
    return SimpleLayout(page, strip, STANDARD_EDGE)


def custom_layout(arrangement: dict, keys: Sequence[str]) -> SimpleLayout:
    """The Custom preset: the edit sheet's own arrangement, read off *arrangement*.

    *arrangement* is :meth:`~mm_companion.ui.block_canvas.BlockCanvas.arrangement`'s
    model. Anything it cannot be read as falls back to Standard rather than showing a
    blank page — the same "a layout that half-describes somebody's blocks is worse than
    none" rule the canvas applies to a saved layout.
    """
    known = set(keys)
    try:
        page = lt.from_dict(arrangement.get("page"), known)
        region = arrangement.get("region") or {}
        strip = lt.from_dict(region.get("root"), known) if region.get("root") else None
    except (AttributeError, TypeError):
        return standard_layout(keys)
    if page is None and strip is None:
        return standard_layout(keys)
    edge = region.get("edge") if isinstance(region, dict) else None
    page_split = _as_page(page)
    placed = set(lt.keys(page_split)) | set(lt.keys(strip))
    floating = arrangement.get("floating") or {}
    extra = [key for key in keys if key in floating and key not in placed]
    return SimpleLayout(
        _append_rows(page_split, extra), strip, edge if edge in EDGES else STANDARD_EDGE
    )


def without_keys(layout: SimpleLayout, dropped: set[str]) -> SimpleLayout:
    """*layout* with the blocks *dropped* names taken out, their rows closing up."""
    if not dropped:
        return layout
    kept = set(layout.keys()) - dropped
    return SimpleLayout(_as_page(_keep(layout.page, kept)), _keep(layout.strip, kept), layout.edge)


def printable_page(layout: SimpleLayout, printable: set[str]) -> Split:
    """The page with everything that does not belong on paper taken out."""
    return _as_page(_keep(layout.page, printable))
