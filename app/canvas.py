"""Map canvas for displaying GeoTIFF images with tiled rendering."""
import math
from pathlib import Path
from enum import Enum, auto

import numpy as np
from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsTextItem, QMenu, QWidget, QLabel
)
from PyQt5.QtGui import QImage, QPixmap, QWheelEvent, QTransform, QPen, QBrush, QColor, QFont, QPainter
from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QTimer
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS


# Web Mercator CRS
WEB_MERCATOR = CRS.from_epsg(3857)
TILE_SIZE = 512  # Pixels per tile


class CanvasMode(Enum):
    """Canvas interaction modes."""
    PAN = auto()      # Default pan/zoom mode
    LABEL = auto()    # Point labeling mode


class TiledLayer:
    """Manages tiled rendering for a single raster layer."""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.name = Path(file_path).stem  # File name without extension
        self.group_path = ""  # Group hierarchy (e.g., "folder/subfolder")
        self.visible = True
        self.bounds = None  # (west, south, east, north) in Web Mercator
        self.tiles: dict[tuple[int, int], QGraphicsPixmapItem] = {}
        self.z_value = 0
        
        # Original image info for coordinate transforms
        self._src_crs = None  # Original CRS
        self._src_transform = None  # Original geotransform
        self._src_width = 0
        self._src_height = 0
        
        # Image data (kept in memory after reprojection)
        self._rgba_data: np.ndarray | None = None
        self._width = 0
        self._height = 0
        
        # Tile grid info
        self._n_tiles_x = 0
        self._n_tiles_y = 0
        self._tile_world_width = 0
        self._tile_world_height = 0
        
        self._load_and_reproject()
    
    def _load_and_reproject(self):
        """Load GeoTIFF and reproject to Web Mercator."""
        with rasterio.open(self.file_path) as src:
            # Store original image info for coordinate transforms
            self._src_crs = src.crs
            self._src_transform = src.transform
            self._src_width = src.width
            self._src_height = src.height
            
            dst_crs = WEB_MERCATOR
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            
            self._width = width
            self._height = height
            
            # Use a sentinel nodata value to identify padded pixels after reprojection
            # We use np.nan for float operations, then track the nodata mask
            NODATA_SENTINEL = np.nan
            
            # Reproject each band using float32 to support nan as nodata
            bands = []
            for i in range(1, src.count + 1):
                src_band = src.read(i).astype(np.float32)
                # Handle source nodata - convert to nan
                if src.nodata is not None:
                    src_band[src_band == src.nodata] = np.nan
                
                dst_band = np.full((height, width), NODATA_SENTINEL, dtype=np.float32)
                reproject(
                    source=src_band,
                    destination=dst_band,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                    src_nodata=np.nan,
                    dst_nodata=NODATA_SENTINEL
                )
                bands.append(dst_band)
            
            # Store bounds in Web Mercator
            self.bounds = rasterio.transform.array_bounds(height, width, transform)
            west, south, east, north = self.bounds
            
            # Create nodata mask - pixels are nodata if ALL bands are nan
            # This identifies padded areas from reprojection
            nodata_mask = np.all([np.isnan(b) for b in bands], axis=0)
            
            # Convert to RGBA
            if len(bands) >= 3:
                r, g, b = bands[0], bands[1], bands[2]
            else:
                r = g = b = bands[0]
            
            # Replace nan with 0 before converting to uint8, then clip
            r = np.nan_to_num(r, nan=0.0)
            g = np.nan_to_num(g, nan=0.0)
            b = np.nan_to_num(b, nan=0.0)
            
            r = np.clip(r, 0, 255).astype(np.uint8)
            g = np.clip(g, 0, 255).astype(np.uint8)
            b = np.clip(b, 0, 255).astype(np.uint8)
            
            self._rgba_data = np.zeros((height, width, 4), dtype=np.uint8)
            self._rgba_data[:, :, 0] = r
            self._rgba_data[:, :, 1] = g
            self._rgba_data[:, :, 2] = b
            # Set alpha to 0 for nodata/padded pixels, 255 for valid pixels
            self._rgba_data[:, :, 3] = np.where(nodata_mask, 0, 255).astype(np.uint8)
            
            # Calculate tile grid
            self._n_tiles_x = math.ceil(width / TILE_SIZE)
            self._n_tiles_y = math.ceil(height / TILE_SIZE)
            self._tile_world_width = (east - west) / self._n_tiles_x
            self._tile_world_height = (north - south) / self._n_tiles_y
    
    def get_tile_bounds(self, tx: int, ty: int) -> tuple[int, int, int, int, float, float, float, float]:
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
    
    def get_visible_tile_indices(self, view_bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
        """Get list of tile indices that intersect with the view bounds.
        
        Args:
            view_bounds: (west, south, east, north) in Web Mercator
        """
        view_west, view_south, view_east, view_north = view_bounds
        layer_west, layer_south, layer_east, layer_north = self.bounds
        
        # Check if view intersects layer at all
        if (view_east < layer_west or view_west > layer_east or
            view_north < layer_south or view_south > layer_north):
            return []
        
        # Calculate which tiles are visible
        visible = []
        for ty in range(self._n_tiles_y):
            for tx in range(self._n_tiles_x):
                _, _, _, _, tile_west, tile_south, tile_east, tile_north = self.get_tile_bounds(tx, ty)
                
                # Check intersection
                if (tile_east >= view_west and tile_west <= view_east and
                    tile_north >= view_south and tile_south <= view_north):
                    visible.append((tx, ty))
        
        return visible
    
    def create_tile_pixmap(self, tx: int, ty: int) -> QPixmap | None:
        """Create a QPixmap for a specific tile."""
        if self._rgba_data is None:
            return None
        
        px_left, px_top, px_right, px_bottom, _, _, _, _ = self.get_tile_bounds(tx, ty)
        
        # Extract tile data
        tile_data = self._rgba_data[px_top:px_bottom, px_left:px_right].copy()
        height, width = tile_data.shape[:2]
        
        if height == 0 or width == 0:
            return None
        
        image = QImage(
            tile_data.data,
            width,
            height,
            width * 4,
            QImage.Format_RGBA8888
        )
        return QPixmap.fromImage(image.copy())
    
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
    
    def latlon_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        """Convert WGS84 lat/lon to pixel coordinates in the original image.
        
        Args:
            lon: Longitude in degrees (WGS84)
            lat: Latitude in degrees (WGS84)
            
        Returns:
            Tuple of (pixel_x, pixel_y) where pixel_x is column and pixel_y is row.
            Values are floats for sub-pixel precision.
        """
        from rasterio.warp import transform as transform_coords
        from rasterio.crs import CRS
        
        # Transform from WGS84 to the image's native CRS
        wgs84 = CRS.from_epsg(4326)
        xs, ys = transform_coords(wgs84, self._src_crs, [lon], [lat])
        x_native, y_native = xs[0], ys[0]
        
        # Use inverse of geotransform to get pixel coordinates
        # ~transform gives the inverse transform
        col, row = ~self._src_transform * (x_native, y_native)
        
        return (col, row)


class ScaleBarWidget(QWidget):
    """Overlay widget showing a distance scale bar."""
    
    # Nice round numbers for scale bar distances (in meters)
    NICE_DISTANCES = [
        1, 2, 5, 10, 20, 50, 100, 200, 500,
        1000, 2000, 5000, 10000, 20000, 50000, 100000,
        200000, 500000, 1000000
    ]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._distance_meters = 100  # Current scale bar distance
        self._bar_width_pixels = 100  # Current bar width in pixels
        self.setFixedSize(150, 40)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # Don't intercept mouse events
        
    def set_scale(self, meters_per_pixel: float):
        """Update the scale bar based on meters per pixel."""
        if meters_per_pixel <= 0:
            return
        
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
        self._bar_width_pixels = max(30, min(140, self._bar_width_pixels))  # Clamp width
        self.update()
    
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
        
        # Draw scale bar
        bar_height = 6
        bar_y = 25
        bar_x = 10
        
        painter.setPen(QPen(QColor(0, 0, 0), 2))
        painter.setBrush(QColor(0, 0, 0))
        
        # Main bar
        painter.drawRect(bar_x, bar_y, self._bar_width_pixels, bar_height)
        
        # End caps (vertical lines)
        cap_height = 10
        painter.drawLine(bar_x, bar_y - 2, bar_x, bar_y + bar_height + 2)
        painter.drawLine(bar_x + self._bar_width_pixels, bar_y - 2, 
                        bar_x + self._bar_width_pixels, bar_y + bar_height + 2)
        
        # Draw distance text
        painter.setPen(QColor(0, 0, 0))
        font = QFont("Arial", 10, QFont.Bold)
        painter.setFont(font)
        text = self._format_distance(self._distance_meters)
        painter.drawText(bar_x, 5, self._bar_width_pixels + 20, 18, 
                        Qt.AlignLeft | Qt.AlignVCenter, text)
        
        painter.end()


class MapCanvas(QGraphicsView):
    """Canvas widget for displaying geospatial raster layers with tiling."""
    
    # Signal emitted when mouse moves: (longitude, latitude, layer_name, group_path)
    coordinates_changed = pyqtSignal(float, float, str, str)
    
    # Signal emitted when a label is placed: (pixel_x, pixel_y, lon, lat, image_name, image_group, image_path)
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
    
    # Signal emitted when user requests to hide layers outside view: (list of layer_ids to hide)
    hide_layers_outside_view = pyqtSignal(list)
    
    # Signal emitted when user requests to show layers inside view: (list of layer_ids to show)
    show_layers_in_view = pyqtSignal(list)
    
    # Signal emitted when user requests to toggle layer visibility: (layer_id)
    toggle_layer_visibility_requested = pyqtSignal(str)
    
    def __init__(self):
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
        # Add some padding but keep it reasonable
        WEB_MERCATOR_MAX = 20037508.34  # meters (at 180° longitude)
        self.setSceneRect(
            -WEB_MERCATOR_MAX * 1.1,  # left (west)
            -WEB_MERCATOR_MAX * 1.1,  # top (remember Y is flipped: -north)
            WEB_MERCATOR_MAX * 2.2,   # width
            WEB_MERCATOR_MAX * 2.2    # height
        )
        
        # Canvas mode
        self._mode = CanvasMode.PAN
        self._current_class = ""  # Currently selected class for labeling
        
        # Link mode state
        self._link_mode_active = False
        self._link_source_label_id: int | None = None
        
        # Label graphics items: label_id -> (ellipse_item, text_item)
        self._label_items: dict[int, tuple[QGraphicsEllipseItem, QGraphicsTextItem]] = {}
        # Z-value offset for labels (added to max layer z-value to ensure labels are always on top)
        self._label_z_offset = 1000
        
        # Layer storage
        self._layers: dict[str, TiledLayer] = {}
        self._layer_order: list[str] = []
        self._path_to_layer: dict[str, str] = {}  # file_path -> layer_id for duplicate detection
        self._next_id = 1
        
        # Tile update timer (debounce rapid view changes)
        self._tile_update_timer = QTimer()
        self._tile_update_timer.setSingleShot(True)
        self._tile_update_timer.timeout.connect(self._update_visible_tiles)
        
        # Scale bar overlay widget
        self._scale_bar = ScaleBarWidget(self)
        self._scale_bar.move(10, 10)  # Will be repositioned in resizeEvent
    
    def add_layer(self, file_path: str) -> str | None:
        """Add a GeoTIFF layer to the canvas. Returns existing layer_id if already loaded."""
        # Check if this file is already loaded
        if file_path in self._path_to_layer:
            return self._path_to_layer[file_path]
        
        try:
            layer = TiledLayer(file_path)
            
            layer_id = f"layer_{self._next_id}"
            self._next_id += 1
            
            self._layers[layer_id] = layer
            self._layer_order.append(layer_id)
            self._path_to_layer[file_path] = layer_id
            self._update_z_order()
            
            # Load visible tiles
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
            import traceback
            traceback.print_exc()
            return None
    
    def _get_view_bounds(self) -> tuple[float, float, float, float]:
        """Get current view bounds in Web Mercator coordinates."""
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        # Scene coords: X = easting, Y = -northing
        return (rect.left(), -rect.bottom(), rect.right(), -rect.top())
    
    def _update_visible_tiles(self):
        """Load tiles that are visible, unload tiles that aren't."""
        view_bounds = self._get_view_bounds()
        
        for layer_id, layer in self._layers.items():
            if not layer.visible:
                continue
            
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
                
                # Get tile bounds
                px_left, px_top, px_right, px_bottom, tile_west, tile_south, tile_east, tile_north = layer.get_tile_bounds(tx, ty)
                
                # Scale to world coordinates
                pixel_width = px_right - px_left
                pixel_height = px_bottom - px_top
                scale_x = (tile_east - tile_west) / pixel_width
                scale_y = (tile_north - tile_south) / pixel_height
                
                transform = QTransform()
                transform.scale(scale_x, scale_y)
                item.setTransform(transform)
                
                # Position at top-left of tile (Y flipped)
                item.setPos(tile_west, -tile_north)
                item.setZValue(layer.z_value)
                item.setVisible(layer.visible)
                
                layer.tiles[idx] = item
    
    def _schedule_tile_update(self):
        """Schedule a tile update (debounced)."""
        self._tile_update_timer.start(50)  # 50ms debounce
    
    def set_layer_visibility(self, layer_id: str, visible: bool):
        """Show or hide a layer."""
        if layer_id in self._layers:
            self._layers[layer_id].set_visibility(visible)
            if visible:
                self._update_visible_tiles()
    
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
        
        # In Web Mercator, units are meters, so size_meters directly gives the extent
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
        """Zoom in/out with mouse wheel."""
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1 / factor, 1 / factor)
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
    
    def _update_scale_bar(self):
        """Update scale bar based on current zoom level."""
        # Get meters per pixel from current transform
        # In Web Mercator, scene units are meters
        transform = self.transform()
        # m11() gives the horizontal scale factor (scene units per pixel)
        # Since we're in Web Mercator, this is meters per pixel (inverted because of scaling)
        if transform.m11() != 0:
            meters_per_pixel = 1.0 / abs(transform.m11())
            self._scale_bar.set_scale(meters_per_pixel)
    
    def set_mode(self, mode: CanvasMode):
        """Set the canvas interaction mode."""
        self._mode = mode
        if mode == CanvasMode.PAN:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.setCursor(Qt.ArrowCursor)
        elif mode == CanvasMode.LABEL:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
    
    def set_current_class(self, class_name: str):
        """Set the current class for labeling."""
        self._current_class = class_name
    
    def get_current_class(self) -> str:
        """Get the current class for labeling."""
        return self._current_class
    
    def mousePressEvent(self, event):
        """Handle mouse press for labeling."""
        if self._mode == CanvasMode.LABEL and event.button() == Qt.LeftButton:
            # Check if we're in link mode
            if self._link_mode_active:
                label_id, image_path = self._get_label_at_position(event.pos())
                if label_id is not None and label_id != self._link_source_label_id:
                    # Link the two labels
                    self.labels_linked.emit(self._link_source_label_id, label_id)
                # Exit link mode regardless
                self._exit_link_mode()
                return
            
            if not self._current_class:
                return  # No class selected
            
            scene_pos = self.mapToScene(event.pos())
            easting = scene_pos.x()
            northing = -scene_pos.y()
            
            # Get image at this position and the layer object
            layer, layer_name, group_path = self._get_layer_and_info_at_position(easting, northing)
            
            # Only allow labeling on actual images (not "nearest" ones)
            if layer and layer_name and not layer_name.startswith("~"):
                lon, lat = self._web_mercator_to_wgs84(easting, northing)
                # Convert lat/lon to pixel coordinates in the original image
                pixel_x, pixel_y = layer.latlon_to_pixel(lon, lat)
                self.label_placed.emit(pixel_x, pixel_y, lon, lat, layer_name, group_path, layer.file_path)
        elif self._mode == CanvasMode.LABEL and event.button() == Qt.RightButton:
            # Right-click in label mode - exit link mode if active, otherwise show context menu
            if self._link_mode_active:
                self._exit_link_mode()
            else:
                self._show_label_context_menu(event.pos())
        elif self._mode == CanvasMode.PAN and event.button() == Qt.RightButton:
            # Right-click in pan mode - show pan context menu
            self._show_pan_context_menu(event.pos())
        else:
            super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        super().mouseReleaseEvent(event)
    
    def keyPressEvent(self, event):
        """Handle key press events."""
        if event.key() == Qt.Key_Escape and self._link_mode_active:
            self._exit_link_mode()
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        """Track mouse position and emit lat/lon coordinates."""
        super().mouseMoveEvent(event)
        
        scene_pos = self.mapToScene(event.pos())
        easting = scene_pos.x()
        northing = -scene_pos.y()
        
        lon, lat = self._web_mercator_to_wgs84(easting, northing)
        layer_name, group_path = self._get_layer_at_position(easting, northing)
        self.coordinates_changed.emit(lon, lat, layer_name, group_path)
    
    def _web_mercator_to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        """Convert Web Mercator (EPSG:3857) to WGS84 (EPSG:4326)."""
        R = 6378137.0
        lon = math.degrees(x / R)
        lat = math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
        return lon, lat
    
    def _wgs84_to_web_mercator(self, lon: float, lat: float) -> tuple[float, float]:
        """Convert WGS84 (EPSG:4326) to Web Mercator (EPSG:3857)."""
        R = 6378137.0
        x = math.radians(lon) * R
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
        return x, y
    
    def _get_layer_at_position(self, easting: float, northing: float) -> tuple[str, str]:
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
            return (f"~{closest_layer.name}", closest_layer.group_path)  # Prefix with ~ to indicate "closest to"
        
        return ("", "")
    
    def _get_layer_and_info_at_position(self, easting: float, northing: float) -> tuple:
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
                         class_name: str, color: QColor = None):
        """Add a visual marker for a label on the canvas.
        
        Args:
            label_id: Unique ID of the label
            lon: Longitude (WGS84)
            lat: Latitude (WGS84)
            image_name: Name of the image the label belongs to
            image_group: Group path of the image
            image_path: Full file path of the image
            class_name: Class name to display
            color: Optional color for the marker
        """
        if color is None:
            color = QColor(255, 50, 50)  # Default red
        
        # Convert lat/lon to Web Mercator for scene positioning
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
    
    def _get_label_at_position(self, view_pos) -> tuple[int | None, str | None]:
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
                image_path = ellipse.data(0)  # We'll store image_path in the ellipse
                return label_id, image_path
        
        return None, None
    
    def _show_label_context_menu(self, view_pos):
        """Show context menu for label under cursor."""
        label_id, image_path = self._get_label_at_position(view_pos)
        
        if label_id is not None:
            menu = QMenu(self)
            
            # Link option - always available
            link_action = menu.addAction("Link with...")
            
            # Check if label is linked (data slot 1 stores True if linked to others)
            ellipse, _ = self._label_items.get(label_id, (None, None))
            is_linked = ellipse and ellipse.data(1) == True
            
            # Unlink and Show linked options (only if label is linked to others)
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
                layer_north < view_south or # layer is entirely below
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
                layer_north < view_south or # layer is entirely below
                layer_south > view_north    # layer is entirely above
            )
            
            if intersects:
                layers_to_show.append(layer_id)
        
        if layers_to_show:
            self.show_layers_in_view.emit(layers_to_show)
    
    def _enter_link_mode(self, source_label_id: int):
        """Enter link mode with the given label as the source."""
        self._link_mode_active = True
        self._link_source_label_id = source_label_id
        self.setCursor(Qt.CrossCursor)
        
        # Highlight the source label
        if source_label_id in self._label_items:
            ellipse, _ = self._label_items[source_label_id]
            ellipse.setData(2, ellipse.pen())  # Store original pen in data slot 2
            highlight_pen = QPen(QColor(255, 255, 0), ellipse.pen().widthF() * 2)
            ellipse.setPen(highlight_pen)
        
        self.link_mode_changed.emit(True, "Link mode: Click another label to link, or right-click/Escape to cancel")
    
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
        if self._mode == CanvasMode.LABEL:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        
        self.link_mode_changed.emit(False, "")
    
    def is_link_mode_active(self) -> bool:
        """Check if link mode is currently active."""
        return self._link_mode_active
    
    def set_label_linked(self, label_id: int, is_linked: bool):
        """Update whether a label is linked to other labels."""
        if label_id in self._label_items:
            ellipse, _ = self._label_items[label_id]
            ellipse.setData(1, is_linked)  # Store linked status in data slot 1
    
    def highlight_labels(self, label_ids: list[int], highlight: bool = True):
        """Highlight or unhighlight a set of label markers."""
        for label_id in label_ids:
            if label_id in self._label_items:
                ellipse, text = self._label_items[label_id]
                if highlight:
                    # Store original pen and apply highlight
                    if ellipse.data(3) is None:  # data slot 3 for highlight state
                        ellipse.setData(3, ellipse.pen())
                    highlight_pen = QPen(QColor(0, 255, 255), ellipse.pen().widthF() * 1.5)
                    ellipse.setPen(highlight_pen)
                else:
                    # Restore original pen
                    original_pen = ellipse.data(3)
                    if original_pen:
                        ellipse.setPen(original_pen)
                        ellipse.setData(3, None)

