"""Map canvas for displaying GeoTIFF images with tiled rendering."""
import math
from pathlib import Path
from enum import Enum, auto

import numpy as np
from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsEllipseItem, QGraphicsTextItem
)
from PyQt5.QtGui import QImage, QPixmap, QWheelEvent, QTransform, QPen, QBrush, QColor, QFont
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
            
            # Reproject each band
            bands = []
            for i in range(1, src.count + 1):
                src_band = src.read(i)
                dst_band = np.zeros((height, width), dtype=src_band.dtype)
                reproject(
                    source=src_band,
                    destination=dst_band,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear
                )
                bands.append(dst_band)
            
            # Store bounds in Web Mercator
            self.bounds = rasterio.transform.array_bounds(height, width, transform)
            west, south, east, north = self.bounds
            
            # Convert to RGBA
            if len(bands) >= 3:
                r, g, b = bands[0], bands[1], bands[2]
            else:
                r = g = b = bands[0]
            
            r = np.clip(r, 0, 255).astype(np.uint8)
            g = np.clip(g, 0, 255).astype(np.uint8)
            b = np.clip(b, 0, 255).astype(np.uint8)
            
            self._rgba_data = np.zeros((height, width, 4), dtype=np.uint8)
            self._rgba_data[:, :, 0] = r
            self._rgba_data[:, :, 1] = g
            self._rgba_data[:, :, 2] = b
            self._rgba_data[:, :, 3] = 255
            
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


class MapCanvas(QGraphicsView):
    """Canvas widget for displaying geospatial raster layers with tiling."""
    
    # Signal emitted when mouse moves: (longitude, latitude, layer_name, group_path)
    coordinates_changed = pyqtSignal(float, float, str, str)
    
    # Signal emitted when a label is placed: (pixel_x, pixel_y, lon, lat, image_name, image_group, image_path)
    label_placed = pyqtSignal(float, float, float, float, str, str, str)
    
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
        self.setSceneRect(-1e10, -1e10, 2e10, 2e10)
        
        # Canvas mode
        self._mode = CanvasMode.PAN
        self._current_class = ""  # Currently selected class for labeling
        
        # Label graphics items: label_id -> (ellipse_item, text_item)
        self._label_items: dict[int, tuple[QGraphicsEllipseItem, QGraphicsTextItem]] = {}
        self._label_z_base = 1000  # Z-value for labels (above all tiles)
        
        # Layer storage
        self._layers: dict[str, TiledLayer] = {}
        self._layer_order: list[str] = []
        self._next_id = 1
        
        # Tile update timer (debounce rapid view changes)
        self._tile_update_timer = QTimer()
        self._tile_update_timer.setSingleShot(True)
        self._tile_update_timer.timeout.connect(self._update_visible_tiles)
    
    def add_layer(self, file_path: str) -> str | None:
        """Add a GeoTIFF layer to the canvas."""
        try:
            layer = TiledLayer(file_path)
            
            layer_id = f"layer_{self._next_id}"
            self._next_id += 1
            
            self._layers[layer_id] = layer
            self._layer_order.append(layer_id)
            self._update_z_order()
            
            # Load visible tiles
            self._update_visible_tiles()
            
            # Fit view on first layer
            if len(self._layers) == 1:
                west, south, east, north = layer.bounds
                rect = QRectF(west, -north, east - west, north - south)
                self.fitInView(rect, Qt.KeepAspectRatio)
            
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
    
    def remove_layer(self, layer_id: str):
        """Remove a layer from the canvas."""
        if layer_id in self._layers:
            self._layers[layer_id].remove_from_scene(self._scene)
            del self._layers[layer_id]
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
    
    def wheelEvent(self, event: QWheelEvent):
        """Zoom in/out with mouse wheel."""
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1 / factor, 1 / factor)
        self._schedule_tile_update()
        self.update_label_markers_scale()
    
    def scrollContentsBy(self, dx: int, dy: int):
        """Called when view is scrolled (panned)."""
        super().scrollContentsBy(dx, dy)
        self._schedule_tile_update()
    
    def resizeEvent(self, event):
        """Called when view is resized."""
        super().resizeEvent(event)
        self._schedule_tile_update()
    
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
        else:
            super().mousePressEvent(event)
    
    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        super().mouseReleaseEvent(event)

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
                         image_name: str, image_group: str,
                         class_name: str, color: QColor = None):
        """Add a visual marker for a label on the canvas.
        
        Args:
            label_id: Unique ID of the label
            lon: Longitude (WGS84)
            lat: Latitude (WGS84)
            image_name: Name of the image the label belongs to
            image_group: Group path of the image
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
        ellipse.setZValue(self._label_z_base)
        
        # Create text label
        text = QGraphicsTextItem(class_name)
        text.setDefaultTextColor(Qt.white)
        font = QFont("Arial", 8)
        font.setBold(True)
        text.setFont(font)
        text.setScale(1 / view_scale if view_scale > 0 else 1)
        text.setPos(x + marker_size / 2, -y - marker_size / 2)
        text.setZValue(self._label_z_base + 1)
        
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
