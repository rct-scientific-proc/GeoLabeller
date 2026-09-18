"""The snippet strip: which labels a snippet editor works through.

A list of snippets with thumbnails, the Class and Show filters with their
live counts, a line explaining an empty list, and stepping for Space /
Ctrl+Space. Moved out of the mask editor so every section of the Snippet
Editor can share it.

What "done" means for the Show filter is the host's to say, as a Worklist:
"has a mask" for the mask editor today, "has an orientation" or "has a
confidence" as those sections arrive. The strip knows nothing about any of
them - it imports the snippet service and nothing else in the app.

It owns its widgets but not their placement: the two filters go on the
host's toolbar, the list pane into the host's splitter. So the strip can
move between windows without dragging a layout with it.
"""
from dataclasses import dataclass
from typing import Callable

from PyQt5.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (QComboBox, QLabel, QListWidget, QListWidgetItem,
                             QVBoxLayout, QWidget)

from ..snippets import SnippetLoader

THUMB_PX = 96


def _no_badge(_entry: dict) -> str:
    return ""


@dataclass(frozen=True)
class Worklist:
    """What "done" means for the Show filter, and how a snippet shows it.

    ``needs`` and ``done`` name the two filtered views ("Needs masks",
    "Masked"); ``is_done`` decides which a snippet is in; ``badge`` is
    appended to its caption; the two messages explain an empty list.
    """
    needs: str
    done: str
    is_done: Callable[[dict], bool]
    badge: Callable[[dict], str] = _no_badge
    nothing_left: str = "Everything here is done. Nothing left to do."
    none_done: str = "Nothing here is done yet."


class SnippetStrip(QObject):
    """The list of snippets being worked through, and its filters."""

    # The entry dict now selected (the strip's own object - hosts edit it
    # in place and call refresh_current), or None for an empty list.
    entry_picked = pyqtSignal(object)

    ID_ROLE = Qt.UserRole
    ALL_CLASSES = "All classes"

    # Show filter, in combo order. It opens on All, and deliberately:
    # reopening a finished class onto an empty strip reads as lost work.
    # The counts sit on the filter, so "212 still need masks" is legible
    # without hiding anything to find it out.
    FILTER_ALL, FILTER_NEEDS, FILTER_DONE = 0, 1, 2

    def __init__(self, worklist: Worklist, parent=None):
        super().__init__(parent)
        self._worklist = worklist
        self.entries: list = []
        self.items_by_label: dict = {}          # label_id -> list item
        self.entries_by_label: dict = {}        # label_id -> entry
        self._current_id = None
        self.loader = SnippetLoader(self)
        self.loader.ready.connect(self._on_snippet_ready)

        self.class_combo = QComboBox()
        # No arguments on the slot: PyQt hands a Python callable as many
        # signal arguments as its signature will take (see rebuild).
        self.class_combo.currentIndexChanged.connect(self.rebuild)

        # A worklist, not an archive: the needs view is what someone opens
        # the editor to work through, and its count falling is how they
        # see the work being done.
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["", "", ""])      # text set with counts
        self.filter_combo.setCurrentIndex(self.FILTER_ALL)
        self.filter_combo.setToolTip(
            "Which snippets the strip lists. A snippet you have just\n"
            "finished stays put until you move off it, so working on it\n"
            "never moves the strip under you.")
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)

        # The list, with a line under it for when the filter leaves it
        # empty - an empty list on its own reads as a broken window rather
        # than as a finished class.
        self.panel = QWidget()
        box = QVBoxLayout(self.panel)
        box.setContentsMargins(0, 0, 0, 0)
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(THUMB_PX, THUMB_PX))
        self.list_widget.setMinimumWidth(120)
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        box.addWidget(self.list_widget, 1)
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        box.addWidget(self.message_label)

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """The label entry dicts to work through.

        Kept in a stable, readable order (class, then image name, then
        label id) rather than project order, so a snippet is always where
        it was last time.
        """
        self.entries = sorted(
            entries, key=lambda e: (e["class_name"], e["image_name"],
                                    e["label_id"]))
        classes = sorted({e["class_name"] for e in self.entries})
        current = self.class_combo.currentText()
        wanted = [self.ALL_CLASSES] + classes
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItems(wanted)
        if current in wanted:
            self.class_combo.setCurrentText(current)
        self.class_combo.blockSignals(False)
        self.rebuild()

    @property
    def worklist(self) -> Worklist:
        return self._worklist

    def set_worklist(self, worklist: Worklist):
        """Change what "done" means - the filter setting itself is kept."""
        self._worklist = worklist
        self.rebuild()

    # -- queries ------------------------------------------------------------

    def filter_mode(self) -> int:
        """Which of FILTER_ALL / FILTER_NEEDS / FILTER_DONE is showing."""
        return self.filter_combo.currentIndex()

    def set_filter_mode(self, mode: int):
        self.filter_combo.setCurrentIndex(mode)

    def message(self) -> str:
        """The line under the list; empty when the list has snippets."""
        return self.message_label.text()

    def entry(self, label_id) -> "dict | None":
        return self.entries_by_label.get(label_id)

    def current_entry(self) -> "dict | None":
        return self.entries_by_label.get(self._current_id)

    def in_class(self, entry: dict) -> bool:
        wanted = self.class_combo.currentText()
        return wanted == self.ALL_CLASSES or entry["class_name"] == wanted

    def matches(self, entry: dict) -> bool:
        mode = self.filter_mode()
        if mode == self.FILTER_ALL:
            return True
        return bool(self._worklist.is_done(entry)) == \
            (mode == self.FILTER_DONE)

    # -- stepping -----------------------------------------------------------

    def cycle(self, delta: int):
        """Step to the next/previous snippet in the list, wrapping."""
        count = self.list_widget.count()
        if count == 0:
            return
        row = self.list_widget.currentRow()
        self.list_widget.setCurrentRow((row + delta) % count)

    # -- after the host edits the current entry -----------------------------

    def refresh_current(self):
        """The current entry changed: recaption and recount, no rebuild.

        A rebuild here would take a just-finished snippet off a Needs list
        while it is still being worked on; it is retired when the user
        moves off it instead (see _retire).
        """
        item = self.list_widget.currentItem()
        entry = self.current_entry()
        if item is not None and entry is not None:
            item.setText(self._caption(entry))
        self._update_counts()

    # -- building -----------------------------------------------------------

    def rebuild(self):
        # No optional arguments: class_combo.currentIndexChanged is
        # connected straight to this slot, and PyQt hands a Python
        # callable as many signal arguments as its signature will take.
        self.loader.cancel_all()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.blockSignals(False)
        # label_id -> item, so a delivered thumbnail lands in O(1).
        # Scanning the list per delivery was O(n^2) on the UI thread, and
        # a cache hit delivers synchronously inside this very loop.
        self.items_by_label = {}
        self.entries_by_label = {e["label_id"]: e for e in self.entries}
        keep_id = self._current_id
        for entry in self.entries:
            if not self.in_class(entry) or not self.matches(entry):
                continue
            item = QListWidgetItem(self._caption(entry))
            item.setData(self.ID_ROLE, entry["label_id"])
            self.list_widget.addItem(item)
            self.items_by_label[entry["label_id"]] = item
            self.loader.request(entry["label_id"], entry["image_path"],
                                entry["pixel_x"], entry["pixel_y"], THUMB_PX)
        self._update_counts()
        if self.list_widget.count():
            row = 0
            if keep_id is not None and keep_id in self.items_by_label:
                row = self.list_widget.row(self.items_by_label[keep_id])
            self.list_widget.setCurrentRow(row)
        else:
            self._current_id = None
            self.entry_picked.emit(None)
        self._update_message()

    def _caption(self, entry: dict) -> str:
        caption = entry["image_name"]
        if self.class_combo.currentText() == self.ALL_CLASSES:
            caption = f"{entry['class_name']}  \N{MIDDLE DOT}  {caption}"
        return caption + self._worklist.badge(entry)

    def _update_counts(self):
        """Live counts on the filter, for the class in view - the number
        left to do falls as the work is done, where the user chose what
        to look at."""
        in_class = [e for e in self.entries if self.in_class(e)]
        done = sum(1 for e in in_class if self._worklist.is_done(e))
        labels = (f"All ({len(in_class)})",
                  f"{self._worklist.needs} ({len(in_class) - done})",
                  f"{self._worklist.done} ({done})")
        self.filter_combo.blockSignals(True)
        for index, text in enumerate(labels):
            self.filter_combo.setItemText(index, text)
        self.filter_combo.blockSignals(False)

    def _update_message(self):
        if self.list_widget.count() or not self.entries:
            self.message_label.setText("")
            self.message_label.setVisible(False)
            return
        if self.filter_mode() == self.FILTER_NEEDS:
            text = self._worklist.nothing_left
        elif self.filter_mode() == self.FILTER_DONE:
            text = self._worklist.none_done
        else:
            text = "No snippets in this class."
        self.message_label.setText(text)
        self.message_label.setVisible(True)

    # -- selection ----------------------------------------------------------

    def _on_filter_changed(self, _index):
        self.rebuild()

    def _on_item_changed(self, item, previous=None):
        self._current_id = (None if item is None
                            else item.data(self.ID_ROLE))
        self.entry_picked.emit(self.current_entry())
        if previous is not None:
            self._retire(previous.data(self.ID_ROLE))

    def _retire(self, label_id):
        """Drop a snippet that no longer belongs, now the user has left it.

        Finishing a snippet should take it off a Needs list - but not while
        it is the one being worked on, which would move the list under the
        user. So it goes when they move on, and the count has already told
        them it counted.
        """
        entry = self.entries_by_label.get(label_id)
        item = self.items_by_label.get(label_id)
        if entry is None or item is None or self.matches(entry):
            return
        if self._current_id == label_id:
            return
        row = self.list_widget.row(item)
        if row < 0:
            return
        self.list_widget.blockSignals(True)
        self.list_widget.takeItem(row)
        self.list_widget.blockSignals(False)
        self.items_by_label.pop(label_id, None)
        self.loader.cancel(label_id)
        self._update_message()

    def _on_snippet_ready(self, label_id, arr):
        if arr is None:
            return
        item = self.items_by_label.get(label_id)
        if item is None:
            return          # a delivery for a list that has moved on
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        item.setIcon(QIcon(QPixmap.fromImage(image)))
