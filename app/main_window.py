"""Main application window."""
from pathlib import Path

import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QSplitter, QMenuBar, QMenu, QAction, QFileDialog,
    QStatusBar, QLabel, QToolBar, QComboBox, QMessageBox, QProgressDialog,
    QApplication, QProgressBar, QSpinBox, QLineEdit, QPushButton, QHBoxLayout, QWidget
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

from .canvas import MapCanvas, CanvasMode, AsyncFileLoaderThread
from .layer_panel import CombinedLayerPanel
from .axis_ruler import MapCanvasWithAxes
from .labels import LabelProject, ImageData, PointLabel
from .class_editor import ClassEditorDialog


# Colors for different classes (cycles through these)
CLASS_COLORS = [
    QColor(255, 50, 50),    # Red
    QColor(50, 255, 50),    # Green
    QColor(50, 50, 255),    # Blue
    QColor(255, 255, 50),   # Yellow
    QColor(255, 50, 255),   # Magenta
    QColor(50, 255, 255),   # Cyan
    QColor(255, 128, 0),    # Orange
    QColor(128, 0, 255),    # Purple
]


class MainWindow(QMainWindow):
    """Main window with canvas and layer panel."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GeoLabel")
        self.setMinimumSize(1024, 768)
        
        # Label project
        self.project = LabelProject()
        self._project_path: Path | None = None
        
        # Async loading state (initialized here to avoid AttributeError)
        self._async_root_path = None
        self._async_group_cache: dict[Path, any] = {}
        self._async_loaded_count = 0
        self._async_total_files = 0
        self._async_loader = None
        self._async_pending_files: list[tuple[str, dict]] = []  # Queue for pending file loads
        self._async_mode: str = "directory"  # "directory" or "project" - controls post-load behavior
        self._async_missing_files: list[str] = []  # Track files that couldn't be found
        self._async_skip_project_add: bool = False  # Skip adding to project (for Open Project)
        
        # Timer for safe UI updates during async loading (avoids reentrancy issues)
        self._async_ui_timer = QTimer()
        self._async_ui_timer.setInterval(100)  # Update UI every 100ms
        self._async_ui_timer.timeout.connect(self._process_pending_async_files)
        
        # Custom reader state
        self._custom_reader_script: str | None = None
        self._custom_reader_func = None
        self._custom_extension = "png"  # Default extension for custom files
        
        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
    
    def _setup_ui(self):
        """Set up the main UI layout."""
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)
        
        # Combined layer panel on the left (includes labeled images panel)
        self.layer_panel = CombinedLayerPanel()
        splitter.addWidget(self.layer_panel)
        
        # Map canvas with axes on the right
        self.canvas = MapCanvas()
        self.canvas_with_axes = MapCanvasWithAxes(self.canvas)
        splitter.addWidget(self.canvas_with_axes)
        
        # Set initial sizes (layer panel smaller than canvas)
        splitter.setSizes([250, 774])
        
        self.setCentralWidget(splitter)
        
        # Set up status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        
        # Progress indicator for async operations
        self.progress_indicator = QProgressBar()
        self.progress_indicator.setMinimumWidth(200)
        self.progress_indicator.setMaximumWidth(300)
        self.progress_indicator.setMaximumHeight(16)
        self.progress_indicator.setTextVisible(True)
        self.progress_indicator.setFormat("%p% (%v/%m)")
        self.progress_indicator.hide()  # Hidden by default
        self.statusBar.addPermanentWidget(self.progress_indicator)
        
        self.coord_label = QLabel("")
        self.statusBar.addPermanentWidget(self.coord_label)
        
        # Connect signals
        self.layer_panel.layer_visibility_changed.connect(self.canvas.set_layer_visibility)
        self.layer_panel.layers_reordered.connect(self.canvas.update_layer_order)
        self.layer_panel.layer_group_changed.connect(self._on_layer_group_changed)
        self.layer_panel.zoom_to_layer_requested.connect(self.canvas.zoom_to_layer)
        self.layer_panel.zoom_to_label_requested.connect(self._on_zoom_to_label)
        self.layer_panel.layer_removed.connect(self.canvas.remove_layer)
        
        # Connect batch visibility progress signals for group toggle
        self.layer_panel.batch_visibility_started.connect(self._on_batch_visibility_started)
        self.layer_panel.batch_visibility_progress.connect(self._update_progress)
        self.layer_panel.batch_visibility_finished.connect(self._hide_progress)
        
        self.canvas.coordinates_changed.connect(self._update_coordinates)
        self.canvas.label_placed.connect(self._on_label_placed)
        self.canvas.label_removed.connect(self._on_label_removed)
        self.canvas.labels_linked.connect(self._on_labels_linked)
        self.canvas.label_unlinked.connect(self._on_label_unlinked)
        self.canvas.show_linked_requested.connect(self._on_show_linked)
        self.canvas.link_mode_changed.connect(self._on_link_mode_changed)
        self.canvas.hide_layers_outside_view.connect(self.layer_panel.uncheck_layers)
        self.canvas.show_layers_in_view.connect(self.layer_panel.check_layers)
        self.canvas.toggle_layer_visibility_requested.connect(self.layer_panel.toggle_layer_visibility)
    
    def _setup_menu(self):
        """Set up the menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        # New Project
        new_project_action = QAction("&New Project", self)
        new_project_action.setShortcut("Ctrl+N")
        new_project_action.triggered.connect(self._new_project)
        file_menu.addAction(new_project_action)
        
        # Open Project
        open_project_action = QAction("&Open Project...", self)
        open_project_action.setShortcut("Ctrl+Shift+P")
        open_project_action.triggered.connect(self._open_project)
        file_menu.addAction(open_project_action)
        
        # Save Project
        save_project_action = QAction("&Save Project", self)
        save_project_action.setShortcut("Ctrl+S")
        save_project_action.triggered.connect(self._save_project)
        file_menu.addAction(save_project_action)
        
        # Save Project As
        save_as_action = QAction("Save Project &As...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)
        
        file_menu.addSeparator()
        
        # Add GeoTIFF action
        add_action = QAction("&Add GeoTIFF...", self)
        add_action.setShortcut("Ctrl+O")
        add_action.triggered.connect(self._add_geotiff)
        file_menu.addAction(add_action)
        
        # Add Directory action
        add_dir_action = QAction("Add &Directory...", self)
        add_dir_action.setShortcut("Ctrl+Shift+O")
        add_dir_action.triggered.connect(self._add_directory)
        file_menu.addAction(add_dir_action)
        
        file_menu.addSeparator()
        
        # Custom Reader submenu
        custom_menu = file_menu.addMenu("Custom &Reader")
        
        # Set Reader Script
        set_reader_action = QAction("Set Reader &Script...", self)
        set_reader_action.triggered.connect(self._set_custom_reader_script)
        custom_menu.addAction(set_reader_action)
        
        # Add Custom File(s)
        add_custom_action = QAction("Add Custom &File(s)...", self)
        add_custom_action.triggered.connect(self._add_custom_files)
        custom_menu.addAction(add_custom_action)
        
        # Add Custom Directory
        add_custom_dir_action = QAction("Add Custom &Directory...", self)
        add_custom_dir_action.triggered.connect(self._add_custom_directory)
        custom_menu.addAction(add_custom_dir_action)
        
        file_menu.addSeparator()
        
        # Combine Projects
        combine_action = QAction("&Combine Projects...", self)
        combine_action.triggered.connect(self._combine_projects)
        file_menu.addAction(combine_action)
        
        file_menu.addSeparator()
        
        # Exit action
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Labels menu
        labels_menu = menubar.addMenu("&Labels")
        
        # Edit Classes
        edit_classes_action = QAction("Edit &Classes...", self)
        edit_classes_action.triggered.connect(self._edit_classes)
        labels_menu.addAction(edit_classes_action)
        
        labels_menu.addSeparator()
        
        # Clear all labels
        clear_labels_action = QAction("Clear All Labels", self)
        clear_labels_action.triggered.connect(self._clear_all_labels)
        labels_menu.addAction(clear_labels_action)
        
        # Export menu
        export_menu = menubar.addMenu("&Export")
        
        # Export Ground Truth
        export_gt_action = QAction("&Ground Truth...", self)
        export_gt_action.triggered.connect(self._export_ground_truth)
        export_menu.addAction(export_gt_action)
        
        # Export Ground Truth (Labeled Only)
        export_gt_labeled_action = QAction("Ground Truth (Labeled Only)...", self)
        export_gt_labeled_action.triggered.connect(self._export_ground_truth_labeled_only)
        export_menu.addAction(export_gt_labeled_action)

        # Export Sub-images
        export_subimages_action = QAction("&Sub-images...", self)
        export_subimages_action.triggered.connect(self._export_subimages)
        export_menu.addAction(export_subimages_action)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        
        # Keyboard Shortcuts
        shortcuts_action = QAction("&Keyboard Shortcuts...", self)
        shortcuts_action.setShortcut("F1")
        shortcuts_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_action)
        
        # About
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
    
    def _setup_toolbar(self):
        """Set up the toolbar for labeling."""
        toolbar = QToolBar("Labeling")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Mode selector
        toolbar.addWidget(QLabel(" Mode: "))
        
        self.pan_action = QAction("Pan", self)
        self.pan_action.setCheckable(True)
        self.pan_action.setChecked(True)
        self.pan_action.setShortcut("P")
        self.pan_action.triggered.connect(lambda: self._set_mode(CanvasMode.PAN))
        toolbar.addAction(self.pan_action)
        
        self.label_action = QAction("Label", self)
        self.label_action.setCheckable(True)
        self.label_action.setShortcut("L")
        self.label_action.triggered.connect(lambda: self._set_mode(CanvasMode.LABEL))
        toolbar.addAction(self.label_action)
        
        toolbar.addSeparator()
        
        # Class selector
        toolbar.addWidget(QLabel(" Class: "))
        self.class_combo = QComboBox()
        self.class_combo.setMinimumWidth(150)
        self.class_combo.currentTextChanged.connect(self._on_class_changed)
        toolbar.addWidget(self.class_combo)

        toolbar.addSeparator()
        
        # Custom reader extension control
        toolbar.addWidget(QLabel(" Custom Ext: "))
        self.custom_ext_edit = QLineEdit()
        self.custom_ext_edit.setPlaceholderText("png")
        self.custom_ext_edit.setMaximumWidth(60)
        self.custom_ext_edit.setToolTip(
            "File extension to search for when using custom reader (without dot)"
        )
        self.custom_ext_edit.textChanged.connect(self._on_custom_ext_changed)
        toolbar.addWidget(self.custom_ext_edit)
        
        # Reader status indicator
        self.reader_status_label = QLabel(" Reader: None")
        self.reader_status_label.setToolTip("No custom reader loaded. Use File > Custom Reader > Set Reader Script...")
        toolbar.addWidget(self.reader_status_label)
    
    def _set_mode(self, mode: CanvasMode):
        """Set the canvas interaction mode."""
        self.canvas.set_mode(mode)
        self.pan_action.setChecked(mode == CanvasMode.PAN)
        self.label_action.setChecked(mode == CanvasMode.LABEL)
    
    def _on_class_changed(self, class_name: str):
        """Handle class selection change."""
        self.canvas.set_current_class(class_name)
    
    def _update_class_combo(self):
        """Update the class combo box with current classes."""
        current = self.class_combo.currentText()
        self.class_combo.clear()
        self.class_combo.addItems(self.project.classes)
        
        # Restore selection if possible
        if current in self.project.classes:
            self.class_combo.setCurrentText(current)
        elif self.project.classes:
            self.class_combo.setCurrentIndex(0)
    
    def _get_class_color(self, class_name: str) -> QColor:
        """Get the color for a class."""
        if class_name in self.project.classes:
            idx = self.project.classes.index(class_name)
            return CLASS_COLORS[idx % len(CLASS_COLORS)]
        return CLASS_COLORS[0]
    
    def _on_label_placed(self, pixel_x: float, pixel_y: float, lon: float, lat: float, 
                         image_name: str, image_group: str, image_path: str):
        """Handle a new label being placed."""
        class_name = self.canvas.get_current_class()
        if not class_name:
            self.statusBar.showMessage("No class selected", 3000)
            return
        
        # Add to project
        label = self.project.add_label(
            class_name=class_name,
            pixel_x=pixel_x, pixel_y=pixel_y,
            lon=lon, lat=lat,
            image_name=image_name,
            image_group=image_group,
            image_path=image_path
        )
        
        # Add visual marker
        color = self._get_class_color(class_name)
        self.canvas.add_label_marker(
            label.id, lon, lat, image_name, image_group, image_path, class_name, color
        )
        
        # Refresh labeled images panel
        self.layer_panel.refresh_labeled_panel(self.project)
        
        self.statusBar.showMessage(
            f"Added label: {class_name} at ({lon:.6f}, {lat:.6f}) on {image_name}", 
            3000
        )
    
    def _on_label_removed(self, label_id: int, image_path: str):
        """Handle a label being removed."""
        # Remove from project
        self.project.remove_label(label_id)
        
        # Remove visual marker
        self.canvas.remove_label_marker(label_id)
        
        # Refresh labeled images panel
        self.layer_panel.refresh_labeled_panel(self.project)
        
        self.statusBar.showMessage(f"Removed label", 3000)
    
    def _on_labels_linked(self, label_id1: int, label_id2: int):
        """Handle two labels being linked."""
        object_id = self.project.link_labels(label_id1, label_id2)
        
        if object_id:
            # Update the linked status for all labels with this object_id
            linked_labels = self.project.get_linked_labels(label_id1)
            for _, label in linked_labels:
                self.canvas.set_label_linked(label.id, True)
            
            # Refresh labeled images panel (grouping may have changed)
            self.layer_panel.refresh_labeled_panel(self.project)
            
            count = len(linked_labels)
            self.statusBar.showMessage(
                f"Linked labels (object has {count} labels)", 3000
            )
        else:
            self.statusBar.showMessage("Failed to link labels", 3000)
    
    def _on_label_unlinked(self, label_id: int):
        """Handle a label being unlinked from its object group."""
        # First get the labels that were linked before unlinking
        old_linked = self.project.get_linked_labels(label_id)
        
        self.project.unlink_label(label_id)
        
        # Update the unlinked label
        self.canvas.set_label_linked(label_id, False)
        
        # Clear highlight from the unlinked label
        self.canvas.highlight_labels([label_id], highlight=False)
        
        # Update remaining linked labels (if only 1 left, it's no longer "linked")
        remaining = [l for _, l in old_linked if l.id != label_id]
        if len(remaining) == 1:
            self.canvas.set_label_linked(remaining[0].id, False)
            # Also clear highlight since it's no longer part of a group
            self.canvas.highlight_labels([remaining[0].id], highlight=False)
        
        # Refresh labeled images panel (grouping may have changed)
        self.layer_panel.refresh_labeled_panel(self.project)
        
        self.statusBar.showMessage("Label unlinked from object", 3000)
    
    def _on_show_linked(self, label_id: int):
        """Highlight all labels linked to the given label."""
        linked_labels = self.project.get_linked_labels(label_id)
        
        if linked_labels:
            # First, clear any existing highlights
            all_label_ids = [label.id for _, label in self.project.get_all_labels()]
            self.canvas.highlight_labels(all_label_ids, highlight=False)
            
            # Highlight linked labels
            linked_ids = [label.id for _, label in linked_labels]
            self.canvas.highlight_labels(linked_ids, highlight=True)
            
            self.statusBar.showMessage(
                f"Showing {len(linked_labels)} linked labels (click anywhere to clear)", 3000
            )
    
    def _on_link_mode_changed(self, is_active: bool, message: str):
        """Handle link mode state changes."""
        if is_active:
            self.statusBar.showMessage(message, 0)  # 0 = no timeout
        else:
            self.statusBar.clearMessage()
    
    def _on_zoom_to_label(self, lon: float, lat: float):
        """Zoom to a label by its coordinates."""
        self.canvas.zoom_to_point(lon, lat, size_meters=10.0)
        self.statusBar.showMessage(
            f"Zoomed to label at ({lon:.6f}, {lat:.6f})", 3000
        )
    
    def _refresh_label_markers(self):
        """Refresh all label markers on the canvas."""
        self.canvas.clear_label_markers()
        for image, label in self.project.get_all_labels():
            color = self._get_class_color(label.class_name)
            self.canvas.add_label_marker(
                label.id, label.lon, label.lat, 
                image.name, image.group, image.path,
                label.class_name, color
            )
            # Check if label is linked to others
            linked_labels = self.project.get_linked_labels(label.id)
            self.canvas.set_label_linked(label.id, len(linked_labels) > 1)
        
        # Refresh labeled images panel
        self.layer_panel.refresh_labeled_panel(self.project)
    
    def _edit_classes(self):
        """Open the class editor dialog."""
        dialog = ClassEditorDialog(self.project.classes, self)
        if dialog.exec_():
            new_classes = dialog.get_classes()
            
            # Find removed classes
            removed = set(self.project.classes) - set(new_classes)
            if removed:
                # Warn about label deletion
                count = sum(1 for l in self.project.labels if l.class_name in removed)
                if count > 0:
                    reply = QMessageBox.question(
                        self,
                        "Remove Classes",
                        f"Removing classes will delete {count} labels. Continue?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.No:
                        return
            
            # Update classes
            self.project.classes = new_classes
            
            # Remove labels for deleted classes
            for class_name in removed:
                self.project.remove_class(class_name)
            
            self._update_class_combo()
            self._refresh_label_markers()
    
    def _clear_all_labels(self):
        """Clear all labels after confirmation."""
        if self.project.label_count == 0:
            return
        
        reply = QMessageBox.question(
            self,
            "Clear Labels",
            f"Delete all {self.project.label_count} labels?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.project.clear()
            self.canvas.clear_label_markers()
            # Refresh labeled images panel (now empty)
            self.layer_panel.refresh_labeled_panel(self.project)
            self.statusBar.showMessage("All labels cleared", 3000)
    
    def _new_project(self):
        """Create a new project."""
        if self.project.label_count > 0 or self.project.images:
            reply = QMessageBox.question(
                self,
                "New Project",
                "Discard current project and labels?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        
        self.project = LabelProject()
        self._project_path = None
        self.canvas.clear_label_markers()
        self.canvas.clear_layers()
        self.layer_panel.clear()
        self._update_class_combo()
        self.setWindowTitle("GeoLabel")
        self.statusBar.showMessage("New project created", 3000)
    
    def _open_project(self):
        """Open a project file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            "",
            "GeoLabel Project (*.geolabel);;All Files (*)"
        )
        if file_path:
            try:
                # Clear existing state
                self.canvas.clear_label_markers()
                self.canvas.clear_layers()
                self.layer_panel.clear()
                
                self.project = LabelProject.load(file_path)
                self._project_path = Path(file_path)
                
                # Show progress for loading images
                num_images = len(self.project.images)
                if num_images > 0:
                    self._show_progress(num_images, "Loading project")
                    # Start async project loading
                    self._start_project_image_loading()
                else:
                    self._update_class_combo()
                    self._refresh_label_markers()
                    self.setWindowTitle(f"GeoLabel - {self._project_path.name}")
                    self.statusBar.showMessage(f"Opened project with {self.project.label_count} labels", 3000)
            except Exception as e:
                import traceback
                traceback.print_exc()
                QMessageBox.critical(self, "Error", f"Failed to open project: {e}")
    
    def _start_project_image_loading(self):
        """Start async loading of project images using the unified async loader."""
        import os
        
        # Prepare file list with group paths from project
        files_with_groups = []
        missing_files = []
        
        for image in self.project.images.values():
            if os.path.exists(image.path):
                files_with_groups.append((image.path, image.group or ""))
            else:
                missing_files.append(image.path)
        
        # Store missing files to report later
        self._async_missing_files = missing_files
        
        if not files_with_groups:
            # No valid files to load
            self._finish_async_loading_project()
            return
        
        # Use the unified async loader with project mode
        self._start_unified_async_loading(
            files_with_groups,
            mode="project",
            progress_label="Loading project",
            skip_project_add=True  # Images already in project
        )

    def _load_project_images(self):
        """Load images stored in the project and recreate group structure."""
        import os
        
        loaded = 0
        missing = []
        
        # Group cache for recreating hierarchy
        group_cache: dict[str, any] = {}
        
        def get_or_create_group(group_path: str):
            """Get or create group hierarchy for a group path."""
            if not group_path:
                return None
            
            if group_path in group_cache:
                return group_cache[group_path]
            
            # Split path and create hierarchy
            parts = group_path.replace("\\", "/").split("/")
            parent = None
            current_path = ""
            
            for part in parts:
                current_path = f"{current_path}/{part}" if current_path else part
                if current_path not in group_cache:
                    group = self.layer_panel.add_group(part, parent)
                    group_cache[current_path] = group
                parent = group_cache[current_path]
            
            return parent
        
        for idx, image in enumerate(self.project.images.values()):
            if os.path.exists(image.path):
                layer_id = self.canvas.add_layer(image.path)
                if layer_id:
                    # Recreate group structure
                    parent_group = get_or_create_group(image.group)
                    self.layer_panel.add_layer(layer_id, image.path, parent_group)
                    # Set the group path on the canvas layer
                    self.canvas.set_layer_group(layer_id, image.group)
                    loaded += 1
            else:
                missing.append(image.path)
            
            # Update progress indicator (progress bar repaints itself)
            self._update_progress(idx + 1)
        
        # Collapse all groups (user expands as needed)
        self.layer_panel.tree.collapseAll()
        
        if missing:
            QMessageBox.warning(
                self,
                "Missing Images",
                f"Could not find {len(missing)} image(s):\n" + 
                "\n".join(missing[:5]) +
                ("\n..." if len(missing) > 5 else "")
            )
    
    def _save_project(self):
        """Save the current project."""
        if self._project_path:
            self._do_save(self._project_path)
        else:
            self._save_project_as()
    
    def _save_project_as(self):
        """Save the project to a new file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            "",
            "GeoLabel Project (*.geolabel)"
        )
        if file_path:
            if not file_path.endswith('.geolabel'):
                file_path += '.geolabel'
            self._do_save(Path(file_path))
    
    def _do_save(self, path: Path):
        """Perform the actual save operation."""
        try:
            self.project.save(path)
            self._project_path = path
            self.setWindowTitle(f"GeoLabel - {path.name}")
            self.statusBar.showMessage(f"Saved {self.project.label_count} labels to {path.name}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project: {e}")
    
    def _combine_projects(self):
        """Combine two .geolabel project files into a new project file."""
        # Select first project file
        file1, _ = QFileDialog.getOpenFileName(
            self,
            "Select First Project to Combine",
            "",
            "GeoLabel Project (*.geolabel);;All Files (*)"
        )
        if not file1:
            return
        
        # Select second project file
        file2, _ = QFileDialog.getOpenFileName(
            self,
            "Select Second Project to Combine",
            "",
            "GeoLabel Project (*.geolabel);;All Files (*)"
        )
        if not file2:
            return
        
        # Select output file
        output_file, _ = QFileDialog.getSaveFileName(
            self,
            "Save Combined Project As",
            "",
            "GeoLabel Project (*.geolabel)"
        )
        if not output_file:
            return
        
        if not output_file.endswith('.geolabel'):
            output_file += '.geolabel'
        
        try:
            # Load both projects
            project1 = LabelProject.load(file1)
            project2 = LabelProject.load(file2)

            # Combine classes (deduplicate while preserving order)
            combined_classes = list(dict.fromkeys(project1.classes + project2.classes))

            # Create combined project and deep-copy images/labels from project1
            combined = LabelProject()
            combined.classes = combined_classes

            # Helper: clone ImageData (and contained labels) to avoid mutating originals
            def clone_image(image: ImageData) -> ImageData:
                return ImageData.from_dict(image.to_dict())

            # Track maximum label id
            max_id = 0

            for path, image in project1.images.items():
                new_img = clone_image(image)
                combined.images[path] = new_img
                for lbl in new_img.labels:
                    if lbl.id > max_id:
                        max_id = lbl.id

            # Offset for project2 labels to ensure unique IDs
            id_offset = max_id

            # Merge images and labels from project2 (cloned, with remapped ids)
            for path, image in project2.images.items():
                cloned = clone_image(image)
                for lbl in cloned.labels:
                    lbl.id = lbl.id + id_offset
                    if lbl.id > max_id:
                        max_id = lbl.id

                if path in combined.images:
                    combined.images[path].labels.extend(cloned.labels)
                else:
                    combined.images[path] = cloned

            # Set next id
            combined._next_id = max_id + 1

            # Save combined project
            combined.save(output_file)

            # Show summary
            QMessageBox.information(
                self,
                "Projects Combined",
                f"Successfully combined projects:\n\n"
                f"• Classes: {len(combined_classes)}\n"
                f"• Images: {len(combined.images)}\n"
                f"• Labels: {combined.label_count}\n\n"
                f"Saved to: {Path(output_file).name}"
            )

            self.statusBar.showMessage(f"Combined projects saved to {Path(output_file).name}", 5000)

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to combine projects: {e}")
    
    def _export_ground_truth(self):
        """Export ground truth labels to a JSON file."""
        if self.project.label_count == 0:
            QMessageBox.information(self, "Export", "No labels to export.")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Ground Truth",
            "",
            "JSON Files (*.json)"
        )
        if file_path:
            if not file_path.endswith('.json'):
                file_path += '.json'
            try:
                self.project.save(file_path)
                self.statusBar.showMessage(f"Exported {self.project.label_count} labels to {file_path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export ground truth: {e}")

    def _export_ground_truth_labeled_only(self):
        """Export ground truth JSON but include only images that have labels."""
        if self.project.label_count == 0:
            QMessageBox.information(self, "Export", "No labels to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Ground Truth (Labeled Only)",
            "",
            "JSON Files (*.json)"
        )
        if not file_path:
            return
        if not file_path.endswith('.json'):
            file_path += '.json'

        try:
            import json

            # Collect only images that have at least one label
            images = [img.to_dict() for img in self.project.images.values() if img.labels]

            if not images:
                QMessageBox.information(self, "Export", "No labeled images to export.")
                return

            data = {
                "version": "2.1",
                "classes": self.project.classes,
                "images": images,
                "_next_id": self.project._next_id
            }

            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)

            total_labels = sum(len(img['labels']) for img in images)
            self.statusBar.showMessage(
                f"Exported {total_labels} labels from {len(images)} images to {file_path}",
                3000
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export ground truth: {e}")

    def _export_subimages(self):
        """Export sub-images centered on labels as GeoTIFFs preserving original pixels."""
        from PyQt5.QtWidgets import QInputDialog, QProgressDialog
        from PyQt5.QtCore import Qt as QtCore_Qt
        import rasterio
        import os
        
        if self.project.label_count == 0:
            QMessageBox.information(self, "Export", "No labels to export.")
            return
        
        # Prompt for sub-image size in meters
        size_meters, ok = QInputDialog.getDouble(
            self,
            "Sub-image Size",
            "Enter the sub-image size in meters (width and height):",
            value=10.0,
            min=0.1,
            max=10000.0,
            decimals=2
        )
        if not ok:
            return
        
        # Prompt for output directory
        output_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory for Sub-images",
            "",
            QFileDialog.ShowDirsOnly
        )
        if not output_dir:
            return
        
        output_path = Path(output_dir)
        
        # Progress dialog
        progress = QProgressDialog("Exporting sub-images...", "Cancel", 0, self.project.label_count, self)
        progress.setWindowModality(QtCore_Qt.WindowModal)
        progress.setMinimumDuration(0)
        
        exported = 0
        errors = []
        
        for idx, (image_data, label) in enumerate(self.project.get_all_labels()):
            if progress.wasCanceled():
                break
            
            progress.setValue(idx)
            
            image_path = image_data.path
            if not os.path.exists(image_path):
                errors.append(f"Image not found: {image_path}")
                continue
            
            try:
                with rasterio.open(image_path) as src:
                    # Get the pixel resolution (meters per pixel)
                    # For projected CRS, transform coefficients give pixel size directly
                    # For geographic CRS, we need to approximate
                    transform = src.transform

                    # Handle missing CRS
                    if src.crs is None:
                        errors.append(f"Image has no CRS: {image_path}")
                        continue

                    if src.crs.is_geographic:
                        # Approximate meters per degree at the label's latitude
                        import math
                        lat_rad = math.radians(label.lat)
                        meters_per_deg_lat = 111320  # approximate
                        meters_per_deg_lon = 111320 * math.cos(lat_rad)
                        pixel_width_m = abs(transform.a) * meters_per_deg_lon
                        pixel_height_m = abs(transform.e) * meters_per_deg_lat
                    else:
                        # Projected CRS - transform gives pixel size in CRS units (usually meters)
                        pixel_width_m = abs(transform.a)
                        pixel_height_m = abs(transform.e)
                    
                    # Calculate pixel size for the requested meter size
                    half_size_px_x = max(1, int((size_meters / 2) / pixel_width_m))
                    half_size_px_y = max(1, int((size_meters / 2) / pixel_height_m))
                    full_size_px_x = half_size_px_x * 2
                    full_size_px_y = half_size_px_y * 2
                    
                    # Get pixel coordinates from the label
                    pixel_x = int(round(label.pixel_x))
                    pixel_y = int(round(label.pixel_y))
                    
                    # Skip if pixel coordinates are outside image bounds
                    if pixel_x < 0 or pixel_x >= src.width or pixel_y < 0 or pixel_y >= src.height:
                        errors.append(f"Label {label.id}: pixel coords ({pixel_x}, {pixel_y}) outside image bounds ({src.width}x{src.height})")
                        continue
                    
                    # Calculate initial window bounds (centered on label)
                    col_start = pixel_x - half_size_px_x
                    col_end = pixel_x + half_size_px_x
                    row_start = pixel_y - half_size_px_y
                    row_end = pixel_y + half_size_px_y
                    
                    # Handle edge cases by shifting the window to stay within bounds
                    # while maintaining the full requested size if possible
                    if col_start < 0:
                        # Shift window right
                        shift = -col_start
                        col_start = 0
                        col_end = min(src.width, col_end + shift)
                    if col_end > src.width:
                        # Shift window left
                        shift = col_end - src.width
                        col_end = src.width
                        col_start = max(0, col_start - shift)
                    
                    if row_start < 0:
                        # Shift window down
                        shift = -row_start
                        row_start = 0
                        row_end = min(src.height, row_end + shift)
                    if row_end > src.height:
                        # Shift window up
                        shift = row_end - src.height
                        row_end = src.height
                        row_start = max(0, row_start - shift)
                    
                    # Final clamp to ensure we're within bounds
                    col_start = max(0, col_start)
                    col_end = min(src.width, col_end)
                    row_start = max(0, row_start)
                    row_end = min(src.height, row_end)
                    
                    window_width = col_end - col_start
                    window_height = row_end - row_start
                    
                    # Skip if the resulting window is too small or invalid
                    if window_width <= 0 or window_height <= 0:
                        errors.append(f"Label {label.id}: invalid window size, skipped")
                        continue
                    
                    if window_width < full_size_px_x // 2 or window_height < full_size_px_y // 2:
                        errors.append(f"Label {label.id} too close to edge, skipped")
                        continue
                    
                    # Read the window using bounded reading
                    window = rasterio.windows.Window(
                        col_off=col_start,
                        row_off=row_start,
                        width=window_width,
                        height=window_height
                    )

                    # Use boundless=False to ensure we stay within image bounds
                    data = src.read(window=window, boundless=False)
                    
                    # Create output directory for this class
                    class_dir = output_path / label.class_name
                    class_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Generate unique filename: {object_id}_{label_id:06d}.tif
                    out_filename = f"{label.object_id}_{label.id:06d}.tif"
                    out_path = class_dir / out_filename
                    
                    # Calculate the transform for the sub-image window (preserves original CRS)
                    window_transform = rasterio.windows.transform(window, src.transform)
                    
                    # Convert to grayscale and normalize
                    num_bands = data.shape[0]

                    # Robust normalization across datatypes: convert to float in [0,1]
                    dtype = data.dtype
                    if np.issubdtype(dtype, np.integer):
                        scale = float(np.iinfo(dtype).max)
                        arr = data.astype(np.float32) / scale
                    else:
                        arr = data.astype(np.float32)
                        # If float data appears to be in 0-255 range, normalize
                        if arr.max() > 1.0:
                            arr = arr / 255.0

                    # Convert to grayscale using luminance weights (or average if single band)
                    if num_bands == 1:
                        gray = arr[0]
                    elif num_bands >= 3:
                        gray = (0.299 * arr[0] + 0.587 * arr[1] + 0.114 * arr[2])
                    else:
                        gray = np.mean(arr, axis=0)
                    
                    # Apply mean/std normalization: shift mean to 0.4, std to 0.2
                    current_mean = np.mean(gray)
                    current_std = np.std(gray)
                    
                    if current_std > 1e-6:  # Avoid division by zero
                        # Standardize (zero mean, unit std), then apply target mean/std
                        gray = (gray - current_mean) / current_std
                        gray = gray * 0.2 + 0.4
                    else:
                        # Flat image - just set to target mean
                        gray = np.full_like(gray, 0.4)
                    
                    # Scale to [0, 255] and clip
                    out_data = np.clip(gray * 255.0, 0, 255).astype(np.uint8)
                    
                    # Reshape for rasterio (1 band)
                    out_data = out_data[np.newaxis, :, :]
                    
                    # Save as grayscale GeoTIFF with original CRS and transform
                    with rasterio.open(
                        out_path,
                        'w',
                        driver='GTiff',
                        height=window_height,
                        width=window_width,
                        count=1,
                        dtype=np.uint8,
                        crs=src.crs,
                        transform=window_transform,
                        compress='lzw'
                    ) as dst:
                        dst.write(out_data)
                    
                    exported += 1
                    
            except Exception as e:
                errors.append(f"Error processing label {label.id} from {image_path}: {e}")
        
        progress.setValue(self.project.label_count)
        
        # Show results
        msg = f"Exported {exported} sub-images to {output_dir}"
        if errors:
            msg += f"\n\n{len(errors)} errors occurred:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n... and {len(errors) - 5} more errors"
            QMessageBox.warning(self, "Export Complete", msg)
        else:
            self.statusBar.showMessage(msg, 5000)
            QMessageBox.information(self, "Export Complete", msg)

    def _add_geotiff(self):
        """Open file dialog to add a GeoTIFF."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add GeoTIFF",
            "",
            "GeoTIFF Files (*.tif *.tiff);;All Files (*)"
        )
        
        skipped = 0
        for file_path in file_paths:
            # Check if already loaded
            if self.canvas.is_path_loaded(file_path):
                skipped += 1
                continue
            
            layer_id = self.canvas.add_layer(file_path)
            if layer_id:
                self.layer_panel.add_layer(layer_id, file_path)
                # Track the loaded image with original dimensions
                name = Path(file_path).stem
                width, height = self.canvas.get_layer_source_dimensions(layer_id)
                self.project.add_image(file_path, name, "", width, height)
        
        if skipped > 0:
            self.statusBar.showMessage(f"Skipped {skipped} already loaded image(s)", 3000)
    
    def _add_directory(self):
        """Open directory dialog and load all GeoTIFFs preserving directory structure.
        
        Uses async loading for better performance with large directories:
        - Files are discovered and tree structure is built immediately
        - Actual file loading happens in background
        - Layers default to hidden (unchecked) during import
        - User can start working while files continue loading
        """
        from pathlib import Path
        
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Directory with GeoTIFFs",
            "",
            QFileDialog.ShowDirsOnly
        )
        
        if not dir_path:
            return
        
        # Find all GeoTIFF files recursively
        root_path = Path(dir_path)
        tiff_files = list(root_path.rglob("*.tif")) + list(root_path.rglob("*.tiff"))
        
        # Sort by path for consistent ordering
        tiff_files.sort()
        
        if not tiff_files:
            self.statusBar.showMessage("No GeoTIFF files found in directory", 5000)
            return
        
        # Check for large import - use async for better UX
        use_async = len(tiff_files) > 50
        
        if use_async:
            self._add_directory_async(root_path, tiff_files)
        else:
            self._add_directory_sync(root_path, tiff_files)
    
    def _add_directory_sync(self, root_path: Path, tiff_files: list):
        """Synchronous directory loading for smaller imports."""
        # Create root group for the selected directory
        root_group_name = root_path.name
        root_group = self.layer_panel.add_group(root_group_name, None, visible=False)
        
        # Build directory structure with groups under the root group
        group_cache: dict[Path, any] = {}
        
        def get_or_create_group(rel_dir: Path):
            if rel_dir == Path("."):
                return root_group  # Files at root level go under the root group
            if rel_dir in group_cache:
                return group_cache[rel_dir]
            parent_group = get_or_create_group(rel_dir.parent)
            group = self.layer_panel.add_group(rel_dir.name, parent_group, visible=False)
            group_cache[rel_dir] = group
            return group
        
        # Create progress dialog
        progress = QProgressDialog(
            "Loading GeoTIFF files...",
            "Cancel",
            0,
            len(tiff_files),
            self
        )
        progress.setWindowTitle("Loading Images")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        
        loaded_count = 0
        for i, file_path in enumerate(tiff_files):
            if progress.wasCanceled():
                break
            
            progress.setValue(i)
            progress.setLabelText(f"Loading {file_path.name}...\n({i + 1} of {len(tiff_files)})")
            QApplication.processEvents()
            
            rel_path = file_path.relative_to(root_path)
            rel_dir = rel_path.parent
            parent_group = get_or_create_group(rel_dir)
            
            file_path_str = str(file_path)
            if self.canvas.is_path_loaded(file_path_str):
                continue
            
            layer_id = self.canvas.add_layer(file_path_str, visible=False)
            if layer_id:
                self.layer_panel.add_layer(layer_id, file_path_str, parent_group, visible=False)
                # Include root group name in the group path
                rel_dir_str = str(rel_dir).replace("\\", "/") if rel_dir != Path(".") else ""
                group_path_str = f"{root_group_name}/{rel_dir_str}" if rel_dir_str else root_group_name
                self.canvas.set_layer_group(layer_id, group_path_str)
                name = file_path.stem
                width, height = self.canvas.get_layer_source_dimensions(layer_id)
                self.project.add_image(file_path_str, name, group_path_str, width, height)
                loaded_count += 1
        
        progress.setValue(len(tiff_files))
        self.layer_panel.tree.collapseAll()
        
        if progress.wasCanceled():
            self.statusBar.showMessage(f"Loading cancelled. Loaded {loaded_count} of {len(tiff_files)} GeoTIFF files", 5000)
        else:
            self.statusBar.showMessage(f"Loaded {loaded_count} of {len(tiff_files)} GeoTIFF files", 5000)
    
    def _add_directory_async(self, root_path: Path, tiff_files: list):
        """Asynchronous directory loading for large imports.
        
        Layers are added with lazy loading (only bounds read initially) and 
        default to hidden. The tree updates progressively as files are discovered.
        """
        # Get root folder name for the group
        root_group_name = root_path.name
        
        # Prepare file list with group paths (prefixed with root folder name)
        files_with_groups = []
        for file_path in tiff_files:
            rel_path = file_path.relative_to(root_path)
            rel_dir = rel_path.parent
            rel_dir_str = str(rel_dir).replace("\\", "/") if rel_dir != Path(".") else ""
            # Prefix with root group name
            group_path_str = f"{root_group_name}/{rel_dir_str}" if rel_dir_str else root_group_name
            files_with_groups.append((str(file_path), group_path_str))
        
        # Use the unified async loader with directory mode
        self._start_unified_async_loading(
            files_with_groups,
            mode="directory",
            progress_label="Loading dir",
            skip_project_add=False  # Add images to project
        )
    
    def _start_unified_async_loading(self, files_with_groups: list[tuple[str, str]], 
                                      mode: str = "directory", 
                                      progress_label: str = "Loading",
                                      skip_project_add: bool = False):
        """Unified async loading for both Open Project and Add Directory.
        
        Args:
            files_with_groups: List of (file_path, group_path) tuples
            mode: "directory" or "project" - controls completion behavior
            progress_label: Label shown in progress bar
            skip_project_add: If True, don't add images to project (they're already there)
        """
        # Store state for the async operation
        self._async_group_cache: dict[Path, any] = {}
        self._async_loaded_count = 0
        self._async_total_files = len(files_with_groups)
        self._async_mode = mode
        self._async_skip_project_add = skip_project_add
        
        # Create and start the async loader
        self._async_loader = AsyncFileLoaderThread(self)
        self._async_loader.set_files(files_with_groups)
        
        # Connect signals
        self._async_loader.file_loaded.connect(self._on_async_file_loaded)
        self._async_loader.file_error.connect(self._on_async_file_error)
        self._async_loader.batch_complete.connect(self._on_async_batch_complete)
        self._async_loader.progress_update.connect(self._on_async_progress)
        
        # Show progress indicator and status
        self._show_progress(len(files_with_groups), progress_label)
        status_msg = f"Loading {len(files_with_groups)} files in background..."
        if mode == "directory":
            status_msg += " (layers hidden by default)"
        self.statusBar.showMessage(status_msg)
        
        # Start the UI update timer
        self._async_ui_timer.start()
        self._async_loader.start()
    
    def _get_or_create_group_async(self, group_path: str):
        """Get or create group hierarchy for async loading."""
        if not group_path:
            return None
        
        # Convert to Path for consistency
        rel_dir = Path(group_path.replace("/", "\\"))
        
        if rel_dir in self._async_group_cache:
            return self._async_group_cache[rel_dir]
        
        # Build path parts
        parts = group_path.split("/")
        parent = None
        current_path = ""
        
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            current_key = Path(current_path.replace("/", "\\"))
            
            if current_key not in self._async_group_cache:
                # Create group with visible=False for async imports
                group = self.layer_panel.add_group(part, parent, visible=False)
                self._async_group_cache[current_key] = group
            parent = self._async_group_cache[current_key]
        
        return parent
    
    def _on_async_file_loaded(self, file_path: str, layer_data: dict):
        """Handle a file being loaded asynchronously.
        
        Queues the file for processing - actual tree updates happen via timer
        to avoid reentrancy issues when user interacts with UI during loading.
        """
        if self.canvas.is_path_loaded(file_path):
            return
        
        # Queue the file for processing
        self._async_pending_files.append((file_path, layer_data))
    
    def _process_pending_async_files(self):
        """Process queued async files and update UI.
        
        Called by timer to safely update the tree without reentrancy issues.
        Handles both directory import and project loading modes.
        """
        if not self._async_pending_files:
            return
        
        # Process a smaller batch to keep UI responsive
        # Each file involves rasterio file opening + tree update
        batch_size = min(5, len(self._async_pending_files))
        batch = self._async_pending_files[:batch_size]
        self._async_pending_files = self._async_pending_files[batch_size:]
        
        # Use batch mode to suppress tree updates during batch processing
        self.layer_panel.begin_batch_update()
        
        try:
            for file_path, layer_data in batch:
                if self.canvas.is_path_loaded(file_path):
                    continue
                
                group_path = layer_data['group_path']
                parent_group = self._get_or_create_group_async(group_path)
                
                # Add layer with lazy loading and hidden by default
                layer_id = self.canvas.add_layer(file_path, lazy=True, visible=False)
                if layer_id:
                    # Add to tree as hidden (unchecked)
                    self.layer_panel.add_layer(layer_id, file_path, parent_group, visible=False)
                    self.canvas.set_layer_group(layer_id, group_path)
                    
                    # Track in project with original dimensions (skip for project loading)
                    if not self._async_skip_project_add:
                        name = Path(file_path).stem
                        width, height = self.canvas.get_layer_source_dimensions(layer_id)
                        self.project.add_image(file_path, name, group_path, width, height)
                    
                    self._async_loaded_count += 1
        finally:
            self.layer_panel.end_batch_update()
    
    def _on_async_file_error(self, file_path: str, error: str):
        """Handle a file failing to load."""
        print(f"Failed to load {file_path}: {error}")
    
    def _on_async_progress(self, processed: int, total: int):
        """Handle progress updates during async loading."""
        self._update_progress(processed)
        self.statusBar.showMessage(
            f"Loading files: {processed}/{total} ({self._async_loaded_count} added)..."
        )
    
    def _on_async_batch_complete(self, loaded: int, errors: int):
        """Handle async loading completion for both directory and project modes."""
        # Stop the UI update timer
        self._async_ui_timer.stop()
        
        # Process any remaining pending files with progress events
        from PyQt5.QtWidgets import QApplication
        while self._async_pending_files:
            self._process_pending_async_files()
            QApplication.processEvents()  # Keep UI responsive during final batch
        
        # Hide progress indicator
        self._hide_progress()
        
        # Collapse all groups (user expands as needed)
        self.layer_panel.tree.collapseAll()
        
        # Clean up loader
        if hasattr(self, '_async_loader') and self._async_loader is not None:
            self._async_loader.wait()  # Ensure thread is finished
            self._async_loader.deleteLater()
            self._async_loader = None
        
        # Call mode-specific completion handler
        if self._async_mode == "project":
            self._finish_async_loading_project(errors)
        else:
            self._finish_async_loading_directory(errors)
    
    def _finish_async_loading_directory(self, errors: int = 0):
        """Complete directory loading after all files are processed."""
        msg = f"Loaded {self._async_loaded_count} GeoTIFF files"
        if errors > 0:
            msg += f" ({errors} errors)"
        msg += ". Check layers to display."
        self.statusBar.showMessage(msg, 10000)
    
    def _finish_async_loading_project(self, errors: int = 0):
        """Complete project loading after all images are processed."""
        # Update UI for project
        self._update_class_combo()
        self._refresh_label_markers()
        self.setWindowTitle(f"GeoLabel - {self._project_path.name}")
        
        # Build status message
        msg = f"Opened project with {self.project.label_count} labels"
        if errors > 0:
            msg += f" ({errors} load errors)"
        self.statusBar.showMessage(msg, 3000)
        
        # Show warning for missing images
        if self._async_missing_files:
            QMessageBox.warning(
                self,
                "Missing Images",
                f"Could not find {len(self._async_missing_files)} image(s):\n" + 
                "\n".join(self._async_missing_files[:5]) +
                ("\n..." if len(self._async_missing_files) > 5 else "")
            )
            self._async_missing_files = []  # Reset
    
    def _on_batch_visibility_started(self, total: int):
        """Handle start of batch visibility change (e.g., group toggle)."""
        self._show_progress(total, "Toggling")
    
    def _show_progress(self, maximum: int, label: str = "Loading"):
        """Show the progress indicator with a maximum value."""
        self.progress_indicator.setMaximum(maximum)
        self.progress_indicator.setValue(0)
        self.progress_indicator.setFormat(f"{label}: %p% (%v/%m)")
        self.progress_indicator.show()
    
    def _update_progress(self, value: int):
        """Update the progress indicator value."""
        self.progress_indicator.setValue(value)
    
    def _hide_progress(self):
        """Hide the progress indicator."""
        self.progress_indicator.hide()
        self.progress_indicator.setValue(0)
    
    def _update_coordinates(self, lon: float, lat: float, layer_name: str, group_path: str):
        """Update the coordinate display in the status bar."""
        if layer_name:
            # Build display name with group path if present
            if group_path:
                display_name = f"{group_path}/{layer_name.lstrip('~')}"
            else:
                display_name = layer_name.lstrip('~')
            
            if layer_name.startswith("~"):
                # Layer name prefixed with ~ means "closest to"
                self.coord_label.setText(f"Lon: {lon:.6f}°  Lat: {lat:.6f}°  |  Nearest: {display_name}")
            else:
                self.coord_label.setText(f"Lon: {lon:.6f}°  Lat: {lat:.6f}°  |  Image: {display_name}")
        else:
            self.coord_label.setText(f"Lon: {lon:.6f}°  Lat: {lat:.6f}°")
    
    def _on_layer_group_changed(self, layer_id: str, group_path: str):
        """Handle layer group change - update both canvas and project."""
        # Update canvas
        self.canvas.set_layer_group(layer_id, group_path)
        
        # Update project
        file_path = self.canvas.get_layer_file_path(layer_id)
        if file_path:
            self.project.update_image_group(file_path, group_path)
    
    def _show_shortcuts(self):
        """Show keyboard shortcuts dialog."""
        shortcuts_text = """
<h2>Keyboard Shortcuts</h2>

<h3>File Operations</h3>
<table>
<tr><td><b>Ctrl+N</b></td><td>New Project</td></tr>
<tr><td><b>Ctrl+Shift+P</b></td><td>Open Project</td></tr>
<tr><td><b>Ctrl+S</b></td><td>Save Project</td></tr>
<tr><td><b>Ctrl+Shift+S</b></td><td>Save Project As</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Add GeoTIFF</td></tr>
<tr><td><b>Ctrl+Shift+O</b></td><td>Add Directory</td></tr>
<tr><td><b>Ctrl+Q</b></td><td>Exit</td></tr>
</table>

<h3>Navigation</h3>
<table>
<tr><td><b>Mouse Wheel</b></td><td>Zoom in/out</td></tr>
<tr><td><b>Click + Drag</b></td><td>Pan (in Pan mode)</td></tr>
<tr><td><b>Right-click</b></td><td>Context menu</td></tr>
</table>

<h3>Labeling</h3>
<table>
<tr><td><b>Left-click</b></td><td>Place label (in Label mode)</td></tr>
<tr><td><b>Right-click label</b></td><td>Label options (remove, link)</td></tr>
<tr><td><b>Escape</b></td><td>Cancel link mode</td></tr>
</table>

<h3>Layer Panel</h3>
<table>
<tr><td><b>Checkbox</b></td><td>Toggle layer/group visibility</td></tr>
<tr><td><b>Right-click group</b></td><td>Select/Unselect all, Expand/Collapse All</td></tr>
<tr><td><b>Right-click layer</b></td><td>Zoom to layer, Remove</td></tr>
<tr><td><b>Drag & Drop</b></td><td>Reorder layers/groups</td></tr>
</table>

<h3>Labeled Images Panel</h3>
<table>
<tr><td><b>Checkbox</b></td><td>Toggle image visibility (synced with layers)</td></tr>
<tr><td><b>Right-click label</b></td><td>Zoom to label or layer</td></tr>
<tr><td><b>Right-click group</b></td><td>Select/Unselect all in group</td></tr>
</table>

<h3>Help</h3>
<table>
<tr><td><b>F1</b></td><td>Show this help</td></tr>
</table>

<h3>Tips</h3>
<ul>
<li>Layers default to hidden when loading - expand groups and check to display</li>
<li>Turning on a layer automatically checks its parent groups</li>
<li>Add Directory creates a root group named after the selected folder</li>
<li>Visibility syncs between Layer Panel and Labeled Images Panel</li>
</ul>
"""
        msg = QMessageBox(self)
        msg.setWindowTitle("Keyboard Shortcuts & Tips")
        msg.setTextFormat(Qt.RichText)
        msg.setText(shortcuts_text)
        msg.setIcon(QMessageBox.Information)
        msg.exec_()
    
    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About GeoLabel",
            "<h2>GeoLabel</h2>"
            "<p>A geospatial image labeling tool for creating ground truth datasets.</p>"
            "<p>Load GeoTIFF images, place point labels, and export annotations "
            "for machine learning workflows.</p>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>Multi-layer GeoTIFF support</li>"
            "<li>Web Mercator reprojection</li>"
            "<li>Point labeling with custom classes</li>"
            "<li>Label linking across images</li>"
            "<li>Ground truth export</li>"
            "</ul>"
        )
    
    # -------------------------------------------------------------------------
    # Custom Reader Methods
    # -------------------------------------------------------------------------
    
    def _on_custom_ext_changed(self, text: str):
        """Handle custom extension text change."""
        # Strip any leading dots and whitespace
        ext = text.strip().lstrip('.')
        self._custom_extension = ext if ext else "png"
    
    def _set_custom_reader_script(self):
        """Open dialog to select a custom reader script."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Custom Reader Script",
            "",
            "Python Files (*.py);;All Files (*)"
        )
        
        if not file_path:
            return
        
        try:
            from .custom_reader import load_reader_function
            
            self._custom_reader_func = load_reader_function(file_path)
            self._custom_reader_script = file_path
            
            # Update status
            script_name = Path(file_path).name
            self.reader_status_label.setText(f" Reader: {script_name}")
            self.reader_status_label.setToolTip(f"Custom reader loaded: {file_path}")
            self.reader_status_label.setStyleSheet("color: green;")
            
            self.statusBar.showMessage(f"Custom reader loaded: {script_name}", 5000)
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Loading Reader",
                f"Failed to load custom reader script:\n\n{e}"
            )
            self._custom_reader_func = None
            self._custom_reader_script = None
            self.reader_status_label.setText(" Reader: None")
            self.reader_status_label.setToolTip("No custom reader loaded")
            self.reader_status_label.setStyleSheet("")
    
    def _add_custom_files(self):
        """Add files using the custom reader."""
        if self._custom_reader_func is None:
            QMessageBox.warning(
                self,
                "No Reader Loaded",
                "Please load a custom reader script first.\n\n"
                "Go to File > Custom Reader > Set Reader Script..."
            )
            return
        
        ext = self._custom_extension
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Custom Files",
            "",
            f"Custom Files (*.{ext});;All Files (*)"
        )
        
        if not file_paths:
            return
        
        skipped = 0
        loaded = 0
        errors = []
        
        for file_path in file_paths:
            if self.canvas.is_path_loaded(file_path):
                skipped += 1
                continue
            
            try:
                layer_id = self.canvas.add_custom_layer(
                    file_path,
                    self._custom_reader_func
                )
                if layer_id:
                    self.layer_panel.add_layer(layer_id, file_path)
                    name = Path(file_path).stem
                    width, height = self.canvas.get_layer_source_dimensions(layer_id)
                    self.project.add_image(file_path, name, "", width, height)
                    loaded += 1
            except Exception as e:
                errors.append(f"{Path(file_path).name}: {e}")
        
        msg = f"Loaded {loaded} file(s)"
        if skipped:
            msg += f", skipped {skipped} already loaded"
        if errors:
            msg += f", {len(errors)} error(s)"
            QMessageBox.warning(self, "Loading Complete", msg + "\n\nErrors:\n" + "\n".join(errors[:5]))
        else:
            self.statusBar.showMessage(msg, 5000)
    
    def _add_custom_directory(self):
        """Add a directory of files using the custom reader."""
        if self._custom_reader_func is None:
            QMessageBox.warning(
                self,
                "No Reader Loaded",
                "Please load a custom reader script first.\n\n"
                "Go to File > Custom Reader > Set Reader Script..."
            )
            return
        
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Directory with Custom Files",
            "",
            QFileDialog.ShowDirsOnly
        )
        
        if not dir_path:
            return
        
        root_path = Path(dir_path)
        ext = self._custom_extension
        custom_files = list(root_path.rglob(f"*.{ext}"))
        custom_files.sort()
        
        if not custom_files:
            self.statusBar.showMessage(f"No .{ext} files found in directory", 5000)
            return
        
        # Create progress dialog
        progress = QProgressDialog(
            f"Loading .{ext} files...",
            "Cancel",
            0,
            len(custom_files),
            self
        )
        progress.setWindowTitle("Loading Custom Files")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        
        # Build group hierarchy cache
        group_cache: dict[Path, any] = {}
        
        def get_or_create_group(rel_dir: Path):
            if rel_dir == Path(".") or str(rel_dir) == ".":
                return None
            if rel_dir in group_cache:
                return group_cache[rel_dir]
            parent_group = get_or_create_group(rel_dir.parent)
            group = self.layer_panel.add_group(rel_dir.name, parent_group)
            group_cache[rel_dir] = group
            return group
        
        loaded_count = 0
        for i, file_path in enumerate(custom_files):
            if progress.wasCanceled():
                break
            
            progress.setValue(i)
            progress.setLabelText(f"Loading {file_path.name}...\n({i + 1} of {len(custom_files)})")
            QApplication.processEvents()
            
            file_path_str = str(file_path)
            if self.canvas.is_path_loaded(file_path_str):
                continue
            
            rel_path = file_path.relative_to(root_path)
            rel_dir = rel_path.parent
            parent_group = get_or_create_group(rel_dir)
            
            try:
                layer_id = self.canvas.add_custom_layer(
                    file_path_str,
                    self._custom_reader_func
                )
                if layer_id:
                    self.layer_panel.add_layer(layer_id, file_path_str, parent_group)
                    group_path_str = str(rel_dir).replace("\\", "/") if rel_dir != Path(".") else ""
                    self.canvas.set_layer_group(layer_id, group_path_str)
                    name = file_path.stem
                    width, height = self.canvas.get_layer_source_dimensions(layer_id)
                    self.project.add_image(file_path_str, name, group_path_str, width, height)
                    loaded_count += 1
            except Exception as e:
                print(f"Error loading {file_path}: {e}")
        
        progress.setValue(len(custom_files))
        self.layer_panel.tree.collapseAll()
        
        if progress.wasCanceled():
            self.statusBar.showMessage(f"Loading cancelled. Loaded {loaded_count} of {len(custom_files)} files", 5000)
        else:
            self.statusBar.showMessage(f"Loaded {loaded_count} of {len(custom_files)} .{ext} files", 5000)

    def closeEvent(self, event):
        """Handle window close - ensure async loader is properly cleaned up."""
        # Cancel and wait for any running async loader
        if hasattr(self, '_async_loader') and self._async_loader is not None:
            if self._async_loader.isRunning():
                self._async_loader.cancel()
                self._async_loader.wait()
            self._async_loader = None
        
        # Stop the UI timer if running
        if hasattr(self, '_async_ui_timer'):
            self._async_ui_timer.stop()
        
        super().closeEvent(event)
