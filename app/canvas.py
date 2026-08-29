"""Map canvas for displaying GeoTIFF images with tiled rendering."""
import math
import traceback
from enum import Enum, auto
from pathlib import Path

import numpy as np
import rasterio
from collections import deque

from PyQt5 import sip
from PyQt5.QtCore import (Qt, pyqtSignal, QRectF, QLineF, QPointF, QTimer,
                          QThread, QObject, QThreadPool, QRunnable)

from PyQt5.QtGui import (
    QImage,
    QPixmap,
    QWheelEvent,
    QTransform,
    QPen,
    QBrush,
    QColor,
    QFont,
    QCursor,
    QPainter,
    QPainterPath)
from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsTextItem,
    QGraphicsPathItem, QGraphicsRectItem, QMenu, QWidget
)
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.warp import calculate_default_transform, reproject, Resampling

from .labels import haversine_distance
from .debug_log import debug
from .tile_reader import (TILE_SIZE as DETAIL_TILE_SIZE, level_grid_for,
                          read_tile, tile_bounds, tile_span, tiles_for_bounds)


# Web Mercator CRS
WEB_MERCATOR = CRS.from_epsg(3857)
TILE_SIZE = 512  # Pixels per tile

# Pixel zone: non-georeferenced images are placed beyond valid Web Mercator bounds.
# Scene units are scaled so pixel images have similar visual size to typical geo images.
PIXEL_ZONE_ORIGIN_X = 25_000_000.0  # Well beyond WEB_MERCATOR_MAX (~20M)
PIXEL_ZONE_ORIGIN_Y = 0.0
PIXEL_ZONE_SCALE = 50.0  # Scene units per pixel (makes images ~similar size to geo layers)
PIXEL_ZONE_GROUP_GAP = 5000.0  # Gap between group columns in scene units

# Ceiling on the pixels one reprojected pyramid level may occupy.
#
# A layer is held in memory as a single RGBA array covering the WHOLE image at
# the displayed level (4 bytes/px), and reprojection needs float32 source and
# destination bands on top, so peak use is roughly 12 bytes per pixel. Memory
# therefore scales with the image, not with the screen - which is why a very
# large mosaic used to take the process with it: at full resolution a
# 100k x 100k source asks for ~37 GB of RGBA and over 100 GB at peak.
#
# Levels finer than this cap are refused, so such an image stays viewable
# (just no sharper than the cap allows) instead of crashing. 150 MP is ~600 MB
# of RGBA and ~1.8 GB peak, and leaves ordinary imagery - including a
# 10000 x 10000 tile at 100 MP - loading at full resolution as before.
#
# The real fix is to read only the visible window per tile, which would make
# memory independent of image size and retire this cap.
MAX_LEVEL_PIXELS = 150_000_000

# Ceiling for the whole-image array of a layer whose detail comes from windowed
# tiles. That array is then only a backdrop - it fills the gaps until tiles
# arrive and behind anything they don't cover - so it wants to be cheap and
# quick rather than detailed. Chasing the zoom with it would reintroduce the
# very cost the tiles exist to avoid.
BACKDROP_MAX_PIXELS = 4_000_000

# Ceiling on the pixels held for images that are loaded but not on screen -
# the cycle's neighbours and the images just stepped off. ~256 MB of RGBA.
WARM_MAX_PIXELS = 64_000_000


class LoadCancelled(Exception):
    """A background load noticed its cancel flag part-way through.

    The read/reproject of a large image runs for seconds; before this, a
    superseded load could only bail before it started or after it finished,
    so a burst of zooming kept workers busy producing arrays nobody wanted
    while the level the user settled on waited for a free thread.
    """


def _as_uint8(band):
    """The band as uint8, copying only when the dtype actually differs."""
    if band.dtype == np.uint8:
        return band
    return np.clip(band, 0, 255).astype(np.uint8)

# Waterfall mode: a bottom-level group's images are stacked vertically in the
# pixel zone (raw pixels, no reprojection) so the view can glide through them
# like a filmstrip. Vertical gap between stacked images, in scene units.
WATERFALL_GAP = 2000.0
# Hold-to-glide navigation: while Space (up) / Ctrl+Space (down) is held, the
# view scrolls this many view pixels every timer tick.
WATERFALL_GLIDE_INTERVAL_MS = 16   # ~60 fps
WATERFALL_GLIDE_PX = 8             # view pixels per tick (~480 px/s)
# Prefetch/retention margin while gliding: layers and tiles within this many
# viewport heights above/below the view are loaded ahead of arrival and kept
# resident after leaving, so gliding (and reversing) shows no pop-in gaps.
WATERFALL_PREFETCH_VIEWPORTS = 1.5


class CanvasMode(Enum):
    """Canvas interaction modes."""
    PAN = auto()      # Default pan/zoom mode
    LABEL = auto()    # Point labeling mode
    CYCLE = auto()    # Cycle through layers in a group
    VIEW_CYCLE = auto()  # Cycle through layers visible in current view
    RULER = auto()    # Measure ground distance by dragging
    WATERFALL = auto()  # Group's images stacked vertically; hold Space to glide


# Cycle-style modes: left click labels, right-drag pans, wheel zooms. WATERFALL
# shares these interactions but navigates by gliding rather than stepping.
CYCLE_MODES = (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE, CanvasMode.WATERFALL)

# Modes that step through layers one at a time (Space advances one layer).
STEP_CYCLE_MODES = (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE)

# Modes where a left click places a label.
LABELING_MODES = (CanvasMode.LABEL,) + CYCLE_MODES


class MeasureStage(Enum):
    """Which measurement line the user is currently drawing."""
    LENGTH = auto()   # First line drawn -> label.length_m
    WIDTH = auto()    # Second line drawn -> label.width_m


_crosshair = None


def _crosshair_cursor() -> QCursor:
    """A crosshair cursor that stays visible on any background.

    ``Qt.CrossCursor`` is a bare black cross on Windows, which all but
    vanishes against the dark canvas outside the loaded imagery; this one
    draws a white underlay beneath the black cross so it carries its own
    contrast. Built once, on first use (a QPixmap needs the QApplication).
    """
    global _crosshair
    if _crosshair is None:
        size = 25  # odd, so the hotspot is an exact pixel centre
        centre = size // 2
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(centre, 0, centre, size - 1)
        painter.drawLine(0, centre, size - 1, centre)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawLine(centre, 0, centre, size - 1)
        painter.drawLine(0, centre, size - 1, centre)
        painter.end()
        _crosshair = QCursor(pixmap, centre, centre)
    return _crosshair


class TiledLayer:
    """Manages tiled rendering for a single raster layer.

    Supports lazy loading - only loads bounds quickly, full raster data is loaded
    on demand when the layer becomes visible.
    """

    def __init__(self, file_path: str, lazy: bool = False,
                 geo: bool = True, metadata: dict | None = None):
        """Initialize a tiled layer.

        Args:
            file_path: Path to the GeoTIFF file
            lazy: If True, only load bounds initially, defer full data loading
            geo: If True (default), reproject to Web Mercator. If False, use raw pixel coordinates.
            metadata: Prefetched header data (an AsyncFileLoader layer_data
                dict). When given with lazy=True the constructor does not
                touch the file at all - the directory import already opened
                every file off-thread and computed exactly these values, and
                re-opening each one here ran on the UI thread, stuttering the
                window for the whole import on network shares.
        """
        self.file_path = file_path
        self.name = Path(file_path).stem  # File name without extension
        self.group_path = ""  # Group hierarchy (e.g., "folder/subfolder")
        self.visible = True
        self.bounds = None  # (west, south, east, north) in Web Mercator or pixel coords
        self.tiles: dict[tuple[int, int], QGraphicsPixmapItem] = {}
        # Full-resolution detail read a tile at a time, drawn over the coarse
        # whole-image tiles above. Keyed by (level, tx, ty); only populated for
        # images too large to hold whole (see MapCanvas._uses_detail_tiles).
        self.detail_tiles: dict[tuple[int, int, int], QGraphicsPixmapItem] = {}
        # The level the canvas last asked detail tiles for; a queued tile
        # arriving at any other level is stale and dropped.
        self._detail_level: int | None = None
        # Destination grid per level, so tile geometry needs no file access.
        self._level_grid_cache: dict[int, tuple] = {}
        self.z_value = 0
        self.geo = geo  # Whether this is a georeferenced layer

        # Original image info for coordinate transforms
        self._src_crs = None  # Original CRS
        self._src_transform = None  # Original geotransform
        self._src_width = 0
        self._src_height = 0

        # Cached pyproj transformer (WGS84 -> native CRS). Built lazily and
        # reused across calls to avoid the per-call cost of constructing a
        # transformer inside rasterio.warp.transform.
        self._wgs84_to_native_transformer: Transformer | None = None
        self._wgs84_to_native_crs = None
        # Cached transformer for the reverse direction (native CRS -> WGS84),
        # used to map a clicked pixel back to lat/lon in waterfall mode.
        self._native_to_wgs84_transformer: Transformer | None = None
        self._native_to_wgs84_crs = None

        # Image data (kept in memory after reprojection)
        self._rgba_data: np.ndarray | None = None
        self._width = 0
        self._height = 0

        # Tile grid info
        self._n_tiles_x = 0
        self._n_tiles_y = 0

        # Pyramid / overview info (populated when the source file is opened).
        # `_overviews` holds decimation factors from src.overviews(1)
        # (e.g. [2, 4, 8, 16, 32, 64]); empty when the file has no pyramids.
        # `_src_level_dims` holds the (width, height) of each overview level.
        self._overviews: list[int] = []
        self._src_level_dims: list[tuple[int, int]] = []
        # Full-resolution reprojected dimensions, kept stable across level
        # switches so overview selection always compares against native res.
        self._full_width = 0
        self._full_height = 0
        # Overview decimation factor of the data currently in `_rgba_data`.
        self._loaded_level = 1
        # Level-of-detail scheduling: the level the view currently wants, and
        # the level (if any) being loaded in a background thread.
        self._target_level = 1
        self._loading_level: int | None = None
        # The in-flight background load runnable for this layer (if any), so it
        # can be cancelled when superseded by a newer zoom or culled from view.
        self._pending_runnable = None


        # Lazy loading state
        self._lazy = lazy
        self._fully_loaded = False

        if lazy and self._apply_prefetched_metadata(metadata):
            return
        if geo:
            if lazy:
                self._load_bounds_only()
            else:
                self._load_and_reproject()
                self._fully_loaded = True
        else:
            if lazy:
                self._load_pixel_bounds_only()
            else:
                self._load_pixel_data()
                self._fully_loaded = True

    def _apply_prefetched_metadata(self, metadata: dict | None) -> bool:
        """Populate lazy-load state from an off-thread header read.

        Returns False when the metadata is missing or incomplete, in which
        case the caller falls back to opening the file - correctness first,
        the optimisation only when everything needed is actually there.
        """
        if not metadata:
            return False
        needed = ("src_width", "src_height", "width", "height", "bounds")
        if any(metadata.get(key) in (None, 0) for key in needed):
            return False
        if self.geo and metadata.get("src_crs") is None:
            return False   # the geo path requires a CRS; let the opener raise

        self._src_crs = metadata.get("src_crs")
        self._src_transform = metadata.get("src_transform")
        self._src_width = int(metadata["src_width"])
        self._src_height = int(metadata["src_height"])
        factors = list(metadata.get("overviews") or [])
        self._overviews = factors
        self._src_level_dims = [
            (max(1, self._src_width // f), max(1, self._src_height // f))
            for f in factors]

        width, height = int(metadata["width"]), int(metadata["height"])
        self._full_width = width
        self._full_height = height
        self._width = width
        self._height = height
        self._n_tiles_x = math.ceil(width / TILE_SIZE)
        self._n_tiles_y = math.ceil(height / TILE_SIZE)
        if self.geo:
            self.bounds = tuple(metadata["bounds"])
        # Non-geo bounds are assigned by the pixel-zone layout, exactly as
        # after _load_pixel_bounds_only.
        return True

    def _load_bounds_only(self):
        """Load only the bounds and metadata, not the full raster data.

        This is much faster than full loading and sufficient for:
        - Determining layer extents
        - Showing the layer in the tree
        - Zoom-to-layer calculations
        """
        with rasterio.open(self.file_path) as src:
            # Store original image info
            self._src_crs = src.crs
            self._src_transform = src.transform
            self._src_width = src.width
            self._src_height = src.height
            self._read_overview_metadata(src)

            if src.crs is None:
                raise ValueError(
                    f"No CRS found in '{self.file_path}'. "
                    "The file may not be a valid GeoTIFF."
                )

            # Calculate bounds in Web Mercator without loading pixel data
            dst_crs = WEB_MERCATOR
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )

            self._full_width = width
            self._full_height = height
            self._width = width
            self._height = height

            # Store bounds in Web Mercator
            self.bounds = rasterio.transform.array_bounds(
                height, width, transform)
            west, south, east, north = self.bounds

            # Calculate tile grid
            self._n_tiles_x = math.ceil(width / TILE_SIZE)
            self._n_tiles_y = math.ceil(height / TILE_SIZE)

    def ensure_loaded(self, level: int | None = None, cancel_check=None):
        """Ensure raster data is loaded, optionally at a specific overview level.

        Args:
            level: Overview decimation factor to load. When ``None`` the
                currently loaded level is kept (or full resolution on first
                load). Reloads only when the data is missing or the requested
                level differs from what is loaded.
            cancel_check: Optional callable polled between the expensive
                stages of the load; returning True raises LoadCancelled,
                abandoning the partial work. Background runnables pass their
                cancel flag so a superseded load frees its worker in a
                band's time instead of running to completion.
        """
        target = self._loaded_level if level is None else max(1, level)
        if not self._fully_loaded or self._loaded_level != target:
            if self.geo:
                self._load_and_reproject(target, cancel_check=cancel_check)
            else:
                self._load_pixel_data(target, cancel_check=cancel_check)
            self._fully_loaded = True

    def is_fully_loaded(self) -> bool:
        """Check if full raster data has been loaded."""
        return self._fully_loaded

    def has_overviews(self) -> bool:
        """Return True if the source file exposes pyramid overviews."""
        return bool(self._overviews)

    def select_overview_level(self, scene_units_per_pixel: float) -> int:
        """Return the coarsest overview decimation factor suitable for display.

        Args:
            scene_units_per_pixel: Size of one on-screen pixel in scene units
                (Web Mercator metres for geo layers). Larger = more zoomed out.

        Returns:
            A decimation factor where 1 means full resolution. Always returns 1
            when the file has no overviews or when the view is zoomed in past
            native resolution.
        """
        full_width = self._full_width or self._width
        if not self._overviews or full_width <= 0 or scene_units_per_pixel <= 0:
            return self.budget_level(1)

        # Scene units covered by one full-resolution data pixel.
        west, _south, east, _north = self.bounds
        native_res = (east - west) / full_width
        if native_res <= 0:
            return self.budget_level(1)

        # Pick the largest decimation factor whose level resolution is still no
        # finer than one screen pixel (overviews are sorted ascending).
        best = 1
        for f in self._overviews:
            if native_res * f <= scene_units_per_pixel:
                best = f
            else:
                break
        return self.budget_level(best)

    def resolution_level(self, scene_units_per_pixel: float) -> int:
        """The level the zoom wants, ignoring the whole-image memory cap.

        Detail tiles are bounded by the viewport rather than the image, so they
        can honour the zoom even where holding that level whole never could -
        which is the entire point of reading them windowed.
        """
        full_width = self._full_width or self._width
        if (not self._overviews or full_width <= 0
                or scene_units_per_pixel <= 0 or self.bounds is None):
            return 1
        west, _south, east, _north = self.bounds
        native_res = (east - west) / full_width
        if native_res <= 0:
            return 1
        best = 1
        for factor in self._overviews:
            if native_res * factor <= scene_units_per_pixel:
                best = factor
            else:
                break
        return best

    def detail_grid(self, level: int):
        """Cached ``(transform, width, height)`` of this layer's level grid.

        Needs no file access - the source CRS, transform and size are already
        known - so working out which tiles a view needs is pure arithmetic.
        """
        level = max(1, int(level))
        cached = self._level_grid_cache.get(level)
        if cached is None:
            if self._src_crs is None or self._src_transform is None:
                return None
            cached = level_grid_for(
                self._src_crs, self._src_transform, self._src_width,
                self._src_height, WEB_MERCATOR, level)
            self._level_grid_cache[level] = cached
        return cached

    def budget_level(self, level: int, max_pixels: int | None = None) -> int:
        """Coarsen ``level`` until the whole reprojected array fits in memory.

        The zoom decides how much detail is *wanted*; this decides how much can
        actually be held (see ``MAX_LEVEL_PIXELS``). Returns ``level`` unchanged
        whenever it already fits, which is the case for ordinary imagery - the
        cap only engages on the very large mosaics that would otherwise exhaust
        memory and take the process down.
        """
        limit = MAX_LEVEL_PIXELS if max_pixels is None else max(1, max_pixels)
        level = max(1, level)
        if self.level_pixel_count(level) <= limit:
            return level

        # Prefer a real overview factor: those are cheap to read, since GDAL
        # serves them straight from the pyramid.
        for factor in self._overviews:
            if factor > level and self.level_pixel_count(factor) <= limit:
                return factor

        # The pyramid doesn't go coarse enough (or there isn't one). Decimating
        # by an arbitrary factor still works - a windowed read with out_shape
        # uses the nearest overview and reduces from there - it is just slower.
        factor = level
        while factor < 1 << 20 and self.level_pixel_count(factor) > limit:
            factor *= 2
        return factor

    def level_pixel_count(self, level: int) -> int:
        """Approximate RGBA pixel count of the array at the given level."""
        fw = self._full_width or self._width
        fh = self._full_height or self._height
        level = max(1, level)
        return (fw // level) * (fh // level)

    def coarsest_level(self) -> int:
        """Return the coarsest available overview decimation factor (or 1)."""
        return self._overviews[-1] if self._overviews else 1

    def apply_level_result(self, result: dict) -> None:
        """Apply raster data computed (possibly off-thread) for one level.

        Sets the RGBA buffer, dimensions, tile grid and level metadata. Geo
        layers get their Web Mercator bounds updated; non-geo layers keep their
        existing pixel-zone bounds.
        """
        self._rgba_data = result['rgba']
        self._width = result['width']
        self._height = result['height']
        if result.get('full_width'):
            self._full_width = result['full_width']
        if result.get('full_height'):
            self._full_height = result['full_height']
        if result.get('overviews'):
            self._overviews = result['overviews']
        if result.get('level_dims'):
            self._src_level_dims = result['level_dims']
        self._src_crs = result['src_crs']
        self._src_transform = result['src_transform']
        self._src_width = result['src_width']
        self._src_height = result['src_height']
        self._n_tiles_x = math.ceil(self._width / TILE_SIZE)
        self._n_tiles_y = math.ceil(self._height / TILE_SIZE)
        if self.geo:
            self.bounds = result['bounds']
        self._loaded_level = result['level']
        self._fully_loaded = True

    def _read_overview_metadata(self, src) -> None:
        """Read pyramid/overview metadata from an open rasterio dataset.

        Populates ``self._overviews`` with the decimation factors reported by
        ``src.overviews(1)`` (e.g. ``[2, 4, 8, 16, 32, 64]``) and
        ``self._src_level_dims`` with the (width, height) of each level. Both
        are left empty when the file has no overviews.
        """
        try:
            factors = list(src.overviews(1))
        except Exception:
            factors = []
        self._overviews = factors
        self._src_level_dims = [
            (max(1, src.width // f), max(1, src.height // f)) for f in factors
        ]
        if factors:
            debug(f"pyramid {Path(self.file_path).name}: "
                  f"overviews={factors} level_dims={self._src_level_dims}")

    @staticmethod
    def _checkpoint(cancel_check):
        """Abandon the load here if it has been cancelled meanwhile."""
        if cancel_check is not None and cancel_check():
            raise LoadCancelled()

    def _load_and_reproject(self, level: int = 1, cancel_check=None):
        """Load GeoTIFF and reproject to Web Mercator at the given overview level.

        Args:
            level: Overview decimation factor (1 = full resolution). Source
                pixels are read from the matching pyramid level via a decimated
                ``out_shape`` so the full image is never decoded when zoomed out.
        """
        with rasterio.open(self.file_path) as src:
            # Store original image info for coordinate transforms
            self._src_crs = src.crs
            self._src_transform = src.transform
            self._src_width = src.width
            self._src_height = src.height
            self._read_overview_metadata(src)

            if src.crs is None:
                raise ValueError(
                    f"No CRS found in '{self.file_path}'. "
                    "The file may not be a valid GeoTIFF."
                )

            level = max(1, level)

            dst_crs = WEB_MERCATOR
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )

            # Remember the full-resolution reprojected dimensions (used for
            # overview level selection) before reducing for this level.
            self._full_width = width
            self._full_height = height

            # Hard floor on memory, applied here rather than only where the
            # level is chosen: a non-lazy layer loads straight from __init__ at
            # level 1, so a very large image would otherwise allocate itself
            # whole before any level selection ran. Cheap to do now - the
            # transform above is arithmetic on the bounds, not a read.
            budgeted = self.budget_level(level)
            if budgeted != level:
                debug(f"memory cap: {Path(self.file_path).name} level {level} "
                      f"needs {self.level_pixel_count(level) / 1e6:.0f} MP; "
                      f"loading at 1/{budgeted} instead")
                level = budgeted

            # Decimated source read shape (served from the nearest overview),
            # and the source transform scaled to match that read shape.
            rd_w = max(1, src.width // level)
            rd_h = max(1, src.height // level)
            src_read_transform = src.transform * src.transform.scale(
                src.width / rd_w, src.height / rd_h)

            if level > 1:
                dst_w = max(1, width // level)
                dst_h = max(1, height // level)
                transform, width, height = calculate_default_transform(
                    src.crs, dst_crs, src.width, src.height, *src.bounds,
                    dst_width=dst_w, dst_height=dst_h
                )

            # Optimization: reproject band 1 as float32 to detect nodata/padding,
            # then reproject remaining bands directly as uint8 (faster, less memory).
            # Padding areas are identical for all bands after reprojection.

            # Band 1: reproject as float32 to detect nodata
            self._checkpoint(cancel_check)
            src_band1 = src.read(1, out_shape=(rd_h, rd_w)).astype(np.float32)
            if src.nodata is not None:
                src_band1[src_band1 == src.nodata] = np.nan

            self._checkpoint(cancel_check)
            dst_band1 = np.full((height, width), np.nan, dtype=np.float32)
            reproject(
                source=src_band1,
                destination=dst_band1,
                src_transform=src_read_transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                src_nodata=np.nan,
                dst_nodata=np.nan
            )
            # ~4 bytes/px of source float32 with no further use; at the
            # 150 MP budget that is ~600 MB held across the whole of the
            # rest of this function unless dropped now.
            del src_band1

            # Create nodata mask from band 1 only (padding is same for all
            # bands)
            nodata_mask = np.isnan(dst_band1)

            # Convert band 1 to uint8
            band1_uint8 = np.clip(
                np.nan_to_num(
                    dst_band1,
                    nan=0.0),
                0,
                255).astype(
                np.uint8)
            del dst_band1  # Free memory

            # Reproject remaining bands directly as uint8 (faster)
            if src.count >= 3:
                # RGB image - reproject bands 2 and 3 as uint8
                bands_uint8 = [band1_uint8]
                for i in range(2, min(src.count + 1, 4)
                               ):  # bands 2, 3 (and skip 4 if exists)
                    self._checkpoint(cancel_check)
                    src_band = src.read(i, out_shape=(rd_h, rd_w))
                    # Handle source nodata by setting to 0 (in place - the
                    # read returned a fresh array, and np.where built a whole
                    # extra frame here)
                    if src.nodata is not None:
                        src_band[src_band == src.nodata] = 0
                    if src_band.dtype != np.uint8:
                        src_band = np.clip(src_band, 0, 255).astype(np.uint8)

                    dst_band = np.zeros((height, width), dtype=np.uint8)
                    reproject(
                        source=src_band,
                        destination=dst_band,
                        src_transform=src_read_transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=dst_crs,
                        resampling=Resampling.bilinear,
                        src_nodata=0,
                        dst_nodata=0
                    )
                    bands_uint8.append(dst_band)

                r, g, b = bands_uint8[0], bands_uint8[1], bands_uint8[2]
            else:
                # Grayscale - use band 1 for all RGB channels
                r = g = b = band1_uint8

            # Build RGBA array
            rgba_full = np.zeros((height, width, 4), dtype=np.uint8)
            rgba_full[:, :, 0] = r
            rgba_full[:, :, 1] = g
            rgba_full[:, :, 2] = b
            # Set alpha to 0 for nodata/padded pixels, 255 for valid pixels.
            # Written into the slice directly: np.where(mask, 0, 255) built a
            # full-frame default-int array first - 8 bytes per pixel, 1.2 GB
            # at the 150 MP budget - only to throw it away after one astype.
            rgba_full[:, :, 3] = 255
            rgba_full[:, :, 3][nodata_mask] = 0

            self._rgba_data = rgba_full

            # Store bounds in Web Mercator
            self.bounds = rasterio.transform.array_bounds(
                height, width, transform)
            west, south, east, north = self.bounds

            # Calculate tile grid
            self._width = width
            self._height = height
            self._n_tiles_x = math.ceil(width / TILE_SIZE)
            self._n_tiles_y = math.ceil(height / TILE_SIZE)
            self._loaded_level = level

    def _load_pixel_bounds_only(self):
        """Load only dimensions for a non-georeferenced image (no CRS/reprojection).

        Bounds are set later by the canvas layout manager via set_pixel_bounds().
        """
        with rasterio.open(self.file_path) as src:
            self._src_width = src.width
            self._src_height = src.height
            # Capture georeferencing if present so a clicked pixel can still be
            # mapped to lat/lon while the image is displayed raw (waterfall).
            self._src_crs = src.crs
            self._src_transform = src.transform
            self._read_overview_metadata(src)

            width = src.width
            height = src.height

            self._full_width = width
            self._full_height = height
            self._width = width
            self._height = height
            self._n_tiles_x = math.ceil(width / TILE_SIZE)
            self._n_tiles_y = math.ceil(height / TILE_SIZE)

            # Bounds will be assigned by the pixel zone layout manager
            # Use placeholder bounds at origin; will be overwritten
            self.bounds = (0, 0, width, height)

    def _load_pixel_data(self, level: int = 1, cancel_check=None):
        """Load a non-georeferenced image directly as pixel data (no reprojection).

        Args:
            level: Overview decimation factor (1 = full resolution). Pixels are
                read at a decimated ``out_shape`` served from the matching
                pyramid level.
        """
        # Preserve bounds if already assigned by set_pixel_bounds()
        saved_bounds = self.bounds

        with rasterio.open(self.file_path) as src:
            self._src_width = src.width
            self._src_height = src.height
            # Keep any georeferencing so pixel -> lat/lon works in waterfall mode.
            self._src_crs = src.crs
            self._src_transform = src.transform
            self._read_overview_metadata(src)

            self._full_width = src.width
            self._full_height = src.height

            level = max(1, level)
            # Same memory floor as the georeferenced path: a raw image large
            # enough to exhaust memory is decimated rather than read whole.
            budgeted = self.budget_level(level)
            if budgeted != level:
                debug(f"memory cap: {Path(self.file_path).name} level {level} "
                      f"needs {self.level_pixel_count(level) / 1e6:.0f} MP; "
                      f"loading at 1/{budgeted} instead")
                level = budgeted
            width = max(1, src.width // level)
            height = max(1, src.height // level)

            if src.count >= 3:
                # astype on an already-uint8 read is a full-frame copy for
                # nothing; nearly all supported imagery is uint8.
                self._checkpoint(cancel_check)
                r = _as_uint8(src.read(1, out_shape=(height, width)))
                self._checkpoint(cancel_check)
                g = _as_uint8(src.read(2, out_shape=(height, width)))
                self._checkpoint(cancel_check)
                b = _as_uint8(src.read(3, out_shape=(height, width)))
            else:
                self._checkpoint(cancel_check)
                gray = _as_uint8(src.read(1, out_shape=(height, width)))
                r = g = b = gray

            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            rgba[:, :, 0] = r
            rgba[:, :, 1] = g
            rgba[:, :, 2] = b
            rgba[:, :, 3] = 255

            self._rgba_data = rgba
            self._width = width
            self._height = height
            self._n_tiles_x = math.ceil(width / TILE_SIZE)
            self._n_tiles_y = math.ceil(height / TILE_SIZE)
            self._loaded_level = level

            # Restore bounds if they were already set (by set_pixel_bounds)
            if saved_bounds and saved_bounds != (0, 0, width, height):
                self.bounds = saved_bounds
            else:
                self.bounds = (0, 0, width, height)

    def set_pixel_bounds(self, origin_x: float, origin_y: float):
        """Set the bounds for a non-georeferenced layer at the given origin.

        Places the image so that its top-left corner is at (origin_x, origin_y)
        in scene coordinates, scaled by PIXEL_ZONE_SCALE.
        """
        w = self._width * PIXEL_ZONE_SCALE
        h = self._height * PIXEL_ZONE_SCALE
        self.bounds = (origin_x, origin_y, origin_x + w, origin_y + h)

    def get_tile_bounds(self,
                        tx: int,
                        ty: int) -> tuple[int,
                                          int,
                                          int,
                                          int,
                                          float,
                                          float,
                                          float,
                                          float]:
        """Get pixel and world bounds for a tile.

        Returns (px_left, px_top, px_right, px_bottom, world_west, world_south, world_east, world_north)
        """
        west, south, east, north = self.bounds

        # Pixel bounds
        px_left = tx * TILE_SIZE
        px_top = ty * TILE_SIZE
        px_right = min((tx + 1) * TILE_SIZE, self._width)
        px_bottom = min((ty + 1) * TILE_SIZE, self._height)

        # Calculate world coords per pixel
        world_per_pixel_x = (east - west) / self._width
        world_per_pixel_y = (north - south) / self._height

        # World bounds based on actual pixel bounds
        tile_west = west + px_left * world_per_pixel_x
        tile_east = west + px_right * world_per_pixel_x
        tile_north = north - px_top * world_per_pixel_y
        tile_south = north - px_bottom * world_per_pixel_y

        return px_left, px_top, px_right, px_bottom, tile_west, tile_south, tile_east, tile_north

    def get_visible_tile_indices(
            self, view_bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
        """Get list of tile indices that intersect with the view bounds.

        Args:
            view_bounds: (west, south, east, north) in Web Mercator

        Returns:
            List of (tx, ty) tile indices. Uses O(1) calculation instead of iterating all tiles.
        """
        view_west, view_south, view_east, view_north = view_bounds
        layer_west, layer_south, layer_east, layer_north = self.bounds

        # Check if view intersects layer at all
        if (view_east < layer_west or view_west > layer_east or
                view_north < layer_south or view_south > layer_north):
            return []

        # Clamp view bounds to layer bounds
        clamped_west = max(view_west, layer_west)
        clamped_east = min(view_east, layer_east)
        clamped_south = max(view_south, layer_south)
        clamped_north = min(view_north, layer_north)

        # Convert world coordinates to pixel coordinates first, then derive
        # tile indices.  This is consistent with get_tile_bounds() which
        # computes tile world extents from pixel bounds.  Using the old
        # _tile_world_width/_tile_world_height (uniform world-space division)
        # caused mismatches for edge tiles whose pixel count is smaller than
        # TILE_SIZE.
        world_per_pixel_x = (layer_east - layer_west) / self._width
        world_per_pixel_y = (layer_north - layer_south) / self._height

        # Pixel coordinates corresponding to clamped view edges
        px_left = (clamped_west - layer_west) / world_per_pixel_x
        px_right = (clamped_east - layer_west) / world_per_pixel_x
        px_top = (layer_north - clamped_north) / world_per_pixel_y
        px_bottom = (layer_north - clamped_south) / world_per_pixel_y

        # Tile indices from pixel coordinates
        tx_min = max(0, int(px_left / TILE_SIZE))
        tx_max = min(self._n_tiles_x - 1, int(px_right / TILE_SIZE))
        ty_min = max(0, int(px_top / TILE_SIZE))
        ty_max = min(self._n_tiles_y - 1, int(px_bottom / TILE_SIZE))

        return [(tx, ty) for ty in range(ty_min, ty_max + 1)
                for tx in range(tx_min, tx_max + 1)]

    def create_tile_pixmap(self, tx: int, ty: int) -> QPixmap | None:
        """Create a QPixmap for a specific tile.

        Returns None if pixel data isn't loaded yet: loading is done off the UI
        thread by the canvas LOD scheduler, and tiles are (re)built once the
        data has been applied, so this must never block on a load.
        """
        if self._rgba_data is None:
            return None

        px_left, px_top, px_right, px_bottom, _, _, _, _ = self.get_tile_bounds(
            tx, ty)

        height = px_bottom - px_top
        width = px_right - px_left

        if height == 0 or width == 0:
            return None

        # Build a QImage that points directly at the slice within _rgba_data
        # without copying. The slice is non-contiguous (its rows are spaced by
        # the parent array's full row stride), so we tell QImage the actual
        # row stride via `bytesPerLine`. The parent buffer has enough bytes;
        # numpy's slice memoryview reports a smaller nbytes and would be
        # rejected, so we wrap the raw address with sip.voidptr instead.
        # QPixmap.fromImage() immediately deep-copies into Qt's native pixmap
        # format, so the numpy buffer only needs to outlive that single call
        # (self._rgba_data does).
        tile_view = self._rgba_data[px_top:px_bottom, px_left:px_right]
        bytes_per_line = self._rgba_data.strides[0]
        ptr = sip.voidptr(tile_view.ctypes.data)
        image = QImage(
            ptr,
            width,
            height,
            bytes_per_line,
            QImage.Format_RGBA8888
        )
        return QPixmap.fromImage(image)

    def set_visibility(self, visible: bool):
        """Set visibility for all tiles."""
        self.visible = visible
        for item in self.tiles.values():
            item.setVisible(visible)
        for item in self.detail_tiles.values():
            item.setVisible(visible)

    def set_z_value(self, z: float):
        """Set z-value for all tiles, keeping detail above the coarse ones."""
        self.z_value = z
        for item in self.tiles.values():
            item.setZValue(z)
        for item in self.detail_tiles.values():
            item.setZValue(z + 0.5)

    def free_data(self, scene: QGraphicsScene | None = None):
        """Release pixel data from memory, keeping bounds and metadata.

        Removes rendered tiles from the scene and frees the RGBA array.
        The layer can be reloaded later via ensure_loaded().
        """
        if scene is not None:
            for item in self.tiles.values():
                scene.removeItem(item)
            self.tiles.clear()
            for item in self.detail_tiles.values():
                scene.removeItem(item)
            self.detail_tiles.clear()
        self._rgba_data = None
        self._fully_loaded = False

    def remove_from_scene(self, scene: QGraphicsScene):
        """Remove all tiles from the scene."""
        for item in self.tiles.values():
            scene.removeItem(item)
        self.tiles.clear()
        for item in self.detail_tiles.values():
            scene.removeItem(item)
        self.detail_tiles.clear()

    def contains_point(self, easting: float, northing: float) -> bool:
        """Check if a point (in Web Mercator) is within this layer's bounds."""
        if self.bounds is None:
            return False
        west, south, east, north = self.bounds
        return west <= easting <= east and south <= northing <= north

    def get_center(self) -> tuple[float, float]:
        """Get the center point of this layer in Web Mercator coordinates."""
        if self.bounds is None:
            return (0, 0)
        west, south, east, north = self.bounds
        return ((west + east) / 2, (south + north) / 2)

    def distance_to_center(self, easting: float, northing: float) -> float:
        """Calculate distance from a point to this layer's center."""
        cx, cy = self.get_center()
        return math.sqrt((easting - cx) ** 2 + (northing - cy) ** 2)

    def _get_wgs84_to_native_transformer(self) -> Transformer:
        """Return a cached WGS84 -> native CRS transformer, building it on first use.

        Cached for the lifetime of the layer (rebuilt only if the source CRS
        changes, which should not happen after load).
        """
        if (self._wgs84_to_native_transformer is None
                or self._wgs84_to_native_crs is not self._src_crs):
            # always_xy=True makes input/output (lon, lat) / (x, y) consistent
            self._wgs84_to_native_transformer = Transformer.from_crs(
                4326, self._src_crs, always_xy=True
            )
            self._wgs84_to_native_crs = self._src_crs
        return self._wgs84_to_native_transformer

    def latlon_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        """Convert WGS84 lat/lon to pixel coordinates in the original image.

        Args:
            lon: Longitude in degrees (WGS84)
            lat: Latitude in degrees (WGS84)

        Returns:
            Tuple of (pixel_x, pixel_y) where pixel_x is column and pixel_y is row.
            Values are floats for sub-pixel precision.
        """
        # Transform from WGS84 to the image's native CRS using a cached transformer
        transformer = self._get_wgs84_to_native_transformer()
        x_native, y_native = transformer.transform(lon, lat)

        # Use inverse of geotransform to get pixel coordinates
        # ~transform gives the inverse transform
        col, row = ~self._src_transform * (x_native, y_native)

        return (col, row)

    def _get_native_to_wgs84_transformer(self) -> Transformer:
        """Cached transformer from the source CRS to WGS84 (lon/lat)."""
        if (self._native_to_wgs84_transformer is None
                or self._native_to_wgs84_crs is not self._src_crs):
            self._native_to_wgs84_transformer = Transformer.from_crs(
                self._src_crs, 4326, always_xy=True)
            self._native_to_wgs84_crs = self._src_crs
        return self._native_to_wgs84_transformer

    def pixel_to_latlon(self, px: float, py: float) -> "tuple[float, float] | None":
        """Convert a source pixel (col, row) to WGS84 (lon, lat).

        Returns None when the image has no georeferencing (a plain raster), in
        which case a pixel has no meaningful lat/lon.
        """
        if self._src_transform is None or self._src_crs is None:
            return None
        x_native, y_native = self._src_transform * (px, py)
        lon, lat = self._get_native_to_wgs84_transformer().transform(
            x_native, y_native)
        return (lon, lat)

    def scene_to_pixel(self, easting: float, northing: float) -> tuple[float, float]:
        """Convert scene coordinates to pixel coordinates for non-geo layers.

        Scene units are scaled by PIXEL_ZONE_SCALE relative to source pixels.
        Pixel Y=0 is the top of the image (north), increasing downward.
        """
        if self.bounds is None:
            return (0, 0)
        west, _, _, north = self.bounds
        pixel_x = (easting - west) / PIXEL_ZONE_SCALE
        pixel_y = (north - northing) / PIXEL_ZONE_SCALE
        return (pixel_x, pixel_y)


class AsyncFileLoader(QObject):
    """Worker object for loading GeoTIFF files asynchronously in a background thread.

    Emits signals as files are loaded, allowing the UI to update progressively.
    """

    # Emitted when a file is successfully loaded: (file_path, layer_data_dict)
    file_loaded = pyqtSignal(str, dict)

    # Emitted when a file fails to load: (file_path, error_message)
    file_error = pyqtSignal(str, str)

    # Emitted when a batch of files is complete: (loaded_count, error_count)
    batch_complete = pyqtSignal(int, int)

    # Emitted periodically during loading: (files_processed, total_files)
    progress_update = pyqtSignal(int, int)

    def __init__(self):
        """Initialize the loader with an empty file queue and no cancellation."""
        super().__init__()
        # (file_path, group_path)
        self._files_to_load: list[tuple[str, str]] = []
        self._cancelled = False

    def set_files(self, files: list[tuple[str, str]]):
        """Set the list of files to load.

        Args:
            files: List of (file_path, group_path) tuples
        """
        self._files_to_load = files
        self._cancelled = False

    def cancel(self):
        """Cancel the loading operation."""
        self._cancelled = True

    def process(self):
        """Process all files in the queue. Run this in a worker thread."""
        loaded_count = 0
        error_count = 0
        total = len(self._files_to_load)

        for i, (file_path, group_path) in enumerate(self._files_to_load):
            if self._cancelled:
                break

            try:
                with rasterio.open(file_path) as src:
                    src_crs = src.crs
                    src_transform = src.transform
                    src_width = src.width
                    src_height = src.height
                    # The UI thread builds the layer from this dict without
                    # reopening the file, so it needs the pyramid factors too.
                    try:
                        overviews = list(src.overviews(1))
                    except Exception:
                        overviews = []

                    if src.crs is not None:
                        dst_crs = WEB_MERCATOR
                        transform, width, height = calculate_default_transform(
                            src.crs, dst_crs, src.width, src.height, *src.bounds
                        )
                        bounds = rasterio.transform.array_bounds(
                            height, width, transform)
                        geo = True
                    else:
                        width = src.width
                        height = src.height
                        bounds = (0, 0, width, height)
                        geo = False

                # Emit the loaded data
                layer_data = {
                    'file_path': file_path,
                    'group_path': group_path,
                    'bounds': bounds,
                    'width': width,
                    'height': height,
                    'src_crs': src_crs,
                    'src_transform': src_transform,
                    'src_width': src_width,
                    'src_height': src_height,
                    'overviews': overviews,
                    'geo': geo,
                }
                self.file_loaded.emit(file_path, layer_data)
                loaded_count += 1

            except Exception as e:
                self.file_error.emit(file_path, str(e))
                error_count += 1

            # Emit progress every 10 files or at the end
            if (i + 1) % 10 == 0 or i == total - 1:
                self.progress_update.emit(i + 1, total)

        self.batch_complete.emit(loaded_count, error_count)


class AsyncFileLoaderThread(QThread):
    """Thread wrapper for AsyncFileLoader."""

    # Forward signals from the loader
    file_loaded = pyqtSignal(str, dict)
    file_error = pyqtSignal(str, str)
    batch_complete = pyqtSignal(int, int)
    progress_update = pyqtSignal(int, int)

    def __init__(self, parent=None):
        """Create the wrapped AsyncFileLoader and forward its signals."""
        super().__init__(parent)
        self._loader = AsyncFileLoader()

        # Connect internal signals to forwarded signals
        self._loader.file_loaded.connect(self.file_loaded.emit)
        self._loader.file_error.connect(self.file_error.emit)
        self._loader.batch_complete.connect(self.batch_complete.emit)
        self._loader.progress_update.connect(self.progress_update.emit)

    def set_files(self, files: list[tuple[str, str]]):
        """Set files to load."""
        self._loader.set_files(files)

    def cancel(self):
        """Cancel loading."""
        self._loader.cancel()

    def run(self):
        """Run the loading in the background thread."""
        self._loader.process()


class ThrobberWidget(QWidget):
    """A small self-contained spinner overlay shown while imagery is loading.

    Draws a rotating arc with a QTimer (no image assets), so the user knows a
    background load is in progress. Transparent to mouse events so it never
    intercepts map interaction.
    """

    def __init__(self, parent=None):
        """Create a hidden 44x44 spinner attached to *parent*."""
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(44, 44)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)  # ~12 fps rotation
        self._timer.timeout.connect(self._advance)
        self.hide()

    def start(self):
        """Show the spinner and start it animating (idempotent)."""
        if not self._timer.isActive():
            self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        """Stop animating and hide the spinner."""
        self._timer.stop()
        self.hide()

    def _advance(self):
        """Rotate the arc one step and repaint."""
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        """Paint a translucent disc with a rotating white arc."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Translucent background disc for contrast over any imagery.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120))
        painter.drawEllipse(self.rect())
        # Rotating arc (a ~270 degree gap sweeps around).
        arc_rect = self.rect().adjusted(11, 11, -11, -11)
        pen = QPen(QColor(255, 255, 255, 235), 4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(arc_rect, -self._angle * 16, -270 * 16)
        painter.end()


def _emit_safely(signal, *args):
    """Emit a signal, ignoring a receiver that has already been torn down.

    Background jobs can finish after the canvas (or the whole app) has gone,
    leaving the signal's C++ object deleted; that is not an error worth
    reporting.
    """
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


class _TileLoadSignals(QObject):
    """Signals for one background detail-tile read.

    Both carry the emitting runnable, for the same reason the level-load
    signals do: the handlers must not act on a stale event just because its
    (layer_id, level, tx, ty) key matches a newer read's.
    """
    # layer_id, level, tx, ty, rgba array (or None when empty), runnable
    finished = pyqtSignal(str, int, int, int, object, object)
    failed = pyqtSignal(str, int, int, int, object)


class _TileLoadRunnable(QRunnable):
    """Reproject a single detail tile off the UI thread.

    Opens its own dataset handle: GDAL datasets are not safe to share between
    threads, and the open is cheap next to the read itself.
    """

    def __init__(self, layer_id: str, file_path: str, level: int,
                 tx: int, ty: int, signals: "_TileLoadSignals", grid=None):
        """Store the tile identity and the signal group to report through.

        ``grid`` is the layer's cached level grid; every tile of a level
        shares it, and recomputing it per tile cost each read a redundant
        densified CRS transform of the whole image bounds.
        """
        super().__init__()
        self._layer_id = layer_id
        self._file_path = file_path
        self._level = level
        self._tx = tx
        self._ty = ty
        self._signals = signals
        self._grid = grid
        self._cancelled = False

    def cancel(self):
        """Request cancellation; checked before the read and again after."""
        self._cancelled = True

    def run(self):
        """Read and reproject the tile, handing the array to the UI thread.

        The whole body is guarded: an exception escaping a QRunnable dies in
        the worker thread without a trace, and the tile would then stay
        "pending" forever, never retried and never reported.
        """
        try:
            if self._cancelled:
                self._fail()
                return
            with rasterio.open(self._file_path) as src:
                rgba = read_tile(src, WEB_MERCATOR, self._level,
                                 self._tx, self._ty, grid=self._grid)
            if self._cancelled:
                self._fail()
                return
            _emit_safely(self._signals.finished, self._layer_id, self._level,
                         self._tx, self._ty, rgba, self)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            debug(f"tile read FAILED: {Path(self._file_path).name} "
                  f"level={self._level} ({self._tx},{self._ty}): "
                  f"{type(exc).__name__}: {exc}")
            self._fail()

    def _fail(self):
        """Report that this tile produced nothing, so it stops being pending.

        Reaching the signal at all can fail once the canvas has been torn
        down, which _emit_safely cannot guard against on its own.
        """
        try:
            _emit_safely(self._signals.failed, self._layer_id, self._level,
                         self._tx, self._ty, self)
        except RuntimeError:
            pass


class _LevelLoadSignals(QObject):
    """Signals for a background overview-level load.

    Every signal carries the runnable that emitted it, so the UI-thread
    handlers can tell a live load from a stale one by identity. Matching on
    (layer_id, level) is not enough: a cancelled load's queued event can
    arrive after a NEW load of the same level was dispatched, and would then
    clear the new load's tracking - orphaning it - or resurrect data the
    user just freed.
    """
    finished = pyqtSignal(str, int, object, object)  # +result, runnable
    error = pyqtSignal(str, int, str, object)        # +message, runnable
    cancelled = pyqtSignal(str, int, object)         # +runnable


class _LevelLoadRunnable(QRunnable):
    """Compute a layer's RGBA data at a given overview level off the UI thread.

    Uses a throwaway TiledLayer so no state is shared with the live layer; the
    finished numpy array and metadata are handed back to the main thread via a
    queued signal for application there.
    """

    def __init__(self, layer_id: str, file_path: str, geo: bool, level: int,
                 signals: "_LevelLoadSignals"):
        """Store the layer identity, level and signal group for the load job."""
        super().__init__()
        self._layer_id = layer_id
        self._file_path = file_path
        self._geo = geo
        self._level = level
        self._signals = signals
        self._cancelled = False

    def cancel(self):
        """Request cancellation. Checked before (and after) the expensive load;
        a still-queued runnable then bails without touching the disk. Safe to
        call from the UI thread (a plain bool flag under the GIL)."""
        self._cancelled = True

    def run(self):
        """Compute the layer's RGBA data for the level off the UI thread and
        emit the result (or an error/cancellation) back to the main thread.

        The whole body is guarded: an exception escaping a QRunnable dies in
        the worker thread with no traceback, and the load counter would never
        be balanced - leaving the spinner turning for a load that already gave
        up.
        """
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            try:
                self._safe_emit(self._signals.error, self._layer_id,
                                self._level, str(exc), self)
            except RuntimeError:
                # The canvas went away mid-load, taking the signal object with
                # it. There is nobody to notify and nothing has gone wrong.
                return
            debug(f"level load FAILED: {self._layer_id} level={self._level}: "
                  f"{type(exc).__name__}: {exc}")

    def _run(self):
        """Do the load and emit exactly one terminal signal."""
        # Bail cheaply if superseded/culled while still queued.
        if self._cancelled:
            self._safe_emit(self._signals.cancelled, self._layer_id,
                            self._level, self)
            return
        try:
            tmp = TiledLayer(self._file_path, lazy=True, geo=self._geo)
            # The flag is polled between the load's expensive stages, so a
            # superseded zoom frees this worker within one band's read
            # instead of holding it for the whole reprojection.
            tmp.ensure_loaded(level=self._level,
                              cancel_check=lambda: self._cancelled)
            result = {
                'rgba': tmp._rgba_data,
                'width': tmp._width,
                'height': tmp._height,
                'bounds': tmp.bounds,
                'full_width': tmp._full_width,
                'full_height': tmp._full_height,
                'overviews': tmp._overviews,
                'level_dims': tmp._src_level_dims,
                'src_crs': tmp._src_crs,
                'src_transform': tmp._src_transform,
                'src_width': tmp._src_width,
                'src_height': tmp._src_height,
                'level': tmp._loaded_level,
            }
        except LoadCancelled:
            self._safe_emit(self._signals.cancelled, self._layer_id,
                            self._level, self)
            return
        except Exception as e:  # report any load failure back to the UI thread
            self._safe_emit(
                self._signals.error, self._layer_id, self._level, str(e), self)
            return
        # Discard the result if the view moved on while we were reprojecting.
        if self._cancelled:
            self._safe_emit(self._signals.cancelled, self._layer_id,
                            self._level, self)
            return
        self._safe_emit(self._signals.finished, self._layer_id, self._level,
                        result, self)

    @staticmethod
    def _safe_emit(signal, *args):
        """Emit a signal, ignoring a receiver that was already torn down."""
        _emit_safely(signal, *args)


class MapCanvas(QGraphicsView):
    """Canvas widget for displaying geospatial raster layers with tiling."""

    # Signal emitted when mouse moves: (longitude, latitude, layer_name,
    # group_path)
    coordinates_changed = pyqtSignal(float, float, str, str, bool)  # x, y, layer, group, is_pixel

    # Signal emitted when a label is placed: (pixel_x, pixel_y, lon, lat,
    # image_name, image_group, image_path)
    label_placed = pyqtSignal(float, float, float, float, str, str, str)

    # Signal emitted when a label is removed: (label_id, image_path)
    label_removed = pyqtSignal(int, str)

    # Signal emitted when two labels are linked: (label_id1, label_id2)
    labels_linked = pyqtSignal(int, int)

    # Signal emitted when a label is unlinked: (label_id)
    label_unlinked = pyqtSignal(int)

    # A label's description should be edited: (label_id). The text itself is
    # not carried - main_window owns the project and prompts for it.
    label_describe_requested = pyqtSignal(int)

    # The shared group name of a label's linked group should be edited:
    # (label_id). Same ownership split as the description.
    label_group_id_requested = pyqtSignal(int)

    # Signal emitted when user wants to highlight linked labels: (label_id)
    show_linked_requested = pyqtSignal(int)

    # Waypoint requests raised from the canvas, handled by the main window
    # (which owns the project). Add carries a WGS84 (lon, lat); the rest carry
    # a waypoint id.
    waypoint_add_requested = pyqtSignal(float, float)

    # The image under the cursor should flip its hard-negative-source flag:
    # (layer_id). The canvas only shows the toggle; the project owns the flag,
    # so main_window flips it and syncs the checked state back down.
    hard_negative_toggle_requested = pyqtSignal(str)

    # The topmost image under the cursor should be hidden ("Unselect layer"):
    # (layer_id). Routed through the layer panel's uncheck path so every
    # checkbox stays in sync, same as "Unselect layers outside view".
    layer_unselect_requested = pyqtSignal(str)
    waypoint_goto_requested = pyqtSignal(int)
    waypoint_rename_requested = pyqtSignal(int)
    waypoint_remove_requested = pyqtSignal(int)

    # Signal emitted when link mode state changes: (is_active, message)
    link_mode_changed = pyqtSignal(bool, str)

    # Signal emitted when chain-link mode state changes: (is_active, message).
    # Lets the main window sync the toolbar toggle and the status bar.
    chain_link_changed = pyqtSignal(bool, str)

    # Signal emitted when a label's length/width has been measured:
    # (label_id, length_m, width_m). Values are floats in metres; `object`
    # payloads allow None (e.g. when clearing measurements later).
    label_measured = pyqtSignal(int, object, object)

    # Signal emitted when measure mode state changes: (is_active, message)
    measure_mode_changed = pyqtSignal(bool, str)

    # Signal emitted while the ruler is measuring: (is_active, message)
    ruler_changed = pyqtSignal(bool, str)

    # Signal emitted when background imagery loading starts/stops: (is_loading)
    loading_changed = pyqtSignal(bool)

    # Signal emitted when the view rotation changes: (degrees). Non-zero means
    # the view no longer points north-up, so lat/lon rulers become meaningless.

    # Signal emitted when user requests to hide layers outside view: (list of
    # layer_ids to hide)
    hide_layers_outside_view = pyqtSignal(list)

    # Signal emitted when user requests to show layers inside view: (list of
    # layer_ids to show)
    show_layers_in_view = pyqtSignal(list)

    # Signal emitted when user requests to toggle layer visibility: (layer_id)
    toggle_layer_visibility_requested = pyqtSignal(str)

    # Signal emitted when Space is pressed in cycle mode
    cycle_next_requested = pyqtSignal()

    # Signal emitted when Ctrl+Space is pressed in cycle mode (go backwards)
    cycle_prev_requested = pyqtSignal()

    # Minimum on-screen separation (view pixels) between the two clicks of a
    # measurement line; shorter lines are treated as an accidental click and
    # ignored so a stray double-click never records a bogus sub-metre value.
    _MIN_MEASURE_PIXELS = 4

    # How far (view pixels) the right button must move before a right-press in a
    # labeling mode counts as a pan-drag rather than a click. Below this, the
    # release opens the label context menu; at or above it, the view pans.
    _RIGHT_DRAG_PIXELS = 4

    # Don't zoom past a very small view; small enough for metre-scale objects.
    _MIN_VIEW_SIZE = 0.5
    # Rolling scene rect: QGraphicsView maps the scene rect to int device pixels
    # for its scrollbars/painting, so a huge fixed scene rect overflows int at
    # high zoom (tiles vanish, cursor glitches). Instead we keep the scene rect
    # only a few viewports large (in scene units) and re-centre it on the view -
    # its device extent then stays tiny, so the view can zoom in arbitrarily far.
    # Detail tiles are drawn this far above their layer's coarse tiles, which
    # sit at z = layer index. Comfortably below the label base (layer count +
    # offset), so labels and waypoints still render on top.
    _DETAIL_Z_OFFSET = 0.5

    # Where the overlay group sits among the tiles. Layer z-values are layer
    # order indices, so this only has to be past any believable image count.
    _OVERLAY_Z = 1e9
    # Guard against a runaway request: a viewport plus its prefetch margin is a
    # few dozen tiles, so this only ever trims pathological cases.
    _MAX_DETAIL_TILES = 200

    _SCENE_RECT_MARGIN = 2.0          # viewports of padding around the view
    _SCENE_RECT_DEVICE_TARGET = 5e8   # re-roll once the rect maps beyond this
    # A small scene rect keeps the *size* bounded, but Web Mercator positions are
    # huge (~2.5e7), so `position * scale` still overflows int at deep zoom even
    # for a tiny rect far from the origin. The floating origin fixes this: when
    # the view centre's scene coordinate maps beyond this many device pixels, we
    # rebase the origin onto the view so scene coordinates return to near zero.
    _REBASE_DEVICE_THRESHOLD = 3e8

    def __init__(self):
        """Set up the graphics scene, view interaction, mode/link/measure state,
        layer storage, overlays and background level-loading pool."""
        super().__init__()
        self._scene = QGraphicsScene()
        self.setScene(self._scene)

        # Floating origin. Web Mercator coordinates are huge (up to ~2.5e7), and
        # QGraphicsView maps scene->device through int scrollbars, so at deep
        # zoom `scene_coord * scale` overflows 2^31 - the view jumps and images
        # vanish. To avoid this, all geo-anchored items (tiles, labels) are
        # children of `_origin_group`, positioned in world coordinates but
        # shifted by `-_origin`, so their *scene* coordinates stay small no
        # matter where in the world we are. `_origin` is the world coordinate
        # currently sitting at scene (0, 0); rebased as needed while zooming.
        #   scene = world - _origin     world = scene + _origin
        # (world here means Web Mercator with Y flipped: x = easting, y = -north)
        self._origin = QPointF(0.0, 0.0)
        self._origin_group = QGraphicsRectItem()
        self._origin_group.setFlag(QGraphicsItem.ItemHasNoContents, True)
        self._scene.addItem(self._origin_group)

        # Everything drawn *on* the imagery - labels, waypoints - lives in
        # here rather than beside the tiles. A layer's z-value is its index in
        # the layer order, so it climbs without limit as images are added, and
        # anything sharing that scale has to be re-stamped above the highest
        # one every time the order changes. Labels were; waypoints were not,
        # so a waypoint drawn while five images were open sat at z 1011 and
        # disappeared under the imagery once the project passed a thousand
        # layers. Stacking overlays as a group settles it structurally: the
        # group is one item as far as the tiles are concerned, its z is a
        # constant no layer count can reach, and z-values inside it only have
        # to order overlays against each other.
        self._overlay_group = QGraphicsRectItem(self._origin_group)
        self._overlay_group.setFlag(QGraphicsItem.ItemHasNoContents, True)
        self._overlay_group.setZValue(self._OVERLAY_Z)

        # Enable pan and zoom
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)

        # Set background and allow dragging on empty space
        self.setBackgroundBrush(Qt.darkGray)
        # Web Mercator bounds: approximately -20037508 to +20037508 meters
        # Extended to include pixel zone (non-georeferenced images placed at X > 25M)
        WEB_MERCATOR_MAX = 20037508.34  # meters (at 180° longitude)
        SCENE_MAX = 30_000_000  # Enough to include pixel zone
        # The full pannable world (Web Mercator + pixel zone). The scene rect is
        # rolled to a small window around the view for zoom (see
        # _refresh_scene_rect), but panning is always clamped to this whole
        # extent so you can move freely well beyond the loaded imagery.
        self._world_rect_base = QRectF(
            -WEB_MERCATOR_MAX * 1.1,             # left (west)
            -SCENE_MAX,                          # top (Y is flipped: -north)
            WEB_MERCATOR_MAX * 1.1 + SCENE_MAX,  # width (extends into pixel zone)
            SCENE_MAX * 2)                       # height
        self.setSceneRect(self._world_rect_base)

        # Canvas mode
        self._mode = CanvasMode.PAN

        # Guard against re-entrancy while rolling the scene rect (setSceneRect
        # can itself trigger scrollContentsBy).
        self._suppress_scene_rect = False

        # Current absolute view rotation in degrees (0 = north-up). Non-zero in
        # image-up cycle mode, where the view is rotated onto an image's grid.
        self._current_class = ""  # Currently selected class for labeling

        # Link mode state
        self._link_mode_active = False
        self._link_source_label_id: int | None = None

        # Chain-link mode state: while active, left-clicking labels links them
        # all into one object (each click links immediately); N starts a new
        # chain. Highlighted items are remembered with their original pens so
        # the highlight can be undone.
        self._chain_link_active = False
        self._chain_link_anchor: int | None = None
        self._chain_members: set[int] = set()
        self._chain_highlighted: list = []

        # Measure mode state (drawing length/width lines on a label). Only
        # active for georeferenced labels; see _enter_measure_mode.
        self._measure_active = False
        self._measure_label_id: int | None = None
        self._measure_stage = MeasureStage.LENGTH
        self._measure_start = None  # QPointF: first click of the current line
        self._measure_start_view = None  # first click in view coords (for min-drag)
        self._measure_temp_line: QGraphicsLineItem | None = None  # rubber band
        self._measure_committed_line: QGraphicsLineItem | None = None  # finished length line
        self._measure_length_m: float | None = None  # result of the length line
        # Last mouse position over the viewport (view coords), used so the
        # 'M' shortcut can find the label under the cursor.
        self._last_mouse_view_pos = None

        # Ruler mode state (drag to measure ground distance).
        self._ruler_dragging = False
        self._ruler_start = None  # QPointF: drag start in scene coords
        self._ruler_line: QGraphicsLineItem | None = None
        self._ruler_text: QGraphicsTextItem | None = None
        # Crosshair dropped by "Go to Coordinates" (see mark_location).
        self._location_marker: QGraphicsPathItem | None = None
        # Waypoint markers: waypoint_id -> (marker, name text). Parented to the
        # floating-origin group, so they ride along with every rebase.
        self._waypoint_items: dict = {}
        # User toggle from the waypoints panel.
        self._waypoints_visible = True
        # Suppressed while waterfall mode is active: the stacked images are raw
        # pixels with no geography, so a geographic marker means nothing there.
        self._waypoints_suppressed = False
        # Right-drag panning while in ruler mode.
        self._ruler_panning = False
        self._ruler_pan_start = None  # QPoint: last pan position (view coords)

        # Label graphics items: label_id -> (ellipse_item, text_item)
        self._label_items: dict[int,
                                tuple[QGraphicsEllipseItem,
                                      QGraphicsTextItem]] = {}
        # Z-value offset for labels (added to max layer z-value to ensure
        # labels are always on top)

        # Layer storage
        self._layers: dict[str, TiledLayer] = {}
        self._layer_order: list[str] = []
        # file_path -> layer_id for duplicate detection
        self._path_to_layer: dict[str, str] = {}
        self._next_id = 1

        # Pixel zone layout: group_path -> (origin_x, max_width)
        # Tracks column positions for non-georeferenced image groups
        self._pixel_zone_groups: dict[str, tuple[float, float]] = {}
        self._pixel_zone_next_x = PIXEL_ZONE_ORIGIN_X

        # File paths of images flagged as hard-negative sources. Owned by the
        # project; main_window mirrors it here so the context menu can show
        # the toggle's current state without a round trip.
        self._hard_negative_paths: set[str] = set()

        # Waterfall mode state: while active, a group's layers are re-laid-out
        # stacked vertically in the pixel zone. Original bounds are saved so the
        # normal layout can be restored on exit; layers that were georeferenced
        # are switched to raw display and flipped back afterwards.
        self._waterfall_active = False
        self._waterfall_saved_bounds: dict[str, tuple] = {}
        self._waterfall_layer_order: list[str] = []
        self._waterfall_was_geo: set[str] = set()
        # Projected label markers (labels shown on other images that contain
        # them): list of (label_id, ellipse_item, text_item).
        self._waterfall_projection_items: list = []
        # Hold-to-glide navigation timer. Direction: -1 glides up, +1 down.
        self._waterfall_glide_dir = 0
        self._waterfall_glide_timer = QTimer()
        self._waterfall_glide_timer.setInterval(WATERFALL_GLIDE_INTERVAL_MS)
        self._waterfall_glide_timer.timeout.connect(
            self._on_waterfall_glide_tick)

        # Tile update timer (debounce rapid view changes)
        self._tile_update_timer = QTimer()
        self._tile_update_timer.setSingleShot(True)
        self._tile_update_timer.timeout.connect(self._update_visible_tiles)

        # Coarse-tile pixmap construction beyond the per-pass budget is
        # drained here, a bounded batch per event-loop turn, so a level
        # landing for a huge image no longer freezes the window while every
        # visible tile is copied into a pixmap in one synchronous pass.
        self._tile_build_queue: deque = deque()   # (layer_id, (tx, ty))
        self._tile_build_queued: set = set()
        self._tile_build_timer = QTimer()
        self._tile_build_timer.setSingleShot(True)
        self._tile_build_timer.timeout.connect(self._drain_tile_build_queue)

        # Coordinates emit throttle: coalesce mouseMoveEvent emissions so the
        # status bar isn't updated on every single pixel of mouse motion.
        # _pending_coords holds the latest payload; the timer fires at most
        # ~33 fps and emits only when the payload differs from the last one.
        self._coords_emit_timer = QTimer()
        self._coords_emit_timer.setSingleShot(True)
        self._coords_emit_timer.setInterval(30)
        self._coords_emit_timer.timeout.connect(self._flush_pending_coords)
        self._pending_coords: tuple | None = None
        self._last_emitted_coords: tuple | None = None

        # Background loader for expensive (fine) overview levels. `_level_load_
        # signals` keeps the per-job signal objects alive until they deliver.
        self._level_load_pool = QThreadPool(self)
        self._level_load_pool.setMaxThreadCount(
            min(4, max(1, QThread.idealThreadCount() - 1)))
        self._level_load_signals: set = set()

        # Detail tiles: full-resolution pixels for the visible area only, used
        # where an image is too large to hold whole. Separate pool from the
        # whole-image loads so a queue of tiles can't starve the coarse
        # backdrop that keeps the canvas populated.
        self._tile_pool = QThreadPool(self)
        self._tile_pool.setMaxThreadCount(
            min(4, max(1, QThread.idealThreadCount() - 1)))
        self._tile_signals: set = set()
        # (layer_id, level, tx, ty) -> runnable, for cancelling on scroll-out.
        self._pending_tiles: dict = {}

        # Images held in memory while off screen, so stepping onto them is
        # instant (see warm_layers). Insertion order is least-recently-wanted
        # first, which is the order they are released in. Values record how
        # an entry got here: "cycle" (prefetched neighbours, released when
        # the cycle ends) or "hidden" (kept on being hidden, released only by
        # the budget or Free Group).
        self._warmed: dict[str, str] = {}

        # Background-load tracking for the loading spinner. `_active_loads`
        # counts in-flight overview loads; the throbber shows while > 0.
        self._active_loads = 0
        self._loading_active = False
        self._throbber = ThrobberWidget(self)
        self._throbber.move(12, 12)

    def add_layer(self, file_path: str, lazy: bool = True,
                  visible: bool = True,
                  metadata: dict | None = None) -> str | None:
        """Add a GeoTIFF layer to the canvas. Returns existing layer_id if already loaded.

        Lazy by default: only bounds and metadata are read here, and the pixels
        arrive through the background level-of-detail path (coarse preview
        first, then the level the zoom actually wants). Loading eagerly instead
        decoded and reprojected the whole image on the UI thread before this
        call returned, which froze the window for as long as that took - many
        seconds on a large mosaic - for a layer that may not even be visible.
        Everything callers read straight afterwards (source dimensions, CRS,
        transform, bounds) is populated either way.

        Args:
            file_path: Path to the GeoTIFF file
            lazy: If True, only load bounds initially (faster for bulk imports)
            visible: Whether the layer should be visible initially
        """
        # Check if this file is already loaded
        if file_path in self._path_to_layer:
            return self._path_to_layer[file_path]

        try:
            layer = TiledLayer(file_path, lazy=lazy, metadata=metadata)
            layer.visible = visible

            layer_id = f"layer_{self._next_id}"
            self._next_id += 1

            self._layers[layer_id] = layer
            self._layer_order.append(layer_id)
            self._path_to_layer[file_path] = layer_id
            self._update_z_order()

            # Only update tiles if visible (skip for hidden layers)
            if visible:
                self._update_visible_tiles()

            # Fit view on first layer
            if len(self._layers) == 1:
                west, south, east, north = layer.bounds
                self._reset_origin_group(
                    QPointF((west + east) / 2.0, -(south + north) / 2.0))
                rect = self._world_rect_to_scene(
                    QRectF(west, -north, east - west, north - south))
                self.fitInView(rect, Qt.KeepAspectRatio)

            return layer_id

        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            traceback.print_exc()
            return None

    def add_pixel_layer(self, file_path: str, group_path: str = "",
                        lazy: bool = True, visible: bool = True,
                        metadata: dict | None = None) -> str | None:
        """Add a non-georeferenced image layer to the pixel zone.

        Lazy by default for the same reason as add_layer: the pixels come in
        through the background loader rather than blocking this call.

        Images in the same group are stacked (same position, cycled via visibility).
        Each group occupies a separate column in the pixel zone.

        Args:
            file_path: Path to the image file
            group_path: Group hierarchy for column layout
            lazy: If True, only load bounds initially
            visible: Whether the layer should be visible initially
        """
        if file_path in self._path_to_layer:
            return self._path_to_layer[file_path]

        try:
            layer = TiledLayer(file_path, lazy=lazy, geo=False,
                               metadata=metadata)
            layer.visible = visible
            layer.group_path = group_path

            # Assign pixel zone position based on group
            origin_x = self._get_pixel_zone_column(group_path, layer._width)
            layer.set_pixel_bounds(origin_x, PIXEL_ZONE_ORIGIN_Y)

            layer_id = f"layer_{self._next_id}"
            self._next_id += 1

            self._layers[layer_id] = layer
            self._layer_order.append(layer_id)
            self._path_to_layer[file_path] = layer_id
            self._update_z_order()

            if visible:
                self._update_visible_tiles()

            return layer_id

        except Exception as e:
            print(f"Error loading pixel layer {file_path}: {e}")
            traceback.print_exc()
            return None

    def _get_pixel_zone_column(self, group_path: str, layer_width: int) -> float:
        """Get or create a pixel zone column for a group.

        All images in the same group share the same X origin (stacked).
        Different groups get different columns.

        Returns:
            The X origin for this group's column.
        """
        if group_path in self._pixel_zone_groups:
            origin_x, max_width = self._pixel_zone_groups[group_path]
            # Update max width if this image is wider
            scaled_width = layer_width * PIXEL_ZONE_SCALE
            if scaled_width > max_width:
                self._pixel_zone_groups[group_path] = (origin_x, scaled_width)
            return origin_x

        # Allocate a new column
        origin_x = self._pixel_zone_next_x
        scaled_width = layer_width * PIXEL_ZONE_SCALE
        self._pixel_zone_groups[group_path] = (origin_x, scaled_width)
        self._pixel_zone_next_x = origin_x + scaled_width + PIXEL_ZONE_GROUP_GAP
        return origin_x

    def is_in_pixel_zone(self, easting: float) -> bool:
        """Check if a scene X coordinate is in the pixel zone."""
        return easting >= PIXEL_ZONE_ORIGIN_X

    # ------------------------------------------------------------------
    # Waterfall mode: stack a group's images vertically (raw pixels, no
    # reprojection) so the view can glide through them like a filmstrip.
    # ------------------------------------------------------------------

    def layout_waterfall(self, layer_ids: list[str]) -> float:
        """Stack the given layers vertically (tree order, top to bottom).

        Georeferenced layers are switched to RAW pixel display (no reprojection
        or warping); their source CRS/geotransform is kept so a clicked pixel
        can still be mapped to lat/lon. Each layer's bounds are overwritten
        with a stacked position in the pixel zone (originals saved for restore
        on exit) and all images share a common left edge.

        Returns the total stack height in scene units.
        """
        self._waterfall_active = True
        # The stack is raw pixels in the pixel zone, so geographic waypoints
        # have nothing to point at here; hide them until the mode is left.
        self._suppress_waypoints(True)
        self._waterfall_layer_order = []
        origin_x = PIXEL_ZONE_ORIGIN_X
        # Scene Y grows downward and scene north = -y: the first image's top
        # sits at north=0 and each subsequent image is placed below it.
        north = 0.0
        for layer_id in layer_ids:
            layer = self._layers.get(layer_id)
            if layer is None:
                continue
            # Show georeferenced images as raw pixels while stacked.
            if layer.geo:
                self._waterfall_was_geo.add(layer_id)
                layer.geo = False
                layer._fully_loaded = False
                self._cancel_layer_load(layer)
            # The stack is sized from source pixel dimensions; read them if
            # this (lazy) layer has never been opened.
            if layer._src_width <= 0 or layer._src_height <= 0:
                try:
                    layer.ensure_loaded(1)
                except Exception:
                    continue
            if layer._src_width <= 0 or layer._src_height <= 0:
                continue
            if layer_id not in self._waterfall_saved_bounds:
                self._waterfall_saved_bounds[layer_id] = layer.bounds
            w = layer._src_width * PIXEL_ZONE_SCALE
            h = layer._src_height * PIXEL_ZONE_SCALE
            south = north - h
            layer.bounds = (origin_x, south, origin_x + w, north)
            # Old tiles were positioned from the previous bounds; drop them so
            # they rebuild at the stacked location (with raw data).
            self._clear_layer_tiles(layer)
            self._waterfall_layer_order.append(layer_id)
            north = south - WATERFALL_GAP
        total_height = -north  # from y=0 down to the bottom of the last image
        self._update_visible_tiles()
        return total_height

    def clear_waterfall(self):
        """Restore the normal layout after leaving waterfall mode."""
        self.stop_waterfall_glide()
        self.clear_waterfall_projections()
        for layer_id, bounds in self._waterfall_saved_bounds.items():
            layer = self._layers.get(layer_id)
            if layer is None:
                continue
            layer.bounds = bounds
            # Layers shown raw go back to reprojected display.
            if layer_id in self._waterfall_was_geo:
                layer.geo = True
                layer._fully_loaded = False
                self._cancel_layer_load(layer)
            self._clear_layer_tiles(layer)
        self._waterfall_was_geo.clear()
        self._waterfall_saved_bounds.clear()
        self._waterfall_layer_order = []
        self._waterfall_active = False
        # Geography applies again, so waypoints come back.
        self._suppress_waypoints(False)
        self._update_visible_tiles()

    def start_waterfall_glide(self, direction: int):
        """Begin gliding the view while a nav key is held (-1 up, +1 down)."""
        if not self._waterfall_active or direction == 0:
            return
        self._waterfall_glide_dir = direction
        if not self._waterfall_glide_timer.isActive():
            self._waterfall_glide_timer.start()

    def stop_waterfall_glide(self):
        """Stop the waterfall glide (nav key released, or mode left)."""
        self._waterfall_glide_dir = 0
        self._waterfall_glide_timer.stop()

    def _on_waterfall_glide_tick(self):
        """Scroll the view a small step in the current glide direction."""
        if self._waterfall_glide_dir == 0 or not self._waterfall_active:
            self._waterfall_glide_timer.stop()
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.value()
                     + self._waterfall_glide_dir * WATERFALL_GLIDE_PX)

    def _scene_to_web(self, scene_pt: QPointF) -> tuple[float, float]:
        """Convert a scene point to Web Mercator (easting, northing).

        Accounts for the floating origin: world = scene + _origin, and scene Y
        is -northing. See the _origin docstring in __init__.
        """
        return (scene_pt.x() + self._origin.x(),
                -(scene_pt.y() + self._origin.y()))

    def _web_to_scene(self, easting: float, northing: float) -> QPointF:
        """Convert Web Mercator (easting, northing) to a scene point."""
        return QPointF(easting - self._origin.x(),
                       -northing - self._origin.y())

    def _world_rect_to_scene(self, rect_world: QRectF) -> QRectF:
        """Translate a rect expressed in world coords (x=easting, y=-north) into
        scene coords by subtracting the floating origin."""
        return rect_world.translated(-self._origin.x(), -self._origin.y())

    def _get_view_bounds(self) -> tuple[float, float, float, float]:
        """Get current view bounds in Web Mercator coordinates."""
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        # Scene coords -> world (easting, northing), honouring the floating origin.
        west, north = self._scene_to_web(rect.topLeft())
        east, south = self._scene_to_web(rect.bottomRight())
        return (west, south, east, north)

    def _view_scale(self) -> float:
        """Return the view's uniform scale factor (view pixels per scene unit).

        ``transform().m11()`` is only the scale while the view is unrotated - it
        becomes ``scale * cos(theta)`` once rotated (image-up cycle mode) - so
        derive the scale from the transform's determinant instead.
        """
        det = abs(self.transform().determinant())
        return math.sqrt(det) if det > 0 else 0.0

    def _scene_units_per_pixel(self) -> float:
        """Return the size of one on-screen pixel in scene units.

        Scene units are Web Mercator metres for geo layers. Larger values mean
        the view is more zoomed out. Derived from the view transform's
        horizontal scale factor (view-pixels per scene-unit).
        """
        scale = self._view_scale()
        return 1.0 / scale if scale > 0 else 0.0

    def view_ground_resolution(self) -> float:
        """Return the view's true ground resolution in metres per pixel.

        `_scene_units_per_pixel()` gives Web Mercator metres per pixel, which
        overestimates real-world distance by 1/cos(latitude). This applies the
        cos(latitude) correction using the latitude at the centre of the view,
        yielding actual metres per pixel on the ground. Falls back to the raw
        scene-units value at the equator (factor ≈ 1) or when there is nothing
        to measure.
        """
        units_per_pixel = self._scene_units_per_pixel()
        if units_per_pixel <= 0:
            return 0.0

        # View-centre latitude in WGS84 (scene Y is -northing in Web Mercator).
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        _easting, center_northing = self._scene_to_web(rect.center())
        _lon, lat = self._web_mercator_to_wgs84(0.0, center_northing)
        return units_per_pixel * math.cos(math.radians(lat))

    def _effective_cull_bounds(self) -> tuple[float, float, float, float]:
        """View bounds used for layer/tile culling decisions.

        Normally the exact viewport. In waterfall mode the bounds are inflated
        vertically by WATERFALL_PREFETCH_VIEWPORTS viewport heights in both
        directions, which does two things while gliding:
        - prefetch: the next images start loading before they scroll on-screen;
        - retention: images just scrolled past keep their tiles, so reversing
          direction shows them instantly instead of re-loading.
        """
        west, south, east, north = self._get_view_bounds()
        if self._waterfall_active:
            margin = (north - south) * WATERFALL_PREFETCH_VIEWPORTS
            south -= margin
            north += margin
        return (west, south, east, north)

    def _update_visible_tiles(self):
        """Load tiles that are visible, unload tiles that aren't."""
        cull_bounds = self._effective_cull_bounds()
        units_per_pixel = self._scene_units_per_pixel()

        for layer_id, layer in self._layers.items():
            if not layer.visible:
                continue

            # Cull layers entirely outside the (possibly inflated) viewport:
            # they must not trigger any pyramid loading. Drop any tiles they
            # may still hold and cancel an in-flight load so we don't keep
            # decoding off-screen data.
            if not self._layer_intersects_view(layer, cull_bounds):
                if layer.tiles:
                    self._clear_layer_tiles(layer)
                if layer.detail_tiles:
                    self._clear_detail_tiles(layer_id, layer)
                self._cancel_layer_load(layer)
                continue

            # Level-of-detail: pick an overview level for the current zoom and
            # (re)load this layer off-thread if it differs from what is loaded.
            self._apply_layer_lod(layer_id, layer, units_per_pixel)

            # Only build tiles for layers that already have data; unloaded ones
            # rebuild in _on_level_loaded once their background load lands.
            if layer.is_fully_loaded():
                self._rebuild_layer_tiles(layer)

            # Then the windowed detail on top, where the whole-image array
            # cannot reach the zoom's level.
            self._update_detail_tiles(layer_id, layer)

    @staticmethod
    def _layer_intersects_view(
            layer: TiledLayer,
            view_bounds: tuple[float, float, float, float]) -> bool:
        """Return True if a layer's bounds overlap the current view bounds."""
        if layer.bounds is None:
            return False
        lw, ls, le, ln = layer.bounds
        vw, vs, ve, vn = view_bounds
        return not (le < vw or lw > ve or ln < vs or ls > vn)

    # How many coarse tiles one pass may build synchronously. Each is up to a
    # 1 MB pixmap copy plus a scene insert, so this bounds the UI-thread cost
    # of any single pass at a few milliseconds; everything beyond it drains
    # through _tile_build_queue between paints, nearest the view centre first.
    _TILE_BUILD_BUDGET = 16

    def _rebuild_layer_tiles(self, layer: TiledLayer):
        """Add/remove a single layer's tiles to match the current view.

        Uses the effective cull bounds, so in waterfall mode tiles within the
        prefetch margin are built ahead of scrolling in and retained after
        scrolling out.

        Building is centre-out and budgeted: the first _TILE_BUILD_BUDGET
        tiles are made immediately (a typical viewport fits entirely in
        that), the rest are queued for the event loop. A 150 MP pyramid-less
        image used to build ~576 pixmaps in one synchronous pass here -
        roughly half a gigabyte of copying at the exact moment its load
        finished.
        """
        view_bounds = self._effective_cull_bounds()
        visible_indices = set(layer.get_visible_tile_indices(view_bounds))
        current_indices = set(layer.tiles.keys())

        # Remove tiles no longer visible
        for idx in current_indices - visible_indices:
            self._scene.removeItem(layer.tiles[idx])
            del layer.tiles[idx]

        missing = visible_indices - current_indices
        if not missing:
            return
        # The centroid of the wanted set stands in for the view centre: for a
        # viewport-clipped set they coincide, and for a whole image at fit
        # zoom the image centre IS the view centre.
        cx = sum(t[0] for t in missing) / len(missing)
        cy = sum(t[1] for t in missing) / len(missing)
        ordered = sorted(
            missing, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)

        for idx in ordered[:self._TILE_BUILD_BUDGET]:
            self._build_layer_tile(layer, idx)

        rest = ordered[self._TILE_BUILD_BUDGET:]
        if rest:
            layer_id = self._path_to_layer.get(layer.file_path)
            if layer_id is None:
                return
            for idx in rest:
                key = (layer_id, idx)
                if key not in self._tile_build_queued:
                    self._tile_build_queued.add(key)
                    self._tile_build_queue.append(key)
            self._tile_build_timer.start(0)

    def _drain_tile_build_queue(self):
        """Build one budget's worth of queued tiles, then yield to the loop."""
        budget = self._TILE_BUILD_BUDGET
        while self._tile_build_queue and budget > 0:
            layer_id, idx = self._tile_build_queue.popleft()
            self._tile_build_queued.discard((layer_id, idx))
            layer = self._layers.get(layer_id)
            # The world may have moved on since this was queued.
            if (layer is None or not layer.visible
                    or not layer.is_fully_loaded() or idx in layer.tiles):
                continue
            self._build_layer_tile(layer, idx)
            budget -= 1
        if self._tile_build_queue:
            self._tile_build_timer.start(0)

    def _build_layer_tile(self, layer: TiledLayer, idx: tuple):
        """Construct and place one coarse tile's pixmap item."""
        tx, ty = idx
        pixmap = layer.create_tile_pixmap(tx, ty)
        if pixmap is None:
            return

        # Parent to the floating-origin group so the tile's *scene*
        # position stays small at deep zoom (setPos below is in world coords).
        item = QGraphicsPixmapItem(pixmap, self._origin_group)

        # Standard axis-aligned scaling for GeoTIFF layers
        px_left, px_top, px_right, px_bottom, tile_west, tile_south, tile_east, tile_north = layer.get_tile_bounds(
            tx, ty)

        pixel_width = px_right - px_left
        pixel_height = px_bottom - px_top
        scale_x = (tile_east - tile_west) / pixel_width
        scale_y = (tile_north - tile_south) / pixel_height

        transform = QTransform()
        transform.scale(scale_x, scale_y)
        item.setTransform(transform)
        item.setPos(tile_west, -tile_north)

        item.setZValue(layer.z_value)
        item.setVisible(layer.visible)

        layer.tiles[idx] = item

    def _clear_layer_tiles(self, layer: TiledLayer):
        """Remove all of a layer's tiles from the scene."""
        for item in layer.tiles.values():
            self._scene.removeItem(item)
        layer.tiles.clear()
        # Queued builds refer to the grid being cleared; a level change would
        # otherwise build tiles for indices that no longer mean the same ground.
        if self._tile_build_queued:
            layer_id = self._path_to_layer.get(layer.file_path)
            if layer_id is not None:
                self._tile_build_queue = deque(
                    k for k in self._tile_build_queue if k[0] != layer_id)
                self._tile_build_queued = {
                    k for k in self._tile_build_queued if k[0] != layer_id}

    # ------------------------------------------------------------------
    # Detail tiles: full-resolution pixels for the visible area only.
    #
    # An image small enough to hold whole is served entirely by the tiles above,
    # cut from one reprojected array. That array is what limits how far a very
    # large mosaic can be zoomed, since it covers the WHOLE image at the
    # displayed level. For those, the coarse array stays on as a backdrop and
    # the detail the zoom actually asks for is read a tile at a time here, so
    # the cost follows the viewport instead of the image.
    # ------------------------------------------------------------------

    def _uses_detail_tiles(self, layer: TiledLayer) -> bool:
        """Whether this layer needs windowed detail rather than the whole image.

        Only for georeferenced images big enough that the whole-image path
        cannot reach full resolution. Waterfall mode is excluded: it rewrites
        bounds into the pixel zone, so the geographic tile grid means nothing
        there.
        """
        return (not self._waterfall_active
                and layer.geo
                and layer.bounds is not None
                and layer._src_crs is not None
                and layer.level_pixel_count(1) > MAX_LEVEL_PIXELS)

    def _update_detail_tiles(self, layer_id: str, layer: TiledLayer):
        """Bring a layer's detail tiles in line with the current view."""
        if not self._uses_detail_tiles(layer) or not layer.visible:
            self._clear_detail_tiles(layer_id, layer)
            return

        level = layer.resolution_level(self._scene_units_per_pixel())
        if level >= layer.budget_level(1):
            # The coarse whole-image array is already at least this detailed,
            # so tiles would add nothing.
            self._clear_detail_tiles(layer_id, layer)
            return

        grid = layer.detail_grid(level)
        if grid is None:
            return
        layer._detail_level = level
        grid_transform, grid_w, grid_h = grid

        view = self._effective_cull_bounds()
        if not self._waterfall_active:
            # A quarter-viewport margin on every side: tiles a small pan is
            # about to need start reading early, and tiles it just left stay
            # instead of being discarded at the exact edge and re-read the
            # moment the pan reverses. (Waterfall already carries its own,
            # much larger, vertical margin.)
            mx = (view[2] - view[0]) * 0.25
            my = (view[3] - view[1]) * 0.25
            view = (view[0] - mx, view[1] - my, view[2] + mx, view[3] + my)
        wanted = set()
        for tx, ty in tiles_for_bounds(grid_transform, grid_w, grid_h, view,
                                       DETAIL_TILE_SIZE):
            wanted.add((level, tx, ty))
        # Centre-out: the ground the user is looking at sharpens first, and
        # the runaway cap trims the corners rather than everything right of
        # an arbitrary column. (Dispatch below follows this order too - the
        # pool runs FIFO, so enqueue order is arrival order.)
        centre_col, centre_row = ~grid_transform * (
            (view[0] + view[2]) / 2.0, (view[1] + view[3]) / 2.0)
        ctx, cty = centre_col / DETAIL_TILE_SIZE, centre_row / DETAIL_TILE_SIZE
        ordered = sorted(
            wanted,
            key=lambda k: (k[1] - ctx) ** 2 + (k[2] - cty) ** 2)
        # A pathological zoom could ask for thousands; the viewport plus its
        # margin is a few dozen, so the cap only ever trims runaway requests
        # (and keeps the pool from being swamped).
        ordered = ordered[:self._MAX_DETAIL_TILES]
        wanted = set(ordered)

        # Drop what is no longer wanted - including every tile at another
        # level, since the zoom has moved on.
        for key in list(layer.detail_tiles):
            if key not in wanted:
                self._scene.removeItem(layer.detail_tiles.pop(key))
        for key in list(self._pending_tiles):
            if key[0] == layer_id and key[1:] not in wanted:
                self._pending_tiles.pop(key).cancel()

        for key in ordered:
            if key in layer.detail_tiles:
                continue
            if (layer_id,) + key in self._pending_tiles:
                continue
            self._dispatch_tile_load(layer_id, layer, *key)

    def _dispatch_tile_load(self, layer_id: str, layer: TiledLayer,
                            level: int, tx: int, ty: int):
        """Queue one detail tile for background reading."""
        signals = _TileLoadSignals()
        self._tile_signals.add(signals)
        signals.finished.connect(self._on_detail_tile_loaded)
        signals.failed.connect(self._on_detail_tile_failed)
        for signal in (signals.finished, signals.failed):
            signal.connect(
                lambda *_a, s=signals: self._tile_signals.discard(s))

        runnable = _TileLoadRunnable(
            layer_id, layer.file_path, level, tx, ty, signals,
            grid=layer.detail_grid(level))
        self._pending_tiles[(layer_id, level, tx, ty)] = runnable
        self._tile_pool.start(runnable)

    def _on_detail_tile_loaded(self, layer_id: str, level: int, tx: int,
                               ty: int, rgba, runnable=None):
        """Place a finished detail tile above the coarse backdrop."""
        key = (layer_id, level, tx, ty)
        # Only the read this key is actually waiting on may deliver. A
        # cancelled read can finish anyway (the cancel raced its final flag
        # check) and its queued event must not install a tile - nor evict the
        # entry of a newer read for the same key, which would leave that one
        # to arrive "unexpected" and the tile to be read a third time.
        if self._pending_tiles.get(key) is not runnable:
            return
        del self._pending_tiles[key]
        layer = self._layers.get(layer_id)
        if layer is None or rgba is None:
            return
        # The view may have moved on while this was reading.
        if not self._uses_detail_tiles(layer) or not layer.visible:
            return
        # The zoom may have moved to a different level: installing this tile
        # would break the one-level-on-screen invariant until the next update
        # pass happened to cull it.
        if level != layer._detail_level:
            return
        grid = layer.detail_grid(level)
        if grid is None:
            return
        grid_transform, grid_w, grid_h = grid
        x0, y0, x1, y1 = tile_span(grid_w, grid_h, tx, ty, DETAIL_TILE_SIZE)
        if x1 <= x0 or y1 <= y0:
            return

        height, width = rgba.shape[:2]
        image = QImage(rgba.data, width, height, 4 * width,
                       QImage.Format_RGBA8888)
        # fromImage already deep-copies into the pixmap, and `rgba` outlives
        # this call - the extra image.copy() doubled every tile's memcpy.
        item = QGraphicsPixmapItem(QPixmap.fromImage(image),
                                   self._origin_group)
        west, south, east, north = tile_bounds(
            grid_transform, x0, y0, x1, y1)
        transform = QTransform()
        transform.scale((east - west) / width, (north - south) / height)
        item.setTransform(transform)
        item.setPos(west, -north)
        # Above the coarse tiles of every layer, below the labels.
        item.setZValue(layer.z_value + self._DETAIL_Z_OFFSET)
        item.setVisible(layer.visible)

        old = layer.detail_tiles.pop((level, tx, ty), None)
        if old is not None:
            self._scene.removeItem(old)
        layer.detail_tiles[(level, tx, ty)] = item

    def _on_detail_tile_failed(self, layer_id: str, level: int, tx: int,
                               ty: int, runnable=None):
        """Forget a cancelled or failed tile so it can be retried later."""
        key = (layer_id, level, tx, ty)
        if self._pending_tiles.get(key) is runnable:
            del self._pending_tiles[key]

    def _clear_detail_tiles(self, layer_id: str, layer: TiledLayer):
        """Remove a layer's detail tiles and cancel any still reading."""
        layer._detail_level = None
        for item in layer.detail_tiles.values():
            self._scene.removeItem(item)
        layer.detail_tiles.clear()
        for key in list(self._pending_tiles):
            if key[0] == layer_id:
                self._pending_tiles.pop(key).cancel()

    def _desired_level(self, layer: TiledLayer, units_per_pixel: float) -> int:
        """The overview level this layer should be holding at this zoom."""
        if not layer.has_overviews():
            # No pyramids: full resolution, or the coarsest decimation that
            # fits in memory for an image too big to hold whole (without a
            # pyramid there is nothing cheaper to show in the meantime, so
            # this is the only load it gets).
            return layer.budget_level(1)
        if self._uses_detail_tiles(layer):
            # Windowed tiles carry the detail; this array only has to be a
            # cheap backdrop behind them.
            return layer.budget_level(1, BACKDROP_MAX_PIXELS)
        return layer.select_overview_level(units_per_pixel)

    def _apply_layer_lod(self, layer_id: str, layer: TiledLayer,
                         units_per_pixel: float):
        """Choose the overview level for the current zoom and load it off-thread.

        Never blocks the UI thread: any (re)load is dispatched to the background
        pool. Whatever is already loaded stays on screen as a preview until the
        new level is applied by _on_level_loaded. Panning at a fixed zoom, once
        loaded, is a no-op.
        """
        if not layer.has_overviews():
            if not layer.is_fully_loaded():
                level = self._desired_level(layer, units_per_pixel)
                if level > 1:
                    debug(f"memory cap: {layer.name} has no pyramids and is "
                          f"too large for full resolution; loading at 1/{level}")
                layer._target_level = level
                self._dispatch_level_load(layer_id, layer, level)
            return

        desired = self._desired_level(layer, units_per_pixel)
        if desired > 1 and layer.level_pixel_count(1) > MAX_LEVEL_PIXELS:
            # Detail is capped rather than zoom-limited; say so once per change
            # so "it stops getting sharper" has a visible reason.
            if layer._target_level != desired:
                debug(f"memory cap: {layer.name} limited to 1/{desired} "
                      f"({layer.level_pixel_count(desired) / 1e6:.0f} MP of "
                      f"{layer.level_pixel_count(1) / 1e6:.0f} MP full res)")
        layer._target_level = desired

        if layer.is_fully_loaded() and layer._loaded_level == desired:
            # Already showing the desired level. If a load for a *different*
            # level is still in flight (e.g. a high-res refine the user just
            # zoomed back out of), it's now obsolete - cancel it.
            if (layer._loading_level is not None
                    and layer._loading_level != desired):
                self._cancel_layer_load(layer)
            return

        if not layer.is_fully_loaded():
            # Nothing on screen yet: load the cheapest level first for a fast
            # preview; _on_level_loaded then chases the desired level.
            self._dispatch_level_load(layer_id, layer, layer.coarsest_level())
            return

        # Already showing a preview at another level: refine to the target
        # while the current tiles stay on screen (swapped in when ready).
        self._dispatch_level_load(layer_id, layer, desired)

    def _fit_units_per_pixel(self, layer: TiledLayer) -> float | None:
        """Scene units per screen pixel once ``zoom_to_layer`` frames *layer*.

        Mirrors the ``fitInView(..., KeepAspectRatio)`` there, so a layer warmed
        with this lands on the same overview level it will ask for when shown.
        """
        if layer.bounds is None:
            return None
        west, south, east, north = layer.bounds
        view_width = max(1, self.viewport().width())
        view_height = max(1, self.viewport().height())
        return max((east - west) / view_width, (north - south) / view_height)

    def warm_layers(self, layer_ids: list[str]):
        """Read hidden layers now so showing them later is instant.

        Cycle modes call this with the images either side of the current one.
        Nothing is drawn: the pixels are read on the background pool and held
        on the layer, so stepping onto one only has to build tiles from memory.

        Held images are trimmed to WARM_MAX_PIXELS afterwards, oldest first.
        That bounds a walk through a large group, which nothing did before -
        every image visited stayed in memory until the window closed - while
        still leaving several steps' worth of small images loaded, so stepping
        back through them stays as immediate as it was.
        """
        for layer_id in layer_ids:
            layer = self._layers.get(layer_id)
            # A visible layer is the user's, or the cycle's; not ours to hold.
            if layer is None or layer.visible:
                continue
            units_per_pixel = self._fit_units_per_pixel(layer)
            if units_per_pixel is None:
                continue
            if layer.level_pixel_count(
                    self._desired_level(layer, units_per_pixel)) > WARM_MAX_PIXELS:
                # One image that fills the whole budget would be evicted again
                # on the next step; leave it to load when it is reached.
                debug(f"prefetch: {layer.name} too large to hold ahead")
                continue
            self._warmed.pop(layer_id, None)
            self._warmed[layer_id] = "cycle"       # most recently wanted, last
            self._apply_layer_lod(layer_id, layer, units_per_pixel)
        self._trim_warmed(set(layer_ids))

    def _trim_warmed(self, protect: set):
        """Free held images, least recently wanted first, until within budget."""
        held = 0
        for layer_id in reversed(list(self._warmed)):     # newest first
            layer = self._layers.get(layer_id)
            if layer is None or layer.visible:
                # Gone, or switched on since - either way not ours any more.
                del self._warmed[layer_id]
                continue
            # Charge the level actually in memory. Hiding a layer whose
            # refine was cancelled mid-flight leaves _loaded_level data behind
            # while _target_level points at the refine - budgeting the target
            # under-counted a level-1 array by up to the level factor squared.
            resident = (layer._loaded_level if layer.is_fully_loaded()
                        else layer._target_level)
            held += layer.level_pixel_count(resident or 1)
            if held <= WARM_MAX_PIXELS or layer_id in protect:
                continue
            self._cancel_layer_load(layer)
            layer.free_data(self._scene)
            del self._warmed[layer_id]

    def clear_warmed_layers(self):
        """Release what warm_layers holds (on leaving a cycle mode).

        Only cycle-warmed entries: layers that joined the pool by being
        hidden are not the cycle's to free - reaching for the ruler mid-
        session must not dump every hidden image's pixels.
        """
        for layer_id in list(self._warmed):
            if self._warmed.get(layer_id) != "cycle":
                continue
            layer = self._layers.get(layer_id)
            if layer is not None and not layer.visible:
                self._cancel_layer_load(layer)
                layer.free_data(self._scene)
            del self._warmed[layer_id]

    def free_layer_data(self, layer_id: str):
        """Release a layer's pixel data, cancelling any in-flight load first.

        Freeing without cancelling let a refine that was already reading
        land seconds later and silently reallocate everything the user had
        just released - on exactly the large-mosaic sessions the Free Group
        action exists for.
        """
        layer = self._layers.get(layer_id)
        if layer is None:
            return
        self._cancel_layer_load(layer)
        self._clear_detail_tiles(layer_id, layer)
        layer.free_data(self._scene)
        # A freed layer holds nothing; leaving it enrolled charged phantom
        # pixels against the warm budget and evicted images that were
        # genuinely held.
        self._warmed.pop(layer_id, None)

    def _dispatch_level_load(self, layer_id: str, layer: TiledLayer, level: int):
        """Start a background load of *layer* at *level*.

        Supersedes any pending load for the same layer at a different level
        (rapid zoom cancels the intermediate loads), so at most one load per
        layer is ever active.
        """
        if layer._loading_level == level:
            return  # already loading exactly this level

        # Cancel the previous in-flight load for this layer (different level).
        if layer._pending_runnable is not None:
            debug(f"supersede: {layer.name} level {layer._loading_level} -> {level}")
            layer._pending_runnable.cancel()
            layer._pending_runnable = None

        debug(f"dispatch load: {layer.name} level={level}")
        layer._loading_level = level

        # Count this in-flight load and show the spinner. Each started runnable
        # emits exactly one finished / error / cancelled, which decrements it.
        self._active_loads += 1
        self._update_throbber()

        signals = _LevelLoadSignals()
        self._level_load_signals.add(signals)
        signals.finished.connect(self._on_level_loaded)
        signals.error.connect(self._on_level_load_error)
        signals.cancelled.connect(self._on_level_load_cancelled)
        # Keep the signals object alive until it has delivered, then release it.
        for sig in (signals.finished, signals.error, signals.cancelled):
            sig.connect(lambda *_a, s=signals: self._level_load_signals.discard(s))

        runnable = _LevelLoadRunnable(
            layer_id, layer.file_path, layer.geo, level, signals)
        layer._pending_runnable = runnable
        self._level_load_pool.start(runnable)

    def _cancel_layer_load(self, layer: TiledLayer):
        """Cancel a layer's in-flight background load, if any.

        The runnable's cancelled signal still fires (decrementing the load
        counter), so the spinner clears once the cancelled loads drain.
        """
        if layer._pending_runnable is not None:
            debug(f"cancel load (out of view/hidden): {layer.name} "
                  f"level={layer._loading_level}")
            layer._pending_runnable.cancel()
            layer._pending_runnable = None
            layer._loading_level = None

    def _on_level_loaded(self, layer_id: str, level: int, result: dict,
                         runnable=None):
        """Apply a background-loaded overview level on the UI thread."""
        # Balance the in-flight counter first (one per started runnable), even
        # if the layer was removed while loading.
        self._active_loads = max(0, self._active_loads - 1)
        self._update_throbber()

        layer = self._layers.get(layer_id)
        if layer is None:
            return  # Layer was removed while loading.

        # Only the tracked runnable's result is welcome. A mismatch means the
        # load was cancelled or superseded while this event sat in the queue -
        # applying it anyway is how freed data came back from the dead and how
        # a waterfall relayout got geo pixels pinned onto a stacked layer.
        if layer._pending_runnable is not runnable:
            debug(f"stale level result discarded: {layer.name} level={level}")
            return
        layer._loading_level = None
        layer._pending_runnable = None

        # A newer zoom may have superseded this level; if so, chase the new one.
        if level != layer._target_level:
            # Show it anyway when the layer has nothing on screen. This is the
            # normal first-load path: _apply_layer_lod deliberately asks for the
            # coarsest level as a quick preview while the target level loads, so
            # discarding it here would bin the very preview it just paid for and
            # leave the canvas blank until the (slow) full-resolution read lands.
            # Once something is displayed, a stale level is genuinely obsolete.
            if not layer.is_fully_loaded():
                layer.apply_level_result(result)
                debug(f"preview level {level}: {layer.name} "
                      f"{layer._width}x{layer._height} "
                      f"(target {layer._target_level})")
                self._clear_layer_tiles(layer)
                # Same guard as the final branch below: a warmed neighbour
                # loading ahead of its turn has nothing to draw yet.
                if layer.visible:
                    self._rebuild_layer_tiles(layer)
            if (layer.has_overviews()
                    and layer._target_level != layer._loaded_level):
                self._dispatch_level_load(layer_id, layer, layer._target_level)
            return

        layer.apply_level_result(result)
        debug(f"applied level {level}: {layer.name} "
              f"{layer._width}x{layer._height}")
        self._clear_layer_tiles(layer)
        # A layer warmed ahead of its turn has no tiles to build yet; they are
        # built from this data the moment it is shown.
        if layer.visible:
            self._rebuild_layer_tiles(layer)

    def _on_level_load_error(self, layer_id: str, level: int, message: str,
                             runnable=None):
        """Handle a failed background level load."""
        self._active_loads = max(0, self._active_loads - 1)
        self._update_throbber()

        layer = self._layers.get(layer_id)
        # Identity, not (layer_id, level): a stale event matching on level
        # would clear a newer runnable's tracking, leaving it uncancellable
        # and spawning a duplicate load on the next LOD pass.
        if layer is not None and layer._pending_runnable is runnable:
            layer._loading_level = None
            layer._pending_runnable = None
        debug(f"load FAILED: {layer_id} level={level}: {message}")

    def _on_level_load_cancelled(self, layer_id: str, level: int,
                                 runnable=None):
        """Handle a cancelled background level load (superseded or culled)."""
        self._active_loads = max(0, self._active_loads - 1)
        self._update_throbber()

        layer = self._layers.get(layer_id)
        if layer is not None and layer._pending_runnable is runnable:
            layer._loading_level = None
            layer._pending_runnable = None

    def _update_throbber(self):
        """Show/hide the loading spinner based on the in-flight load count."""
        active = self._active_loads > 0
        if active:
            self._throbber.start()
        else:
            self._throbber.stop()
        if active != self._loading_active:
            self._loading_active = active
            self.loading_changed.emit(active)


    def _schedule_tile_update(self):
        """Schedule a tile update, coalescing bursts without starving motion.

        Restarting the timer on every call - the previous behaviour - meant a
        source of events faster than 50ms kept it from EVER firing: the
        waterfall glide ticks the scrollbar every 16ms, so a held Space key
        dispatched no loads at all and the user glided into blank rows while
        both pools sat idle. Arming only when idle turns the trailing debounce
        into a throttle: bursts still coalesce, but sustained motion gets an
        update every 50ms.
        """
        if not self._tile_update_timer.isActive():
            self._tile_update_timer.start(50)

    def set_layer_visibility(self, layer_id: str, visible: bool):
        """Show or hide a layer."""
        if layer_id in self._layers:
            layer = self._layers[layer_id]
            layer.set_visibility(visible)
            for item in layer.detail_tiles.values():
                item.setVisible(visible)
            if visible:
                # Deferred: a cycle step toggles visibility BEFORE it
                # zooms, and updating synchronously here evaluated the
                # LOD at the previous image's zoom - loading a level the
                # step was about to discard. One throttled update after
                # the zoom serves both.
                self._schedule_tile_update()
            else:
                # Hidden layers shouldn't keep loading in the background.
                self._cancel_layer_load(layer)
                self._clear_detail_tiles(layer_id, layer)
                # A hidden layer's pixels used to be kept forever, outside
                # every budget - hide fifty large images and they all stayed
                # in memory until the window closed. It joins the warm pool
                # instead: re-showing it stays instant while the budget
                # allows, and the least recently hidden are released once it
                # does not. (Free Group remains the explicit release.)
                if layer.is_fully_loaded():
                    self._warmed.pop(layer_id, None)
                    self._warmed[layer_id] = "hidden"    # most recent last
                    self._trim_warmed(set())
            # For non-geo layers, toggle associated label markers
            if not layer.geo:
                self._set_label_visibility_for_image(layer.file_path, visible)
            # Force viewport update to ensure cursor appears on top of tiles
            self.viewport().update()

    def update_layer_order(self, layer_order: list[str]):
        """Update the rendering order of layers."""
        self._layer_order = layer_order
        self._update_z_order()

    def _update_z_order(self):
        """Update z-values based on layer order."""
        for i, layer_id in enumerate(self._layer_order):
            if layer_id in self._layers:
                self._layers[layer_id].set_z_value(i)

    def _get_label_z_base(self) -> float:
        """Base z-value for overlay items, as an offset within the overlays.

        Overlays used to be stacked against the layers by counting them, which
        meant re-stamping every marker whenever the count changed. They are in
        _overlay_group now, so the only thing left to order is overlays among
        themselves and this is a fixed base. Kept as a method because the
        ruler and measurement lines are top-level scene items rather than
        group members, and still need a value above the origin group's zero.
        """
        return 1.0

    def remove_layer(self, layer_id: str):
        """Remove a layer from the canvas."""
        if layer_id in self._layers:
            file_path = self._layers[layer_id].file_path
            self._cancel_layer_load(self._layers[layer_id])
            self._clear_detail_tiles(layer_id, self._layers[layer_id])
            self._layers[layer_id].remove_from_scene(self._scene)
            del self._layers[layer_id]
            if file_path in self._path_to_layer:
                del self._path_to_layer[file_path]
            if layer_id in self._layer_order:
                self._layer_order.remove(layer_id)
            self._warmed.pop(layer_id, None)

    def clear_layers(self):
        """Remove all layers from the canvas."""
        for layer_id in list(self._layers.keys()):
            self._clear_detail_tiles(layer_id, self._layers[layer_id])
            self._layers[layer_id].remove_from_scene(self._scene)
        self._layers.clear()
        self._warmed.clear()
        self._layer_order.clear()
        self._path_to_layer.clear()
        self._pixel_zone_groups.clear()
        self._pixel_zone_next_x = PIXEL_ZONE_ORIGIN_X
        self._hard_negative_paths.clear()

    def set_layer_group(self, layer_id: str, group_path: str):
        """Set the group path for a layer."""
        if layer_id in self._layers:
            self._layers[layer_id].group_path = group_path

    def is_path_loaded(self, file_path: str) -> bool:
        """Check if a file path is already loaded as a layer."""
        return file_path in self._path_to_layer

    def get_layer_file_path(self, layer_id: str) -> str | None:
        """Get the file path for a layer."""
        if layer_id in self._layers:
            return self._layers[layer_id].file_path
        return None

    def get_layer_infos(self) -> list[dict]:
        """Return per-layer info for all loaded layers, in display order.

        Each dict has keys ``layer_id``, ``file_path``, ``group_path``,
        ``name`` and ``geo``. Used by the optimized-export feature to mirror the
        layer tree structure and locate the source rasters.
        """
        infos = []
        for layer_id in self._layer_order:
            layer = self._layers.get(layer_id)
            if layer is None:
                continue
            infos.append({
                "layer_id": layer_id,
                "file_path": layer.file_path,
                "group_path": layer.group_path,
                "name": layer.name,
                "geo": layer.geo,
                "visible": layer.visible,
            })
        return infos

    def get_layer(self, layer_id: str) -> TiledLayer | None:
        """Get the TiledLayer object for a given layer ID."""
        return self._layers.get(layer_id)

    def get_layer_source_dimensions(self, layer_id: str) -> tuple[int, int]:
        """Get the original source dimensions (width, height) for a layer."""
        if layer_id in self._layers:
            layer = self._layers[layer_id]
            return layer._src_width, layer._src_height
        return 0, 0

    def get_layer_transform(self, layer_id: str) -> tuple:
        """Get the affine transform and CRS for a layer.

        Returns:
            Tuple of (affine, crs) where affine is an Affine transform and
            crs is a rasterio CRS, or (None, None) if layer not found.
        """
        if layer_id in self._layers:
            layer = self._layers[layer_id]
            return layer._src_transform, layer._src_crs
        return None, None

    def zoom_to_layer(self, layer_id: str):
        """Zoom the view to fit a specific layer's bounds."""
        if layer_id not in self._layers:
            return

        bounds = self._layers[layer_id].bounds
        west, south, east, north = bounds
        # Rebase the floating origin onto the layer so its scene coords are near
        # zero (allows zooming in arbitrarily far afterwards without overflow).
        self._reset_origin_group(
            QPointF((west + east) / 2.0, -(south + north) / 2.0))
        rect = self._world_rect_to_scene(
            QRectF(west, -north, east - west, north - south))
        self.fitInView(rect, Qt.KeepAspectRatio)
        self._refresh_scene_rect()
        self._schedule_tile_update()

    def zoom_to_point(self, lon: float, lat: float, size_meters: float = 10.0):
        """Zoom the view to center on a point with a given extent in meters.

        Args:
            lon: Longitude (WGS84)
            lat: Latitude (WGS84)
            size_meters: The width/height of the view in meters (default 10m)
        """
        # Convert point to Web Mercator
        center_x, center_y = self._wgs84_to_web_mercator(lon, lat)

        # In Web Mercator, units are meters, so size_meters directly gives the
        # extent
        half_size = size_meters / 2

        west = center_x - half_size
        east = center_x + half_size
        south = center_y - half_size
        north = center_y + half_size

        # Rebase the floating origin onto the point so scene coords stay small.
        self._reset_origin_group(QPointF(center_x, -center_y))
        # Create rect in world coords (Y flipped), then shift to scene coords.
        rect = self._world_rect_to_scene(
            QRectF(west, -north, east - west, north - south))
        self.fitInView(rect, Qt.KeepAspectRatio)
        self._refresh_scene_rect()
        self._schedule_tile_update()
        self.update_label_markers_scale()

    def wheelEvent(self, event: QWheelEvent):
        """Zoom in/out with the mouse wheel, centred on the cursor.

        Zoom depth is only limited by a tiny minimum view size (metre-scale is
        fine); overflow is avoided by the rolling scene rect below rather than a
        zoom cap.

        The point under the cursor is kept fixed by manual anchoring based on
        event.pos() (NOT Qt's AnchorUnderMouse, which reads the OS cursor and
        misbehaves with our rolling scene rect): record the scene point under
        the cursor, scale about nothing, then re-centre so that same point lands
        back under the cursor. The floating origin keeps scene coordinates small,
        so centerOn never clamps and this stays accurate at any zoom. Done in
        scene coordinates, so it is unaffected by view rotation.
        """
        factor = 1.15
        if event.angleDelta().y() > 0:
            view_rect = self.mapToScene(self.viewport().rect()).boundingRect()
            if (view_rect.width() / factor < self._MIN_VIEW_SIZE
                    or view_rect.height() / factor < self._MIN_VIEW_SIZE):
                return  # at the minimum view size
            zoom = factor
        else:
            zoom = 1 / factor

        # Scene point under the cursor before zooming.
        anchor_scene = self.mapToScene(event.pos())
        # Scale + anchor correction with the scene-rect roll suppressed so it
        # can't re-enter (via scrollContentsBy) mid-correction.
        self._suppress_scene_rect = True
        try:
            prev_anchor = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            self.scale(zoom, zoom)
            self.setTransformationAnchor(prev_anchor)
            # Shift the view so the same scene point sits back under the cursor.
            # centerOn(P) maps the cursor to P + K (K constant at this scale);
            # currently it maps to anchor + drift, so target P = center - drift.
            drift = self.mapToScene(event.pos()) - anchor_scene
            if not drift.isNull():
                center = self.mapToScene(self.viewport().rect().center())
                self.centerOn(center - drift)
        finally:
            self._suppress_scene_rect = False

        # Now roll the scene rect once (view-preserving) so deep zoom never
        # overflows Qt's int coordinates without disturbing the anchor.
        self._refresh_scene_rect()
        self._schedule_tile_update()
        self.update_label_markers_scale()

    def _world_scene_rect(self) -> QRectF:
        """Outer bound the pannable area is clamped to.

        Normally this is the full Web Mercator + pixel-zone extent (so you can
        pan freely into empty space well beyond the loaded imagery), expanded
        to include any loaded layers. In waterfall mode it is just the stacked
        images plus a small margin, so panning stays within the stack while
        zooming is unaffected.
        """
        if self._waterfall_active and self._waterfall_layer_order:
            rect = None
            for layer_id in self._waterfall_layer_order:
                layer = self._layers.get(layer_id)
                if layer is None or layer.bounds is None:
                    continue
                west, south, east, north = layer.bounds
                r = QRectF(west, -north, east - west, north - south)
                rect = r if rect is None else rect.united(r)
            if rect is not None:
                mx = rect.width() * 0.05
                my = rect.height() * 0.02
                return self._world_rect_to_scene(
                    rect.adjusted(-mx, -my, mx, my))

        rect = QRectF(self._world_rect_base)
        for layer in self._layers.values():
            if layer.bounds is None:
                continue
            west, south, east, north = layer.bounds
            rect = rect.united(QRectF(west, -north, east - west, north - south))
        # _world_rect_base and layer bounds are in world coords; the clamp is
        # used in scene coords, so shift by the floating origin.
        return self._world_rect_to_scene(rect)

    def _reset_origin_group(self, world_pt: QPointF):
        """Set the floating origin to `world_pt` without preserving the view.

        Used before an explicit fitInView (zoom-to-layer/point), which sets the
        view itself. `world_pt` is in world coords (x=easting, y=-northing).
        """
        self._origin = QPointF(world_pt)
        self._origin_group.setPos(-world_pt.x(), -world_pt.y())

    def _rebase_origin(self, world_pt: QPointF):
        """Move the floating origin to `world_pt`, keeping the view on the same
        world location. Keeps scene coordinates near zero so `coord * scale`
        never overflows Qt's int device coordinates at deep zoom."""
        vc_scene = self.mapToScene(self.viewport().rect().center())
        vc_world = QPointF(vc_scene.x() + self._origin.x(),
                           vc_scene.y() + self._origin.y())
        view = self.mapToScene(self.viewport().rect()).boundingRect()
        # How far the scene shifts under everything (the origin group moves by
        # -delta; transient overlay items in raw scene coords must follow).
        dx = world_pt.x() - self._origin.x()
        dy = world_pt.y() - self._origin.y()
        self._origin = QPointF(world_pt)
        self._origin_group.setPos(-world_pt.x(), -world_pt.y())
        for item in (self._ruler_line, self._ruler_text,
                     self._measure_temp_line, self._measure_committed_line):
            if item is not None:
                item.moveBy(-dx, -dy)
        new_center = QPointF(vc_world.x() - world_pt.x(),
                             vc_world.y() - world_pt.y())
        w = max(view.width(), 1e-9)
        h = max(view.height(), 1e-9)
        window = QRectF(new_center.x() - 3 * w, new_center.y() - 3 * h,
                        6 * w, 6 * h)
        self._suppress_scene_rect = True
        try:
            self.setSceneRect(window)
            self.centerOn(new_center)
        finally:
            self._suppress_scene_rect = False

    def _maybe_rebase_origin(self, scale: float):
        """Rebase the floating origin onto the view if the view centre's scene
        coordinate has wandered far enough that `coord * scale` risks overflow.
        Skipped mid measure/ruler drag (their items live in raw scene coords)."""
        if self._measure_active or self._ruler_dragging:
            return
        vc = self.mapToScene(self.viewport().rect().center())
        if max(abs(vc.x()), abs(vc.y())) * scale > self._REBASE_DEVICE_THRESHOLD:
            self._rebase_origin(QPointF(vc.x() + self._origin.x(),
                                        vc.y() + self._origin.y()))

    def _refresh_scene_rect(self):
        """Roll the scene rect to a bounded window around the current view.

        Only re-rolls when the view nears the current rect's edge or the rect
        maps to too many device pixels (i.e. after zooming in), so ordinary
        panning within the window is a no-op. Clamped to the full world extent
        (Web Mercator + pixel zone) so you can still pan freely into empty
        space around the imagery.
        """
        if self._suppress_scene_rect:
            return
        scale = self._view_scale()
        if scale <= 0:
            return
        # Keep the view near the scene origin so scene_coord * scale stays within
        # int range (prevents the deep-zoom overflow for far-from-origin data).
        self._maybe_rebase_origin(scale)
        view = self.mapToScene(self.viewport().rect()).boundingRect()
        if view.width() <= 0 or view.height() <= 0:
            return

        cur = self.sceneRect()
        # Comfortable if the view sits >1 viewport inside the rect and the rect
        # still maps to a safe device extent.
        inset = cur.adjusted(view.width(), view.height(),
                             -view.width(), -view.height())
        device_extent = max(cur.width(), cur.height()) * scale
        if (inset.width() > 0 and inset.height() > 0 and inset.contains(view)
                and device_extent <= self._SCENE_RECT_DEVICE_TARGET):
            return

        margin = self._SCENE_RECT_MARGIN
        window = QRectF(
            view.left() - margin * view.width(),
            view.top() - margin * view.height(),
            view.width() * (1 + 2 * margin),
            view.height() * (1 + 2 * margin))
        target = window
        world = self._world_scene_rect()
        if world is not None:
            if world.width() <= window.width() and world.height() <= window.height():
                target = world  # whole world fits: no rolling needed
            else:
                clipped = window.intersected(world)
                if not clipped.isEmpty():
                    target = clipped

        # Changing the scene rect changes the scrollbar ranges, which otherwise
        # shifts the view (the same scrollbar value maps to a new scene point).
        # Re-centre on the current scene centre afterwards so rolling the rect
        # never moves the view - callers rely on this to keep the cursor anchor.
        view_center = self.mapToScene(self.viewport().rect().center())
        self._suppress_scene_rect = True
        try:
            self.setSceneRect(target)
            self.centerOn(view_center)
        finally:
            self._suppress_scene_rect = False

    def scrollContentsBy(self, dx: int, dy: int):
        """Called when view is scrolled (panned)."""
        super().scrollContentsBy(dx, dy)
        self._refresh_scene_rect()
        self._schedule_tile_update()

    def resizeEvent(self, event):
        """Called when view is resized."""
        super().resizeEvent(event)
        self._refresh_scene_rect()
        self._schedule_tile_update()

    def set_mode(self, mode: CanvasMode):
        """Set the canvas interaction mode."""
        # Switching modes ends any in-progress ruler measurement.
        self._clear_ruler()
        # Leaving waterfall stops any active glide immediately.
        if mode != CanvasMode.WATERFALL:
            self.stop_waterfall_glide()
        # Chain linking only makes sense while labels are clickable.
        if mode not in LABELING_MODES and self._chain_link_active:
            self.set_chain_link_mode(False)
        self._mode = mode
        if mode == CanvasMode.PAN:
            # We handle panning manually
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.OpenHandCursor)
            self._pan_active = False
        elif mode == CanvasMode.LABEL:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(_crosshair_cursor())
        elif mode == CanvasMode.RULER:
            # Ruler mode: left-drag to measure distance; wheel still zooms.
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(_crosshair_cursor())
            self._ruler_dragging = False
        elif mode in CYCLE_MODES:
            # Cycle mode: left click labels, right drag pans, wheel zooms
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(_crosshair_cursor())
            self._cycle_panning = False

    def set_current_class(self, class_name: str):
        """Set the current class for labeling."""
        self._current_class = class_name

    def get_current_class(self) -> str:
        """Get the current class for labeling."""
        return self._current_class

    def mousePressEvent(self, event):
        """Handle mouse press for labeling."""
        # Measure mode intercepts clicks regardless of the underlying mode:
        # left click draws a line vertex, right click cancels.
        if self._measure_active:
            if event.button() == Qt.LeftButton:
                self._handle_measure_click(event.pos())
            elif event.button() == Qt.RightButton:
                self._exit_measure_mode()
            return

        # Shift+left-drag measures from whatever mode is active, so reaching for
        # the ruler never costs a mode switch - and in cycle modes, never costs
        # the cycle queue.
        if (event.button() == Qt.LeftButton
                and event.modifiers() & Qt.ShiftModifier
                and self._mode != CanvasMode.RULER):
            self._ruler_begin(event.pos())
            return

        # Ruler mode: left-drag measures distance; right-drag pans the view.
        if self._mode == CanvasMode.RULER:
            if event.button() == Qt.LeftButton:
                self._ruler_begin(event.pos())
            elif event.button() == Qt.RightButton:
                self._ruler_panning = True
                self._ruler_pan_start = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
            return

        # PAN mode: manual left-click drag panning
        if self._mode == CanvasMode.PAN:
            if event.button() == Qt.LeftButton:
                self._pan_active = True
                self._pan_start = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
            elif event.button() == Qt.RightButton:
                self._show_pan_context_menu(event.pos())
            return

        # Handle labeling in LABEL or CYCLE/VIEW_CYCLE mode
        if self._mode in LABELING_MODES and event.button() == Qt.LeftButton:
            # Check if we're in link mode
            if self._link_mode_active:
                label_id, image_path = self._get_label_at_position(event.pos())
                if label_id is not None and label_id != self._link_source_label_id:
                    # Link the two labels
                    self.labels_linked.emit(
                        self._link_source_label_id, label_id)
                # Exit link mode regardless
                self._exit_link_mode()
                return

            # Chain-link overlay: clicks select labels to link instead of
            # placing new labels (a miss on empty canvas does nothing).
            if self._chain_link_active:
                label_id, _ = self._get_label_at_position(event.pos())
                if label_id is not None:
                    self._chain_link_click(label_id)
                return

            # Ctrl+Left-click in CYCLE/VIEW_CYCLE mode shows label context menu (for
            # linking)
            if self._mode in CYCLE_MODES and event.modifiers() & Qt.ControlModifier:
                self._show_label_context_menu(event.pos())
                return

            if not self._current_class:
                return  # No class selected

            scene_pos = self.mapToScene(event.pos())
            easting, northing = self._scene_to_web(scene_pos)

            # Get image at this position and the layer object
            layer, layer_name, group_path = self._get_layer_and_info_at_position(
                easting, northing)

            # Only allow labeling on actual images (not "nearest" ones)
            if layer and layer_name and not layer_name.startswith("~"):
                if layer.geo:
                    lon, lat = self._web_mercator_to_wgs84(easting, northing)
                    pixel_x, pixel_y = layer.latlon_to_pixel(lon, lat)
                else:
                    # Raw/non-geo display (incl. waterfall): scene coords map
                    # directly to pixels. If the file carries georeferencing,
                    # derive the true lat/lon from the clicked pixel; plain
                    # images have no meaningful lat/lon.
                    pixel_x, pixel_y = layer.scene_to_pixel(easting, northing)
                    latlon = layer.pixel_to_latlon(pixel_x, pixel_y)
                    lon, lat = latlon if latlon is not None else (0.0, 0.0)
                self.label_placed.emit(
                    pixel_x,
                    pixel_y,
                    lon,
                    lat,
                    layer_name,
                    group_path,
                    layer.file_path)
        elif self._mode in LABELING_MODES and event.button() == Qt.RightButton:
            # Right button in any labeling mode (Label + Cycle): a right-click
            # without dragging opens the label context menu; a right-drag pans.
            # We can't tell which yet, so begin a potential pan and decide on
            # release based on whether the cursor moved (see mouseReleaseEvent).
            if self._link_mode_active:
                self._exit_link_mode()
            else:
                self._cycle_panning = True
                self._cycle_pan_start = event.pos()
                self._cycle_pan_moved = False
                self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        # Measure mode consumes clicks in mousePressEvent; swallow the matching
        # release so it can't reach pan/cycle release handling.
        if self._measure_active:
            return
        # Finish a Shift+drag measurement started from another mode, before that
        # mode's own release handling can act on the same click.
        if self._ruler_dragging and event.button() == Qt.LeftButton:
            self._ruler_dragging = False
            if self._mode != CanvasMode.RULER:
                return
        # Ruler mode: end left-drag measurement or right-drag panning.
        if self._mode == CanvasMode.RULER:
            if event.button() == Qt.LeftButton:
                self._ruler_dragging = False
            elif event.button() == Qt.RightButton and self._ruler_panning:
                self._ruler_panning = False
                self.setCursor(_crosshair_cursor())
            return
        if self._mode == CanvasMode.PAN and event.button() == Qt.LeftButton:
            if hasattr(self, '_pan_active') and self._pan_active:
                self._pan_active = False
                self.setCursor(Qt.OpenHandCursor)
        elif self._mode in LABELING_MODES and event.button() == Qt.RightButton:
            if getattr(self, '_cycle_panning', False):
                self._cycle_panning = False
                self.setCursor(_crosshair_cursor())
                if not getattr(self, '_cycle_pan_moved', False):
                    # A right-click without dragging: open the label context menu
                    # (consistent across Label and Cycle modes).
                    self._show_label_context_menu(event.pos())
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Track mouse position and emit lat/lon coordinates."""
        self._last_mouse_view_pos = event.pos()

        # Measure mode: stretch the rubber-band line to the cursor. Fall through
        # so the coordinate readout still updates.
        if self._measure_active and self._measure_start is not None:
            self._update_measure_preview(event.pos())

        # Update the measurement line + readout while dragging, whether the
        # ruler was reached by its mode or by Shift+drag.
        if self._ruler_dragging:
            self._ruler_update(event.pos())

        # Ruler mode: right-drag pans the view (like cycle mode).
        if self._mode == CanvasMode.RULER and self._ruler_panning:
            delta = event.pos() - self._ruler_pan_start
            self._ruler_pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())

        # Handle PAN mode left-click panning
        if self._mode == CanvasMode.PAN and hasattr(
                self, '_pan_active') and self._pan_active:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            # Still update coordinates below

        # Handle right-click panning in labeling modes (Label + Cycle). Only
        # starts panning once the cursor has moved past a small threshold, so a
        # stationary right-click stays a click (opens the context menu on
        # release) instead of nudging the view.
        if self._mode in LABELING_MODES and getattr(
                self, '_cycle_panning', False):
            if not self._cycle_pan_moved:
                d = event.pos() - self._cycle_pan_start
                if abs(d.x()) + abs(d.y()) >= self._RIGHT_DRAG_PIXELS:
                    # Crossed the drag threshold: reset the origin here so the
                    # pan doesn't jump by the threshold distance.
                    self._cycle_pan_moved = True
                    self._cycle_pan_start = event.pos()
            if self._cycle_pan_moved:
                delta = event.pos() - self._cycle_pan_start
                self._cycle_pan_start = event.pos()
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y())
            # Still update coordinates below

        scene_pos = self.mapToScene(event.pos())
        easting, northing = self._scene_to_web(scene_pos)

        # One lookup, not two: this runs on every mouse move, and each of the
        # old calls scanned every layer independently.
        layer, layer_name, group_path = self._layer_and_name_at(
            easting, northing)

        if self.is_in_pixel_zone(easting):
            if layer is not None and not layer.geo:
                px, py = layer.scene_to_pixel(easting, northing)
                self._queue_coords_emit(px, py, layer_name, group_path, True)
            else:
                self._queue_coords_emit(0.0, 0.0, layer_name, group_path, True)
        else:
            lon, lat = self._web_mercator_to_wgs84(easting, northing)
            self._queue_coords_emit(lon, lat, layer_name, group_path, False)

    def _queue_coords_emit(self, x: float, y: float, layer_name: str,
                           group_path: str, is_pixel: bool):
        """Coalesce coordinates_changed emissions from rapid mouse movement.

        Stores the latest payload and starts a short timer; only the most
        recent payload is emitted, and only if it differs from the previously
        emitted one (rounded to display precision).
        """
        # Round numeric coords to the precision the status bar actually shows
        # so micro-movements don't trigger redundant UI updates.
        if is_pixel:
            rounded = (round(x, 1), round(y, 1))
        else:
            rounded = (round(x, 6), round(y, 6))
        payload = (rounded[0], rounded[1], layer_name, group_path, is_pixel)
        self._pending_coords = payload
        if not self._coords_emit_timer.isActive():
            self._coords_emit_timer.start()

    def _flush_pending_coords(self):
        """Emit the pending coordinates payload if it changed since last emit."""
        payload = self._pending_coords
        if payload is None or payload == self._last_emitted_coords:
            return
        self._last_emitted_coords = payload
        self.coordinates_changed.emit(*payload)

    def keyPressEvent(self, event):
        """Handle key press events."""
        if event.key() == Qt.Key_Escape and self._measure_active:
            self._exit_measure_mode()
        elif event.key() == Qt.Key_M and not self._measure_active:
            # Start measuring the label under the cursor.
            pos = self._last_mouse_view_pos
            label_id = None
            if pos is not None:
                label_id, _ = self._get_label_at_position(pos)
            if label_id is not None:
                self._enter_measure_mode(label_id)
            else:
                self.measure_mode_changed.emit(
                    False, "Hover over a label, then press M to measure")
        elif event.key() == Qt.Key_Escape and self._link_mode_active:
            self._exit_link_mode()
        elif event.key() == Qt.Key_N and self._chain_link_active:
            # Close the current chain; next click anchors a new one.
            self.chain_link_new_chain()
        elif event.key() == Qt.Key_Escape and self._chain_link_active:
            self.set_chain_link_mode(False)
        elif event.key() == Qt.Key_Escape and (
                self._ruler_line is not None
                or self._location_marker is not None):
            # One press clears the transient overlays: a measurement (from
            # ruler mode or Shift+drag) and the go-to crosshair.
            self._clear_ruler()
            self.clear_location_marker()
        elif event.key() == Qt.Key_Space and self._mode == CanvasMode.WATERFALL:
            # Hold Space to glide the view up the stack, Ctrl+Space to glide
            # back down. Ignore auto-repeat so the glide runs continuously from
            # physical press to release.
            if not event.isAutoRepeat():
                direction = 1 if (event.modifiers() & Qt.ControlModifier) else -1
                self.start_waterfall_glide(direction)
        elif event.key() == Qt.Key_Space and self._mode in STEP_CYCLE_MODES:
            if event.modifiers() & Qt.ControlModifier:
                # Ctrl+Space: go backwards
                self.cycle_prev_requested.emit()
            else:
                # Space: go forwards
                self.cycle_next_requested.emit()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """Stop the waterfall glide when the Space key is physically released."""
        if (self._mode == CanvasMode.WATERFALL
                and event.key() == Qt.Key_Space
                and not event.isAutoRepeat()):
            self.stop_waterfall_glide()
        else:
            super().keyReleaseEvent(event)

    def _web_mercator_to_wgs84(
            self, x: float, y: float) -> tuple[float, float]:
        """Convert Web Mercator (EPSG:3857) to WGS84 (EPSG:4326)."""
        R = 6378137.0
        lon = math.degrees(x / R)
        lat = math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
        return lon, lat

    def _wgs84_to_web_mercator(
            self, lon: float, lat: float) -> tuple[float, float]:
        """Convert WGS84 (EPSG:4326) to Web Mercator (EPSG:3857)."""
        R = 6378137.0
        x = math.radians(lon) * R
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
        return x, y

    def _get_layer_at_position(
            self, easting: float, northing: float) -> tuple[str, str]:
        """Get the name and group of the layer at the given position.

        First checks if cursor is within any layer bounds (returns topmost visible layer).
        If not within any bounds, returns the layer whose center is closest.

        Returns:
            Tuple of (layer_name, group_path). Layer name prefixed with ~ if showing nearest.
        """
        # Check layers in reverse z-order (top to bottom)
        layers_in_bounds = []
        for layer_id in reversed(self._layer_order):
            if layer_id not in self._layers:
                continue
            layer = self._layers[layer_id]
            if layer.visible and layer.contains_point(easting, northing):
                layers_in_bounds.append(layer)

        # If cursor is within one or more layers, return the topmost one
        if layers_in_bounds:
            return (layers_in_bounds[0].name, layers_in_bounds[0].group_path)

        # Otherwise, find the layer with closest center
        closest_layer = None
        min_distance = float('inf')

        for layer_id, layer in self._layers.items():
            if not layer.visible:
                continue
            dist = layer.distance_to_center(easting, northing)
            if dist < min_distance:
                min_distance = dist
                closest_layer = layer

        if closest_layer:
            # Prefix with ~ to indicate "closest to"
            return (f"~{closest_layer.name}", closest_layer.group_path)

        return ("", "")

    def _layer_and_name_at(self, easting: float, northing: float):
        """``(layer, name, group)`` under a position, in a single pass.

        The layer is the topmost visible one actually containing the point, or
        None. The name falls back to the nearest layer's, prefixed with '~',
        so the readout still says roughly where the cursor is over blank space.

        Combining the two lookups matters because this runs on every mouse
        move: asking for the layer and the name separately scanned every layer
        twice, and three times over empty ground.
        """
        layer, name, group = self._get_layer_and_info_at_position(
            easting, northing)
        if layer is not None:
            return layer, name, group
        # Nothing contains the point - fall back to the nearest layer's name.
        closest, min_distance = None, float("inf")
        for candidate in self._layers.values():
            if not candidate.visible:
                continue
            distance = candidate.distance_to_center(easting, northing)
            if distance < min_distance:
                min_distance, closest = distance, candidate
        if closest is not None:
            return None, f"~{closest.name}", closest.group_path
        return None, "", ""

    def set_hard_negative_flag(self, file_path: str, flagged: bool):
        """Mirror an image's hard-negative-source flag for the context menu."""
        if flagged:
            self._hard_negative_paths.add(file_path)
        else:
            self._hard_negative_paths.discard(file_path)

    def _layer_id_at(self, easting: float, northing: float) -> str | None:
        """The topmost visible layer under a position, or None.

        Unlike _get_layer_and_info_at_position this never falls back to the
        nearest layer: a context-menu toggle must apply to the image actually
        under the cursor, not to whichever happens to be closest.
        """
        for layer_id in reversed(self._layer_order):
            layer = self._layers.get(layer_id)
            if (layer is not None and layer.visible
                    and layer.contains_point(easting, northing)):
                return layer_id
        return None

    def _get_layer_and_info_at_position(
            self, easting: float, northing: float) -> tuple:
        """Get the layer object and its info at the given position.

        Returns:
            Tuple of (layer, layer_name, group_path). Layer is None if not found.
            Layer name prefixed with ~ if showing nearest.
        """
        # Check layers in reverse z-order (top to bottom)
        for layer_id in reversed(self._layer_order):
            if layer_id not in self._layers:
                continue
            layer = self._layers[layer_id]
            if layer.visible and layer.contains_point(easting, northing):
                return (layer, layer.name, layer.group_path)

        # Not within any layer bounds
        return (None, "", "")

    def _get_layer_by_name_and_group(self, name: str, group_path: str):
        """Find a layer by its name and group path."""
        for layer in self._layers.values():
            if layer.name == name and layer.group_path == group_path:
                return layer
        return None

    def add_label_marker(self, label_id: int, lon: float, lat: float,
                         image_name: str, image_group: str, image_path: str,
                         class_name: str, color: QColor = None,
                         pixel_x: float = None, pixel_y: float = None):
        """Add a visual marker for a label on the canvas.

        Args:
            label_id: Unique ID of the label
            lon: Longitude (WGS84) — used for geo layers
            lat: Latitude (WGS84) — used for geo layers
            image_name: Name of the image the label belongs to
            image_group: Group path of the image
            image_path: Full file path of the image
            class_name: Class name to display
            color: Optional color for the marker
            pixel_x: Pixel X coord — for non-geo layers, used to position marker
            pixel_y: Pixel Y coord — for non-geo layers, used to position marker
        """
        if color is None:
            color = QColor(255, 50, 50)  # Default red

        # Determine scene position based on whether layer is georeferenced
        layer = self._get_layer_by_name_and_group(image_name, image_group)
        if layer and not layer.geo and pixel_x is not None and pixel_y is not None:
            # Non-geo layer: compute scene position from pixel coords
            # pixel_y=0 is top of image (north), increasing downward
            west, _, _, north = layer.bounds
            x = west + pixel_x * PIXEL_ZONE_SCALE
            y = north - pixel_y * PIXEL_ZONE_SCALE
        else:
            # Geo layer: convert lat/lon to Web Mercator
            x, y = self._wgs84_to_web_mercator(lon, lat)

        ellipse, text = self._make_label_marker_items(
            x, y, class_name, color, image_path)
        self._label_items[label_id] = (ellipse, text)

    def _make_label_marker_items(self, x: float, y: float, class_name: str,
                                 color: QColor, image_path: str):
        """Create the (ellipse, text) items of a label marker at world (x, y).

        Shared by real label markers and waterfall projections so both render
        identically. Items are parented to the floating-origin group.
        """
        # Get current view scale to size markers appropriately
        view_scale = self._view_scale()

        # Marker size in scene coordinates (appears ~10 pixels on screen)
        marker_size = 10 / view_scale if view_scale > 0 else 10

        # Create ellipse marker
        ellipse = QGraphicsEllipseItem(
            -marker_size / 2, -marker_size / 2,
            marker_size, marker_size
        )
        ellipse.setPos(x, -y)  # Y is flipped in scene coords
        ellipse.setPen(QPen(color.darker(150), marker_size / 5))
        ellipse.setBrush(QBrush(color))
        ellipse.setZValue(self._get_label_z_base())
        ellipse.setData(0, image_path)  # Store image_path for later retrieval

        # Create text label
        text = QGraphicsTextItem(class_name)
        text.setData(0, class_name)  # base label text, for measurement relabeling
        text.setDefaultTextColor(Qt.white)
        font = QFont("Arial", 8)
        font.setBold(True)
        text.setFont(font)
        # Ignore the view transform so the label stays upright and a constant
        # on-screen size regardless of zoom.
        text.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        text.setPos(x + marker_size / 2, -y - marker_size / 2)
        text.setZValue(self._get_label_z_base() + 1)

        # Parent to the floating-origin group so scene coords stay small at
        # deep zoom (positions above are in world coords).
        ellipse.setParentItem(self._overlay_group)
        text.setParentItem(self._overlay_group)
        return ellipse, text

    def set_waterfall_projections(self, label_infos: list):
        """Display labels on every stacked image whose bounds contain them.

        In the normal (geographic) canvas a label sits at one lat/lon, so it
        naturally appears "on" every overlapping image. The waterfall pulls
        those images apart vertically, so this restores the effect: each label
        with a real lat/lon is drawn on every OTHER stacked, georeferenced
        image whose pixel bounds contain that position - rendered exactly like
        a normal label and hit-testable as its source label (so the standard
        context menu / link flow applies).

        label_infos: list of (label_id, lon, lat, class_name, color,
        source_image_path) tuples.
        """
        self.clear_waterfall_projections()
        if not self._waterfall_active:
            return
        for label_id, lon, lat, class_name, color, source_path in label_infos:
            for layer_id in self._waterfall_layer_order:
                layer = self._layers.get(layer_id)
                if (layer is None or layer.bounds is None
                        or layer._src_crs is None
                        or layer.file_path == source_path):
                    continue
                try:
                    px, py = layer.latlon_to_pixel(lon, lat)
                except Exception:
                    continue
                if not (0 <= px < layer._src_width
                        and 0 <= py < layer._src_height):
                    continue  # position not within this image
                west, _south, _east, north = layer.bounds
                x = west + px * PIXEL_ZONE_SCALE
                y = north - py * PIXEL_ZONE_SCALE
                # data(0) carries the SOURCE image path so context-menu actions
                # (remove, link, measure) operate on the real label.
                ellipse, text = self._make_label_marker_items(
                    x, y, class_name,
                    color if color is not None else QColor(255, 50, 50),
                    source_path)
                self._waterfall_projection_items.append(
                    (label_id, ellipse, text))

    def clear_waterfall_projections(self):
        """Remove all projected label markers."""
        for _label_id, ellipse, text in self._waterfall_projection_items:
            self._scene.removeItem(ellipse)
            self._scene.removeItem(text)
        self._waterfall_projection_items = []

    def remove_label_marker(self, label_id: int):
        """Remove a label marker from the canvas."""
        if label_id in self._label_items:
            ellipse, text = self._label_items[label_id]
            self._scene.removeItem(ellipse)
            self._scene.removeItem(text)
            del self._label_items[label_id]
        # A removed chain anchor can't take more links; the next chain-link
        # click anchors a fresh chain.
        if self._chain_link_active and label_id == self._chain_link_anchor:
            self._chain_link_anchor = None

    def clear_label_markers(self):
        """Remove all label markers from the canvas."""
        for label_id in list(self._label_items.keys()):
            self.remove_label_marker(label_id)

    def _set_label_visibility_for_image(self, image_path: str, visible: bool):
        """Show or hide all label markers belonging to a specific image."""
        for ellipse, text in self._label_items.values():
            if ellipse.data(0) == image_path:
                ellipse.setVisible(visible)
                text.setVisible(visible)

    def update_label_markers_scale(self):
        """Update label marker sizes based on current zoom level."""
        view_scale = self._view_scale()
        if view_scale <= 0:
            return

        marker_size = 10 / view_scale

        markers = list(self._label_items.values())
        # Waterfall projections rescale exactly like real label markers.
        markers.extend((ellipse, text) for _lid, ellipse, text
                       in self._waterfall_projection_items)
        for ellipse, text in markers:
            # Update ellipse size
            ellipse.setRect(
                -marker_size / 2, -marker_size / 2,
                marker_size, marker_size
            )
            pen = ellipse.pen()
            pen.setWidthF(marker_size / 5)
            ellipse.setPen(pen)

            # Text ignores the view transform (constant size / upright), so it
            # only needs repositioning as the marker size changes.
            pos = ellipse.pos()
            text.setPos(pos.x() + marker_size / 2, pos.y() - marker_size / 2)

        # Keep the ruler readout a constant on-screen size across zoom changes.
        self._rescale_ruler_text()

    def _get_label_at_position(
            self, view_pos) -> tuple[int | None, str | None]:
        """Find the label at the given view position.

        Returns:
            Tuple of (label_id, image_path) or (None, None) if no label found.
        """
        scene_pos = self.mapToScene(view_pos)

        # Check each label marker
        for label_id, (ellipse, text) in self._label_items.items():
            # Bounding rect in scene coordinates (sceneBoundingRect accounts for
            # the floating-origin parent transform).
            scene_rect = ellipse.sceneBoundingRect()

            # Expand hit area slightly for easier clicking
            hit_margin = scene_rect.width() * 0.5
            scene_rect.adjust(-hit_margin, -hit_margin, hit_margin, hit_margin)

            if scene_rect.contains(scene_pos):
                # Found a label - now find the image_path
                # We need to look up which image this label belongs to
                # The label stores its position in scene coords, we need to find
                # which layer it's on based on the stored data
                # We'll store image_path in the ellipse
                image_path = ellipse.data(0)
                return label_id, image_path

        # Waterfall projections hit-test as their source label, so the normal
        # context menu / link flow works on them transparently.
        for label_id, ellipse, _text in self._waterfall_projection_items:
            scene_rect = ellipse.sceneBoundingRect()
            hit_margin = scene_rect.width() * 0.5
            scene_rect.adjust(-hit_margin, -hit_margin, hit_margin, hit_margin)
            if scene_rect.contains(scene_pos):
                return label_id, ellipse.data(0)

        return None, None

    def _show_label_context_menu(self, view_pos):
        """Show context menu for label under cursor.

        With no label under the cursor this falls through to the general
        menu, so the waypoint and hard-negative actions are reachable in the
        labelling modes too - a right click that used to do nothing.
        """
        label_id, image_path = self._get_label_at_position(view_pos)
        if label_id is None:
            self._show_pan_context_menu(view_pos)
            return

        if label_id is not None:
            menu = QMenu(self)

            # Link option - always available
            link_action = menu.addAction("Link with...")

            # Measure length/width - only meaningful for georeferenced images
            measure_action = menu.addAction("Measure Length / Width")

            # Check if label is linked (data slot 1 stores True if linked to
            # others)
            ellipse, _ = self._label_items.get(label_id, (None, None))
            is_linked = ellipse and ellipse.data(1)

            # Clear measurements - only if this label has been measured
            # (data slot 4 stores True when length/width are set).
            clear_measure_action = None
            if ellipse and ellipse.data(4):
                clear_measure_action = menu.addAction("Clear Measurements")

            # Unlink and Show linked options (only if label is linked to
            # others)
            unlink_action = None
            show_linked_action = None
            if is_linked:
                unlink_action = menu.addAction("Unlink")
                show_linked_action = menu.addAction("Show Linked")

            menu.addSeparator()

            describe_action = menu.addAction("Description...")
            group_id_action = menu.addAction("Group ID...")

            # Toggle layer visibility option
            toggle_layer_action = menu.addAction("Toggle Image Visibility")

            menu.addSeparator()
            remove_action = menu.addAction("Remove Label")

            action = menu.exec_(self.mapToGlobal(view_pos))

            if action == remove_action:
                self.label_removed.emit(label_id, image_path)
            elif action == link_action:
                self._enter_link_mode(label_id)
            elif action == measure_action:
                self._enter_measure_mode(label_id)
            elif clear_measure_action is not None and action == clear_measure_action:
                # Clearing is routed through the same signal; main_window
                # resets length_m/width_m and calls set_label_measured(False).
                self.label_measured.emit(label_id, None, None)
            elif action == describe_action:
                self.label_describe_requested.emit(label_id)
            elif action == group_id_action:
                self.label_group_id_requested.emit(label_id)
            elif action == unlink_action:
                self.label_unlinked.emit(label_id)
            elif action == show_linked_action:
                self.show_linked_requested.emit(label_id)
            elif action == toggle_layer_action:
                # Get the layer_id from the image_path and emit toggle signal
                if image_path in self._path_to_layer:
                    layer_id = self._path_to_layer[image_path]
                    self.toggle_layer_visibility_requested.emit(layer_id)

    def _show_waypoint_context_menu(self, view_pos, waypoint_id: int):
        """Context menu for a waypoint marker under the cursor."""
        menu = QMenu(self)
        goto_action = menu.addAction("Go to Waypoint")
        rename_action = menu.addAction("Rename Waypoint...")
        menu.addSeparator()
        remove_action = menu.addAction("Remove Waypoint")

        action = menu.exec_(self.mapToGlobal(view_pos))
        if action == goto_action:
            self.waypoint_goto_requested.emit(waypoint_id)
        elif action == rename_action:
            self.waypoint_rename_requested.emit(waypoint_id)
        elif action == remove_action:
            self.waypoint_remove_requested.emit(waypoint_id)

    def _show_pan_context_menu(self, view_pos):
        """Show context menu for pan mode.

        A waypoint under the cursor takes precedence and gets its own menu,
        mirroring how a label marker claims the right click in labeling modes.
        """
        waypoint_id = self._waypoint_at_position(view_pos)
        if waypoint_id is not None:
            self._show_waypoint_context_menu(view_pos, waypoint_id)
            return

        menu = QMenu(self)

        # Waypoints are geographic, so the pixel zone (non-georeferenced
        # images) has no coordinate to record - offer the action greyed out
        # with the reason rather than silently storing a meaningless position.
        easting, northing = self._scene_to_web(self.mapToScene(view_pos))
        in_pixel_zone = self.is_in_pixel_zone(easting)
        if in_pixel_zone:
            add_waypoint_action = menu.addAction(
                "Add waypoint here (needs a georeferenced location)")
            add_waypoint_action.setEnabled(False)
        else:
            add_waypoint_action = menu.addAction("Add waypoint here")
        menu.addSeparator()

        show_in_view_action = menu.addAction("Select layers in view")
        hide_outside_action = menu.addAction("Unselect layers outside view")

        # Actions on the topmost image under the cursor: hide it (so the
        # ones stacked behind it become visible), or flip its hard-negative
        # -source flag ("this image holds confusers but no true positives" -
        # the H5 export can then slide it into gt=False negatives on request).
        unselect_action = None
        hn_action = None
        hit_layer_id = self._layer_id_at(easting, northing)
        if hit_layer_id is not None:
            layer = self._layers[hit_layer_id]
            menu.addSeparator()
            unselect_action = menu.addAction(
                f"Unselect layer  ({layer.name})")
            hn_action = menu.addAction(
                f"Hard negative source  ({layer.name})")
            hn_action.setCheckable(True)
            hn_action.setChecked(layer.file_path in self._hard_negative_paths)

        action = menu.exec_(self.mapToGlobal(view_pos))

        if action == add_waypoint_action and not in_pixel_zone:
            lon, lat = self._web_mercator_to_wgs84(easting, northing)
            self.waypoint_add_requested.emit(lon, lat)
        elif unselect_action is not None and action == unselect_action:
            self.layer_unselect_requested.emit(hit_layer_id)
        elif hn_action is not None and action == hn_action:
            self.hard_negative_toggle_requested.emit(hit_layer_id)
        elif action == show_in_view_action:
            self._show_layers_in_view()
        elif action == hide_outside_action:
            self._hide_layers_outside_view()

    def _hide_layers_outside_view(self):
        """Find layers that don't intersect the current view and emit signal to hide them."""
        view_bounds = self._get_view_bounds()
        view_west, view_south, view_east, view_north = view_bounds

        layers_to_hide = []

        for layer_id, layer in self._layers.items():
            if layer.bounds is None:
                continue

            layer_west, layer_south, layer_east, layer_north = layer.bounds

            # Check if layer bounds intersect with view bounds
            intersects = not (
                layer_east < view_west or   # layer is entirely to the left
                layer_west > view_east or   # layer is entirely to the right
                layer_north < view_south or  # layer is entirely below
                layer_south > view_north    # layer is entirely above
            )

            if not intersects:
                layers_to_hide.append(layer_id)

        if layers_to_hide:
            self.hide_layers_outside_view.emit(layers_to_hide)

    def _show_layers_in_view(self):
        """Find layers that intersect the current view and emit signal to show them."""
        view_bounds = self._get_view_bounds()
        view_west, view_south, view_east, view_north = view_bounds

        layers_to_show = []

        for layer_id, layer in self._layers.items():
            if layer.bounds is None:
                continue

            layer_west, layer_south, layer_east, layer_north = layer.bounds

            # Check if layer bounds intersect with view bounds
            intersects = not (
                layer_east < view_west or   # layer is entirely to the left
                layer_west > view_east or   # layer is entirely to the right
                layer_north < view_south or  # layer is entirely below
                layer_south > view_north    # layer is entirely above
            )

            if intersects:
                layers_to_show.append(layer_id)

        if layers_to_show:
            self.show_layers_in_view.emit(layers_to_show)

    def get_layers_in_view(self) -> list[str]:
        """Get layer IDs whose bounds intersect the current view.

        Returns:
            List of layer_ids that overlap with the visible viewport.
        """
        view_bounds = self._get_view_bounds()
        view_west, view_south, view_east, view_north = view_bounds

        result = []
        for layer_id, layer in self._layers.items():
            if layer.bounds is None:
                continue
            layer_west, layer_south, layer_east, layer_north = layer.bounds
            intersects = not (
                layer_east < view_west or
                layer_west > view_east or
                layer_north < view_south or
                layer_south > view_north
            )
            if intersects:
                result.append(layer_id)
        return result

    def _enter_link_mode(self, source_label_id: int):
        """Enter link mode with the given label as the source."""
        self._link_mode_active = True
        self._link_source_label_id = source_label_id
        self.setCursor(_crosshair_cursor())

        # Highlight the source label
        if source_label_id in self._label_items:
            ellipse, _ = self._label_items[source_label_id]
            # Store original pen in data slot 2
            ellipse.setData(2, ellipse.pen())
            highlight_pen = QPen(
                QColor(
                    255,
                    255,
                    0),
                ellipse.pen().widthF() *
                2)
            ellipse.setPen(highlight_pen)

        self.link_mode_changed.emit(
            True, "Link mode: Click another label to link, or right-click/Escape to cancel")

    def _exit_link_mode(self):
        """Exit link mode and restore normal state."""
        # Restore source label appearance
        if self._link_source_label_id and self._link_source_label_id in self._label_items:
            ellipse, _ = self._label_items[self._link_source_label_id]
            original_pen = ellipse.data(2)
            if original_pen:
                ellipse.setPen(original_pen)

        self._link_mode_active = False
        self._link_source_label_id = None

        # Restore cursor based on mode
        if self._mode in LABELING_MODES:
            self.setCursor(_crosshair_cursor())
        else:
            self.setCursor(Qt.ArrowCursor)

        self.link_mode_changed.emit(False, "")

    def set_chain_link_mode(self, active: bool):
        """Enter or leave chain-link mode.

        While active, left-clicking labels links them all into one object
        (links are created immediately, click by click, so there is nothing to
        commit or lose). Works in any labeling mode, including on waterfall
        projections. Pressing N starts a new chain.
        """
        if active == self._chain_link_active:
            return
        if active:
            # Chain mode takes over the mouse: end the single-pair link mode
            # and any measurement first.
            if self._link_mode_active:
                self._exit_link_mode()
            if self._measure_active:
                self._exit_measure_mode()
            self._chain_link_active = True
            self._chain_link_anchor = None
            self._chain_members = set()
            self.setCursor(_crosshair_cursor())
            self.chain_link_changed.emit(
                True, "Chain link: click a label to anchor a chain - "
                      "N = new chain, Esc = done")
        else:
            self._chain_link_active = False
            self._chain_link_anchor = None
            self._chain_members = set()
            self._chain_restore_highlights()
            if self._mode in LABELING_MODES:
                self.setCursor(_crosshair_cursor())
            elif self._mode == CanvasMode.PAN:
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            self.chain_link_changed.emit(False, "")

    def chain_link_new_chain(self):
        """Close the current chain; the next label clicked anchors a new one."""
        if not self._chain_link_active:
            return
        self._chain_restore_highlights()
        self._chain_link_anchor = None
        self._chain_members = set()
        self.chain_link_changed.emit(
            True, "Chain link: new chain - click a label to anchor it")

    def _chain_link_click(self, label_id: int):
        """Handle a click on a label while chain-link mode is active."""
        # First click (or the anchor was removed): anchor a new chain.
        if (self._chain_link_anchor is None
                or self._chain_link_anchor not in self._label_items):
            self._chain_link_anchor = label_id
            self._chain_members = {label_id}
            self._chain_highlight_label(label_id)
            self.chain_link_changed.emit(
                True, "Chain link: anchored - click labels to link them, "
                      "N = new chain, Esc = done")
            return
        if label_id in self._chain_members:
            return  # already part of this chain
        self._chain_members.add(label_id)
        self._chain_highlight_label(label_id)
        # Link immediately; project-side handling merges object groups and
        # updates linked indicators (and measurement wiring) right away.
        self.labels_linked.emit(self._chain_link_anchor, label_id)
        self.chain_link_changed.emit(
            True, f"Chain link: {len(self._chain_members)} labels in this "
                  "chain - N = new chain, Esc = done")

    _CHAIN_HIGHLIGHT_COLOR = QColor(255, 255, 0)

    def _chain_highlight_label(self, label_id: int):
        """Yellow-highlight a chained label (and any waterfall projections)."""
        items = []
        if label_id in self._label_items:
            items.append(self._label_items[label_id][0])
        for lid, ellipse, _text in self._waterfall_projection_items:
            if lid == label_id:
                items.append(ellipse)
        for ellipse in items:
            pen = ellipse.pen()
            if pen.color() == self._CHAIN_HIGHLIGHT_COLOR:
                continue  # already highlighted
            self._chain_highlighted.append((ellipse, QPen(pen)))
            ellipse.setPen(QPen(self._CHAIN_HIGHLIGHT_COLOR,
                                pen.widthF() * 2))

    def _chain_restore_highlights(self):
        """Undo the chain highlights, preserving pens changed in the meantime
        (e.g. a measured label's cyan outline set while the chain was open)."""
        for ellipse, pen in self._chain_highlighted:
            try:
                if ellipse.pen().color() == self._CHAIN_HIGHLIGHT_COLOR:
                    ellipse.setPen(pen)
            except RuntimeError:
                pass  # item was deleted with its label
        self._chain_highlighted = []

    # ------------------------------------------------------------------
    # Measure mode: draw two lines on a label to record length + width (m)
    # ------------------------------------------------------------------

    def _measure_target_layer(self, label_id: int) -> TiledLayer | None:
        """Return the TiledLayer a label belongs to, via its stored image path."""
        if label_id not in self._label_items:
            return None
        ellipse, _ = self._label_items[label_id]
        image_path = ellipse.data(0)  # image_path stored in slot 0 at creation
        layer_id = self._path_to_layer.get(image_path) if image_path else None
        return self._layers.get(layer_id) if layer_id else None

    def _enter_measure_mode(self, label_id: int):
        """Begin drawing length/width measurement lines for a label.

        Measurement is only supported on georeferenced layers (metres are
        undefined in the pixel zone), so entry is refused for non-geo labels.
        """
        layer = self._measure_target_layer(label_id)
        if layer is None or not layer.geo:
            self.measure_mode_changed.emit(
                False, "Measurements need a georeferenced image")
            return

        # Cancel any in-progress link mode before taking over the mouse.
        if self._link_mode_active:
            self._exit_link_mode()

        self._measure_active = True
        self._measure_label_id = label_id
        self._measure_stage = MeasureStage.LENGTH
        self._measure_start = None
        self._measure_length_m = None
        self.setCursor(_crosshair_cursor())
        self.measure_mode_changed.emit(
            True, "Measure LENGTH: click start, then end (Esc to cancel)")

    def _handle_measure_click(self, view_pos):
        """Handle a left click while in measure mode (line start, then end)."""
        scene_pos = self.mapToScene(view_pos)

        if self._measure_start is None:
            # First click of this line: anchor it and start the rubber band.
            self._measure_start = scene_pos
            self._measure_start_view = view_pos
            self._ensure_measure_temp_line()
            return

        # Reject an accidental click too close to the start (in screen pixels),
        # which would otherwise record a bogus near-zero line.
        if self._measure_start_view is not None:
            dx = view_pos.x() - self._measure_start_view.x()
            dy = view_pos.y() - self._measure_start_view.y()
            if (dx * dx + dy * dy) ** 0.5 < self._MIN_MEASURE_PIXELS:
                return

        # Second click: finalise the current line.
        dist_m = self._line_distance_m(self._measure_start, scene_pos)
        if dist_m is None or dist_m <= 0:
            # Degenerate (zero-length) line - ignore and let the user retry.
            return

        if self._measure_stage == MeasureStage.LENGTH:
            self._measure_length_m = dist_m
            self._promote_temp_to_committed(scene_pos)
            self._measure_stage = MeasureStage.WIDTH
            self._measure_start = None
            self._measure_start_view = None
            self.measure_mode_changed.emit(
                True, "Measure WIDTH: click start, then end (Esc to cancel)")
        else:
            width_m = dist_m
            length_m = self._measure_length_m
            label_id = self._measure_label_id
            self._exit_measure_mode()
            if label_id is not None:
                self.label_measured.emit(label_id, length_m, width_m)

    def _update_measure_preview(self, view_pos):
        """Stretch the rubber-band line to the cursor and show a live readout."""
        if self._measure_temp_line is None or self._measure_start is None:
            return
        scene_pos = self.mapToScene(view_pos)
        self._measure_temp_line.setLine(QLineF(self._measure_start, scene_pos))

        dist_m = self._line_distance_m(self._measure_start, scene_pos)
        stage = ("LENGTH" if self._measure_stage == MeasureStage.LENGTH
                 else "WIDTH")
        if dist_m is not None:
            self.measure_mode_changed.emit(
                True, f"Measure {stage}: {dist_m:.2f} m "
                      "(click to set, Esc to cancel)")

    def _ensure_measure_temp_line(self):
        """Create the rubber-band line item for the line being drawn."""
        if self._measure_temp_line is not None:
            return
        pen = QPen(QColor(0, 200, 255), 0)
        pen.setCosmetic(True)  # constant ~1px width regardless of zoom
        line = QGraphicsLineItem(
            QLineF(self._measure_start, self._measure_start))
        line.setPen(pen)
        line.setZValue(self._get_label_z_base() + 2)
        self._scene.addItem(line)
        self._measure_temp_line = line

    def _promote_temp_to_committed(self, end_scene_pos):
        """Freeze the finished length line on screen (dimmed) while width is drawn."""
        if self._measure_temp_line is None:
            return
        self._measure_temp_line.setLine(
            QLineF(self._measure_start, end_scene_pos))
        pen = QPen(QColor(0, 200, 255, 120), 0)
        pen.setCosmetic(True)
        self._measure_temp_line.setPen(pen)
        self._measure_committed_line = self._measure_temp_line
        self._measure_temp_line = None

    def _line_distance_m(self, start_scene, end_scene) -> float | None:
        """Geodesic length in metres of a line between two scene points.

        Scene coordinates are Web Mercator metres (scene Y = -northing). Both
        endpoints are converted to WGS84 and measured with the Haversine
        formula so the result is a true ground distance rather than the
        latitude-inflated planar Web Mercator distance.
        """
        e1, n1 = self._scene_to_web(start_scene)
        e2, n2 = self._scene_to_web(end_scene)
        lon1, lat1 = self._web_mercator_to_wgs84(e1, n1)
        lon2, lat2 = self._web_mercator_to_wgs84(e2, n2)
        return haversine_distance(lat1, lon1, lat2, lon2)

    def _exit_measure_mode(self):
        """Leave measure mode, removing any in-progress/committed line items."""
        for item in (self._measure_temp_line, self._measure_committed_line):
            if item is not None:
                self._scene.removeItem(item)
        self._measure_temp_line = None
        self._measure_committed_line = None
        self._measure_start_view = None
        self._measure_active = False
        self._measure_label_id = None
        self._measure_start = None
        self._measure_stage = MeasureStage.LENGTH
        self._measure_length_m = None

        # Restore the cursor for the underlying interaction mode.
        if self._mode in LABELING_MODES:
            self.setCursor(_crosshair_cursor())
        elif self._mode == CanvasMode.PAN:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        self.measure_mode_changed.emit(False, "")

    def _ruler_begin(self, view_pos):
        """Start a ruler measurement at the given view position."""
        self._clear_ruler()
        self._ruler_start = self.mapToScene(view_pos)
        self._ruler_dragging = True

        pen = QPen(QColor(255, 140, 0), 0)  # orange, cosmetic (constant width)
        pen.setCosmetic(True)
        self._ruler_line = QGraphicsLineItem(
            QLineF(self._ruler_start, self._ruler_start))
        self._ruler_line.setPen(pen)
        self._ruler_line.setZValue(self._get_label_z_base() + 5)
        self._scene.addItem(self._ruler_line)

        self._ruler_text = QGraphicsTextItem()
        self._ruler_text.setDefaultTextColor(QColor(255, 140, 0))
        font = QFont("Arial", 9)
        font.setBold(True)
        self._ruler_text.setFont(font)
        self._ruler_text.setZValue(self._get_label_z_base() + 6)
        self._scene.addItem(self._ruler_text)

        self._ruler_update(view_pos)

    def _ruler_update(self, view_pos):
        """Update the ruler line, on-canvas readout and status message."""
        if self._ruler_line is None or self._ruler_start is None:
            return
        end = self.mapToScene(view_pos)
        self._ruler_line.setLine(QLineF(self._ruler_start, end))

        value, unit = self._ruler_measure(self._ruler_start, end)
        text = self._format_ruler_distance(value, unit)
        self._position_ruler_text(end, text)
        self.ruler_changed.emit(True, f"Distance: {text}")

    def _ruler_measure(self, start, end) -> tuple[float, str]:
        """Return (distance, unit) for a ruler line.

        Geo layers give geodesic metres (scene coords are Web Mercator, so both
        endpoints are converted to WGS84 and measured with Haversine). The
        non-georeferenced pixel zone has no real-world scale, so distance is
        reported in source pixels instead.
        """
        start_easting = self._scene_to_web(start)[0]
        if self.is_in_pixel_zone(start_easting):
            # Coordinate differences are origin-invariant, so raw scene deltas
            # give the correct pixel distance.
            d_scene = math.hypot(end.x() - start.x(), end.y() - start.y())
            return d_scene / PIXEL_ZONE_SCALE, "px"
        return self._line_distance_m(start, end), "m"

    @staticmethod
    def _format_ruler_distance(value: float, unit: str) -> str:
        """Human-readable distance string (m / km, or px in the pixel zone)."""
        if unit == "px":
            return f"{value:,.1f} px"
        if value >= 1000.0:
            return f"{value / 1000.0:.3f} km"
        return f"{value:.2f} m"

    def _position_ruler_text(self, end_scene, text: str):
        """Place the readout just off the ruler's end point, at constant size."""
        if self._ruler_text is None:
            return
        self._ruler_text.setPlainText(text)
        view_scale = self._view_scale()
        if view_scale > 0:
            self._ruler_text.setScale(1.0 / view_scale)
            offset = 12.0 / view_scale
        else:
            offset = 12.0
        self._ruler_text.setPos(end_scene.x() + offset, end_scene.y() - offset)

    def _rescale_ruler_text(self):
        """Keep the ruler readout a constant on-screen size after a zoom."""
        if self._ruler_text is None:
            return
        view_scale = self._view_scale()
        if view_scale > 0:
            self._ruler_text.setScale(1.0 / view_scale)

    def _clear_ruler(self):
        """Remove the ruler line + readout and reset ruler state."""
        had_ruler = self._ruler_line is not None
        for item in (self._ruler_line, self._ruler_text):
            if item is not None:
                self._scene.removeItem(item)
        self._ruler_line = None
        self._ruler_text = None
        self._ruler_start = None
        self._ruler_dragging = False
        if had_ruler:
            self.ruler_changed.emit(False, "")

    def clear_ruler(self):
        """Remove any ruler measurement currently on the canvas."""
        self._clear_ruler()

    def mark_location(self, lon: float, lat: float):
        """Drop a crosshair on a WGS84 coordinate, replacing any previous one.

        Parented to the origin group, so it is positioned in world coordinates
        and rides along with every floating-origin rebase for free. It ignores
        the view transform, so it stays the same size on screen at any zoom and
        upright when the view is rotated in image-up mode.
        """
        self.clear_location_marker()
        easting, northing = self._wgs84_to_web_mercator(lon, lat)
        marker = self._make_crosshair_marker(
            easting, northing, QColor(0, 200, 255),
            self._get_label_z_base() + 7)
        self._location_marker = marker

    @staticmethod
    def _crosshair_path(arm: float = 14.0, radius: float = 6.0) -> QPainterPath:
        """A circle with four gapped arms, sized in screen pixels.

        Shared by the go-to marker and waypoints so the two can never drift
        apart visually; the colour is what tells them apart.
        """
        path = QPainterPath()
        path.moveTo(-arm, 0.0)
        path.lineTo(-radius, 0.0)
        path.moveTo(radius, 0.0)
        path.lineTo(arm, 0.0)
        path.moveTo(0.0, -arm)
        path.lineTo(0.0, -radius)
        path.moveTo(0.0, radius)
        path.lineTo(0.0, arm)
        path.addEllipse(QPointF(0.0, 0.0), radius, radius)
        return path

    def _make_crosshair_marker(self, easting: float, northing: float,
                               color: QColor, z: float) -> QGraphicsPathItem:
        """Place a crosshair at a Web Mercator position.

        Parented to the origin group, so it is positioned in world coordinates
        and rides along with every floating-origin rebase for free. It ignores
        the view transform, so it stays the same size on screen at any zoom and
        upright when the view is rotated.
        """
        marker = QGraphicsPathItem(self._crosshair_path(), self._overlay_group)
        pen = QPen(color, 2)
        pen.setCosmetic(True)
        marker.setPen(pen)
        marker.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        marker.setZValue(z)
        marker.setPos(easting, -northing)
        return marker

    def clear_location_marker(self):
        """Remove the go-to crosshair, if one is showing."""
        if self._location_marker is not None:
            self._scene.removeItem(self._location_marker)
            self._location_marker = None

    # ------------------------------------------------------------------
    # Waypoints: named geographic bookmarks drawn on the map
    # ------------------------------------------------------------------

    _WAYPOINT_COLOR = QColor(255, 170, 0)   # amber, vs the go-to marker's cyan

    def add_waypoint_marker(self, waypoint_id: int, name: str,
                            lon: float, lat: float):
        """Draw (or redraw) the marker and name for one waypoint."""
        self.remove_waypoint_marker(waypoint_id)
        easting, northing = self._wgs84_to_web_mercator(lon, lat)
        z = self._get_label_z_base() + 6
        marker = self._make_crosshair_marker(
            easting, northing, self._WAYPOINT_COLOR, z)

        text = QGraphicsTextItem(name)
        text.setDefaultTextColor(self._WAYPOINT_COLOR)
        font = QFont("Arial", 8)
        font.setBold(True)
        text.setFont(font)
        # Constant on-screen size and upright, like the label text.
        text.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        text.setZValue(z)
        text.setParentItem(self._overlay_group)
        text.setPos(easting, -northing)
        # Nudge clear of the crosshair arms (screen pixels, transform-ignored).
        text.setTransform(QTransform.fromTranslate(10.0, -22.0), True)

        visible = self._waypoints_visible and not self._waypoints_suppressed
        marker.setVisible(visible)
        text.setVisible(visible)
        self._waypoint_items[waypoint_id] = (marker, text)

    def remove_waypoint_marker(self, waypoint_id: int):
        """Remove one waypoint's marker, if present."""
        items = self._waypoint_items.pop(waypoint_id, None)
        if items is not None:
            for item in items:
                self._scene.removeItem(item)

    def clear_waypoint_markers(self):
        """Remove every waypoint marker from the canvas."""
        for waypoint_id in list(self._waypoint_items):
            self.remove_waypoint_marker(waypoint_id)

    def set_waypoints_visible(self, visible: bool):
        """Show or hide all waypoint markers (the panel's "Show on map")."""
        self._waypoints_visible = bool(visible)
        self._apply_waypoint_visibility()

    def _suppress_waypoints(self, suppressed: bool):
        """Hide waypoints for a mode where geography doesn't apply."""
        self._waypoints_suppressed = bool(suppressed)
        self._apply_waypoint_visibility()

    def _apply_waypoint_visibility(self):
        """Apply the combined user toggle and mode suppression to the items."""
        visible = self._waypoints_visible and not self._waypoints_suppressed
        for marker, text in self._waypoint_items.values():
            marker.setVisible(visible)
            text.setVisible(visible)

    def _waypoint_at_position(self, view_pos) -> "int | None":
        """Return the id of the waypoint under a view position, or None."""
        if not self._waypoint_items or self._waypoints_suppressed:
            return None
        if not self._waypoints_visible:
            return None
        scene_pos = self.mapToScene(view_pos)
        for waypoint_id, (marker, _text) in self._waypoint_items.items():
            rect = marker.sceneBoundingRect()
            if rect.contains(scene_pos):
                return waypoint_id
        return None

    def set_label_linked(self, label_id: int, is_linked: bool):
        """Update whether a label is linked to other labels."""
        if label_id in self._label_items:
            ellipse, _ = self._label_items[label_id]
            ellipse.setData(1, is_linked)  # Store linked status in data slot 1

    def set_label_measured(self, label_id: int, measured: bool,
                           length_m: float | None = None,
                           width_m: float | None = None):
        """Adorn a label marker to reflect whether it has length/width set.

        Measured labels get a cyan outline (matching the measure lines) and the
        dimensions appended to their text; clearing restores the class colour
        and base text. The measured flag is stored in data slot 4 so the
        context menu can offer "Clear Measurements".
        """
        if label_id not in self._label_items:
            return
        ellipse, text = self._label_items[label_id]
        ellipse.setData(4, bool(measured))

        pen = ellipse.pen()
        if measured:
            pen.setColor(QColor(0, 200, 255))
        else:
            # Restore the default outline (derived from the class fill colour).
            pen.setColor(ellipse.brush().color().darker(150))
        ellipse.setPen(pen)

        base = text.data(0) or text.toPlainText()
        if measured and (length_m is not None or width_m is not None):
            length_s = f"{length_m:.1f}" if length_m is not None else "?"
            width_s = f"{width_m:.1f}" if width_m is not None else "?"
            text.setPlainText(f"{base} ({length_s}×{width_s} m)")
        else:
            text.setPlainText(base)

    def set_label_description(self, label_id: int, description: str):
        """Record a label's description and refresh the marker's tooltip.

        A tooltip rather than more text on the map: descriptions are sentences,
        and drawing them beside every marker would bury the imagery they are
        describing. Hovering is also how a user asks "which one is this?",
        which is the question a description answers.
        """
        if label_id not in self._label_items:
            return
        ellipse, _text = self._label_items[label_id]
        ellipse.setData(5, description or "")
        self._refresh_label_tooltip(label_id)

    def set_label_group_id(self, label_id: int, group_id: str):
        """Record a label's shared group name and refresh the tooltip."""
        if label_id not in self._label_items:
            return
        ellipse, _text = self._label_items[label_id]
        ellipse.setData(6, group_id or "")
        self._refresh_label_tooltip(label_id)

    def _refresh_label_tooltip(self, label_id: int):
        """Tooltip = group name line (if any) plus the description."""
        ellipse, text = self._label_items[label_id]
        parts = []
        if ellipse.data(6):
            parts.append(f"Group: {ellipse.data(6)}")
        if ellipse.data(5):
            parts.append(ellipse.data(5))
        tooltip = "\n".join(parts)
        for item in (ellipse, text):
            item.setToolTip(tooltip)

    def highlight_labels(self, label_ids: list[int], highlight: bool = True):
        """Highlight or unhighlight a set of label markers."""
        for label_id in label_ids:
            if label_id in self._label_items:
                ellipse, text = self._label_items[label_id]
                if highlight:
                    # Store original pen and apply highlight
                    if ellipse.data(
                            3) is None:  # data slot 3 for highlight state
                        ellipse.setData(3, ellipse.pen())
                    highlight_pen = QPen(
                        QColor(
                            0,
                            255,
                            255),
                        ellipse.pen().widthF() *
                        1.5)
                    ellipse.setPen(highlight_pen)
                else:
                    # Restore original pen
                    original_pen = ellipse.data(3)
                    if original_pen:
                        ellipse.setPen(original_pen)
                        ellipse.setData(3, None)
