"""The single-snippet view: one snippet, zoomable, with paintable layers.

Part of the Snippet Editor package (see app/snippet_editor/__init__.py).
It is the mask editor's paint surface, and the Snippet Editor's Single
view, where each section draws its overlay: masks as coloured layers, and
an arrow for the orientation. Two tools: Paint (left-drag paints the
active mask, right-drag erases) and Orient (left-drag reports a line,
right-click asks for it to be cleared) - what a line MEANS is the host's
business; this reports it in snippet pixels.

Deliberately independent of the rest of the app: it needs numpy and Qt and
nothing else, so it can be read, tested and reused on its own.
"""
import numpy as np

import math

from PyQt5.QtCore import QLineF, QPoint, QPointF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import QScrollArea, QWidget

MASK_SNIPPET_SIZE = 224     # default source pixels painted on
MAX_DISPLAY_PX = 448        # starting-view cap; the user zooms from there
DEFAULT_BRUSH_PX = 12       # brush diameter in SOURCE pixels
MIN_ZOOM = 0.25             # far enough out to survey a huge snippet
MAX_ZOOM = 32.0             # far enough in for single-pixel brushwork

TOOL_PAINT = "paint"        # left-drag paints, right-drag erases
TOOL_ORIENT = "orient"      # left-drag reports a line, right-click clears
# Shorter than this on screen is a click, not a direction - the same
# threshold the orientation grid uses, where screen and source are 1:1.
MIN_LINE_PX = 6
LINE_PREVIEW_COLOR = QColor(0, 220, 255)


def display_scale(size_px: int) -> int:
    """Starting upscale for the paint surface: small snippets get room to
    paint, large ones start on screen (32px -> 4x, 224px -> 2x, 512px+ ->
    1x). Only the initial view - the wheel zooms freely from there.
    """
    return max(1, min(4, MAX_DISPLAY_PX // max(1, size_px)))

# Overlay colours cycle per mask on a snippet. Alpha is applied at paint
# time (active mask brighter than the rest).
MASK_COLORS = [
    QColor(230, 25, 75), QColor(60, 180, 75), QColor(0, 130, 200),
    QColor(245, 130, 48), QColor(145, 30, 180), QColor(70, 240, 240),
    QColor(240, 50, 230), QColor(230, 190, 60),
]


def apply_display_adjust(rgb: np.ndarray, brightness: int,
                         contrast: int) -> np.ndarray:
    """``rgb`` brightened and contrast-stretched FOR DISPLAY ONLY.

    Both are -100..100, zero meaning untouched. Contrast pivots around
    mid-grey so a stretch opens a flat snippet out rather than washing it
    to white, and the result is clipped back into the byte range.

    Nothing that is measured or stored passes through here. The mask is
    what the user painted, and the object-vs-background statistics are
    computed from the raw source values, so neither moves when a slider
    does - a panel that reported whatever contrast happened to be set
    would be worse than no panel.
    """
    if not brightness and not contrast:
        return rgb
    # -100..100 -> 0..(near) 2, so -100 flattens and +100 roughly doubles
    # the spread. Capped below 1 so the divisor can never reach zero.
    gain = (1.0 + contrast / 100.0) if contrast >= 0 else \
        max(0.02, 1.0 + contrast / 110.0)
    adjusted = (rgb.astype(np.float32) - 128.0) * gain + 128.0
    adjusted += brightness * 1.27
    return np.clip(adjusted, 0, 255).astype(np.uint8)


class MaskPaintCanvas(QWidget):
    """The snippet, upscaled for painting, with paintable mask overlays."""

    stroke_finished = pyqtSignal()
    # Orient tool: a line from (sx, sy) to (ex, ey) in SNIPPET pixels -
    # floats, since a zoomed view resolves finer than a pixel.
    vector_drawn = pyqtSignal(float, float, float, float)
    # Orient tool: right-click - clear this snippet's orientation.
    orientation_clear_requested = pyqtSignal()

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
        # Whether a stroke may paint over pixels another mask owns. Off by
        # default, by request: masks are normally exclusive regions. The
        # wall is the union of the OTHER layers, built on the first stamp
        # of a stroke and dropped at its end - a drag is hundreds of
        # stamps, and the other layers cannot change mid-stroke.
        self._allow_overlap = False
        self._blocked: "np.ndarray | None" = None
        self._w = self._h = MASK_SNIPPET_SIZE
        self._scale = float(display_scale(MASK_SNIPPET_SIZE))
        self._pan_last = None       # global pos while drag-panning
        # No imagery means no idea what is being painted over, and (when the
        # image size is unknown too) no idea where in the source the strokes
        # would land - so the canvas refuses them outright.
        self._read_only = False
        # The Masks section switched off: no layers drawn, no strokes taken.
        # Kept apart from _read_only, which means "this image cannot be
        # read" and carries its own cursor and message.
        self._layers_shown = True
        self._tool = TOOL_PAINT
        # The orientation arrow, drawn back from a stored angle: (radians,
        # colour, centre in snippet pixels) or None. The colour is the
        # caller's - drawn or propagated is not this widget's business.
        self._arrow = None
        self._line_start: "QPointF | None" = None   # an Orient drag
        self._line_now: "QPointF | None" = None
        # Brush preview: the cell under the cursor plus the outline edges of
        # the exact pixel set a stamp there would paint.
        self._hover_cell = None
        self._brush_edges = self._footprint_edges(self._brush_px)
        self._update_fixed_size()
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    # -- data ---------------------------------------------------------------

    def set_snippet(self, arr: "np.ndarray | None", width: int, height: int):
        """Show a snippet's display pixels; masks are set separately."""
        self._w, self._h = width, height
        self._scale = float(display_scale(max(width, height)))
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
        self._blocked = None
        self._overlay_cache.clear()
        self.update()

    def set_active(self, name: "str | None"):
        self._active = name
        self._blocked = None
        self._overlay_cache.clear()
        self.update()

    def allow_overlap(self) -> bool:
        return self._allow_overlap

    def set_allow_overlap(self, allow: bool):
        self._allow_overlap = bool(allow)
        self._blocked = None

    def end_stroke(self):
        """A stroke is over: the next one rebuilds its wall from scratch."""
        self._blocked = None

    def _wall(self) -> "np.ndarray | None":
        """Pixels this stroke may not paint: every other mask's, when
        overlap is off. None when there is nothing to stop."""
        if self._allow_overlap or self._active is None:
            return None
        if self._blocked is None:
            others = [layer for name, layer in self._layers.items()
                      if name != self._active and layer is not None]
            if not others:
                return None
            wall = np.zeros((self._h, self._w), dtype=bool)
            for layer in others:
                wall |= layer
            self._blocked = wall
        return self._blocked

    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str):
        """TOOL_PAINT or TOOL_ORIENT."""
        self._tool = tool
        self._line_start = self._line_now = None
        self.update()

    def arrow(self) -> "tuple | None":
        return self._arrow

    def set_arrow(self, angle_rad: "float | None", color: QColor,
                  centre: "tuple | None"):
        """Show an orientation arrow through ``centre`` (snippet pixels),
        or none when ``angle_rad`` is None. The angle is the stored
        convention's: counter-clockwise from +x with y UP."""
        self._arrow = (None if angle_rad is None or centre is None
                       else (angle_rad, QColor(color), tuple(centre)))
        self.update()

    def layers_shown(self) -> bool:
        return self._layers_shown

    def set_layers_shown(self, shown: bool):
        """Draw the mask layers and take strokes - or neither."""
        self._layers_shown = bool(shown)
        self.update()

    def set_read_only(self, read_only: bool):
        """Show the masks but accept no strokes."""
        self._read_only = bool(read_only)
        self.setCursor(Qt.ForbiddenCursor if self._read_only
                       else Qt.CrossCursor)

    def invalidate_layer(self, name: str):
        """Redraw one layer whose array was changed outside a stroke."""
        self._overlay_cache.pop(name, None)
        self.update()

    def set_brush(self, px: int):
        self._brush_px = max(1, int(px))
        self._brush_edges = self._footprint_edges(self._brush_px)
        self.update()

    @staticmethod
    def _footprint_cells(brush_px: int) -> np.ndarray:
        """The exact pixel disc _stamp paints, centred in a small array.

        MUST mirror _stamp's formula - the brush preview draws this set's
        outline, and an outline of anything else would be a lie.
        """
        r = max(0.5, brush_px / 2.0)
        reach = int(r) + 1
        yy, xx = np.ogrid[-reach:reach + 1, -reach:reach + 1]
        return (xx * xx + yy * yy) <= r * r

    @classmethod
    def _footprint_edges(cls, brush_px: int) -> list:
        """Boundary segments of the brush footprint, in cell units.

        Coordinates are relative to the top-left corner of the CENTRE cell:
        painted cell (dx, dy) occupies the unit square at (dx, dy). A
        segment is emitted wherever a painted cell borders an unpainted one,
        which traces the pixelated outer edge of the brush - the wireframe.
        """
        cells = cls._footprint_cells(brush_px)
        reach = cells.shape[0] // 2
        edges = []
        h, w = cells.shape
        for iy in range(h):
            for ix in range(w):
                if not cells[iy, ix]:
                    continue
                dx, dy = ix - reach, iy - reach
                if iy == 0 or not cells[iy - 1, ix]:
                    edges.append((dx, dy, dx + 1, dy))          # top
                if iy == h - 1 or not cells[iy + 1, ix]:
                    edges.append((dx, dy + 1, dx + 1, dy + 1))  # bottom
                if ix == 0 or not cells[iy, ix - 1]:
                    edges.append((dx, dy, dx, dy + 1))          # left
                if ix == w - 1 or not cells[iy, ix + 1]:
                    edges.append((dx + 1, dy, dx + 1, dy + 1))  # right
        return edges

    def _update_fixed_size(self):
        self.setFixedSize(QSize(max(1, round(self._w * self._scale)),
                                max(1, round(self._h * self._scale))))

    # -- zoom and pan -------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._scale

    def _scroll_area(self) -> "QScrollArea | None":
        widget = self.parentWidget()
        while widget is not None and not isinstance(widget, QScrollArea):
            widget = widget.parentWidget()
        return widget

    def set_zoom(self, scale: float, anchor=None):
        """Zoom the paint surface, keeping ``anchor`` (widget pos) fixed.

        The canvas lives in a scroll area; zooming resizes the widget, and
        the scrollbars are nudged so the mask pixel under the cursor stays
        under the cursor - the standard image-editor feel.
        """
        scale = max(MIN_ZOOM, min(MAX_ZOOM, float(scale)))
        if scale == self._scale:
            return
        old = self._scale
        area = self._scroll_area()
        viewport_pos = None
        if anchor is not None and area is not None:
            # Capture where the anchor sits in the viewport BEFORE the
            # resize moves everything around.
            viewport_pos = self.mapTo(area.viewport(), anchor)
        # The mask-space point to hold steady.
        ax = (anchor.x() / old) if anchor is not None else self._w / 2.0
        ay = (anchor.y() / old) if anchor is not None else self._h / 2.0
        self._scale = scale
        self._update_fixed_size()
        if area is not None and viewport_pos is not None:
            area.horizontalScrollBar().setValue(
                round(ax * scale - viewport_pos.x()))
            area.verticalScrollBar().setValue(
                round(ay * scale - viewport_pos.y()))
        self.update()

    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        if dy == 0:
            event.ignore()      # a sideways gesture is not a zoom-out
            return
        factor = 1.25 if dy > 0 else 1 / 1.25
        self.set_zoom(self._scale * factor, anchor=event.pos())
        event.accept()

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
        if self._layers_shown:
            for name in self._order:
                if name in self._layers:
                    painter.drawImage(target, self._overlay_image(name))
            if self._tool == TOOL_PAINT:
                self._draw_brush_preview(painter)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._line_start is not None and self._line_now is not None:
            self._draw_arrow(painter, self._line_start, self._line_now,
                             LINE_PREVIEW_COLOR)
        elif self._arrow is not None:
            rad, color, (cx, cy) = self._arrow
            # A fixed length on screen, through the centre; the minus
            # because the convention's y grows up and the screen's down.
            half = 0.35 * min(self._w, self._h) * self._scale
            x, y = cx * self._scale, cy * self._scale
            dx, dy = math.cos(rad) * half, -math.sin(rad) * half
            self._draw_arrow(painter, QPointF(x - dx, y - dy),
                             QPointF(x + dx, y + dy), color)
        painter.end()

    @staticmethod
    def _draw_arrow(painter, start: QPointF, end: QPointF, color: QColor):
        """A shaft and a filled head, as the orientation grid draws them."""
        painter.setPen(QPen(color, 2))
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        head = 10.0
        left = QPointF(end.x() - head * math.cos(angle - 0.5),
                       end.y() - head * math.sin(angle - 0.5))
        right = QPointF(end.x() - head * math.cos(angle + 0.5),
                        end.y() - head * math.sin(angle + 0.5))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end, left, right]))

    def _draw_brush_preview(self, painter):
        """Wireframe of the exact pixels the next stamp would paint.

        Drawn twice - a dark line under a light one - so the outline reads
        on bright and dark imagery alike.
        """
        if (self._hover_cell is None or self._pan_last is not None
                or self._active is None or self._active not in self._layers):
            return
        cx, cy = self._hover_cell
        s = self._scale
        lines = [QLineF((cx + x1) * s, (cy + y1) * s,
                        (cx + x2) * s, (cy + y2) * s)
                 for x1, y1, x2, y2 in self._brush_edges]
        for color, width in ((QColor(0, 0, 0, 200), 3),
                             (QColor(255, 255, 255, 230), 1)):
            pen = QPen(color, width)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLines(lines)

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
        if self._stroke_value:
            # Erasing is never blocked: it only ever removes from this
            # mask, so it cannot create an overlap.
            wall = self._wall()
            if wall is not None:
                disc = disc & ~wall[y_lo:y_hi, x_lo:x_hi]
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
        # Shift+left-drag pans the zoomed view (middle-drag still works for
        # those with the habit); plain left/right stay paint/erase.
        if (event.button() == Qt.MiddleButton
                or (event.button() == Qt.LeftButton
                    and event.modifiers() & Qt.ShiftModifier)):
            self._pan_last = event.globalPos()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self._tool == TOOL_ORIENT:
            if event.button() == Qt.LeftButton:
                self._line_start = self._line_now = QPointF(event.pos())
            elif event.button() == Qt.RightButton:
                self.orientation_clear_requested.emit()
            return
        if (self._read_only or not self._layers_shown
                or self._active is None
                or self._active not in self._layers):
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
        # The brush preview follows the cursor whatever else is going on.
        self._hover_cell = self._to_mask_point(event.pos())
        if self._pan_last is not None:
            area = self._scroll_area()
            if area is not None:
                delta = event.globalPos() - self._pan_last
                hbar = area.horizontalScrollBar()
                vbar = area.verticalScrollBar()
                hbar.setValue(hbar.value() - delta.x())
                vbar.setValue(vbar.value() - delta.y())
            self._pan_last = event.globalPos()
            return
        if self._line_start is not None:
            self._line_now = QPointF(event.pos())
            self.update()
            return
        if self._painting:
            self._stroke_to(event.pos())
        else:
            self.update()

    def leaveEvent(self, _event):
        self._hover_cell = None
        self.update()

    def mouseReleaseEvent(self, event):
        if (self._pan_last is not None
                and event.button() in (Qt.MiddleButton, Qt.LeftButton)):
            self._pan_last = None
            self.setCursor(Qt.CrossCursor)
            return
        if self._line_start is not None and event.button() == Qt.LeftButton:
            start, end = self._line_start, QPointF(event.pos())
            self._line_start = self._line_now = None
            self.update()
            if math.hypot(end.x() - start.x(),
                          end.y() - start.y()) >= MIN_LINE_PX:
                self.vector_drawn.emit(
                    start.x() / self._scale, start.y() / self._scale,
                    end.x() / self._scale, end.y() / self._scale)
            return
        if self._painting and event.button() in (Qt.LeftButton,
                                                 Qt.RightButton):
            self._painting = False
            self._last_pos = None
            self.end_stroke()
            self.stroke_finished.emit()
