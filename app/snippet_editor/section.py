"""A section of the Snippet Editor: one thing recorded about each snippet.

Orientation, masks and confidence are sections, and so is whatever the ML
team asks for next. The window loops over its sections and knows none of
them by name, so adding one is a new module with a Section subclass and
one line in the window's list - see confidence_section.py for the
smallest complete example.

A section is:

  key, title   its id (settings, lookups) and the name on its header
  worklist     what "done" means for it - its needs/done pair on the Show
               filter, and its badge on the list's captions (strip.py);
               None for a section with nothing to be done (notes)
  panel        its widget in the column on the right
  tool         optionally, a tool it owns on the Single view's canvas:
               (tool id, button text, tooltip, the key that picks it up)

and these hooks, all optional:

  show_entry(entry)     the Single view's snippet changed (None: none)
  refresh(label_id)     some section edited this label: redisplay it if
                        it is the one in hand
  set_shown(shown)      switch everything beyond the panel on or off -
                        overlays, tool input; the window hides the panel
  view_changed(single)  the Single / Grid view changed; return a line to
                        show under the header ("" for none)
  key_pressed(key)      a key in the Single view; True if it was used
  restore_settings(s)   take up what it remembers between sessions
  save_settings(s)      and leave it for the next one

A section that edits a label edits the entry dict it was given in place,
emits entry_changed(label_id) - the window then recounts the list and
asks every section to refresh (entries_changed([ids]) for many at once,
recounted once) - and emits whatever signal carries the new
value out to the main window. A section whose key finishes a snippet can
emit advance_requested, and the window moves on to the next.
"""
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QWidget

from .strip import Worklist


class Section(QObject):
    """One thing recorded per snippet, as the Snippet Editor shows it."""

    key = ""
    title = ""
    # (tool id, button text, tooltip, key) for a Single-view tool, or None.
    tool = None

    # A label this section edited (label_id) - or several at once, which
    # the window recounts once for ([label ids]).
    entry_changed = pyqtSignal(int)
    entries_changed = pyqtSignal(list)
    # A key finished the snippet in hand: move on to the next.
    advance_requested = pyqtSignal()

    def __init__(self, worklist: "Worklist | None", panel: QWidget,
                 parent=None):
        super().__init__(parent)
        self.worklist = worklist
        self.panel = panel
        self.shown = True

    def show_entry(self, entry: "dict | None"):
        """The Single view's snippet changed."""

    def refresh(self, label_id: int):
        """A label was edited; redisplay it if it is the one in hand."""

    def set_shown(self, shown: bool):
        """Everything beyond the panel, on or off."""
        self.shown = bool(shown)

    def view_changed(self, single: bool) -> str:
        """The view changed; a line for under the header, or ""."""
        return ""

    def key_pressed(self, key: int) -> bool:
        """A key pressed in the Single view; True if this section used it."""
        return False

    def restore_settings(self, settings):
        """Take up what this section remembers between sessions."""

    def save_settings(self, settings):
        """Leave it for the next session."""
