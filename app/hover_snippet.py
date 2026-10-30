"""View > Hover Snippet (2.9.2): a label's snippet beside the cursor.

Hold Ctrl and rest the mouse on a label's marker: a small window shows
the snippet around that label, read the way the group's Display
Settings draw it, captioned with its class and image, for a quick look
without opening the Snippet Editor. It goes when the mouse leaves the
marker, Ctrl is released, or anything is clicked. Only with Ctrl held,
so a mouse crossing the canvas while panning raises nothing. The size
of the snippet - source pixels read, and pixels on screen - is chosen in
the View menu, Off among the choices, and remembered.

The canvas notices the rest and asks (label_hover_requested); the main
window, which has the project, reads the snippet off the UI thread
through a SnippetLoader and shows this window.

Oriented as on the map (2.9.16): the snippet used to show the file's
pixels as they lie, the framing the exports and the Snippet Editor use,
while the canvas draws the image warped onto the map - so on a survey
line flown at an angle the snippet looked turned against the image
under the cursor, and users took the object for rotating. By default
the hover snippet is now warped the way the map shows the image (north
up, the canvas's Web Mercator) by read_map_oriented_snippet; View >
Hover Snippet > Oriented as on the map switches it back to the file's
framing. Nothing else - the exports, the editor, the sidebar - changes.
"""
from __future__ import annotations

import math

import numpy as np
from affine import Affine
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject, transform as _transform

from . import display_settings, gdal_config
from .debug_log import debug
from .settings_scope import settings as app_settings
from .snippets import read_display_window, read_label_snippet

HOVER_KEY = "view/hover_snippet_px"
HOVER_SIZES = (0, 96, 128, 192, 256)      # 0 is Off
DEFAULT_HOVER_PX = 128
OFFSET_PX = 16                            # from the cursor
NORTH_UP_KEY = "view/hover_snippet_north_up"
MAP_CRS = CRS.from_epsg(3857)             # what the canvas draws in


def hover_px() -> int:
    """The snippet size chosen, or 0 for Off."""
    try:
        value = int(app_settings().value(HOVER_KEY, DEFAULT_HOVER_PX))
    except (TypeError, ValueError):
        return DEFAULT_HOVER_PX
    return value if value in HOVER_SIZES else DEFAULT_HOVER_PX


def set_hover_px(size: int):
    if size not in HOVER_SIZES:
        raise ValueError(size)
    app_settings().setValue(HOVER_KEY, int(size))


def north_up() -> bool:
    """Is the hover snippet turned the way the map shows the image (the
    default), or shown as its pixels lie in the file."""
    value = app_settings().value(NORTH_UP_KEY, True)
    return str(value).lower() in ("true", "1")


def set_north_up(on: bool):
    app_settings().setValue(NORTH_UP_KEY, bool(on))


def read_hover_snippet(image_path: str, pixel_x: float, pixel_y: float,
                       size_px: int, display=None) -> "np.ndarray | None":
    """What the hover's loader reads with: the map-oriented snippet, or
    the file's framing while View > Hover Snippet > Oriented as on the
    map is unticked - decided at each read, so one loader serves both
    looks (the main window keys its cache by the look, 2.9.17)."""
    reader = read_map_oriented_snippet if north_up() else read_label_snippet
    return reader(image_path, pixel_x, pixel_y, size_px, display=display)


def read_map_oriented_snippet(image_path: str, pixel_x: float,
                              pixel_y: float, size_px: int,
                              display=None) -> "np.ndarray | None":
    """The snippet around a label turned the way the map shows the
    image: north up, in the canvas's Web Mercator, ``size_px`` across
    at the length a source pixel has on the map there. The same
    signature as read_label_snippet, for a SnippetLoader's reader.

    The window of the file the north-up square covers - its four
    corners taken back through the map and the file's own transform,
    and their bounding box with a margin: exact for any turn, any pixel
    shape (lat/lon imagery at high latitude, a 0.25 x 1 m grid) and any
    projection - is read as the group's Display Settings draw it and
    warped onto a grid centred on the label:
    bilinear, or nearest for a paletted display, whose colours are
    classes and must not be blended. A non-georeferenced image has no
    map to turn to and is read as it lies; a warp that fails falls back
    to that too.
    """
    try:
        with gdal_config.opened(image_path) as src:
            width, height = src.width, src.height
            affine, crs = src.transform, src.crs
            shown = (display if display is not None
                     else display_settings.for_image(image_path))
            paletted = display_settings.paletted_band(shown, src) is not None
        if crs is None or affine is None or affine.is_identity:
            return read_label_snippet(image_path, pixel_x, pixel_y, size_px,
                                      display=display)
        size = max(1, int(size_px))
        # Where the label is on the map, and how long a source pixel is
        # there along each of the file's axes; the output's pixel is the
        # mean of the two.
        corners = [affine * (pixel_x, pixel_y), affine * (pixel_x + 1, pixel_y),
                   affine * (pixel_x, pixel_y + 1)]
        xs, ys = _transform(crs, MAP_CRS, [c[0] for c in corners],
                            [c[1] for c in corners])
        along_col = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
        along_row = math.hypot(xs[2] - xs[0], ys[2] - ys[0])
        res = (along_col + along_row) / 2.0
        if (not (along_col > 0 and along_row > 0)
                or not all(map(math.isfinite, (xs[0], ys[0], res)))):
            return read_label_snippet(image_path, pixel_x, pixel_y, size_px,
                                      display=display)
        half = size / 2.0 * res
        box_x = [xs[0] - half, xs[0] + half, xs[0] + half, xs[0] - half]
        box_y = [ys[0] - half, ys[0] - half, ys[0] + half, ys[0] + half]
        sx, sy = _transform(MAP_CRS, crs, box_x, box_y)
        cols, rows = zip(*[~affine * (x, y) for x, y in zip(sx, sy)])
        x0 = max(0, int(math.floor(min(cols))) - 2)
        y0 = max(0, int(math.floor(min(rows))) - 2)
        x1 = min(width, int(math.ceil(max(cols))) + 2)
        y1 = min(height, int(math.ceil(max(rows))) + 2)
        if x1 <= x0 or y1 <= y0:
            return read_label_snippet(image_path, pixel_x, pixel_y, size_px,
                                      display=display)
        rgb = read_display_window(image_path, x0, y0, x1 - x0, y1 - y0,
                                  display=display)
        if rgb is None:
            return None
        dst_transform = Affine(res, 0.0, xs[0] - size / 2.0 * res,
                               0.0, -res, ys[0] + size / 2.0 * res)
        out = np.zeros((3, size, size), dtype=np.uint8)
        reproject(np.ascontiguousarray(rgb.transpose(2, 0, 1)), out,
                  src_transform=affine * Affine.translation(x0, y0),
                  src_crs=crs, dst_transform=dst_transform, dst_crs=MAP_CRS,
                  resampling=(Resampling.nearest if paletted
                              else Resampling.bilinear),
                  dst_nodata=0)
        return np.ascontiguousarray(out.transpose(1, 2, 0))
    except Exception as exc:  # noqa: BLE001 - a look, never a crash
        debug(f"map-oriented snippet failed: {image_path}: "
              f"{type(exc).__name__}: {exc}")
        return read_label_snippet(image_path, pixel_x, pixel_y, size_px,
                                  display=display)


class HoverSnippet(QWidget):
    """The small window: the picture and a caption, placed beside the
    cursor and kept on the screen."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: #1e1e1e; color: white;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.picture)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.caption)
        self.label_id: int | None = None

    def show_at(self, where: QPoint, arr: np.ndarray, caption: str,
                label_id: int | None = None):
        """Show ``arr`` (an RGB array) at its own size, captioned, beside
        the screen point ``where``."""
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self.picture.setPixmap(QPixmap.fromImage(image))   # a copy
        self.caption.setText(caption)
        self.label_id = label_id
        self.adjustSize()
        self.move(self._place(where, self.sizeHint()))
        self.show()

    @staticmethod
    def _place(where: QPoint, size) -> QPoint:
        """Below and right of the cursor, or on the other side of it
        where that would leave the screen."""
        x = where.x() + OFFSET_PX
        y = where.y() + OFFSET_PX
        screen = QApplication.screenAt(where) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if x + size.width() > area.right():
                x = where.x() - OFFSET_PX - size.width()
            if y + size.height() > area.bottom():
                y = where.y() - OFFSET_PX - size.height()
            x = max(area.left(), x)
            y = max(area.top(), y)
        return QPoint(x, y)

    def hideEvent(self, event):
        self.label_id = None
        super().hideEvent(event)
