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
import threading
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


def nodata_mask(data, nodata):
    """Which pixels carry no data - by sentinel AND by NaN.

    Two things mean "nothing here" and only one of them is comparable.
    ``data == nodata`` is False for every NaN, because NaN equals nothing,
    itself included - so a sentinel test alone lets NaN through as if it
    were a measurement. It then poisons a mean (one NaN makes the mean
    NaN), casts to an undefined byte, or hides an entirely empty window
    from the "skip this" check.

    Returns a boolean array shaped like ``data``, or None when nothing in
    it can be nodata (integer imagery with no declared sentinel), so
    callers can skip the work entirely.
    """
    data = np.asarray(data)
    mask = None
    if np.issubdtype(data.dtype, np.floating):
        mask = np.isnan(data)
    if nodata is not None:
        try:
            sentinel = float(nodata)
        except (TypeError, ValueError):
            sentinel = None
        # A declared NaN sentinel is already covered by the isnan pass, and
        # comparing against it would match nothing.
        if sentinel is not None and not np.isnan(sentinel):
            hit = data == nodata
            mask = hit if mask is None else (mask | hit)
    return mask


def _window_pixels(src, window, channels, nodata, scaling=None):
    """Read a window as an (H, W, C) uint8 array, or None if entirely nodata.

    ``scaling`` is the per-band stretch from :func:`_band_scaling` for
    non-uint8 sources (uint8 data passes through byte-exact).
    """
    data = src.read(window=window)  # (bands, h, w)
    empty = nodata_mask(data, nodata)
    if empty is not None and bool(empty.all()):
        return None
    if scaling is not None:
        lo, hi = scaling
        nb = min(data.shape[0], lo.size)
        scaled = data[:nb].astype("float32")
        scaled -= lo[:, None, None]
        scaled *= (255.0 / np.maximum(hi - lo, 1e-6))[:, None, None]
        data = np.clip(scaled, 0.0, 255.0)
    if np.issubdtype(data.dtype, np.floating) and not np.isfinite(data).all():
        # NaN cast to an integer is undefined (0 on x86, plus a numpy
        # RuntimeWarning); paint nodata black rather than arbitrary.
        data = np.nan_to_num(data, nan=0.0, posinf=255.0, neginf=0.0)
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
# The read behind a miss is a 3 x 1024 x 1024 decimated masked read plus
# per-band percentiles. Four snippet threads reaching an untouched file at
# once - which is exactly what a strip of labels on one image does - all
# missed and all computed it. One computes; the rest wait on its event.
_scaling_lock = threading.Lock()
_scaling_pending: dict[str, "threading.Event"] = {}


def cached_band_scaling(src):
    """The per-file display stretch for an open dataset (None for uint8).

    One cache for every display surface - snippets, the canvas's coarse
    and detail tiles, the pixel zone - so a float32 or 16-bit raster shows
    the SAME contrast everywhere it is drawn. Keyed by the dataset's path.
    """
    key = src.name
    while True:
        with _scaling_lock:
            if key in _scaling_cache:
                return _scaling_cache[key]
            waiting = _scaling_pending.get(key)
            if waiting is None:
                waiting = threading.Event()
                _scaling_pending[key] = waiting
                mine = True
            else:
                mine = False
        if not mine:
            # Someone else is sampling this raster; take their answer.
            waiting.wait(timeout=30.0)
            with _scaling_lock:
                if key in _scaling_cache:
                    return _scaling_cache[key]
                # The computing thread died or timed out; try it ourselves.
                _scaling_pending.pop(key, None)
            continue
        try:
            value = _band_scaling(src)
        finally:
            with _scaling_lock:
                _scaling_pending.pop(key, None)
            waiting.set()
        with _scaling_lock:
            _scaling_cache[key] = value
        return value


def apply_band_stretch(band: np.ndarray, scaling, band_index: int):
    """One band mapped through the display stretch (float32, unclipped).

    Pass-through when ``scaling`` is None (uint8 imagery) or the band has
    no recorded stretch. NaNs survive, so nodata marking done before the
    stretch stays valid; the caller still clips to 0..255 and casts.
    """
    if scaling is None or band_index >= scaling[0].size:
        return band
    lo = float(scaling[0][band_index])
    hi = float(scaling[1][band_index])
    return (band.astype(np.float32) - lo) * (255.0 / max(hi - lo, 1e-6))


def snippet_frame(pixel_x: float, pixel_y: float, size_px: int,
                  src_width: int, src_height: int) -> tuple[int, int, int, int]:
    """The exact crop a snippet uses: (x0, y0, w, h) in source pixels.

    One function, because the orientation editor must map a point drawn ON a
    snippet back to source pixels, and any drift between how the crop was
    made and how it is inverted would silently bend every angle.
    """
    w = min(size_px, src_width)
    h = min(size_px, src_height)
    x0, y0 = centered_window(pixel_x, pixel_y, w, h, src_width, src_height)
    return x0, y0, w, h


def read_label_window_raw(image_path: str, pixel_x: float, pixel_y: float,
                          size_px: int) -> "tuple | None":
    """RAW source values of a label's snippet window, plus its frame.

    Returns ((bands, h, w) array in the source dtype, (x0, y0, w, h),
    nodata) or None on failure. No stretch and no RGB collapse: the mask
    editor's object-versus-background statistics must describe the actual
    data, and a display-stretched byte distribution would describe the
    stretch. The declared nodata rides along because raw values alone
    cannot say which of them mean "nothing here" - see nodata_mask.
    """
    try:
        with rasterio.open(image_path) as src:
            x0, y0, w, h = snippet_frame(pixel_x, pixel_y, size_px,
                                         src.width, src.height)
            data = src.read(window=Window(x0, y0, w, h))
            return data, (x0, y0, w, h), src.nodata
    except Exception as exc:  # noqa: BLE001 - stats just go missing
        debug(f"raw window read failed: {Path(image_path).name}: "
              f"{type(exc).__name__}: {exc}")
        return None


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
            scaling = cached_band_scaling(src)
            x0, y0, w, h = snippet_frame(pixel_x, pixel_y, size_px,
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

    def __init__(self, key, image_path, px, py, size, token, signals,
                 loader):
        super().__init__()
        self._args = (key, image_path, px, py, size, token)
        self._signals = signals
        self._loader = loader
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        key, image_path, px, py, size, token = self._args
        try:
            # A queued read whose token is no longer the loader's current
            # one has been cancelled or superseded, and its result would be
            # discarded on arrival. Checking BEFORE the read is what makes
            # cancellation mean anything: a filter change used to leave
            # every queued runnable to open its file and read a snippet
            # nobody would ever see.
            if self._cancelled or not self._loader.is_current(key, token):
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

    # Budgeted in bytes, not entries: a 512 px source snippet is 786 KB
    # and 256 of them is ~192 MB, while a 64 px one is 12 KB and 256 of
    # them cache almost nothing. 64 MB holds ~5,000 of the default 224 px
    # snippets, so a panel rebuild over a few thousand labels re-serves
    # from memory instead of re-reading every file.
    _CACHE_BYTES = 64 * 1024 * 1024

    def __init__(self, parent=None, max_workers: int = 4):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(
            min(max_workers, max(1, QThread.idealThreadCount() - 1)))
        self._tokens: dict = {}          # key -> latest token issued
        self._counter = 0
        self._signals_alive: set = set()
        self._cache: OrderedDict = OrderedDict()   # content key -> array
        self._cache_bytes = 0

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
                                    size_px, token, signals, self)
        self._pool.start(runnable)

    def _on_finished(self, key, arr, token, signals, content):
        self._signals_alive.discard(signals)
        if self._tokens.get(key) != token:
            return   # superseded while reading; a newer delivery is coming
        del self._tokens[key]
        if arr is not None:
            self._store(content, arr)
        self.ready.emit(key, arr)

    def _store(self, content, arr):
        """Cache one snippet, evicting oldest until inside the byte budget."""
        previous = self._cache.pop(content, None)
        if previous is not None:
            self._cache_bytes -= previous.nbytes
        self._cache[content] = arr
        self._cache_bytes += arr.nbytes
        while self._cache_bytes > self._CACHE_BYTES and len(self._cache) > 1:
            _key, evicted = self._cache.popitem(last=False)
            self._cache_bytes -= evicted.nbytes

    def is_current(self, key, token) -> bool:
        """Is this still the newest request for ``key``?

        Read from worker threads: a plain dict lookup, no iteration, so it
        needs no lock. See _SnippetRunnable.run. A None token is never
        current - tokens start at 1, so None means "no request", which a
        forgotten key also reports.
        """
        return token is not None and self._tokens.get(key) == token

    def cancel(self, key):
        """Drop one outstanding request; a queued read for it will bail."""
        self._tokens.pop(key, None)

    def cancel_all(self):
        """Forget every outstanding request (e.g. on a filter change).

        Clearing the tokens also disarms whatever is still queued: each
        runnable checks is_current() before opening its file, so cancelled
        work costs a dictionary lookup instead of a windowed raster read.
        """
        self._tokens.clear()

    def clear_cache(self):
        """Drop cached pixels (e.g. when snippet size changes everywhere)."""
        self._cache.clear()
        self._cache_bytes = 0
