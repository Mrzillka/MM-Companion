# Shared UI utilities and view modes

Matters when adding widgets.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

The `ui/` package has a small support layer that section code is expected to go
through rather than reinvent. When building new sheet widgets, use it:

- `ui/widgets.py` — shared factories (`make_spin_box`, `make_double_spin_box`,
  `readonly_item`, `hline_separator`) and the shared inline style snippets
  (`muted_style`, `tinted_style`, `BOLD_STYLE`) that keep construction consistent
  and wheel-guarded. Build spin boxes and read-only table cells through these, not
  by hand.
- `ui/wheel_guard.py` — `guard_wheel(*widgets)` stops nested spin boxes, combo
  boxes, and inner tables from hijacking the page scroll: a guarded widget only
  reacts to the wheel once it has keyboard focus, otherwise the wheel is
  redirected to the enclosing page. `make_spin_box` guards by default. The guard
  walks up to the **outermost** enclosing scroll area, which is the single page
  scroll area that `CharacterSheet` owns around the whole canvas (blocks have no
  inner scroll areas of their own).
- `ui/lock.py` — `set_widget_locked(widget, locked)` implements the read-only
  **view** mode. Locking is *not* `setEnabled(False)` (which greys a control
  out); a locked field keeps showing its value but sheds its input chrome
  (frame, spin buttons, dropdown arrow) so it reads like a label. Combo boxes
  have no native read-only mode, so it installs an event-filter interaction
  blocker.
- `ui/flow_layout.py` — a reflowing layout for wrapping widget rows. **Host a
  `FlowLayout` in a `FlowContainer`, never a bare `QWidget`.** The layout answers
  `hasHeightForWidth` with *no*, on purpose: Qt evaluates that at the parent's
  **hint** width, which for a flow is one item's, so every item claims a row of its
  own and the surplus goes to whatever else shares the page. `FlowContainer` pins
  its `minimumHeight` to what the flow really wraps to at the width it was *given*
  instead, and re-takes that pin as items come and go. A bare host reports one row
  and everything below it is clipped — a `QFormLayout` row showing one of Enhanced
  Senses' twenty check boxes, a `widgetResizable` `QScrollArea` showing the first
  row of the launcher's character library with no scroll bar to reach the rest.
  Neither container asks a second time, which is why the host has to answer right.
- A word-wrapped `QLabel` inside a composite widget will be sized for one line
  and clipped: `heightForWidth` only reaches it if every layout in between agrees
  to ask, and `QFormLayout` does not reliably ask. Pin the column to a width token
  and set the label's minimum height from `label.heightForWidth(width)` — see
  `ui/settings/token_editor.py::_field_label`.
- Tests build real windows, and `conftest.py` tears them down after each one.
  `processEvents()` alone does **not** run deferred deletions, so the teardown also
  sends `QEvent.Type.DeferredDelete` explicitly — without it every window built all
  session stays alive and each new application stylesheet re-polishes all of them,
  which is quietly quadratic.

The Lock pattern is threaded top-down: `MainWindow` owns the checkable lock
action, `CharacterSheet.set_locked(bool)` fans out to each section's
`set_locked`, and sections call `set_widget_locked` on their editable widgets.
The sheet **starts locked** (a read-only viewer, not an editor). Any new section
with editable widgets should expose `set_locked` and be wired into
`CharacterSheet.set_locked`. That action lives **on the menu bar**, not in a
menu — `menu_bar.addAction(…)` with no submenu, so one click toggles it — and its
🔒/🔓 glyph *is* the state read-out (`_show_lock_state`). It is a play-time view
switch reached constantly, not a preference; a GM's read-only `gm_view` window
returns before it is built and so has none.

**Toggling the lock is a resize, not only a costume change**, and the width must not be
part of it. A locked field sheds its border and its padding, and several blocks hide
their editing entry points outright — so the block's own minimum moves. The window does
*not* resize itself on a toggle, so a block that grew when unlocked simply clipped
against a window the user had already sized. The rule is therefore: **only the height may
change**. Most blocks get that free, their `min_width` floor in `block_sizes.json`
already covering the unlocked content; the Equipment block did not (three "Add…" buttons
abreast are 360px against a 240px floor) and wraps them in a `FlowContainer` instead.
`tests/test_lock_geometry.py` asserts the invariance per block, on the *frame* — the
floors mask the section-level deltas, so a preset that lowers one would unmask it there.
`BlockFrame.set_locked` raises the geometry invalidation and `CharacterSheet.set_locked`
recomputes the page's minimum, since a lock toggle is not an `arrangement_changed` and
the page's minimum is an explicit number behind a `QScrollArea` — the same link
`PinnedPanel.eventFilter` exists to bridge.

`set_widget_locked` sheds a field's chrome with a small **widget-level**
stylesheet (`_LOCKED_SPIN_STYLE` / `_LOCKED_COMBO_STYLE`) as well as
`setFrame`/`setButtonSymbols`, because a styled preset's application sheet states
a border, a radius and a padding that outrank both. It is deliberately not a
`[locked="true"]` rule in the theme QSS: Classic emits almost no sheet, so an
app-level rule would exist under some presets and not others.
