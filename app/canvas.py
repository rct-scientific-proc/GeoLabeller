"""Map canvas for displaying GeoTIFF images with Web Mercator reprojection."""
import math
import tempfile
from pathlib import Path

import numpy as np
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem
from PyQt5.QtGui import QImage, QPixmap, QWheelEvent, QTransform
from PyQt5.QtCore import Qt, pyqtSignal
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.crs import CRS


# Web Mercator CRS
WEB_MERCATOR = CRS.from_epsg(3857)


class MapCanvas(QGraphicsView):
    """Canvas widget for displaying geospatial raster layers."""
    
    # Signal emitted when mouse moves: (longitude, latitude)
    coordinates_changed = pyqtSignal(float, float)
    
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
        self.setSceneRect(-1e10, -1e10, 2e10, 2e10)  # Large scene for panning
        
        # Layer storage: {layer_id: {pixmap_item, visible, file_path, bounds}}
        self._layers = {}
        self._layer_order = []  # Bottom to top
        self._next_id = 1
    
    def add_layer(self, file_path: str) -> str | None:
        """Add a GeoTIFF layer to the canvas.
        
        Returns the layer ID or None if loading failed.
        """
        try:
            pixmap, bounds = self._load_geotiff(file_path)
            if pixmap is None:
                return None
            
            pixmap_item = self._scene.addPixmap(pixmap)
            
            # Position and scale the image to Web Mercator coordinates
            # Scene coords: X = easting, Y = -northing (Y flipped for screen)
            west, south, east, north = bounds
            pixel_width = pixmap.width()
            pixel_height = pixmap.height()
            
            # Calculate scale to map pixels to world units
            scale_x = (east - west) / pixel_width
            scale_y = (north - south) / pixel_height
            
            # Apply transform
            transform = QTransform()
            transform.scale(scale_x, scale_y)
            pixmap_item.setTransform(transform)
            
            # Position at top-left corner
            pixmap_item.setPos(west, -north)
            
            layer_id = f"layer_{self._next_id}"
            self._next_id += 1
            
            self._layers[layer_id] = {
                "pixmap_item": pixmap_item,
                "visible": True,
                "file_path": file_path,
                "bounds": bounds,
            }
            self._layer_order.append(layer_id)
            self._update_z_order()
            
            # Fit view to scene on first layer
            if len(self._layers) == 1:
                self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
            
            return layer_id
            
        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _load_geotiff(self, file_path: str) -> tuple[QPixmap | None, tuple | None]:
        """Load a GeoTIFF, reproject to Web Mercator, and convert to QPixmap.
        
        Returns (pixmap, bounds) where bounds is (west, south, east, north) in Web Mercator.
        """
        with rasterio.open(file_path) as src:
            # Calculate the transform for Web Mercator
            dst_crs = WEB_MERCATOR
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            
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
            
            # Get bounds in Web Mercator
            bounds = rasterio.transform.array_bounds(height, width, transform)
            
            # Convert bands to RGBA
            if len(bands) >= 3:
                r, g, b = bands[0], bands[1], bands[2]
            else:
                r = g = b = bands[0]
            
            # Clip to 0-255
            r = np.clip(r, 0, 255).astype(np.uint8)
            g = np.clip(g, 0, 255).astype(np.uint8)
            b = np.clip(b, 0, 255).astype(np.uint8)
            
            # Create RGBA array
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            rgba[:, :, 0] = r
            rgba[:, :, 1] = g
            rgba[:, :, 2] = b
            rgba[:, :, 3] = 255
            
            # Convert to QImage then QPixmap
            image = QImage(
                rgba.data,
                width,
                height,
                width * 4,
                QImage.Format_RGBA8888
            )
            return QPixmap.fromImage(image.copy()), bounds
    
    def set_layer_visibility(self, layer_id: str, visible: bool):
        """Show or hide a layer."""
        if layer_id in self._layers:
            self._layers[layer_id]["visible"] = visible
            self._layers[layer_id]["pixmap_item"].setVisible(visible)
    
    def update_layer_order(self, layer_order: list[str]):
        """Update the rendering order of layers."""
        self._layer_order = layer_order
        self._update_z_order()
    
    def _update_z_order(self):
        """Update z-values based on layer order."""
        for i, layer_id in enumerate(self._layer_order):
            if layer_id in self._layers:
                self._layers[layer_id]["pixmap_item"].setZValue(i)
    
    def remove_layer(self, layer_id: str):
        """Remove a layer from the canvas."""
        if layer_id in self._layers:
            self._scene.removeItem(self._layers[layer_id]["pixmap_item"])
            del self._layers[layer_id]
            if layer_id in self._layer_order:
                self._layer_order.remove(layer_id)
    
    def wheelEvent(self, event: QWheelEvent):
        """Zoom in/out with mouse wheel."""
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1 / factor, 1 / factor)
    
    def mouseMoveEvent(self, event):
        """Track mouse position and emit lat/lon coordinates."""
        super().mouseMoveEvent(event)
        
        # Convert viewport position to scene coordinates
        scene_pos = self.mapToScene(event.pos())
        
        # Scene coords: X = easting, Y = -northing
        easting = scene_pos.x()
        northing = -scene_pos.y()
        
        # Convert Web Mercator to WGS84 lat/lon
        lon, lat = self._web_mercator_to_wgs84(easting, northing)
        
        self.coordinates_changed.emit(lon, lat)
    
    def _web_mercator_to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        """Convert Web Mercator (EPSG:3857) to WGS84 (EPSG:4326).
        
        Args:
            x: Easting in meters
            y: Northing in meters
            
        Returns:
            (longitude, latitude) in degrees
        """
        # Web Mercator uses a sphere with radius 6378137 meters
        R = 6378137.0
        
        lon = math.degrees(x / R)
        lat = math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
        
        return lon, lat
