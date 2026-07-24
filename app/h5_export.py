"""Export labelled GeoTIFFs to the HDF5 CNN dataset format.

See ``todo/h5_format.md`` for the target layout. Each source raster is tiled by
a sliding H x W (pixel) window with a given overlap; a snippet whose bounding
box contains a label becomes a genuine example of that label's class
(``gt=True``), and every other snippet is a hard negative (``gt=False``, class
``"hard_negative"``). When a snippet contains labels of more than one class, the
label nearest the snippet centre wins.

The datasets are resizable and written incrementally (streamed to disk), so an
export can be huge without exhausting memory, and a later export can **append**
to the same file - e.g. export the visible "Train" layers, then turn on the
"Validate" layers and append them with a different split value.

Contents:
- ``H5DatasetWriter`` - create/append + streamed writes (no Qt).
- ``export_image`` - slide over one raster, add snippets to a writer.
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
    QDialogButtonBox, QFileDialog,
)

HARD_NEGATIVE = "hard_negative"

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


def _snippet_positions(total: int, window: int, step: int) -> list[int]:
    """Top-left offsets tiling ``[0, total)`` with a final shifted-to-fit one."""
    if total < window:
        return []
    positions = list(range(0, total - window + 1, max(1, step)))
    if positions[-1] != total - window:
        positions.append(total - window)
    return positions


def _window_pixels(src, window, channels, nodata):
    """Read a window as an (H, W, C) uint8 array, or None if entirely nodata."""
    data = src.read(window=window)  # (bands, h, w)
    if nodata is not None and bool(np.all(data == nodata)):
        return None
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
    ``"hard_negative"`` last). On append the existing file's H/W/C and class
    list must match, so label indices stay consistent.
    """

    def __init__(self, path, height, width, channels, classes,
                 chunk=_DEFAULT_CHUNK, compression=None):
        """Open ``path`` for create-or-append and prepare the datasets.

        ``chunk`` and ``compression`` apply only when the file is *created*;
        appending reuses the existing datasets' storage properties (chunking and
        compression are fixed at creation time).
        """
        self.height, self.width, self.channels = height, width, channels
        self.classes = list(classes)
        self._chunk = max(1, int(chunk))
        self._compression = compression
        self._n = 0
        self._img_buf, self._lbl_buf, self._gt_buf, self._split_buf = [], [], [], []

        already = os.path.exists(path) and os.path.getsize(path) > 0
        self._f = h5py.File(path, "a")
        if already and "images" in self._f:
            self._validate_existing()
            self._n = self._f["images"].shape[0]
        else:
            self._create()

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
        if existing and existing != self.classes:
            raise ValueError(
                "Cannot append: the existing file's class list differs from "
                "the current project's classes (label indices would be "
                "inconsistent). Keep the project's classes stable across "
                "appends.")

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


def export_image(writer, path, labels, height, width, overlap, channels,
                 split_value, class_to_index, hard_negative_index,
                 cancel_check=None) -> int:
    """Slide over one raster, adding every snippet to ``writer``.

    Returns the number of snippets added. Windows never cross the image edge
    (a final window is shifted to fit), so every snippet is exactly HxW.
    """
    added = 0
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
        xs = _snippet_positions(src.width, width, step_x)
        ys = _snippet_positions(src.height, height, step_y)
        for y0 in ys:
            for x0 in xs:
                if cancel_check and cancel_check():
                    return added
                arr = _window_pixels(
                    src, Window(x0, y0, width, height), channels, nodata)
                if arr is None:
                    continue  # entirely nodata
                inside = [(x, y, ci) for (x, y, ci) in pts
                          if x0 <= x < x0 + width and y0 <= y < y0 + height]
                if inside:
                    cx, cy = x0 + width / 2.0, y0 + height / 2.0
                    _x, _y, ci = min(
                        inside, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
                    writer.add(arr, ci, True, split_value)
                else:
                    writer.add(arr, hard_negative_index, False, split_value)
                added += 1
    return added


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
        try:
            writer = H5DatasetWriter(
                self._out_path, opts["height"], opts["width"],
                opts["channels"], classes,
                chunk=opts.get("chunk", _DEFAULT_CHUNK),
                compression=opts.get("compression"))
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
                    samples += export_image(
                        writer, path, labels, opts["height"], opts["width"],
                        opts["overlap"], opts["channels"], opts["split_value"],
                        class_to_index, hard_negative_index,
                        cancel_check=lambda: self._cancelled)
                except Exception as e:  # noqa: BLE001 - report, keep going
                    errors.append((path, str(e)))
            total = writer.close()
            writer = None
            self.finished.emit(
                {"total": total, "path": self._out_path,
                 "cancelled": self._cancelled, "errors": errors}, "")
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            self.finished.emit(None, str(e))


class H5ExportDialog(QDialog):
    """Setup dialog for the HDF5 dataset export."""

    def __init__(self, all_count, visible_count, parent=None):
        """Build the dialog. ``*_count`` size the scope radio labels."""
        super().__init__(parent)
        self._all_count = all_count
        self._visible_count = visible_count
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
        self.scope_all.setChecked(True)
        if self._visible_count == 0:
            self.scope_visible.setEnabled(False)
        self._scope_group = QButtonGroup(self)
        self._scope_group.addButton(self.scope_all)
        self._scope_group.addButton(self.scope_visible)
        scope_layout.addWidget(self.scope_all)
        scope_layout.addWidget(self.scope_visible)
        layout.addWidget(scope_box)

        # Options
        opts = QGroupBox("Snippet options")
        form = QFormLayout(opts)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 8192)
        self.height_spin.setValue(64)
        form.addRow("Snippet height (px):", self.height_spin)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 8192)
        self.width_spin.setValue(64)
        form.addRow("Snippet width (px):", self.width_spin)

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 95)
        self.overlap_spin.setValue(50)
        self.overlap_spin.setSuffix(" %")
        form.addRow("Overlap:", self.overlap_spin)

        self.channel_combo = QComboBox()
        self.channel_combo.addItems(list(CHANNEL_CHOICES.keys()))
        form.addRow("Channels:", self.channel_combo)

        self.split_combo = QComboBox()
        self.split_combo.addItems(list(SPLIT_CHOICES.keys()))
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

        # Output file
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output .h5:"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("dataset.h5 (existing file is appended)")
        self.out_edit.textChanged.connect(self._update_ok_enabled)
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

        self._update_ok_enabled()

    def _choose_file(self):
        """Pick an output .h5 (new or existing to append to)."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export HDF5 Dataset", self.out_edit.text() or "dataset.h5",
            "HDF5 (*.h5 *.hdf5)", options=QFileDialog.DontConfirmOverwrite)
        if path:
            if not path.lower().endswith((".h5", ".hdf5")):
                path += ".h5"
            self.out_edit.setText(path)

    def _update_ok_enabled(self):
        """Enable Export only once an output path is set; note append mode."""
        text = self.out_edit.text().strip()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(text))
        if text and os.path.exists(text):
            self._append_note.setText("Existing file - snippets will be appended.")
        else:
            self._append_note.setText("")

    def scope_visible_only(self) -> bool:
        """Return True if only visible layers should be exported."""
        return self.scope_visible.isChecked()

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
            "compression": COMPRESSION_CHOICES[self.compress_combo.currentText()],
        }
