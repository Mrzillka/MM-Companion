"""The Notes block: a tabbed markdown editor over the workspace's note files.

Unlike every other block on the sheet, this one can exist **more than once** —
"New Notes Block" in the View menu adds another, dragging one onto another
merges their tabs, and dragging a tab off a bar splits it back out. So a section
here is told which block it is (``block_key``) and reads and writes only its own
entry in ``Character.notes``.

Two things are split that look like one, and the split is the whole design:

* **Which notes are open** is model state. It lives on the character, it is
  undoable, and it marks the sheet dirty — so :attr:`edited` is emitted for a
  tab opened, closed or reordered.
* **What is written in them** is not. Note text autosaves to its own ``.md``
  file on a debounce, exactly the way the portrait's *pixels* are not part of
  the character while ``image_path`` is. ``Ctrl+Z`` therefore does not walk a
  paragraph back — the editor's own undo stack does, inside the box.

That is also what makes a note shareable: two characters can have the same file
open, and neither owns it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import notes
from mm_companion.core.character import Character, NotesState
from mm_companion.core.data_loader import GameData
from mm_companion.ui.notes.editor import NoteEditor
from mm_companion.ui.notes.events import note_events
from mm_companion.ui.notes.picker import NotePickerDialog
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.tab_drag import TabSplitGesture
from mm_companion.ui.widgets import discard_widget, muted_style

#: How far a tab must be dragged *off* its bar before the drag becomes a split.
#: Generous on purpose: a bar's own left/right reorder is the common gesture and
#: must not turn into a new block because a hand wobbled.

#: The longest a tab caption gets before it is elided.
MAX_TAB_CHARS = 22

#: The preview toggle's two faces, and they are **words**. A glyph is the right
#: answer on a title bar, where there is room for nothing else and the same three
#: marks recur on every block until they are learned; it is the wrong answer here,
#: beside four text buttons, where one small symbol reads as neither a label nor
#: an icon and says nothing about what a click would do. The button still carries
#: the state the way the lock's 🔒/🔓 does — the label is always the action, so it
#: needs no tooltip to be understood.
LABEL_PREVIEW = "Preview"
LABEL_EDIT = "Edit"

_MISSING = "(missing)"


@dataclass
class _OpenNote:
    """One open tab: the file it is, the editor showing it, and what we last read."""

    ref: str
    editor: NoteEditor
    mtime: float | None


class NotesSection(QGroupBox):
    """One Notes block: a tab per open note, over the workspace's ``notes/`` dir."""

    #: An editor grows into whatever height the block is given — see
    #: :meth:`~mm_companion.ui.block_frame._InnerScroll.set_section`. A note is as
    #: long as you make it and the room is where you make it longer.
    fills_height = True

    #: A tab was opened, closed or reordered — a character edit, so undoable.
    edited = Signal()
    #: The block's title should now read this (the active note's name).
    titleChanged = Signal(str)
    #: A tab was dragged off the bar: (note ref, where the cursor is). The sheet
    #: answers by making a new Notes block holding that note and handing the drag
    #: to the canvas, so it finishes as an ordinary block drop.
    splitRequested = Signal(str, QPoint)
    #: The split drag moved / ended. The tab bar still holds the mouse grab, so
    #: these are how the canvas hears about the rest of the gesture.
    splitMoved = Signal(QPoint)
    splitReleased = Signal(QPoint)

    def __init__(
        self,
        data: GameData,
        character: Character,
        parent: QWidget | None = None,
        *,
        block_key: str = "notes",
    ) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        self._data = data
        self._character = character
        self.block_key = block_key
        self._loading = True
        self._locked = False
        self._open: list[_OpenNote] = []
        self._picker: NotePickerDialog | None = None
        self._focus_filter = _FocusRefresh(self)

        outer = QVBoxLayout(self)

        self._toolbar = QHBoxLayout()
        self._open_button = QPushButton("Open…")
        self._open_button.setToolTip("Open a note from your workspace")
        self._open_button.clicked.connect(self.open_picker)
        self._toolbar.addWidget(self._open_button)

        self._new_button = QPushButton("New")
        self._new_button.setToolTip("Create a new note")
        self._new_button.clicked.connect(self._new_note)
        self._toolbar.addWidget(self._new_button)

        self._import_button = QPushButton("Import…")
        self._import_button.setToolTip("Copy a markdown file into your workspace and open it")
        self._import_button.clicked.connect(self._import_note)
        self._toolbar.addWidget(self._import_button)

        # Only while the tab bar is hidden — with tabs showing, each one carries
        # its own ✕. Here rather than beside the preview toggle on the right, both
        # because it acts on a note like its three neighbours do and because a ✕
        # over there would sit directly under the title bar's, which closes the
        # whole block.
        self._close_button = QPushButton("Close")
        self._close_button.setToolTip("Close this note (the file is kept)")
        self._close_button.clicked.connect(self._close_current)
        self._toolbar.addWidget(self._close_button)

        self._toolbar.addStretch(1)

        # A QToolButton and not a QPushButton, for the reason spelled out in
        # docs/notes/theme.md: the app's stylesheet states a push button's box
        # and emits no `QPushButton:checked`, so a checked one paints exactly
        # like an unchecked one. `QToolButton:checked` *is* in the sheet.
        self._preview_button = QToolButton()
        self._preview_button.setCheckable(True)
        self._preview_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._preview_button.toggled.connect(self._on_preview_toggled)
        self._toolbar.addWidget(self._preview_button)
        self._show_preview_state(False)
        self._fix_preview_width()
        outer.addLayout(self._toolbar)

        # The tabs and the empty state share one slot, exactly one showing: an
        # empty block still has to say what it is and offer the way in, and a
        # bare tab widget with no tabs says nothing at all.
        self._stack = QStackedWidget()
        self._tabs = QTabWidget()
        self._tabs.setMovable(True)
        self._tabs.setTabsClosable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        self._split = TabSplitGesture(
            self._tabs.tabBar(),
            begin=self._begin_split,
            moved=self.splitMoved.emit,
            released=self.splitReleased.emit,
        )
        self._stack.addWidget(self._tabs)

        self._empty = QWidget()
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.addStretch(1)
        hint = QLabel("No notes open.\nOpen one from your workspace, or make a new one.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(muted_style(italic=True))
        empty_layout.addWidget(hint)
        empty_layout.addStretch(1)
        self._stack.addWidget(self._empty)
        outer.addWidget(self._stack, stretch=1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Autosave. One timer for the whole block rather than one per editor: a
        # flush writes every dirty tab, and a note is only ever dirty because
        # somebody is typing in exactly one of them.
        self._dirty: set[NoteEditor] = set()
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(0)
        self._autosave.timeout.connect(self.flush)

        # Renames and deletions are workspace events, not this picker's: the same
        # note can be open in a second block after a split, or on another sheet
        # entirely, and every holder has to follow it (see ui/notes/events.py).
        events = note_events()
        events.renamed.connect(self._follow_rename)
        events.deleted.connect(self._follow_delete)

        self.reseed()
        self._loading = False

    # -- model ---------------------------------------------------------------

    def _state(self) -> NotesState:
        """This block's entry in the model, created on demand.

        Created rather than looked up, because a block with nothing open still
        has to have somewhere to record the first note opened into it. An entry
        with no files is omitted from ``to_dict`` (see :class:`NotesState`), so
        this never adds anything to a saved character.
        """
        state = self._character.notes.get(self.block_key)
        if state is None:
            state = NotesState()
            self._character.notes[self.block_key] = state
        return state

    def open_refs(self) -> tuple[str, ...]:
        """The notes this block currently has open, in tab order."""
        return tuple(item.ref for item in self._open)

    # -- seeding -------------------------------------------------------------

    def reseed(self) -> None:
        """Reconcile the open tabs against the model.

        Reads the model and never writes it — the sheet calls this after an undo
        has already put an earlier state back, and a write here would record a
        step nobody took.
        """
        was_loading, self._loading = self._loading, True
        try:
            state = self._character.notes.get(self.block_key) or NotesState()
            wanted = list(dict.fromkeys(state.files))  # a ref can only be open once
            current = [item.ref for item in self._open]
            if wanted != current:
                self.flush()
                self._rebuild_tabs(wanted)
            self._select(state.active)
            self._refresh_empty_state()
            self._emit_title()
        finally:
            self._loading = was_loading

    def _rebuild_tabs(self, refs: list[str]) -> None:
        """Re-open exactly *refs*, keeping any editor that is already right.

        Kept rather than rebuilt because an editor carries what the model does
        not: the caret, the scroll position, the preview toggle, and its own undo
        stack. Throwing them away on every reseed would make an undo of an
        unrelated field lose someone's place in a long note.
        """
        keep = {item.ref: item for item in self._open}
        while self._tabs.count():
            self._tabs.removeTab(0)
        rebuilt: list[_OpenNote] = []
        for ref in refs:
            item = keep.pop(ref, None) or self._load(ref)
            rebuilt.append(item)
            self._tabs.addTab(item.editor, "")
        for orphan in keep.values():
            discard_widget(orphan.editor)
        self._open = rebuilt
        self._refresh_tab_captions()

    def _load(self, ref: str) -> _OpenNote:
        editor = NoteEditor()
        editor.set_text(notes.read_note(ref))
        editor.set_locked(self._locked)
        editor.set_preview(self._preview_button.isChecked())
        # Keyed by the *editor*, not by the ref it opened with: a note can be
        # renamed under an open tab, and a captured filename would then mark a
        # file that no longer exists as dirty and never write the one that does.
        editor.edited.connect(lambda ed=editor: self._on_note_edited(ed))
        editor.source.installEventFilter(self._focus_filter)
        return _OpenNote(ref, editor, notes.note_mtime(ref))

    # -- opening and closing -------------------------------------------------

    def open_note(self, ref: str, *, focus: bool = True) -> None:
        """Open note *ref* in this block, or focus it if it is already here."""
        if not ref:
            return
        state = self._state()
        if ref not in state.files:
            state.files.append(ref)
        if focus:
            state.active = ref
        self.reseed()
        if not self._loading:
            self.edited.emit()

    def adopt(self, refs: list[str], *, active: str = "") -> None:
        """Take *refs* over from another Notes block (a merge).

        The other block's tabs land after this one's, in their own order, and
        anything already open here is not opened twice.
        """
        state = self._state()
        for ref in refs:
            if ref not in state.files:
                state.files.append(ref)
        if active:
            state.active = active
        self.reseed()
        if not self._loading:
            self.edited.emit()

    def release(self, ref: str) -> None:
        """Give up note *ref* without touching its file (the other half of a split)."""
        self._close_ref(ref)

    def _close_tab(self, index: int) -> None:
        if 0 <= index < len(self._open):
            self._close_ref(self._open[index].ref)

    def _close_current(self) -> None:
        self._close_tab(self._tabs.currentIndex())

    def _close_ref(self, ref: str) -> None:
        """Close *ref*'s tab. The file is left alone — closing is not deleting."""
        self.flush()
        state = self._state()
        if ref in state.files:
            state.files.remove(ref)
        if state.active == ref:
            state.active = state.files[0] if state.files else ""
        self.reseed()
        if not self._loading:
            self.edited.emit()

    def _new_note(self) -> None:
        self.open_note(notes.create_note("Untitled Note"))

    def _import_note(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import a Note", "", "Markdown (*.md *.markdown *.txt);;All files (*)"
        )
        if not path:
            return
        ref = notes.store_note(path)
        if ref:
            self.open_note(ref)

    def open_picker(self) -> None:
        """Show the workspace's notes. Modeless, and one per block."""
        if self._picker is None:
            self._picker = NotePickerDialog(self, open_refs=self.open_refs())
            self._picker.noteChosen.connect(self.open_note)
        else:
            self._picker.set_open_refs(self.open_refs())
        self._picker.show()
        self._picker.raise_()
        self._picker.activateWindow()

    def _follow_rename(self, old_ref: str, new_ref: str) -> None:
        """A note's file was renamed under us; keep pointing at it."""
        state = self._state()
        if old_ref not in state.files:
            return
        state.files[state.files.index(old_ref)] = new_ref
        if state.active == old_ref:
            state.active = new_ref
        for item in self._open:
            if item.ref == old_ref:
                item.ref = new_ref
        self._refresh_tab_captions()
        self._emit_title()
        self.edited.emit()

    def _follow_delete(self, ref: str) -> None:
        """A note's file left the workspace; drop the tab without writing it back.

        The discard is the whole point. ``_close_ref`` flushes first, so closing a
        just-deleted note re-created the file it had only now removed — and
        ``write_note`` builds the parent directory on its way, so there was nothing
        to stop it. Anything typed into a note being deleted is being deleted too.
        """
        if ref not in self._state().files:
            return
        for item in self._open:
            if item.ref == ref:
                self._dirty.discard(item.editor)
        self._close_ref(ref)

    # -- tabs ----------------------------------------------------------------

    def _select(self, ref: str) -> None:
        for index, item in enumerate(self._open):
            if item.ref == ref:
                self._tabs.setCurrentIndex(index)
                return
        if self._open:
            self._tabs.setCurrentIndex(0)

    def _on_tab_changed(self, index: int) -> None:
        self.flush()
        if self._loading or not (0 <= index < len(self._open)):
            return
        item = self._open[index]
        self._state().active = item.ref
        self._preview_button.setChecked(item.editor.is_preview())
        self._refresh_from_disk(item)
        self._emit_title()

    def _on_tab_moved(self, source: int, target: int) -> None:
        """The user dragged a tab along the bar: reorder is a character edit."""
        if self._loading:
            return
        self._open.insert(target, self._open.pop(source))
        self._state().files[:] = [item.ref for item in self._open]
        self.edited.emit()

    def _refresh_tab_captions(self) -> None:
        for index, item in enumerate(self._open):
            title = notes.note_title(item.ref)
            if notes.note_mtime(item.ref) is None:
                title = f"{title} {_MISSING}"
            self._tabs.setTabText(index, _elide(title))
            self._tabs.setTabToolTip(index, f"{title}\n{item.ref}")

    def _refresh_empty_state(self) -> None:
        """Show whichever of the three faces the open notes call for.

        A single note gets **no tab bar**: one tab is not a choice, and a strip of
        chrome that never changes is a row of the block's height spent saying
        nothing. Its name is still on the block's title bar, which is what makes
        hiding the bar safe, and the toolbar's Close takes over from the ✕ that
        went with it.
        """
        self._stack.setCurrentWidget(self._tabs if self._open else self._empty)
        tabbed = len(self._open) > 1
        self._tabs.tabBar().setVisible(tabbed)
        self._close_button.setVisible(bool(self._open) and not tabbed)
        self._close_button.setEnabled(not self._locked)
        self._preview_button.setEnabled(bool(self._open))

    def _emit_title(self) -> None:
        index = self._tabs.currentIndex()
        if 0 <= index < len(self._open):
            self.titleChanged.emit(f"Notes — {notes.note_title(self._open[index].ref)}")
        else:
            self.titleChanged.emit("Notes")

    def block_title(self) -> str:
        """What the block's title bar should read right now."""
        index = self._tabs.currentIndex()
        if 0 <= index < len(self._open):
            return f"Notes — {notes.note_title(self._open[index].ref)}"
        return "Notes"

    # -- preview -------------------------------------------------------------

    def _on_preview_toggled(self, preview: bool) -> None:
        self._show_preview_state(preview)
        index = self._tabs.currentIndex()
        if 0 <= index < len(self._open):
            if preview:
                self.flush()  # render what is written, not what was last saved
            self._open[index].editor.set_preview(preview)

    def _show_preview_state(self, preview: bool) -> None:
        """Put the toggle's label on what a click would now do."""
        self._preview_button.setText(LABEL_EDIT if preview else LABEL_PREVIEW)
        self._preview_button.setToolTip(
            "Edit the markdown source" if preview else "Show the note laid out"
        )

    def _fix_preview_width(self) -> None:
        """Hold the toggle at its wider label's width.

        The two labels are different lengths and the button sits after a stretch,
        so left to itself it would jump sideways every time the mode changed —
        out from under the cursor that just clicked it.

        The width is taken by *asking the button* for each label's size hint
        rather than measuring the text and adding a guess at the chrome: how much
        a tool button puts around its label is the style's business, and the
        guess was 12px short under the shipped one.
        """
        button = self._preview_button
        current = button.text()
        widest = 0
        for label in (LABEL_PREVIEW, LABEL_EDIT):
            button.setText(label)
            widest = max(widest, button.sizeHint().width())
        button.setText(current)
        button.setMinimumWidth(widest)

    # -- autosave ------------------------------------------------------------

    def _on_note_edited(self, editor: NoteEditor) -> None:
        """A note's text settled. Queue it for writing.

        Deliberately does **not** emit :attr:`edited`: note text is not part of
        the character, so typing must neither mark the sheet dirty nor land on
        the undo stack.
        """
        self._dirty.add(editor)
        self._autosave.start()

    def flush(self) -> None:
        """Write every note whose text has changed, now.

        Lands each editor's own pending debounce first: a flush provoked by
        something else (a tab change, the block being hidden, the sheet closing)
        happens *during* the 400 ms after the last keystroke as often as not, and
        those characters are exactly the ones that must not be lost.
        """
        for item in self._open:
            item.editor.flush()
        self._autosave.stop()
        if not self._dirty:
            return
        # A note that would not write stays dirty. write_note is careful to answer
        # rather than raise (a read-only file, a full disk, a Windows lock), and
        # dropping that answer meant the text was declared written, never retried,
        # and then overwritten from disk by the next refresh — the paragraph gone
        # with nothing on screen having said so.
        unwritten: set[NoteEditor] = set()
        for item in self._open:
            if item.editor not in self._dirty:
                continue
            if notes.write_note(item.ref, item.editor.text()):
                item.mtime = notes.note_mtime(item.ref)
            else:
                unwritten.add(item.editor)
        self._dirty = unwritten
        if unwritten:
            self._autosave.start()  # try again on the next settle
        self._refresh_tab_captions()
        self._emit_title()

    def _refresh_from_disk(self, item: _OpenNote) -> None:
        """Re-read *item* if its file moved under us (another sheet, another app).

        Only when this block has nothing unwritten of its own: whoever is typing
        wins over whatever is on disk, and clobbering a half-typed paragraph with
        a stale read would be much worse than the two copies drifting.
        """
        if item.editor in self._dirty:
            return
        mtime = notes.note_mtime(item.ref)
        if mtime is not None and mtime != item.mtime:
            item.editor.set_text(notes.read_note(item.ref))
            item.mtime = mtime
            self._refresh_tab_captions()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.flush()
        super().hideEvent(event)

    def _begin_split(self, index: int, global_pos: QPoint) -> bool:
        """A tab dragged clear of the bar: ask the sheet for a new Notes block.

        Refused for a block's only tab — that tab *is* the block, so splitting it
        out would leave an empty block behind and a new one holding what the old
        one held.
        """
        refs = self.open_refs()
        if not (0 <= index < len(refs)) or len(refs) < 2:
            return False
        self.splitRequested.emit(refs[index], global_pos)
        return True

    # -- lock ----------------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        for item in self._open:
            item.editor.set_locked(locked)
        for button in (self._new_button, self._import_button):
            button.setEnabled(not locked)
        self._close_button.setEnabled(not locked)
        self._tabs.setTabsClosable(not locked)


def _elide(text: str) -> str:
    return text if len(text) <= MAX_TAB_CHARS else text[: MAX_TAB_CHARS - 1] + "…"


class _FocusRefresh(QObject):
    """Re-read a note when its editor is focused, in case the file moved."""

    def __init__(self, section: NotesSection) -> None:
        super().__init__(section)
        self._section = section

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.FocusIn:
            for item in self._section._open:
                if item.editor.source is watched:
                    self._section._refresh_from_disk(item)
                    break
        return False


__all__ = ["NotesSection"]
