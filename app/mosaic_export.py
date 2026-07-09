"""Mosaic multiple loaded GeoTIFFs into a single output raster.

Powers the Export -> Mosaic feature. All selected sources are reprojected to a
common output CRS and composited into the requested colour mode (RGB /
grayscale / palette), then written as a tiled, pyramided GeoTIFF with a chosen
nodata value and compression.

Contents:
- ``build_mosaic`` - the pure builder (no Qt).
- ``MosaicWorker`` - a QObject worker that runs the build off the UI thread.
- ``MosaicExportDialog`` - the setup dialog shown to the user.

The mosaic is streamed one output tile at a time (each source is reprojected
into the current output block with rasterio.warp.reproject and composited), so
memory stays bounded by a single tile regardless of the total mosaic size.
"""
import math
import os
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import ColorInterp, Resampling
from rasterio.warp import calculate_default_transform, reproject, transform_bounds
from rasterio.windows import bounds as window_bounds, transform as window_transform
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QComboBox, QRadioButton, QButtonGroup,
    QDialogButtonBox, QFileDialog,
)

DEFAULT_OVERVIEWS = [2, 4, 8, 16, 32]
COLOR_MODES = ["RGB", "Grayscale", "Palette"]

RESAMPLING_CHOICES = {
    "Nearest": Resampling.nearest,
    "Bilinear": Resampling.bilinear,
    "Cubic": Resampling.cubic,
    "Average": Resampling.average,
}

COMPRESSION_CHOICES = {
    "DEFLATE (recommended)": "DEFLATE",
    "LZW": "LZW",
    "ZSTD": "ZSTD",
    "None": "NONE",
}


class _Cancelled(Exception):
    """Internal signal used to abort a mosaic build."""


def _expand_palette(index_band, colormap):
    """Expand a paletted index band (H,W) into a (3,H,W) uint8 RGB array."""
    lut = np.zeros((256, 3), dtype="uint8")
    for idx, rgba in colormap.items():
        if 0 <= idx < 256:
            lut[idx] = rgba[:3]
    idx = np.clip(index_band.astype("int32"), 0, 255)
    return np.transpose(lut[idx], (2, 0, 1))


def _color_spec(native_count, mode, colormap):
    """Return (out_band_count, colorinterp, colormap_or_None) for a colour mode.

    Computed once up front so the output dataset can be created before the
    per-block conversion loop runs.
    """
    if mode == "Grayscale":
        return 1, [ColorInterp.gray], None
    if mode == "Palette":
        if native_count != 1:
            raise ValueError(
                "Palette output requires single-band input layers.")
        interp = [ColorInterp.palette] if colormap else [ColorInterp.gray]
        return 1, interp, colormap
    return 3, [ColorInterp.red, ColorInterp.green, ColorInterp.blue], None


def _convert_block(buf, native_count, mode, colormap):
    """Convert a native (native_count,H,W) block to the output colour mode."""
    dtype = buf.dtype
    if mode == "Grayscale":
        if native_count >= 3:
            lum = (0.299 * buf[0].astype("float64")
                   + 0.587 * buf[1].astype("float64")
                   + 0.114 * buf[2].astype("float64"))
            return lum.astype(dtype)[np.newaxis, ...]
        return buf[:1]
    if mode == "Palette":
        return buf[:1]
    # RGB
    if native_count >= 3:
        return buf[:3]
    if native_count == 1 and colormap:
        return _expand_palette(buf[0], colormap)
    # 1-band without palette (replicate) or 2-band (pad with band 1).
    return (np.repeat(buf[:1], 3, axis=0) if native_count == 1
            else np.concatenate([buf, buf[:1]], axis=0)[:3])


def build_mosaic(sources, out_path, *, target_crs, color_mode="RGB", nodata=0,
                 resampling=Resampling.nearest, compress="DEFLATE",
                 blocksize=512, overviews=DEFAULT_OVERVIEWS, res=None,
                 progress_cb=None, cancel_check=None) -> dict:
    """Build a mosaic GeoTIFF from ``sources`` into ``out_path``.

    Args:
        sources: list of source raster file paths (all georeferenced).
        out_path: output GeoTIFF path.
        target_crs: CRS every source is reprojected to (str/EPSG/CRS).
        color_mode: "RGB", "Grayscale" or "Palette".
        nodata: output nodata value (also fills gaps / overlaps).
        resampling: reprojection + overview resampling.
        compress: "DEFLATE"/"LZW"/"ZSTD"/"NONE".
        blocksize: internal tile size.
        overviews: pyramid decimation factors ([] for none).
        res: optional (xres, yres) output resolution; None = the first source's
            resolution in the target CRS.
        progress_cb: optional callable(message, fraction).
        cancel_check: optional callable -> True to abort.

    Returns a summary dict; raises on error or ``_Cancelled`` if aborted.
    """
    def report(msg, frac):
        if progress_cb:
            progress_cb(msg, frac)

    def cancelled():
        return bool(cancel_check and cancel_check())

    if not sources:
        raise ValueError("No source layers to mosaic.")

    target = CRS.from_user_input(target_crs)
    report("Opening sources...", 0.03)
    datasets = []
    try:
        band_counts, dtypes = set(), set()
        first_colormap = None
        src_bounds = []  # each source's bounds in the TARGET crs (index-aligned)
        for path in sources:
            if cancelled():
                raise _Cancelled()
            ds = rasterio.open(path)
            datasets.append(ds)
            if ds.crs is None:
                raise ValueError(
                    f"{Path(path).name} has no CRS; only georeferenced "
                    "layers can be mosaicked.")
            band_counts.add(ds.count)
            dtypes.add(ds.dtypes[0])
            if first_colormap is None:
                try:
                    first_colormap = ds.colormap(1)
                except (ValueError, KeyError):
                    pass
            src_bounds.append(transform_bounds(ds.crs, target, *ds.bounds))

        if len(band_counts) != 1:
            raise ValueError(
                f"Selected layers have differing band counts "
                f"{sorted(band_counts)}; mosaic requires the same number of "
                "bands in every layer.")
        if len(dtypes) != 1:
            raise ValueError(
                f"Selected layers have differing data types {sorted(dtypes)}; "
                "mosaic requires a uniform data type.")
        native_count = band_counts.pop()
        dtype = dtypes.pop()

        # --- output grid: union of source footprints at the chosen resolution
        report("Computing output grid...", 0.08)
        if res:
            res_x, res_y = abs(float(res[0])), abs(float(res[1]))
        else:
            # Match rasterio.merge's default: the first source's resolution
            # once reprojected into the target CRS.
            t0, _w0, _h0 = calculate_default_transform(
                datasets[0].crs, target, datasets[0].width,
                datasets[0].height, *datasets[0].bounds)
            res_x, res_y = abs(t0.a), abs(t0.e)

        west = min(b[0] for b in src_bounds)
        south = min(b[1] for b in src_bounds)
        east = max(b[2] for b in src_bounds)
        north = max(b[3] for b in src_bounds)
        width = max(1, int(math.ceil((east - west) / res_x)))
        height = max(1, int(math.ceil((north - south) / res_y)))
        out_transform = Affine(res_x, 0.0, west, 0.0, -res_y, north)

        out_count, colorinterp, colormap = _color_spec(
            native_count, color_mode, first_colormap)

        profile = {
            "driver": "GTiff",
            "height": height,
            "width": width,
            "count": out_count,
            "dtype": dtype,
            "crs": target,
            "transform": out_transform,
            "nodata": nodata,
            "tiled": True,
            "blockxsize": blocksize,
            "blockysize": blocksize,
            "bigtiff": "IF_SAFER",
        }
        comp = (compress or "NONE").upper()
        if comp != "NONE":
            profile["compress"] = comp.lower()
            if comp in ("DEFLATE", "LZW", "ZSTD"):
                profile["predictor"] = (
                    3 if str(dtype).startswith("float") else 2)

        # Use GDAL's built-in multi-threaded warping (safe: GDAL manages its own
        # worker threads over a single dataset). We deliberately keep the
        # per-tile loop single-threaded to avoid GDAL's dataset-handle
        # thread-safety pitfalls.
        num_threads = os.cpu_count() or 1
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        try:
            with rasterio.open(tmp_path, "w", **profile) as dst:
                # Stream the mosaic one output tile at a time: for each output
                # block, reproject every overlapping source into it and
                # composite. Memory stays bounded by a single tile regardless of
                # how large the whole mosaic is.
                windows = list(dst.block_windows(1))
                total = len(windows) or 1
                for bi, (_, win) in enumerate(windows):
                    if cancelled():
                        raise _Cancelled()
                    w_b = window_bounds(win, out_transform)  # (w, s, e, n)
                    wh, ww = int(win.height), int(win.width)
                    block_transform = window_transform(win, out_transform)
                    acc = np.full((out_count, wh, ww), nodata, dtype=dtype)

                    for ds, s_b in zip(datasets, src_bounds):
                        # Skip sources that don't overlap this output tile.
                        if (s_b[2] < w_b[0] or s_b[0] > w_b[2]
                                or s_b[3] < w_b[1] or s_b[1] > w_b[3]):
                            continue
                        native = np.full((native_count, wh, ww), nodata,
                                         dtype=dtype)
                        reproject(
                            source=rasterio.band(
                                ds, tuple(range(1, native_count + 1))),
                            destination=native,
                            src_transform=ds.transform, src_crs=ds.crs,
                            dst_transform=block_transform, dst_crs=target,
                            src_nodata=ds.nodata, dst_nodata=nodata,
                            resampling=resampling, num_threads=num_threads,
                        )
                        conv = _convert_block(
                            native, native_count, color_mode, colormap)
                        # Composite (later sources win where they have data).
                        valid = (conv != nodata).any(axis=0)
                        if valid.any():
                            acc[:, valid] = conv[:, valid]

                    dst.write(acc, window=win)
                    report(f"Mosaicking tile {bi + 1}/{total}...",
                           0.12 + 0.80 * (bi + 1) / total)

                dst.colorinterp = colorinterp
                if colormap:
                    dst.write_colormap(1, colormap)
                if overviews:
                    report("Building overviews...", 0.96)
                    dst.build_overviews(list(overviews), resampling)
                    dst.update_tags(ns="rio_overview",
                                    resampling=resampling.name)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, out_path)
        report("Done", 1.0)
        return {"width": width, "height": height, "count": out_count,
                "path": str(out_path)}
    finally:
        for ds in datasets:
            try:
                ds.close()
            except Exception:
                pass


class MosaicWorker(QObject):
    """Runs a single mosaic build off the UI thread."""

    progress = pyqtSignal(str, int)     # (message, percent 0-100)
    finished = pyqtSignal(object, str)  # (result dict or None, error message)

    def __init__(self, sources, out_path, options):
        """Store the source list, output path and build options."""
        super().__init__()
        self._sources = sources
        self._out_path = out_path
        self._options = options
        self._cancelled = False

    def cancel(self):
        """Request cancellation (checked between build stages)."""
        self._cancelled = True

    def process(self):
        """Run the mosaic build and emit the result or an error."""
        try:
            result = build_mosaic(
                self._sources, self._out_path,
                progress_cb=lambda m, f: self.progress.emit(m, int(f * 100)),
                cancel_check=lambda: self._cancelled,
                **self._options,
            )
            self.finished.emit(result, "")
        except _Cancelled:
            self.finished.emit(None, "cancelled")
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            self.finished.emit(None, str(e))


class MosaicExportDialog(QDialog):
    """Setup dialog for the mosaic export."""

    def __init__(self, geo_infos, default_epsg=3857, parent=None):
        """Build the dialog.

        Args:
            geo_infos: list of layer info dicts (georeferenced layers only),
                each with ``file_path`` and ``visible``.
            default_epsg: EPSG code to preselect for the output CRS.
        """
        super().__init__(parent)
        self._infos = geo_infos
        self._visible_count = sum(1 for i in geo_infos if i.get("visible"))
        self.setWindowTitle("Mosaic Export")
        self.setMinimumWidth(520)
        self._build_ui(default_epsg)

    def _build_ui(self, default_epsg):
        """Assemble the dialog widgets."""
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Combine the loaded GeoTIFFs into a single mosaic. All inputs are "
            "reprojected to the output CRS. The output is written tile-by-tile, "
            "so even very large mosaics use little memory."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Scope: all vs visible-only
        scope_box = QGroupBox("Layers to mosaic")
        scope_layout = QVBoxLayout(scope_box)
        self.scope_all = QRadioButton(
            f"All georeferenced layers ({len(self._infos)})")
        self.scope_visible = QRadioButton(
            f"Only toggled-on layers ({self._visible_count})")
        self.scope_all.setChecked(True)
        if self._visible_count == 0:
            self.scope_visible.setEnabled(False)
        self._scope_group = QButtonGroup(self)
        self._scope_group.addButton(self.scope_all)
        self._scope_group.addButton(self.scope_visible)
        scope_layout.addWidget(self.scope_all)
        scope_layout.addWidget(self.scope_visible)
        layout.addWidget(scope_box)

        # Options form
        opts = QGroupBox("Options")
        form = QFormLayout(opts)

        self.crs_combo = QComboBox()
        self.crs_combo.setEditable(True)
        common = [f"EPSG:{default_epsg}", "EPSG:3857", "EPSG:4326"]
        seen = []
        for c in common:
            if c not in seen:
                seen.append(c)
        self.crs_combo.addItems(seen)
        form.addRow("Output CRS:", self.crs_combo)

        self.color_combo = QComboBox()
        self.color_combo.addItems(COLOR_MODES)
        self.color_combo.currentTextChanged.connect(self._on_color_changed)
        form.addRow("Colour mode:", self.color_combo)

        self.nodata_edit = QLineEdit("0")
        form.addRow("NoData value:", self.nodata_edit)

        self.resampling_combo = QComboBox()
        self.resampling_combo.addItems(list(RESAMPLING_CHOICES.keys()))
        form.addRow("Resampling:", self.resampling_combo)

        self.compress_combo = QComboBox()
        self.compress_combo.addItems(list(COMPRESSION_CHOICES.keys()))
        form.addRow("Compression:", self.compress_combo)

        self.overview_edit = QLineEdit(
            ", ".join(str(f) for f in DEFAULT_OVERVIEWS))
        form.addRow("Pyramid levels:", self.overview_edit)

        self.block_combo = QComboBox()
        self.block_combo.addItems(["256", "512"])
        self.block_combo.setCurrentText("512")
        form.addRow("Tile block size:", self.block_combo)
        layout.addWidget(opts)

        # Output file
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output file:"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("mosaic.tif")
        self.out_edit.textChanged.connect(self._update_ok_enabled)
        out_row.addWidget(self.out_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_file)
        out_row.addWidget(browse)
        layout.addLayout(out_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Create Mosaic")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_ok_enabled()

    def _on_color_changed(self, mode):
        """Default resampling to Nearest for palette/categorical output."""
        if mode == "Palette":
            self.resampling_combo.setCurrentText("Nearest")

    def _choose_file(self):
        """Open a save-file picker for the output GeoTIFF."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Mosaic As", self.out_edit.text() or "mosaic.tif",
            "GeoTIFF (*.tif *.tiff)")
        if path:
            if not path.lower().endswith((".tif", ".tiff")):
                path += ".tif"
            self.out_edit.setText(path)

    def _update_ok_enabled(self):
        """Enable Create only once an output file is chosen."""
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            bool(self.out_edit.text().strip()))

    def _parse_overviews(self):
        """Parse the pyramid-levels field into a sorted list of ints (>1)."""
        import re
        factors = []
        for tok in re.split(r"[,\s]+", self.overview_edit.text().strip()):
            try:
                v = int(tok)
            except ValueError:
                continue
            if v > 1:
                factors.append(v)
        return sorted(set(factors))

    def selected_sources(self):
        """Return the source file paths for the chosen scope."""
        if self.scope_visible.isChecked():
            return [i["file_path"] for i in self._infos if i.get("visible")]
        return [i["file_path"] for i in self._infos]

    def output_path(self):
        """Return the chosen output file path."""
        return self.out_edit.text().strip()

    def build_options(self) -> dict:
        """Return the keyword options for :func:`build_mosaic`."""
        nodata_text = self.nodata_edit.text().strip()
        try:
            nodata = float(nodata_text)
            if nodata.is_integer():
                nodata = int(nodata)
        except ValueError:
            nodata = 0
        return {
            "target_crs": self.crs_combo.currentText().strip(),
            "color_mode": self.color_combo.currentText(),
            "nodata": nodata,
            "resampling": RESAMPLING_CHOICES[self.resampling_combo.currentText()],
            "compress": COMPRESSION_CHOICES[self.compress_combo.currentText()],
            "blocksize": int(self.block_combo.currentText()),
            "overviews": self._parse_overviews(),
        }
