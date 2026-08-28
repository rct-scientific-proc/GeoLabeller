"""Label snippets: raw, un-warped source pixels centred on a label.

This is the one place that turns a label into the pixels around it. The
framing (centered_window) and the contrast stretch (_band_scaling) started
life in the HDF5 export and are shared by the sub-image GeoTIFF export; they
moved here so the snippet sidebar and the orientation editor show EXACTLY
what those exports write - same centring rule, same stretch, byte for byte.
h5_export imports them back from here.

Reads are windowed, so a snippet costs a small decoded window rather than an
image, and everything expensive is cacheable:

- the per-image stretch is computed once per file (it samples the raster);
- decoded snippets are LRU-cached by (path, pixel, size);
- SnippetLoader runs reads on a small pool and drops stale deliveries by
  token, the same idiom the canvas's tile loads use.
"""
from collections import OrderedDict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, QThread, pyqtSignal

from .debug_log import debug


# ---------------------------------------------------------------------------
# Framing and stretch (shared with the exports)
# ---------------------------------------------------------------------------

def centered_window(px: float, py: float, width: int, height: int,
                    img_width: int, img_height: int) -> tuple[int, int]:
    """Top-left of the ``width`` x ``height`` window centred on a label pixel.

    This is the one definition of "centred on a label" shared by the HDF5
    snippet export, the sub-image GeoTIFF export and the snippet views, so
    all frame identical ground for the same label: the label pixel is rounded
    to a whole pixel, the window is placed symmetrically around it, then
    shifted (never cropped) to stay inside the raster - so every snippet is
    exactly HxW, and the label sits dead centre except where that edge shift
    moves it.
    """
    x0 = int(round(px)) - width // 2
    y0 = int(round(py)) - height // 2
    x0 = min(max(x0, 0), max(0, img_width - width))
    y0 = min(max(y0, 0), max(0, img_height - height))
    return x0, y0


def _band_scaling(src):
    """Per-band (low, high) stretch for a non-uint8 raster, else ``None``.

    A plain ``astype`` cast of 16-bit or float imagery wraps values modulo
    256 into noise. Instead, sample the raster once (a decimated read, served
    from overviews when present, with nodata masked out) and derive a
    per-band 2-98 percentile window - the same idea as a viewer's default
    contrast stretch. Every snippet of the image is then scaled through this
    one linear mapping, so snippets stay consistent with each other and with
    how the imagery looks on screen.
    """
    if np.dtype(src.dtypes[0]) == np.uint8:
        return None
    bands = min(src.count, 3)
    out_h = min(src.height, 1024)
    out_w = min(src.width, 1024)
    sample = src.read(indexes=list(range(1, bands + 1)),
                      out_shape=(bands, out_h, out_w), masked=True)
    sample = np.ma.filled(sample.astype("float32"), np.nan)
    lows = np.empty(bands, dtype="float32")
    highs = np.empty(bands, dtype="float32")
    for b in range(bands):
        band = sample[b]
        with np.errstate(all="ignore"):
            lo = np.nanpercentile(band, 2.0)
            hi = np.nanpercentile(band, 98.0)
            if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
                # Degenerate percentiles (e.g. a rare bright object on a flat
                # background): fall back to the full data range.
                lo, hi = np.nanmin(band), np.nanmax(band)
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            lo, hi = 0.0, 1.0  # fully empty/flat band - nothing to stretch
        lows[b], highs[b] = lo, hi
    return lows, highs


def _window_pixels(src, window, channels, nodata, scaling=None):
    """Read a window as an (H, W, C) uint8 array, or None if entirely nodata.

    ``scaling`` is the per-band stretch from :func:`_band_scaling` for
    non-uint8 sources (uint8 data passes through byte-exact).
    """
    data = src.read(window=window)  # (bands, h, w)
    if nodata is not None and bool(np.all(data == nodata)):
        return None
    if scaling is not None:
        lo, hi = scaling
        nb = min(data.shape[0], lo.size)
        scaled = data[:nb].astype("float32")
        scaled -= lo[:, None, None]
        scaled *= (255.0 / np.maximum(hi - lo, 1e-6))[:, None, None]
        data = np.clip(scaled, 0.0, 255.0)
    bands = data.shape[0]
    if channels == 1:
        if bands >= 3:
            lum = (0.299 * data[0].astype("float32")
                   + 0.587 * data[1].astype("float32")
                   + 0.114 * data[2].astype("float32"))
            arr = lum.astype("uint8")[..., np.newaxis]
        else:
            arr = data[0].astype("uint8")[..., np.newaxis]
    else:  # RGB
        if bands >= 3:
            arr = np.transpose(data[:3], (1, 2, 0)).astype("uint8")
        else:
            gray = data[0].astype("uint8")
            arr = np.stack([gray, gray, gray], axis=-1)
    return np.ascontiguousarray(arr)


# ---------------------------------------------------------------------------
# One-shot snippet reads
# ---------------------------------------------------------------------------

# Per-file stretch cache: computing it samples the raster, and a project's
# labels cluster on few files. Never invalidated within a session - source
# imagery does not change under the application.
_scaling_cache: dict[str, object] = {}


def read_label_snippet(image_path: str, pixel_x: float, pixel_y: float,
                       size_px: int) -> np.ndarray | None:
    """The un-warped RGB pixels around one label, export-identical framing.

    Returns an (H, W, 3) uint8 array, clamped to the raster for images
    smaller than the requested size (the exports skip those outright; a
    viewer is more useful showing what exists). None when the window is
    entirely nodata or the file cannot be read.
    """
    try:
        with rasterio.open(image_path) as src:
            if image_path not in _scaling_cache:
                _scaling_cache[image_path] = _band_scaling(src)
            scaling = _scaling_cache[image_path]
            w = min(size_px, src.width)
            h = min(size_px, src.height)
            x0, y0 = centered_window(pixel_x, pixel_y, w, h,
                                     src.width, src.height)
            return _window_pixels(src, Window(x0, y0, w, h), 3,
                                  src.nodata, scaling=scaling)
    except Exception as exc:  # noqa: BLE001 - a bad file costs one thumbnail
        debug(f"snippet read failed: {Path(image_path).name} "
              f"({pixel_x:.0f},{pixel_y:.0f}): {type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Background loader with cache and stale-drop
# ---------------------------------------------------------------------------

class _SnippetSignals(QObject):
    """Per-request signals; carries the token so stale deliveries drop."""
    finished = pyqtSignal(object, object, int)   # key, array-or-None, token


class _SnippetRunnable(QRunnable):
    """One windowed read on the pool."""

    def __init__(self, key, image_path, px, py, size, token, signals):
        super().__init__()
        self._args = (key, image_path, px, py, size, token)
        self._signals = signals
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        key, image_path, px, py, size, token = self._args
        try:
            if self._cancelled:
                arr = None
            else:
                arr = read_label_snippet(image_path, px, py, size)
            self._signals.finished.emit(key, arr, token)
        except Exception:  # noqa: BLE001 - never die silently in a worker
            try:
                self._signals.finished.emit(key, None, token)
            except RuntimeError:
                pass   # loader torn down mid-read


class SnippetLoader(QObject):
    """Reads label snippets off the UI thread, newest request wins per key.

    Consumers call :meth:`request` with any hashable key (a label id) and
    listen on :attr:`ready`. Results are LRU-cached by content identity
    (path, pixel, size), so re-filtering a list re-serves from memory, and a
    re-request of a key while an older read is in flight supersedes it - the
    old delivery is dropped by token, never shown.
    """

    ready = pyqtSignal(object, object)   # key, (H, W, 3) uint8 array

    _CACHE_ENTRIES = 256

    def __init__(self, parent=None, max_workers: int = 4):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(
            min(max_workers, max(1, QThread.idealThreadCount() - 1)))
        self._tokens: dict = {}          # key -> latest token issued
        self._counter = 0
        self._signals_alive: set = set()
        self._cache: OrderedDict = OrderedDict()   # content key -> array

    def request(self, key, image_path: str, pixel_x: float, pixel_y: float,
                size_px: int):
        """Ask for one snippet; `ready` fires with the newest request's data."""
        content = (image_path, int(round(pixel_x)), int(round(pixel_y)),
                   int(size_px))
        cached = self._cache.get(content)
        if cached is not None:
            self._cache.move_to_end(content)
            self.ready.emit(key, cached)
            return
        self._counter += 1
        token = self._counter
        self._tokens[key] = token
        signals = _SnippetSignals()
        self._signals_alive.add(signals)
        signals.finished.connect(
            lambda k, arr, t, s=signals, c=content:
                self._on_finished(k, arr, t, s, c))
        runnable = _SnippetRunnable(key, image_path, pixel_x, pixel_y,
                                    size_px, token, signals)
        self._pool.start(runnable)

    def _on_finished(self, key, arr, token, signals, content):
        self._signals_alive.discard(signals)
        if self._tokens.get(key) != token:
            return   # superseded while reading; a newer delivery is coming
        del self._tokens[key]
        if arr is not None:
            self._cache[content] = arr
            while len(self._cache) > self._CACHE_ENTRIES:
                self._cache.popitem(last=False)
        self.ready.emit(key, arr)

    def cancel_all(self):
        """Forget every outstanding request (e.g. on a filter change)."""
        self._tokens.clear()

    def clear_cache(self):
        """Drop cached pixels (e.g. when snippet size changes everywhere)."""
        self._cache.clear()
