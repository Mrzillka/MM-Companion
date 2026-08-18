---
name: rulebook
description: Look a rule up in the Mutants & Masterminds core rulebook — the real book text, cited by page. Use when a rules answer isn't in docs/ or docs/notes/, when a number, cost, table value or edge case needs verifying against the source, or when asked "what does the core book say" / "check the book" / "is that right by RAW". Also covers the one-time setup that makes the book searchable.
---

# The core rulebook

A local copy of the M&M core rulebook can be searched **as plain text**, cheaply,
and every answer cited by page. The book itself is never in git.

## Why not just read the PDF

The `Read` tool renders PDF pages as **images** — about 2k tokens a page, 20
pages a call. Paging through a ~250-page book that way costs six figures of
tokens and still often misses. So the book is extracted **once** into one text
file per page, and looked up with `Grep`. A lookup then costs a few hundred
tokens and returns a page number you can check against the printed book.

**Never `Read` the `.pdf` itself without asking the user first.** That is the
expensive image path this whole setup exists to avoid. The only legitimate
reason to want it is a scanned PDF with no text layer (see Troubleshooting).

## One-time setup

1. Put your own copy of the book at **`reference/core-book.pdf`** (that exact
   name). Everything under `reference/` is gitignored.
2. Extract it:

```bash
python .claude/skills/rulebook/extract.py
```

That writes `reference/core-book/p0001.txt … pNNNN.txt` plus an `INDEX.md`, and
prints the first line of the opening 15 pages so the **printed-page offset**
(cover, credits, contents) can be read off by eye and recorded in `INDEX.md`.
Re-running rebuilds the output from scratch. Needs `pdftotext` on PATH — Git for
Windows already ships it at `/mingw64/bin/pdftotext`.

If `reference/core-book/` is missing, the book has not been set up: say so and
answer from `docs/` instead. Do not run the extraction on a PDF the user has not
put there.

## Looking a rule up

**Answer from `docs/` and `docs/notes/` first.** They are the project's own
record of *why* the code is shaped as it is, and they are cheaper still. Come
here when the rules answer genuinely is not there, or when a value needs
verifying against the source.

Then, three steps:

1. **Grep the page files**, always bounded:

   ```
   Grep  pattern: "Affliction"
         path: "reference/core-book"
         output_mode: "content"
         -C: 3
         head_limit: 40
   ```

   Search for the game term as printed, not a paraphrase. A term like *Damage*
   or *Move* hits hundreds of pages — pair it with a companion word
   (`"Affliction.*resist"`, `"Progressive"`) or grep the table caption instead.

2. **The filename is the page.** A hit in `p0142.txt` is PDF page 142. Read that
   one file for the full context — a page of text is ~600–900 tokens.

3. **Cite the page** in the answer, so the user can check the printed book.
   Give both numbers if the offset in `INDEX.md` is filled in: "PDF p142
   (printed p136)".

Fill in the chapter map in `INDEX.md` as chapters turn up. It lets a later
lookup narrow to a page range before grepping.

## Caveats of extracted text

- **Two-column pages and sidebars can interleave.** If a hit reads like two
  sentences spliced together, that is why — read the neighbouring page files
  (`p0141.txt`, `p0143.txt`) rather than trusting the fragment.
- **Tables survive but can wrap.** Extraction uses `pdftotext -layout`, which
  keeps column alignment; a very wide table may still wrap. Check the whole page
  file before quoting a row.
- **Art-heavy pages come out near-empty.** That is expected, not a failure.

## Troubleshooting

- *Grep finds nothing that should exist* — check `reference/core-book/INDEX.md`
  for the chars/page figure. If extraction warned about a missing text layer,
  the PDF is a scan and grep cannot work on it. Tell the user; reading pages as
  images is the only fallback and needs their go-ahead.
- *`pdftotext` not found* — it is at `/mingw64/bin/pdftotext` under Git Bash on
  this machine, or `poppler-utils` on Linux.

## What may cross from the book into the repo

This matters, and it is not optional. `src/mm_companion/data/` is Open Game
Content under the OGL; the published book mixes OGC with Product Identity.

**The book informs what a mechanic does. It is not a source to copy from.**

- **May inform `data/`:** mechanics — ranks, costs, DCs, degrees, table values,
  how an effect resolves. Facts and numbers, written into the data files in our
  own words, with provenance recorded for the OGL Section 15 as the Licensing
  boundary in `CLAUDE.md` already requires.
- **Never transcribed anywhere:** prose and flavour text, sidebars, sample
  characters, setting and character names, product names, artwork descriptions —
  Product Identity, whether into `data/`, `docs/`, or a commit message.

When quoting the book in conversation to settle a rules question, keep it to the
short passage that answers it, and cite the page.
