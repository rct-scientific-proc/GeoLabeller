"""The snippet sidebar: every label as its exported pixels, in one column.

A vertical stack of un-warped snippets on the right of the window, filtered
to all labels, one class, or only linked labels. Each entry shows what the
HDF5/sub-image exports would write for that label (the snippet service
guarantees the framing and stretch are identical), so this doubles as an
export preview.

Double-click or "Zoom to" reveals the label on the canvas - main_window owns
that (it must ensure the image is loaded and toggled on), the panel only
emits the label id.
"""
from pathlib import Path

import numpy as np

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QSpinBox, QVBoxLayout, QWidget)

from .snippets import SnippetLoader

FILTER_ALL = "All"
FILTER_LINKED = "Linked only"
_CLASS_PREFIX = "All: "


def _array_to_icon(arr: np.ndarray) -> QIcon:
    """An RGB array as a list icon (fromImage copies, so no lifetime games)."""
    h, w = arr.shape[:2]
    image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
    return QIcon(QPixmap.fromImage(image))


class SnippetPanel(QWidget):
    """One-column stack of label snippets with class/linked filtering."""

    # The user wants this label revealed on the canvas: (label_id).
    reveal_requested = pyqtSignal(int)

    _ID_ROLE = Qt.UserRole

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loader = SnippetLoader(self)
        self._loader.ready.connect(self._on_snippet_ready)
        self._entries: list = []          # current project labels, unfiltered
        self._items: dict[int, QListWidgetItem] = {}
        self._setup_ui()

    # -- UI -----------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("Snippets")
        header.setStyleSheet("font-weight: bold; padding: 4px;")
        header.setToolTip(
            "Each label as the exports would write it: raw source pixels, "
            "export framing and stretch.")
        layout.addWidget(header)

        controls = QHBoxLayout()
        self.filter_combo = QComboBox()
        self.filter_combo.setToolTip("Which labels to show.")
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        controls.addWidget(self.filter_combo, 1)

        # Which linked group to show; only meaningful (and only visible)
        # under the Linked filter. Groups are named by their group_id where
        # one is set - the readable name exists for exactly this - and by a
        # truncated object UUID otherwise. Keyed by object_id underneath, so
        # two groups that happen to share a name stay distinct.
        self.group_combo = QComboBox()
        self.group_combo.setToolTip("Which linked group to show.")
        self.group_combo.hide()
        self.group_combo.currentIndexChanged.connect(self._rebuild)
        controls.addWidget(self.group_combo, 1)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(32, 512)
        self.size_spin.setSingleStep(32)
        self.size_spin.setValue(128)
        self.size_spin.setSuffix(" px")
        self.size_spin.setToolTip(
            "Snippet size in SOURCE pixels around each label.")
        self.size_spin.valueChanged.connect(self._on_size_changed)
        controls.addWidget(self.size_spin)
        layout.addLayout(controls)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setFlow(QListWidget.TopToBottom)
        self.list.setWrapping(False)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setUniformItemSizes(True)
        self._apply_display_size()
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list)

    def _apply_display_size(self):
        size = self.size_spin.value()
        # Snippets are read at source resolution and shown 1:1 up to a sane
        # display cap, so the panel width follows the spin box.
        display = min(size, 256)
        self.list.setIconSize(QSize(display, display))
        self.list.setGridSize(QSize(display + 16, display + 36))

    # -- Data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Feed the current project labels.

        ``entries`` are dicts: label_id, image_path, image_name, pixel_x,
        pixel_y, class_name, linked (bool), group_id, object_id. Built by
        main_window from the project, so the panel never touches project
        internals.
        """
        self._entries = list(entries)
        self._refresh_filter_choices()
        self._refresh_group_choices()
        self._rebuild()

    ALL_GROUPS = "All groups"

    def _refresh_group_choices(self):
        """One entry per linked group, named readably, keyed by object_id."""
        groups: dict[str, tuple[str, int]] = {}
        for e in self._entries:
            if not e.get("linked"):
                continue
            oid = e.get("object_id", "")
            name, count = groups.get(oid, ("", 0))
            groups[oid] = (e.get("group_id") or name, count + 1)
        current = self.group_combo.currentData()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem(self.ALL_GROUPS, None)
        # Named groups first, alphabetically; unnamed after, by id.
        def sort_key(item):
            oid, (name, _count) = item
            return (0, name.lower()) if name else (1, oid)
        for oid, (name, count) in sorted(groups.items(), key=sort_key):
            display = name or f"(unnamed) {oid[:8]}"
            self.group_combo.addItem(f"{display}  ({count})", oid)
        index = self.group_combo.findData(current)
        if index >= 0:
            self.group_combo.setCurrentIndex(index)
        self.group_combo.blockSignals(False)

    def _on_filter_changed(self):
        self.group_combo.setVisible(
            self.filter_combo.currentText() == FILTER_LINKED)
        self._rebuild()

    def _refresh_filter_choices(self):
        current = self.filter_combo.currentText()
        classes = sorted({e["class_name"] for e in self._entries})
        wanted = [FILTER_ALL] + [f"{_CLASS_PREFIX}{c}" for c in classes] \
            + [FILTER_LINKED]
        existing = [self.filter_combo.itemText(i)
                    for i in range(self.filter_combo.count())]
        if existing != wanted:
            self.filter_combo.blockSignals(True)
            self.filter_combo.clear()
            self.filter_combo.addItems(wanted)
            if current in wanted:
                self.filter_combo.setCurrentText(current)
            self.filter_combo.blockSignals(False)

    def _filtered(self) -> list:
        choice = self.filter_combo.currentText() or FILTER_ALL
        if choice == FILTER_LINKED:
            linked = [e for e in self._entries if e.get("linked")]
            wanted_oid = self.group_combo.currentData()
            if wanted_oid is None:
                return linked
            return [e for e in linked
                    if e.get("object_id") == wanted_oid]
        if choice.startswith(_CLASS_PREFIX):
            wanted = choice[len(_CLASS_PREFIX):]
            return [e for e in self._entries if e["class_name"] == wanted]
        return self._entries

    def _rebuild(self):
        self._loader.cancel_all()
        self.list.clear()
        self._items.clear()
        size = self.size_spin.value()
        # Items are created before their icons arrive (snippets load async),
        # and with uniform item sizes the view caches the FIRST size hint it
        # computes - a text-only strip - leaving every icon painted into a
        # ~17px-tall rect until some resize forces a relayout. Pin an
        # explicit icon+caption hint up front so geometry never depends on
        # icon timing. Must stay inside the grid cell set by
        # _apply_display_size.
        display = min(size, 256)
        hint = QSize(display + 12, display + 32)
        for entry in self._filtered():
            label_id = entry["label_id"]
            caption = f"{entry['class_name']}  ·  {entry['image_name']}"
            if entry.get("group_id"):
                caption = f"[{entry['group_id']}]  {caption}"
            item = QListWidgetItem(caption)
            item.setSizeHint(hint)
            item.setData(self._ID_ROLE, label_id)
            item.setToolTip(
                f"{entry['class_name']} on {Path(entry['image_path']).name}"
                + (f"\nGroup: {entry['group_id']}" if entry.get("group_id")
                   else ""))
            self.list.addItem(item)
            self._items[label_id] = item
            self._loader.request(label_id, entry["image_path"],
                                 entry["pixel_x"], entry["pixel_y"], size)

    def _on_size_changed(self):
        self._apply_display_size()
        self._rebuild()

    def _on_snippet_ready(self, label_id, arr):
        item = self._items.get(label_id)
        if item is None:
            return   # filtered away while the read ran
        if arr is None:
            item.setForeground(QColor(150, 150, 150))
            item.setText(item.text() + "  (unreadable)")
            return
        item.setIcon(_array_to_icon(arr))

    # -- Interaction ---------------------------------------------------------

    def _on_double_clicked(self, item):
        label_id = item.data(self._ID_ROLE)
        if label_id is not None:
            self.reveal_requested.emit(label_id)

    def _show_context_menu(self, position):
        item = self.list.itemAt(position)
        if item is None:
            return
        label_id = item.data(self._ID_ROLE)
        if label_id is None:
            return
        menu = QMenu(self)
        zoom_action = menu.addAction("Zoom to")
        action = menu.exec_(self.list.viewport().mapToGlobal(position))
        if action == zoom_action:
            self.reveal_requested.emit(label_id)
