"""The reference strip: chips already given a class, beside the grid.

Asked for 2026-10-13: while labelling, it helps to have in view what
the class already looks like. The strip sits under the detail panel on
the right. A class is chosen at its head - every class with examples,
Ignore last - and under it come small pictures of the chips that became
that class: this session's verdicts first, the newest at the top, then
the project's memory of earlier rounds and its labels of the class.

The panel draws what it is given: the window gathers the examples
(learner.gather_examples), asks the loader for their pixels under keys of
its own, and hands them over as they come.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .learner import name_of
from .ledger import IGNORE

MAX_SHOWN = 60                  # examples at most, the newest first
COLUMNS = 3
SPACING = 4
NONE_YET = "No chips of this class yet."


def class_order(class_name: str) -> tuple:
    """Classes by name, Ignore last."""
    return (class_name == IGNORE, class_name)


class ReferenceTile(QLabel):
    """One example's picture, square, with a word on where it is from."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: rgb(30, 30, 30);")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_pixels(self, arr):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self._show()

    def set_side(self, px: int):
        self.setFixedSize(px, px)
        self._show()

    def has_picture(self) -> bool:
        return self._pixmap is not None

    def _show(self):
        if self._pixmap is None:
            self.clear()
            return
        side = max(8, min(self.width(), self.height()))
        self.setPixmap(self._pixmap.scaled(side, side, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))


class ReferencePanel(QWidget):
    """See the module."""

    class_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._examples: list = []
        self._tiles: list = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Reference")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        head.addWidget(title)
        self.class_combo = QComboBox()
        self.class_combo.setToolTip(
            "Chips already given this class - this session's verdicts, the\n"
            "newest first, then earlier rounds and the project's labels -\n"
            "to hold up against the chip in hand.")
        self.class_combo.currentIndexChanged.connect(
            lambda _i: self.class_changed.emit())
        head.addWidget(self.class_combo, 1)
        layout.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setSpacing(SPACING)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self._host)
        layout.addWidget(self.scroll, 1)
        self.count_label = QLabel(NONE_YET)
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)
        self.scroll.setMinimumHeight(40)

    # -- what is offered ------------------------------------------------------

    def set_classes(self, counts: dict):
        """The classes with examples and how many, the choice kept."""
        combo = self.class_combo
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for class_name in sorted(counts, key=class_order):
            combo.addItem(f"{name_of(class_name)} ({counts[class_name]})",
                          class_name)
        index = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def chosen(self):
        """The class picked, or None when there is none to pick."""
        return self.class_combo.currentData()

    def set_chosen(self, class_name) -> bool:
        index = self.class_combo.findData(class_name)
        if index < 0:
            return False
        self.class_combo.setCurrentIndex(index)
        return True

    # -- what is shown --------------------------------------------------------

    def set_examples(self, examples: list, description: str = "") -> bool:
        """The examples to show, in order; True when the tiles were
        rebuilt (the set changed), so pixels should be asked for."""
        keys = [self._key(e) for e in examples]
        if keys == [self._key(e) for e in self._examples]:
            self.count_label.setText(description or self.count_label.text())
            return False
        self._examples = list(examples)
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._tiles = []
        side = self.tile_px()
        for i, example in enumerate(self._examples):
            tile = ReferenceTile()
            tile.set_side(side)
            tile.setToolTip(f"{example.image_name if hasattr(example, 'image_name') else example.image_path}"
                            f"  ({example.pixel_x:.0f}, {example.pixel_y:.0f})"
                            f"\n{self._origin_text(example.origin)}")
            self._grid.addWidget(tile, i // COLUMNS, i % COLUMNS)
            self._tiles.append(tile)
        self.count_label.setText(description or (NONE_YET if not examples
                                                 else ""))
        return True

    @staticmethod
    def _key(example):
        return (example.image_path, int(round(example.pixel_x)),
                int(round(example.pixel_y)), example.class_name)

    @staticmethod
    def _origin_text(origin: str) -> str:
        return {"verdict": "a verdict given here",
                "memory": "reviewed in an earlier round",
                "label": "a label in the project"}.get(origin, origin)

    def examples(self) -> list:
        return list(self._examples)

    def set_pixels(self, index: int, arr):
        """The loader delivered for the tile at ``index``."""
        if 0 <= index < len(self._tiles) and arr is not None:
            self._tiles[index].set_pixels(arr)

    def pictures(self) -> int:
        return sum(1 for tile in self._tiles if tile.has_picture())

    def tile_px(self) -> int:
        width = self.scroll.viewport().width() or self.width() or 240
        return max(24, (width - SPACING * (COLUMNS - 1) - 2) // COLUMNS)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        side = self.tile_px()
        for tile in self._tiles:
            if tile.width() != side:
                tile.set_side(side)
