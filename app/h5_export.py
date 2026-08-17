"""Export labelled GeoTIFFs to the HDF5 CNN dataset format.

Examples (``gt=True``) are cut around the labels themselves: one H x W snippet
centred on each label, plus eight more shifted by a configurable offset - up,
down, left, right and the four diagonals - so an object is never sliced by a
window edge and appears at nine known positions.

Hard negatives (``gt=False``, class ``"hard_negative"``) come from sliding the
same H x W window over the raster with a given overlap, skipping any window
that overlaps an example crop - so no ground is ever taught as both an object
and not-an-object.

The datasets are resizable and written incrementally (streamed to disk), so an
export can be huge without exhausting memory, and a later export can **append**
to the same file - e.g. export the visible "Train" layers, then turn on the
"Validate" layers and append them with a different split value.

A file records the settings it was written with, so re-exporting into it (say
after adding more labels) reuses the same tiling instead of making the user
re-enter it - see ``read_settings``.

Contents:
- ``H5DatasetWriter`` - create/append + streamed writes (no Qt).
- ``export_image`` - cut one raster's examples and negatives into a writer.
- ``H5ExportWorker`` - runs the export off the UI thread.
- ``H5ExportDialog`` - the setup dialog.
"""
import os

import numpy as np
import rasterio
from rasterio.windows import Window
import h5py
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QSpinBox, QComboBox, QRadioButton, QButtonGroup, QPushButton,
    QDialogButtonBox, QFileDialog, QCheckBox,
)

from .debug_log import debug

HARD_NEGATIVE = "hard_negative"

# How far, in pixels, the eight surrounding example crops sit from the one
# centred on the label - see _positive_windows. A quarter of the default 64px
# snippet: enough to vary where the object lands, small enough to keep it well
# clear of the crop edge.
DEFAULT_POSITIVE_OFFSET = 16

# Which layers an export covers.
SCOPE_ALL = "all"
SCOPE_VISIBLE = "visible"
SCOPE_LABELLED = "labelled"  # visible *and* carrying at least one label
# Examples-only scopes: only snippets containing a label are exported - the
# hard-negative sliding window never runs.
SCOPE_ALL_EXAMPLES = "all_examples"          # every loaded layer with labels
SCOPE_VISIBLE_EXAMPLES = "visible_examples"  # visible layers with labels

SPLIT_CHOICES = {"Train (0)": 0, "Validate (1)": 1, "Test (2)": 2}
CHANNEL_CHOICES = {"RGB (3 channels)": 3, "Grayscale (1 channel)": 1}
COMPRESSION_CHOICES = {"None": None, "gzip": "gzip", "lzf": "lzf"}

# One sample per chunk (default) makes shuffled random-access reads cheap during
# training: a single-index read fetches exactly one sample instead of a whole
# multi-sample chunk (and, with compression, decompressing it). This is separate
# from the write buffer below.
_DEFAULT_CHUNK = 1
# Rows buffered in memory before a bulk write - independent of the HDF5 chunk
# size, so a chunk of 1 doesn't force one resize/write per sample.
_FLUSH_BATCH = 512
# The 1-D label/gt/split datasets are tiny per element, so a larger chunk keeps
# metadata overhead low with negligible read amplification.
_META_CHUNK = 4096
# Label rows read at a time when re-indexing an existing file's classes; 1M
# uint16 is a couple of MB, so even a huge dataset migrates in bounded memory.
_SCAN_BLOCK = 1 << 20


def read_settings(path) -> dict | None:
    """Return the settings an existing dataset was written with, else ``None``.

    Snippet size, channels, chunking and compression are read back from the
    ``images`` dataset itself (so files written before the overlap attribute
    existed still work); the overlap is only available as an attribute. Any
    unreadable or non-dataset file gives ``None``.
    """
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with h5py.File(path, "r") as f:
            if "images" not in f or f["images"].ndim != 4:
                return None
            images = f["images"]
            _, height, width, channels = images.shape
            settings = {
                "height": int(height),
                "width": int(width),
                "channels": int(channels),
                "chunk": int(images.chunks[0]) if images.chunks else _DEFAULT_CHUNK,
                "compression": images.compression,
            }
            if "overlap" in f.attrs:
                settings["overlap"] = float(f.attrs["overlap"])
            if "split_negatives" in f.attrs:
                settings["split_negatives"] = bool(f.attrs["split_negatives"])
            if "negative_ratio" in f.attrs:
                settings["negative_ratio"] = tuple(
                    float(v) for v in f.attrs["negative_ratio"])
            if "positive_offset" in f.attrs:
                settings["positive_offset"] = int(f.attrs["positive_offset"])
            return settings
    except Exception:  # noqa: BLE001 - a bad/locked file just has no settings
        return None


def centered_window(px: float, py: float, width: int, height: int,
                    img_width: int, img_height: int) -> tuple[int, int]:
    """Top-left of the ``width`` x ``height`` window centred on a label pixel.

    This is the one definition of "centred on a label" shared by the HDF5
    snippet export and the sub-image GeoTIFF export, so both frame identical
    ground for the same label: the label pixel is rounded to a whole pixel, the
    window is placed symmetrically around it, then shifted (never cropped) to
    stay inside the raster - so every snippet is exactly HxW, and the label
    sits dead centre except where that edge shift moves it.
    """
    x0 = int(round(px)) - width // 2
    y0 = int(round(py)) - height // 2
    x0 = min(max(x0, 0), max(0, img_width - width))
    y0 = min(max(y0, 0), max(0, img_height - height))
    return x0, y0


def _snippet_positions(total: int, window: int, step: int) -> list[int]:
    """Top-left offsets tiling ``[0, total)`` with a final shifted-to-fit one."""
    if total < window:
        return []
    positions = list(range(0, total - window + 1, max(1, step)))
    if positions[-1] != total - window:
        positions.append(total - window)
    return positions


def _band_scaling(src):
    """Per-band (low, high) stretch for a non-uint8 raster, else ``None``.

    The HDF5 dataset stores uint8, but a plain ``astype`` cast of 16-bit or
    float imagery wraps values modulo 256 into noise. Instead, sample the
    raster once (a decimated read, served from overviews when present, with
    nodata masked out) and derive a per-band 2-98 percentile window - the
    same idea as a viewer's default contrast stretch. Every snippet of the
    image is then scaled through this one linear mapping, so snippets stay
    consistent with each other and with how the imagery looks on screen.
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


class H5DatasetWriter:
    """Create or append to the HDF5 dataset, buffering and streaming rows.

    ``classes`` is the full ordered class list (project classes with
    ``"hard_negative"`` last). On append the existing file's H/W/C must match;
    a class list that has since gained or moved classes is migrated in place
    (see ``_reconcile_classes``), and what changed is reported on
    ``added_classes`` / ``dropped_classes``.
    """

    def __init__(self, path, height, width, channels, classes,
                 chunk=_DEFAULT_CHUNK, compression=None, overlap=None,
                 split_negatives=None, negative_ratio=None,
                 positive_offset=None):
        """Open ``path`` for create-or-append and prepare the datasets.

        ``chunk`` and ``compression`` apply only when the file is *created*;
        appending reuses the existing datasets' storage properties (chunking and
        compression are fixed at creation time). ``overlap`` is recorded as an
        attribute - it doesn't affect storage, it is kept so a later export into
        this file can offer the same tiling (see ``read_settings``).
        """
        self.height, self.width, self.channels = height, width, channels
        self.classes = list(classes)
        self._chunk = max(1, int(chunk))
        self._compression = compression
        self._n = 0
        self._img_buf, self._lbl_buf, self._gt_buf, self._split_buf = [], [], [], []
        # Set when appending to a file whose class list has since changed.
        self.added_classes, self.dropped_classes = [], []

        already = os.path.exists(path) and os.path.getsize(path) > 0
        self._f = h5py.File(path, "a")
        if already and "images" in self._f:
            self._validate_existing()
            self._n = self._f["images"].shape[0]
        else:
            self._create()
        # Most recently used, so an append made with different settings becomes
        # the default next time round.
        if overlap is not None:
            self._f.attrs["overlap"] = float(overlap)
        if split_negatives is not None:
            self._f.attrs["split_negatives"] = bool(split_negatives)
        if negative_ratio is not None:
            self._f.attrs["negative_ratio"] = np.asarray(
                negative_ratio, dtype="float64")
        if positive_offset is not None:
            self._f.attrs["positive_offset"] = int(positive_offset)

    def _create(self):
        """Create the resizable, chunked datasets."""
        f = self._f
        f.create_dataset(
            "images",
            shape=(0, self.height, self.width, self.channels),
            maxshape=(None, self.height, self.width, self.channels),
            dtype="uint8",
            chunks=(self._chunk, self.height, self.width, self.channels),
            compression=self._compression)
        for name, dtype in (("labels", "uint16"), ("gt", "bool"),
                            ("split", "uint8")):
            f.create_dataset(name, shape=(0,), maxshape=(None,), dtype=dtype,
                             chunks=(_META_CHUNK,))
        f.attrs["height"] = self.height
        f.attrs["width"] = self.width
        f.attrs["channels"] = self.channels
        self._write_classes()

    def _write_classes(self):
        """(Re)write the classes dataset."""
        if "classes" in self._f:
            del self._f["classes"]
        self._f.create_dataset(
            "classes",
            data=np.array(self.classes, dtype=h5py.string_dtype("utf-8")))

    def _validate_existing(self):
        """Ensure an existing file is compatible for appending."""
        f = self._f
        if (int(f.attrs.get("height", -1)) != self.height
                or int(f.attrs.get("width", -1)) != self.width
                or int(f.attrs.get("channels", -1)) != self.channels):
            raise ValueError(
                "Cannot append: the existing file's snippet size is "
                f"{f.attrs.get('height')}x{f.attrs.get('width')}x"
                f"{f.attrs.get('channels')}, not "
                f"{self.height}x{self.width}x{self.channels}.")
        existing = list(f["classes"].asstr()[:]) if "classes" in f else []
        if existing != self.classes:
            self._reconcile_classes(existing)

    def _reconcile_classes(self, existing):
        """Bring an existing file's label indices onto the new class list.

        ``labels`` holds indices into ``classes``, so adding a class renumbers
        the ones after it - and since ``hard_negative`` is always last, *any*
        new class shifts it. Left alone, the file's existing hard negatives
        would silently read as examples of some other class.

        Rows are therefore remapped by class *name*, which makes appending
        safe across added, inserted and reordered classes. The one case with
        no answer is a class that has left the project while rows in the file
        still use it: those rows can't be renamed, so that is still refused.
        """
        f = self._f
        labels = f["labels"] if "labels" in f else None
        new_index = {name: i for i, name in enumerate(self.classes)}
        missing = [name for name in existing if name not in new_index]

        if missing and labels is not None and labels.shape[0]:
            gone = [existing.index(name) for name in missing]
            in_use = self._used_indices(labels, gone)
            stranded = sorted(name for name, i in zip(missing, gone)
                              if i in in_use)
            if stranded:
                raise ValueError(
                    "Cannot append: the file has samples labelled "
                    f"{', '.join(repr(n) for n in stranded)}, which the "
                    "project no longer has. Add the class back under the same "
                    "name, or export to a new file.")

        # Missing classes that no row uses just fall off the list.
        self.dropped_classes = list(missing)
        self.added_classes = [n for n in self.classes if n not in existing]

        if labels is not None and labels.shape[0] and existing:
            lut = np.array([new_index.get(name, 0) for name in existing],
                           dtype="uint16")
            if not np.array_equal(lut, np.arange(lut.size, dtype="uint16")):
                total = labels.shape[0]
                for start in range(0, total, _SCAN_BLOCK):
                    stop = min(start + _SCAN_BLOCK, total)
                    block = labels[start:stop]
                    # clip guards a corrupt index rather than raising deep in
                    # the middle of a rewrite.
                    labels[start:stop] = lut[np.clip(block, 0, lut.size - 1)]
        self._write_classes()

    @staticmethod
    def _used_indices(labels, candidates):
        """Which of ``candidates`` actually appear in the labels dataset."""
        remaining, found = set(candidates), set()
        total = labels.shape[0]
        for start in range(0, total, _SCAN_BLOCK):
            block = labels[start:min(start + _SCAN_BLOCK, total)]
            for index in list(remaining):
                if bool(np.any(block == index)):
                    found.add(index)
                    remaining.discard(index)
            if not remaining:
                break
        return found

    def add(self, image_hwc, label_index, gt, split_value):
        """Buffer one sample; flushes to disk when a chunk has accumulated."""
        self._img_buf.append(image_hwc)
        self._lbl_buf.append(label_index)
        self._gt_buf.append(gt)
        self._split_buf.append(split_value)
        if len(self._img_buf) >= _FLUSH_BATCH:
            self._flush()

    def _flush(self):
        """Write buffered samples to the resizable datasets."""
        if not self._img_buf:
            return
        f = self._f
        b = len(self._img_buf)
        end = self._n + b
        f["images"].resize(end, axis=0)
        f["images"][self._n:end] = np.stack(self._img_buf)
        for name, buf, dt in (("labels", self._lbl_buf, "uint16"),
                              ("gt", self._gt_buf, "bool"),
                              ("split", self._split_buf, "uint8")):
            f[name].resize(end, axis=0)
            f[name][self._n:end] = np.asarray(buf, dtype=dt)
        self._n = end
        self._img_buf.clear(); self._lbl_buf.clear()
        self._gt_buf.clear(); self._split_buf.clear()

    def close(self) -> int:
        """Flush, close the file and return the total sample count."""
        try:
            self._flush()
        finally:
            total = self._n
            self._f.close()
        return total


def _positive_windows(pts, img_width, img_height, width, height, offset):
    """The example crops for every label: ``{(x0, y0): class_index}``.

    Each label yields a crop centred on it plus eight more shifted by
    ``offset`` px - up, down, left, right and the four diagonals. The object is
    therefore always whole and always at a known position, while the ring gives
    the network the same translation variety that blind tiling produces at
    inference time.

    Every crop is nudged back inside the raster, so a label near an edge still
    contributes - it just yields fewer than nine, because crops pushed against
    the same edge land on each other and are kept once. An object that close to
    the border sits near its crop edge in the dataset, which is exactly how a
    tile will present it at inference.

    Two labels close together can also generate the same crop; it is kept once,
    for whichever label sits nearest its centre.
    """
    windows: dict[tuple[int, int], tuple[int, float]] = {}
    max_x, max_y = img_width - width, img_height - height
    if max_x < 0 or max_y < 0:
        return {}

    def offer(x0, y0, x, y, class_index):
        """Record a crop, keeping whichever label sits nearest its centre."""
        x0 = min(max(x0, 0), max_x)
        y0 = min(max(y0, 0), max_y)
        away = max(abs(x - (x0 + width / 2.0)), abs(y - (y0 + height / 2.0)))
        held = windows.get((x0, y0))
        if held is None or away < held[1]:
            windows[(x0, y0)] = (class_index, away)

    # Pass 1: the exactly-centred crop for every label, using the same centring
    # rule as the sub-image GeoTIFF export. Dicts keep insertion order, so these
    # are written to the dataset before any shifted copy - the first snippet of
    # each label is the one framed exactly like its sub-image.
    bases = []
    for x, y, class_index in pts:
        base_x, base_y = centered_window(
            x, y, width, height, img_width, img_height)
        bases.append((base_x, base_y, x, y, class_index))
        offer(base_x, base_y, x, y, class_index)
        # Where the label lands inside its centred crop. Anything other than
        # (width//2, height//2) means the crop was shifted off an image edge -
        # log it so a real off-centre export can be traced to its label.
        debug(f"h5 centre crop: label px ({x:.1f}, {y:.1f}) on "
              f"{img_width}x{img_height} -> window ({base_x}, {base_y}), "
              f"label at ({x - base_x:.1f}, {y - base_y:.1f}) of "
              f"{width}x{height}")

    # Pass 2: the eight offsets, measured from each label's centred crop.
    if offset > 0:
        for base_x, base_y, x, y, class_index in bases:
            for dy in (-offset, 0, offset):
                for dx in (-offset, 0, offset):
                    if dx == 0 and dy == 0:
                        continue  # already placed in pass 1
                    offer(base_x + dx, base_y + dy, x, y, class_index)

    return {pos: held[0] for pos, held in windows.items()}


def _excluded_grid(positive_positions, xs, ys, width, height):
    """Grid windows overlapping any example crop, which cannot be negatives.

    A hard negative must not share a single pixel with an example, or the same
    ground would be taught as both. A grid window at ``x0`` overlaps a crop at
    ``px`` exactly when ``px - width < x0 < px + width``, a contiguous run of
    the sorted offsets, so each crop marks its own block.
    """
    mask = np.zeros((len(ys), len(xs)), dtype=bool)
    if not positive_positions or mask.size == 0:
        return mask
    xs_arr, ys_arr = np.asarray(xs), np.asarray(ys)
    for px, py in positive_positions:
        ix0 = int(np.searchsorted(xs_arr, px - width, side="right"))
        ix1 = int(np.searchsorted(xs_arr, px + width, side="left"))
        iy0 = int(np.searchsorted(ys_arr, py - height, side="right"))
        iy1 = int(np.searchsorted(ys_arr, py + height, side="left"))
        mask[iy0:iy1, ix0:ix1] = True
    return mask


def _negative_split_pool(count, ratio, rng):
    """A shuffled array of ``count`` split values in the given proportions.

    Quotas are exact rather than rolled per snippet, so an image with a
    handful of hard negatives still splits 70/15/15 instead of landing them
    all in one set by chance. Cumulative boundaries keep every share
    non-negative whatever the ratio rounds to.
    """
    if count <= 0:
        return np.empty(0, dtype="uint8")
    train = min(int(round(count * ratio[0])), count)
    upto_validate = min(max(int(round(count * (ratio[0] + ratio[1]))), train),
                        count)
    pool = np.empty(count, dtype="uint8")
    pool[:train] = 0
    pool[train:upto_validate] = 1
    pool[upto_validate:] = 2
    rng.shuffle(pool)
    return pool


def export_image(writer, path, labels, height, width, overlap, channels,
                 split_value, class_to_index, hard_negative_index,
                 cancel_check=None, negative_ratio=None, rng=None,
                 positive_offset=DEFAULT_POSITIVE_OFFSET,
                 examples_only=False):
    """Extract one raster's examples and hard negatives into ``writer``.

    Returns ``(added, negative_counts, excluded)`` - the number of snippets
    added, how many hard negatives went to each split, and how many grid
    windows were withheld for overlapping an example.

    Examples are cut around the labels themselves: one crop centred on each
    label and eight more shifted by ``positive_offset`` px (see
    ``_positive_windows``), so an object is never sliced by a window edge that
    happened to fall across it.

    Hard negatives come from sliding a window over the raster, skipping any
    that overlaps an example crop - so no ground is ever taught as both an
    object and not-an-object. Windows never cross the image edge (a final
    window is shifted to fit), so every snippet is exactly HxW.

    With ``negative_ratio`` set, this image's hard negatives are shared out
    over train/validate/test in those proportions instead of all taking
    ``split_value``; examples always take ``split_value``.

    With ``examples_only`` set, only the label-bearing example crops are
    written - the hard-negative sliding window never runs.
    """
    added = 0
    negative_counts = [0, 0, 0]
    step_x = max(1, int(round(width * (1.0 - overlap))))
    step_y = max(1, int(round(height * (1.0 - overlap))))

    # Label pixel positions + resolved class indices (drop unknown classes).
    pts = []
    for lab in labels:
        ci = class_to_index.get(lab.class_name)
        if ci is not None:
            pts.append((float(lab.pixel_x), float(lab.pixel_y), ci))

    with rasterio.open(path) as src:
        nodata = src.nodata
        # One contrast stretch per raster (None for uint8), so every snippet
        # of this image is scaled identically - see _band_scaling.
        scaling = _band_scaling(src)
        positives = _positive_windows(
            pts, src.width, src.height, width, height,
            max(0, int(positive_offset)))

        for (x0, y0), class_index in positives.items():
            if cancel_check and cancel_check():
                return added, negative_counts, 0
            arr = _window_pixels(
                src, Window(x0, y0, width, height), channels, nodata,
                scaling=scaling)
            if arr is None:
                continue  # entirely nodata
            writer.add(arr, class_index, True, split_value)
            added += 1

        if examples_only:
            # No hard negatives wanted: the sliding window never runs.
            return added, negative_counts, 0

        xs = _snippet_positions(src.width, width, step_x)
        ys = _snippet_positions(src.height, height, step_y)
        excluded = _excluded_grid(positives.keys(), xs, ys, width, height)

        pool, taken = None, 0
        if negative_ratio is not None:
            # Quotas cover every window that will be a hard negative. Windows
            # dropped for being all nodata simply leave the tail of the
            # shuffled pool unused, which takes a random share from each split
            # rather than skewing one.
            pool = _negative_split_pool(
                int(excluded.size - excluded.sum()), negative_ratio,
                rng if rng is not None else np.random.default_rng())

        for iy, y0 in enumerate(ys):
            for ix, x0 in enumerate(xs):
                if cancel_check and cancel_check():
                    return added, negative_counts, int(excluded.sum())
                if excluded[iy, ix]:
                    continue  # shares ground with an example
                arr = _window_pixels(
                    src, Window(x0, y0, width, height), channels, nodata,
                    scaling=scaling)
                if arr is None:
                    continue  # entirely nodata
                negative_split = split_value
                if pool is not None and taken < pool.size:
                    negative_split = int(pool[taken])
                    taken += 1
                writer.add(arr, hard_negative_index, False, negative_split)
                negative_counts[negative_split] += 1
                added += 1
    return added, negative_counts, int(excluded.sum())


class H5ExportWorker(QObject):
    """Runs the HDF5 export off the UI thread."""

    progress = pyqtSignal(int, int, int)   # (image_index, total_images, samples)
    finished = pyqtSignal(object, str)     # (summary dict or None, error)

    def __init__(self, out_path, images, options):
        """Store the output path, (path, labels) list and export options."""
        super().__init__()
        self._out_path = out_path
        self._images = images
        self._options = options
        self._cancelled = False

    def cancel(self):
        """Request cancellation (checked between snippets and images)."""
        self._cancelled = True

    def process(self):
        """Build the dataset and emit a summary (or an error)."""
        opts = self._options
        classes = opts["classes"]
        class_to_index = {name: i for i, name in enumerate(classes)}
        hard_negative_index = classes.index(HARD_NEGATIVE)
        writer = None
        errors = []
        negative_ratio = (opts.get("negative_ratio")
                          if opts.get("split_negatives") else None)
        # One generator for the whole export, so each image's negatives are
        # shuffled independently but the run stays self-contained.
        rng = np.random.default_rng()
        negative_counts = [0, 0, 0]
        excluded = 0
        try:
            writer = H5DatasetWriter(
                self._out_path, opts["height"], opts["width"],
                opts["channels"], classes,
                chunk=opts.get("chunk", _DEFAULT_CHUNK),
                compression=opts.get("compression"),
                overlap=opts.get("overlap"),
                split_negatives=opts.get("split_negatives"),
                negative_ratio=opts.get("negative_ratio"),
                positive_offset=opts.get("positive_offset"))
            total_images = len(self._images)
            samples = 0
            for i, (path, labels) in enumerate(self._images):
                if self._cancelled:
                    break
                self.progress.emit(i, total_images, samples)
                if not os.path.exists(path):
                    errors.append((path, "file not found"))
                    continue
                try:
                    added, negatives, dropped = export_image(
                        writer, path, labels, opts["height"], opts["width"],
                        opts["overlap"], opts["channels"], opts["split_value"],
                        class_to_index, hard_negative_index,
                        cancel_check=lambda: self._cancelled,
                        negative_ratio=negative_ratio, rng=rng,
                        positive_offset=opts.get(
                            "positive_offset", DEFAULT_POSITIVE_OFFSET),
                        examples_only=opts.get("examples_only", False))
                    samples += added
                    excluded += dropped
                    for i, count in enumerate(negatives):
                        negative_counts[i] += count
                except Exception as e:  # noqa: BLE001 - report, keep going
                    errors.append((path, str(e)))
            added_classes = writer.added_classes
            dropped_classes = writer.dropped_classes
            total = writer.close()
            writer = None
            self.finished.emit(
                {"total": total, "path": self._out_path,
                 "cancelled": self._cancelled, "errors": errors,
                 "added_classes": added_classes,
                 "dropped_classes": dropped_classes,
                 "split_negatives": bool(negative_ratio),
                 "negative_counts": negative_counts,
                 "excluded": excluded}, "")
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            self.finished.emit(None, str(e))


def _choice_label(choices: dict, value):
    """Reverse-lookup a combo label from its value, or None."""
    for label, choice in choices.items():
        if choice == value:
            return label
    return None


class H5ExportDialog(QDialog):
    """Setup dialog for the HDF5 dataset export.

    Settings carry over between exports: ``defaults`` pre-fills the widgets
    (the caller's last-used options), and once the output path points at an
    existing dataset its own recorded settings take over - the ones that are
    fixed at creation time are shown but locked.
    """

    def __init__(self, all_count, visible_count, labelled_count=0, parent=None,
                 defaults=None, all_labelled_count=0):
        """Build the dialog. ``*_count`` size the scope radio labels."""
        super().__init__(parent)
        self._all_count = all_count
        self._visible_count = visible_count
        self._labelled_count = labelled_count
        self._all_labelled_count = all_labelled_count
        self._defaults = dict(defaults or {})
        self.setWindowTitle("Export HDF5 Dataset")
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self):
        """Assemble the dialog widgets."""
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Extract H x W pixel snippets from the images with a sliding window "
            "and write the HDF5 CNN dataset. Snippets containing a label become "
            "genuine examples; all others are hard negatives. Existing files "
            "are appended to."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Scope
        scope_box = QGroupBox("Images to export")
        scope_layout = QVBoxLayout(scope_box)
        self.scope_all = QRadioButton(f"All loaded layers ({self._all_count})")
        self.scope_visible = QRadioButton(
            f"Only visible (ON) layers ({self._visible_count})")
        self.scope_labelled = QRadioButton(
            f"Only visible (ON) layers with labels ({self._labelled_count})")
        self.scope_labelled.setToolTip(
            "Skips visible layers that have no labels, so the export contains "
            "no images made up entirely of hard negatives.")
        self.scope_all_examples = QRadioButton(
            "Labelled snippets ONLY - all loaded layers "
            f"({self._all_labelled_count})")
        self.scope_all_examples.setToolTip(
            "Export only the snippets that contain a label centre, from every "
            "loaded layer that has labels (visible or not). No hard negatives "
            "are written at all.")
        self.scope_visible_examples = QRadioButton(
            "Labelled snippets ONLY - visible (ON) layers "
            f"({self._labelled_count})")
        self.scope_visible_examples.setToolTip(
            "Export only the snippets that contain a label centre, from the "
            "layers toggled on in the viewer. No hard negatives are written "
            "at all.")
        self.scope_all.setChecked(True)
        if self._visible_count == 0:
            self.scope_visible.setEnabled(False)
        if self._labelled_count == 0:
            self.scope_labelled.setEnabled(False)
            self.scope_visible_examples.setEnabled(False)
        if self._all_labelled_count == 0:
            self.scope_all_examples.setEnabled(False)
        self._scope_group = QButtonGroup(self)
        for button in (self.scope_all, self.scope_visible, self.scope_labelled,
                       self.scope_all_examples, self.scope_visible_examples):
            self._scope_group.addButton(button)
            scope_layout.addWidget(button)
            # Examples-only scopes make the hard-negative options irrelevant.
            button.toggled.connect(self._on_scope_changed)
        layout.addWidget(scope_box)

        # Options
        opts = QGroupBox("Snippet options")
        form = QFormLayout(opts)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 8192)
        self.height_spin.setValue(64)
        # The usable offset range depends on the snippet size.
        self.height_spin.valueChanged.connect(self._update_ok_enabled)
        form.addRow("Snippet height (px):", self.height_spin)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 8192)
        self.width_spin.setValue(64)
        self.width_spin.valueChanged.connect(self._update_ok_enabled)
        form.addRow("Snippet width (px):", self.width_spin)

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 95)
        self.overlap_spin.setValue(50)
        self.overlap_spin.setSuffix(" %")
        form.addRow("Overlap:", self.overlap_spin)

        self.channel_combo = QComboBox()
        self.channel_combo.addItems(list(CHANNEL_CHOICES.keys()))
        form.addRow("Channels:", self.channel_combo)

        self.offset_check = QCheckBox("Add 8 offset copies of each example")
        self.offset_check.setChecked(True)
        self.offset_check.setToolTip(
            "When on, every label yields the centred snippet plus eight more "
            "shifted by the offset below - up, down, left, right and the "
            "diagonals - so the object appears at nine known positions. When "
            "off, only the centred snippet is exported.")
        self.offset_check.toggled.connect(self._on_offset_toggled)
        form.addRow("", self.offset_check)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(1, 4096)
        self.offset_spin.setValue(DEFAULT_POSITIVE_OFFSET)
        self.offset_spin.setSuffix(" px")
        self.offset_spin.setToolTip(
            "Shift distance for the eight offset copies. Hard negatives that "
            "would overlap any example crop are left out.")
        self.offset_spin.valueChanged.connect(self._update_ok_enabled)
        form.addRow("Example offset:", self.offset_spin)

        self._offset_note = QLabel("")
        self._offset_note.setWordWrap(True)
        self._offset_note.setStyleSheet("color: #cc0000;")
        form.addRow("", self._offset_note)

        self.split_combo = QComboBox()
        self.split_combo.addItems(list(SPLIT_CHOICES.keys()))
        self.split_combo.setToolTip(
            "Which set this batch's snippets belong to. Hard negatives take "
            "this too, unless they are split by the ratio below.")
        form.addRow("Split (this batch):", self.split_combo)

        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(1, 8192)
        self.chunk_spin.setValue(1)
        self.chunk_spin.setToolTip(
            "HDF5 samples per chunk. 1 = fastest shuffled reads during "
            "training; larger favours sequential reads. (New files only.)")
        form.addRow("Chunk (samples):", self.chunk_spin)

        self.compress_combo = QComboBox()
        self.compress_combo.addItems(list(COMPRESSION_CHOICES.keys()))
        self.compress_combo.setToolTip(
            "Compression for the images dataset. None = fastest random reads. "
            "(New files only.)")
        form.addRow("Image compression:", self.compress_combo)
        layout.addWidget(opts)

        # Hard-negative split
        neg_box = QGroupBox("Hard negatives")
        self._neg_box = neg_box  # greyed out for examples-only scopes
        neg_layout = QVBoxLayout(neg_box)
        self.split_negatives_check = QCheckBox(
            "Split hard negatives across train / validate / test")
        self.split_negatives_check.setToolTip(
            "Share each image's hard negatives out over all three sets in the "
            "ratio below, instead of putting them all in the batch's split. "
            "Labelled snippets are unaffected. Quotas are per image, so every "
            "image is represented in every set.")
        self.split_negatives_check.toggled.connect(self._on_split_negatives)
        neg_layout.addWidget(self.split_negatives_check)

        ratio_row = QHBoxLayout()
        self.negative_ratio_spins = {}
        for name, default in (("Train", 70), ("Validate", 15), ("Test", 15)):
            ratio_row.addWidget(QLabel(f"{name}:"))
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setValue(default)
            spin.setSuffix(" %")
            spin.setEnabled(False)  # the checkbox starts clear
            spin.valueChanged.connect(self._update_ok_enabled)
            self.negative_ratio_spins[name] = spin
            ratio_row.addWidget(spin)
        ratio_row.addStretch(1)
        neg_layout.addLayout(ratio_row)

        self._ratio_note = QLabel("")
        self._ratio_note.setStyleSheet("color: #cc0000;")
        neg_layout.addWidget(self._ratio_note)
        layout.addWidget(neg_box)

        # Output file
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output .h5:"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("dataset.h5 (existing file is appended)")
        self.out_edit.textChanged.connect(self._on_out_path_changed)
        out_row.addWidget(self.out_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_file)
        out_row.addWidget(browse)
        layout.addLayout(out_row)

        self._append_note = QLabel("")
        self._append_note.setStyleSheet("color: #0066cc;")
        layout.addWidget(self._append_note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Export")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # Last: setting the path fires _on_out_path_changed, which needs every
        # widget above to exist, and lets the file's own settings win over the
        # caller's defaults.
        self._apply_settings(self._defaults)
        self.out_edit.setText(self._defaults.get("out_path", ""))
        self._on_out_path_changed()

    def _apply_settings(self, settings):
        """Set the widgets from a settings dict; missing keys are left alone."""
        if not settings:
            return
        for key, spin in (("height", self.height_spin),
                          ("width", self.width_spin),
                          ("chunk", self.chunk_spin)):
            if settings.get(key) is not None:
                spin.setValue(int(settings[key]))
        if settings.get("positive_offset") is not None:
            offset = int(settings["positive_offset"])
            # 0 means the file was built without the eight offset copies.
            self.offset_check.setChecked(offset > 0)
            if offset > 0:
                self.offset_spin.setValue(offset)
        if settings.get("overlap") is not None:
            self.overlap_spin.setValue(int(round(float(settings["overlap"]) * 100)))
        if settings.get("split_negatives") is not None:
            self.split_negatives_check.setChecked(
                bool(settings["split_negatives"]))
        ratio = settings.get("negative_ratio")
        if ratio is not None and len(ratio) == 3:
            for spin, share in zip(
                    (self.negative_ratio_spins["Train"],
                     self.negative_ratio_spins["Validate"],
                     self.negative_ratio_spins["Test"]), ratio):
                spin.setValue(int(round(float(share) * 100)))
        for key, choices, combo in (
                ("channels", CHANNEL_CHOICES, self.channel_combo),
                ("split_value", SPLIT_CHOICES, self.split_combo),
                ("compression", COMPRESSION_CHOICES, self.compress_combo)):
            if key in settings:
                label = _choice_label(choices, settings[key])
                if label is not None:
                    combo.setCurrentText(label)

    def _fixed_widgets(self):
        """The widgets an existing file dictates.

        Storage layout (size, channels, chunking, compression) is fixed once
        the datasets exist. The object radius is not, but changing it would
        mix two extraction rules in one dataset, so a file keeps the one it
        was built with.
        """
        return (self.height_spin, self.width_spin, self.channel_combo,
                self.chunk_spin, self.compress_combo, self.offset_spin,
                self.offset_check)

    def _on_split_negatives(self, enabled):
        """Enable the ratio inputs only while the split is switched on."""
        for spin in self.negative_ratio_spins.values():
            spin.setEnabled(enabled)
        self._update_ok_enabled()

    def _on_scope_changed(self, _checked=False):
        """Grey out the hard-negative options for the examples-only scopes."""
        self._neg_box.setEnabled(not self.examples_only())
        self._update_ok_enabled()

    def examples_only(self) -> bool:
        """True when the selected scope exports labelled snippets only."""
        return (self.scope_all_examples.isChecked()
                or self.scope_visible_examples.isChecked())

    def _on_offset_toggled(self, enabled):
        """Enable the offset distance only while the copies are switched on."""
        self.offset_spin.setEnabled(enabled)
        self._update_ok_enabled()

    def _ratio_total(self) -> int:
        """The three ratio percentages summed."""
        return sum(s.value() for s in self.negative_ratio_spins.values())

    def _update_ok_enabled(self):
        """Export needs a path, a ratio totalling 100% and a usable offset."""
        has_path = bool(self.out_edit.text().strip())
        # With an examples-only scope no negatives exist, so the ratio is moot.
        ratio_ok = (self.examples_only()
                    or not self.split_negatives_check.isChecked()
                    or self._ratio_total() == 100)
        self._ratio_note.setText(
            "" if ratio_ok
            else f"The three shares must add up to 100% (currently "
                 f"{self._ratio_total()}%).")

        # Past half the snippet the label falls outside its own offset crops,
        # which would file snippets of empty ground as examples. Only applies
        # while the offset copies are switched on.
        limit = min(self.height_spin.value(), self.width_spin.value()) // 2
        offset_ok = (not self.offset_check.isChecked()
                     or self.offset_spin.value() < limit)
        self._offset_note.setText(
            "" if offset_ok
            else f"Offset must be under half the snippet ({limit} px), or the "
                 "object falls outside its own examples.")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            has_path and ratio_ok and offset_ok)

    def _on_out_path_changed(self):
        """Adopt an existing target file's settings and enable/disable Export."""
        text = self.out_edit.text().strip()
        self._update_ok_enabled()

        settings = read_settings(text)
        if settings is not None:
            # Snippet size, channels and storage are fixed once the datasets
            # exist; overlap is only a default, so it stays editable.
            self._apply_settings(settings)
            for widget in self._fixed_widgets():
                widget.setEnabled(False)
            self._append_note.setText(
                "Existing dataset - snippets will be appended using its "
                "snippet size, channels, object radius and storage settings.")
            return

        for widget in self._fixed_widgets():
            widget.setEnabled(True)
        if text and os.path.exists(text):
            self._append_note.setText("Existing file - snippets will be appended.")
        else:
            self._append_note.setText("")

    def _choose_file(self):
        """Pick an output .h5 (new or existing to append to)."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export HDF5 Dataset", self.out_edit.text() or "dataset.h5",
            "HDF5 (*.h5 *.hdf5)", options=QFileDialog.DontConfirmOverwrite)
        if path:
            if not path.lower().endswith((".h5", ".hdf5")):
                path += ".h5"
            self.out_edit.setText(path)

    def scope(self) -> str:
        """Return which layers to export: one of the ``SCOPE_*`` constants."""
        if self.scope_all_examples.isChecked():
            return SCOPE_ALL_EXAMPLES
        if self.scope_visible_examples.isChecked():
            return SCOPE_VISIBLE_EXAMPLES
        if self.scope_labelled.isChecked():
            return SCOPE_LABELLED
        if self.scope_visible.isChecked():
            return SCOPE_VISIBLE
        return SCOPE_ALL

    def output_path(self) -> str:
        """Return the chosen output .h5 path."""
        return self.out_edit.text().strip()

    def options(self) -> dict:
        """Return the export options (without ``classes``, added by the caller)."""
        return {
            "height": self.height_spin.value(),
            "width": self.width_spin.value(),
            "overlap": self.overlap_spin.value() / 100.0,
            "channels": CHANNEL_CHOICES[self.channel_combo.currentText()],
            "split_value": SPLIT_CHOICES[self.split_combo.currentText()],
            "chunk": self.chunk_spin.value(),
            # 0 disables the eight offset copies (centred snippet only).
            "positive_offset": (self.offset_spin.value()
                                if self.offset_check.isChecked() else 0),
            "compression": COMPRESSION_CHOICES[self.compress_combo.currentText()],
            # Examples-only scopes write no hard negatives, so there is
            # nothing to split either.
            "examples_only": self.examples_only(),
            "split_negatives": (self.split_negatives_check.isChecked()
                                and not self.examples_only()),
            "negative_ratio": tuple(
                self.negative_ratio_spins[name].value() / 100.0
                for name in ("Train", "Validate", "Test")),
        }
