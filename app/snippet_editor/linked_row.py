"""The linked row: every snippet of the object in hand, side by side.

Asked for 2026-09-28 (feature 8 of the Snippet Editor list). Linked
labels are one object seen in different images, and what they record
should agree - the class always, the orientation and size roughly - so
the Single view shows them together under the snippet being worked on,
each captioned with its image, class and the same badges the list shows
(ratings, headings, sizes, reviews). An inconsistency shows at a glance;
clicking a snippet makes it the one in hand.

Hidden when the snippet in hand is not linked, so it costs no room in the
common case.
"""
from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (QLabel, QListView, QListWidget, QListWidgetItem,
                             QVBoxLayout, QWidget)

from ..snippets import SnippetLoader
from .strip import THUMB_PX


class LinkedRow(QWidget):
    """A horizontal strip of the current object's snippets."""

    picked = pyqtSignal(int)             # label_id clicked

    ID_ROLE = Qt.UserRole

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: bold;")
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListView.IconMode)
        self.list_widget.setFlow(QListView.LeftToRight)
        self.list_widget.setWrapping(False)
        self.list_widget.setMovement(QListView.Static)
        self.list_widget.setIconSize(QSize(THUMB_PX, THUMB_PX))
        self.list_widget.setFixedHeight(THUMB_PX + 78)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.itemClicked.connect(
            lambda item: self.picked.emit(item.data(self.ID_ROLE)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.list_widget)
        self.loader = SnippetLoader(self)
        self.loader.ready.connect(self._on_snippet_ready)
        self.items: dict = {}
        self.entries: list = []
        self._current = None
        self._badges = lambda entry: ""
        self.setVisible(False)

    # -- what is shown -------------------------------------------------------

    def set_badges(self, badges):
        """``badges(entry) -> str``: the list's badges for a snippet."""
        self._badges = badges

    def show_for(self, entry, all_entries):
        """Show the object ``entry`` belongs to (hide when it is alone)."""
        self._current = None if entry is None else entry["label_id"]
        if entry is None:
            self._clear()
            self.setVisible(False)
            return
        members = sorted(
            (e for e in all_entries
             if e.get("object_id") == entry.get("object_id")),
            key=lambda e: (e["image_name"], e["label_id"]))
        if len(members) < 2:
            self._clear()
            self.setVisible(False)
            return
        same = [e["label_id"] for e in members]
        if same != [e["label_id"] for e in self.entries]:
            self._rebuild(members)
        else:
            self.entries = members
            self.refresh()
        group = entry.get("group_id") or ""
        self.title.setText(
            f"This object in {len(members)} images"
            + (f" - {group}" if group else "")
            + self._disagreement(members))
        self.setVisible(True)

    def refresh(self):
        """Recaption (an entry was edited) and mark the one in hand."""
        for entry in self.entries:
            item = self.items.get(entry["label_id"])
            if item is None:
                continue
            item.setText(self.caption(entry))
            font = item.font()
            font.setBold(entry["label_id"] == self._current)
            item.setFont(font)
            if entry["label_id"] == self._current:
                self.list_widget.setCurrentItem(item)

    def caption(self, entry) -> str:
        return (f"{entry['image_name']}\n{entry['class_name']}"
                + self._badges(entry))

    @staticmethod
    def _disagreement(members) -> str:
        classes = {e["class_name"] for e in members}
        return (f"  -  classes differ: {', '.join(sorted(classes))}"
                if len(classes) > 1 else "")

    # -- building --------------------------------------------------------------

    def _clear(self):
        self.loader.cancel_all()
        self.list_widget.clear()
        self.items = {}
        self.entries = []

    def _rebuild(self, members):
        self._clear()
        self.entries = members
        for entry in members:
            item = QListWidgetItem(self.caption(entry))
            item.setData(self.ID_ROLE, entry["label_id"])
            item.setSizeHint(QSize(THUMB_PX + 40, THUMB_PX + 70))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list_widget.addItem(item)
            self.items[entry["label_id"]] = item
            self.loader.request(entry["label_id"], entry["image_path"],
                                entry["pixel_x"], entry["pixel_y"], THUMB_PX)
        self.refresh()

    def _on_snippet_ready(self, label_id, arr):
        item = self.items.get(label_id)
        if item is None or arr is None:
            return
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        item.setIcon(QIcon(QPixmap.fromImage(image)))
