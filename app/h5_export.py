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
from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.windows import Window
import h5py
from PyQt5.QtCore import QObject, pyqtSignal

# The framing and stretch live with the snippet service now, so the
# sidebar/orientation views and the exports can never frame differently.
from .masks import entry_in_window
from .snippets import (centered_window, nodata_mask, _band_scaling,  # noqa: F401
                       _window_pixels)
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QSpinBox, QComboBox, QRadioButton, QPushButton,
    QDialogButtonBox, QFileDialog, QCheckBox,
)

from .debug_log import debug

HARD_NEGATIVE = "hard_negative"


@dataclass
class ExportImage:
    """One raster's contribution to an export.

    ``labels`` become gt=True example crops. ``protect`` are labels whose
    ground must not be written as a hard negative even though they are not
    being exported - every other object on the image, whatever its class.
    Keeping the two apart is the whole point: protection used to be a side
    effect of exporting, so exporting a subset of an image's labels (one
    linked object, or none at all) silently offered the rest as background.
    """

    path: str
    labels: list = field(default_factory=list)
    # False slides the hard-negative window over this raster as well.
    examples_only: bool = False
    # The image's free-text location tag, recorded on every snippet cut here.
    location: str = ""
    # Additional labels to keep clear of the negative grid. The exported
    # labels are always protected; this is what is protected *as well*, so
    # the default can never protect less than before.
    protect: list = field(default_factory=list)
    # The raster's pixel dimensions, when the project knows them. Used only
    # by estimate_export, so the dialog can count what a run would write
    # without opening a single file; the export itself reads the real size.
    src_width: int = 0
    src_height: int = 0

# How far, in pixels, the eight surrounding example crops sit from the one
# centred on the label - see _positive_windows. A quarter of the default 64px
# snippet: enough to vary where the object lands, small enough to keep it well
# clear of the crop edge.
DEFAULT_POSITIVE_OFFSET = 16

# An export answers two independent questions - which images contribute
# gt=True examples, and which get the hard-negative window slid over them.
# They used to share one list of five radio buttons, which left most
# combinations unreachable: "examples from everything, negatives only from
# the layers I have toggled on" could not be said at all, and neither could
# "negatives and nothing else".
#
# Where the example crops come from:
EXAMPLES_NONE = "none"                    # negatives only
EXAMPLES_OBJECT = "object"                # the selected object(s), every view
EXAMPLES_OBJECT_VISIBLE = "object_visible"   # ...only on toggled-on layers
EXAMPLES_VISIBLE = "visible"              # toggled-on layers that have labels
EXAMPLES_ALL = "all"                      # every loaded layer that has labels

# Which images the hard-negative window is slid over:
NEGATIVES_NONE = "none"                   # true positives only
NEGATIVES_SAME = "same"                   # only images contributing examples
NEGATIVES_VISIBLE = "visible"             # every toggled-on layer
NEGATIVES_FLAGGED = "flagged"             # flagged hard-negative sources only
NEGATIVES_ALL = "all"                     # every loaded layer

SPLIT_CHOICES = {"Train (0)": 0, "Validate (1)": 1, "Test (2)": 2}
CHANNEL_CHOICES = {"RGB (3 channels)": 3, "Grayscale (1 channel)": 1}
COMPRESSION_CHOICES = {"None": None, "gzip": "gzip", "lzf": "lzf"}
# Pixel storage for the images dataset. uint8 applies the per-raster display
# stretch (the historical behaviour); float32 keeps float sources in their
# NATIVE units (no stretch - physical values stay comparable across images)
# and normalizes integer sources by their dtype's full range (uint8 / 255),
# always as one grayscale channel so the file only grows ~4/3 over 3-channel
# uint8.
PIXEL_FORMAT_CHOICES = {"uint8 (stretched 0-255)": "uint8",
                        "float32 grayscale (native / 0-1)": "float32"}

# One sample per chunk (default) makes shuffled random-access reads cheap during
# training: a single-index read fetches exactly one sample instead of a whole
# multi-sample chunk (and, with compression, decompressing it). This is separate
# from the write buffer below.
_DEFAULT_CHUNK = 1
# Rows buffered in memory before a bulk write - independent of the HDF5 chunk
# size, so a chunk of 1 doesn't force one resize/write per sample.
_FLUSH_BATCH = 512
# ...but capped by bytes as well, because the row count says nothing about
# what a row costs: the dialog allows snippets up to 8192 px, and 512 rows
# of 1024 px RGB is ~1.5 GiB buffered plus another ~1.5 GiB for the stack
# the write builds. Whichever limit is reached first triggers the flush.
_FLUSH_BYTES = 64 * 1024 * 1024
# The 1-D label/gt/split datasets are tiny per element, so a larger chunk keeps
# metadata overhead low with negligible read amplification.
_META_CHUNK = 4096
# Aligned per-sample string columns, written only when a run has values for
# them. See _H5DatasetWriter._flush_string_column.
_STRING_COLUMNS = ("locations", "object_ids")
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
                # Read from the dataset itself, so files written before the
                # float32 option existed correctly report uint8.
                "pixel_dtype": str(images.dtype),
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


def _float_window_pixels(src, window, nodata):
    """Read a window as an (H, W, 1) float32 grayscale array, or ``None``.

    The float32 pixel format keeps values MEANINGFUL rather than displayable:
    float sources (sonar dB, elevation) pass through in their native units,
    exactly as stored - so the same physical value reads the same in every
    image - while integer sources are divided by their dtype's full range
    (uint8 by 255, uint16 by 65535) to land in [0, 1]. No contrast stretch is
    ever applied. Multi-band integer imagery collapses to the same luminance
    the uint8 grayscale export uses; multi-band float imagery takes band 1
    (mixing physical units through luminance weights would mean nothing).
    Entirely-nodata windows return None, like the uint8 reader.
    """
    data = src.read(window=window)
    empty = nodata_mask(data, nodata)
    if empty is not None and bool(empty.all()):
        return None
    integer = np.issubdtype(data.dtype, np.integer)
    if data.shape[0] >= 3 and integer:
        gray = (0.299 * data[0].astype(np.float32)
                + 0.587 * data[1].astype(np.float32)
                + 0.114 * data[2].astype(np.float32))
    else:
        gray = data[0].astype(np.float32)
    if integer:
        gray /= float(np.iinfo(data.dtype).max)
    return np.ascontiguousarray(gray[..., np.newaxis])


def _snippet_positions(total: int, window: int, step: int) -> list[int]:
    """Top-left offsets tiling ``[0, total)`` with a final shifted-to-fit one."""
    if total < window:
        return []
    positions = list(range(0, total - window + 1, max(1, step)))
    if positions[-1] != total - window:
        positions.append(total - window)
    return positions


def estimate_export(images, width, height, overlap, channels,
                    positive_offset, pixel_dtype="uint8",
                    class_names=None) -> dict:
    """What an export would contain, without reading a single raster.

    Everything here is arithmetic on label positions and the grid step, so
    the dialog can answer "how big is this?" as the user types. Returns
    ``examples``, ``negatives``, ``per_class`` ({name: count}) and ``bytes``
    - an estimate of the image data alone, which dominates the file.

    ``images`` are :class:`ExportImage`; each must carry ``src_width`` and
    ``src_height`` for its negatives to be counted (they come from the
    project, so no header read is needed). An image whose size is unknown
    contributes its examples and no negative estimate.
    """
    offset = max(0, int(positive_offset))
    step_x = max(1, int(round(width * (1.0 - overlap))))
    step_y = max(1, int(round(height * (1.0 - overlap))))
    examples = 0
    negatives = 0
    per_class: dict = {}
    for request in images:
        src_w = int(getattr(request, "src_width", 0) or 0)
        src_h = int(getattr(request, "src_height", 0) or 0)
        crops = {}
        if src_w >= width and src_h >= height:
            pts = [(float(l.pixel_x), float(l.pixel_y), 0,
                    getattr(l, "class_name", "")) for l in request.labels]
            crops = _positive_windows(pts, src_w, src_h, width, height,
                                      offset)
        elif request.labels:
            # Unknown or undersized: every label still yields its crops, we
            # just cannot say how the ring merges at the edges.
            crops = {(i, 0): (0, l.class_name)
                     for i, l in enumerate(request.labels)}
        examples += len(crops)
        for _pos, (_index, name) in crops.items():
            per_class[name] = per_class.get(name, 0) + 1

        if request.examples_only or src_w < width or src_h < height:
            continue
        guarded = set(crops)
        if request.protect:
            guarded |= set(_positive_windows(
                [(float(l.pixel_x), float(l.pixel_y), 0, "")
                 for l in request.protect], src_w, src_h, width, height,
                offset))
        xs = _snippet_positions(src_w, width, step_x)
        ys = _snippet_positions(src_h, height, step_y)
        blocked = _excluded_grid(guarded, xs, ys, width, height)
        negatives += int(blocked.size - blocked.sum())

    stored_channels = 1 if pixel_dtype == "float32" else channels
    itemsize = 4 if pixel_dtype == "float32" else 1
    total = examples + negatives
    if class_names:
        per_class = {name: per_class.get(name, 0) for name in class_names
                     if per_class.get(name)}
    return {"examples": examples, "negatives": negatives,
            "total": total, "per_class": per_class,
            "bytes": total * width * height * stored_channels * itemsize}


def format_estimate(estimate: dict) -> str:
    """The estimate as the one line the dialog shows."""
    if not estimate["total"]:
        return "Nothing to export with these settings."
    size = estimate["bytes"]
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            readable = (f"{size:.0f} {unit}" if unit == "bytes"
                        else f"{size:.1f} {unit}")
            break
        size /= 1024.0
    parts = [f"{estimate['total']:,} snippets",
             f"{estimate['examples']:,} examples",
             f"{estimate['negatives']:,} hard negatives"]
    spread = ", ".join(f"{name} {count:,}"
                       for name, count in estimate["per_class"].items())
    line = "  \u00b7  ".join(parts) + f"  \u00b7  ~{readable}"
    return line + (f"\n{spread}" if spread else "")


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
                 positive_offset=None, pixel_dtype="uint8"):
        """Open ``path`` for create-or-append and prepare the datasets.

        ``chunk`` and ``compression`` apply only when the file is *created*;
        appending reuses the existing datasets' storage properties (chunking and
        compression are fixed at creation time). ``overlap`` is recorded as an
        attribute - it doesn't affect storage, it is kept so a later export into
        this file can offer the same tiling (see ``read_settings``).
        """
        self.height, self.width, self.channels = height, width, channels
        self.classes = list(classes)
        self.pixel_dtype = str(pixel_dtype)
        self._chunk = max(1, int(chunk))
        self._compression = compression
        self._n = 0
        self._img_buf, self._lbl_buf, self._gt_buf, self._split_buf = [], [], [], []
        # Buffered bytes, so the flush is bounded by memory as well as
        # by row count (see _FLUSH_BYTES).
        self._img_bytes = 0
        self._mask_bytes = 0
        # Painted snippet masks ride along in their own aligned table:
        # mask_pixels[j] is a binary (H, W) layer belonging to sample row
        # mask_sample[j], named mask_names[j]. A separate table (rather than
        # a per-sample dataset) keeps overlapping masks and any per-sample
        # count lossless. Datasets are created on demand so legacy files
        # append cleanly and mask-free exports stay byte-identical.
        self._mask_px_buf, self._mask_row_buf, self._mask_name_buf = [], [], []
        self._mask_n = 0
        # Aligned per-sample string columns, each created on demand - only
        # once a non-empty value is seen - so an export that never uses one
        # stays byte-identical and legacy files append cleanly; rows written
        # before the first value read back as "".
        #
        #   locations   the project's per-image location tag
        #   object_ids  which linked object a sample is a view of ("" for a
        #               hard negative, which is a view of nothing)
        self._str_bufs = {name: [] for name in _STRING_COLUMNS}
        # Set when appending to a file whose class list has since changed.
        self.added_classes, self.dropped_classes = [], []

        already = os.path.exists(path) and os.path.getsize(path) > 0
        self._f = h5py.File(path, "a")
        if already and "images" in self._f:
            self._validate_existing()
            self._n = self._f["images"].shape[0]
            if "mask_pixels" in self._f:
                self._mask_n = self._f["mask_pixels"].shape[0]
        else:
            self._create()
        self._has_str = {name: name in self._f for name in _STRING_COLUMNS}
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
            dtype=self.pixel_dtype,
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
        existing_dtype = str(f["images"].dtype)
        if existing_dtype != self.pixel_dtype:
            raise ValueError(
                f"Cannot append: the existing file stores {existing_dtype} "
                f"pixels, not {self.pixel_dtype} - mixing the two would "
                "give the samples two different value scales.")
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

    def add(self, image_hwc, label_index, gt, split_value,
            location: str = "", object_id: str = "") -> int:
        """Buffer one sample; flushes when a batch has accumulated.

        Returns the sample's global row index, so masks (and any future
        per-sample sidecar) can reference it before the flush happens.

        ``location`` is the sample's free-text location tag ("" when
        untagged). ``object_id`` names the linked object this sample is a
        view of - the same id every other view of it carries, so a consumer
        can group the views of one object, hold all of them out of a split
        together, and tell whether a file already contains an object before
        appending it again. Empty for a hard negative.
        """
        self._img_buf.append(image_hwc)
        self._lbl_buf.append(label_index)
        self._gt_buf.append(gt)
        self._split_buf.append(split_value)
        self._str_bufs["locations"].append(str(location or ""))
        self._str_bufs["object_ids"].append(str(object_id or ""))
        self._img_bytes += getattr(image_hwc, "nbytes", 0)
        row = self._n + len(self._img_buf) - 1
        if (len(self._img_buf) >= _FLUSH_BATCH
                or self._img_bytes >= _FLUSH_BYTES):
            self._flush()
        return row

    def add_mask(self, sample_row: int, name: str, mask):
        """Buffer one named binary mask belonging to sample ``sample_row``."""
        pixels = np.asarray(mask, dtype="uint8")
        self._mask_px_buf.append(pixels)
        self._mask_row_buf.append(int(sample_row))
        self._mask_name_buf.append(str(name))
        self._mask_bytes += pixels.nbytes
        if (len(self._mask_px_buf) >= _FLUSH_BATCH
                or self._mask_bytes >= _FLUSH_BYTES):
            self._flush_masks()

    def _ensure_mask_datasets(self):
        """Create the mask table lazily (legacy files gain it on first use)."""
        f = self._f
        if "mask_pixels" in f:
            return
        f.create_dataset(
            "mask_pixels",
            shape=(0, self.height, self.width),
            maxshape=(None, self.height, self.width),
            dtype="uint8",
            chunks=(self._chunk, self.height, self.width),
            compression=self._compression)
        f.create_dataset("mask_sample", shape=(0,), maxshape=(None,),
                         dtype="int64", chunks=(_META_CHUNK,))
        f.create_dataset(
            "mask_names", shape=(0,), maxshape=(None,),
            dtype=h5py.string_dtype("utf-8"), chunks=(_META_CHUNK,))

    def _flush_masks(self):
        if not self._mask_px_buf:
            return
        self._ensure_mask_datasets()
        f = self._f
        end = self._mask_n + len(self._mask_px_buf)
        f["mask_pixels"].resize(end, axis=0)
        f["mask_pixels"][self._mask_n:end] = np.stack(self._mask_px_buf)
        f["mask_sample"].resize(end, axis=0)
        f["mask_sample"][self._mask_n:end] = np.asarray(
            self._mask_row_buf, dtype="int64")
        f["mask_names"].resize(end, axis=0)
        f["mask_names"][self._mask_n:end] = self._mask_name_buf
        self._mask_n = end
        self._mask_px_buf.clear()
        self._mask_row_buf.clear()
        self._mask_name_buf.clear()
        self._mask_bytes = 0

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
        for name in _STRING_COLUMNS:
            self._flush_string_column(name, end)
        self._n = end
        self._img_buf.clear(); self._lbl_buf.clear()
        self._gt_buf.clear(); self._split_buf.clear()
        for buf in self._str_bufs.values():
            buf.clear()
        self._img_bytes = 0

    def _flush_string_column(self, name: str, end: int):
        """Write one aligned string column, creating it on first use.

        The dataset appears the first time a non-empty value is seen, sized
        to the rows already written so those read back as "" and every row
        stays aligned with ``images``. An export with nothing to say in this
        column writes no dataset at all, which is what keeps files written
        before the column existed appendable.
        """
        buf = self._str_bufs[name]
        f = self._f
        if not self._has_str.get(name) and any(buf):
            f.create_dataset(
                name, shape=(self._n,), maxshape=(None,),
                dtype=h5py.string_dtype("utf-8"), chunks=(_META_CHUNK,))
            self._has_str[name] = True
        if self._has_str.get(name):
            f[name].resize(end, axis=0)
            f[name][self._n:end] = buf

    def close(self) -> int:
        """Flush, close the file and return the total sample count."""
        try:
            self._flush()
            self._flush_masks()
        finally:
            total = self._n
            self._f.close()
        return total


def _positive_windows(pts, img_width, img_height, width, height, offset):
    """The example crops for every label: ``{(x0, y0): (class_index, object_id)}``.

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
    for whichever label sits nearest its centre - and that label's object id
    goes with it, so the crop names the object it is actually framed on.

    ``pts`` are ``(x, y, class_index, object_id)``.
    """
    windows: dict[tuple[int, int], tuple[int, str, float]] = {}
    max_x, max_y = img_width - width, img_height - height
    if max_x < 0 or max_y < 0:
        return {}

    def offer(x0, y0, x, y, class_index, object_id):
        """Record a crop, keeping whichever label sits nearest its centre."""
        x0 = min(max(x0, 0), max_x)
        y0 = min(max(y0, 0), max_y)
        away = max(abs(x - (x0 + width / 2.0)), abs(y - (y0 + height / 2.0)))
        held = windows.get((x0, y0))
        if held is None or away < held[2]:
            windows[(x0, y0)] = (class_index, object_id, away)

    # Pass 1: the exactly-centred crop for every label, using the same centring
    # rule as the sub-image GeoTIFF export. Dicts keep insertion order, so these
    # are written to the dataset before any shifted copy - the first snippet of
    # each label is the one framed exactly like its sub-image.
    bases = []
    for x, y, class_index, object_id in pts:
        base_x, base_y = centered_window(
            x, y, width, height, img_width, img_height)
        bases.append((base_x, base_y, x, y, class_index, object_id))
        offer(base_x, base_y, x, y, class_index, object_id)
        # Where the label lands inside its centred crop. Anything other than
        # (width//2, height//2) means the crop was shifted off an image edge -
        # log it so a real off-centre export can be traced to its label.
        debug(f"h5 centre crop: label px ({x:.1f}, {y:.1f}) on "
              f"{img_width}x{img_height} -> window ({base_x}, {base_y}), "
              f"label at ({x - base_x:.1f}, {y - base_y:.1f}) of "
              f"{width}x{height}")

    # Pass 2: the eight offsets, measured from each label's centred crop.
    if offset > 0:
        for base_x, base_y, x, y, class_index, object_id in bases:
            for dy in (-offset, 0, offset):
                for dx in (-offset, 0, offset):
                    if dx == 0 and dy == 0:
                        continue  # already placed in pass 1
                    offer(base_x + dx, base_y + dy, x, y, class_index,
                          object_id)

    return {pos: (held[0], held[1]) for pos, held in windows.items()}


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
                 examples_only=False, location="", pixel_dtype="uint8",
                 protect_labels=()):
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

    ``protect_labels`` are labels that are NOT being exported here but whose
    ground must still be kept out of the negative grid: every other object
    on this raster, whatever its class. Without them the rule "nothing
    labelled becomes a hard negative" only held because every run happened
    to export every label it knew about - exporting one linked object, or
    exporting negatives alone, would have offered the rest as background.
    They are protected with the same crop footprint the exported labels get,
    so the guarded area does not depend on which labels were chosen.

    With ``negative_ratio`` set, this image's hard negatives are shared out
    over train/validate/test in those proportions instead of all taking
    ``split_value``; examples always take ``split_value``.

    With ``examples_only`` set, only the label-bearing example crops are
    written - the hard-negative sliding window never runs.

    ``location`` is the image's free-text location tag; every snippet cut
    from this raster (examples and negatives alike) records it in the
    ``locations`` dataset.

    ``pixel_dtype`` selects how snippet pixels are stored: "uint8" applies
    the per-raster display stretch (the historical behaviour, honouring
    ``channels``); "float32" writes one grayscale channel of native-scale
    values with NO stretch (see :func:`_float_window_pixels`).
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
            pts.append((float(lab.pixel_x), float(lab.pixel_y), ci,
                        str(getattr(lab, "object_id", "") or "")))

    # Every painted mask on this image, in source-pixel anchoring. Each
    # example crop gets every mask that intersects it (re-anchored by pure
    # translation), so the mask table stays true to the ground even where
    # crops overlap or a neighbouring object's mask leaks into the window.
    mask_entries = [entry for lab in labels
                    for entry in getattr(lab, "masks", []) or []]

    with rasterio.open(path) as src:
        nodata = src.nodata
        as_float = pixel_dtype == "float32"
        # One contrast stretch per raster (None for uint8 sources), so every
        # snippet of this image is scaled identically - see _band_scaling.
        # The float32 format never stretches, so it skips the sampling read.
        scaling = None if as_float else _band_scaling(src)

        def window_pixels(win):
            if as_float:
                return _float_window_pixels(src, win, nodata)
            return _window_pixels(src, win, channels, nodata, scaling=scaling)

        offset = max(0, int(positive_offset))
        positives = _positive_windows(
            pts, src.width, src.height, width, height, offset)

        for (x0, y0), (class_index, object_id) in positives.items():
            if cancel_check and cancel_check():
                return added, negative_counts, 0
            arr = window_pixels(Window(x0, y0, width, height))
            if arr is None:
                continue  # entirely nodata
            row = writer.add(arr, class_index, True, split_value,
                             location=location, object_id=object_id)
            for entry in mask_entries:
                layer = entry_in_window(entry, x0, y0, width, height)
                if layer.any():
                    writer.add_mask(row, entry["name"], layer)
            added += 1

        if examples_only:
            # No hard negatives wanted: the sliding window never runs.
            return added, negative_counts, 0

        xs = _snippet_positions(src.width, width, step_x)
        ys = _snippet_positions(src.height, height, step_y)
        # Guarded ground: the crops actually being written, plus the crops
        # every other labelled object on this raster WOULD occupy. The class
        # index is irrelevant here - a label of a class this export does not
        # carry (or does not want) still marks ground that is not background.
        guarded = set(positives)
        if protect_labels:
            guarded |= set(_positive_windows(
                [(float(l.pixel_x), float(l.pixel_y), 0, "")
                 for l in protect_labels],
                src.width, src.height, width, height, offset))
        excluded = _excluded_grid(guarded, xs, ys, width, height)

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
                arr = window_pixels(Window(x0, y0, width, height))
                if arr is None:
                    continue  # entirely nodata
                negative_split = split_value
                if pool is not None and taken < pool.size:
                    negative_split = int(pool[taken])
                    taken += 1
                writer.add(arr, hard_negative_index, False, negative_split,
                           location=location)
                negative_counts[negative_split] += 1
                added += 1
    return added, negative_counts, int(excluded.sum())


class H5ExportWorker(QObject):
    """Runs the HDF5 export off the UI thread."""

    progress = pyqtSignal(int, int, int)   # (image_index, total_images, samples)
    finished = pyqtSignal(object, str)     # (summary dict or None, error)

    def __init__(self, out_path, images, options):
        """Store the output path, image list and export options.

        ``images`` is a list of :class:`ExportImage`. The per-image
        ``examples_only`` lets one run mix labels-only images with flagged
        hard-negative sources, which are slid in full; ``protect`` carries
        the labels that must stay out of the negative grid without being
        exported.
        """
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
                positive_offset=opts.get("positive_offset"),
                pixel_dtype=opts.get("pixel_dtype", "uint8"))
            total_images = len(self._images)
            samples = 0
            for i, request in enumerate(self._images):
                if self._cancelled:
                    break
                self.progress.emit(i, total_images, samples)
                if not os.path.exists(request.path):
                    errors.append((request.path, "file not found"))
                    continue
                try:
                    added, negatives, dropped = export_image(
                        writer, request.path, request.labels,
                        opts["height"], opts["width"],
                        opts["overlap"], opts["channels"], opts["split_value"],
                        class_to_index, hard_negative_index,
                        cancel_check=lambda: self._cancelled,
                        negative_ratio=negative_ratio, rng=rng,
                        positive_offset=opts.get(
                            "positive_offset", DEFAULT_POSITIVE_OFFSET),
                        examples_only=request.examples_only,
                        location=request.location,
                        pixel_dtype=opts.get("pixel_dtype", "uint8"),
                        protect_labels=request.protect)
                    samples += added
                    excluded += dropped
                    # split_idx, not i: reusing the outer image index here
                    # left it stuck at 2 after the first image with negatives,
                    # so every later progress signal reported a stale index.
                    for split_idx, count in enumerate(negatives):
                        negative_counts[split_idx] += count
                except Exception as e:  # noqa: BLE001 - report, keep going
                    errors.append((request.path, str(e)))
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

    def __init__(self, counts=None, parent=None, defaults=None,
                 selection=None, estimator=None):
        """Build the dialog.

        ``counts`` sizes the scope options - keys ``all``, ``visible``,
        ``labelled_all``, ``labelled_visible``, ``flagged_all`` and
        ``flagged_visible``, each an image count, all defaulting to 0.

        ``selection`` is what turns this into the per-object dialog: keys
        ``ids`` (the chosen object ids), ``instances`` and
        ``instances_visible`` (how many images they appear in) and
        ``classes`` ({class name: count} across those instances, so a linked
        object whose views disagree about a class shows the disagreement
        before it is exported). ``None`` for the whole-project export.

        ``estimator`` is called with this dialog and must return an
        ``estimate_export`` dict - what the current settings would write.
        It runs on every change, so it must not touch the filesystem.
        """
        super().__init__(parent)
        self._counts = dict(counts or {})
        self._selection = dict(selection) if selection else None
        self._estimator = estimator
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

        # Where the examples come from. When the dialog was opened on an
        # object, that object leads the list and is preselected.
        n = self._counts
        ex_box = QGroupBox("Examples (gt=true) from")
        ex_layout = QVBoxLayout(ex_box)
        self._example_buttons = []
        if self._selection:
            summary = self._selection_summary()
            layout.addWidget(self._selection_banner(summary))
            self._add_example_option(
                ex_layout, EXAMPLES_OBJECT,
                f"The selected object - every image it appears in "
                f"({self._selection.get('instances', 0)})",
                "Export the true-positive snippets for this object alone, "
                "from every image it was labelled in.")
            self._add_example_option(
                ex_layout, EXAMPLES_OBJECT_VISIBLE,
                f"The selected object - only layers toggled on "
                f"({self._selection.get('instances_visible', 0)})",
                "The same, restricted to the images currently toggled on in "
                "the viewer.",
                enabled=self._selection.get("instances_visible", 0) > 0)
        self._add_example_option(
            ex_layout, EXAMPLES_VISIBLE,
            f"Layers toggled on, with labels ({n.get('labelled_visible', 0)})",
            "Every label on the images toggled on in the viewer.",
            enabled=n.get("labelled_visible", 0) > 0)
        self._add_example_option(
            ex_layout, EXAMPLES_ALL,
            f"All loaded layers, with labels ({n.get('labelled_all', 0)})",
            "Every label in the project's loaded imagery, visible or not.",
            enabled=n.get("labelled_all", 0) > 0)
        self._add_example_option(
            ex_layout, EXAMPLES_NONE, "Nothing - hard negatives only",
            "Write no examples at all: just the sliding-window negatives "
            "chosen below, which is how a set is topped up with negatives "
            "from a new area.")
        layout.addWidget(ex_box)

        # ...and, independently, which images get slid for hard negatives.
        neg_box = QGroupBox("Hard negatives (gt=false) from")
        neg_layout = QVBoxLayout(neg_box)
        self._negative_buttons = []
        self._add_negative_option(
            neg_layout, NEGATIVES_NONE, "Nothing - true positives only",
            "No sliding window runs; the file gets example crops alone.")
        self._add_negative_option(
            neg_layout, NEGATIVES_SAME,
            "Only the images that contribute examples",
            "Slide the same images the examples came from, and no others - "
            "so the export contains no image made up entirely of negatives.")
        self._add_negative_option(
            neg_layout, NEGATIVES_VISIBLE,
            f"Layers toggled on ({n.get('visible', 0)})",
            "Slide every image toggled on in the viewer, labelled or not.",
            enabled=n.get("visible", 0) > 0)
        self._add_negative_option(
            neg_layout, NEGATIVES_FLAGGED,
            f"Flagged hard-negative sources only "
            f"({n.get('flagged_all', 0)})",
            "Only the images flagged as hard-negative sources (right click "
            "an image on the canvas): confusers with no true positives.",
            enabled=n.get("flagged_all", 0) > 0)
        self._add_negative_option(
            neg_layout, NEGATIVES_ALL,
            f"All loaded layers ({n.get('all', 0)})",
            "Slide every loaded image.",
            enabled=n.get("all", 0) > 0)
        layout.addWidget(neg_box)

        # Nothing labelled is ever offered as background, whatever these two
        # say - every label on an image guards its ground (see export_image).
        guard = QLabel(
            "Labelled ground is never written as a hard negative, whichever "
            "images are slid.")
        guard.setWordWrap(True)
        layout.addWidget(guard)

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

        self.pixel_format_combo = QComboBox()
        self.pixel_format_combo.addItems(list(PIXEL_FORMAT_CHOICES.keys()))
        self.pixel_format_combo.setToolTip(
            "uint8 stores display-stretched bytes (each raster's 2-98 "
            "percentile window). float32 stores one grayscale channel of "
            "NATIVE-scale values: float imagery (sonar dB, elevation) "
            "passes through unchanged, so the same physical value reads "
            "the same in every image, and integer imagery is divided by "
            "its full range (uint8 by 255) to land in 0-1. (New files "
            "only.)")
        self.pixel_format_combo.currentTextChanged.connect(
            self._on_pixel_format_changed)
        form.addRow("Pixel format:", self.pixel_format_combo)

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
        self._neg_box = neg_box  # greyed out when no negatives are written
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

        # What the current settings would actually write. Pure arithmetic
        # on the label positions and the grid step - no raster is opened -
        # so it can follow every keystroke. Without it the only way to find
        # out was to press Export: at 64 px with 50% overlap one 12k x 12k
        # mosaic is about 140,000 windows, and the difference between a 2 GB
        # file and a 200 GB one is one spin box.
        self.estimate_label = QLabel("")
        self.estimate_label.setWordWrap(True)
        layout.addWidget(self.estimate_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Export")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # An opening pair for the two axes. Opened on an object, the useful
        # default is that object's true positives and nothing else; opened
        # from the menu it is what the single "All loaded layers" scope used
        # to mean - every labelled image exported and every image slid.
        if self._selection:
            self._select_value(self._example_buttons, EXAMPLES_OBJECT)
            self._select_value(self._negative_buttons, NEGATIVES_NONE)
        else:
            self._select_value(self._example_buttons, EXAMPLES_ALL)
            self._select_value(self._negative_buttons, NEGATIVES_ALL)

        # Last: setting the path fires _on_out_path_changed, which needs every
        # widget above to exist, and lets the file's own settings win over the
        # caller's defaults.
        self._apply_settings(self._defaults)
        self.out_edit.setText(self._defaults.get("out_path", ""))
        self._on_out_path_changed()
        # The axes may have been set before their toggled signals were
        # connected, so run the handler once for the initial enabled states.
        self._on_scope_changed()
        for spin in (self.height_spin, self.width_spin, self.overlap_spin,
                     self.offset_spin):
            spin.valueChanged.connect(self._refresh_estimate)
        self.offset_check.toggled.connect(self._refresh_estimate)
        self.channel_combo.currentIndexChanged.connect(self._refresh_estimate)
        self.pixel_format_combo.currentIndexChanged.connect(
            self._refresh_estimate)
        self._refresh_estimate()

    def _refresh_estimate(self, *_args):
        """Show what the current settings would write."""
        if self._estimator is None:
            return
        try:
            estimate = self._estimator(self)
        except Exception as exc:      # noqa: BLE001 - a count must not block
            debug(f"h5 estimate failed: {type(exc).__name__}: {exc}")
            self.estimate_label.setText("")
            return
        self.estimate_label.setText(format_estimate(estimate)
                                    if estimate else "")

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
        # The remembered axes, when they still apply. An object scope is
        # never restored: it belongs to the selection this dialog was opened
        # on, not to the last export.
        wanted = settings.get("examples_scope")
        if wanted in (EXAMPLES_VISIBLE, EXAMPLES_ALL, EXAMPLES_NONE):
            self._select_value(self._example_buttons, wanted)
        if settings.get("negatives_scope"):
            self._select_value(self._negative_buttons,
                               settings["negatives_scope"])
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
                ("compression", COMPRESSION_CHOICES, self.compress_combo),
                ("pixel_dtype", PIXEL_FORMAT_CHOICES,
                 self.pixel_format_combo)):
            if key in settings:
                label = _choice_label(choices, settings[key])
                if label is not None:
                    combo.setCurrentText(label)

    def _add_example_option(self, box, value, text, tip, enabled=True):
        """One radio on the examples axis."""
        self._add_option(box, self._example_buttons, value, text, tip,
                         enabled)

    def _add_negative_option(self, box, value, text, tip, enabled=True):
        """One radio on the hard-negatives axis."""
        self._add_option(box, self._negative_buttons, value, text, tip,
                         enabled)

    def _add_option(self, box, bucket, value, text, tip, enabled):
        button = QRadioButton(text)
        button.setToolTip(tip)
        button.setEnabled(enabled)
        button.toggled.connect(self._on_scope_changed)
        box.addWidget(button)
        bucket.append((value, button))

    def _selection_summary(self) -> str:
        """One line naming the object(s) and how their views are classed."""
        ids = self._selection.get("ids") or []
        classes = self._selection.get("classes") or {}
        spread = ", ".join(f"{name} x{count}"
                           for name, count in sorted(classes.items()))
        who = (f"Object {ids[0][:8]}..." if len(ids) == 1
               else f"{len(ids)} objects")
        return (f"{who}   seen in {self._selection.get('instances', 0)} "
                f"image(s)   {spread}")

    def _selection_banner(self, summary: str) -> QLabel:
        """The object header, and a warning when its views disagree."""
        text = summary
        if len(self._selection.get("classes") or {}) > 1:
            # Not an error: the instances are exported under their own class
            # names, as any other export would. But two names on one linked
            # object is usually a disagreement worth settling first.
            text += ("\nThese views do not agree on a class. Each snippet is "
                     "written under its own label's class.")
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _checked_value(self, bucket, fallback):
        for value, button in bucket:
            if button.isChecked():
                return value
        return fallback

    def _select_value(self, bucket, wanted):
        """Check the option for ``wanted``, or the first enabled one."""
        for value, button in bucket:
            if value == wanted and button.isEnabled():
                button.setChecked(True)
                return True
        for _value, button in bucket:
            if button.isEnabled():
                button.setChecked(True)
                return False
        return False

    def examples_scope(self) -> str:
        """Which images contribute gt=True crops - an EXAMPLES_* value."""
        return self._checked_value(self._example_buttons, EXAMPLES_NONE)

    def negatives_scope(self) -> str:
        """Which images get slid for negatives - a NEGATIVES_* value."""
        return self._checked_value(self._negative_buttons, NEGATIVES_NONE)

    def selected_object_ids(self) -> list:
        """The object ids this dialog was opened on ([] when it was not)."""
        return list((self._selection or {}).get("ids") or [])

    def _fixed_widgets(self):
        """The widgets an existing file dictates.

        Storage layout (size, channels, chunking, compression) is fixed once
        the datasets exist. The object radius is not, but changing it would
        mix two extraction rules in one dataset, so a file keeps the one it
        was built with.
        """
        return (self.height_spin, self.width_spin, self.channel_combo,
                self.pixel_format_combo, self.chunk_spin, self.compress_combo,
                self.offset_spin, self.offset_check)

    def _on_pixel_format_changed(self, *_):
        """float32 storage is always single-channel grayscale."""
        is_float = PIXEL_FORMAT_CHOICES.get(
            self.pixel_format_combo.currentText()) == "float32"
        if is_float:
            label = _choice_label(CHANNEL_CHOICES, 1)
            if label is not None:
                self.channel_combo.setCurrentText(label)
            self.channel_combo.setEnabled(False)
        elif self.pixel_format_combo.isEnabled():
            # Not file-locked (that path disables both combos together).
            self.channel_combo.setEnabled(True)

    def _on_split_negatives(self, enabled):
        """Enable the ratio inputs only while the split is switched on."""
        for spin in self.negative_ratio_spins.values():
            spin.setEnabled(enabled)
        self._update_ok_enabled()

    def _on_scope_changed(self, _checked=False):
        """Keep the split controls and the OK button in step with the axes."""
        self._neg_box.setEnabled(self._writes_negatives())
        self._update_ok_enabled()
        self._refresh_estimate()

    def _writes_negatives(self) -> bool:
        """Will this export write any hard negatives at all?

        Governs whether the split controls and their 100% check matter.
        """
        return self.negatives_scope() != NEGATIVES_NONE

    def _writes_anything(self) -> bool:
        """The one nonsense pair: no examples AND no negatives."""
        return (self.examples_scope() != EXAMPLES_NONE
                or self._writes_negatives())

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
        # The ratio only matters when negatives will actually be written.
        ratio_ok = (not self._writes_negatives()
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
        # An export with neither examples nor negatives would write an
        # empty file; it is the one pair of axis choices that means nothing.
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            has_path and ratio_ok and offset_ok and self._writes_anything())

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
        # Re-enabling everything may have freed the channels combo although
        # float32 (grayscale-only) is still selected.
        self._on_pixel_format_changed()
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
            "pixel_dtype": PIXEL_FORMAT_CHOICES[
                self.pixel_format_combo.currentText()],
            "split_value": SPLIT_CHOICES[self.split_combo.currentText()],
            "chunk": self.chunk_spin.value(),
            # 0 disables the eight offset copies (centred snippet only).
            "positive_offset": (self.offset_spin.value()
                                if self.offset_check.isChecked() else 0),
            "compression": COMPRESSION_CHOICES[self.compress_combo.currentText()],
            # The two axes; MainWindow turns them into the per-image
            # example/slide decisions the worker takes.
            "examples_scope": self.examples_scope(),
            "negatives_scope": self.negatives_scope(),
            "object_ids": self.selected_object_ids(),
            "split_negatives": (self.split_negatives_check.isChecked()
                                and self._writes_negatives()),
            "negative_ratio": tuple(
                self.negative_ratio_spins[name].value() / 100.0
                for name in ("Train", "Validate", "Test")),
        }
