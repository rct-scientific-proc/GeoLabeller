"""Layer panel for managing loaded layers and groups."""
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog, QMessageBox, QStyle, QApplication,
    QLabel, QSplitter
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor


class LayerTreeWidget(QTreeWidget):
    """Tree widget that emits signal after drag-drop."""
    
    items_reordered = pyqtSignal()
    
    def dropEvent(self, event):
        """Handle drop and emit reorder signal."""
        super().dropEvent(event)
        self.items_reordered.emit()


class LayerPanel(QWidget):
    """Panel for managing layers and groups."""
    
    # Signals
    layer_visibility_changed = pyqtSignal(str, bool)  # layer_id, visible
    layers_reordered = pyqtSignal(list)  # list of layer_ids
    layer_group_changed = pyqtSignal(str, str)  # layer_id, group_path
    zoom_to_layer_requested = pyqtSignal(str)  # layer_id
    layer_removed = pyqtSignal(str)  # layer_id
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Tree widget for layers and groups
        self.tree = LayerTreeWidget()
        self.tree.setHeaderLabel("Layers")
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # Connect signals
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.items_reordered.connect(self._on_rows_moved)
        
        layout.addWidget(self.tree)
    
    def add_layer(self, layer_id: str, file_path: str, parent: QTreeWidgetItem = None):
        """Add a layer item to the tree.
        
        Args:
            layer_id: Unique identifier for the layer
            file_path: Path to the GeoTIFF file
            parent: Optional parent group item. If None, adds to top level.
        """
        item = QTreeWidgetItem()
        item.setText(0, os.path.basename(file_path))
        item.setData(0, Qt.UserRole, layer_id)
        item.setData(0, Qt.UserRole + 1, "layer")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        item.setToolTip(0, file_path)
        
        # Set image icon for layers
        style = QApplication.style()
        item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
        
        if parent:
            parent.addChild(item)
        else:
            self.tree.addTopLevelItem(item)
    
    def add_group(self, name: str, parent: QTreeWidgetItem = None):
        """Add a group to the tree.
        
        Args:
            name: Display name for the group
            parent: Optional parent group item. If None, adds to top level.
        """
        item = QTreeWidgetItem()
        item.setText(0, name)
        item.setData(0, Qt.UserRole + 1, "group")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        
        # Set folder icon and bold font for groups
        style = QApplication.style()
        item.setIcon(0, style.standardIcon(QStyle.SP_DirIcon))
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, QColor(70, 130, 180))  # Steel blue color
        
        if parent:
            parent.addChild(item)
        else:
            self.tree.addTopLevelItem(item)
        return item
    
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle item check state changes."""
        item_type = item.data(0, Qt.UserRole + 1)
        checked = item.checkState(0) == Qt.Checked
        
        if item_type == "layer":
            layer_id = item.data(0, Qt.UserRole)
            self.layer_visibility_changed.emit(layer_id, checked)
        elif item_type == "group":
            # Toggle all children
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, item.checkState(0))
    
    def _on_rows_moved(self):
        """Handle drag-drop reordering."""
        layer_order = self._get_layer_order()
        self.layers_reordered.emit(layer_order)
        
        # Emit group changes for all layers (their groups may have changed)
        self._emit_all_layer_group_changes()
    
    def _get_group_path(self, item: QTreeWidgetItem) -> str:
        """Get the group path for an item by traversing up the tree."""
        parts = []
        parent = item.parent()
        while parent:
            parts.append(parent.text(0))
            parent = parent.parent()
        # Reverse to get root-to-leaf order
        parts.reverse()
        return "/".join(parts)
    
    def _emit_all_layer_group_changes(self):
        """Emit group change signals for all layers."""
        def emit_for_item(item: QTreeWidgetItem):
            item_type = item.data(0, Qt.UserRole + 1)
            if item_type == "layer":
                layer_id = item.data(0, Qt.UserRole)
                group_path = self._get_group_path(item)
                self.layer_group_changed.emit(layer_id, group_path)
            else:
                for i in range(item.childCount()):
                    emit_for_item(item.child(i))
        
        for i in range(self.tree.topLevelItemCount()):
            emit_for_item(self.tree.topLevelItem(i))
    
    def _get_layer_order(self) -> list[str]:
        """Get layer IDs in current order (top to bottom in tree = front to back)."""
        layers = []
        
        def collect_layers(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    collect_layers(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    layers.append(parent.data(0, Qt.UserRole))
                else:
                    for i in range(parent.childCount()):
                        collect_layers(parent.child(i))
        
        collect_layers()
        # Reverse so bottom items render first
        return list(reversed(layers))
    
    def _show_context_menu(self, position):
        """Show right-click context menu."""
        menu = QMenu()
        
        # Add group action
        add_group_action = menu.addAction("New Group")
        add_group_action.triggered.connect(self._create_group)
        
        # If item selected, show options
        item = self.tree.itemAt(position)
        if item:
            item_type = item.data(0, Qt.UserRole + 1)
            
            # Zoom to layer option (only for layers, not groups)
            if item_type == "layer":
                menu.addSeparator()
                zoom_action = menu.addAction("Zoom to Layer")
                layer_id = item.data(0, Qt.UserRole)
                zoom_action.triggered.connect(lambda: self.zoom_to_layer_requested.emit(layer_id))
            
            # Group-specific options
            if item_type == "group":
                menu.addSeparator()
                select_all_action = menu.addAction("Select all children")
                select_all_action.triggered.connect(lambda: self._set_all_children_checked(item, True))
                
                unselect_all_action = menu.addAction("Unselect all children")
                unselect_all_action.triggered.connect(lambda: self._set_all_children_checked(item, False))
            
            menu.addSeparator()
            remove_action = menu.addAction("Remove")
            remove_action.triggered.connect(lambda: self._remove_item(item))
        
        menu.exec_(self.tree.mapToGlobal(position))
    
    def _create_group(self):
        """Create a new group via dialog."""
        name, ok = QInputDialog.getText(self, "New Group", "Group name:")
        if ok and name:
            self.add_group(name)
    
    def _set_all_children_checked(self, item: QTreeWidgetItem, checked: bool):
        """Recursively set check state for all children of an item.
        
        Args:
            item: The parent item whose children will be updated
            checked: True to check all children, False to uncheck
        """
        check_state = Qt.Checked if checked else Qt.Unchecked
        
        def set_children_state(parent: QTreeWidgetItem):
            for i in range(parent.childCount()):
                child = parent.child(i)
                child.setCheckState(0, check_state)
                # Recurse into nested groups
                if child.data(0, Qt.UserRole + 1) == "group":
                    set_children_state(child)
        
        set_children_state(item)
        # Also set the group item itself
        item.setCheckState(0, check_state)
    
    def _remove_item(self, item: QTreeWidgetItem):
        """Remove an item from the tree."""
        item_type = item.data(0, Qt.UserRole + 1)
        
        if item_type == "group" and item.childCount() > 0:
            reply = QMessageBox.question(
                self,
                "Remove Group",
                "This group contains layers. Remove anyway?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        
        # Collect layer IDs to remove (for groups, get all children)
        layer_ids_to_remove = []
        self._collect_layer_ids(item, layer_ids_to_remove)
        
        # Get parent and remove from tree
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
        
        # Emit removal signals for each layer
        for layer_id in layer_ids_to_remove:
            self.layer_removed.emit(layer_id)
    
    def _collect_layer_ids(self, item: QTreeWidgetItem, layer_ids: list):
        """Recursively collect layer IDs from an item and its children."""
        item_type = item.data(0, Qt.UserRole + 1)
        if item_type == "layer":
            layer_ids.append(item.data(0, Qt.UserRole))
        else:
            for i in range(item.childCount()):
                self._collect_layer_ids(item.child(i), layer_ids)
    
    def uncheck_layers(self, layer_ids: list[str]):
        """Uncheck (hide) layers by their IDs.
        
        Args:
            layer_ids: List of layer IDs to uncheck
        """
        def find_and_uncheck(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_uncheck(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) in layer_ids:
                        parent.setCheckState(0, Qt.Unchecked)
                else:
                    for i in range(parent.childCount()):
                        find_and_uncheck(parent.child(i))
        
        find_and_uncheck()
    
    def clear(self):
        """Clear all items from the tree."""
        self.tree.clear()
    
    def set_layer_checked(self, layer_id: str, checked: bool):
        """Set the check state of a specific layer without emitting signals.
        
        Args:
            layer_id: The layer ID to update
            checked: True to check, False to uncheck
        """
        def find_and_set(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    if find_and_set(self.tree.topLevelItem(i)):
                        return True
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) == layer_id:
                        # Block signals to prevent cascading updates
                        self.tree.blockSignals(True)
                        parent.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                        self.tree.blockSignals(False)
                        return True
                else:
                    for i in range(parent.childCount()):
                        if find_and_set(parent.child(i)):
                            return True
            return False
        
        find_and_set()
    
    def get_layer_id_by_path(self, file_path: str) -> str | None:
        """Find layer ID by file path.
        
        Args:
            file_path: The file path to search for
            
        Returns:
            The layer ID if found, None otherwise
        """
        def find_layer(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    result = find_layer(self.tree.topLevelItem(i))
                    if result:
                        return result
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.toolTip(0) == file_path:
                        return parent.data(0, Qt.UserRole)
                else:
                    for i in range(parent.childCount()):
                        result = find_layer(parent.child(i))
                        if result:
                            return result
            return None
        
        return find_layer()


class LabeledLayerPanel(QWidget):
    """Panel showing images that have labels, grouped by object_id."""
    
    # Signals
    layer_visibility_changed = pyqtSignal(str, bool)  # layer_id, visible
    zoom_to_layer_requested = pyqtSignal(str)  # layer_id
    
    def __init__(self):
        super().__init__()
        self._layer_id_map: dict[str, str] = {}  # file_path -> layer_id (from main panel)
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Header label
        header = QLabel("Labeled Images")
        header.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(header)
        
        # Tree widget for labeled images grouped by object_id
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Images with Labels")
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # Connect signals
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        
        layout.addWidget(self.tree)
    
    def set_layer_id_map(self, file_path: str, layer_id: str):
        """Register the mapping from file path to layer ID.
        
        Args:
            file_path: The file path of the image
            layer_id: The layer ID assigned by the main layer panel
        """
        self._layer_id_map[file_path] = layer_id
    
    def get_layer_id(self, file_path: str) -> str | None:
        """Get the layer ID for a file path."""
        return self._layer_id_map.get(file_path)
    
    def refresh(self, project):
        """Refresh the tree with current labeled images from the project.
        
        Args:
            project: The LabelProject containing images and labels
        """
        self.tree.blockSignals(True)
        self.tree.clear()
        
        # Group images by object_id
        # object_id -> list of (image_path, image_name, label_count_for_this_object)
        object_groups: dict[str, list[tuple[str, str, int]]] = {}
        
        # Also track total labels per image for display
        image_total_labels: dict[str, int] = {}
        
        for image in project.images.values():
            if not image.labels:
                continue
            
            # Count total labels per image
            image_total_labels[image.path] = len(image.labels)
            
            # Get unique object_ids for this image
            for label in image.labels:
                object_id = label.object_id
                if object_id not in object_groups:
                    object_groups[object_id] = []
                
                # Check if this image is already in the group for this object_id
                existing = [x for x in object_groups[object_id] if x[0] == image.path]
                if not existing:
                    object_groups[object_id].append((image.path, image.name, 1))
                else:
                    # Update label count for this object_id
                    idx = object_groups[object_id].index(existing[0])
                    path, name, count = object_groups[object_id][idx]
                    object_groups[object_id][idx] = (path, name, count + 1)
        
        # Create tree items
        style = QApplication.style()
        
        for object_id, images in object_groups.items():
            if len(images) > 1:
                # Multiple images share this object_id - create a group
                group_item = QTreeWidgetItem()
                short_id = object_id[:8] + "..."  # Truncate UUID for display
                group_item.setText(0, f"Object: {short_id}")
                group_item.setData(0, Qt.UserRole, object_id)
                group_item.setData(0, Qt.UserRole + 1, "group")
                group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
                group_item.setCheckState(0, Qt.Checked)
                group_item.setIcon(0, style.standardIcon(QStyle.SP_DirIcon))
                
                # Bold font for groups
                font = group_item.font(0)
                font.setBold(True)
                group_item.setFont(0, font)
                group_item.setForeground(0, QColor(70, 130, 180))
                
                self.tree.addTopLevelItem(group_item)
                
                for file_path, name, label_count in images:
                    # Show total labels on this image, not just for this object_id
                    total_labels = image_total_labels.get(file_path, label_count)
                    layer_item = QTreeWidgetItem()
                    layer_item.setText(0, f"{name} ({total_labels})")
                    layer_item.setData(0, Qt.UserRole, file_path)
                    layer_item.setData(0, Qt.UserRole + 1, "layer")
                    layer_item.setFlags(layer_item.flags() | Qt.ItemIsUserCheckable)
                    layer_item.setCheckState(0, Qt.Checked)
                    layer_item.setToolTip(0, file_path)
                    layer_item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
                    group_item.addChild(layer_item)
                
                group_item.setExpanded(True)
            else:
                # Single image - still create a group for consistency
                file_path, name, label_count = images[0]
                
                # Create group for this object_id
                group_item = QTreeWidgetItem()
                short_id = object_id[:8] + "..."  # Truncate UUID for display
                group_item.setText(0, f"Object: {short_id}")
                group_item.setData(0, Qt.UserRole, object_id)
                group_item.setData(0, Qt.UserRole + 1, "group")
                group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
                group_item.setCheckState(0, Qt.Checked)
                group_item.setIcon(0, style.standardIcon(QStyle.SP_DirIcon))
                
                # Use different color for single-image groups
                font = group_item.font(0)
                font.setBold(True)
                group_item.setFont(0, font)
                group_item.setForeground(0, QColor(100, 149, 237))  # Cornflower blue
                
                self.tree.addTopLevelItem(group_item)
                
                # Add the layer as child - show total labels on this image
                total_labels = image_total_labels.get(file_path, label_count)
                layer_item = QTreeWidgetItem()
                layer_item.setText(0, f"{name} ({total_labels})")
                layer_item.setData(0, Qt.UserRole, file_path)
                layer_item.setData(0, Qt.UserRole + 1, "layer")
                layer_item.setFlags(layer_item.flags() | Qt.ItemIsUserCheckable)
                layer_item.setCheckState(0, Qt.Checked)
                layer_item.setToolTip(0, file_path)
                layer_item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
                group_item.addChild(layer_item)
                
                group_item.setExpanded(True)
        
        self.tree.blockSignals(False)
    
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle item check state changes."""
        item_type = item.data(0, Qt.UserRole + 1)
        checked = item.checkState(0) == Qt.Checked
        
        if item_type == "layer":
            file_path = item.data(0, Qt.UserRole)
            layer_id = self._layer_id_map.get(file_path)
            if layer_id:
                self.layer_visibility_changed.emit(layer_id, checked)
        elif item_type == "group":
            # Toggle all children
            self.tree.blockSignals(True)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, item.checkState(0))
                # Also emit visibility change for each child
                file_path = child.data(0, Qt.UserRole)
                layer_id = self._layer_id_map.get(file_path)
                if layer_id:
                    self.layer_visibility_changed.emit(layer_id, checked)
            self.tree.blockSignals(False)
    
    def _show_context_menu(self, position):
        """Show right-click context menu."""
        item = self.tree.itemAt(position)
        if not item:
            return
        
        menu = QMenu()
        item_type = item.data(0, Qt.UserRole + 1)
        
        if item_type == "layer":
            # Zoom to layer
            zoom_action = menu.addAction("Zoom to Layer")
            file_path = item.data(0, Qt.UserRole)
            layer_id = self._layer_id_map.get(file_path)
            if layer_id:
                zoom_action.triggered.connect(lambda: self.zoom_to_layer_requested.emit(layer_id))
        elif item_type == "group":
            # Select/unselect all in group
            select_all_action = menu.addAction("Select all")
            select_all_action.triggered.connect(lambda: self._set_group_checked(item, True))
            
            unselect_all_action = menu.addAction("Unselect all")
            unselect_all_action.triggered.connect(lambda: self._set_group_checked(item, False))
        
        menu.exec_(self.tree.mapToGlobal(position))
    
    def _set_group_checked(self, item: QTreeWidgetItem, checked: bool):
        """Set check state for a group and all its children."""
        check_state = Qt.Checked if checked else Qt.Unchecked
        item.setCheckState(0, check_state)
        # Children will be updated by _on_item_changed
    
    def set_layer_checked(self, file_path: str, checked: bool):
        """Set the check state of a layer by file path without emitting signals.
        
        Args:
            file_path: The file path of the layer
            checked: True to check, False to uncheck
        """
        def find_and_set(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    if find_and_set(self.tree.topLevelItem(i)):
                        return True
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) == file_path:
                        self.tree.blockSignals(True)
                        parent.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                        self.tree.blockSignals(False)
                        return True
                elif item_type == "group":
                    for i in range(parent.childCount()):
                        if find_and_set(parent.child(i)):
                            return True
            return False
        
        find_and_set()
    
    def clear(self):
        """Clear all items from the tree."""
        self.tree.clear()


class CombinedLayerPanel(QWidget):
    """Combined panel with both the main layer panel and labeled images panel."""
    
    # Forward signals from main panel
    layer_visibility_changed = pyqtSignal(str, bool)
    layers_reordered = pyqtSignal(list)
    layer_group_changed = pyqtSignal(str, str)
    zoom_to_layer_requested = pyqtSignal(str)
    layer_removed = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self._syncing = False  # Prevent infinite recursion during sync
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the combined panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Vertical splitter for two panels
        splitter = QSplitter(Qt.Vertical)
        
        # Main layer panel
        self.main_panel = LayerPanel()
        splitter.addWidget(self.main_panel)
        
        # Labeled images panel
        self.labeled_panel = LabeledLayerPanel()
        splitter.addWidget(self.labeled_panel)
        
        # Set initial sizes (main panel takes more space)
        splitter.setSizes([400, 200])
        
        layout.addWidget(splitter)
        
        # Forward signals from main panel
        self.main_panel.layer_visibility_changed.connect(self._on_main_visibility_changed)
        self.main_panel.layers_reordered.connect(self.layers_reordered)
        self.main_panel.layer_group_changed.connect(self.layer_group_changed)
        self.main_panel.zoom_to_layer_requested.connect(self.zoom_to_layer_requested)
        self.main_panel.layer_removed.connect(self.layer_removed)
        
        # Forward signals from labeled panel
        self.labeled_panel.layer_visibility_changed.connect(self._on_labeled_visibility_changed)
        self.labeled_panel.zoom_to_layer_requested.connect(self.zoom_to_layer_requested)
    
    def _on_main_visibility_changed(self, layer_id: str, visible: bool):
        """Handle visibility change from main panel."""
        if self._syncing:
            return
        
        self._syncing = True
        
        # Emit the signal
        self.layer_visibility_changed.emit(layer_id, visible)
        
        # Find the file path for this layer_id and sync to labeled panel
        file_path = self._get_file_path_for_layer_id(layer_id)
        if file_path:
            self.labeled_panel.set_layer_checked(file_path, visible)
        
        self._syncing = False
    
    def _on_labeled_visibility_changed(self, layer_id: str, visible: bool):
        """Handle visibility change from labeled panel."""
        if self._syncing:
            return
        
        self._syncing = True
        
        # Emit the signal
        self.layer_visibility_changed.emit(layer_id, visible)
        
        # Sync to main panel
        self.main_panel.set_layer_checked(layer_id, visible)
        
        self._syncing = False
    
    def _get_file_path_for_layer_id(self, layer_id: str) -> str | None:
        """Find file path by layer ID from the labeled panel's map."""
        for file_path, lid in self.labeled_panel._layer_id_map.items():
            if lid == layer_id:
                return file_path
        return None
    
    # Delegate methods to main panel
    def add_layer(self, layer_id: str, file_path: str, parent: QTreeWidgetItem = None):
        """Add a layer item to the main tree."""
        self.main_panel.add_layer(layer_id, file_path, parent)
        # Register mapping in labeled panel
        self.labeled_panel.set_layer_id_map(file_path, layer_id)
    
    def add_group(self, name: str, parent: QTreeWidgetItem = None):
        """Add a group to the main tree."""
        return self.main_panel.add_group(name, parent)
    
    def uncheck_layers(self, layer_ids: list[str]):
        """Uncheck layers by their IDs in both panels."""
        self.main_panel.uncheck_layers(layer_ids)
        # Also update labeled panel
        for layer_id in layer_ids:
            file_path = self._get_file_path_for_layer_id(layer_id)
            if file_path:
                self.labeled_panel.set_layer_checked(file_path, False)
    
    def clear(self):
        """Clear all items from both trees."""
        self.main_panel.clear()
        self.labeled_panel.clear()
    
    def refresh_labeled_panel(self, project):
        """Refresh the labeled images panel with current project data."""
        self.labeled_panel.refresh(project)
    
    @property
    def tree(self):
        """Access the main panel's tree widget for compatibility."""
        return self.main_panel.tree
