"""Main application window."""
from PyQt5.QtWidgets import (
    QMainWindow, QSplitter, QMenuBar, QMenu, QAction, QFileDialog,
    QStatusBar, QLabel
)
from PyQt5.QtCore import Qt

from .canvas import MapCanvas
from .layer_panel import LayerPanel


class MainWindow(QMainWindow):
    """Main window with canvas and layer panel."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GeoLabel")
        self.setMinimumSize(1024, 768)
        
        self._setup_ui()
        self._setup_menu()
    
    def _setup_ui(self):
        """Set up the main UI layout."""
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)
        
        # Layer panel on the left
        self.layer_panel = LayerPanel()
        splitter.addWidget(self.layer_panel)
        
        # Map canvas on the right
        self.canvas = MapCanvas()
        splitter.addWidget(self.canvas)
        
        # Set initial sizes (layer panel smaller than canvas)
        splitter.setSizes([250, 774])
        
        self.setCentralWidget(splitter)
        
        # Set up status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.coord_label = QLabel("")
        self.statusBar.addPermanentWidget(self.coord_label)
        
        # Connect signals
        self.layer_panel.layer_visibility_changed.connect(self.canvas.set_layer_visibility)
        self.layer_panel.layers_reordered.connect(self.canvas.update_layer_order)
        self.canvas.coordinates_changed.connect(self._update_coordinates)
    
    def _setup_menu(self):
        """Set up the menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        # Add GeoTIFF action
        add_action = QAction("&Add GeoTIFF...", self)
        add_action.setShortcut("Ctrl+O")
        add_action.triggered.connect(self._add_geotiff)
        file_menu.addAction(add_action)
        
        file_menu.addSeparator()
        
        # Exit action
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
    
    def _add_geotiff(self):
        """Open file dialog to add a GeoTIFF."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add GeoTIFF",
            "",
            "GeoTIFF Files (*.tif *.tiff);;All Files (*)"
        )
        
        for file_path in file_paths:
            layer_id = self.canvas.add_layer(file_path)
            if layer_id:
                self.layer_panel.add_layer(layer_id, file_path)
    
    def _update_coordinates(self, lon: float, lat: float):
        """Update the coordinate display in the status bar."""
        self.coord_label.setText(f"Lon: {lon:.6f}°  Lat: {lat:.6f}°")
