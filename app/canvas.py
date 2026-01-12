"""Map canvas for displaying GeoTIFF images."""
import numpy as np
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene
from PyQt5.QtGui import QImage, QPixmap, QWheelEvent
from PyQt5.QtCore import Qt
import rasterio


class MapCanvas(QGraphicsView):
    """Canvas widget for displaying geospatial raster layers."""
    
    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene()
        self.setScene(self._scene)
        
        # Enable pan and zoom
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        
        # Layer storage: {layer_id: {pixmap_item, visible, file_path, transform}}
        self._layers = {}
        self._layer_order = []  # Bottom to top
        self._next_id = 1
    
    def add_layer(self, file_path: str) -> str | None:
        """Add a GeoTIFF layer to the canvas.
        
        Returns the layer ID or None if loading failed.
        """
        try:
            pixmap = self._load_geotiff(file_path)
            if pixmap is None:
                return None
            
            pixmap_item = self._scene.addPixmap(pixmap)
            layer_id = f"layer_{self._next_id}"
            self._next_id += 1
            
            self._layers[layer_id] = {
                "pixmap_item": pixmap_item,
                "visible": True,
                "file_path": file_path,
            }
            self._layer_order.append(layer_id)
            self._update_z_order()
            
            # Fit view to scene on first layer
            if len(self._layers) == 1:
                self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
            
            return layer_id
            
        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            return None
    
    def _load_geotiff(self, file_path: str) -> QPixmap | None:
        """Load a GeoTIFF and convert to QPixmap."""
        with rasterio.open(file_path) as src:
            # Read data (handle different band counts)
            if src.count >= 3:
                # RGB or more - use first 3 bands
                r = src.read(1)
                g = src.read(2)
                b = src.read(3)
            elif src.count == 1:
                # Single band - display as grayscale
                r = g = b = src.read(1)
            else:
                return None
            
            # Normalize to 0-255 if needed
            def normalize(band):
                band = band.astype(np.float32)
                # Handle nodata
                valid = np.isfinite(band)
                if not valid.any():
                    return np.zeros_like(band, dtype=np.uint8)
                min_val = np.nanmin(band[valid])
                max_val = np.nanmax(band[valid])
                if max_val > min_val:
                    band = (band - min_val) / (max_val - min_val) * 255
                return np.clip(band, 0, 255).astype(np.uint8)
            
            r = normalize(r)
            g = normalize(g)
            b = normalize(b)
            
            # Create RGBA array
            height, width = r.shape
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            rgba[:, :, 0] = r
            rgba[:, :, 1] = g
            rgba[:, :, 2] = b
            rgba[:, :, 3] = 255  # Fully opaque
            
            # Convert to QImage then QPixmap
            image = QImage(
                rgba.data,
                width,
                height,
                width * 4,
                QImage.Format_RGBA8888
            )
            # Make a copy since rgba data goes out of scope
            return QPixmap.fromImage(image.copy())
    
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
