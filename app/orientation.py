"""The orientation editor: draw a heading across each snippet of a class.

A separate window showing a grid of un-warped snippets for one class at a
time. Dragging start->end across a snippet is the object's orientation - a
car's nose, a ship's bow - and yields both stored angles at once: the
unit-circle pixel angle and, for georeferenced imagery, the true-north
heading (see orientation_math for the exact conventions). Right-click
clears. Labels already oriented show their arrow, so the grid doubles as a
review pass.

Snippets come from the shared snippet service, so what the user orients on
is exactly what the exports write. Cells display source pixels 1:1 - no
scaling - so the drawn vector IS a source-pixel vector once the crop's
origin is added back.
"""
import math
from pathlib import Path

import numpy as np
import rasterio

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
    QWidget)

from .debug_log import debug
from .orientation_math import principal_angle_rad, true_heading_deg
from .snippets import SnippetLoader, snippet_frame

SNIPPET_SIZE = 224      # source pixels per cell, shown 1:1
GRID_COLUMNS = 3
MIN_DRAG_PX = 6         # anything shorter is a click, not a direction


class OrientationCell(QWidget):
    """One snippet the user can drag an orientation arrow across."""

    # Drawn start->end in SNIPPET pixel coordinates: (label_id, sx, sy, ex, ey)
    vector_drawn = pyqtSignal(int, float, float, float, float)
    clear_requested = pyqtSignal(int)

    def __init__(self, label_id: int, size: int, parent=None):
        super().__init__(parent)
        self._label_id = label_id
        self._size = size
        self._pixmap: QPixmap | None = None
        self._angle_rad: float | None = None    # committed pixel angle
        self._drag_start: QPointF | None = None
        self._drag_now: QPointF | None = None
        self.setFixedSize(size, size)
        self.setCursor(Qt.CrossCursor)

    # -- data ---------------------------------------------------------------

    def set_pixels(self, arr: np.ndarray):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def set_angle(self, angle_rad: float | None):
        """Show a committed orientation (or clear the arrow)."""
        self._angle_rad = angle_rad
        self.update()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._drag_start is not None and self._drag_now is not None:
            self._draw_arrow(painter, self._drag_start, self._drag_now,
                             QColor(0, 220, 255))
        elif self._angle_rad is not None:
            # Reconstruct a centred arrow from the stored angle; screen y
            # grows downward, the convention's y grows up, hence the minus.
            half = self._size * 0.35
            cx = cy = self._size / 2.0
            dx = math.cos(self._angle_rad) * half
            dy = -math.sin(self._angle_rad) * half
            self._draw_arrow(painter,
                             QPointF(cx - dx, cy - dy),
                             QPointF(cx + dx, cy + dy),
                             QColor(255, 170, 0))
        painter.end()

    @staticmethod
    def _draw_arrow(painter, start: QPointF, end: QPointF, color: QColor):
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        head = 10.0
        left = QPointF(end.x() - head * math.cos(angle - 0.5),
                       end.y() - head * math.sin(angle - 0.5))
        right = QPointF(end.x() - head * math.cos(angle + 0.5),
                        end.y() - head * math.sin(angle + 0.5))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end, left, right]))

    # -- interaction --------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = QPointF(event.pos())
            self._drag_now = self._drag_start
            self.update()
        elif event.button() == Qt.RightButton:
            self.clear_requested.emit(self._label_id)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._drag_now = QPointF(event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._drag_start is None:
            return
        start, end = self._drag_start, QPointF(event.pos())
        self._drag_start = self._drag_now = None
        length = math.hypot(end.x() - start.x(), end.y() - start.y())
        if length < MIN_DRAG_PX:
            self.update()   # a click; keep whatever was committed
            return
        self.vector_drawn.emit(self._label_id, start.x(), start.y(),
                               end.x(), end.y())


class OrientationEditor(QWidget):
    """Grid of one class's snippets, each accepting a drawn orientation."""

    # (label_id, orientation_px_rad or None, orientation_deg or None)
    orientation_changed = pyqtSignal(int, object, object)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Orientation Editor")
        self.setMinimumSize(GRID_COLUMNS * (SNIPPET_SIZE + 24) + 60, 600)
        self._loader = SnippetLoader(self)
        self._loader.ready.connect(self._on_snippet_ready)
        self._entries: list = []
        self._cells: dict[int, OrientationCell] = {}
        self._captions: dict[int, QLabel] = {}
        self._geo_cache: dict[str, tuple] = {}   # path -> (affine, crs, w, h)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Class:"))
        self.class_combo = QComboBox()
        self.class_combo.currentIndexChanged.connect(self._rebuild)
        controls.addWidget(self.class_combo, 1)
        layout.addLayout(controls)

        hint = QLabel(
            "Drag across a snippet from the object's tail to its nose to "
            "set its orientation; right-click clears it.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(12)
        self._scroll.setWidget(self._grid_host)
        layout.addWidget(self._scroll)

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Same entry dicts the snippet sidebar takes; grid follows class."""
        self._entries = list(entries)
        classes = sorted({e["class_name"] for e in self._entries})
        current = self.class_combo.currentText()
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItems(classes)
        if current in classes:
            self.class_combo.setCurrentText(current)
        self.class_combo.blockSignals(False)
        self._rebuild()

    def _rebuild(self):
        self._loader.cancel_all()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cells.clear()
        self._captions.clear()

        wanted = self.class_combo.currentText()
        shown = [e for e in self._entries if e["class_name"] == wanted]
        for i, entry in enumerate(shown):
            label_id = entry["label_id"]
            cell = OrientationCell(label_id, SNIPPET_SIZE)
            cell.vector_drawn.connect(self._on_vector_drawn)
            cell.clear_requested.connect(self._on_clear)
            cell.set_angle(entry.get("orientation_px_rad"))
            caption = QLabel()
            caption.setAlignment(Qt.AlignHCenter)
            box = QVBoxLayout()
            box.setSpacing(2)
            holder = QWidget()
            holder.setLayout(box)
            box.addWidget(cell, alignment=Qt.AlignHCenter)
            box.addWidget(caption)
            self._grid.addWidget(holder, i // GRID_COLUMNS, i % GRID_COLUMNS)
            self._cells[label_id] = cell
            self._captions[label_id] = caption
            self._set_caption(entry)
            self._loader.request(label_id, entry["image_path"],
                                 entry["pixel_x"], entry["pixel_y"],
                                 SNIPPET_SIZE)

    def _entry(self, label_id: int) -> dict | None:
        for entry in self._entries:
            if entry["label_id"] == label_id:
                return entry
        return None

    def _set_caption(self, entry):
        caption = self._captions.get(entry["label_id"])
        if caption is None:
            return
        parts = [entry["image_name"]]
        rad = entry.get("orientation_px_rad")
        deg = entry.get("orientation_deg")
        if rad is not None:
            parts.append(f"{rad:+.3f} rad")
        if deg is not None:
            parts.append(f"{deg:.1f}\N{DEGREE SIGN} true")
        caption.setText("   ".join(parts))

    def _on_snippet_ready(self, label_id, arr):
        cell = self._cells.get(label_id)
        if cell is not None and arr is not None:
            cell.set_pixels(arr)

    # -- geo info -----------------------------------------------------------

    def _geo_info(self, image_path: str):
        """(affine, crs, width, height) straight from the file, cached.

        Read from the source rather than trusted from project metadata, so
        the heading is right even for entries written before the project
        recorded transforms.
        """
        info = self._geo_cache.get(image_path)
        if info is None:
            try:
                with rasterio.open(image_path) as src:
                    info = (src.transform if src.crs is not None else None,
                            src.crs, src.width, src.height)
            except Exception as exc:  # noqa: BLE001 - no heading, still usable
                debug(f"orientation geo info failed: "
                      f"{Path(image_path).name}: {exc}")
                info = (None, None, 0, 0)
            self._geo_cache[image_path] = info
        return info

    # -- committing ---------------------------------------------------------

    def _on_vector_drawn(self, label_id, sx, sy, ex, ey):
        entry = self._entry(label_id)
        if entry is None:
            return
        affine, crs, src_w, src_h = self._geo_info(entry["image_path"])
        if src_w and src_h:
            x0, y0, _w, _h = snippet_frame(
                entry["pixel_x"], entry["pixel_y"], SNIPPET_SIZE,
                src_w, src_h)
        else:
            x0 = y0 = 0     # unreadable file: pixel angle still valid
        col_s, row_s = x0 + sx, y0 + sy
        col_e, row_e = x0 + ex, y0 + ey
        rad = principal_angle_rad(col_s, row_s, col_e, row_e)
        if rad is None:
            return
        deg = true_heading_deg(col_s, row_s, col_e, row_e, affine, crs)
        entry["orientation_px_rad"] = rad
        entry["orientation_deg"] = deg
        self._cells[label_id].set_angle(rad)
        self._set_caption(entry)
        self.orientation_changed.emit(label_id, rad, deg)

    def _on_clear(self, label_id):
        entry = self._entry(label_id)
        if entry is None:
            return
        entry["orientation_px_rad"] = None
        entry["orientation_deg"] = None
        self._cells[label_id].set_angle(None)
        self._set_caption(entry)
        self.orientation_changed.emit(label_id, None, None)
