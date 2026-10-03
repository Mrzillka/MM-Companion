"""The simple sheet — the character laid out for play, and for paper.

Deliberately empty of imports: the card modules the Powers and Equipment blocks draw
with reach for :mod:`mm_companion.ui.simple.style`, and the views reach for those
blocks, so a package that imported its own parts here would import itself in a circle.

See :mod:`mm_companion.ui.simple.sheet` for what the simple sheet is and how it borrows
the live blocks, :mod:`~mm_companion.ui.simple.layout` for its two presets,
:mod:`~mm_companion.ui.simple.views` for the blocks it draws for itself, and
:mod:`~mm_companion.ui.simple.printing` for putting it on paper.
"""
