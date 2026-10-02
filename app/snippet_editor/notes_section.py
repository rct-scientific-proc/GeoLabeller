"""The notes section: a label's description, and its object's Group ID.

Both could only be read and written by right-clicking the marker on the
map (Describe..., Group ID...), so a note about what a snippet shows had
to be written somewhere the snippet was not. Here they are shown for the
snippet in hand and edited in place:

  * Description - free text, this label's alone: linked labels are the
    same object seen in different images, and what is worth describing is
    usually what differs between those views.
  * Group ID - one name for the whole linked object. Setting it here
    names every label linked to this one, as it does from the map.

A note is stored when its field is left: a click anywhere else, Ctrl+Enter
in the description, Enter in the Group ID. The keys also hand the keyboard
back to the window, so Space steps the list again. Escape puts back what
is stored.

This section has no worklist: most labels never need a note, so a "Needs
notes" on the Show filter would be a list of everything.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
                             QWidget)

from .section import Section


class _DescriptionEdit(QPlainTextEdit):
    """A few lines of text that say when they are done with."""

    left = pyqtSignal()                  # store what is here
    put_back = pyqtSignal()              # Escape: what was stored, again

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() & Qt.ControlModifier):
            self.clearFocus()            # which stores it: focusOutEvent
            return
        if event.key() == Qt.Key_Escape:
            self.put_back.emit()
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.left.emit()


class _GroupEdit(QLineEdit):
    """One line, done with at Enter, Escape or a click elsewhere."""

    left = pyqtSignal()
    put_back = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.clearFocus()
            return
        if event.key() == Qt.Key_Escape:
            self.put_back.emit()
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.left.emit()


class NotesPanel(QWidget):
    """The description and the Group ID of the snippet in hand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Description:"))
        self.description_edit = _DescriptionEdit()
        self.description_edit.setPlaceholderText("Nothing written yet.")
        self.description_edit.setTabChangesFocus(True)
        # Three lines showing; a longer note scrolls.
        self.description_edit.setFixedHeight(
            self.description_edit.fontMetrics().lineSpacing() * 3 + 14)
        self.description_edit.setToolTip(
            "A note on this label alone. Stored when you click elsewhere "
            "or press Ctrl+Enter; Escape puts back what was stored.")
        layout.addWidget(self.description_edit)
        layout.addWidget(QLabel("Group ID:"))
        self.group_edit = _GroupEdit()
        self.group_edit.setPlaceholderText("No shared name")
        self.group_edit.setToolTip(
            "One name for the whole object: every label linked to this "
            "one takes it.\nStored when you click elsewhere or press "
            "Enter; Escape puts back what was stored.")
        layout.addWidget(self.group_edit)
        hint = QLabel("Stored when you click away, or with Ctrl+Enter "
                      "(Enter for the Group ID). The Group ID is shared "
                      "by linked labels; the description is not.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)


class NotesSection(Section):
    """The Snippet Editor's Notes section."""

    key = "notes"
    title = "Notes"

    # (label_id, text) - out to the main window; "" clears.
    description_changed = pyqtSignal(int, str)
    # (label_id, name): for the label's whole linked object.
    group_id_changed = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(None, NotesPanel(), parent)
        self._entry = None
        self.panel.description_edit.left.connect(self._store_description)
        self.panel.description_edit.put_back.connect(self._fill)
        self.panel.group_edit.left.connect(self._store_group_id)
        self.panel.group_edit.put_back.connect(self._fill)
        self.show_entry(None)

    def show_entry(self, entry):
        # A note still being typed belongs to the snippet it was typed
        # on. Leaving the field stores it, and a click on another snippet
        # leaves the field before it picks the snippet - but a note must
        # not hang on the order two events arrive in.
        if self.panel.description_edit.document().isModified():
            self._store_description()
        if self.panel.group_edit.isModified():
            self._store_group_id()
        self._entry = entry
        self._fill()
        self.panel.description_edit.setEnabled(entry is not None)
        self.panel.group_edit.setEnabled(entry is not None)

    def refresh(self, label_id):
        if self._entry is not None and self._entry["label_id"] == label_id:
            self._fill()

    def _fill(self):
        """Show what is stored - leaving alone a field that already does,
        whose cursor and undo a refill would throw away."""
        entry = self._entry or {}
        description = entry.get("description") or ""
        if self.panel.description_edit.toPlainText() != description:
            self.panel.description_edit.setPlainText(description)
        group = entry.get("group_id") or ""
        if self.panel.group_edit.text() != group:
            self.panel.group_edit.setText(group)

    def _store_description(self):
        if self._entry is None:
            return
        text = self.panel.description_edit.toPlainText().strip()
        if text == (self._entry.get("description") or ""):
            return
        self._entry["description"] = text
        label_id = self._entry["label_id"]
        self.description_changed.emit(label_id, text)
        self.entry_changed.emit(label_id)

    def _store_group_id(self):
        if self._entry is None:
            return
        text = self.panel.group_edit.text().strip()
        if text == (self._entry.get("group_id") or ""):
            return
        self._entry["group_id"] = text
        label_id = self._entry["label_id"]
        self.group_id_changed.emit(label_id, text)
        self.entry_changed.emit(label_id)

    def view_changed(self, single):
        # Written about the snippet in hand, which the Grid does not show.
        self.panel.setEnabled(single)
        return ("" if single else "Notes are written in the Single view - "
                                  "double-click a snippet to open it there.")
