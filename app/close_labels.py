"""Clean Close Labels (2.9.2): two labels on one image within a few pixels
of each other, looked at side by side and thinned.

Labels pile up on an object: two people labelled it, an import landed
on a label already there with Import Options set to keep both, a hand
slipped. Labels > Clean Close Labels... finds, on every image, each two
labels whose centres lie within a distance in pixels of the image (the
Import Options distance to start with), filtered to the same class, to
different classes, or any, and with or without pairs that are already
one linked object; then shows the pairs one at a time, each label's
snippet beside the other's, for a decision: keep both, remove the left,
remove the right, or remove both. A removal is made at once, as one
undo step of the main window's history, and every pair that involved a
removed label goes with it, so a run of three close labels is never
offered against a label that is gone. The survivor of a pair keeps its
links; nothing here edits an object.

The search buckets each image's labels by a grid of the distance, so a
project of twenty thousand labels is one pass and not a comparison of
every label with every other.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PurePosixPath

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                             QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from . import duplicates
from .settings_scope import settings as app_settings
from .snippets import SnippetLoader, snippet_frame

ANY = "any"
SAME = "same"
DIFFERENT = "different"
CLASS_FILTERS = ((ANY, "Any classes"),
                 (SAME, "The same class only"),
                 (DIFFERENT, "Different classes only"))

DISTANCE_KEY = "clean/distance_px"
CLASSES_KEY = "clean/classes"
LINKED_KEY = "clean/include_linked"
CHIP_KEY = "clean/chip_px"
CHIP_SIZES = (96, 128, 160, 224, 320)
DEFAULT_CHIP_PX = 160
PICTURE_PX = 300                # each snippet is shown this wide

CROSS_COLOR = QColor(255, 220, 0)
LINK_COLOR = QColor(80, 200, 255)


# -- the pairs ----------------------------------------------------------------

@dataclass
class Pair:
    """Two labels on one image within the distance; ``a`` has the lower
    id. ``linked``: the two are one object already."""
    image_path: str
    a: object
    b: object
    distance: float
    linked: bool

    @property
    def image_name(self) -> str:
        return PurePosixPath(str(self.image_path).replace("\\", "/")).name

    @property
    def ids(self) -> tuple:
        return (self.a.id, self.b.id)


def find_pairs(project, distance: float, classes: str = ANY,
               include_linked: bool = True) -> list:
    """Every two labels on one image whose centres lie within
    ``distance`` pixels of each other: images in project order, and
    within an image the closest pair first.

    ``classes`` is ANY, SAME (the two of one class) or DIFFERENT;
    ``include_linked`` False leaves out the pairs that are one object.
    """
    distance = max(0.0, float(distance))
    cell = max(distance, 0.5)
    pairs: list = []
    for path, image in project.images.items():
        labels = image.labels
        if len(labels) < 2:
            continue
        grid: dict = {}
        for label in labels:
            key = (math.floor(label.pixel_x / cell),
                   math.floor(label.pixel_y / cell))
            grid.setdefault(key, []).append(label)
        found = []
        for (cx, cy), bucket in grid.items():
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    other = grid.get((cx + dx, cy + dy))
                    if other is None:
                        continue
                    for a in bucket:
                        for b in other:
                            # Each pair once, from the side of the lower
                            # id (a cell against itself included).
                            if a.id >= b.id:
                                continue
                            d = math.hypot(a.pixel_x - b.pixel_x,
                                           a.pixel_y - b.pixel_y)
                            if d > distance:
                                continue
                            if classes == SAME \
                                    and a.class_name != b.class_name:
                                continue
                            if classes == DIFFERENT \
                                    and a.class_name == b.class_name:
                                continue
                            linked = bool(a.object_id) \
                                and a.object_id == b.object_id
                            if linked and not include_linked:
                                continue
                            found.append(Pair(path, a, b, d, linked))
        found.sort(key=lambda p: (p.distance, p.a.id, p.b.id))
        pairs.extend(found)
    return pairs


def describe(pairs: list) -> str:
    """The status line for a search's result."""
    if not pairs:
        return "No two labels within the distance on one image."
    images = len({p.image_path for p in pairs})
    linked = sum(1 for p in pairs if p.linked)
    text = (f"{len(pairs)} pair{'' if len(pairs) == 1 else 's'} on "
            f"{images} image{'' if images == 1 else 's'}")
    if linked:
        text += f", {linked} of them one linked object"
    return text + "."


# -- the settings ---------------------------------------------------------------

def distance_px() -> float:
    try:
        value = float(app_settings().value(
            DISTANCE_KEY, duplicates.distance_px()))
    except (TypeError, ValueError):
        return duplicates.distance_px()
    return min(max(value, 0.0), 10000.0)


def class_filter() -> str:
    value = str(app_settings().value(CLASSES_KEY, ANY))
    return value if value in dict(CLASS_FILTERS) else ANY


def include_linked() -> bool:
    return str(app_settings().value(LINKED_KEY, "true")).lower() != "false"


def chip_px() -> int:
    try:
        value = int(app_settings().value(CHIP_KEY, DEFAULT_CHIP_PX))
    except (TypeError, ValueError):
        return DEFAULT_CHIP_PX
    return value if value in CHIP_SIZES else DEFAULT_CHIP_PX


# -- the window -------------------------------------------------------------------

class _Picture(QWidget):
    """One label's snippet, a cross where the label is, and a dot where
    the other label of the pair lies when it is inside the picture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(PICTURE_PX, PICTURE_PX)
        self._pixmap = None
        self._text = ""
        self._at = (0.5, 0.5)           # the label, as a fraction
        self._other = None              # the other label, as a fraction

    def set_reading(self):
        self._pixmap = None
        self._text = "Reading..."
        self.update()

    def set_failed(self, text: str = "No picture: the image could not "
                                     "be read"):
        self._pixmap = None
        self._text = text
        self.update()

    def set_picture(self, arr: np.ndarray, at: tuple, other):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self._at = at
        self._other = other
        self._text = ""
        self.update()

    def has_picture(self) -> bool:
        return self._pixmap is not None

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        if self._pixmap is None:
            painter.setPen(QColor(160, 160, 160))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                             self._text)
            painter.end()
            return
        scale = min(self.width() / self._pixmap.width(),
                    self.height() / self._pixmap.height())
        shown_w = self._pixmap.width() * scale
        shown_h = self._pixmap.height() * scale
        left = (self.width() - shown_w) / 2.0
        top = (self.height() - shown_h) / 2.0
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(int(left), int(top), int(shown_w), int(shown_h),
                           self._pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        x = left + self._at[0] * shown_w
        y = top + self._at[1] * shown_h
        painter.setPen(QPen(CROSS_COLOR, 2))
        painter.drawLine(int(x - 14), int(y), int(x - 5), int(y))
        painter.drawLine(int(x + 5), int(y), int(x + 14), int(y))
        painter.drawLine(int(x), int(y - 14), int(x), int(y - 5))
        painter.drawLine(int(x), int(y + 5), int(x), int(y + 14))
        if self._other is not None and 0.0 <= self._other[0] <= 1.0 \
                and 0.0 <= self._other[1] <= 1.0:
            ox = left + self._other[0] * shown_w
            oy = top + self._other[1] * shown_h
            painter.setPen(QPen(LINK_COLOR, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(int(ox - 6), int(oy - 6), 12, 12)
        painter.end()


class CloseLabelsWindow(QDialog):
    """See the module. ``project_getter()`` returns the current project.
    The window asks the main window to remove labels through
    ``remove_requested`` (a list of ids) and to show one on the map
    through ``reveal_requested``; it never edits the project itself."""

    remove_requested = pyqtSignal(list)
    reveal_requested = pyqtSignal(int)

    def __init__(self, project_getter, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clean Close Labels")
        self.setModal(False)
        self._project = project_getter
        self._pairs: list = []
        self._index = 0
        self._kept: set = set()
        self._shown: tuple = ()         # the ids on screen, (left, right)
        self.loader = SnippetLoader(self)
        self.loader.ready.connect(self._on_snippet)
        self._build()
        self._restore()
        self._show_pair()

    # -- layout -------------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Centres within"))
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(0.0, 10000.0)
        self.distance_spin.setDecimals(1)
        self.distance_spin.setSingleStep(1.0)
        self.distance_spin.setSuffix(" px")
        self.distance_spin.setToolTip(
            "Pixels of the image. Two labels on one image closer than "
            "this are a pair to look at.")
        row.addWidget(self.distance_spin)
        self.classes_combo = QComboBox()
        for key, text in CLASS_FILTERS:
            self.classes_combo.addItem(text, key)
        self.classes_combo.setToolTip(
            "Which pairs: of any classes, only two labels of one class, "
            "or only two of different classes")
        row.addWidget(self.classes_combo)
        self.linked_check = QCheckBox("Include linked pairs")
        self.linked_check.setToolTip(
            "Also pairs whose two labels are one linked object already")
        row.addWidget(self.linked_check)
        row.addStretch(1)
        row.addWidget(QLabel("Chip"))
        self.chip_combo = QComboBox()
        for size in CHIP_SIZES:
            self.chip_combo.addItem(f"{size} px", size)
        self.chip_combo.setToolTip("The snippet read around each label")
        self.chip_combo.currentIndexChanged.connect(
            lambda _i: self._show_pair())
        row.addWidget(self.chip_combo)
        self.find_button = QPushButton("Find")
        self.find_button.setDefault(True)
        self.find_button.clicked.connect(self.find)
        row.addWidget(self.find_button)
        layout.addLayout(row)

        self.status_label = QLabel("Find lists every two labels on one "
                                   "image within the distance.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.pair_label = QLabel("")
        self.pair_label.setWordWrap(True)
        layout.addWidget(self.pair_label)

        pictures = QHBoxLayout()
        self.pictures = (_Picture(), _Picture())
        self.captions = (QLabel(""), QLabel(""))
        self.show_buttons = (QPushButton("Show on map"),
                             QPushButton("Show on map"))
        self.remove_buttons = (QPushButton("Remove left (1)"),
                               QPushButton("Remove right (2)"))
        for side in (0, 1):
            column = QVBoxLayout()
            column.addWidget(self.pictures[side])
            self.captions[side].setWordWrap(True)
            self.captions[side].setAlignment(Qt.AlignTop)
            column.addWidget(self.captions[side])
            buttons = QHBoxLayout()
            buttons.addWidget(self.show_buttons[side])
            buttons.addWidget(self.remove_buttons[side])
            column.addLayout(buttons)
            pictures.addLayout(column, 1)
            self.show_buttons[side].clicked.connect(
                lambda _c, s=side: self._reveal(s))
            self.remove_buttons[side].clicked.connect(
                lambda _c, s=side: self.remove(s))
        layout.addLayout(pictures, 1)

        row = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.previous_button.setToolTip("Left arrow")
        self.previous_button.clicked.connect(self.previous)
        row.addWidget(self.previous_button)
        self.keep_button = QPushButton("Keep both (K)")
        self.keep_button.setToolTip(
            "Leave the two as they are and go to the next pair")
        self.keep_button.clicked.connect(self.keep)
        row.addWidget(self.keep_button)
        self.both_button = QPushButton("Remove both (3)")
        self.both_button.clicked.connect(lambda: self.remove(None))
        row.addWidget(self.both_button)
        self.next_button = QPushButton("Next")
        self.next_button.setToolTip("Right arrow")
        self.next_button.clicked.connect(self.next)
        row.addWidget(self.next_button)
        row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        row.addWidget(close_button)
        layout.addLayout(row)
        for button in (self.previous_button, self.keep_button,
                       self.both_button, self.next_button, close_button,
                       *self.show_buttons, *self.remove_buttons):
            button.setAutoDefault(False)
        self.resize(760, 560)

    def _restore(self):
        self.distance_spin.setValue(distance_px())
        index = self.classes_combo.findData(class_filter())
        self.classes_combo.setCurrentIndex(max(0, index))
        self.linked_check.setChecked(include_linked())
        self.chip_combo.blockSignals(True)
        self.chip_combo.setCurrentIndex(
            max(0, self.chip_combo.findData(chip_px())))
        self.chip_combo.blockSignals(False)

    def save_settings(self):
        store = app_settings()
        store.setValue(DISTANCE_KEY, float(self.distance_spin.value()))
        store.setValue(CLASSES_KEY, self.classes_combo.currentData())
        store.setValue(LINKED_KEY, "true" if self.linked_check.isChecked()
                       else "false")
        store.setValue(CHIP_KEY, int(self.chip_combo.currentData()))

    # -- the search ---------------------------------------------------------------

    def find(self):
        """Find the pairs with the controls as set, and show the first."""
        self.save_settings()
        self._pairs = find_pairs(self._project(), self.distance_spin.value(),
                                 self.classes_combo.currentData(),
                                 self.linked_check.isChecked())
        self._index = 0
        self._kept = set()
        self.status_label.setText(describe(self._pairs))
        self._show_pair()

    def pairs(self) -> list:
        return list(self._pairs)

    def index(self) -> int:
        return self._index

    def current(self) -> "Pair | None":
        if 0 <= self._index < len(self._pairs):
            return self._pairs[self._index]
        return None

    def refresh(self):
        """The project changed under the window (a removal, an undo):
        pairs with a label gone are dropped, the rest re-read."""
        project = self._project()
        kept = []
        for pair in self._pairs:
            _ia, a = project.get_label_by_id(pair.a.id)
            _ib, b = project.get_label_by_id(pair.b.id)
            if a is None or b is None:
                continue
            pair.a, pair.b = a, b
            kept.append(pair)
        if len(kept) != len(self._pairs):
            self._pairs = kept
            self.status_label.setText(
                describe(self._pairs) + " (after removals)"
                if self._pairs else "Every pair found has been dealt with.")
        self._index = min(self._index, max(0, len(self._pairs) - 1))
        self._show_pair()

    # -- the decisions ------------------------------------------------------------

    def keep(self):
        """Keep both: noted, and on to the next pair."""
        pair = self.current()
        if pair is None:
            return
        self._kept.add(pair.ids)
        self.next()

    def remove(self, side: "int | None"):
        """Remove the left (0), the right (1), or both (None) of the pair
        on screen, through the main window, which records one undo step;
        the pairs that involved the removed labels go."""
        pair = self.current()
        if pair is None:
            return
        ids = list(pair.ids) if side is None else [pair.ids[side]]
        self.remove_requested.emit(ids)
        self.refresh()

    def next(self):
        if self._index + 1 < len(self._pairs):
            self._index += 1
            self._show_pair()

    def previous(self):
        if self._index > 0:
            self._index -= 1
            self._show_pair()

    def _reveal(self, side: int):
        pair = self.current()
        if pair is not None:
            self.reveal_requested.emit(pair.ids[side])

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_P):
            self.previous()
        elif key in (Qt.Key_Right, Qt.Key_N):
            self.next()
        elif key == Qt.Key_K:
            self.keep()
        elif key == Qt.Key_1:
            self.remove(0)
        elif key == Qt.Key_2:
            self.remove(1)
        elif key == Qt.Key_3:
            self.remove(None)
        else:
            super().keyPressEvent(event)

    # -- showing a pair -----------------------------------------------------------

    def _show_pair(self):
        pair = self.current()
        have = pair is not None
        for button in (self.keep_button, self.both_button,
                       *self.remove_buttons, *self.show_buttons):
            button.setEnabled(have)
        self.previous_button.setEnabled(have and self._index > 0)
        self.next_button.setEnabled(have
                                    and self._index + 1 < len(self._pairs))
        if pair is None:
            self._shown = ()
            self.pair_label.setText("")
            for picture, caption in zip(self.pictures, self.captions):
                picture.set_failed("")
                caption.setText("")
            return
        parts = [f"Pair {self._index + 1} of {len(self._pairs)}",
                 pair.image_name, f"{pair.distance:.1f} px apart"]
        if pair.linked:
            parts.append("one linked object")
        if pair.ids in self._kept:
            parts.append("kept")
        self.pair_label.setText("  |  ".join(parts))
        self._shown = pair.ids
        size = int(self.chip_combo.currentData() or DEFAULT_CHIP_PX)
        for side, label in enumerate((pair.a, pair.b)):
            self.captions[side].setText(self._caption(label))
            self.pictures[side].set_reading()
            self.loader.request(label.id, pair.image_path, label.pixel_x,
                                label.pixel_y, size)

    @staticmethod
    def _caption(label) -> str:
        lines = [f"<b>{label.class_name}</b> at ({label.pixel_x:.0f}, "
                 f"{label.pixel_y:.0f}), id {label.id}"]
        if label.description:
            lines.append(label.description)
        made = label.attribution.get("created")
        if made:
            lines.append(f"Labelled by {made['by']}")
        if label.length_m is not None or label.width_m is not None:
            length = "?" if label.length_m is None else f"{label.length_m:.1f}"
            width = "?" if label.width_m is None else f"{label.width_m:.1f}"
            lines.append(f"Size {length} × {width} m")
        return "<br>".join(lines)

    def _on_snippet(self, key, arr):
        if key not in self._shown:
            return
        side = self._shown.index(key)
        pair = self.current()
        if pair is None or pair.ids != self._shown:
            return
        if arr is None:
            self.pictures[side].set_failed()
            return
        label = (pair.a, pair.b)[side]
        other = (pair.b, pair.a)[side]
        size = int(self.chip_combo.currentData() or DEFAULT_CHIP_PX)
        image = self._project().images.get(pair.image_path)
        width = getattr(image, "original_width", 0) or 0
        height = getattr(image, "original_height", 0) or 0
        h, w = arr.shape[:2]
        if width and height:
            x0, y0, w, h = snippet_frame(label.pixel_x, label.pixel_y, size,
                                         width, height)
        else:
            # The size unknown: the frame is centred unless clamped at an
            # edge, which cannot be told; the middle is the best guess.
            x0, y0 = label.pixel_x - w / 2.0, label.pixel_y - h / 2.0
        at = ((label.pixel_x - x0) / w, (label.pixel_y - y0) / h)
        where = ((other.pixel_x - x0) / w, (other.pixel_y - y0) / h)
        self.pictures[side].set_picture(arr, at, where)

    def closeEvent(self, event):
        self.save_settings()
        self.loader.cancel_all()
        super().closeEvent(event)

