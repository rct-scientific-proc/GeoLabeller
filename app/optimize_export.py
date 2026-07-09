"""Create optimized (tiled + pyramided) copies of loaded GeoTIFFs.

This module powers the Export -> Optimized GeoTIFFs feature. It mirrors the
layer tree's group structure into a chosen output directory and rewrites each
raster as an internally **tiled** GeoTIFF with **pyramid overviews**, while
preserving the original metadata (CRS, transform, dtype, nodata, band colour
interpretation, colormaps/palettes, tags, descriptions, scales/offsets, units).

Contents:
- ``optimize_geotiff`` - the pure conversion function (no Qt).
- ``plan_output_path`` - map a layer's (group, file) to its output path.
- ``OptimizeWorker`` - a QObject worker that runs conversions off the UI thread.
- ``OptimizeExportDialog`` - the setup dialog shown to the user.
"""
import os
import re
from pathlib import Path

import rasterio
from rasterio.enums import Resampling
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QDialogButtonBox, QFileDialog, QStyle, QApplication,
)

# Pyramid decimation factors requested for optimized outputs.
DEFAULT_OVERVIEWS = [2, 4, 8, 16, 32]

# Resampling methods offered in the UI (label -> enum).
RESAMPLING_CHOICES = {
    "Average (imagery)": Resampling.average,
    "Nearest (categorical/palette)": Resampling.nearest,
    "Gauss": Resampling.gauss,
    "Cubic": Resampling.cubic,
    "Mode": Resampling.mode,
}

# Compression options (UI label -> GDAL value; "KEEP" means leave source as-is).
COMPRESSION_CHOICES = {
    "DEFLATE (recommended)": "DEFLATE",
    "LZW": "LZW",
    "ZSTD": "ZSTD",
    "None": "NONE",
    "Keep original": "KEEP",
}

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


def _sanitize(part: str) -> str:
    """Make a single path component safe for the filesystem."""
    return _ILLEGAL.sub("_", part).strip() or "_"


def plan_output_path(output_dir, group_path: str, file_path: str) -> Path:
    """Return the output path for a layer, mirroring its group hierarchy.

    ``group_path`` uses '/' separators (e.g. "folder/sub"); each component
    becomes a subdirectory under ``output_dir``. The output keeps the source
    file's stem with a ``.tif`` extension.
    """
    out = Path(output_dir)
    for part in (group_path or "").split("/"):
        if part:
            out = out / _sanitize(part)
    return out / (_sanitize(Path(file_path).stem) + ".tif")


def _has_colormap(src) -> bool:
    """Return True if any band carries a colour table (paletted raster)."""
    for band in range(1, src.count + 1):
        try:
            if src.colormap(band):
                return True
        except (ValueError, KeyError):
            pass
    return False


def optimize_geotiff(src_path, dst_path, overviews=DEFAULT_OVERVIEWS,
                     resampling=Resampling.average, compress="DEFLATE",
                     blocksize=512, overwrite=False, cancel_check=None) -> str:
    """Write a tiled, pyramided copy of ``src_path`` to ``dst_path``.

    Returns one of ``"done"``, ``"skipped"`` (destination exists and
    ``overwrite`` is False) or ``"cancelled"``. Metadata from the source is
    preserved. Paletted rasters are always overviewed with nearest-neighbour so
    palette indices aren't blended into invalid values.

    ``cancel_check`` is an optional callable returning True to abort.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    if dst_path.exists() and not overwrite:
        return "skipped"
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(src_path) as src:
        profile = src.profile.copy()
        paletted = _has_colormap(src)
        overview_resampling = Resampling.nearest if paletted else resampling

        profile.update(
            driver="GTiff",
            tiled=True,
            blockxsize=blocksize,
            blockysize=blocksize,
            bigtiff="IF_SAFER",
        )
        comp = (compress or "KEEP").upper()
        if comp != "KEEP":
            if comp == "NONE":
                profile["compress"] = "none"
                profile.pop("predictor", None)
            else:
                profile["compress"] = comp.lower()
                if comp in ("DEFLATE", "LZW", "ZSTD"):
                    # Predictor 3 for floating point, 2 for integer data.
                    profile["predictor"] = (
                        3 if str(profile.get("dtype", "")).startswith("float")
                        else 2
                    )

        # Write to a temp file, then atomically replace, so a cancelled or
        # crashed run never leaves a half-written output in place.
        tmp_path = dst_path.with_suffix(dst_path.suffix + ".tmp")
        try:
            with rasterio.open(tmp_path, "w", **profile) as dst:
                # Copy pixels block-by-block so huge rasters don't blow memory.
                for _, window in src.block_windows(1):
                    if cancel_check and cancel_check():
                        raise _Cancelled()
                    dst.write(src.read(window=window), window=window)

                # --- preserve metadata -------------------------------------
                dst.colorinterp = src.colorinterp
                for band in range(1, src.count + 1):
                    try:
                        cmap = src.colormap(band)
                    except (ValueError, KeyError):
                        cmap = None
                    if cmap:
                        dst.write_colormap(band, cmap)

                dataset_tags = src.tags()
                if dataset_tags:
                    dst.update_tags(**dataset_tags)
                for band in range(1, src.count + 1):
                    band_tags = src.tags(band)
                    if band_tags:
                        dst.update_tags(band, **band_tags)

                if any(d is not None for d in src.descriptions):
                    dst.descriptions = src.descriptions
                try:
                    dst.scales = src.scales
                    dst.offsets = src.offsets
                except Exception:
                    pass
                if any(u for u in src.units):
                    dst.units = src.units

                # Build the internal pyramid overviews.
                dst.build_overviews(list(overviews), overview_resampling)
                dst.update_tags(ns="rio_overview",
                                resampling=overview_resampling.name)
        except _Cancelled:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return "cancelled"
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    os.replace(tmp_path, dst_path)
    return "done"


class _Cancelled(Exception):
    """Internal signal used to abort a conversion mid-file."""


class OptimizeWorker(QObject):
    """Runs a batch of GeoTIFF optimizations off the UI thread.

    Emits ``progress`` before each file and ``finished`` with a summary. The
    conversions themselves are pure file I/O (no shared app state), so this is
    safe to run on a worker thread.
    """

    progress = pyqtSignal(int, int, str)   # (index, total, filename)
    finished = pyqtSignal(int, int, object)  # (done, skipped, errors list)

    def __init__(self, tasks, options):
        """Store the (src, dst) task list and conversion options.

        Args:
            tasks: list of (source_path, dest_path) string tuples.
            options: dict with keys overviews, resampling, compress,
                blocksize, overwrite.
        """
        super().__init__()
        self._tasks = tasks
        self._options = options
        self._cancelled = False

    def cancel(self):
        """Request cancellation (checked between and within files)."""
        self._cancelled = True

    def process(self):
        """Convert every task, emitting progress and a final summary."""
        total = len(self._tasks)
        done = 0
        skipped = 0
        errors = []
        opts = self._options
        for i, (src, dst) in enumerate(self._tasks):
            if self._cancelled:
                break
            self.progress.emit(i, total, os.path.basename(src))
            try:
                result = optimize_geotiff(
                    src, dst,
                    overviews=opts["overviews"],
                    resampling=opts["resampling"],
                    compress=opts["compress"],
                    blocksize=opts["blocksize"],
                    overwrite=opts["overwrite"],
                    cancel_check=lambda: self._cancelled,
                )
                if result == "done":
                    done += 1
                elif result == "skipped":
                    skipped += 1
            except Exception as e:  # noqa: BLE001 - report, keep going
                errors.append((src, str(e)))
        self.finished.emit(done, skipped, errors)


class OptimizeExportDialog(QDialog):
    """Setup dialog for the "Optimized GeoTIFFs" export.

    Shows the layer group tree that will be mirrored, lets the user pick an
    output directory and conversion options, and exposes them via
    :meth:`get_options` after the dialog is accepted.
    """

    def __init__(self, layer_infos, parent=None):
        """Build the dialog from the given layer infos.

        Args:
            layer_infos: list of dicts with keys ``file_path``, ``group_path``,
                ``name`` (as returned by ``MapCanvas.get_layer_infos``).
        """
        super().__init__(parent)
        self._layer_infos = layer_infos
        self.setWindowTitle("Create Optimized GeoTIFFs")
        self.setMinimumWidth(560)
        self._build_ui()

    def _build_ui(self):
        """Assemble the dialog widgets and layout."""
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Create tiled, pyramided copies of the loaded GeoTIFFs for faster "
            "rendering. The layer group structure is mirrored into the output "
            "directory, and each image is rewritten with internal tiling and "
            "overviews while keeping its CRS, bands, colormap and other "
            "metadata."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Output directory row
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output directory:"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Choose a folder to write into...")
        self.out_edit.textChanged.connect(self._update_ok_enabled)
        out_row.addWidget(self.out_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_dir)
        out_row.addWidget(browse)
        layout.addLayout(out_row)

        # Options group
        opts = QGroupBox("Options")
        form = QFormLayout(opts)

        self.overview_edit = QLineEdit(
            ", ".join(str(f) for f in DEFAULT_OVERVIEWS))
        form.addRow("Pyramid levels:", self.overview_edit)

        self.resampling_combo = QComboBox()
        self.resampling_combo.addItems(list(RESAMPLING_CHOICES.keys()))
        form.addRow("Overview resampling:", self.resampling_combo)

        self.compress_combo = QComboBox()
        self.compress_combo.addItems(list(COMPRESSION_CHOICES.keys()))
        form.addRow("Compression:", self.compress_combo)

        self.block_combo = QComboBox()
        self.block_combo.addItems(["256", "512"])
        self.block_combo.setCurrentText("512")
        form.addRow("Tile block size:", self.block_combo)

        self.overwrite_check = QCheckBox("Overwrite existing output files")
        form.addRow("", self.overwrite_check)
        layout.addWidget(opts)

        # Preview of the mirrored tree
        layout.addWidget(QLabel(
            f"Structure to be created ({len(self._layer_infos)} image(s)):"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self._populate_preview()
        layout.addWidget(self.tree, 1)

        # Buttons
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Create")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_ok_enabled()

    def _populate_preview(self):
        """Fill the preview tree with the mirrored group/file structure."""
        style = QApplication.style()
        folder_icon = style.standardIcon(QStyle.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.SP_FileIcon)
        group_nodes = {"": None}  # group_path -> QTreeWidgetItem (None = root)

        def ensure_group(group_path: str):
            """Return the tree item for a group, creating ancestors as needed."""
            if group_path in group_nodes:
                return group_nodes[group_path]
            parent_path, _, name = group_path.rpartition("/")
            parent_item = ensure_group(parent_path) if parent_path else None
            item = QTreeWidgetItem([name or group_path])
            item.setIcon(0, folder_icon)
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            group_nodes[group_path] = item
            return item

        for info in self._layer_infos:
            parent = ensure_group(info.get("group_path", "") or "")
            leaf = QTreeWidgetItem(
                [_sanitize(Path(info["file_path"]).stem) + ".tif"])
            leaf.setIcon(0, file_icon)
            if parent is None:
                self.tree.addTopLevelItem(leaf)
            else:
                parent.addChild(leaf)
        self.tree.expandAll()

    def _choose_dir(self):
        """Open a directory picker and store the result."""
        path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.out_edit.text() or "")
        if path:
            self.out_edit.setText(path)

    def _update_ok_enabled(self):
        """Enable the Create button only once an output directory is chosen."""
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(bool(self.out_edit.text().strip()))

    def _parse_overviews(self):
        """Parse the pyramid-levels field into a sorted list of ints (>1)."""
        factors = []
        for tok in re.split(r"[,\s]+", self.overview_edit.text().strip()):
            if not tok:
                continue
            try:
                v = int(tok)
            except ValueError:
                continue
            if v > 1:
                factors.append(v)
        return sorted(set(factors)) or list(DEFAULT_OVERVIEWS)

    def get_options(self) -> dict:
        """Return the chosen output directory and conversion options."""
        return {
            "output_dir": Path(self.out_edit.text().strip()),
            "overviews": self._parse_overviews(),
            "resampling": RESAMPLING_CHOICES[self.resampling_combo.currentText()],
            "compress": COMPRESSION_CHOICES[self.compress_combo.currentText()],
            "blocksize": int(self.block_combo.currentText()),
            "overwrite": self.overwrite_check.isChecked(),
        }
