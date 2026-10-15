"""The pixel values under the cursor, for the status bar (2.8.1).

The canvas says which image the cursor is over and where; a worker reads
that one pixel off the UI thread, and the status bar shows every band's
value - and, for a band drawn through its colour table, the value's
colour. Newest request wins: a read that finishes after a later request
was made is dropped unseen, and a request queued behind a slow read is
skipped once it is no longer the newest.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

from . import display_settings, gdal_config
from .debug_log import debug

MAX_BANDS = 8                   # values shown before "..."


@dataclass(frozen=True)
class Probe:
    """One pixel's values: one per band, None where the band is nodata;
    and, when one band is drawn through the file's colour table, which
    band and the colour its value takes."""
    values: tuple
    dtype: str
    band: int | None = None
    colour: tuple | None = None

    def _one(self, value) -> str:
        if value is None:
            return "nodata"
        if isinstance(value, float) and not float(value).is_integer():
            return display_settings.format_value(value)
        return str(int(value))

    def text(self) -> str:
        """Rich text for the status bar: ``Value: 3`` with a square in
        the value's colour, or ``Values: 120, 88, 64``."""
        parts = [self._one(v) for v in self.values[:MAX_BANDS]]
        if len(self.values) > MAX_BANDS:
            parts.append("...")
        if self.band is not None and self.colour is not None:
            square = ('<span style="color:#%02x%02x%02x">&#9632;</span>'
                      % tuple(self.colour))
            parts[self.band - 1] += " " + square
        return ("Value: " if len(self.values) == 1 else "Values: ") \
            + ", ".join(parts)


def read_values(image_path: str, x: float, y: float,
                is_pixel: bool) -> "Probe | None":
    """The band values of ``image_path`` under a position - pixel
    ``(x, y)`` when ``is_pixel``, else longitude and latitude - or None
    off the image, or when the file cannot be read."""
    try:
        with gdal_config.opened(image_path) as src:
            if is_pixel:
                col, row = int(math.floor(x)), int(math.floor(y))
            else:
                if src.crs is None:
                    return None
                xs, ys = warp_transform("EPSG:4326", src.crs, [x], [y])
                row, col = src.index(xs[0], ys[0])
            if not (0 <= col < src.width and 0 <= row < src.height):
                return None
            data = src.read(window=Window(col, row, 1, 1))[:, 0, 0]
            nodata = src.nodata
            values = []
            for value in data.tolist():
                empty = (nodata is not None and value == nodata) or (
                    isinstance(value, float) and math.isnan(value))
                values.append(None if empty else value)
            band = display_settings.paletted_band(
                display_settings.for_image(image_path), src)
            colour = None
            if band is not None and values[band - 1] is not None:
                table = display_settings.colour_table(src, band)
                index = int(round(values[band - 1]))
                if table is not None and 0 <= index < len(table):
                    colour = tuple(int(c) for c in table[index])
                else:
                    colour = (0, 0, 0)      # beyond the table: drawn black
            if colour is None:
                band = None
            return Probe(tuple(values), str(src.dtypes[0]), band, colour)
    except Exception as exc:  # noqa: BLE001 - a readout, not a failure
        debug(f"value probe: cannot read {image_path}: "
              f"{type(exc).__name__}: {exc}")
        return None


class _Signals(QObject):
    finished = pyqtSignal(int, object)      # token, Probe or None


class _Runnable(QRunnable):

    def __init__(self, token: int, is_current, image_path: str, x: float,
                 y: float, is_pixel: bool, signals: _Signals):
        super().__init__()
        self.token = token
        self.is_current = is_current
        self.image_path, self.x, self.y = image_path, x, y
        self.is_pixel = is_pixel
        self.signals = signals

    def run(self):
        probe = (read_values(self.image_path, self.x, self.y, self.is_pixel)
                 if self.is_current(self.token) else None)
        self.signals.finished.emit(self.token, probe)


class ValueProbe(QObject):
    """Reads the pixel under the cursor off the UI thread; ``ready``
    carries the newest request's Probe, or None off the image."""

    ready = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._token = 0
        self._alive: set = set()

    def _is_current(self, token: int) -> bool:
        return token == self._token

    def request(self, image_path: str, x: float, y: float, is_pixel: bool):
        self._token += 1
        signals = _Signals()
        self._alive.add(signals)
        signals.finished.connect(
            lambda token, probe, s=signals: self._on_finished(token, probe, s))
        self._pool.start(_Runnable(self._token, self._is_current, image_path,
                                   x, y, is_pixel, signals))

    def clear(self):
        """Forget the request in flight: its answer is dropped."""
        self._token += 1

    def wait(self, ms: int = 5000) -> bool:
        return self._pool.waitForDone(ms)

    def _on_finished(self, token: int, probe, signals):
        self._alive.discard(signals)
        if sip.isdeleted(self):
            return      # the window is gone; emitting now would abort
        if token != self._token:
            return      # superseded; a newer answer is coming
        self.ready.emit(probe)
