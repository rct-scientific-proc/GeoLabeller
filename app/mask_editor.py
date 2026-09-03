"""The mask editor: paint named binary masks onto label snippets.

A separate window (Labels > Mask Editor) for marking which pixels of a
snippet belong to the actual object, so its pixel distribution can be
measured against the background. Pick a class, pick a snippet from the
strip on the left, add a named mask, and paint: left-drag paints, right-
drag erases, the brush size is adjustable, and several masks can coexist
on one snippet (they may overlap - each is an independent binary layer
with its own overlay colour).

Masks are stored on the label in SOURCE-pixel anchoring (the crop origin
comes from the shared snippet_frame), run-length encoded - see app/masks.py
for the exact format. Every stroke re-encodes and emits immediately, so
the project (and its autosave) is never behind the screen.

The stats line is the point of the exercise: per-band mean +/- std of the
active mask's pixels versus the background, computed from RAW source
values, never from the display stretch.
"""
import numpy as np

from PyQt5.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
    QWidget)

from .masks import (entry_in_window, fill_enclosed, mask_statistics,
                    merged_entry)
from .snippets import (SnippetLoader, read_label_snippet,
                       read_label_window_raw)

MASK_SNIPPET_SIZE = 224     # default source pixels painted on
MAX_DISPLAY_PX = 448        # paint surface cap; scale adapts to the size
DEFAULT_BRUSH_PX = 12       # brush diameter in SOURCE pixels


def display_scale(size_px: int) -> int:
    """Integer upscale for the paint surface: small snippets get room to
    paint, large ones stay on screen (32px -> 4x, 224px -> 2x, 512px -> 1x).
    """
    return max(1, min(4, MAX_DISPLAY_PX // max(1, size_px)))

# Overlay colours cycle per mask on a snippet. Alpha is applied at paint
# time (active mask brighter than the rest).
MASK_COLORS = [
    QColor(230, 25, 75), QColor(60, 180, 75), QColor(0, 130, 200),
    QColor(245, 130, 48), QColor(145, 30, 180), QColor(70, 240, 240),
    QColor(240, 50, 230), QColor(230, 190, 60),
]


class MaskPaintCanvas(QWidget):
    """The snippet, upscaled for painting, with paintable mask overlays."""

    stroke_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._layers: "dict[str, np.ndarray]" = {}   # name -> bool (h, w)
        self._order: list = []                       # names, colour order
        self._active: "str | None" = None
        self._overlay_cache: dict = {}               # name -> QImage
        self._brush_px = DEFAULT_BRUSH_PX
        self._last_pos: "QPoint | None" = None
        self._stroke_value = True
        self._painting = False
        self._w = self._h = MASK_SNIPPET_SIZE
        self._scale = display_scale(MASK_SNIPPET_SIZE)
        self._update_fixed_size()
        self.setCursor(Qt.CrossCursor)

    # -- data ---------------------------------------------------------------

    def set_snippet(self, arr: "np.ndarray | None", width: int, height: int):
        """Show a snippet's display pixels; masks are set separately."""
        self._w, self._h = width, height
        self._scale = display_scale(max(width, height))
        if arr is None:
            self._pixmap = None
        else:
            h, w = arr.shape[:2]
            image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
            self._pixmap = QPixmap.fromImage(image)
        self._update_fixed_size()
        self.update()

    def set_layers(self, layers: dict, order: list, active: "str | None"):
        self._layers = layers
        self._order = order
        self._active = active
        self._overlay_cache.clear()
        self.update()

    def set_active(self, name: "str | None"):
        self._active = name
        self._overlay_cache.clear()
        self.update()

    def invalidate_layer(self, name: str):
        """Redraw one layer whose array was changed outside a stroke."""
        self._overlay_cache.pop(name, None)
        self.update()

    def set_brush(self, px: int):
        self._brush_px = max(1, int(px))

    def _update_fixed_size(self):
        self.setFixedSize(QSize(self._w * self._scale,
                                self._h * self._scale))

    # -- painting -----------------------------------------------------------

    def _overlay_image(self, name: str) -> QImage:
        cached = self._overlay_cache.get(name)
        if cached is not None:
            return cached
        layer = self._layers[name]
        color = MASK_COLORS[self._order.index(name) % len(MASK_COLORS)]
        alpha = 150 if name == self._active else 70
        h, w = layer.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[layer] = (color.red(), color.green(), color.blue(), alpha)
        image = QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()
        self._overlay_cache[name] = image
        return image

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        target = self.rect()
        if self._pixmap is not None:
            painter.drawPixmap(target, self._pixmap)
        for name in self._order:
            if name in self._layers:
                painter.drawImage(target, self._overlay_image(name))
        painter.end()

    # -- strokes ------------------------------------------------------------

    def _to_mask_point(self, pos) -> "tuple[int, int]":
        return (int(pos.x() / self._scale), int(pos.y() / self._scale))

    def _stamp(self, cx: int, cy: int):
        layer = self._layers.get(self._active)
        if layer is None:
            return
        r = max(0.5, self._brush_px / 2.0)
        x_lo = max(0, int(cx - r)); x_hi = min(self._w, int(cx + r) + 1)
        y_lo = max(0, int(cy - r)); y_hi = min(self._h, int(cy + r) + 1)
        if x_hi <= x_lo or y_hi <= y_lo:
            return
        yy, xx = np.ogrid[y_lo:y_hi, x_lo:x_hi]
        disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        layer[y_lo:y_hi, x_lo:x_hi][disc] = self._stroke_value

    def _stroke_to(self, pos):
        """Stamp along the segment from the previous point (no drag gaps)."""
        x, y = self._to_mask_point(pos)
        if self._last_pos is None:
            self._stamp(x, y)
        else:
            lx, ly = self._last_pos.x(), self._last_pos.y()
            steps = max(1, int(max(abs(x - lx), abs(y - ly))))
            for i in range(steps + 1):
                t = i / steps
                self._stamp(round(lx + (x - lx) * t),
                            round(ly + (y - ly) * t))
        self._last_pos = QPoint(x, y)
        self._overlay_cache.pop(self._active, None)
        self.update()

    def mousePressEvent(self, event):
        if self._active is None or self._active not in self._layers:
            return
        if event.button() == Qt.LeftButton:
            self._stroke_value = True
        elif event.button() == Qt.RightButton:
            self._stroke_value = False
        else:
            return
        self._painting = True
        self._last_pos = None
        self._stroke_to(event.pos())

    def mouseMoveEvent(self, event):
        if self._painting:
            self._stroke_to(event.pos())

    def mouseReleaseEvent(self, event):
        if self._painting and event.button() in (Qt.LeftButton,
                                                 Qt.RightButton):
            self._painting = False
            self._last_pos = None
            self.stroke_finished.emit()


class MaskEditor(QWidget):
    """Window for painting named binary masks on a class's snippets."""

    # (label_id, [mask entries]) - the label's full replacement mask list.
    masks_changed = pyqtSignal(int, list)

    _ID_ROLE = Qt.UserRole

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Mask Editor")
        self._loader = SnippetLoader(self)
        self._loader.ready.connect(self._on_snippet_ready)
        self._entries: list = []
        self._current: "dict | None" = None      # selected entry
        self._frame = (0, 0, MASK_SNIPPET_SIZE, MASK_SNIPPET_SIZE)
        # The as-stored entries, by name: committing a stroke merges the
        # edited window into these, so mask content lying OUTSIDE the
        # current window (painted earlier at a larger size) survives.
        self._stored_by_name: dict = {}
        self._layers: "dict[str, np.ndarray]" = {}
        self._order: list = []
        self._raw = None                          # (bands, h, w) source data
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Class:"))
        self.class_combo = QComboBox()
        self.class_combo.currentIndexChanged.connect(self._rebuild_list)
        controls.addWidget(self.class_combo, 1)
        controls.addWidget(QLabel("Snippet:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(32, 512)
        self.size_spin.setSingleStep(32)
        self.size_spin.setValue(MASK_SNIPPET_SIZE)
        self.size_spin.setSuffix(" px")
        self.size_spin.setToolTip(
            "Snippet size in SOURCE pixels around the label. Masks are\n"
            "anchored to the imagery, so changing size only changes the\n"
            "window you paint in - existing masks stay where they are,\n"
            "including any part outside the current view.")
        self.size_spin.valueChanged.connect(self._on_size_changed)
        controls.addWidget(self.size_spin)
        controls.addWidget(QLabel("Brush:"))
        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(1, 64)
        self.brush_spin.setValue(DEFAULT_BRUSH_PX)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.setToolTip("Brush diameter in source pixels.")
        controls.addWidget(self.brush_spin)
        layout.addLayout(controls)

        hint = QLabel("Left-drag paints the active mask, right-drag erases. "
                      "Masks may overlap; each is its own layer.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()

        # Snippet strip: which label is being painted.
        self.snippet_list = QListWidget()
        self.snippet_list.setIconSize(QSize(96, 96))
        self.snippet_list.setFixedWidth(210)
        self.snippet_list.currentItemChanged.connect(self._on_snippet_picked)
        body.addWidget(self.snippet_list)

        # The paint surface.
        self.canvas = MaskPaintCanvas()
        self.canvas.stroke_finished.connect(self._on_stroke_finished)
        self.brush_spin.valueChanged.connect(self.canvas.set_brush)
        canvas_holder = QVBoxLayout()
        canvas_holder.addWidget(self.canvas, alignment=Qt.AlignTop)
        body.addLayout(canvas_holder, 1)

        # Mask management + statistics.
        side = QVBoxLayout()
        side.addWidget(QLabel("Masks on this snippet:"))
        self.mask_list = QListWidget()
        self.mask_list.setFixedWidth(220)
        self.mask_list.currentItemChanged.connect(self._on_mask_picked)
        side.addWidget(self.mask_list)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add Mask...")
        self.add_button.clicked.connect(self._on_add_mask)
        buttons.addWidget(self.add_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._on_delete_mask)
        buttons.addWidget(self.delete_button)
        side.addLayout(buttons)
        self.fill_button = QPushButton("Fill Enclosed")
        self.fill_button.setToolTip(
            "Draw a closed outline with a thin brush, then fill its\n"
            "inside in one click. Refused when the outline has a gap\n"
            "(nothing is actually enclosed).")
        self.fill_button.clicked.connect(self._on_fill_enclosed)
        side.addWidget(self.fill_button)
        side.addWidget(QLabel("Object vs background (raw values):"))
        self.stats_label = QLabel("-")
        self.stats_label.setWordWrap(True)
        self.stats_label.setFixedWidth(220)
        side.addWidget(self.stats_label)
        side.addStretch(1)
        body.addLayout(side)

        layout.addLayout(body, 1)

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Same entry dicts as the other snippet views (masks included)."""
        self._entries = list(entries)
        classes = sorted({e["class_name"] for e in self._entries})
        current = self.class_combo.currentText()
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItems(classes)
        if current in classes:
            self.class_combo.setCurrentText(current)
        self.class_combo.blockSignals(False)
        self._rebuild_list()

    def _rebuild_list(self):
        self._loader.cancel_all()
        self.snippet_list.blockSignals(True)
        self.snippet_list.clear()
        self.snippet_list.blockSignals(False)
        wanted = self.class_combo.currentText()
        for entry in self._entries:
            if entry["class_name"] != wanted:
                continue
            caption = entry["image_name"]
            n_masks = len(entry.get("masks") or [])
            if n_masks:
                caption += f"  [{n_masks} mask{'s' if n_masks > 1 else ''}]"
            item = QListWidgetItem(caption)
            item.setData(self._ID_ROLE, entry["label_id"])
            self.snippet_list.addItem(item)
            self._loader.request(entry["label_id"], entry["image_path"],
                                 entry["pixel_x"], entry["pixel_y"], 96)
        if self.snippet_list.count():
            self.snippet_list.setCurrentRow(0)
        else:
            self._show_entry(None)

    def _on_snippet_ready(self, label_id, arr):
        for i in range(self.snippet_list.count()):
            item = self.snippet_list.item(i)
            if item.data(self._ID_ROLE) == label_id and arr is not None:
                h, w = arr.shape[:2]
                image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
                item.setIcon(QIcon(QPixmap.fromImage(image)))
                return

    def _entry(self, label_id) -> "dict | None":
        for entry in self._entries:
            if entry["label_id"] == label_id:
                return entry
        return None

    # -- selection ----------------------------------------------------------

    def _on_snippet_picked(self, item, _previous=None):
        self._show_entry(None if item is None
                         else self._entry(item.data(self._ID_ROLE)))

    def _show_entry(self, entry: "dict | None"):
        self._current = entry
        self._layers = {}
        self._order = []
        self._raw = None
        self._stored_by_name = {}
        size = self.size_spin.value()
        if entry is None:
            self.canvas.set_snippet(None, size, size)
            self.canvas.set_layers({}, [], None)
            self._refresh_mask_list()
            self._refresh_stats()
            return
        raw = read_label_window_raw(entry["image_path"], entry["pixel_x"],
                                    entry["pixel_y"], size)
        if raw is not None:
            self._raw, self._frame = raw
        else:
            self._frame = (0, 0, size, size)
        x0, y0, w, h = self._frame
        # Stored masks re-anchor into the current crop (paint-time snippet
        # size may differ from today's).
        for stored in entry.get("masks") or []:
            self._layers[stored["name"]] = entry_in_window(
                stored, x0, y0, w, h)
            self._order.append(stored["name"])
            self._stored_by_name[stored["name"]] = stored
        display = read_label_snippet(entry["image_path"], entry["pixel_x"],
                                     entry["pixel_y"], size)
        self.canvas.set_snippet(display, w, h)
        active = self._order[0] if self._order else None
        self.canvas.set_layers(self._layers, self._order, active)
        self._refresh_mask_list(select=active)
        self._refresh_stats()

    def _on_size_changed(self):
        # Strokes commit as they happen, so the entry dicts already hold the
        # latest masks; re-showing re-anchors them into the new window.
        self._show_entry(self._current)

    # -- mask management ----------------------------------------------------

    def _active_name(self) -> "str | None":
        item = self.mask_list.currentItem()
        return item.text() if item is not None else None

    def _refresh_mask_list(self, select: "str | None" = None):
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for i, name in enumerate(self._order):
            item = QListWidgetItem(name)
            color = MASK_COLORS[i % len(MASK_COLORS)]
            pix = QPixmap(12, 12)
            pix.fill(color)
            item.setIcon(QIcon(pix))
            self.mask_list.addItem(item)
            if name == select:
                self.mask_list.setCurrentItem(item)
        if select is None and self.mask_list.count():
            self.mask_list.setCurrentRow(0)
        self.mask_list.blockSignals(False)
        self.canvas.set_active(self._active_name())

    def _on_mask_picked(self, item, _previous=None):
        self.canvas.set_active(item.text() if item is not None else None)
        self._refresh_stats()

    def _on_add_mask(self):
        if self._current is None:
            return
        name, accepted = QInputDialog.getText(
            self, "New Mask", "Mask name (e.g. hull, deck, shadow):")
        name = name.strip()
        if not accepted or not name:
            return
        if name in self._layers:
            QMessageBox.information(
                self, "Mask exists",
                f"This snippet already has a mask named '{name}'.")
            return
        _x0, _y0, w, h = self._frame
        self._layers[name] = np.zeros((h, w), dtype=bool)
        self._order.append(name)
        self.canvas.set_layers(self._layers, self._order, name)
        self._refresh_mask_list(select=name)
        self._emit_masks()

    def _on_delete_mask(self):
        name = self._active_name()
        if name is None or self._current is None:
            return
        self._layers.pop(name, None)
        self._stored_by_name.pop(name, None)
        if name in self._order:
            self._order.remove(name)
        nxt = self._order[0] if self._order else None
        self.canvas.set_layers(self._layers, self._order, nxt)
        self._refresh_mask_list(select=nxt)
        self._emit_masks()
        self._refresh_stats()

    # -- persistence + stats ------------------------------------------------

    def _on_fill_enclosed(self):
        """Fill the active mask's enclosed interior (a drawn hull)."""
        name = self._active_name()
        if name is None or name not in self._layers:
            return
        filled, added = fill_enclosed(self._layers[name])
        if added == 0:
            QMessageBox.information(
                self, "Nothing enclosed",
                f"'{name}' does not fully enclose any area - the outline "
                "probably has a gap. Close the hull and try again.")
            return
        self._layers[name][:] = filled
        self.canvas.invalidate_layer(name)
        self._emit_masks()
        self._refresh_stats()

    def _on_stroke_finished(self):
        self._emit_masks()
        self._refresh_stats()

    def _emit_masks(self):
        if self._current is None:
            return
        x0, y0, _w, _h = self._frame
        entries = [merged_entry(name, x0, y0, self._layers[name],
                                self._stored_by_name.get(name))
                   for name in self._order]
        self._stored_by_name = {e["name"]: e for e in entries}
        self._current["masks"] = entries
        self._update_snippet_caption()
        self.masks_changed.emit(self._current["label_id"], entries)

    def _update_snippet_caption(self):
        item = self.snippet_list.currentItem()
        if item is None or self._current is None:
            return
        caption = self._current["image_name"]
        n = len(self._order)
        if n:
            caption += f"  [{n} mask{'s' if n > 1 else ''}]"
        item.setText(caption)

    def _refresh_stats(self):
        name = self._active_name()
        if (name is None or self._raw is None
                or name not in self._layers):
            self.stats_label.setText("-")
            return
        stats = mask_statistics(self._raw, self._layers[name])
        if stats is None:
            self.stats_label.setText(
                "Paint some pixels to compare the object's raw values "
                "with the background.")
            return
        lines = [f"'{name}': {stats['pixels_object']} px object, "
                 f"{stats['pixels_background']} px background"]
        for band, (om, os_, bm, bs) in enumerate(zip(
                stats["object_mean"], stats["object_std"],
                stats["background_mean"], stats["background_std"]), 1):
            lines.append(f"B{band}: obj {om:.1f}\N{PLUS-MINUS SIGN}{os_:.1f}"
                         f"  bg {bm:.1f}\N{PLUS-MINUS SIGN}{bs:.1f}")
        self.stats_label.setText("\n".join(lines))
