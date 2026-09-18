"""The orientation section: draw a heading across each snippet of a class.

Part of the Snippet Editor package (see app/snippet_editor/__init__.py).
Built as its own window today (Labels > Orientation Editor) or, with
window=False, as a plain panel another window can hold.

A grid of un-warped snippets for one class at a
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
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget)

from ..debug_log import debug
from ..orientation_math import (
    pixel_angle_from_heading, principal_angle_rad, true_heading_deg)
from ..snippets import SnippetLoader, snippet_frame
from .section import Section
from .single_view import TOOL_ORIENT
from .strip import SnippetStrip, Worklist

SNIPPET_SIZE = 224      # source pixels per cell, shown 1:1
GRID_COLUMNS = 3
# Cells built at once. Every cell is a 224 px widget with its own pixmap
# and a queued file read, so building the whole class was 10.3 s of frozen
# window for 4,000 labels (and superlinear: 0.3 s at 200, 1.4 s at 1,000).
# A page is instant, and the work for labels nobody has scrolled to is
# never done at all.
PAGE_SIZE = 60
MIN_DRAG_PX = 6         # anything shorter is a click, not a direction

# Committed-orientation colours. Amber: drawn by hand on this snippet.
# Violet: propagated from a linked label's heading (a different calculation -
# derived through this image's georeferencing, not drawn). A cell with either
# also gets a border in the same colour, so finished snippets read at a
# glance. Drawing over a violet arrow turns it amber.
MANUAL_COLOR = QColor(255, 170, 0)
DERIVED_COLOR = QColor(175, 110, 255)


def _oriented(entry: dict) -> bool:
    return entry.get("orientation_px_rad") is not None


# What "done" means for this section in the shared snippet strip: the label
# has an orientation, drawn or propagated.
ORIENTATION_WORKLIST = Worklist(
    needs="Needs orientation", done="Oriented",
    is_done=_oriented,
    badge=lambda entry: "  [oriented]" if _oriented(entry) else "",
    nothing_left="Every snippet here has an orientation. "
                 "Nothing left to do.",
    none_done="No snippet here has an orientation yet.")


class OrientationCell(QWidget):
    """One snippet the user can drag an orientation arrow across."""

    # Drawn start->end in SNIPPET pixel coordinates: (label_id, sx, sy, ex, ey)
    vector_drawn = pyqtSignal(int, float, float, float, float)
    clear_requested = pyqtSignal(int)
    # Double-click: look at this one closer (the Snippet Editor opens it
    # in its Single view).
    open_requested = pyqtSignal(int)

    def __init__(self, label_id: int, size: int, parent=None):
        super().__init__(parent)
        self._label_id = label_id
        self._size = size
        self._pixmap: QPixmap | None = None
        self._angle_rad: float | None = None    # committed pixel angle
        self._derived = False                   # propagated, not drawn
        self._drag_start: QPointF | None = None
        self._drag_now: QPointF | None = None
        # The Orientation section switched off: no arrow, no border, no
        # drawing - but a double-click still opens the snippet.
        self._orientation_shown = True
        self.setFixedSize(size, size)
        self.setCursor(Qt.CrossCursor)

    # -- data ---------------------------------------------------------------

    def set_pixels(self, arr: np.ndarray):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def set_angle(self, angle_rad: float | None, derived: bool = False):
        """Show a committed orientation (or clear the arrow).

        ``derived`` selects the violet propagated-from-a-link colour instead
        of the amber drawn-by-hand one.
        """
        self._angle_rad = angle_rad
        self._derived = derived and angle_rad is not None
        self.update()

    def orientation_shown(self) -> bool:
        return self._orientation_shown

    def set_orientation_shown(self, shown: bool):
        self._orientation_shown = bool(shown)
        self._drag_start = self._drag_now = None
        self.setCursor(Qt.CrossCursor if shown else Qt.ArrowCursor)
        self.update()

    def _committed_color(self) -> QColor:
        return DERIVED_COLOR if self._derived else MANUAL_COLOR

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._orientation_shown:
            painter.end()
            return
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
                             self._committed_color())
        if self._angle_rad is not None:
            # Oriented snippets get a border in the arrow's colour, so the
            # grid shows what is done (and how) without reading captions.
            pen = QPen(self._committed_color(), 3)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(pen)
            painter.drawRect(1, 1, self._size - 3, self._size - 3)
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
        if not self._orientation_shown:
            return
        if event.button() == Qt.LeftButton:
            self._drag_start = QPointF(event.pos())
            self._drag_now = self._drag_start
            self.update()
        elif event.button() == Qt.RightButton:
            self.clear_requested.emit(self._label_id)

    def mouseDoubleClickEvent(self, event):
        # A double-click is two clicks, not a drag: drop anything the
        # second press started, so its release draws nothing.
        self._drag_start = self._drag_now = None
        if event.button() == Qt.LeftButton:
            self.open_requested.emit(self._label_id)
        self.update()

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

    # (label_id, orientation_px_rad or None, orientation_deg or None,
    #  derived) - derived is True for orientations propagated from a linked
    # label's heading rather than drawn on this snippet.
    orientation_changed = pyqtSignal(int, object, object, bool)
    # A cell was double-clicked: (label_id).
    open_requested = pyqtSignal(int)

    def __init__(self, parent=None, window: bool = True,
                 strip: "SnippetStrip | None" = None):
        # A window of its own by default (Labels > Orientation Editor);
        # window=False builds it as a plain panel for another window to
        # hold - the Snippet Editor's Grid view.
        super().__init__(parent, Qt.Window if window else Qt.Widget)
        if window:
            self.setWindowTitle("Orientation Editor")
            self.setMinimumSize(GRID_COLUMNS * (SNIPPET_SIZE + 24) + 60, 600)
        self._loader = SnippetLoader(self)
        self._loader.ready.connect(self._on_snippet_ready)
        self._entries: list = []
        self._entries_by_id: dict = {}
        self._page = 0          # which page of PAGE_SIZE cells is built
        self._cells: dict[int, OrientationCell] = {}
        self._captions: dict[int, QLabel] = {}
        self._geo_cache: dict[str, tuple] = {}   # path -> (affine, crs, w, h)
        # HOSTED - a shared strip passed in - the grid lays out whatever the
        # strip lists, in the strip's order: its class choice ("All
        # classes" included) and its Show filter replace this grid's own
        # class picker and Unoriented-only box, which are then left out.
        self._strip = strip
        self._orientation_shown = True
        self._setup_ui()
        if strip is not None:
            strip.rebuilt.connect(self._on_strip_rebuilt)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.class_combo = QComboBox()
        # A different class is a different set of labels: start at its
        # first page rather than wherever the last class was.
        self.class_combo.currentIndexChanged.connect(
            self._on_filter_changed)
        if self._strip is None:
            controls.addWidget(QLabel("Class:"))
            controls.addWidget(self.class_combo, 1)

        # Draw once, orient the whole linked group: the heading measured
        # here is re-derived into each linked label's own image (violet =
        # propagated; drawing over one makes it amber/manual again).
        self.propagate_check = QCheckBox("Apply heading to linked labels")
        self.propagate_check.setChecked(True)
        self.propagate_check.setToolTip(
            "When a label has linked labels (same object on other images),\n"
            "give them the drawn TRUE-NORTH heading too - each one's pixel\n"
            "angle is derived through its own image's georeferencing.\n"
            "Propagated orientations show violet until drawn over.")
        if self._strip is None:
            # Hosted, the toggle is the host's to place - the Snippet
            # Editor puts it in its Orientation section.
            controls.addWidget(self.propagate_check)
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

        pager = QHBoxLayout()
        self.unoriented_check = QCheckBox("Unoriented only")
        self.unoriented_check.setToolTip(
            "Show only labels that have no orientation yet - what a review\n"
            "pass is looking for.")
        self.unoriented_check.toggled.connect(self._on_filter_changed)
        if self._strip is None:
            pager.addWidget(self.unoriented_check)
        pager.addStretch(1)
        self.prev_button = QPushButton("< Previous")
        self.prev_button.clicked.connect(lambda: self._step_page(-1))
        pager.addWidget(self.prev_button)
        self.page_label = QLabel("")
        pager.addWidget(self.page_label)
        self.next_button = QPushButton("Next >")
        self.next_button.clicked.connect(lambda: self._step_page(1))
        pager.addWidget(self.next_button)
        layout.addLayout(pager)

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Same entry dicts the snippet sidebar takes; grid follows class."""
        if self._strip is not None:
            self._strip.set_labels(entries)     # rebuilt -> the grid
            return
        self._entries = list(entries)
        self._entries_by_id = {e["label_id"]: e for e in self._entries}
        classes = sorted({e["class_name"] for e in self._entries})
        current = self.class_combo.currentText()
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItems(classes)
        if current in classes:
            self.class_combo.setCurrentText(current)
        self.class_combo.blockSignals(False)
        self._rebuild()

    def _on_filter_changed(self, _checked=False):
        """The filter changes which labels exist, so start from page one."""
        self._page = 0
        self._rebuild()

    def _step_page(self, delta: int):
        pages = max(1, self._page_count())
        self._page = max(0, min(pages - 1, self._page + delta))
        self._rebuild()

    def _on_strip_rebuilt(self, chosen_anew: bool):
        """The shared strip changed what it lists. A new choice (class,
        filter) starts at page one; new labels keep the page."""
        self._entries = self._strip.entries
        self._entries_by_id = {e["label_id"]: e for e in self._entries}
        if chosen_anew:
            self._page = 0
        self._rebuild()

    def _shown_entries(self) -> list:
        """The labels this class (and filter) covers, in a stable order."""
        if self._strip is not None:
            return self._strip.listed_entries()
        wanted = self.class_combo.currentText()
        shown = [e for e in self._entries if e["class_name"] == wanted]
        if self.unoriented_check.isChecked():
            shown = [e for e in shown
                     if e.get("orientation_px_rad") is None]
        return shown

    def _page_count(self) -> int:
        return max(1, -(-len(self._shown_entries()) // PAGE_SIZE))

    def _rebuild(self):
        self._loader.cancel_all()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cells.clear()
        self._captions.clear()

        every = self._shown_entries()
        pages = max(1, -(-len(every) // PAGE_SIZE))
        self._page = max(0, min(pages - 1, self._page))
        start = self._page * PAGE_SIZE
        shown = every[start:start + PAGE_SIZE]
        self.page_label.setText(
            f"{start + 1}-{start + len(shown)} of {len(every)}"
            if every else "none")
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < pages - 1)
        for i, entry in enumerate(shown):
            label_id = entry["label_id"]
            cell = OrientationCell(label_id, SNIPPET_SIZE)
            cell.vector_drawn.connect(self._on_vector_drawn)
            cell.clear_requested.connect(self._on_clear)
            cell.open_requested.connect(self.open_requested)
            cell.set_angle(entry.get("orientation_px_rad"),
                           derived=bool(entry.get("orientation_derived")))
            cell.set_orientation_shown(self._orientation_shown)
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

    def set_orientation_shown(self, shown: bool):
        """Show arrows and take drawings - or neither (the section is off)."""
        self._orientation_shown = bool(shown)
        for cell in self._cells.values():
            cell.set_orientation_shown(shown)
        for entry in self._entries:
            self._set_caption(entry)

    def _entry(self, label_id: int) -> dict | None:
        return getattr(self, "_entries_by_id", {}).get(label_id)

    def _set_caption(self, entry):
        caption = self._captions.get(entry["label_id"])
        if caption is None:
            return
        parts = [entry["image_name"]]
        if (self._strip is not None and self._strip.class_combo.currentText()
                == SnippetStrip.ALL_CLASSES):
            parts = [f"{entry['class_name']} \N{MIDDLE DOT} "
                     f"{entry['image_name']}"]
        rad = entry.get("orientation_px_rad")
        deg = entry.get("orientation_deg")
        if not self._orientation_shown:
            rad = deg = None
        if rad is not None:
            # + 0.0 turns the -0.0 a due-right drag produces into 0.0: the
            # screen's y axis is flipped into the convention's, and "-0.000
            # rad" for a horizontal arrow reads as a mistake.
            parts.append(f"{rad + 0.0:+.3f} rad")
        if deg is not None:
            parts.append(f"{deg:.1f}\N{DEGREE SIGN} true")
        if rad is not None and entry.get("orientation_derived"):
            parts.append("auto")
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
        """A line drawn on a grid cell, in that cell's snippet pixels."""
        entry = self._entry(label_id)
        if entry is None:
            return
        _affine, _crs, src_w, src_h = self._geo_info(entry["image_path"])
        if src_w and src_h:
            x0, y0, _w, _h = snippet_frame(
                entry["pixel_x"], entry["pixel_y"], SNIPPET_SIZE,
                src_w, src_h)
        else:
            x0 = y0 = 0     # unreadable file: pixel angle still valid
        self.orient_from_source_line(label_id, x0 + sx, y0 + sy,
                                     x0 + ex, y0 + ey)

    def orient_from_source_line(self, label_id, col_s, row_s, col_e, row_e):
        """Commit an orientation drawn tail -> nose in SOURCE pixels.

        The one way in for a drawn orientation, whichever view it was
        drawn in: a grid cell adds its crop's origin (above); the Snippet
        Editor's Single view adds its own - its snippet size is the user's
        to set - so the same line on the image always commits the same
        angle, heading and propagation.
        """
        entry = self._entry(label_id)
        if entry is None:
            return
        affine, crs, _w, _h = self._geo_info(entry["image_path"])
        rad = principal_angle_rad(col_s, row_s, col_e, row_e)
        if rad is None:
            return
        deg = true_heading_deg(col_s, row_s, col_e, row_e, affine, crs)
        entry["orientation_px_rad"] = rad
        entry["orientation_deg"] = deg
        # Drawing by hand always wins: even over a propagated (violet) one.
        entry["orientation_derived"] = False
        cell = self._cells.get(label_id)    # may be off the page in view
        if cell is not None:
            cell.set_angle(rad, derived=False)
        self._set_caption(entry)
        self.orientation_changed.emit(label_id, rad, deg, False)
        self._propagate_heading(entry, deg)

    def _propagate_heading(self, source_entry: dict, deg):
        """Give the drawn heading to the source label's linked labels.

        The shared truth between linked labels is the ground heading, not
        the pixel angle - each linked label sits on a different image with
        its own rotation. So every linked label gets orientation_deg as-is
        and an orientation_px_rad derived through ITS image's
        georeferencing, marked (and coloured) as propagated. Skipped
        entirely when the toggle is off; linked labels on non-geo imagery
        are left untouched (no georeferencing to derive through). A label
        the user already oriented BY HAND is never overwritten.
        """
        if (not self.propagate_check.isChecked()
                or deg is None
                or not source_entry.get("linked")):
            return
        object_id = source_entry.get("object_id")
        source_id = source_entry["label_id"]
        for entry in self._entries:
            if (entry.get("object_id") != object_id
                    or entry["label_id"] == source_id):
                continue
            if (entry.get("orientation_px_rad") is not None
                    and not entry.get("orientation_derived")):
                continue    # hand-drawn measurement; keep it
            affine, crs, _w, _h = self._geo_info(entry["image_path"])
            rad = pixel_angle_from_heading(
                deg, entry.get("lon"), entry.get("lat"), affine, crs)
            if rad is None:
                continue
            entry["orientation_px_rad"] = rad
            entry["orientation_deg"] = deg
            entry["orientation_derived"] = True
            cell = self._cells.get(entry["label_id"])
            if cell is not None:
                cell.set_angle(rad, derived=True)
            self._set_caption(entry)
            self.orientation_changed.emit(entry["label_id"], rad, deg, True)

    def _on_clear(self, label_id):
        entry = self._entry(label_id)
        if entry is None:
            return
        entry["orientation_px_rad"] = None
        entry["orientation_deg"] = None
        entry["orientation_derived"] = False
        # Not indexed: the Snippet Editor's Clear button reaches snippets
        # that are not on the page in view.
        cell = self._cells.get(label_id)
        if cell is not None:
            cell.set_angle(None)
        self._set_caption(entry)
        self.orientation_changed.emit(label_id, None, None, False)


class OrientationPanel(QWidget):
    """The Orientation section's panel in the Snippet Editor.

    What the snippet in hand's orientation is - its pixel angle and true
    heading, and whether it was drawn or propagated from a linked label -
    with Clear, and the "apply heading to linked labels" toggle the grid's
    drawing obeys (the grid's own, placed here by the host).
    """

    clear_requested = pyqtSignal(int)          # label_id

    def __init__(self, propagate_check: QCheckBox, parent=None):
        super().__init__(parent)
        self._label_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._readout = QLabel("")
        self._readout.setWordWrap(True)
        layout.addWidget(self._readout)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip(
            "Remove this snippet's orientation (right-click on it in the\n"
            "grid does the same).")
        self.clear_button.clicked.connect(self._on_clear_clicked)
        layout.addWidget(self.clear_button)
        layout.addWidget(propagate_check)
        hint = QLabel("Draw from the object's tail to its nose - on a "
                      "snippet in the Grid view, or with the Orient tool "
                      "in the Single view. Right-click clears it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
        self.show_entry(None)

    def show_entry(self, entry: "dict | None"):
        """Read out ``entry``'s orientation (None: no snippet in hand)."""
        self._label_id = None if entry is None else entry["label_id"]
        rad = None if entry is None else entry.get("orientation_px_rad")
        self.clear_button.setEnabled(rad is not None)
        if entry is None:
            self._readout.setText("No snippet selected.")
            return
        if rad is None:
            self._readout.setText("No orientation yet.")
            return
        deg = entry.get("orientation_deg")
        parts = [f"{rad + 0.0:+.3f} rad"]       # no "-0.000" (see caption)
        parts.append(f"{deg:.1f}\N{DEGREE SIGN} true" if deg is not None
                     else "no georeferencing: pixel angle only")
        text = "   ".join(parts)
        if entry.get("orientation_derived"):
            text += "\nPropagated from a linked label's heading."
        self._readout.setText(text)

    def readout_text(self) -> str:
        return self._readout.text()

    def _on_clear_clicked(self):
        if self._label_id is not None:
            self.clear_requested.emit(self._label_id)


class OrientationSection(Section):
    """The Snippet Editor's Orientation section (see section.py).

    Its panel reads out the snippet in hand. It owns the Orient tool on the
    Single view - a line drawn there goes through the grid's
    orient_from_source_line with the Single view's own crop origin, so it
    commits exactly what the same line drawn on the grid would - and the
    arrow showing the orientation there, through the label's own pixel.
    Switched off: no arrows or drawing in the grid, no arrow on the canvas.
    """

    key = "orientation"
    title = "Orientation"
    tool = (TOOL_ORIENT, "Orient",
            "Drag from the object's tail to its nose to set its\n"
            "orientation; right-click clears it.")

    def __init__(self, grid: OrientationEditor, single, parent=None):
        """``single`` is the Single view's mask editor: its canvas, its
        crop (_frame) and the snippet it shows."""
        super().__init__(ORIENTATION_WORKLIST,
                         OrientationPanel(grid.propagate_check), parent)
        self._grid = grid
        self._single = single
        self._entry = None
        self.panel.clear_requested.connect(grid._on_clear)
        canvas = single.canvas
        canvas.vector_drawn.connect(self._on_canvas_line)
        canvas.orientation_clear_requested.connect(self._on_canvas_clear)
        grid.orientation_changed.connect(
            lambda label_id, *_rest: self.entry_changed.emit(label_id))

    def show_entry(self, entry):
        self._entry = entry
        self.panel.show_entry(entry)
        self._update_arrow()

    def refresh(self, label_id):
        if self._entry is not None and self._entry["label_id"] == label_id:
            self.show_entry(self._entry)

    def set_shown(self, shown):
        super().set_shown(shown)
        self._grid.set_orientation_shown(shown)
        self._update_arrow()

    def _on_canvas_line(self, sx, sy, ex, ey):
        """A line drawn with the Orient tool, in snippet pixels: add the
        Single view's own crop origin, commit it as the grid would."""
        if self._entry is None:
            return
        x0, y0, _w, _h = self._single._frame
        self._grid.orient_from_source_line(self._entry["label_id"],
                                           x0 + sx, y0 + sy, x0 + ex, y0 + ey)

    def _on_canvas_clear(self):
        if self._entry is not None:
            self._grid._on_clear(self._entry["label_id"])

    def _update_arrow(self):
        """The orientation on the Single view - through the label's own
        pixel, which a clamped crop moves off the snippet's middle."""
        canvas = self._single.canvas
        entry = self._entry
        rad = None if entry is None else entry.get("orientation_px_rad")
        if rad is None or not self.shown:
            canvas.set_arrow(None, MANUAL_COLOR, None)
            return
        x0, y0, _w, _h = self._single._frame
        colour = (DERIVED_COLOR if entry.get("orientation_derived")
                  else MANUAL_COLOR)
        canvas.set_arrow(rad, colour, (entry["pixel_x"] - x0,
                                       entry["pixel_y"] - y0))
