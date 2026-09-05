"""The arrangement as a tree of splits and leaves, with no Qt in sight.

The sheet used to hold its arrangement as ``list[list[str]]`` — rows of block
keys — which said everything there was to say when a row was the only container
and a block's size came entirely from its content. A user-resizable page needs
more: a cell inside a row can hold *another* split, that one can hold another,
and a cell can hold several blocks at once as a tab group.

So the model is a tree of two node kinds:

* a :class:`Leaf` names one or more block keys. **One key is a plain block; two
  or more is a tab group** — that is the whole of the merge feature in the model,
  and the reason there is no third node kind for it. ``active`` says which tab is
  showing.
* a :class:`Split` divides its space along one axis between its children, with
  the pixel ``sizes`` the user dragged.

The **page itself is a** ``Split("v", …)`` whose children are the rows and whose
sizes are the row heights. That is not a special case bolted on the top: a
vertical split directly inside a vertical split *is* just more rows, which is
exactly what :func:`normalize` collapses. The one place the page differs from
every other split is in the renderer, where its sizes are absolute (the rows may
total more than the viewport, and the page scrolls) rather than shares of a fixed
extent.

Everything here is a pure function over frozen dataclasses, so the structural
half of a drag gesture can be tested without a widget, a window or a display
server. The Qt half only has to draw the answer.

Two rules are inherited deliberately from the code this replaces:

* **Sizes are live pixel measurements, true only of the shape they were measured
  in.** So a node *arriving* in a split clears that split's sizes rather than
  trying to slot a plausible number in beside them. Mixing a remembered size with
  a newcomer's natural hint is what once handed a moved block a sliver of the
  pinned strip.
* **Strict about where a block lives, lenient about the cosmetic numbers.** A key
  that is not a known block rejects the whole layout, because guessing would
  silently move somebody's block; a malformed ``sizes`` or ``active`` degrades to
  the default, because that only costs the page its remembered proportions.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

#: The two axes a split may divide its space along.
HORIZONTAL = "h"
VERTICAL = "v"
ORIENTATIONS = (HORIZONTAL, VERTICAL)

#: Which axis a drop on a given side divides. Dropping to the left or right of a
#: block splits horizontally; above or below, vertically.
SIDES = ("left", "right", "top", "bottom")
_SIDE_AXIS = {"left": HORIZONTAL, "right": HORIZONTAL, "top": VERTICAL, "bottom": VERTICAL}
_SIDE_AFTER = {"left": False, "right": True, "top": False, "bottom": True}

#: The edges the pinned region may sit on, and which axis its lines run along.
_VERTICAL_EDGES = ("left", "right")


@dataclass(frozen=True)
class Leaf:
    """One cell of the grid: a block, or several blocks as a tab group."""

    keys: tuple[str, ...]
    active: int = 0

    @property
    def tabbed(self) -> bool:
        """Whether this cell renders as a tab group rather than a bare block."""
        return len(self.keys) > 1

    def active_key(self) -> str:
        """The key currently showing. ``active`` is clamped, never trusted."""
        if not self.keys:
            raise ValueError("an empty leaf has no active key")
        return self.keys[max(0, min(self.active, len(self.keys) - 1))]


@dataclass(frozen=True)
class Split:
    """A run of children sharing one axis, with the sizes the user dragged.

    ``sizes`` is parallel to ``children`` and is *advisory*: an empty tuple (or
    one of the wrong length) means "no remembered proportions", and the renderer
    divides the space from the children's own hints instead.

    **A zero is a real value, not a gap.** It means "this child has no size of its
    own", which the page's own split reads as *be as tall as your content* — so a
    row nobody has dragged still grows when a skill is added to it, exactly as it
    always did, while a row somebody has dragged stays where they put it. A run of
    nothing but zeros is the same as no sizes at all and is dropped.
    """

    orientation: str
    children: tuple[Node, ...]
    sizes: tuple[int, ...] = ()

    @property
    def horizontal(self) -> bool:
        return self.orientation == HORIZONTAL

    def usable_sizes(self) -> tuple[int, ...]:
        """The remembered sizes, or ``()`` when they do not describe these children."""
        if len(self.sizes) != len(self.children) or any(size < 0 for size in self.sizes):
            return ()
        return self.sizes

    def with_children(self, children: tuple[Node, ...]) -> Split:
        """The same split over a different run of children, its sizes dropped."""
        return Split(self.orientation, children, ())


Node = Leaf | Split

#: Where a node sits: the child index at each level, from the root down.
Path = tuple[int, ...]


# -- reading the tree --------------------------------------------------------


def iter_leaves(node: Node, path: Path = ()) -> Iterator[tuple[Path, Leaf]]:
    """Every leaf, depth first and left to right, with the path that reaches it."""
    if isinstance(node, Leaf):
        yield path, node
        return
    for index, child in enumerate(node.children):
        yield from iter_leaves(child, path + (index,))


def keys(node: Node | None) -> list[str]:
    """Every block key in the tree, in reading order."""
    if node is None:
        return []
    return [key for _, leaf in iter_leaves(node) for key in leaf.keys]


def find(node: Node | None, key: str) -> Path | None:
    """The path to the leaf holding *key*, or None when it is not in this tree."""
    if node is None:
        return None
    for path, leaf in iter_leaves(node):
        if key in leaf.keys:
            return path
    return None


def at(node: Node, path: Path) -> Node:
    """The node *path* names. Raises IndexError if the path does not fit."""
    current: Node = node
    for index in path:
        if isinstance(current, Leaf):
            raise IndexError(f"path {path!r} runs past a leaf")
        current = current.children[index]
    return current


def leaf_for(node: Node | None, key: str) -> Leaf | None:
    """The leaf holding *key*, or None."""
    path = find(node, key)
    if path is None or node is None:
        return None
    found = at(node, path)
    return found if isinstance(found, Leaf) else None


def depth(node: Node) -> int:
    """How many splits deep the tree goes. A bare leaf is 0."""
    if isinstance(node, Leaf):
        return 0
    return 1 + max((depth(child) for child in node.children), default=0)


# -- keeping the tree in a canonical shape -----------------------------------


def normalize(node: Node | None) -> Node | None:
    """Reduce the tree to its canonical shape, or None when nothing is left.

    Four reductions, applied bottom up:

    * an empty leaf disappears (it is a cell holding no block);
    * a split with no children disappears, and one with a single child *becomes*
      that child — a row you dragged the last neighbour out of is not a row;
    * a split nested directly inside a split of the **same** orientation is
      spliced into its parent, so ``h(a, h(b, c))`` is ``h(a, b, c)``. This is
      what makes a vertical split inside the page mean "more rows" rather than a
      second kind of container;
    * ``active`` is clamped into the leaf's keys.

    Sizes do not survive a reduction that changes a split's children — they
    described the old shape, and are cleared rather than reinterpreted.
    """
    if node is None:
        return None

    if isinstance(node, Leaf):
        if not node.keys:
            return None
        active = max(0, min(node.active, len(node.keys) - 1))
        return node if active == node.active else replace(node, active=active)

    orientation = node.orientation if node.orientation in ORIENTATIONS else HORIZONTAL
    sizes = node.usable_sizes()
    children: list[Node] = []
    kept_sizes: list[int] = []

    for index, raw in enumerate(node.children):
        child = normalize(raw)
        if child is None:
            continue
        size = sizes[index] if sizes else 0
        if isinstance(child, Split) and child.orientation == orientation:
            # Splice the grandchildren up. Their own proportions are still true of
            # each other, but nothing relates them to their new siblings, so the
            # whole run loses its sizes.
            children.extend(child.children)
            kept_sizes.extend([0] * len(child.children))
            continue
        children.append(child)
        kept_sizes.append(size)

    if not children:
        return None
    if len(children) == 1:
        return children[0]
    if not any(kept_sizes):
        kept_sizes = []
    return Split(orientation, tuple(children), tuple(kept_sizes))


def as_page(node: Node | None) -> Split:
    """The tree as a page: a vertical split whose children are the rows.

    A page reduced to a single row is still a page, so a bare leaf (or a
    horizontal row) is wrapped rather than returned as it is. An empty page is a
    vertical split with no children, which is a legal thing to render — it is what
    a sheet with every block hidden looks like.
    """
    reduced = normalize(node)
    if reduced is None:
        return Split(VERTICAL, ())
    if isinstance(reduced, Split) and reduced.orientation == VERTICAL:
        return reduced
    return Split(VERTICAL, (reduced,))


# -- changing the tree -------------------------------------------------------


def remove(node: Node | None, key: str) -> Node | None:
    """The tree without *key*, normalized. Unchanged when the key is not in it."""
    if node is None:
        return None
    if find(node, key) is None:
        return node
    return normalize(_remove(node, key))


def _remove(node: Node, key: str) -> Node | None:
    if isinstance(node, Leaf):
        if key not in node.keys:
            return node
        index = node.keys.index(key)
        remaining = node.keys[:index] + node.keys[index + 1 :]
        if not remaining:
            return None
        # Keep looking at whatever the user was looking at: the tab to the left
        # when the active one went, and the same tab otherwise.
        active = node.active - 1 if node.active >= index and node.active > 0 else node.active
        return Leaf(remaining, active)

    sizes = node.usable_sizes()
    children: list[Node] = []
    kept: list[int] = []
    for index, child in enumerate(node.children):
        replacement = _remove(child, key)
        if replacement is None:
            continue
        children.append(replacement)
        kept.append(sizes[index] if sizes else 0)
    # The survivors keep the sizes they had. This used to drop the whole run's
    # sizes when a child left, on the reasoning that the space it freed is no
    # longer described by them — true of a *row*, where a splitter renormalises
    # the remembered numbers to its real width and so keeps the proportions the
    # user dragged, and flatly wrong of the **page**, whose sizes are absolute
    # heights that owe nothing to each other. Closing one block therefore forgot
    # the height of every other row on the sheet.
    return Split(node.orientation, tuple(children), tuple(kept))


def insert_beside(node: Node | None, key: str, target: str, side: str, *, extent: int = 0) -> Node:
    """Put *key* in its own cell on the given *side* of the cell holding *target*.

    When the target's parent already divides along the side's axis, the new cell
    joins that run; otherwise the target's cell is wrapped in a new split of the
    axis the drop asked for.

    **The newcomer takes half of the target and nothing else moves.** That is the
    promise the drop mark makes — the wash under the pointer fills half the block
    being dropped beside — and it used to be broken: the run the arrival joined had
    its remembered sizes *cleared* and was laid out afresh from every cell's own
    hint, so the block you aimed at frequently did not change size at all while its
    neighbours paid for the arrival. Halving is not the mistake that rule was
    written against ("a remembered size mixed with a newcomer's natural hint"),
    because every cell in the run ends up with an explicit number and the newcomer's
    is derived from the cell it displaced.

    A run with nothing remembered has no proportion to keep and none to halve, so it
    is still laid out from the cells' hints. *extent* is the one measurement the
    tree cannot make for itself: the target cell's live pixel size along the drop's
    axis, needed when the cell is *wrapped* in a brand-new split, since a new run
    has no sizes of its own to divide. Without it that pair falls back to its hints.

    A *target* that is not in the tree puts *key* in a row of its own at the end,
    which is the same answer the old canvas gave a block it could not place.
    """
    return insert_node_beside(node, Leaf((key,)), target, side, extent=extent)


def insert_node_beside(
    node: Node | None, arriving: Node, target: str, side: str, *, extent: int = 0
) -> Node:
    """Put a whole *arriving* cell on the given *side* of the cell holding *target*.

    The general form of :func:`insert_beside`, which is now one line of it. What
    arrives is a node rather than a key because a tab group is a cell like any
    other and has to be movable as one — the alternative was moving its blocks one
    at a time and merging them back together at the far end, which is the same
    answer arrived at through four intermediate arrangements nobody asked for.
    """
    if side not in SIDES:
        raise ValueError(f"{side!r} is not one of {SIDES}")
    if node is None:
        return arriving
    path = find(node, target)
    if path is None:
        return append_node_row(node, arriving)
    grown = _insert_at(node, path, arriving, _SIDE_AXIS[side], _SIDE_AFTER[side], int(extent))
    return normalize(grown) or arriving


def _share_extent(extent: int, after: bool) -> tuple[int, ...]:
    """*extent* divided between the cell that was there and the one arriving.

    The odd pixel goes to whichever of the two is *not* the newcomer, so a block
    dropped beside another never comes out the larger of the pair.
    """
    half = extent // 2
    return (extent - half, half) if after else (half, extent - half)


def _insert_at(node: Node, path: Path, arriving: Node, axis: str, after: bool, extent: int) -> Node:
    if not path:
        # The target *is* this node: wrap it in a split of the requested axis. The
        # pair divides the extent the target had, so the newcomer takes half of it
        # and nothing outside this cell moves at all.
        pair = (node, arriving) if after else (arriving, node)
        return Split(axis, pair, _share_extent(extent, after) if extent > 0 else ())
    index, rest = path[0], path[1:]
    if not isinstance(node, Split):
        raise IndexError("path runs past a leaf")
    child = node.children[index]
    if not rest and node.orientation == axis:
        # The run already divides the way this drop wants, so join it rather than
        # nesting another split inside it.
        slot = index + 1 if after else index
        children = node.children[:slot] + (arriving,) + node.children[slot:]
        sizes = node.usable_sizes()
        if not sizes:
            # Nothing remembered about this run, so there is no proportion to keep
            # and nothing to halve.
            return node.with_children(children)
        # The target gives up half of *its own* share and every other cell in the
        # run keeps exactly what it had. A zero is not a size but "take your
        # content's" — the page's own answer for a row nobody has dragged — so it
        # divides into two of itself rather than into two noughts of a number.
        divided = _share_extent(sizes[index], after) if sizes[index] > 0 else (0, 0)
        return Split(node.orientation, children, sizes[:index] + divided + sizes[index + 1 :])
    replaced = _insert_at(child, rest, arriving, axis, after, extent)
    children = node.children[:index] + (replaced,) + node.children[index + 1 :]
    # The child's own extent did not change, so this run keeps its proportions.
    return Split(node.orientation, children, node.usable_sizes())


def append_row(node: Node | None, key: str, index: int | None = None) -> Split:
    """Add *key* as a row of its own, at *index* (the end when None)."""
    return append_node_row(node, Leaf((key,)), index)


def append_node_row(node: Node | None, arriving: Node, index: int | None = None) -> Split:
    """Add a whole *arriving* cell as a row of its own — see :func:`insert_node_beside`.

    Every other row keeps the height it had, and the newcomer states **zero**, which
    on the page means "be as tall as your content" — the same thing a row nobody has
    dragged says. Dropping the sizes here instead forgot every height on the sheet
    the moment a block was dragged into a row of its own, which is not something a
    row *elsewhere* on the page has any part in.
    """
    page = as_page(node)
    slot = len(page.children) if index is None else max(0, min(index, len(page.children)))
    children = page.children[:slot] + (arriving,) + page.children[slot:]
    sizes = page.usable_sizes()
    if not sizes:
        return Split(VERTICAL, children, ())
    return Split(VERTICAL, children, sizes[:slot] + (0,) + sizes[slot:])


def merge_into(node: Node | None, key: str, target: str) -> Node | None:
    """Move *key* into the cell holding *target*, making it a tab group.

    The arriving block becomes the active tab, because a block you just dropped
    somewhere is the one you want to look at. Merging a block into the cell it is
    already in is a no-op rather than an error — it is what a drop that did not
    really move anything should do.
    """
    if node is None or key == target:
        return node
    target_path = find(node, target)
    if target_path is None:
        return node
    if find(node, key) == target_path:
        return node

    detached = remove(node, key)
    if detached is None:
        return node
    path = find(detached, target)
    if path is None:  # the target's cell went with the removal; nothing to join
        return node
    leaf = at(detached, path)
    if not isinstance(leaf, Leaf):
        return node
    grown = Leaf(leaf.keys + (key,), len(leaf.keys))
    return normalize(_replace_at(detached, path, grown))


def split_out(node: Node | None, key: str, side: str = "right") -> Node | None:
    """Pull *key* out of its tab group into a cell of its own beside it.

    Only meaningful for a key sharing a leaf with others; a block already alone in
    its cell comes back untouched, which is what a tab dragged off a bar holding
    one tab should do.
    """
    if node is None:
        return None
    path = find(node, key)
    if path is None:
        return node
    leaf = at(node, path)
    if not isinstance(leaf, Leaf) or not leaf.tabbed:
        return node
    anchor = next(other for other in leaf.keys if other != key)
    detached = remove(node, key)
    if detached is None:
        return node
    return insert_beside(detached, key, anchor, side)


def move(node: Node | None, key: str, target: str, side: str) -> Node | None:
    """Take *key* out of wherever it is and put it beside *target*."""
    if node is None or key == target:
        return node
    detached = remove(node, key)
    if detached is None or find(detached, target) is None:
        return node
    return insert_beside(detached, key, target, side)


def _detach_leaf(node: Node | None, keys: Sequence[str]) -> tuple[Node | None, Leaf | None]:
    """Take the cell holding *keys* out whole, and hand it back with the rest.

    The cell itself is returned rather than rebuilt from the keys, so which tab was
    active travels with it — a group put down somewhere else showing a different
    block than it showed when it was picked up is a small thing that feels like a
    bug every time.
    """
    wanted = tuple(keys)
    if node is None or not wanted:
        return node, None
    path = find(node, wanted[0])
    if path is None:
        return node, None
    leaf = at(node, path)
    if not isinstance(leaf, Leaf) or leaf.keys != wanted:
        return node, None
    detached: Node | None = node
    for key in wanted:
        detached = remove(detached, key)
    return detached, leaf


def move_leaf(
    node: Node | None, keys: Sequence[str], target: str, side: str, *, extent: int = 0
) -> Node | None:
    """Take the whole cell holding *keys* out and put it beside *target*.

    :func:`move`'s counterpart for a tab group, and the model half of dragging one
    by its bar. A *target* inside the cell being moved is a no-op — a group dropped
    on itself has not gone anywhere. *extent* halves the target, exactly as it does
    for :func:`insert_beside`.
    """
    if node is None or target in tuple(keys):
        return node
    detached, leaf = _detach_leaf(node, keys)
    if leaf is None or detached is None or find(detached, target) is None:
        return node
    return insert_node_beside(detached, leaf, target, side, extent=extent)


def move_leaf_to_row(node: Node | None, keys: Sequence[str], index: int) -> Node | None:
    """Take the whole cell holding *keys* out and give it a row of its own at *index*.

    The index counts rows of the page **as it stands**, which is how a drop names
    the seam it landed on; removing the cell first can take a row with it, so the
    seam is re-measured against what is left rather than trusted.
    """
    if node is None:
        return node
    page = as_page(node)
    before = len(page.children)
    detached, leaf = _detach_leaf(page, keys)
    if leaf is None:
        return node
    page = as_page(detached)
    lost = before - len(page.children)
    return append_node_row(page, leaf, max(0, index - lost) if index > 0 else 0)


def merge_leaf_into(node: Node | None, keys: Sequence[str], target: str) -> Node | None:
    """Move the whole cell holding *keys* into the cell holding *target*.

    One group, holding everything both cells held. The arriving blocks keep their
    order and the one that was showing goes on showing, which is the same promise
    :func:`merge_into` makes for a single block.
    """
    wanted = tuple(keys)
    if node is None or target in wanted:
        return node
    if find(node, target) is None:
        return node
    detached, leaf = _detach_leaf(node, wanted)
    if leaf is None or detached is None:
        return node
    path = find(detached, target)
    if path is None:  # the target's cell went with the removal
        return node
    into = at(detached, path)
    if not isinstance(into, Leaf):
        return node
    grown = Leaf(into.keys + leaf.keys, len(into.keys) + leaf.active)
    return normalize(_replace_at(detached, path, grown))


def reorder_leaf(node: Node | None, keys: Sequence[str]) -> Node | None:
    """The cell holding *keys* re-dealt into that order, still showing the same block.

    A tab bar reorders itself when a tab is dragged along it, and the tree has to
    follow or the order is forgotten the moment the page is rebuilt or saved.
    ``active`` is carried by *block* rather than by index, since the whole point is
    that the indices have moved.

    A *keys* that is not exactly one cell's contents is refused — this is a
    re-dealing of one cell, never a way to move a block between two.
    """
    wanted = tuple(keys)
    if node is None or not wanted:
        return node
    path = find(node, wanted[0])
    if path is None:
        return node
    leaf = at(node, path)
    if not isinstance(leaf, Leaf) or set(leaf.keys) != set(wanted) or len(leaf.keys) != len(wanted):
        return node
    showing = leaf.active_key()
    return _replace_at(node, path, Leaf(wanted, wanted.index(showing)))


def set_active(node: Node | None, key: str) -> Node | None:
    """Bring *key* to the front of its tab group."""
    if node is None:
        return None
    path = find(node, key)
    if path is None:
        return node
    leaf = at(node, path)
    if not isinstance(leaf, Leaf):
        return node
    return _replace_at(node, path, replace(leaf, active=leaf.keys.index(key)))


def set_sizes(node: Node | None, path: Path, sizes: Sequence[int]) -> Node | None:
    """Record the pixel sizes a handle drag settled on for the split at *path*."""
    if node is None:
        return None
    split = at(node, path)
    if not isinstance(split, Split) or len(sizes) != len(split.children):
        return node
    return _replace_at(node, path, replace(split, sizes=tuple(int(size) for size in sizes)))


def _replace_at(node: Node, path: Path, replacement: Node) -> Node:
    if not path:
        return replacement
    index, rest = path[0], path[1:]
    if not isinstance(node, Split):
        raise IndexError("path runs past a leaf")
    child = _replace_at(node.children[index], rest, replacement)
    children = node.children[:index] + (child,) + node.children[index + 1 :]
    return Split(node.orientation, children, node.usable_sizes())


# -- persistence -------------------------------------------------------------


def to_dict(node: Node | None) -> dict | None:
    """*node* as plain JSON-able data."""
    if node is None:
        return None
    if isinstance(node, Leaf):
        return {"type": "leaf", "keys": list(node.keys), "active": node.active}
    return {
        "type": "split",
        "orientation": node.orientation,
        "children": [to_dict(child) for child in node.children],
        "sizes": list(node.sizes),
    }


def from_dict(value: object, known: set[str]) -> Node | None:
    """Parse a persisted node, or None when it is unusable.

    Strict about the block keys — an unknown one, or one named twice, returns None
    and the caller falls back to the default arrangement, because a layout that
    half-describes where somebody's blocks are is worse than no layout at all.
    Lenient about ``sizes`` and ``active``: both are cosmetic, and
    :func:`normalize` clamps whatever survives.
    """
    seen: set[str] = set()
    return normalize(_from_dict(value, known, seen))


def _from_dict(value: object, known: set[str], seen: set[str]) -> Node | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("type")
    if kind == "leaf":
        raw = value.get("keys")
        if not isinstance(raw, list) or not raw:
            return None
        parsed: list[str] = []
        for key in raw:
            if not isinstance(key, str) or key not in known or key in seen:
                return None
            seen.add(key)
            parsed.append(key)
        active = value.get("active")
        active = active if isinstance(active, int) and not isinstance(active, bool) else 0
        return Leaf(tuple(parsed), active)
    if kind != "split":
        return None
    orientation = value.get("orientation")
    if orientation not in ORIENTATIONS:
        return None
    raw_children = value.get("children")
    if not isinstance(raw_children, list):
        return None
    children: list[Node] = []
    for raw in raw_children:
        child = _from_dict(raw, known, seen)
        if child is None:
            return None
        children.append(child)
    if not children:
        return None
    raw_sizes = value.get("sizes")
    sizes: tuple[int, ...] = ()
    if isinstance(raw_sizes, list) and all(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0 for size in raw_sizes
    ):
        sizes = tuple(raw_sizes)
    return Split(orientation, tuple(children), sizes)


# -- migration ---------------------------------------------------------------


def rows_to_page(rows: Sequence[Sequence[str]]) -> Split:
    """A ``list[list[str]]`` of rows as a page tree.

    The shape the sheet held its arrangement in for its whole life so far, and
    what :func:`migrate_v7` needs for the page half. A row of one block is a bare
    leaf; a row of several is a horizontal split of leaves.
    """
    children: list[Node] = []
    for row in rows:
        cells = tuple(Leaf((key,)) for key in row)
        if not cells:
            continue
        children.append(cells[0] if len(cells) == 1 else Split(HORIZONTAL, cells))
    return Split(VERTICAL, tuple(children))


def lines_to_region(lines: Sequence[Sequence[str]], edge: str) -> Node | None:
    """The pinned strip's ``lines`` as a region tree.

    The strip's lines run *along* it and its blocks sit *across* each line, so
    which axis is which follows the edge: a strip down the left or right stacks
    its lines vertically, and one along the top or bottom lays them out in a row.
    """
    along = VERTICAL if edge in _VERTICAL_EDGES else HORIZONTAL
    across = HORIZONTAL if along == VERTICAL else VERTICAL
    children: list[Node] = []
    for line in lines:
        cells = tuple(Leaf((key,)) for key in line)
        if not cells:
            continue
        children.append(cells[0] if len(cells) == 1 else Split(across, cells))
    if not children:
        return None
    return normalize(Split(along, tuple(children)))


def region_lines(node: Node | None, edge: str) -> list[list[str]]:
    """A region tree back as the strip's ``lines`` — the inverse of
    :func:`lines_to_region`.

    The strip's own widgets still speak in lines while the grid grows into it, so
    this is the bridge: the model is a tree everywhere, and the one view that has
    not caught up is handed the shape it understands. A tree deeper than the strip
    can draw is flattened rather than refused — a line is every key under one
    child, in order — which is the honest lossy answer while the two coexist.
    """
    if node is None:
        return []
    along = VERTICAL if edge in _VERTICAL_EDGES else HORIZONTAL
    if isinstance(node, Leaf) or node.orientation != along:
        return [keys(node)]
    return [keys(child) for child in node.children]


def migrate_v7(model: dict, known: set[str]) -> dict | None:
    """A schema-7 arrangement as a schema-8 one, or None when it will not convert.

    Version 7 held the page as ``rows`` and the strip as ``pinned.lines``, both
    flat lists of block keys, and every one of those has an exact reading as a
    tree — so there is no reason to throw away a page somebody arranged. What it
    cannot carry over is the strip's ``align``, which resizable cells replace, and
    the per-line pixel sizes, which described a layout engine that no longer
    exists.

    Returns the schema-8 body *without* ``version``; the caller stamps that, so
    this stays a pure translation.
    """
    if not isinstance(model, dict) or model.get("version") != 7:
        return None
    rows = model.get("rows")
    floating = model.get("floating")
    hidden = model.get("hidden")
    if not (isinstance(rows, list) and isinstance(floating, dict) and isinstance(hidden, list)):
        return None
    if not all(isinstance(row, list) and all(isinstance(k, str) for k in row) for row in rows):
        return None

    raw_pinned = model.get("pinned")
    pinned = raw_pinned if isinstance(raw_pinned, dict) else {}
    edge = pinned.get("edge")
    edge = edge if edge in _VERTICAL_EDGES + ("top", "bottom") else "right"
    raw_lines = pinned.get("lines")
    lines = [
        line
        for line in (raw_lines if isinstance(raw_lines, list) else [])
        if isinstance(line, list) and all(isinstance(key, str) for key in line)
    ]
    extent = pinned.get("extent")
    extent = (
        extent if isinstance(extent, int) and not isinstance(extent, bool) and extent > 0 else 0
    )

    page = as_page(rows_to_page(rows))
    region = lines_to_region(lines, edge)
    placed = keys(page) + keys(region)
    if len(placed) != len(set(placed)):  # a block in two places is not ours to fix
        return None
    if not set(placed) <= known:
        return None

    body: dict = {
        "instances": model.get("instances") if isinstance(model.get("instances"), list) else [],
        "page": to_dict(page),
        "region": {"edge": edge, "extent": extent, "root": to_dict(region)},
        "floating": floating,
        "hidden": list(hidden),
    }
    anchors = model.get("hidden_anchors")
    if isinstance(anchors, dict):
        body["hidden_anchors"] = anchors
    return body
