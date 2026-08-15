# The Notes block

Matters when touching Notes, or adding a block there can be two of.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

A tabbed markdown editor over ordinary `.md` files in the workspace, and the one
block the sheet can have **more than one of**. `docs/` has no separate map; this is it.

- **A note belongs to no character.** `core/notes.py` (pure Python) is the only seam
  to the workspace `notes/` dir — `create_note`, `read_note`, `write_note` (atomic:
  temp file + `os.replace`, because it runs on an autosave timer), `store_note`,
  `rename_note`, `list_notes`, `resolve_note_path`. All of it is modelled on
  `library.resolve_image_path`/`_store_image` and reuses their `slugify`/`unique_path`
  (promoted to public for it). A character records only *which* notes its sheet has
  open, so the same file can be open on two sheets, and nothing garbage-collects an
  orphaned note — exactly as nothing prunes an unreferenced image.
- **The split that shapes everything else.** `Character.notes` is
  `dict[block key -> NotesState]` (`files` in tab order + the focused `active`),
  omitted from `to_dict()` while empty so an older save round-trips unchanged.
  Opening, closing or reordering a tab **is** a character edit: undoable, and it marks
  the sheet dirty. The markdown **inside** a note is not — it autosaves to its own file
  on a debounce and `Ctrl+Z` does not walk a paragraph back. Same bargain the portrait
  strikes between `image_path` and the pixels, and it is what makes a note shareable.
  `Character.restore` needs no change: it is driven by `dataclasses.fields`, so the
  block must mutate `character.notes` in place and never rebind it.
- **One note means no tab bar.** One tab is not a choice, and a strip of chrome that
  never changes is a row of the block's height spent saying nothing; the note's name is
  on the block's title bar either way, which is what makes hiding the bar safe. Hiding it
  takes the per-tab `✕` with it, so the toolbar's **Close** appears in its place — in the
  left group with Open…/New/Import… because it acts on a note like they do, and *not*
  beside the preview toggle on the right, where a `✕` would sit directly under the title
  bar's, which closes the whole block.
- **The preview toggle is a word, not a glyph** — `Preview` / `Edit`, swapping so the
  label is always the action, the way the lock's `🔒`/`🔓` is. A glyph is right on a
  *title bar*, where there is room for nothing else and the same three marks recur on
  every block until they are learned; it is wrong in a toolbar beside four text buttons,
  where one small symbol reads as neither a label nor an icon (`👁`, then `▤`/`✎`, were
  both tried and both just noise at real size). Two things it needs: a **`QToolButton`**,
  since the sheet states a push button's box and emits no `QPushButton:checked` so a
  checked one paints identically — the same trap `_mode_toggle_style` documents — and a
  **held minimum width**, taken by asking the button for each label's `sizeHint` rather
  than measuring the text and guessing the chrome (the guess was 12px short, and the
  button sits after a stretch, so it jumped out from under the cursor that clicked it).
- **The editor dims markers, it does not hide them** (`ui/notes/highlighter.py`).
  A `QPlainTextEdit` under a `MarkdownHighlighter`: headings large and bold, `**bold**`
  bold, code monospace on an accent wash, links accented — with the markers themselves
  (`##`, `**`, the backticks) painted in `text.muted.rich` on every block **but the one
  the caret is in**, where they come back to full strength (`set_active_block`, two
  `rehighlightBlock` calls per cursor move). Obsidian *hides* a marker, which Qt has no
  supported way to do: a `QSyntaxHighlighter` paints characters, it cannot remove them,
  and the alternatives (rewriting the document per paragraph on every cursor move, a
  custom `QAbstractTextDocumentLayout`) cost the native undo stack, selection across
  paragraphs, and a relayout per keystroke. Dimming also keeps a property concealment
  cannot: **nothing ever changes width**, so the text never reflows under the caret.
  Note `text.muted.rich` and not `text.muted` — the plain one is `palette(placeholder-text)`
  under Classic, and a `QTextCharFormat` needs a real colour now.
- **Preview is Qt's own** — `document().setMarkdown(…, MarkdownDialectGitHub)` in a
  `QTextBrowser`, so it costs no parser and no dependency, rendered on the toggle rather
  than per keystroke. One wrinkle worth remembering: `setMarkdown` **bakes `#0000ff`
  into each anchor as it parses**, so the palette's `Link` role is never consulted and
  `setDefaultStyleSheet` (which only applies to `setHtml`) never sees the document.
  `_recolour_links` walks the fragments afterwards; on a dark preset the default is
  otherwise unreadable.
- **This block scrolls, and that is the exception it looks like.** Every other block
  shows all of its content and lets the page scroll; a note has no bound, so the block
  takes `block_sizes.json`'s `notes` height and the text scrolls inside it — the same
  call the roll history and `gm_rolls` already make. More room comes from floating or
  pinning it.
- Three new theme tokens, added to `classic.json` (which is also the `_lookup` fallback,
  so one edit reaches every preset): `family.mono`, `size.notes`, `scale.notes.heading`.
  **No new colour token** — code is told apart by monospace on a wash rather than a hue,
  because a hue legible on both a light and a dark window is hard to pick and would be
  one more thing to keep right in every preset.
