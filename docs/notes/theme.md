# The theme layer

Matters whenever you write a colour or a size.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

**Never hardcode a colour, radius, border width, column minimum or font size in
widget code.** All of them are named **tokens** read from the active theme
preset — the same rule for the *look* that "no game rules in Python" is for the
*content*. The shape:

- `src/mm_companion/ui/theme/` — `tokens.py` (the `Theme`/`Chrome` records, plus
  `rgba`/`contrast_ratio`/`measurement_backdrops`; pure data, no Qt), `loader.py`
  (discovery, `extends` resolution, caching, *and* the workspace store —
  `theme_to_dict`/`save_workspace_theme`/`delete_workspace_theme`/
  `unique_theme_id`/`is_workspace_theme`/`shadows_bundled`), `qss.py` (builds the
  app stylesheet), `palette.py` (builds the `QPalette`), `token_meta.py` +
  `token_meta.json` (friendly labels/hints/grouping for the editor — presentation
  only, never a gate on which tokens exist), and `__init__.py` (the API).
- **Read a token where you use it**: `theme.color("tint.worse")`,
  `theme.metric("radius.card")`, `theme.font_size("size.card-name")`,
  `theme.wash("accent", 0.10)` (a translucent fill of the same hue),
  `theme.box("card.margins")` (a 4-tuple), `theme.asset("die")` (which bundled
  drawing — see `ui/svg_assets.py`). Never cache one in a module constant
  — a preset switch would not reach it. An unknown name raises `UnknownToken`
  with a did-you-mean.
- Repeated snippets live in `ui/widgets.py`: `muted_style(italic=…)`,
  `tinted_style(token, bold=…)`, `BOLD_STYLE`.
- **Presets** are JSON: bundled in `ui/theme/themes/*.json`, plus anything in the
  workspace `themes/` dir (which wins on an id clash, and whose parse failure is
  skipped, never fatal). A preset may `extends` another and restate only what it
  changes. `classic` is the default and reproduces the historical native look;
  `slate-dark`, `parchment-light` and `crimson-gold` (built around the medallion
  artwork) are `chrome.mode: "styled"`. A `_`-prefixed
  key inside a token map is an inline comment and is dropped, not parsed as a
  token. A preset the Settings window writes is a **full snapshot** (every
  resolved token, no `extends`) so it stays portable; the price is that it cannot
  inherit a token added later, which `theme._lookup` pays by falling back to the
  default preset's value before raising.
- **Three mechanisms, and which does what.** `theme.apply(app)` installs all
  three, and mixing them up is the main way to break this:
  1. **Palette** (`styled` only) — colour for ordinary widgets, reaching them
     through Qt's own inheritance. It is also what any `palette(role)` token
     value resolves against. A `system` preset installs none, so the app keeps
     following the OS light/dark setting.
  2. **Application font** — the family only. Sizes never go here.
  3. **Stylesheet** — geometry, the object-named block chrome, and the menu/tab
     classes the native Windows style paints from the *system* theme and which
     therefore ignore the palette.
- **Four rules, each guarded by a test in `tests/test_theme_qss.py`:**
  1. Never select a bare `QFrame`/`QLabel`/`QGroupBox`/`QScrollArea` — every
     nested separator and label inherits it. Use an object name or a class that
     names a whole component.
  2. Never set `font-size` in a stylesheet. It outranks the widget's `QFont`,
     which is what the powers cards animate through; use
     `QFont.setPointSizeF(theme.font_size(...))`.
  3. A semantic tint must clear **3.0:1** against its background — for a
     `system` preset that means against *both* a light and a dark window, since
     it cannot know which it is on. `tests/test_theme.py` enforces it per preset.
  4. **State a complex widget's box, give its arrow column back.** One
     `border`/`padding`/`background` on a `QSpinBox` or a `QComboBox` makes
     `QStyleSheetStyle` compute `SC_SpinBoxEditField` from the box's own padding
     rect, which knows nothing about the arrows — so the edit field spanned the
     whole widget and the `QLineEdit` was laid over both arrow buttons. The arrows
     still painted, still looked right, and the child under the cursor was the
     line edit, which took every click and ignored it. `qss._arrow_column_rules`
     restores a `padding-right` and is emitted for **every** preset, because the
     focus ring alone is box enough to trigger it (Classic was fine until a field
     was clicked into). Two things not to redo: placing the buttons yourself stops
     the platform drawing its indicator inside them (working arrows nobody can
     see), and Qt renders each border edge as a rectangle rather than mitring
     them, so the CSS-triangle substitute comes out square. **How wide that column
     is belongs to the style, not the theme** — 50px under `windows11`, 15px under
     `Fusion` — so `theme.arrow_columns(app)` measures it once and `qss.build`
     takes it; there is deliberately no token. A `make_spin_box(buttons=False)` box
     carries `theme.ARROWLESS_PROPERTY` so the sheet hands the room straight back.
     `tests/test_input_arrow_columns.py` clicks a real arrow, per preset, across
     every base style Qt ships.
- A plain `QWidget` ignores a stylesheet `background` unless it sets
  `WA_StyledBackground` (a `QFrame` honours it natively). If a wash you applied
  doesn't paint, that is why.
- **A checkable `QPushButton` must say what "checked" looks like itself.** The
  sheet states a push button's box (`_chrome_rules`) but emits no
  `QPushButton:checked` — and once a box is stated, `QStyleSheetStyle` stops
  painting the platform's sunken panel, so a lit segment paints exactly like an
  unlit one. Adding the rule app-side would not fix it either, since Classic emits
  no widget chrome at all. So a segmented control carries its own widget-level
  stylesheet from tokens *every* preset defines — `_mode_toggle_style` in
  `ui/sections/powers.py` is the worked example, the same bargain `ui/lock.py` and
  `CompactOverlayButton` strike. `QToolButton:checked` *is* in the sheet; push
  buttons are the gap.
- **Two more of the same trap, both found by a *text* tool button.** The Notes
  toolbar's Preview toggle is the app's first text-only `QToolButton`, and it showed
  that `QToolButton` stated **no `color` in either state**: Qt fell back to white, which
  was invisible on Parchment — where the block title bars had all but lost their
  `🖈 ↗ ✕` — and a *checked* one painted no label at all on the dark presets. Both
  states state `color` now. And the **focus ring must give back as padding whatever it
  takes as border**: a widget's size hint comes from its *resting* rule, so a 2px ring
  replacing a 1px border stole two pixels from the caption ("Open…" came out "Open..").
  `_focus_padding` subtracts the difference, styled presets only — Classic states no
  resting padding to correct, and its buttons carry the platform's 80px minimum so they
  had tens of pixels of slack to lose. `QToolButton`'s resting border is `focus.width`
  for the same reason, since it has no padding to give back. Guarded as rules in
  `tests/test_theme_qss.py`; the *label* half is guarded again as paint in
  `tests/test_button_paint.py`, which must build a **real** Notes block (a standalone
  button reads fine even with the rule gone — the cascade it sits in is what matters)
  and is style-dependent, so it only bites on a real style, not under `offscreen`. The
  *ring* half has no paint test on purpose: two pixels is below what counting ink can
  tell apart, and one written for it passed with the bug present.
- **The same trap, one level up: state a colour and you own its states.** The
  menus block states a flat `color` on `QMenuBar`/`QMenu` (it has to — the native
  Windows style paints menu chrome from the *system* theme and ignores the
  palette), and that alone stopped `QStyleSheetStyle` painting a **disabled**
  entry any differently. It went unnoticed until the bar carried an action that is
  routinely disabled — Undo and Redo on an empty history — which is a button that
  looks live and does nothing. So the block also emits
  `QMenuBar::item:disabled` / `QMenu::item:disabled` in `text.muted`. Classic
  states no colour and keeps the platform's own painting, which is why the rule
  test skips a preset that states nothing rather than requiring it everywhere.
  Guarded twice, on the `test_input_arrow_columns` precedent: the *rule* in
  `tests/test_theme_qss.py`, the *consequence* in `tests/test_menu_disabled_paint.py`,
  which paints the bar and measures the glyph's ink.
- `ui/drop_feedback.py` — `DropFeedback` gives one drop target its idle / accept
  / **reject** styling from tokens. Use it in a `dragEnterEvent` instead of
  open-coding a highlight, and call `show_reject()` on the else branch: a bare
  `event.ignore()` is invisible whenever an ancestor accepts the drag. Its
  counterpart `DropIndicator` (same module) is the thin accent insert line —
  dress the *target* with the first, mark the *place* with the second.
- `ui/block_sizes.json` is the *baseline* for block bounds; a preset's `blocks`
  map overrides any bound. The GM window's blocks live there too, under `gm_`
  keys.
- The look is changed in the **Settings window** (`ui/settings/`, opened from a
  sheet's `Settings ▸ Preferences…`, the GM window's, or the launcher's Settings
  button): a `QListWidget` nav over a `QStackedWidget`, whose pages come from
  `window.PAGES` — `GeneralPage`, `ThemePage` and `GMPage`. Adding another area of
  settings is an
  entry in that tuple plus a `SettingsPage` subclass (`page.py`: `title`,
  `is_dirty`, `save`, `discard`, `needs_restart`). Which page it *opens on* is the
  caller's to say (`SettingsWindow(page=GMPage.title)`), which is how the GM window
  lands on its own rather than on the sheet's; a caller that names none gets the
  **first**, so the tuple's order is a decision and not an accident.
- The Themes page separates two things on purpose. **Picking** a preset writes
  through at once (`set_active_theme`), as the old menu did. **Editing** one is a
  draft: `TokenEditor` (`ui/settings/token_editor.py`) generates a form by walking
  the preset's token maps and choosing a widget from the *shape of each value* —
  never a hardcoded list, so a token added later or by a mod appears on its own.
  Each edit previews live through `theme.set_preview(draft, app)`, debounced;
  nothing is written until Save, and `closeEvent` always calls `discard()` so a
  preview never outlives its window. A filter box above the form
  (`TokenEditor.set_filter`) matches every word against the token's own dotted
  name *and* its label and hint, folding away any group box it empties; it
  survives a reload, so picking a preset does not silently widen the form back
  out under a filter that still reads `accent`. Then the usual relaunch offer
  (`ui/app_restart.py`) for widgets that styled themselves in their constructors —
  the same bargain the Mod Manager strikes.
- Bundled presets are shown **locked** (`ui/lock.py`) rather than hidden — they
  are the readable documentation of what each token is for — and Duplicate is how
  you get an editable one. `unique_theme_id` treats bundled ids as taken so a copy
  never accidentally shadows a built-in; a workspace file that deliberately does
  is still supported and the page labels its button "Revert to built-in".
- Two guards worth knowing before adding a token: a colour a `theme.wash(...)` is
  derived from must be a literal (mark it `"washed": true` in `token_meta.json`, or
  the editor will let a `palette(role)` through and it will raise inside a card's
  paint path), and `qss._chrome_rules` requires the `surface.*`/`text.primary`/
  `border.block` colours with no fallback, so flipping a Classic-derived draft to
  `styled` offers to borrow them from a shipped preset rather than raising.
- A whole new **token group** (`assets` is the fifth, after `colors`/`metrics`/
  `typography`/`blocks`) is five edits and **all five are load-bearing**: a field on
  `Theme`, the name in `loader._TOKEN_GROUPS` (which alone buys parse validation,
  `extends` merging and comment-stripping), passing it in `loader._build`, emitting it
  in `loader.theme_to_dict` — miss that one and the group silently vanishes the first
  time the Settings window saves a preset — and an accessor in `theme/__init__.py`. A
  top-level record like `chrome` is the wrong shape for anything inheritable: `_build`
  reads chrome from the preset's *own* raw dict, so it does **not** come down an
  `extends` chain.
- Screenshot it with `driver.py settings` / `settings-demo` (see the
  `run-mm-companion` skill).

## Bundled SVG artwork

- `ui/svg_assets.py` renders the bundled SVG artwork — the d20 and the hero-point pips —
  to pixmaps. **Eagerly**, at the screen's device pixel ratio: `QIcon` reads an SVG path
  lazily, at paint time, and `importlib.resources.as_file`'s extraction of a zipped
  install is gone by then. It also does the aspect-ratio fitting itself, since
  `QSvgRenderer` stretches to whatever rectangle it is handed and the d20 is not square.
  Each drawing comes in **variants**, held in the `DIE_VARIANTS` / `HERO_POINT_VARIANTS`
  registries, and **which one is a theme token** — `theme.asset("die")` /
  `theme.asset("hero-point")`, read at the call site (`d20_pixmap`,
  `HeroPointsWidget._render`) and never kept in a constant. The split is deliberate: this
  module owns *what drawings exist*, the preset owns *which*, so a hand-edited theme file
  names a variant id and can never point the renderer at an arbitrary path. An unknown id
  falls back rather than raising — this resolves inside a paint path. Adding a drawing is
  a file in `ui/assets/` plus an entry in the registry; the Settings combo lists it on its
  own (a friendly name in `token_editor._VARIANT_LABELS` is optional).
