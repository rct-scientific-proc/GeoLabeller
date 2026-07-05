"""Map canvas for displaying GeoTIFF images with tiled rendering."""
import math
import traceback
from enum import Enum, auto
from pathlib import Path

import numpy as np
import rasterio
from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QLineF, QTimer, QThread, QObject, QThreadPool, QRunnable
from PyQt5.QtGui import (
    QImage,
    QPixmap,
    QWheelEvent,
    QTransform,
    QPen,
    QBrush,
    QColor,
    QFont,
    QPainter)
from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsTextItem, QMenu, QWidget
)
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform as transform_coords

from .labels import haversine_distance


# Web Mercator CRS
WEB_MERCATOR = CRS.from_epsg(3857)
TILE_SIZE = 512  # Pixels per tile

# Pixel zone: non-georeferenced images are placed beyond valid Web Mercator bounds.
# Scene units are scaled so pixel images have similar visual size to typical geo images.
PIXEL_ZONE_ORIGIN_X = 25_000_000.0  # Well beyond WEB_MERCATOR_MAX (~20M)
PIXEL_ZONE_ORIGIN_Y = 0.0
PIXEL_ZONE_SCALE = 50.0  # Scene units per pixel (makes images ~similar size to geo layers)
PIXEL_ZONE_GROUP_GAP = 5000.0  # Gap between group columns in scene units


class CanvasMode(Enum):
    """Canvas interaction modes."""
    PAN = auto()      # Default pan/zoom mode
    LABEL = auto()    # Point labeling mode
    CYCLE = auto()    # Cycle through layers in a group
    VIEW_CYCLE = auto()  # Cycle through layers visible in current view


class MeasureStage(Enum):
    """Which measurement line the user is currently drawing."""
    LENGTH = auto()   # First line drawn -> label.length_m
    WIDTH = auto()    # Second line drawn -> label.width_m


class TiledLayer:
    """Manages tiled rendering for a single raster layer.

    Supports lazy loading - only loads bounds quickly, full raster data is loaded
    on demand when the layer becomes visible.
    """

    def __init__(self, file_path: str, lazy: bool = False,
                 geo: bool = True):
        """Initialize a tiled layer.

        Args:
            file_path: Path to the GeoTIFF file
            lazy: If True, only load bounds initially, defer full data loading
            geo: If True (default), reproject to Web Mercator. If False, use raw pixel coordinates.
        """
        self.file_path = file_path
        self.name = Path(file_path).stem  # File name without extension
        self.group_path = ""  # Group hierarchy (e.g., "folder/subfolder")
        self.visible = True
        self.bounds = None  # (west, south, east, north) in Web Mercator or pixel coords
        self.tiles: dict[tuple[int, int], QGraphicsPixmapItem] = {}
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


        # Lazy loading state
        self._lazy = lazy
        self._fully_loaded = False

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

    def ensure_loaded(self, level: int | None = None):
        """Ensure raster data is loaded, optionally at a specific overview level.

        Args:
            level: Overview decimation factor to load. When ``None`` the
                currently loaded level is kept (or full resolution on first
                load). Reloads only when the data is missing or the requested
                level differs from what is loaded.
        """
        target = self._loaded_level if level is None else max(1, level)
        if not self._fully_loaded or self._loaded_level != target:
            if self.geo:
                self._load_and_reproject(target)
            else:
                self._load_pixel_data(target)
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
            return 1

        # Scene units covered by one full-resolution data pixel.
        west, _south, east, _north = self.bounds
        native_res = (east - west) / full_width
        if native_res <= 0:
            return 1

        # Pick the largest decimation factor whose level resolution is still no
        # finer than one screen pixel (overviews are sorted ascending).
        best = 1
        for f in self._overviews:
            if native_res * f <= scene_units_per_pixel:
                best = f
            else:
                break
        return best

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
            print(f"[pyramid] {Path(self.file_path).name}: "
                  f"overviews={factors} level_dims={self._src_level_dims}")

    def _load_and_reproject(self, level: int = 1):
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

            # Decimated source read shape (served from the nearest overview),
            # and the source transform scaled to match that read shape.
            rd_w = max(1, src.width // level)
            rd_h = max(1, src.height // level)
            src_read_transform = src.transform * src.transform.scale(
                src.width / rd_w, src.height / rd_h)

            dst_crs = WEB_MERCATOR
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )

            # Remember the full-resolution reprojected dimensions (used for
            # overview level selection) before reducing for this level.
            self._full_width = width
            self._full_height = height

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
            src_band1 = src.read(1, out_shape=(rd_h, rd_w)).astype(np.float32)
            if src.nodata is not None:
                src_band1[src_band1 == src.nodata] = np.nan

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
                    src_band = src.read(i, out_shape=(rd_h, rd_w))
                    # Handle source nodata by setting to 0
                    if src.nodata is not None:
                        src_band = np.where(
                            src_band == src.nodata, 0, src_band)
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
            # Set alpha to 0 for nodata/padded pixels, 255 for valid pixels
            rgba_full[:, :, 3] = np.where(nodata_mask, 0, 255).astype(np.uint8)

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

    def _load_pixel_data(self, level: int = 1):
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
            self._read_overview_metadata(src)

            self._full_width = src.width
            self._full_height = src.height

            level = max(1, level)
            width = max(1, src.width // level)
            height = max(1, src.height // level)

            if src.count >= 3:
                r = src.read(1, out_shape=(height, width)).astype(np.uint8)
                g = src.read(2, out_shape=(height, width)).astype(np.uint8)
                b = src.read(3, out_shape=(height, width)).astype(np.uint8)
            else:
                gray = src.read(1, out_shape=(height, width)).astype(np.uint8)
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
        """Create a QPixmap for a specific tile."""
        # Ensure full data is loaded before accessing pixels
        self.ensure_loaded()

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

    def set_z_value(self, z: float):
        """Set z-value for all tiles."""
        self.z_value = z
        for item in self.tiles.values():
            item.setZValue(z)

    def free_data(self, scene: QGraphicsScene | None = None):
        """Release pixel data from memory, keeping bounds and metadata.

        Removes rendered tiles from the scene and frees the RGBA array.
        The layer can be reloaded later via ensure_loaded().
        """
        if scene is not None:
            for item in self.tiles.values():
                scene.removeItem(item)
            self.tiles.clear()
        self._rgba_data = None
        self._fully_loaded = False

    def remove_from_scene(self, scene: QGraphicsScene):
        """Remove all tiles from the scene."""
        for item in self.tiles.values():
            scene.removeItem(item)
        self.tiles.clear()

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


class ScaleBarWidget(QWidget):
    """Overlay widget showing a distance scale bar."""

    # Nice round numbers for scale bar distances (in meters)
    NICE_DISTANCES = [
        1, 2, 5, 10, 20, 50, 100, 200, 500,
        1000, 2000, 5000, 10000, 20000, 50000, 100000,
        200000, 500000, 1000000
    ]

    def __init__(self, parent=None, orientation=Qt.Horizontal):
        """Initialize the scale bar's orientation, size and default scale state."""
        super().__init__(parent)
        self._orientation = orientation
        self._distance_meters = 100  # Current scale bar distance
        self._bar_width_pixels = 100  # Current bar extent in pixels
        self._meters_per_pixel = 0.0  # Ground metres per view pixel
        if orientation == Qt.Horizontal:
            self.setFixedSize(160, 54)
        else:
            self.setFixedSize(120, 170)
        # Don't intercept mouse events
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_scale(self, meters_per_pixel: float):
        """Update the scale bar based on meters per pixel."""
        if meters_per_pixel <= 0:
            return

        self._meters_per_pixel = meters_per_pixel

        # Target bar width: 80-120 pixels
        target_width = 100
        target_meters = target_width * meters_per_pixel

        # Find the best nice distance
        best_distance = self.NICE_DISTANCES[0]
        for d in self.NICE_DISTANCES:
            if d <= target_meters * 1.5:
                best_distance = d
            else:
                break

        self._distance_meters = best_distance
        self._bar_width_pixels = int(best_distance / meters_per_pixel)
        self._bar_width_pixels = max(
            30, min(140, self._bar_width_pixels))  # Clamp width
        self.update()

    def _format_mpp(self, mpp: float) -> str:
        """Format ground resolution (metres per view pixel)."""
        if mpp <= 0:
            return "-- m/px"
        if mpp >= 100:
            return f"{mpp:.0f} m/px"
        if mpp >= 1:
            return f"{mpp:.2f} m/px"
        if mpp >= 0.001:
            return f"{mpp:.3f} m/px"
        return f"{mpp:.2e} m/px"

    def _format_distance(self, meters: float) -> str:
        """Format distance with appropriate units."""
        if meters >= 1000:
            km = meters / 1000
            if km == int(km):
                return f"{int(km)} km"
            return f"{km:.1f} km"
        else:
            if meters == int(meters):
                return f"{int(meters)} m"
            return f"{meters:.1f} m"

    def paintEvent(self, event):
        """Draw the scale bar."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Semi-transparent background
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 5, 5)

        if self._orientation == Qt.Horizontal:
            self._paint_horizontal(painter)
        else:
            self._paint_vertical(painter)

        painter.end()

    def _paint_horizontal(self, painter):
        """Draw the horizontal scale bar with distance above and m/px below."""
        # Draw scale bar
        bar_height = 6
        bar_y = 22
        bar_x = 10

        painter.setPen(QPen(QColor(0, 0, 0), 2))
        painter.setBrush(QColor(0, 0, 0))

        # Main bar
        painter.drawRect(bar_x, bar_y, self._bar_width_pixels, bar_height)

        # End caps (vertical lines)
        painter.drawLine(bar_x, bar_y - 2, bar_x, bar_y + bar_height + 2)
        painter.drawLine(
            bar_x + self._bar_width_pixels,
            bar_y - 2,
            bar_x + self._bar_width_pixels,
            bar_y + bar_height + 2)

        # Draw distance text (above the bar)
        painter.setPen(QColor(0, 0, 0))
        font = QFont("Arial", 10, QFont.Bold)
        painter.setFont(font)
        text = self._format_distance(self._distance_meters)
        painter.drawText(bar_x, 2, self._bar_width_pixels + 30, 16,
                         Qt.AlignLeft | Qt.AlignVCenter, text)

        # Draw ground resolution (metres per view pixel) below the bar
        res_font = QFont("Arial", 8)
        painter.setFont(res_font)
        painter.drawText(bar_x, bar_y + bar_height + 2,
                         self.width() - bar_x - 4, 16,
                         Qt.AlignLeft | Qt.AlignVCenter,
                         self._format_mpp(self._meters_per_pixel))

    def _paint_vertical(self, painter):
        """Draw the vertical scale bar with distance and m/px beside it."""
        bar_width = 6
        bar_x = 12
        bar_top = 12
        length = self._bar_width_pixels

        painter.setPen(QPen(QColor(0, 0, 0), 2))
        painter.setBrush(QColor(0, 0, 0))

        # Main bar (vertical)
        painter.drawRect(bar_x, bar_top, bar_width, length)

        # End caps (horizontal lines)
        painter.drawLine(bar_x - 2, bar_top, bar_x + bar_width + 2, bar_top)
        painter.drawLine(bar_x - 2, bar_top + length,
                         bar_x + bar_width + 2, bar_top + length)

        # Distance and resolution text to the right, centred on the bar
        text_x = bar_x + bar_width + 8
        text_w = self.width() - text_x - 4
        center_y = bar_top + length / 2

        painter.setPen(QColor(0, 0, 0))
        font = QFont("Arial", 10, QFont.Bold)
        painter.setFont(font)
        painter.drawText(text_x, int(center_y) - 16, text_w, 16,
                         Qt.AlignLeft | Qt.AlignVCenter,
                         self._format_distance(self._distance_meters))

        res_font = QFont("Arial", 8)
        painter.setFont(res_font)
        painter.drawText(text_x, int(center_y), text_w, 16,
                         Qt.AlignLeft | Qt.AlignVCenter,
                         self._format_mpp(self._meters_per_pixel))


class _LevelLoadSignals(QObject):
    """Signals for a background overview-level load."""
    finished = pyqtSignal(str, int, object)  # layer_id, level, result dict
    error = pyqtSignal(str, str)             # layer_id, message


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

    def run(self):
        """Compute the layer's RGBA data for the level off the UI thread and
        emit the result (or an error) back to the main thread."""
        try:
            tmp = TiledLayer(self._file_path, lazy=True, geo=self._geo)
            tmp.ensure_loaded(level=self._level)
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
        except Exception as e:  # report any load failure back to the UI thread
            self._safe_emit(self._signals.error, self._layer_id, str(e))
            return
        self._safe_emit(self._signals.finished, self._layer_id, self._level, result)

    @staticmethod
    def _safe_emit(signal, *args):
        """Emit a signal, ignoring the case where the receiver was already
        deleted (e.g. the canvas/app was torn down while this load was running).
        """
        try:
            signal.emit(*args)
        except RuntimeError:
            pass


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

    # Signal emitted when user wants to highlight linked labels: (label_id)
    show_linked_requested = pyqtSignal(int)

    # Signal emitted when link mode state changes: (is_active, message)
    link_mode_changed = pyqtSignal(bool, str)

    # Signal emitted when a label's length/width has been measured:
    # (label_id, length_m, width_m). Values are floats in metres; `object`
    # payloads allow None (e.g. when clearing measurements later).
    label_measured = pyqtSignal(int, object, object)

    # Signal emitted when measure mode state changes: (is_active, message)
    measure_mode_changed = pyqtSignal(bool, str)

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

    # Maximum RGBA pixel count to load synchronously on the UI thread. Larger
    # levels load in a background thread with a coarse preview shown first.
    _SYNC_LOAD_MAX_PIXELS = 500_000

    # Minimum on-screen separation (view pixels) between the two clicks of a
    # measurement line; shorter lines are treated as an accidental click and
    # ignored so a stray double-click never records a bogus sub-metre value.
    _MIN_MEASURE_PIXELS = 4

    def __init__(self):
        """Set up the graphics scene, view interaction, mode/link/measure state,
        layer storage, overlays and background level-loading pool."""
        super().__init__()
        self._scene = QGraphicsScene()
        self.setScene(self._scene)

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
        self.setSceneRect(
            -WEB_MERCATOR_MAX * 1.1,  # left (west)
            -SCENE_MAX,               # top (remember Y is flipped: -north)
            WEB_MERCATOR_MAX * 1.1 + SCENE_MAX,  # width (extends into pixel zone)
            SCENE_MAX * 2             # height
        )

        # Canvas mode
        self._mode = CanvasMode.PAN
        self._current_class = ""  # Currently selected class for labeling

        # Link mode state
        self._link_mode_active = False
        self._link_source_label_id: int | None = None

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

        # Label graphics items: label_id -> (ellipse_item, text_item)
        self._label_items: dict[int,
                                tuple[QGraphicsEllipseItem,
                                      QGraphicsTextItem]] = {}
        # Z-value offset for labels (added to max layer z-value to ensure
        # labels are always on top)
        self._label_z_offset = 1000

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

        # Tile update timer (debounce rapid view changes)
        self._tile_update_timer = QTimer()
        self._tile_update_timer.setSingleShot(True)
        self._tile_update_timer.timeout.connect(self._update_visible_tiles)

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

        # Scale bar overlay widget
        self._scale_bar = ScaleBarWidget(self)
        self._scale_bar.move(10, 10)  # Will be repositioned in resizeEvent

        # Vertical scale bar (vertical distance + m/px), shown below the
        # horizontal one.
        self._scale_bar_v = ScaleBarWidget(self, orientation=Qt.Vertical)
        self._scale_bar_v.move(10, 74)  # Will be repositioned in resizeEvent

        # Background loader for expensive (fine) overview levels. `_level_load_
        # signals` keeps the per-job signal objects alive until they deliver.
        self._level_load_pool = QThreadPool(self)
        self._level_load_pool.setMaxThreadCount(2)
        self._level_load_signals: set = set()

    def add_layer(self, file_path: str, lazy: bool = False,
                  visible: bool = True) -> str | None:
        """Add a GeoTIFF layer to the canvas. Returns existing layer_id if already loaded.

        Args:
            file_path: Path to the GeoTIFF file
            lazy: If True, only load bounds initially (faster for bulk imports)
            visible: Whether the layer should be visible initially
        """
        # Check if this file is already loaded
        if file_path in self._path_to_layer:
            return self._path_to_layer[file_path]

        try:
            layer = TiledLayer(file_path, lazy=lazy)
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
                rect = QRectF(west, -north, east - west, north - south)
                self.fitInView(rect, Qt.KeepAspectRatio)
                self._update_scale_bar()

            return layer_id

        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            traceback.print_exc()
            return None

    def add_pixel_layer(self, file_path: str, group_path: str = "",
                        lazy: bool = False, visible: bool = True) -> str | None:
        """Add a non-georeferenced image layer to the pixel zone.

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
            layer = TiledLayer(file_path, lazy=lazy, geo=False)
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

    def _get_view_bounds(self) -> tuple[float, float, float, float]:
        """Get current view bounds in Web Mercator coordinates."""
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        # Scene coords: X = easting, Y = -northing
        return (rect.left(), -rect.bottom(), rect.right(), -rect.top())

    def _scene_units_per_pixel(self) -> float:
        """Return the size of one on-screen pixel in scene units.

        Scene units are Web Mercator metres for geo layers. Larger values mean
        the view is more zoomed out. Derived from the view transform's
        horizontal scale factor (view-pixels per scene-unit).
        """
        m11 = self.transform().m11()
        return 1.0 / m11 if m11 > 0 else 0.0

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
        center_northing = -rect.center().y()
        _lon, lat = self._web_mercator_to_wgs84(0.0, center_northing)
        return units_per_pixel * math.cos(math.radians(lat))

    def _update_visible_tiles(self):
        """Load tiles that are visible, unload tiles that aren't."""
        view_bounds = self._get_view_bounds()
        units_per_pixel = self._scene_units_per_pixel()

        for layer_id, layer in self._layers.items():
            if not layer.visible:
                continue

            # Cull layers entirely outside the viewport: they must not trigger
            # any pyramid loading. Drop any tiles they may still hold.
            if not self._layer_intersects_view(layer, view_bounds):
                if layer.tiles:
                    self._clear_layer_tiles(layer)
                continue

            # Level-of-detail: pick an overview level for the current zoom and
            # (re)load this layer if it differs from what is currently loaded.
            self._apply_layer_lod(layer_id, layer, units_per_pixel)

            self._rebuild_layer_tiles(layer)

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

    def _rebuild_layer_tiles(self, layer: TiledLayer):
        """Add/remove a single layer's tiles to match the current view."""
        view_bounds = self._get_view_bounds()
        visible_indices = set(layer.get_visible_tile_indices(view_bounds))
        current_indices = set(layer.tiles.keys())

        # Remove tiles no longer visible
        for idx in current_indices - visible_indices:
            self._scene.removeItem(layer.tiles[idx])
            del layer.tiles[idx]

        # Add newly visible tiles
        for idx in visible_indices - current_indices:
            tx, ty = idx
            pixmap = layer.create_tile_pixmap(tx, ty)
            if pixmap is None:
                continue

            item = self._scene.addPixmap(pixmap)

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

    def _apply_layer_lod(self, layer_id: str, layer: TiledLayer,
                         units_per_pixel: float):
        """Choose and apply the overview level for a layer at the current zoom.

        Cheap (coarse) levels load synchronously. Expensive (fine) levels load
        in a background thread while a coarser preview stays on screen, so the
        UI never freezes on zoom-in. Panning at a fixed zoom is a no-op.
        """
        if not layer.has_overviews():
            return

        desired = layer.select_overview_level(units_per_pixel)
        layer._target_level = desired

        if layer.is_fully_loaded() and layer._loaded_level == desired:
            return

        expensive = layer.level_pixel_count(desired) > self._SYNC_LOAD_MAX_PIXELS

        if not layer.is_fully_loaded():
            # Nothing on screen yet: show a cheap preview immediately, then
            # refine to the target level in the background if it is expensive.
            preview = layer.coarsest_level() if expensive else desired
            self._clear_layer_tiles(layer)
            layer.ensure_loaded(level=preview)
            if expensive and layer._loaded_level != desired:
                self._dispatch_level_load(layer_id, layer, desired)
            return

        if not expensive:
            # Cheap swap: replace data now; the caller rebuilds the tiles.
            self._clear_layer_tiles(layer)
            layer.ensure_loaded(level=desired)
        else:
            # Keep the current tiles as a preview and load the target level in
            # the background (swapped in by _on_level_loaded when ready).
            self._dispatch_level_load(layer_id, layer, desired)

    def _dispatch_level_load(self, layer_id: str, layer: TiledLayer, level: int):
        """Start a background load of *layer* at *level* (if not already running)."""
        if layer._loading_level == level:
            return
        layer._loading_level = level

        signals = _LevelLoadSignals()
        self._level_load_signals.add(signals)
        signals.finished.connect(self._on_level_loaded)
        signals.error.connect(self._on_level_load_error)
        # Keep the signals object alive until it has delivered, then release it.
        signals.finished.connect(
            lambda *_a, s=signals: self._level_load_signals.discard(s))
        signals.error.connect(
            lambda *_a, s=signals: self._level_load_signals.discard(s))

        runnable = _LevelLoadRunnable(
            layer_id, layer.file_path, layer.geo, level, signals)
        self._level_load_pool.start(runnable)

    def _on_level_loaded(self, layer_id: str, level: int, result: dict):
        """Apply a background-loaded overview level on the UI thread."""
        layer = self._layers.get(layer_id)
        if layer is None:
            return  # Layer was removed while loading.

        if layer._loading_level == level:
            layer._loading_level = None

        # A newer zoom may have superseded this level; if so, chase the new one.
        if level != layer._target_level:
            if (layer.has_overviews()
                    and layer._target_level != layer._loaded_level):
                self._dispatch_level_load(layer_id, layer, layer._target_level)
            return

        layer.apply_level_result(result)
        self._clear_layer_tiles(layer)
        self._rebuild_layer_tiles(layer)

    def _on_level_load_error(self, layer_id: str, message: str):
        """Handle a failed background level load."""
        layer = self._layers.get(layer_id)
        if layer is not None:
            layer._loading_level = None
        print(f"[pyramid] background level load failed for {layer_id}: {message}")


    def _schedule_tile_update(self):
        """Schedule a tile update (debounced)."""
        self._tile_update_timer.start(50)  # 50ms debounce

    def set_layer_visibility(self, layer_id: str, visible: bool):
        """Show or hide a layer."""
        if layer_id in self._layers:
            layer = self._layers[layer_id]
            layer.set_visibility(visible)
            if visible:
                self._update_visible_tiles()
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
        # Update label z-values to ensure they remain above all layers
        self._update_label_z_values()

    def _get_label_z_base(self) -> float:
        """Get the base z-value for labels, ensuring it's always above all layers."""
        # Labels should be above all layers (layer z-values are 0, 1, 2, ...)
        max_layer_z = len(self._layer_order)
        return max_layer_z + self._label_z_offset

    def _update_label_z_values(self):
        """Update all label markers to ensure they stay above all layers."""
        label_z = self._get_label_z_base()
        for ellipse, text in self._label_items.values():
            ellipse.setZValue(label_z)
            text.setZValue(label_z + 1)

    def remove_layer(self, layer_id: str):
        """Remove a layer from the canvas."""
        if layer_id in self._layers:
            file_path = self._layers[layer_id].file_path
            self._layers[layer_id].remove_from_scene(self._scene)
            del self._layers[layer_id]
            if file_path in self._path_to_layer:
                del self._path_to_layer[file_path]
            if layer_id in self._layer_order:
                self._layer_order.remove(layer_id)

    def clear_layers(self):
        """Remove all layers from the canvas."""
        for layer_id in list(self._layers.keys()):
            self._layers[layer_id].remove_from_scene(self._scene)
        self._layers.clear()
        self._layer_order.clear()
        self._path_to_layer.clear()
        self._pixel_zone_groups.clear()
        self._pixel_zone_next_x = PIXEL_ZONE_ORIGIN_X

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
        rect = QRectF(west, -north, east - west, north - south)
        self.fitInView(rect, Qt.KeepAspectRatio)
        self._schedule_tile_update()
        self._update_scale_bar()

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

        # Create rect (Y is flipped in scene coordinates)
        rect = QRectF(west, -north, east - west, north - south)
        self.fitInView(rect, Qt.KeepAspectRatio)
        self._schedule_tile_update()
        self.update_label_markers_scale()
        self._update_scale_bar()

    def wheelEvent(self, event: QWheelEvent):
        """Zoom in/out with mouse wheel, centered on mouse position."""
        # Get the scene position under the mouse before scaling
        old_pos = self.mapToScene(event.pos())

        factor = 1.15
        if event.angleDelta().y() > 0:
            # Zooming in - check if we'd exceed the minimum view size (10m)
            # Get current view bounds in scene coordinates (Web Mercator = meters)
            view_rect = self.mapToScene(self.viewport().rect()).boundingRect()
            new_width = view_rect.width() / factor
            new_height = view_rect.height() / factor
            MIN_VIEW_SIZE = 10.0  # meters
            if new_width < MIN_VIEW_SIZE or new_height < MIN_VIEW_SIZE:
                # Don't zoom in further
                return
            self.scale(factor, factor)
        else:
            self.scale(1 / factor, 1 / factor)

        # Get the new scene position under the mouse after scaling
        new_pos = self.mapToScene(event.pos())

        # Adjust scrollbars to keep the point under the mouse fixed
        # Clamp values to prevent integer overflow when zoomed in extremely far
        delta = old_pos - new_pos
        INT_MAX = 2**31 - 1
        INT_MIN = -(2**31)
        h_delta = delta.x() * self.transform().m11()
        v_delta = delta.y() * self.transform().m22()
        h_delta = max(INT_MIN, min(INT_MAX, h_delta))
        v_delta = max(INT_MIN, min(INT_MAX, v_delta))
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + int(h_delta))
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + int(v_delta))

        self._schedule_tile_update()
        self.update_label_markers_scale()
        self._update_scale_bar()

    def scrollContentsBy(self, dx: int, dy: int):
        """Called when view is scrolled (panned)."""
        super().scrollContentsBy(dx, dy)
        self._schedule_tile_update()

    def resizeEvent(self, event):
        """Called when view is resized."""
        super().resizeEvent(event)
        self._schedule_tile_update()
        self._position_scale_bar()
        self._update_scale_bar()

    def _position_scale_bar(self):
        """Position the scale bar in the top-right corner."""
        margin = 10
        x = self.viewport().width() - self._scale_bar.width() - margin
        y = margin
        self._scale_bar.move(x, y)

        # Vertical scale bar directly below the horizontal one, right-aligned.
        vx = self.viewport().width() - self._scale_bar_v.width() - margin
        vy = y + self._scale_bar.height() + margin
        self._scale_bar_v.move(vx, vy)

    def _update_scale_bar(self):
        """Update scale bars based on current zoom level."""
        # In Web Mercator, scene units are meters. m11()/m22() give the
        # horizontal/vertical scale factors (view pixels per scene unit), so the
        # inverse is meters per pixel along each axis.
        transform = self.transform()
        if transform.m11() != 0:
            self._scale_bar.set_scale(1.0 / abs(transform.m11()))
        if transform.m22() != 0:
            self._scale_bar_v.set_scale(1.0 / abs(transform.m22()))

    def set_mode(self, mode: CanvasMode):
        """Set the canvas interaction mode."""
        self._mode = mode
        if mode == CanvasMode.PAN:
            # We handle panning manually
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.OpenHandCursor)
            self._pan_active = False
        elif mode == CanvasMode.LABEL:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
        elif mode in (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE):
            # Cycle mode: left click labels, right drag pans, wheel zooms
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
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
        if self._mode in (
                CanvasMode.LABEL,
                CanvasMode.CYCLE,
                CanvasMode.VIEW_CYCLE) and event.button() == Qt.LeftButton:
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

            # Ctrl+Left-click in CYCLE/VIEW_CYCLE mode shows label context menu (for
            # linking)
            if self._mode in (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE) and event.modifiers() & Qt.ControlModifier:
                self._show_label_context_menu(event.pos())
                return

            if not self._current_class:
                return  # No class selected

            scene_pos = self.mapToScene(event.pos())
            easting = scene_pos.x()
            northing = -scene_pos.y()

            # Get image at this position and the layer object
            layer, layer_name, group_path = self._get_layer_and_info_at_position(
                easting, northing)

            # Only allow labeling on actual images (not "nearest" ones)
            if layer and layer_name and not layer_name.startswith("~"):
                if layer.geo:
                    lon, lat = self._web_mercator_to_wgs84(easting, northing)
                    pixel_x, pixel_y = layer.latlon_to_pixel(lon, lat)
                else:
                    # Non-georeferenced: scene coords map directly to pixels
                    pixel_x, pixel_y = layer.scene_to_pixel(easting, northing)
                    lon, lat = 0.0, 0.0
                self.label_placed.emit(
                    pixel_x,
                    pixel_y,
                    lon,
                    lat,
                    layer_name,
                    group_path,
                    layer.file_path)
        elif self._mode == CanvasMode.LABEL and event.button() == Qt.RightButton:
            # Right-click in label mode - exit link mode if active, otherwise
            # show context menu
            if self._link_mode_active:
                self._exit_link_mode()
            else:
                self._show_label_context_menu(event.pos())
        elif self._mode in (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE) and event.button() == Qt.RightButton:
            # Right-click drag in cycle mode - start panning
            self._cycle_panning = True
            self._cycle_pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        # Measure mode consumes clicks in mousePressEvent; swallow the matching
        # release so it can't reach pan/cycle release handling.
        if self._measure_active:
            return
        if self._mode == CanvasMode.PAN and event.button() == Qt.LeftButton:
            if hasattr(self, '_pan_active') and self._pan_active:
                self._pan_active = False
                self.setCursor(Qt.OpenHandCursor)
        elif self._mode in (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE) and event.button() == Qt.RightButton:
            if hasattr(self, '_cycle_panning') and self._cycle_panning:
                self._cycle_panning = False
                self.setCursor(Qt.CrossCursor)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        """Track mouse position and emit lat/lon coordinates."""
        self._last_mouse_view_pos = event.pos()

        # Measure mode: stretch the rubber-band line to the cursor. Fall through
        # so the coordinate readout still updates.
        if self._measure_active and self._measure_start is not None:
            self._update_measure_preview(event.pos())

        # Handle PAN mode left-click panning
        if self._mode == CanvasMode.PAN and hasattr(
                self, '_pan_active') and self._pan_active:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            # Still update coordinates below

        # Handle cycle mode right-click panning
        if self._mode in (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE) and hasattr(
                self, '_cycle_panning') and self._cycle_panning:
            delta = event.pos() - self._cycle_pan_start
            self._cycle_pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            # Still update coordinates below

        scene_pos = self.mapToScene(event.pos())
        easting = scene_pos.x()
        northing = -scene_pos.y()

        if self.is_in_pixel_zone(easting):
            # In the pixel zone: find the layer and compute pixel coords
            layer_name, group_path = self._get_layer_at_position(easting, northing)
            layer, _, _ = self._get_layer_and_info_at_position(easting, northing)
            if layer and not layer.geo:
                px, py = layer.scene_to_pixel(easting, northing)
                self._queue_coords_emit(px, py, layer_name, group_path, True)
            else:
                self._queue_coords_emit(0.0, 0.0, layer_name, group_path, True)
        else:
            lon, lat = self._web_mercator_to_wgs84(easting, northing)
            layer_name, group_path = self._get_layer_at_position(easting, northing)
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
        elif event.key() == Qt.Key_Space and self._mode in (CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE):
            if event.modifiers() & Qt.ControlModifier:
                # Ctrl+Space: go backwards
                self.cycle_prev_requested.emit()
            else:
                # Space: go forwards
                self.cycle_next_requested.emit()
        else:
            super().keyPressEvent(event)

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

        # Get current view scale to size markers appropriately
        view_scale = self.transform().m11()  # Horizontal scale factor

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
        text.setScale(1 / view_scale if view_scale > 0 else 1)
        text.setPos(x + marker_size / 2, -y - marker_size / 2)
        text.setZValue(self._get_label_z_base() + 1)

        self._scene.addItem(ellipse)
        self._scene.addItem(text)

        self._label_items[label_id] = (ellipse, text)

    def remove_label_marker(self, label_id: int):
        """Remove a label marker from the canvas."""
        if label_id in self._label_items:
            ellipse, text = self._label_items[label_id]
            self._scene.removeItem(ellipse)
            self._scene.removeItem(text)
            del self._label_items[label_id]

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
        view_scale = self.transform().m11()
        if view_scale <= 0:
            return

        marker_size = 10 / view_scale

        for ellipse, text in self._label_items.values():
            # Update ellipse size
            ellipse.setRect(
                -marker_size / 2, -marker_size / 2,
                marker_size, marker_size
            )
            pen = ellipse.pen()
            pen.setWidthF(marker_size / 5)
            ellipse.setPen(pen)

            # Update text scale and position
            text.setScale(1 / view_scale)
            pos = ellipse.pos()
            text.setPos(pos.x() + marker_size / 2, pos.y() - marker_size / 2)

    def _get_label_at_position(
            self, view_pos) -> tuple[int | None, str | None]:
        """Find the label at the given view position.

        Returns:
            Tuple of (label_id, image_path) or (None, None) if no label found.
        """
        scene_pos = self.mapToScene(view_pos)

        # Check each label marker
        for label_id, (ellipse, text) in self._label_items.items():
            # Get ellipse bounding rect in scene coordinates
            item_pos = ellipse.pos()
            rect = ellipse.boundingRect()
            scene_rect = QRectF(
                item_pos.x() + rect.x(),
                item_pos.y() + rect.y(),
                rect.width(),
                rect.height()
            )

            # Expand hit area slightly for easier clicking
            hit_margin = rect.width() * 0.5
            scene_rect.adjust(-hit_margin, -hit_margin, hit_margin, hit_margin)

            if scene_rect.contains(scene_pos):
                # Found a label - now find the image_path
                # We need to look up which image this label belongs to
                # The label stores its position in scene coords, we need to find
                # which layer it's on based on the stored data
                # We'll store image_path in the ellipse
                image_path = ellipse.data(0)
                return label_id, image_path

        return None, None

    def _show_label_context_menu(self, view_pos):
        """Show context menu for label under cursor."""
        label_id, image_path = self._get_label_at_position(view_pos)

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
            elif action == unlink_action:
                self.label_unlinked.emit(label_id)
            elif action == show_linked_action:
                self.show_linked_requested.emit(label_id)
            elif action == toggle_layer_action:
                # Get the layer_id from the image_path and emit toggle signal
                if image_path in self._path_to_layer:
                    layer_id = self._path_to_layer[image_path]
                    self.toggle_layer_visibility_requested.emit(layer_id)

    def _show_pan_context_menu(self, view_pos):
        """Show context menu for pan mode."""
        menu = QMenu(self)

        show_in_view_action = menu.addAction("Select layers in view")
        hide_outside_action = menu.addAction("Unselect layers outside view")

        action = menu.exec_(self.mapToGlobal(view_pos))

        if action == show_in_view_action:
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
        self.setCursor(Qt.CrossCursor)

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
        if self._mode in (CanvasMode.LABEL, CanvasMode.CYCLE, CanvasMode.VIEW_CYCLE):
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        self.link_mode_changed.emit(False, "")

    def is_link_mode_active(self) -> bool:
        """Check if link mode is currently active."""
        return self._link_mode_active

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
        self.setCursor(Qt.CrossCursor)
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
        lon1, lat1 = self._web_mercator_to_wgs84(
            start_scene.x(), -start_scene.y())
        lon2, lat2 = self._web_mercator_to_wgs84(
            end_scene.x(), -end_scene.y())
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
        if self._mode in (CanvasMode.LABEL, CanvasMode.CYCLE,
                          CanvasMode.VIEW_CYCLE):
            self.setCursor(Qt.CrossCursor)
        elif self._mode == CanvasMode.PAN:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        self.measure_mode_changed.emit(False, "")

    def is_measure_mode_active(self) -> bool:
        """Check if measure mode is currently active."""
        return self._measure_active

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
