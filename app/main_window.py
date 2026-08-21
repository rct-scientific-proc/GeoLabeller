"""Main application window."""
import json
import math
import os
import platform
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

import rasterio
from pyproj import Transformer
from PyQt5.QtCore import (Qt, Qt as QtCore_Qt, QTimer, QEvent, QThread,
                          QObject, QUrl, pyqtSignal)
from PyQt5.QtGui import QColor, QDesktopServices, QKeyEvent
from PyQt5.QtWidgets import (
    QMainWindow,
    QSplitter,
    QAction,
    QFileDialog,
    QStatusBar,
    QLabel,
    QToolBar,
    QComboBox,
    QMessageBox,
    QProgressDialog,
    QApplication,
    QProgressBar,
    QInputDialog)

from .axis_ruler import MapCanvasWithAxes
from .canvas import (MapCanvas, CanvasMode, STEP_CYCLE_MODES,
                     AsyncFileLoaderThread, TiledLayer)
from .class_editor import ClassEditorDialog
from .goto_location import (GoToLocationDialog, WaypointDialog,
                            format_lat_lon)
from .labels import LabelProject, ImageData, haversine_distance
from .layer_panel import CombinedLayerPanel
from .optimize_export import OptimizeExportDialog, OptimizeWorker, plan_output_path
from .h5_export import (H5ExportDialog, H5ExportWorker, HARD_NEGATIVE,
                        SCOPE_LABELLED, SCOPE_VISIBLE,
                        SCOPE_ALL_EXAMPLES, SCOPE_VISIBLE_EXAMPLES,
                        centered_window)
from .debug_log import debug, debug_log, DebugConsole
from .shortcuts import ShortcutsDialog
from .relocate import RelocateImagesDialog, silently_resolve
from .resources import icd_path
from .version import app_title


class GroupMemoryWorker(QObject):
    """Worker that reprojects layer pixel data off the UI thread for preloading.

    To avoid racing the renderer, it never mutates the live TiledLayer objects.
    Each layer is loaded into a throwaway TiledLayer on the worker thread and
    the finished RGBA buffer + metadata is emitted via ``layer_ready``; the main
    thread applies it to the real layer (see
    ``MainWindow._on_preload_layer_ready``). This mirrors the throwaway-layer
    pattern already used for background overview-level loads (_LevelLoadRunnable).
    """

    progress = pyqtSignal(int, int)         # (current, total)
    layer_ready = pyqtSignal(str, object)   # (layer_id, result dict)
    finished = pyqtSignal()
    error = pyqtSignal(str, str)            # (layer_id, error_message)

    def __init__(self, layers: list[tuple[str, str, bool]]):
        """Initialize the worker.

        Args:
            layers: List of (layer_id, file_path, geo) tuples to preload. Only
                identifiers and load parameters are passed - never the live
                TiledLayer - so nothing shared with the renderer is touched off
                the UI thread.
        """
        super().__init__()
        self._layers = layers
        self._cancelled = False

    def cancel(self):
        """Request cancellation; processing stops before the next layer."""
        self._cancelled = True

    def process(self):
        """Reproject each layer off-thread and emit its data for the UI thread."""
        total = len(self._layers)
        for i, (layer_id, file_path, geo) in enumerate(self._layers):
            if self._cancelled:
                break
            try:
                # Load into a throwaway layer so the live layer (which the
                # renderer may read at any time) is never mutated here.
                tmp = TiledLayer(file_path, lazy=True, geo=geo)
                tmp.ensure_loaded()
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
                self.layer_ready.emit(layer_id, result)
            except Exception as e:
                self.error.emit(layer_id, str(e))
            self.progress.emit(i + 1, total)
        self.finished.emit()


def get_recovery_dir() -> Path:
    """Get the directory for recovery files (platform-specific)."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", tempfile.gettempdir()))
    else:
        base = Path.home()
    recovery_dir = base / ".geolabel"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    return recovery_dir


# Recovery file paths
RECOVERY_DIR = get_recovery_dir()
RECOVERY_FILE = RECOVERY_DIR / "recovery.geolabel"
CRASH_MARKER_FILE = RECOVERY_DIR / ".running"

# Auto-save interval in milliseconds (60 seconds)
AUTOSAVE_INTERVAL_MS = 60000

# How many images either side of the current one a cycle mode reads ahead.
# What is then kept in memory is capped by the canvas (WARM_MAX_PIXELS), so
# raising this widens the read-ahead without uncapping what it costs.
CYCLE_PREFETCH_RADIUS = 1


def _write_recovery_snapshot(
        snapshot: dict, recovery_path: Path, crash_marker_path: Path):
    """Write a recovery snapshot to disk on a background thread.

    Uses compact JSON separators (no indentation) since the recovery file is
    machine-read, and writes via a temp file + atomic rename so a crash
    during write never leaves the recovery file half-serialized.
    """
    try:
        tmp_path = recovery_path.with_suffix(recovery_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(tmp_path, recovery_path)
        crash_marker_path.write_text(datetime.now().isoformat())
    except Exception as e:
        # Background thread: log only, don't surface to user.
        print(f"Warning: Auto-save write failed: {e}")

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
        """Initialize the window, canvas, layer panel, menus, and project state."""
        super().__init__()
        self.setWindowTitle(app_title())
        self.setMinimumSize(1024, 768)

        # Create the debug logger on the UI thread up front (before any
        # background thread logs) so cross-thread messages queue correctly.
        debug_log()
        self._debug_console: DebugConsole | None = None

        # Label project
        self.project = LabelProject()
        self._project_path: Path | None = None

        # Options (in-memory, default off): when on, measuring one label's
        # length/width propagates to all labels linked to it (same object_id).
        self._wire_meas_to_linked = False

        # Async loading state (initialized here to avoid AttributeError)
        self._async_root_path = None
        self._async_group_cache: dict[Path, any] = {}
        self._async_loaded_count = 0
        self._async_total_files = 0
        self._async_loader = None
        # Queue for pending file loads
        self._async_pending_files: list[tuple[str, dict]] = []
        # "directory" or "project" - controls post-load behavior
        self._async_mode: str = "directory"
        # Track files that couldn't be found
        self._async_missing_files: list[str] = []
        # Skip adding to project (for Open Project)
        self._async_skip_project_add: bool = False

        # Timer for safe UI updates during async loading (avoids reentrancy
        # issues)
        self._async_ui_timer = QTimer()
        self._async_ui_timer.setInterval(100)  # Update UI every 100ms
        self._async_ui_timer.timeout.connect(self._process_pending_async_files)

        # Cycle mode state
        self._cycle_layers: list[str] = []  # Layer IDs to cycle through
        # Which way the last step went: -1 forward (Space), +1 back. Only
        # orders the prefetch, so the likelier image is read first.
        self._cycle_direction = -1
        # Current position in cycle (-1 means not started)
        self._cycle_index: int = -1
        # (layers, index) kept while the user steps out of a cycle mode, so
        # the cycle resumes instead of restarting. See _suspend_cycle.
        self._cycle_parked: tuple[list[str], int] | None = None

        # Last "Go to Coordinates" entry, re-filled next time it opens.
        self._goto_defaults: dict = {}

        # Auto-save timer for crash recovery
        self._autosave_timer = QTimer()
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.timeout.connect(self._autosave_recovery)

        # Background autosave plumbing: a single worker thread at a time
        # writes the recovery file. The snapshot dict is always built on
        # the UI thread (so it sees a consistent project state), then the
        # JSON serialization + atomic file write run on a daemon thread.
        self._autosave_thread: threading.Thread | None = None

        # Group preload worker state (background reproject into memory). The
        # dialog/thread/worker refs are kept so the main-thread slots can reach
        # them and so the QThread/worker aren't garbage-collected mid-run.
        self._group_mem_thread: QThread | None = None
        self._group_mem_worker: GroupMemoryWorker | None = None
        self._group_mem_dialog = None
        self._group_mem_total = 0
        self._group_mem_label = ""

        # Optimized-export worker state (background tiled/pyramid conversion).
        self._optimize_thread: QThread | None = None
        self._optimize_worker: OptimizeWorker | None = None
        self._optimize_dialog = None
        self._optimize_total = 0

        # HDF5-dataset export worker state.
        self._h5_thread: QThread | None = None
        self._h5_worker: H5ExportWorker | None = None
        self._h5_dialog = None
        self._h5_total = 0
        # Last-used export settings, so a re-export doesn't start from scratch.
        # An existing target file's own settings still take precedence.
        self._h5_last_options: dict = {}


        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()

        # Start auto-save and crash detection
        self._start_crash_detection()
        self._check_for_recovery()

    def _setup_ui(self):
        """Set up the main UI layout."""
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)

        # Combined layer panel on the left (includes labeled images panel)
        self.layer_panel = CombinedLayerPanel()
        splitter.addWidget(self.layer_panel)

        # Install event filter on tree widget to intercept Space key in cycle
        # mode
        self.layer_panel.tree.installEventFilter(self)

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

        # Selected group label for cycle mode
        self.group_label = QLabel("")
        self.group_label.setStyleSheet("color: #0066cc; font-weight: bold;")
        self.statusBar.addPermanentWidget(self.group_label)

        # Connect signals
        self.layer_panel.layer_visibility_changed.connect(
            self.canvas.set_layer_visibility)
        self.layer_panel.layers_reordered.connect(
            self.canvas.update_layer_order)
        self.layer_panel.layer_group_changed.connect(
            self._on_layer_group_changed)
        self.layer_panel.hard_negative_unflag_requested.connect(
            self._on_hard_negative_toggled)
        self.layer_panel.zoom_to_layer_requested.connect(
            self.canvas.zoom_to_layer)
        self.layer_panel.zoom_to_label_requested.connect(
            self._on_zoom_to_label)
        self.layer_panel.layer_removed.connect(self.canvas.remove_layer)
        # After the canvas drops it, prune any hard-negative mirror entry.
        self.layer_panel.layer_removed.connect(
            lambda _lid: self._refresh_hard_negative_panel())

        # Connect batch visibility progress signals for group toggle
        self.layer_panel.batch_visibility_started.connect(
            self._on_batch_visibility_started)
        self.layer_panel.batch_visibility_progress.connect(
            self._update_progress)
        self.layer_panel.batch_visibility_finished.connect(self._hide_progress)

        # Connect group memory management signals
        self.layer_panel.group_preload_requested.connect(
            self._on_group_preload_requested)
        self.layer_panel.group_free_requested.connect(
            self._on_group_free_requested)

        self.canvas.coordinates_changed.connect(self._update_coordinates)
        self.canvas.label_placed.connect(self._on_label_placed)
        self.canvas.label_removed.connect(self._on_label_removed)
        self.canvas.labels_linked.connect(self._on_labels_linked)
        self.canvas.label_unlinked.connect(self._on_label_unlinked)
        self.canvas.label_describe_requested.connect(self._describe_label)
        self.canvas.show_linked_requested.connect(self._on_show_linked)
        self.canvas.link_mode_changed.connect(self._on_link_mode_changed)
        self.canvas.label_measured.connect(self._on_label_measured)
        self.canvas.measure_mode_changed.connect(self._on_measure_mode_changed)
        self.canvas.ruler_changed.connect(self._on_ruler_changed)
        self.canvas.hide_layers_outside_view.connect(
            self.layer_panel.uncheck_layers)
        self.canvas.show_layers_in_view.connect(self.layer_panel.check_layers)
        self.canvas.toggle_layer_visibility_requested.connect(
            self.layer_panel.toggle_layer_visibility)
        self.canvas.cycle_next_requested.connect(self._cycle_to_next_layer)
        self.canvas.cycle_prev_requested.connect(self._cycle_to_prev_layer)
        self.canvas.chain_link_changed.connect(self._on_chain_link_changed)

        # Waypoints: raised either from the map (right-click) or the panel.
        self.canvas.waypoint_add_requested.connect(self._add_waypoint_at)
        self.canvas.hard_negative_toggle_requested.connect(
            self._on_hard_negative_toggled)
        self.canvas.waypoint_goto_requested.connect(self._goto_waypoint)
        self.canvas.waypoint_rename_requested.connect(self._rename_waypoint)
        self.canvas.waypoint_remove_requested.connect(self._remove_waypoint)
        self.layer_panel.waypoint_add_requested.connect(
            self._add_waypoint_by_coordinates)
        self.layer_panel.waypoint_goto_requested.connect(self._goto_waypoint)
        self.layer_panel.waypoint_rename_requested.connect(
            self._rename_waypoint)
        self.layer_panel.waypoint_remove_requested.connect(
            self._remove_waypoint)
        self.layer_panel.waypoints_visibility_changed.connect(
            self.canvas.set_waypoints_visible)

    def _on_chain_link_changed(self, active: bool, message: str):
        """Sync the Chain Link toolbar toggle and status bar with the canvas."""
        self.chain_link_action.setChecked(active)
        if message:
            self.statusBar.showMessage(message, 0)
        elif not active:
            self.statusBar.clearMessage()

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

        locate_action = QAction("&Locate Missing Images...", self)
        locate_action.setStatusTip(
            "Find this project's images on this machine when the recorded "
            "paths came from another one")
        locate_action.triggered.connect(self._offer_relocation)
        file_menu.addAction(locate_action)

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

        # View menu
        view_menu = menubar.addMenu("&View")

        # Go to a typed latitude/longitude
        goto_action = QAction("&Go to Coordinates...", self)
        goto_action.setShortcut("Ctrl+G")
        goto_action.triggered.connect(self._go_to_coordinates)
        view_menu.addAction(goto_action)

        # Waypoints: named bookmarks, also addable by right-clicking the map
        add_waypoint_action = QAction("Add &Waypoint...", self)
        add_waypoint_action.setShortcut("Ctrl+Shift+W")
        add_waypoint_action.setStatusTip(
            "Save a named latitude/longitude to return to later")
        add_waypoint_action.triggered.connect(self._add_waypoint_by_coordinates)
        view_menu.addAction(add_waypoint_action)

        # Remove the crosshair that Go to Coordinates leaves behind
        clear_marker_action = QAction("Clear Coordinate &Marker", self)
        clear_marker_action.triggered.connect(self._clear_coordinate_marker)
        view_menu.addAction(clear_marker_action)

        # Export menu
        export_menu = menubar.addMenu("&Export")

        # Export Ground Truth
        export_gt_action = QAction("&Ground Truth...", self)
        export_gt_action.triggered.connect(self._export_ground_truth)
        export_menu.addAction(export_gt_action)

        # Export Ground Truth (Labeled Only)
        export_gt_labeled_action = QAction(
            "Ground Truth (Labeled Only)...", self)
        export_gt_labeled_action.triggered.connect(
            self._export_ground_truth_labeled_only)
        export_menu.addAction(export_gt_labeled_action)

        # Export Sub-images
        export_subimages_action = QAction("&Sub-images...", self)
        export_subimages_action.triggered.connect(self._export_subimages)
        export_menu.addAction(export_subimages_action)

        export_menu.addSeparator()

        # Export Optimized GeoTIFFs (tiled + pyramided copies)
        export_optimized_action = QAction("&Optimized GeoTIFFs...", self)
        export_optimized_action.triggered.connect(self._export_optimized)
        export_menu.addAction(export_optimized_action)

        # Export HDF5 Dataset (CNN training snippets)
        export_h5_action = QAction("&HDF5 Dataset...", self)
        export_h5_action.triggered.connect(self._export_h5)
        export_menu.addAction(export_h5_action)

        # Options menu
        options_menu = menubar.addMenu("&Options")

        # Wire measurements to linked objects: propagate a label's measured
        # length/width to all labels sharing its object_id.
        self._wire_meas_action = QAction(
            "Wire meas. to linked objects", self)
        self._wire_meas_action.setCheckable(True)
        self._wire_meas_action.setChecked(self._wire_meas_to_linked)
        self._wire_meas_action.toggled.connect(self._on_wire_meas_toggled)
        options_menu.addAction(self._wire_meas_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        # Keyboard Shortcuts
        shortcuts_action = QAction("&Keyboard Shortcuts...", self)
        shortcuts_action.setShortcut("F1")
        shortcuts_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcuts_action)

        # Interface Control Document (ships next to the executable)
        icd_action = QAction("&ICD", self)
        icd_action.setStatusTip(
            "Open the Interface Control Document (PDF)")
        icd_action.triggered.connect(self._show_icd)
        help_menu.addAction(icd_action)

        # About
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        help_menu.addSeparator()

        # Debug Console (live timestamped debug messages)
        debug_action = QAction("&Debug Console", self)
        debug_action.setShortcut("F12")
        debug_action.triggered.connect(self._show_debug_console)
        help_menu.addAction(debug_action)

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
        self.pan_action.triggered.connect(
            lambda: self._set_mode(CanvasMode.PAN))
        toolbar.addAction(self.pan_action)

        self.label_action = QAction("Label", self)
        self.label_action.setCheckable(True)
        self.label_action.setShortcut("L")
        self.label_action.triggered.connect(
            lambda: self._set_mode(CanvasMode.LABEL))
        toolbar.addAction(self.label_action)

        self.cycle_action = QAction("Cycle", self)
        self.cycle_action.setCheckable(True)
        self.cycle_action.setShortcut("C")
        self.cycle_action.triggered.connect(
            lambda: self._set_mode(CanvasMode.CYCLE))
        toolbar.addAction(self.cycle_action)

        self.view_cycle_action = QAction("View Cycle", self)
        self.view_cycle_action.setCheckable(True)
        self.view_cycle_action.setShortcut("V")
        self.view_cycle_action.triggered.connect(
            lambda: self._set_mode(CanvasMode.VIEW_CYCLE))
        toolbar.addAction(self.view_cycle_action)

        self.waterfall_action = QAction("Waterfall", self)
        self.waterfall_action.setCheckable(True)
        self.waterfall_action.setShortcut("W")
        self.waterfall_action.setToolTip(
            "Waterfall mode: stack a bottom-level group's images vertically.\n"
            "Hold Space to glide up, Ctrl+Space to glide down. Starts at the\n"
            "bottom of the stack; all labels stay visible.")
        self.waterfall_action.triggered.connect(
            lambda: self._set_mode(CanvasMode.WATERFALL))
        toolbar.addAction(self.waterfall_action)

        self.chain_link_action = QAction("Chain Link", self)
        self.chain_link_action.setCheckable(True)
        self.chain_link_action.setShortcut("K")
        self.chain_link_action.setToolTip(
            "Chain link (K): click labels to link them all into one object.\n"
            "N starts a new chain, Esc finishes. Works in any labeling mode.")
        # The canvas is created after the toolbar; defer the attribute lookup.
        self.chain_link_action.triggered.connect(
            lambda checked: self.canvas.set_chain_link_mode(checked))
        toolbar.addAction(self.chain_link_action)

        self.ruler_action = QAction("Ruler", self)
        self.ruler_action.setCheckable(True)
        self.ruler_action.setShortcut("R")
        self.ruler_action.setToolTip(
            "Ruler mode: left-drag measures, right-drag pans (R).\n"
            "Shift+drag measures from any mode without leaving it.")
        self.ruler_action.triggered.connect(
            lambda: self._set_mode(CanvasMode.RULER))
        toolbar.addAction(self.ruler_action)

        toolbar.addSeparator()

        # Class selector
        toolbar.addWidget(QLabel(" Class: "))
        self.class_combo = QComboBox()
        self.class_combo.setMinimumWidth(150)
        self.class_combo.currentTextChanged.connect(self._on_class_changed)
        toolbar.addWidget(self.class_combo)

    def keyPressEvent(self, event: QKeyEvent):
        """Handle global key press events.

        Captures Space (next) / Ctrl+Space (previous) in cycle mode regardless of
        which widget has focus. Keys 1-9 switch to the corresponding class.
        """
        if event.key() == Qt.Key_Space and self.canvas._mode in STEP_CYCLE_MODES:
            # Handle space in stepping cycle modes globally. Ctrl+Space steps
            # backwards, matching the canvas handler and the documented
            # shortcut. (Waterfall handles Space itself via hold-to-glide.)
            if event.modifiers() & Qt.ControlModifier:
                self._cycle_to_prev_layer()
            else:
                self._cycle_to_next_layer()
            event.accept()
            return

        # Handle 1-9 keys for quick class switching
        if Qt.Key_1 <= event.key() <= Qt.Key_9:
            class_index = event.key() - Qt.Key_1  # 0-8
            if class_index < len(self.project.classes):
                self.class_combo.setCurrentIndex(class_index)
                event.accept()
                return

        super().keyPressEvent(event)

    def eventFilter(self, obj, event: QEvent) -> bool:
        """Filter events from child widgets.

        Intercepts the Space key on the layer tree when in a cycle/waterfall
        mode so the tree never toggles checkboxes: in stepping cycle modes it
        advances the cycle; in waterfall mode it drives the hold-to-glide
        (press starts, release stops), matching the canvas handler.
        """
        etype = event.type()
        if (etype in (QEvent.KeyPress, QEvent.KeyRelease)
                and event.key() == Qt.Key_Space):
            mode = self.canvas._mode
            if mode == CanvasMode.WATERFALL:
                if etype == QEvent.KeyPress and not event.isAutoRepeat():
                    direction = (1 if event.modifiers() & Qt.ControlModifier
                                 else -1)
                    self.canvas.start_waterfall_glide(direction)
                elif etype == QEvent.KeyRelease and not event.isAutoRepeat():
                    self.canvas.stop_waterfall_glide()
                return True  # Event consumed either way
            if etype == QEvent.KeyPress and mode in STEP_CYCLE_MODES:
                # Ctrl+Space steps backwards, consistent with the canvas handler.
                if event.modifiers() & Qt.ControlModifier:
                    self._cycle_to_prev_layer()
                else:
                    self._cycle_to_next_layer()
                return True  # Event consumed
        return super().eventFilter(obj, event)

    def _set_mode(self, mode: CanvasMode):
        """Set the canvas interaction mode."""
        was_waterfall = self.canvas._waterfall_active
        self.canvas.set_mode(mode)
        self.pan_action.setChecked(mode == CanvasMode.PAN)
        self.label_action.setChecked(mode == CanvasMode.LABEL)
        self.cycle_action.setChecked(mode == CanvasMode.CYCLE)
        self.view_cycle_action.setChecked(mode == CanvasMode.VIEW_CYCLE)
        self.ruler_action.setChecked(mode == CanvasMode.RULER)
        self.waterfall_action.setChecked(mode == CanvasMode.WATERFALL)

        # Leaving waterfall: restore the normal layout and re-place labels.
        if was_waterfall and mode != CanvasMode.WATERFALL:
            self.canvas.clear_waterfall()
            self._refresh_label_markers()

        # Handle mode entry
        if mode == CanvasMode.CYCLE:
            self._start_cycle_mode()
        elif mode == CanvasMode.VIEW_CYCLE:
            self._start_view_cycle_mode()
        elif mode == CanvasMode.WATERFALL:
            self._start_waterfall_mode()
        else:
            self._suspend_cycle()

    def _suspend_cycle(self):
        """Park the cycle on leaving it, so a detour can be resumed.

        Reaching for the ruler or the pan tool part-way through a group used to
        throw the queue away, and coming back restarted it from the top - on a
        few hundred images that is a lot of progress to lose over one
        measurement.
        """
        # Warmed neighbours are only worth their memory inside the cycle.
        self.canvas.clear_warmed_layers()
        if self._cycle_layers and self._cycle_index >= 0:
            self._cycle_parked = (list(self._cycle_layers), self._cycle_index)
        self._cycle_layers = []
        self._cycle_index = -1
        self.group_label.setText("")

    def _parked_cycle_index(self, layers: list[str]) -> int | None:
        """Where to resume in ``layers``, or None to start the cycle fresh.

        Only resumes into the identical queue: change the group, or load or
        remove a layer, and the parked position no longer refers to the same
        run, so the cycle starts over.
        """
        if not self._cycle_parked:
            return None
        parked_layers, parked_index = self._cycle_parked
        if parked_layers != layers or not 0 <= parked_index < len(layers):
            return None
        return parked_index

    def _cycle_zoom_to(self, layer_id: str):
        """Zoom to a cycled layer."""
        # A measurement belongs to the image it was taken on; leaving that
        # image behind should not leave the line hanging over the next one.
        self.canvas.clear_ruler()
        self.canvas.zoom_to_layer(layer_id)
        self._prefetch_cycle_neighbours()

    def _prefetch_cycle_neighbours(self):
        """Read the images either side of this one before they are asked for.

        Stepping through a group used to stall on every press while the next
        image came off disk - long enough to be in the way on a slow machine.
        The neighbours are read in the background instead, so a step is usually
        just tiles being built from memory. Holding the one behind as well
        makes Ctrl+Space free, which is the common "wait, go back" case.
        """
        if not self._cycle_layers or self._cycle_index < 0:
            return
        # Nearest first, and the direction of travel ahead of the other, so the
        # likelier image is the one already reading when the next key lands.
        offsets = sorted(
            (offset for radius in range(1, CYCLE_PREFETCH_RADIUS + 1)
             for offset in (radius, -radius)),
            key=lambda offset: (abs(offset),
                                offset * self._cycle_direction < 0))
        neighbours = []
        for offset in offsets:
            index = self._cycle_index + offset
            if 0 <= index < len(self._cycle_layers):
                neighbours.append(self._cycle_layers[index])
        self.canvas.warm_layers(neighbours)

    def _layer_name(self, layer_id: str) -> str:
        """Return a layer's display name for logging (falls back to its id)."""
        layer = self.canvas.get_layer(layer_id)
        return layer.name if layer is not None else layer_id

    def _start_cycle_mode(self):
        """Initialize cycle mode with layers from selected group."""
        # Get and display the selected group name
        group_name = self.layer_panel.get_selected_group_name()
        if group_name:
            self.group_label.setText(f"Group: {group_name}")
        else:
            self.group_label.setText("")

        self._cycle_layers = self.layer_panel.get_all_layers_in_selected_group()
        if not self._cycle_layers:
            self.statusBar.showMessage("No layers in selected group", 3000)
            self._cycle_index = -1
            return

        # Pick up where a detour left off, else start at the last layer.
        resumed = self._parked_cycle_index(self._cycle_layers)
        self._cycle_index = (resumed if resumed is not None
                             else len(self._cycle_layers) - 1)
        layer_id = self._cycle_layers[self._cycle_index]
        self.layer_panel.check_layers([layer_id])
        self._cycle_zoom_to(layer_id)
        count = len(self._cycle_layers)
        debug(f"cycle {'resume' if resumed is not None else 'start'}: group "
              f"'{group_name}' - {count} images; "
              f"at {self._layer_name(layer_id)} [{self._cycle_index + 1}/{count}]")
        self.statusBar.showMessage(
            f"Cycle {'resumed' if resumed is not None else 'mode'}: Layer "
            f"{self._cycle_index + 1}/{count} - Space=next, Ctrl+Space=prev",
            0  # No timeout
        )

        # Give canvas keyboard focus so Space key works immediately
        self.canvas.setFocus()

    def _start_view_cycle_mode(self):
        """Initialize view cycle mode with layers visible in the current canvas view."""
        self._cycle_layers = self.canvas.get_layers_in_view()
        if not self._cycle_layers:
            self.statusBar.showMessage("No layers in current view", 3000)
            self._cycle_index = -1
            self.group_label.setText("View Cycle")
            return

        self.group_label.setText("View Cycle")

        # Pick up where a detour left off, else start at the last layer.
        resumed = self._parked_cycle_index(self._cycle_layers)
        self._cycle_index = (resumed if resumed is not None
                             else len(self._cycle_layers) - 1)
        layer_id = self._cycle_layers[self._cycle_index]
        self.layer_panel.check_layers([layer_id])
        self._cycle_zoom_to(layer_id)
        count = len(self._cycle_layers)
        debug(f"view cycle {'resume' if resumed is not None else 'start'}: "
              f"{count} images in view; "
              f"at {self._layer_name(layer_id)} [{self._cycle_index + 1}/{count}]")
        self.statusBar.showMessage(
            f"View Cycle{' resumed' if resumed is not None else ''}: Layer "
            f"{self._cycle_index + 1}/{count} - Space=next, Ctrl+Space=prev",
            0  # No timeout
        )

        # Give canvas keyboard focus so Space key works immediately
        self.canvas.setFocus()

    def _start_waterfall_mode(self):
        """Stack the selected bottom-level group's images vertically.

        All the group's images become visible and are re-laid-out top-to-bottom
        as raw pixels; labels are re-rendered so each lands on its own image
        (every label in the group stays visible at once) and geo labels are
        projected onto the other images that contain them. The view starts at
        the BOTTOM of the stack; holding Space glides up, Ctrl+Space back down.
        """
        # Waterfall only works on bottom-level groups: a group of groups has no
        # single well-defined image sequence to stack.
        if self.layer_panel.selected_group_is_bottom_level() is False:
            self.statusBar.showMessage(
                "Waterfall needs a bottom-level group (no sub-groups) - "
                "select one and try again", 5000)
            self._set_mode(CanvasMode.PAN)
            return

        group_name = self.layer_panel.get_selected_group_name()
        self.group_label.setText(f"Group: {group_name}" if group_name else "")

        layer_ids = self.layer_panel.get_all_layers_in_selected_group()
        if not layer_ids:
            self.statusBar.showMessage("No layers in selected group", 3000)
            self._set_mode(CanvasMode.PAN)
            return

        # Show the whole group so the entire stack renders while gliding.
        self.layer_panel.check_layers(layer_ids)
        self.canvas.layout_waterfall(layer_ids)
        # Re-place every label onto its (now stacked) image, then project geo
        # labels onto the other images that also contain them.
        self._refresh_label_markers()
        self._update_waterfall_projections()
        # Start at the BOTTOM of the stack (the same image the cycle modes
        # start on) and give the canvas focus so Space glides immediately.
        self.canvas.zoom_to_layer(layer_ids[-1])
        self.canvas.setFocus()

        debug(f"waterfall: group '{group_name}' - "
              f"{len(layer_ids)} images stacked")
        self.statusBar.showMessage(
            f"Waterfall: {len(layer_ids)} images - hold Space to glide up, "
            "Ctrl+Space to glide down", 0)

    def _cycle_to_next_layer(self):
        """Toggle off current layer, turn on and zoom to the next layer in the cycle."""
        if not self._cycle_layers or self._cycle_index < 0:
            self.statusBar.showMessage("No layers to cycle through", 3000)
            return

        self._cycle_direction = -1

        # Toggle off current layer
        current_layer_id = self._cycle_layers[self._cycle_index]
        self.layer_panel.uncheck_layers([current_layer_id])

        # Move to previous index (going backwards through the list)
        self._cycle_index -= 1

        if self._cycle_index < 0:
            # Reached the beginning, cycle complete
            debug("cycle complete: all images processed")
            self.statusBar.showMessage(
                "Cycle complete - all layers processed", 3000)
            self._cycle_layers = []
            self._cycle_index = -1
            # Nothing left to come back to, so the next C starts a fresh run.
            self._cycle_parked = None
            self.group_label.setText("")
            return

        # Turn on and zoom to next layer
        next_layer_id = self._cycle_layers[self._cycle_index]
        self.layer_panel.check_layers([next_layer_id])
        self._cycle_zoom_to(next_layer_id)
        count = len(self._cycle_layers)
        debug(f"cycle next: {self._layer_name(next_layer_id)} "
              f"[{self._cycle_index + 1}/{count}, {self._cycle_index} remaining]")
        self.statusBar.showMessage(
            f"Cycle mode: Layer {
                self._cycle_index + 1}/{
                len(
                    self._cycle_layers)} - Space=next, Ctrl+Space=prev",
            0
        )

        # Refocus canvas so Space key continues to work
        self.canvas.setFocus()

    def _cycle_to_prev_layer(self):
        """Go back to the previous layer in the cycle (undo the last forward step)."""
        if not self._cycle_layers or self._cycle_index < 0:
            self.statusBar.showMessage("No layers to cycle through", 3000)
            return

        self._cycle_direction = 1

        # Check if we're already at the last layer (can't go back further)
        if self._cycle_index >= len(self._cycle_layers) - 1:
            self.statusBar.showMessage("Already at the first layer in cycle", 3000)
            return

        # Toggle off current layer
        current_layer_id = self._cycle_layers[self._cycle_index]
        self.layer_panel.uncheck_layers([current_layer_id])

        # Move to next index (going forwards through the list = backwards in cycle)
        self._cycle_index += 1

        # Turn on and zoom to previous layer
        prev_layer_id = self._cycle_layers[self._cycle_index]
        self.layer_panel.check_layers([prev_layer_id])
        self._cycle_zoom_to(prev_layer_id)
        count = len(self._cycle_layers)
        debug(f"cycle prev: {self._layer_name(prev_layer_id)} "
              f"[{self._cycle_index + 1}/{count}, {self._cycle_index} remaining]")
        self.statusBar.showMessage(
            f"Cycle mode: Layer {
                self._cycle_index + 1}/{
                len(
                    self._cycle_layers)} - Space=next, Ctrl+Space=prev",
            0
        )

        # Refocus canvas so keys continue to work
        self.canvas.setFocus()

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

    def _on_label_placed(
            self,
            pixel_x: float,
            pixel_y: float,
            lon: float,
            lat: float,
            image_name: str,
            image_group: str,
            image_path: str):
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
        debug(f"label added: #{label.id} '{class_name}' on {image_name} "
              f"at pixel ({pixel_x:.1f}, {pixel_y:.1f}) "
              f"[{self.project.label_count} total]")

        # Add visual marker
        color = self._get_class_color(class_name)
        self.canvas.add_label_marker(
            label.id,
            lon,
            lat,
            image_name,
            image_group,
            image_path,
            class_name,
            color,
            pixel_x=pixel_x,
            pixel_y=pixel_y)

        # Add to labeled images panel incrementally (O(1) instead of full refresh)
        image = self.project.images.get(image_path)
        if image:
            self.layer_panel.add_label_to_panel(label, image)

        # In waterfall, show this label on the other images that contain it.
        self._update_waterfall_projections()

        # Show appropriate message for geo vs pixel layers
        layer = self.canvas.get_layer(self.canvas._path_to_layer.get(image_path, ""))
        if layer and not layer.geo:
            self.statusBar.showMessage(
                f"Added label: {class_name} at pixel ({pixel_x:.1f}, {pixel_y:.1f}) on {image_name}",
                3000)
        else:
            self.statusBar.showMessage(
                f"Added label: {class_name} at ({
                    lon:.6f}, {
                    lat:.6f}) on {image_name}",
                3000
            )

    def _on_label_removed(self, label_id: int, image_path: str):
        """Handle a label being removed."""
        debug(f"label removed: #{label_id} "
              f"[{self.project.label_count - 1} remaining]")
        # Remove from project
        self.project.remove_label(label_id)

        # Remove visual marker
        self.canvas.remove_label_marker(label_id)

        # Remove from labeled images panel incrementally (O(1) instead of full refresh)
        self.layer_panel.remove_label_from_panel(label_id)

        # Projections may reference the removed label.
        self._update_waterfall_projections()

        self.statusBar.showMessage("Removed label", 3000)

    def _update_waterfall_projections(self):
        """Refresh the projected label markers in waterfall mode.

        Gathers every label whose source image carries georeferencing (only
        those have a real lat/lon) and asks the canvas to display each one on
        the other stacked images that geographically contain it. No-op outside
        waterfall mode.
        """
        if not self.canvas._waterfall_active:
            return
        infos = []
        for image, label in self.project.get_all_labels():
            layer = self.canvas.get_layer(
                self.canvas._path_to_layer.get(image.path, ""))
            if layer is None or layer._src_crs is None:
                continue  # plain image - a pixel has no meaningful lat/lon
            infos.append((label.id, label.lon, label.lat, label.class_name,
                          self._get_class_color(label.class_name), image.path))
        self.canvas.set_waterfall_projections(infos)

    def _sync_measurements_in_group(self, label_id1: int, label_id2: int) -> int:
        """Make a just-linked object group share one measurement.

        Only runs when the "Wire meas. to linked objects" option is on. Picks a
        donor measurement (preferring the link source, then the clicked label,
        then any measured label in the group) and applies its length/width to
        every other label in the group. This is what makes linking a measured
        object to an unmeasured one propagate the values immediately.

        Returns the number of labels updated (0 if wiring is off, nobody in the
        group has a measurement, or the group is already consistent).
        """
        if not self._wire_meas_to_linked:
            return 0

        linked = self.project.get_linked_labels(label_id1)
        if not linked:
            return 0
        group = [lbl for _, lbl in linked]

        def has_meas(lbl):
            """Return True if the label has a length or width measurement."""
            return lbl.length_m is not None or lbl.width_m is not None

        # Choose the donor: link source first, then the clicked label, then any
        # measured label in the group.
        _, l1 = self.project.get_label_by_id(label_id1)
        _, l2 = self.project.get_label_by_id(label_id2)
        donor = None
        if l1 and has_meas(l1):
            donor = l1
        elif l2 and has_meas(l2):
            donor = l2
        else:
            donor = next((lbl for lbl in group if has_meas(lbl)), None)
        if donor is None:
            return 0  # no measurements anywhere in the group

        length_m, width_m = donor.length_m, donor.width_m
        has_measurement = length_m is not None or width_m is not None
        updated = 0
        for lbl in group:
            if lbl.length_m == length_m and lbl.width_m == width_m:
                continue  # already consistent
            lbl.length_m = length_m
            lbl.width_m = width_m
            self.canvas.set_label_measured(
                lbl.id, has_measurement, length_m, width_m)
            updated += 1
        return updated

    def _on_labels_linked(self, label_id1: int, label_id2: int):
        """Handle two labels being linked."""
        object_id = self.project.link_labels(label_id1, label_id2)

        if object_id:
            # Update the linked status for all labels with this object_id
            linked_labels = self.project.get_linked_labels(label_id1)
            for _, label in linked_labels:
                self.canvas.set_label_linked(label.id, True)

            # If wiring is on, propagate any existing measurement across the
            # newly-merged group before refreshing the panel.
            synced = self._sync_measurements_in_group(label_id1, label_id2)

            # Refresh labeled images panel (grouping may have changed)
            self.layer_panel.refresh_labeled_panel(self.project)

            count = len(linked_labels)
            msg = f"Linked labels (object has {count} labels)"
            if synced:
                msg += f" · shared measurements to {synced}"
            self.statusBar.showMessage(msg, 3000)
        else:
            self.statusBar.showMessage("Failed to link labels", 3000)

    def _describe_label(self, label_id: int):
        """Prompt for the free-text description of one label.

        Multi-line, because a description is a note rather than a name. The
        text is stored on this label only: linked labels are the same object
        seen in different images, and what is worth describing is usually what
        differs between those views. This is why it is not applied across the
        object group the way "Wire meas. to linked objects" applies
        measurements - see _on_label_measured.
        """
        _, label = self.project.get_label_by_id(label_id)
        if label is None:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self, "Label Description",
            f"Describe this {label.class_name}:", label.description)
        if not accepted:
            return
        label.description = text.strip()
        self.canvas.set_label_description(label_id, label.description)
        self.layer_panel.refresh_labeled_panel(self.project)
        self.statusBar.showMessage(
            "Description saved" if label.description else "Description cleared",
            3000)

    def _on_label_unlinked(self, label_id: int):
        """Handle a label being unlinked from its object group."""
        # First get the labels that were linked before unlinking
        old_linked = self.project.get_linked_labels(label_id)

        self.project.unlink_label(label_id)

        # Update the unlinked label
        self.canvas.set_label_linked(label_id, False)

        # Clear highlight from the unlinked label
        self.canvas.highlight_labels([label_id], highlight=False)

        # Update remaining linked labels (if only 1 left, it's no longer
        # "linked")
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
            all_label_ids = [label.id for _,
                             label in self.project.get_all_labels()]
            self.canvas.highlight_labels(all_label_ids, highlight=False)

            # Highlight linked labels
            linked_ids = [label.id for _, label in linked_labels]
            self.canvas.highlight_labels(linked_ids, highlight=True)

            self.statusBar.showMessage(
                f"Showing {
                    len(linked_labels)} linked labels (click anywhere to clear)",
                3000)

    def _on_link_mode_changed(self, is_active: bool, message: str):
        """Handle link mode state changes."""
        if is_active:
            self.statusBar.showMessage(message, 0)  # 0 = no timeout
        else:
            self.statusBar.clearMessage()

    def _on_wire_meas_toggled(self, checked: bool):
        """Toggle propagation of measurements to linked objects."""
        self._wire_meas_to_linked = checked
        state = "on" if checked else "off"
        self.statusBar.showMessage(
            f"Wire measurements to linked objects: {state}", 3000)

    def _on_label_measured(self, label_id: int, length_m, width_m):
        """Store measured length/width (metres) on a label.

        Values are picked up automatically by the periodic recovery autosave
        and by normal project save/export (PointLabel.to_dict serialises
        length_m / width_m). ``None`` values clear the respective dimension.

        When the "Wire meas. to linked objects" option is on and this label is
        linked (shares an object_id with others), the same values are applied
        to every label in that object group.
        """
        _, label = self.project.get_label_by_id(label_id)
        if label is None:
            return

        # Decide which labels receive the values: just this one, or the whole
        # linked object group. get_linked_labels returns [] for an unlinked
        # label and otherwise includes the source label itself.
        targets = [label]
        if self._wire_meas_to_linked:
            linked = self.project.get_linked_labels(label_id)
            if linked:
                targets = [lbl for _, lbl in linked]

        has_measurement = length_m is not None or width_m is not None
        for lbl in targets:
            lbl.length_m = length_m
            lbl.width_m = width_m
            # Marker may not exist if its image isn't loaded; set_label_measured
            # is a no-op then, and the load path re-adorns it later.
            self.canvas.set_label_measured(
                lbl.id, has_measurement, length_m, width_m)
        self.layer_panel.refresh_labeled_panel(self.project)

        n = len(targets)
        linked_note = f" ({n} linked labels)" if n > 1 else ""
        if length_m is None and width_m is None:
            self.statusBar.showMessage(f"Cleared measurements{linked_note}", 3000)
        else:
            self.statusBar.showMessage(
                f"Measured: length {length_m:.2f} m, width {width_m:.2f} m"
                f"{linked_note}", 4000)

    def _on_measure_mode_changed(self, is_active: bool, message: str):
        """Handle measure mode state changes (live status-bar prompt)."""
        if is_active:
            self.statusBar.showMessage(message, 0)  # 0 = no timeout
        elif message:
            self.statusBar.showMessage(message, 3000)
        else:
            self.statusBar.clearMessage()

    def _on_ruler_changed(self, is_active: bool, message: str):
        """Show the live ruler distance in the status bar."""
        if is_active:
            self.statusBar.showMessage(message, 0)  # 0 = no timeout
        else:
            self.statusBar.clearMessage()

    def _on_zoom_to_label(self, lon: float, lat: float):
        """Zoom to a label by its coordinates."""
        self.canvas.zoom_to_point(lon, lat, size_meters=10.0)

    def _go_to_coordinates(self):
        """Move the view to a latitude/longitude the user types in."""
        dialog = GoToLocationDialog(self, defaults=self._goto_defaults)
        if not dialog.exec_():
            return
        coords = dialog.coordinates()
        if coords is None:
            return
        lat, lon = coords
        width_m = dialog.view_width_m()
        self._goto_defaults = {"text": dialog.entered_text(), "width": width_m}

        # zoom_to_point sizes the view in Web Mercator units, which are
        # stretched by 1/cos(latitude); undo that so the width the user asked
        # for is the width they get on the ground.
        mercator_width = width_m / max(math.cos(math.radians(lat)), 1e-6)
        self.canvas.zoom_to_point(lon, lat, size_meters=mercator_width)
        self.canvas.mark_location(lon, lat)
        # Focus the map so Escape reaches it without needing a click first.
        self.canvas.setFocus()
        self.statusBar.showMessage(
            f"Moved to {format_lat_lon(lat, lon)} - press Escape to clear the "
            "marker", 8000)

    # ------------------------------------------------------------------
    # Waypoints: named geographic bookmarks kept with the project
    # ------------------------------------------------------------------

    def _refresh_waypoints(self):
        """Redraw every waypoint marker and rebuild the panel list."""
        self.canvas.clear_waypoint_markers()
        for wp in self.project.waypoints:
            self.canvas.add_waypoint_marker(wp.id, wp.name, wp.lon, wp.lat)
        self.canvas.set_waypoints_visible(self.layer_panel.waypoints_shown())
        self.layer_panel.refresh_waypoints(
            self.project.waypoints, format_lat_lon)

    def _add_waypoint_at(self, lon: float, lat: float, name: str = ""):
        """Add a waypoint at a WGS84 position (from the map right-click)."""
        wp = self.project.add_waypoint(lat, lon, name=name)
        self.canvas.add_waypoint_marker(wp.id, wp.name, wp.lon, wp.lat)
        self.layer_panel.refresh_waypoints(
            self.project.waypoints, format_lat_lon)
        debug(f"waypoint added: #{wp.id} '{wp.name}' at "
              f"({wp.lat:.6f}, {wp.lon:.6f})")
        self.statusBar.showMessage(
            f"Added waypoint '{wp.name}' at {format_lat_lon(wp.lat, wp.lon)}",
            5000)

    def _add_waypoint_by_coordinates(self):
        """Add a waypoint from a typed latitude/longitude."""
        dialog = WaypointDialog(self)
        if not dialog.exec_():
            return
        coords = dialog.coordinates()
        if coords is None:
            return
        lat, lon = coords
        self._add_waypoint_at(lon, lat, name=dialog.waypoint_name())

    def _goto_waypoint(self, waypoint_id: int):
        """Fly the view to a waypoint."""
        wp = self.project.get_waypoint(waypoint_id)
        if wp is None:
            return
        # zoom_to_point sizes the view in Web Mercator units, which are
        # stretched by 1/cos(latitude); undo that so the view covers the
        # ground width intended.
        width_m = float(self._goto_defaults.get("width", 200))
        mercator_width = width_m / max(math.cos(math.radians(wp.lat)), 1e-6)
        self.canvas.zoom_to_point(wp.lon, wp.lat, size_meters=mercator_width)
        self.canvas.setFocus()
        self.statusBar.showMessage(
            f"Moved to waypoint '{wp.name}' - "
            f"{format_lat_lon(wp.lat, wp.lon)}", 5000)

    def _rename_waypoint(self, waypoint_id: int):
        """Prompt for a new name for a waypoint."""
        wp = self.project.get_waypoint(waypoint_id)
        if wp is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename Waypoint", "Name:", text=wp.name)
        if not accepted or not name.strip():
            return
        self.project.rename_waypoint(waypoint_id, name)
        self.canvas.add_waypoint_marker(wp.id, wp.name, wp.lon, wp.lat)
        self.layer_panel.refresh_waypoints(
            self.project.waypoints, format_lat_lon)
        self.statusBar.showMessage(f"Renamed waypoint to '{wp.name}'", 3000)

    def _remove_waypoint(self, waypoint_id: int):
        """Remove a waypoint from the project and the map."""
        wp = self.project.get_waypoint(waypoint_id)
        if wp is None:
            return
        name = wp.name
        self.project.remove_waypoint(waypoint_id)
        self.canvas.remove_waypoint_marker(waypoint_id)
        self.layer_panel.refresh_waypoints(
            self.project.waypoints, format_lat_lon)
        debug(f"waypoint removed: #{waypoint_id} '{name}'")
        self.statusBar.showMessage(f"Removed waypoint '{name}'", 3000)

    def _clear_coordinate_marker(self):
        """Remove the go-to crosshair from the map."""
        self.canvas.clear_location_marker()
        self.canvas.setFocus()
        self.statusBar.showMessage("Cleared the coordinate marker", 3000)

    def _refresh_label_markers(self):
        """Refresh all label markers on the canvas."""
        self.canvas.clear_label_markers()
        for image, label in self.project.get_all_labels():
            color = self._get_class_color(label.class_name)
            self.canvas.add_label_marker(
                label.id, label.lon, label.lat,
                image.name, image.group, image.path,
                label.class_name, color,
                pixel_x=label.pixel_x, pixel_y=label.pixel_y
            )
            # Check if label is linked to others
            linked_labels = self.project.get_linked_labels(label.id)
            self.canvas.set_label_linked(label.id, len(linked_labels) > 1)

            # Restore measurement adornment for labels loaded with dimensions
            if label.length_m is not None or label.width_m is not None:
                self.canvas.set_label_measured(
                    label.id, True, label.length_m, label.width_m)

            # ...and the description tooltip, which is otherwise lost every
            # time the markers are rebuilt (project load, mode change).
            if label.description:
                self.canvas.set_label_description(label.id, label.description)

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
                count = sum(
                    1 for l in self.project.labels if l.class_name in removed)
                if count > 0:
                    reply = QMessageBox.question(
                        self,
                        "Remove Classes",
                        f"Removing classes will delete {count} labels. Continue?",
                        QMessageBox.Yes | QMessageBox.No)
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

        # Cancel any pending async operations
        self._async_ui_timer.stop()
        if self._async_loader is not None:
            self._async_loader.cancel()
            self._async_loader = None
        self._async_pending_files.clear()
        self._async_missing_files.clear()

        self._hide_progress()

        # Clear project state
        self.project = LabelProject()
        self._project_path = None

        # Clear cycle mode state
        self._cycle_layers.clear()
        self._cycle_index = -1
        self._cycle_parked = None

        # Clear canvas and UI
        self.canvas.clear_label_markers()
        self.canvas.clear_layers()
        self.layer_panel.clear()
        self._refresh_waypoints()  # the new project has none
        self._refresh_hard_negative_panel()
        self._update_class_combo()
        self.setWindowTitle(app_title())
        self.statusBar.showMessage("New project created", 3000)

    def _open_project(self):
        """Open a project file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            "",
            "GeoLabeller Project (*.geolabel);;All Files (*)"
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
                    self._refresh_waypoints()
                    self._refresh_hard_negative_panel()
                    self.setWindowTitle(
                        f"{app_title()} - {self._project_path.name}")
                    self.statusBar.showMessage(
                        f"Opened project with {
                            self.project.label_count} labels", 3000)
            except Exception as e:
                traceback.print_exc()
                QMessageBox.critical(
                    self, "Error", f"Failed to open project: {e}")

    def _start_project_image_loading(self):
        """Start async loading of project images."""

        # A project shared from another machine often travels WITH its
        # imagery; resolving missing paths against the project file's own
        # folder fixes that case before the user sees a single warning.
        if self._project_path is not None:
            fixed = silently_resolve(
                self.project, str(self._project_path.parent))
            if fixed:
                self.statusBar.showMessage(
                    f"Relocated {fixed} image(s) next to the project file - "
                    "save the project to keep the new paths", 8000)

        geotiff_files = []
        missing_files = []

        for image in self.project.images.values():
            if not os.path.exists(image.path):
                missing_files.append(image.path)
            else:
                geotiff_files.append((image.path, image.group or ""))

        # Store for later use
        self._async_missing_files = missing_files
        self._project_geotiff_files = geotiff_files

        total_files = len(geotiff_files)

        if total_files == 0:
            self._finish_async_loading_project()
            return

        self._start_project_geotiff_loading()

    def _start_project_geotiff_loading(self):
        """Start async loading of GeoTIFF files during project load."""
        geotiff_files = self._project_geotiff_files
        self._project_geotiff_files = []  # Clear

        if not geotiff_files:
            self._finish_async_loading_project()
            return

        # Use the unified async loader for GeoTIFFs
        self._start_unified_async_loading(
            geotiff_files,
            mode="project",
            progress_label="Loading GeoTIFFs",
            skip_project_add=True  # Images already in project
        )

    def _start_crash_detection(self):
        """Start crash detection and auto-save timer.

        Creates a crash marker file that persists while the app is running.
        If the app crashes, this file will still exist on next startup.
        """
        try:
            # Create crash marker with timestamp
            CRASH_MARKER_FILE.write_text(datetime.now().isoformat())
            # Start auto-save timer
            self._autosave_timer.start()
        except Exception as e:
            print(f"Warning: Could not start crash detection: {e}")

    def _check_for_recovery(self):
        """Check for recovery file on startup and offer to restore.

        If a crash marker exists along with a recovery file, it means
        the previous session crashed without saving.
        """
        try:
            has_crash_marker = CRASH_MARKER_FILE.exists()
            has_recovery = RECOVERY_FILE.exists()

            if has_crash_marker and has_recovery:
                # Get recovery file age
                recovery_time = datetime.fromtimestamp(
                    RECOVERY_FILE.stat().st_mtime)
                age_minutes = (datetime.now() - recovery_time).total_seconds() / 60

                reply = QMessageBox.question(
                    self,
                    "Recover Previous Session",
                    f"GeoLabeller appears to have closed unexpectedly.\n\n"
                    f"A recovery file was found from {age_minutes:.0f} minutes ago.\n\n"
                    f"Would you like to restore your previous session?",
                    QMessageBox.Yes | QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    self._restore_from_recovery()
                # If user declines, recovery file is preserved until next save

            # If has_recovery but no crash_marker, keep the recovery file
            # until user explicitly saves

        except Exception as e:
            print(f"Warning: Error checking for recovery: {e}")

    def _restore_from_recovery(self):
        """Restore project state from recovery file."""
        try:
            self.project = LabelProject.load(RECOVERY_FILE)

            # Show progress for loading images
            num_images = len(self.project.images)
            if num_images > 0:
                self._show_progress(num_images, "Restoring session")
                self._start_project_image_loading()
            else:
                self._update_class_combo()
                self._refresh_label_markers()
                self._refresh_waypoints()
                self._refresh_hard_negative_panel()

            self.setWindowTitle(f"{app_title()} - Recovered Session (unsaved)")
            self.statusBar.showMessage(
                f"Restored {self.project.label_count} labels from recovery", 5000)

            # Recovery file is preserved until user explicitly saves

        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(
                self,
                "Recovery Failed",
                f"Could not restore from recovery file:\n{e}\n\n"
                f"The recovery file will be preserved at:\n{RECOVERY_FILE}"
            )

    def _autosave_recovery(self):
        """Auto-save current project state to recovery file.

        Called periodically by the auto-save timer. The snapshot is built on
        the UI thread (cheap dict construction over current project state)
        and the JSON serialization + file write are dispatched to a daemon
        thread so the UI doesn't stall every minute. Skips silently if a
        previous autosave is still in flight.
        """
        try:
            if self.project.label_count == 0 and not self.project.images:
                return

            # Skip if a previous autosave hasn't finished yet (don't queue up
            # writes if a save is genuinely slow).
            prev = self._autosave_thread
            if prev is not None and prev.is_alive():
                return

            # Build the serializable snapshot on the UI thread for consistency
            # with the project state. This is pure-Python and does not perform
            # any I/O.
            snapshot = self.project.to_dict()
            recovery_path = RECOVERY_FILE
            crash_marker_path = CRASH_MARKER_FILE

            self._autosave_thread = threading.Thread(
                target=_write_recovery_snapshot,
                args=(snapshot, recovery_path, crash_marker_path),
                name="GeoLabelAutosave",
                daemon=True,
            )
            self._autosave_thread.start()
        except Exception as e:
            # Don't show error to user for background auto-save
            print(f"Warning: Auto-save failed: {e}")

    def _clear_recovery_file(self):
        """Clear the recovery file (called after manual save or new project)."""
        try:
            if RECOVERY_FILE.exists():
                RECOVERY_FILE.unlink()
        except Exception as e:
            print(f"Warning: Could not clear recovery file: {e}")

    def _clean_exit(self):
        """Clean up crash detection on normal exit."""
        try:
            # Stop auto-save timer
            self._autosave_timer.stop()
            # Wait briefly for any in-flight autosave to finish so the
            # recovery file isn't left half-written.
            t = self._autosave_thread
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
            # Remove crash marker (indicates clean exit)
            if CRASH_MARKER_FILE.exists():
                CRASH_MARKER_FILE.unlink()
            # Recovery file is preserved until user explicitly saves
        except Exception as e:
            print(f"Warning: Could not clean up on exit: {e}")

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
            "GeoLabeller Project (*.geolabel)"
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
            self.setWindowTitle(f"{app_title()} - {path.name}")
            self.statusBar.showMessage(
                f"Saved {
                    self.project.label_count} labels to {
                    path.name}", 3000)
            # Clear recovery file after successful save
            self._clear_recovery_file()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project: {e}")

    def _combine_projects(self):
        """Combine two .geolabel project files into a new project file."""
        # Select first project file
        file1, _ = QFileDialog.getOpenFileName(
            self,
            "Select First Project to Combine",
            "",
            "GeoLabeller Project (*.geolabel);;All Files (*)"
        )
        if not file1:
            return

        # Select second project file
        file2, _ = QFileDialog.getOpenFileName(
            self,
            "Select Second Project to Combine",
            "",
            "GeoLabeller Project (*.geolabel);;All Files (*)"
        )
        if not file2:
            return

        # Select output file
        output_file, _ = QFileDialog.getSaveFileName(
            self,
            "Save Combined Project As",
            "",
            "GeoLabeller Project (*.geolabel)"
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
            combined_classes = list(
                dict.fromkeys(
                    project1.classes +
                    project2.classes))

            # Create combined project and deep-copy images/labels from project1
            combined = LabelProject()
            combined.classes = combined_classes

            # Helper: clone ImageData (and contained labels) to avoid mutating
            # originals
            def clone_image(image: ImageData) -> ImageData:
                """Deep-copy an ImageData (and its labels) via serialization."""
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

            self.statusBar.showMessage(
                f"Combined projects saved to {
                    Path(output_file).name}", 5000)

        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(
                self, "Error", f"Failed to combine projects: {e}")

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
                self.statusBar.showMessage(
                    f"Exported {
                        self.project.label_count} labels to {file_path}",
                    3000)
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to export ground truth: {e}")

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
            # Collect only images that have at least one label
            images = [img.to_dict()
                      for img in self.project.images.values() if img.labels]

            if not images:
                QMessageBox.information(
                    self, "Export", "No labeled images to export.")
                return

            data = {
                "version": "3.2",
                "classes": self.project.classes,
                "images": images,
                "_next_id": self.project._next_id
            }

            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)

            total_labels = sum(len(img['labels']) for img in images)
            self.statusBar.showMessage(
                f"Exported {total_labels} labels from {
                    len(images)} images to {file_path}",
                3000
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to export ground truth: {e}")

    def _export_optimized(self):
        """Export tiled + pyramided copies of the loaded GeoTIFFs."""
        infos = self.canvas.get_layer_infos()
        if not infos:
            QMessageBox.information(
                self, "Optimized GeoTIFFs",
                "No layers are loaded to optimize.")
            return

        dialog = OptimizeExportDialog(infos, self)
        if not dialog.exec_():
            return
        opts = dialog.get_options()

        # Build the (source, destination) task list, mirroring the group tree.
        tasks = []
        for info in infos:
            dst = plan_output_path(
                opts["output_dir"], info.get("group_path", ""),
                info["file_path"])
            tasks.append((info["file_path"], str(dst)))

        self._start_optimize_worker(tasks, opts)

    def _start_optimize_worker(self, tasks, opts):
        """Run the optimize worker off the UI thread with a progress dialog."""
        total = len(tasks)
        dlg = QProgressDialog("Preparing...", "Cancel", 0, total, self)
        dlg.setWindowTitle("Creating Optimized GeoTIFFs")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        thread = QThread(self)
        worker = OptimizeWorker(tasks, opts)
        worker.moveToThread(thread)

        self._optimize_thread = thread
        self._optimize_worker = worker
        self._optimize_dialog = dlg
        self._optimize_total = total

        worker.progress.connect(self._on_optimize_progress)
        worker.finished.connect(self._on_optimize_finished)
        thread.started.connect(worker.process)
        thread.finished.connect(self._on_optimize_thread_finished)
        # Direct connection so Cancel is seen between files: the worker loop has
        # no event loop of its own to deliver a queued slot call.
        dlg.canceled.connect(worker.cancel, Qt.DirectConnection)

        thread.start()

    def _on_optimize_progress(self, index: int, total: int, filename: str):
        """Update the optimize progress dialog (runs on the main thread)."""
        if self._optimize_dialog is not None:
            self._optimize_dialog.setLabelText(
                f"Optimizing {index + 1}/{total}: {filename}")
            self._optimize_dialog.setValue(index)

    def _on_optimize_finished(self, done: int, skipped: int, errors):
        """Show a summary and stop the worker thread (main thread)."""
        if self._optimize_dialog is not None:
            self._optimize_dialog.setValue(self._optimize_total)
        if self._optimize_thread is not None:
            self._optimize_thread.quit()

        msg = f"Optimized {done} image(s)."
        if skipped:
            msg += f"\nSkipped {skipped} (output already existed)."
        if errors:
            msg += f"\n\n{len(errors)} error(s):\n" + "\n".join(
                f"- {os.path.basename(p)}: {e}" for p, e in errors[:5])
            if len(errors) > 5:
                msg += f"\n... and {len(errors) - 5} more."
            QMessageBox.warning(self, "Optimized GeoTIFFs", msg)
        else:
            QMessageBox.information(self, "Optimized GeoTIFFs", msg)

        note = f"Optimized {done} image(s)"
        if skipped:
            note += f", {skipped} skipped"
        self.statusBar.showMessage(note, 5000)

    def _on_optimize_thread_finished(self):
        """Drop worker references after the thread ends (main thread)."""
        self._optimize_thread = None
        self._optimize_worker = None
        self._optimize_dialog = None

    def _h5_labels_for(self, path: str) -> list:
        """Labels on ``path`` that the export can actually use.

        Labels of a class the project no longer has are dropped here rather
        than silently inside the export, so a layer only counts as labelled
        when it would really contribute genuine (non-hard-negative) examples.
        """
        img = self.project.images.get(path)
        if img is None:
            return []
        classes = set(self.project.classes)
        return [l for l in img.labels if l.class_name in classes]

    def _h5_is_hn_source(self, path: str) -> bool:
        """Is this image flagged as a hard-negative source?"""
        img = self.project.images.get(path)
        return bool(img is not None and img.hard_negative_source)

    def _export_h5(self):
        """Export sliding-window snippets to the HDF5 CNN dataset format."""
        # The editor refuses this name now, but a project written before the
        # guard (or edited by hand) can still carry it - and exporting would
        # write two indistinguishable 'hard_negative' classes.
        if HARD_NEGATIVE in self.project.classes:
            QMessageBox.warning(
                self, "HDF5 Export",
                f"A label class is named '{HARD_NEGATIVE}', which the export "
                "reserves for its sliding-window negatives - the dataset "
                "would contain two classes with that name.\n\n"
                "Rename the class (Edit Classes), or flag whole images as "
                "hard negative sources instead.")
            return
        infos = self.canvas.get_layer_infos()
        if not infos:
            QMessageBox.information(
                self, "HDF5 Export", "No layers are loaded to export.")
            return

        all_count = len(infos)
        visible_count = sum(1 for i in infos if i.get("visible"))
        labelled_count = sum(1 for i in infos if i.get("visible")
                             and self._h5_labels_for(i["file_path"]))
        all_labelled_count = sum(1 for i in infos
                                 if self._h5_labels_for(i["file_path"]))
        hn_all_count = sum(1 for i in infos
                           if self._h5_is_hn_source(i["file_path"]))
        hn_visible_count = sum(1 for i in infos if i.get("visible")
                               and self._h5_is_hn_source(i["file_path"]))
        dialog = H5ExportDialog(all_count, visible_count, labelled_count, self,
                                defaults=self._h5_last_options,
                                all_labelled_count=all_labelled_count,
                                hn_all_count=hn_all_count,
                                hn_visible_count=hn_visible_count)
        if not dialog.exec_():
            return

        out_path = dialog.output_path()
        scope = dialog.scope()
        # Which images the scope covers. The examples-only scopes only need
        # labelled images (unlabelled ones would contribute nothing anyway).
        needs_visible = scope in (SCOPE_VISIBLE, SCOPE_LABELLED,
                                  SCOPE_VISIBLE_EXAMPLES)
        needs_labels = scope in (SCOPE_LABELLED, SCOPE_ALL_EXAMPLES,
                                 SCOPE_VISIBLE_EXAMPLES)
        options = dialog.options()
        include_hn = options.get("include_hard_negatives", False)
        # Per-image examples_only: normally the scope decides, but a flagged
        # image being pulled in must slide, so it gets False even under an
        # examples-only scope - its labels (if any) still export as examples,
        # and the engine keeps negatives off the ground they cover.
        images = []
        for info in infos:
            if needs_visible and not info.get("visible"):
                continue
            path = info["file_path"]
            labels = self._h5_labels_for(path)
            flagged = include_hn and self._h5_is_hn_source(path)
            if needs_labels and not labels and not flagged:
                continue
            images.append((path, labels,
                           options["examples_only"] and not flagged))
        if not images:
            QMessageBox.information(
                self, "HDF5 Export", "No images in the selected scope.")
            return

        self._h5_last_options = dict(options, out_path=out_path)
        options["classes"] = list(self.project.classes) + [HARD_NEGATIVE]
        self._start_h5_worker(out_path, images, options)

    def _start_h5_worker(self, out_path, images, options):
        """Run the HDF5 export off the UI thread with a progress dialog."""
        total = len(images)
        dlg = QProgressDialog("Preparing HDF5 export...", "Cancel", 0, total, self)
        dlg.setWindowTitle("Exporting HDF5 Dataset")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        thread = QThread(self)
        worker = H5ExportWorker(out_path, images, options)
        worker.moveToThread(thread)

        self._h5_thread = thread
        self._h5_worker = worker
        self._h5_dialog = dlg
        self._h5_total = total

        worker.progress.connect(self._on_h5_progress)
        worker.finished.connect(self._on_h5_finished)
        thread.started.connect(worker.process)
        thread.finished.connect(self._on_h5_thread_finished)
        dlg.canceled.connect(worker.cancel, Qt.DirectConnection)

        thread.start()

    def _on_h5_progress(self, index: int, total: int, samples: int):
        """Update the HDF5 export progress dialog (runs on the main thread)."""
        if self._h5_dialog is not None:
            self._h5_dialog.setLabelText(
                f"Image {index + 1}/{total}  ({samples:,} snippets so far)")
            self._h5_dialog.setValue(index)

    def _on_h5_finished(self, result, error: str):
        """Report the HDF5 export result and stop the worker (main thread)."""
        if self._h5_dialog is not None:
            self._h5_dialog.setValue(self._h5_total)
        if self._h5_thread is not None:
            self._h5_thread.quit()

        if error:
            QMessageBox.warning(
                self, "HDF5 Export", f"Export failed:\n\n{error}")
            return

        if not result:
            return
        msg = f"Wrote {result['total']:,} samples to\n{result['path']}"
        if result.get("cancelled"):
            msg = "Export cancelled. " + msg
        if result.get("excluded"):
            msg += (f"\n\n{result['excluded']:,} window(s) withheld from the "
                    "hard negatives for overlapping an example.")
        if result.get("split_negatives"):
            counts = result.get("negative_counts") or [0, 0, 0]
            msg += ("\n\nHard negatives split "
                    f"{counts[0]:,} train / {counts[1]:,} validate / "
                    f"{counts[2]:,} test.")
        added = result.get("added_classes") or []
        dropped = result.get("dropped_classes") or []
        if added:
            msg += ("\n\nThe file's class list gained "
                    + ", ".join(added)
                    + "; its existing labels were re-indexed to match.")
        if dropped:
            msg += ("\n\nUnused class(es) dropped from the file's class list: "
                    + ", ".join(dropped) + ".")
        errors = result.get("errors") or []
        if errors:
            msg += (f"\n\n{len(errors)} image error(s):\n"
                    + "\n".join(f"- {os.path.basename(p)}: {e}"
                                for p, e in errors[:5]))
            QMessageBox.warning(self, "HDF5 Export", msg)
        else:
            QMessageBox.information(self, "HDF5 Export", msg)
        self.statusBar.showMessage(
            f"HDF5 dataset: {result['total']:,} samples", 5000)

    def _on_h5_thread_finished(self):
        """Drop worker references after the thread ends (main thread)."""
        self._h5_thread = None
        self._h5_worker = None
        self._h5_dialog = None

    @staticmethod
    def _ground_res_per_pixel(src, px: int, py: int) -> tuple[float, float]:
        """Measure true ground metres per pixel at a pixel, for any CRS.

        Projects the pixel and its immediate right/below neighbours to WGS84
        and takes geodesic (Haversine) distances, so the result is correct for
        projected, geographic and Web Mercator sources alike (the last of which
        has a cos(lat) scale factor that raw transform coefficients ignore) and
        for non-square pixels.

        Returns (metres_per_pixel_x, metres_per_pixel_y); (0, 0) if it can't be
        determined.
        """
        try:
            transformer = Transformer.from_crs(src.crs, 4326, always_xy=True)
            ax, ay = src.transform * (px, py)
            bx, by = src.transform * (px + 1, py)
            cx, cy = src.transform * (px, py + 1)
            a_lon, a_lat = transformer.transform(ax, ay)
            b_lon, b_lat = transformer.transform(bx, by)
            c_lon, c_lat = transformer.transform(cx, cy)
            mppx = haversine_distance(a_lat, a_lon, b_lat, b_lon)
            mppy = haversine_distance(a_lat, a_lon, c_lat, c_lon)
            return mppx, mppy
        except Exception:
            return 0.0, 0.0

    def _export_subimages(self):
        """Export sub-images centered on labels as GeoTIFFs preserving original pixels."""

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
        progress = QProgressDialog(
            "Exporting sub-images...",
            "Cancel",
            0,
            self.project.label_count,
            self)
        progress.setWindowModality(QtCore_Qt.WindowModal)
        progress.setMinimumDuration(0)

        exported = 0
        errors = []

        for idx, (image_data, label) in enumerate(
                self.project.get_all_labels()):
            if progress.wasCanceled():
                break

            progress.setValue(idx)

            image_path = image_data.path
            if not os.path.exists(image_path):
                errors.append(f"Image not found: {image_path}")
                continue

            try:
                with rasterio.open(image_path) as src:
                    # Handle missing CRS
                    if src.crs is None:
                        errors.append(f"Image has no CRS: {image_path}")
                        continue

                    # Label pixel in the ORIGINAL image (absolute pixel coords)
                    pixel_x = int(round(label.pixel_x))
                    pixel_y = int(round(label.pixel_y))

                    # Skip if pixel coordinates are outside image bounds
                    if (pixel_x < 0 or pixel_x >= src.width
                            or pixel_y < 0 or pixel_y >= src.height):
                        errors.append(
                            f"Label {label.id}: pixel coords "
                            f"({pixel_x}, {pixel_y}) outside image bounds "
                            f"({src.width}x{src.height})")
                        continue

                    # True ground metres per pixel at the label, measured from
                    # the actual pixel geometry so it is correct for any CRS
                    # (projected, geographic, or Web Mercator) and for
                    # non-square pixels.
                    pixel_width_m, pixel_height_m = self._ground_res_per_pixel(
                        src, pixel_x, pixel_y)
                    if pixel_width_m <= 0 or pixel_height_m <= 0:
                        errors.append(
                            f"Label {label.id}: could not determine pixel "
                            f"resolution")
                        continue

                    # Pixels spanning the requested square ground region.
                    half_size_px_x = max(
                        1, int((size_meters / 2) / pixel_width_m))
                    half_size_px_y = max(
                        1, int((size_meters / 2) / pixel_height_m))
                    full_size_px_x = half_size_px_x * 2
                    full_size_px_y = half_size_px_y * 2

                    # Centre the window on the label using the shared rule the
                    # HDF5 snippet export also uses, so a given label frames
                    # exactly the same ground in both exports. The window is
                    # shifted (not cropped) to stay inside the raster.
                    col_start, row_start = centered_window(
                        label.pixel_x, label.pixel_y,
                        full_size_px_x, full_size_px_y, src.width, src.height)
                    col_end = min(src.width, col_start + full_size_px_x)
                    row_end = min(src.height, row_start + full_size_px_y)

                    window_width = col_end - col_start
                    window_height = row_end - row_start

                    # Skip if the resulting window is too small or invalid
                    if window_width <= 0 or window_height <= 0:
                        errors.append(
                            f"Label {
                                label.id}: invalid window size, skipped")
                        continue

                    if window_width < full_size_px_x // 2 or window_height < full_size_px_y // 2:
                        errors.append(
                            f"Label {
                                label.id} too close to edge, skipped")
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

                    # Calculate the transform for the sub-image window
                    # (preserves original CRS)
                    window_transform = rasterio.windows.transform(
                        window, src.transform)

                    # Write the cropped pixels unmodified: all source bands at
                    # the original dtype, preserving CRS/transform, colour
                    # interpretation and nodata so RGB (or any multi-band) data
                    # round-trips exactly.
                    with rasterio.open(
                        out_path,
                        'w',
                        driver='GTiff',
                        height=window_height,
                        width=window_width,
                        count=data.shape[0],
                        dtype=data.dtype,
                        crs=src.crs,
                        transform=window_transform,
                        nodata=src.nodata,
                        compress='lzw'
                    ) as dst:
                        dst.write(data)
                        # Preserve per-band colour interpretation (RGB tagging).
                        try:
                            dst.colorinterp = src.colorinterp
                        except Exception:
                            pass

                    exported += 1

            except Exception as e:
                errors.append(
                    f"Error processing label {
                        label.id} from {image_path}: {e}")

        progress.setValue(self.project.label_count)

        # Show results
        msg = f"Exported {exported} sub-images to {output_dir}"
        if errors:
            msg += f"\n\n{len(errors)} errors occurred:\n" + \
                "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n... and {len(errors) - 5} more errors"
            QMessageBox.warning(self, "Export Complete", msg)
        else:
            self.statusBar.showMessage(msg, 5000)
            QMessageBox.information(self, "Export Complete", msg)

    def _add_geotiff(self):
        """Open file dialog to add a GeoTIFF image."""
        file_filter = "GeoTIFF (*.tif *.tiff);;All Files (*)"
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Image",
            "",
            file_filter
        )

        skipped = 0
        for file_path in file_paths:
            # Check if already loaded
            if self.canvas.is_path_loaded(file_path):
                skipped += 1
                continue

            # Check if the file has a valid CRS
            has_crs = True
            try:
                with rasterio.open(file_path) as src:
                    if src.crs is None:
                        has_crs = False
            except Exception:
                pass

            if has_crs:
                layer_id = self.canvas.add_layer(file_path)
                if layer_id:
                    self.layer_panel.add_layer(layer_id, file_path)
            else:
                layer_id = self.canvas.add_pixel_layer(file_path)
                if layer_id:
                    self.layer_panel.add_nongeo_layer(layer_id, file_path)

            if layer_id:
                # Track the loaded image with original dimensions and transform
                name = Path(file_path).stem
                width, height = self.canvas.get_layer_source_dimensions(
                    layer_id)
                affine, crs = self.canvas.get_layer_transform(layer_id)
                self.project.add_image(
                    file_path, name, "", width, height,
                    affine=affine, crs=crs)

        # A re-added image whose path is flagged in the project should show
        # up in the mirror section straight away.
        self._refresh_hard_negative_panel()

        if skipped > 0:
            self.statusBar.showMessage(
                f"Skipped {skipped} already loaded image(s)", 3000)

    def _add_directory(self):
        """Open directory dialog and load all supported images preserving directory structure.

        Uses async loading for better performance with large directories:
        - Files are discovered and tree structure is built immediately
        - Actual file loading happens in background
        - Layers default to hidden (unchecked) during import
        - User can start working while files continue loading
        """
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Directory with Images",
            "",
            QFileDialog.ShowDirsOnly
        )

        if not dir_path:
            return

        # Find all supported files recursively
        root_path = Path(dir_path)
        image_files = []
        for pattern in ("*.tif", "*.tiff"):
            image_files.extend(root_path.rglob(pattern))
        # Deduplicate (in case of overlapping patterns) and sort
        image_files = sorted(set(image_files))

        if not image_files:
            self.statusBar.showMessage(
                "No supported image files found in directory", 5000)
            return

        # Check for large import - use async for better UX
        use_async = len(image_files) > 50

        if use_async:
            self._add_directory_async(root_path, image_files)
        else:
            self._add_directory_sync(root_path, image_files)

    def _add_directory_sync(self, root_path: Path, image_files: list):
        """Synchronous directory loading for smaller imports."""
        # Create root group for the selected directory
        root_group_name = root_path.name
        root_group = self.layer_panel.add_group(
            root_group_name, None, visible=False)

        # Build directory structure with groups under the root group
        group_cache: dict[Path, any] = {}
        nongeo_group_cache: dict[str, any] = {}

        def get_or_create_group(rel_dir: Path):
            """Return the panel group for a directory, creating parents as needed."""
            if rel_dir == Path("."):
                return root_group  # Files at root level go under the root group
            if rel_dir in group_cache:
                return group_cache[rel_dir]
            parent_group = get_or_create_group(rel_dir.parent)
            group = self.layer_panel.add_group(
                rel_dir.name, parent_group, visible=False)
            group_cache[rel_dir] = group
            return group

        def get_or_create_nongeo_group(name: str):
            """Return the cached non-geo panel group for a name, creating it once."""
            if name in nongeo_group_cache:
                return nongeo_group_cache[name]
            group = self.layer_panel.add_nongeo_group(name)
            nongeo_group_cache[name] = group
            return group

        # Create progress dialog
        progress = QProgressDialog(
            "Loading image files...",
            "Cancel",
            0,
            len(image_files),
            self
        )
        progress.setWindowTitle("Loading Images")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        loaded_count = 0
        for i, file_path in enumerate(image_files):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(
                f"Loading {
                    file_path.name}...\n({
                    i +
                    1} of {
                    len(image_files)})")
            QApplication.processEvents()

            rel_path = file_path.relative_to(root_path)
            rel_dir = rel_path.parent
            parent_group = get_or_create_group(rel_dir)

            file_path_str = str(file_path)
            if self.canvas.is_path_loaded(file_path_str):
                continue

            # Check CRS to decide geo vs pixel-mode loading
            has_crs = True
            try:
                with rasterio.open(file_path_str) as src:
                    if src.crs is None:
                        has_crs = False
            except Exception:
                pass

            rel_dir_str = str(rel_dir).replace(
                "\\", "/") if rel_dir != Path(".") else ""
            group_path_str = f"{root_group_name}/{rel_dir_str}" if rel_dir_str else root_group_name

            if has_crs:
                layer_id = self.canvas.add_layer(
                    file_path_str, visible=False)
                if layer_id:
                    self.layer_panel.add_layer(
                        layer_id, file_path_str, parent_group, visible=False)
                    self.canvas.set_layer_group(layer_id, group_path_str)
            else:
                layer_id = self.canvas.add_pixel_layer(
                    file_path_str, group_path=group_path_str, visible=False)
                if layer_id:
                    nongeo_parent = get_or_create_nongeo_group(
                        rel_dir.name if rel_dir != Path(".") else root_group_name)
                    self.layer_panel.add_nongeo_layer(
                        layer_id, file_path_str, nongeo_parent, visible=False)

            if layer_id:
                name = file_path.stem
                width, height = self.canvas.get_layer_source_dimensions(
                    layer_id)
                affine, crs = self.canvas.get_layer_transform(layer_id)
                self.project.add_image(
                    file_path_str, name, group_path_str, width, height,
                    affine=affine, crs=crs)
                loaded_count += 1

        progress.setValue(len(image_files))

        # Remove empty geo groups (e.g. directory had only non-geo files)
        self._remove_empty_groups(root_group)

        self.layer_panel.tree.collapseAll()

        if progress.wasCanceled():
            self.statusBar.showMessage(
                f"Loading cancelled. Loaded {loaded_count} of {
                    len(image_files)} image files", 5000)
        else:
            self.statusBar.showMessage(
                f"Loaded {loaded_count} of {
                    len(image_files)} image files", 5000)

    def _add_directory_async(self, root_path: Path, image_files: list):
        """Asynchronous directory loading for large imports.

        Layers are added with lazy loading (only bounds read initially) and
        default to hidden. The tree updates progressively as files are discovered.
        """
        # Get root folder name for the group
        root_group_name = root_path.name

        # Prepare file list with group paths (prefixed with root folder name)
        files_with_groups = []
        for file_path in image_files:
            rel_path = file_path.relative_to(root_path)
            rel_dir = rel_path.parent
            rel_dir_str = str(rel_dir).replace(
                "\\", "/") if rel_dir != Path(".") else ""
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

    def _start_unified_async_loading(self,
                                     files_with_groups: list[tuple[str,
                                                                   str]],
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
        # Only one loader at a time: starting a second while the first runs
        # (Locate Missing Images during a long project load) clobbered the
        # thread reference, and the first one's completion then wait()ed on
        # the SECOND, freezing the UI and tearing progress state down under
        # a live load.
        if self._async_loader is not None:
            self._async_loader.cancel()
            self._async_loader.wait()
            self._async_loader.deleteLater()
            self._async_loader = None
            self._async_ui_timer.stop()
            self._async_pending_files.clear()

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
        self._async_loader.batch_complete.connect(
            self._on_async_batch_complete)
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

    def _remove_empty_groups(self, item):
        """Recursively remove empty group items from the layer tree.

        A group is empty if it has no children after pruning.
        """
        if item is None:
            return
        # Process children bottom-up
        for i in range(item.childCount() - 1, -1, -1):
            child = item.child(i)
            if child.data(0, Qt.UserRole + 1) == "group":
                self._remove_empty_groups(child)
        # If this group is now childless, remove it
        if item.childCount() == 0:
            parent = item.parent()
            if parent:
                parent.removeChild(item)
            else:
                index = self.layer_panel.tree.indexOfTopLevelItem(item)
                if index >= 0:
                    self.layer_panel.tree.takeTopLevelItem(index)

    def _find_existing_group(self, name: str, parent):
        """An already-present group item with this name, or None.

        ``parent`` None means the tree's top level, matching add_group.
        """
        tree = self.layer_panel.tree
        if parent is None:
            children = [tree.topLevelItem(i)
                        for i in range(tree.topLevelItemCount())]
        else:
            children = [parent.child(i) for i in range(parent.childCount())]
        for item in children:
            if (item is not None
                    and item.data(0, QtCore_Qt.UserRole + 1) == "group"
                    and item.text(0) == name):
                return item
        return None

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
                # A relocation pass re-enters loading on a populated panel;
                # the cache alone would recreate groups the first pass made,
                # putting the relocated image in a duplicate tree.
                existing = self._find_existing_group(part, parent)
                if existing is not None:
                    self._async_group_cache[current_key] = existing
                else:
                    # Create group with visible=False for async imports
                    group = self.layer_panel.add_group(
                        part, parent, visible=False)
                    self._async_group_cache[current_key] = group
            parent = self._async_group_cache[current_key]

        return parent

    def _get_or_create_nongeo_group_async(self, group_path: str):
        """Get or create group hierarchy for non-georeferenced async loading.

        Uses the 'Non-Georeferenced' tree section in the layer panel.
        """
        nongeo_key = Path("__nongeo__")
        if nongeo_key not in self._async_group_cache:
            nongeo_root = self.layer_panel.get_or_create_nongeo_root()
            self._async_group_cache[nongeo_key] = nongeo_root

        if not group_path:
            return self._async_group_cache[nongeo_key]

        # Build sub-groups under the non-geo root
        parts = group_path.split("/")
        parent = self._async_group_cache[nongeo_key]
        current_path = "__nongeo__"

        for part in parts:
            current_path = f"{current_path}/{part}"
            current_key = Path(current_path.replace("/", "\\"))

            if current_key not in self._async_group_cache:
                group = self.layer_panel.add_nongeo_group(part, parent, visible=False)
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
                is_geo = layer_data.get('geo', True)

                if is_geo:
                    parent_group = self._get_or_create_group_async(group_path)

                    # Add georeferenced layer with lazy loading
                    # The worker already opened this file and shipped its
                    # header; passing it along means no rasterio open runs on
                    # the UI thread for the whole import.
                    layer_id = self.canvas.add_layer(
                        file_path, lazy=True, visible=False,
                        metadata=layer_data)
                    if layer_id:
                        self.layer_panel.add_layer(
                            layer_id, file_path, parent_group, visible=False)
                        self.canvas.set_layer_group(layer_id, group_path)
                else:
                    # Non-georeferenced: add to pixel zone
                    parent_group = self._get_or_create_nongeo_group_async(group_path)

                    layer_id = self.canvas.add_pixel_layer(
                        file_path, group_path=group_path, lazy=True,
                        visible=False, metadata=layer_data)
                    if layer_id:
                        self.layer_panel.add_nongeo_layer(
                            layer_id, file_path, parent_group, visible=False)

                if layer_id:
                    # Track in project with original dimensions (skip for
                    # project loading)
                    if not self._async_skip_project_add:
                        name = Path(file_path).stem
                        width, height = self.canvas.get_layer_source_dimensions(
                            layer_id)
                        affine, crs = self.canvas.get_layer_transform(layer_id)
                        self.project.add_image(
                            file_path, name, group_path, width, height,
                            affine=affine, crs=crs)

                    self._async_loaded_count += 1
        finally:
            self.layer_panel.end_batch_update()

    def _on_async_file_error(self, file_path: str, error: str):
        """Handle a file failing to load."""
        name = os.path.basename(file_path)
        print(f"Failed to load {file_path}: {error}")
        self.statusBar.showMessage(f"Failed to load {name}: {error}", 8000)

    def _on_async_progress(self, processed: int, total: int):
        """Handle progress updates during async loading."""
        self._update_progress(processed)
        self.statusBar.showMessage(
            f"Loading files: {processed}/{total} ({
                self._async_loaded_count} added)..."
        )

    def _on_async_batch_complete(self, loaded: int, errors: int):
        """Handle async loading completion for both directory and project modes."""
        # Stop the UI update timer
        self._async_ui_timer.stop()

        # Process any remaining pending files with progress events
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
        # Remove empty geo groups (e.g. directory had only non-geo files)
        if hasattr(self, '_async_group_cache'):
            for item in self._async_group_cache.values():
                self._remove_empty_groups(item)

        self._refresh_hard_negative_panel()
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
        self._refresh_waypoints()
        self._refresh_hard_negative_panel()

        # Update window title (handle recovery case where _project_path is None)
        if self._project_path:
            self.setWindowTitle(f"{app_title()} - {self._project_path.name}")
        else:
            self.setWindowTitle(f"{app_title()} - Recovered Session (unsaved)")

        # Build status message
        msg = f"Opened project with {self.project.label_count} labels"
        if errors > 0:
            msg += f" ({errors} load errors)"
        self.statusBar.showMessage(msg, 3000)

        # Missing images: offer to find them rather than only reporting
        # them - a project shared from another machine usually has ALL of
        # them somewhere on this one, just under different paths.
        if self._async_missing_files:
            self._async_missing_files = []  # consumed; recomputed on demand
            self._offer_relocation()

    def _missing_project_images(self) -> list:
        """The ImageData entries whose recorded paths do not exist."""
        return [img for img in self.project.images.values()
                if not os.path.exists(img.path)]

    def _offer_relocation(self):
        """Show the relocation dialog for whatever is currently missing."""
        missing = self._missing_project_images()
        if not missing:
            QMessageBox.information(
                self, "Locate Missing Images",
                "Every image path in this project resolves on this machine.")
            return
        dialog = RelocateImagesDialog(missing, self)
        if not dialog.exec_():
            return
        applied, refused = 0, 0
        for res in dialog.found_resolutions():
            old_path = res.old_path
            if self.canvas.is_path_loaded(old_path):
                # Loaded-but-missing: the file vanished after it was loaded.
                # Drop the stale layer so the reload builds a fresh one keyed
                # by the new path instead of stranding the old.
                layer_id = self.canvas._path_to_layer.get(old_path)
                if layer_id is not None:
                    self.canvas.remove_layer(layer_id)
            if self.project.relocate_image(
                    old_path, os.path.abspath(res.new_path)):
                applied += 1
            else:
                refused += 1
        if not applied and not refused:
            return
        note = f" ({refused} could not be applied)" if refused else ""
        self.statusBar.showMessage(
            f"Relocated {applied} image(s){note} - save the project to keep "
            "the new paths", 8000)
        if not applied:
            return
        # Load the newly found images through the normal project pipeline;
        # already-loaded paths are skipped by the loader, and any still-
        # missing images simply come around again.
        self._show_progress(applied, "Loading relocated images")
        self._start_project_image_loading()

    def _on_batch_visibility_started(self, total: int):
        """Handle start of batch visibility change (e.g., group toggle)."""
        self._show_progress(total, "Toggling")

    # ── Group memory management ──────────────────────────────────────

    def _on_group_preload_requested(self, layer_ids: list[str]):
        """Preload all layers in a group into memory (full reproject)."""
        layers = []
        for lid in layer_ids:
            layer = self.canvas.get_layer(lid)
            if layer and not layer.is_fully_loaded():
                # Pass only load parameters; the worker must not touch the
                # live layer off the UI thread.
                layers.append((lid, layer.file_path, layer.geo))

        if not layers:
            QMessageBox.information(self, "Preload Group",
                                   "All layers in this group are already loaded.")
            return

        self._start_group_memory_worker(layers, "Preloading")

    def _on_group_free_requested(self, layer_ids: list[str]):
        """Free pixel data for all layers in a group."""
        layers = []
        for lid in layer_ids:
            layer = self.canvas.get_layer(lid)
            if layer and layer.is_fully_loaded():
                layers.append((lid, layer))

        if not layers:
            QMessageBox.information(self, "Free Group",
                                   "No loaded layers to free in this group.")
            return

        # Cancel any in-flight load and remove on-screen tiles before
        # freeing, or a refine already reading would reallocate the data
        # moments after the user freed it.
        for lid, layer in layers:
            self.canvas.free_layer_data(lid)

        QMessageBox.information(
            self, "Free Group",
            f"Freed pixel data for {len(layers)} layer(s).")

    def _start_group_memory_worker(self, layers, label):
        """Launch the preload worker with a progress dialog.

        The worker computes each layer's pixel data off-thread and emits it via
        ``layer_ready``; results are applied to the live layers on the main
        thread by ``_on_preload_layer_ready``. All worker signals connect to
        bound-method slots (not lambdas): because the worker lives on another
        thread, those connect as QueuedConnection and run on the UI thread,
        which is what keeps the shared layer state off the worker thread.
        """
        total = len(layers)

        dlg = QProgressDialog(f"{label} 0/{total}...", "Cancel", 0, total, self)
        dlg.setWindowTitle(label)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        thread = QThread(self)
        worker = GroupMemoryWorker(layers)
        worker.moveToThread(thread)

        # Store references so they aren't garbage-collected and so the
        # main-thread slots can reach the dialog/thread.
        self._group_mem_thread = thread
        self._group_mem_worker = worker
        self._group_mem_dialog = dlg
        self._group_mem_total = total
        self._group_mem_label = label

        worker.progress.connect(self._on_preload_progress)
        worker.layer_ready.connect(self._on_preload_layer_ready)
        worker.finished.connect(self._on_preload_finished)
        worker.error.connect(self._on_preload_error)
        thread.started.connect(worker.process)
        thread.finished.connect(self._on_preload_thread_finished)

        dlg.canceled.connect(worker.cancel)
        dlg.canceled.connect(thread.quit)

        thread.start()

    def _on_preload_progress(self, current: int, total: int):
        """Update the preload progress dialog (runs on the main thread)."""
        if self._group_mem_dialog is not None:
            self._group_mem_dialog.setLabelText(
                f"{self._group_mem_label} {current}/{total}...")
            self._group_mem_dialog.setValue(current)

    def _on_preload_layer_ready(self, layer_id: str, result: dict):
        """Apply off-thread-computed pixel data to the live layer (main thread).

        Delivered via QueuedConnection, so the mutation of the live TiledLayer
        happens on the UI thread and can never race the renderer.
        """
        layer = self.canvas.get_layer(layer_id)
        if layer is None:
            return
        if layer._loading_level is not None:
            # The canvas is mid-load for this layer (the user zoomed or made
            # it visible while the preload ran). Cancel it rather than throw
            # this result away: the in-flight load can die without delivering
            # (culled, hidden, trimmed), which left the layer with nothing at
            # all despite the preload dialog reporting it loaded. The
            # scheduler below re-chases the zoom's own level if it differs.
            self.canvas._cancel_layer_load(layer)
        layer.apply_level_result(result)
        # Tiles rendered from the previous array are stale the moment the new
        # one lands; rebuild visible layers now rather than leaving mixed
        # generations on screen until the whole group finishes.
        if layer.visible:
            self.canvas._clear_layer_tiles(layer)
            self.canvas._rebuild_layer_tiles(layer)
        self.canvas._schedule_tile_update()

    def _on_preload_finished(self):
        """Complete the dialog and stop the worker thread (main thread)."""
        if self._group_mem_dialog is not None:
            self._group_mem_dialog.setValue(self._group_mem_total)
        if self._group_mem_thread is not None:
            self._group_mem_thread.quit()

    def _on_preload_thread_finished(self):
        """Refresh tiles for now-loaded layers and drop worker refs (main thread)."""
        self.canvas._update_visible_tiles()
        self._group_mem_thread = None
        self._group_mem_worker = None
        self._group_mem_dialog = None

    def _on_preload_error(self, layer_id: str, msg: str):
        """Log a per-layer preload failure."""
        print(f"Group preload error on {layer_id}: {msg}")

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

    def _update_coordinates(self, x: float, y: float,
                            layer_name: str, group_path: str,
                            is_pixel: bool = False):
        """Update the coordinate display in the status bar."""
        if layer_name:
            # Build display name with group path if present
            if group_path:
                display_name = f"{group_path}/{layer_name.lstrip('~')}"
            else:
                display_name = layer_name.lstrip('~')

            if is_pixel:
                # Non-georeferenced image: show pixel coordinates
                self.coord_label.setText(
                    f"Pixel: ({x:.1f}, {y:.1f})  |  Image: {display_name}")
            elif layer_name.startswith("~"):
                # Layer name prefixed with ~ means "closest to"
                self.coord_label.setText(
                    f"Lon: {
                        x:.6f}°  Lat: {
                        y:.6f}°  |  Nearest: {display_name}")
            else:
                self.coord_label.setText(
                    f"Lon: {
                        x:.6f}°  Lat: {
                        y:.6f}°  |  Image: {display_name}")
        else:
            if is_pixel:
                self.coord_label.setText(f"Pixel: ({x:.1f}, {y:.1f})")
            else:
                self.coord_label.setText(f"Lon: {x:.6f}°  Lat: {y:.6f}°")

    def _on_hard_negative_toggled(self, layer_id: str):
        """Flip an image's hard-negative-source flag.

        Reached from the canvas context menu and from the mirror panel's
        "Remove hard negative flag" (its entries are always flagged, so a
        flip there is an unflag). The project owns the flag; the canvas set
        and the panel are both refreshed from it afterwards.
        """
        file_path = self.canvas.get_layer_file_path(layer_id)
        if not file_path:
            return
        img = self.project.images.get(file_path)
        if img is None:
            # A never-labelled image has no project entry yet; create one the
            # same way loading an image does, so the flag has somewhere to
            # live and survives save/load.
            name = Path(file_path).stem
            width, height = self.canvas.get_layer_source_dimensions(layer_id)
            affine, crs = self.canvas.get_layer_transform(layer_id)
            img = self.project.add_image(
                file_path, name, "", width, height, affine=affine, crs=crs)
        img.hard_negative_source = not img.hard_negative_source
        self._refresh_hard_negative_panel()
        self.statusBar.showMessage(
            f"'{img.name}' "
            f"{'flagged as' if img.hard_negative_source else 'no longer'} "
            f"a hard negative source", 4000)

    def _refresh_hard_negative_panel(self):
        """Rebuild the mirror section and the canvas's menu state.

        Entries are the project's flagged images joined against the loaded
        layers - a flagged image whose layer is not loaded keeps its flag in
        the project but has nothing to show or export right now.
        """
        entries = []
        for info in self.canvas.get_layer_infos():
            path = info["file_path"]
            img = self.project.images.get(path)
            flagged = bool(img is not None and img.hard_negative_source)
            self.canvas.set_hard_negative_flag(path, flagged)
            if flagged:
                entries.append({
                    "layer_id": info["layer_id"],
                    "file_path": path,
                    "name": info.get("name") or Path(path).stem,
                    "group": info.get("group_path", ""),
                    "visible": bool(info.get("visible")),
                })
        self.layer_panel.refresh_hard_negatives(entries)

    def _on_layer_group_changed(self, layer_id: str, group_path: str):
        """Handle layer group change - update both canvas and project."""
        # Update canvas
        self.canvas.set_layer_group(layer_id, group_path)

        # Update project
        file_path = self.canvas.get_layer_file_path(layer_id)
        if file_path:
            self.project.update_image_group(file_path, group_path)

    def _show_shortcuts(self):
        """Show the keyboard shortcut reference."""
        ShortcutsDialog(self).exec_()

    def _show_icd(self):
        """Open the Interface Control Document in the system PDF viewer.

        The document ships beside the executable, so an installed copy has it
        without needing the repository or a network connection. Handing it to
        the OS rather than rendering it here means the user gets whatever
        reader they already use, with their own search and bookmarks.
        """
        path = icd_path()
        if not path.exists():
            QMessageBox.information(
                self, "Interface Control Document",
                "The ICD was not found at:\n\n"
                f"{path}\n\n"
                "It ships with the installed application; a copy is also in "
                "the project's docs folder.")
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            # No handler registered for PDFs, or the shell refused it. Say
            # where the file is so it can be opened by hand.
            QMessageBox.warning(
                self, "Interface Control Document",
                "Could not open the PDF viewer. The document is at:\n\n"
                f"{path}")

    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self, "About GeoLabeller",
            f"<h2>{app_title()}</h2>"
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
            "</ul>")

    def _show_debug_console(self):
        """Open (or focus) the live Debug Console window."""
        if self._debug_console is None:
            self._debug_console = DebugConsole(self)
        self._debug_console.show()
        self._debug_console.raise_()
        self._debug_console.activateWindow()
        debug("Debug Console opened")

    def closeEvent(self, event):
        """Handle window close - ensure async loaders are properly cleaned up."""
        # Clean up crash detection and recovery
        self._clean_exit()

        # Cancel and wait for any running async loader (GeoTIFF)
        if hasattr(self, '_async_loader') and self._async_loader is not None:
            if self._async_loader.isRunning():
                self._async_loader.cancel()
                self._async_loader.wait()
            self._async_loader = None

        # Cancel and wait for any running group memory worker
        if hasattr(self, '_group_mem_thread') and self._group_mem_thread is not None:
            if self._group_mem_thread.isRunning():
                if self._group_mem_worker:
                    self._group_mem_worker.cancel()
                self._group_mem_thread.quit()
                self._group_mem_thread.wait()
            self._group_mem_thread = None
            self._group_mem_worker = None

        # Stop the UI timers if running
        if hasattr(self, '_async_ui_timer'):
            self._async_ui_timer.stop()

        super().closeEvent(event)
