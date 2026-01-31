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
    
    # Batch progress signals for group toggle operations
    batch_visibility_started = pyqtSignal(int)  # total items to process
    batch_visibility_progress = pyqtSignal(int)  # current progress
    batch_visibility_finished = pyqtSignal()  # batch complete
    
    def __init__(self):
        super().__init__()
        self._batch_mode = False  # When True, suppress signals during batch operations
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
        
        # Optimize tree widget for large datasets
        self.tree.setUniformRowHeights(True)  # Faster scrolling with many items
        
        # Connect signals
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.items_reordered.connect(self._on_rows_moved)
        
        layout.addWidget(self.tree)
    
    def begin_batch_update(self):
        """Begin a batch update - suppresses signals and tree updates.
        
        Call this before adding many items, then call end_batch_update() when done.
        """
        self._batch_mode = True
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
    
    def end_batch_update(self):
        """End a batch update - re-enables signals and refreshes the tree."""
        self._batch_mode = False
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)
        self.tree.update()
    
    def add_layer(self, layer_id: str, file_path: str, parent: QTreeWidgetItem = None, visible: bool = True):
        """Add a layer item to the tree.
        
        Args:
            layer_id: Unique identifier for the layer
            file_path: Path to the GeoTIFF file
            parent: Optional parent group item. If None, adds to top level.
            visible: Whether the layer should be visible (checked) initially.
        """
        item = QTreeWidgetItem()
        item.setText(0, os.path.basename(file_path))
        item.setData(0, Qt.UserRole, layer_id)
        item.setData(0, Qt.UserRole + 1, "layer")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if visible else Qt.Unchecked)
        item.setToolTip(0, file_path)
        
        # Set image icon for layers
        style = QApplication.style()
        item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
        
        if parent:
            parent.addChild(item)
        else:
            self.tree.addTopLevelItem(item)
    
    def add_group(self, name: str, parent: QTreeWidgetItem = None, visible: bool = True):
        """Add a group to the tree.
        
        Args:
            name: Display name for the group
            parent: Optional parent group item. If None, adds to top level.
            visible: Whether the group should be visible (checked) initially.
        """
        item = QTreeWidgetItem()
        item.setText(0, name)
        item.setData(0, Qt.UserRole + 1, "group")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked if visible else Qt.Unchecked)
        
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
            # Count all descendant layers for progress tracking
            layer_count = self._count_descendant_layers(item)
            use_progress = layer_count >= 10  # Only show progress for 10+ items
            
            if use_progress:
                self.batch_visibility_started.emit(layer_count)
            
            # Toggle all children with progress tracking
            self._toggle_group_children(item, checked, use_progress)
            
            if use_progress:
                self.batch_visibility_finished.emit()
    
    def _count_descendant_layers(self, item: QTreeWidgetItem) -> int:
        """Count all layer items that are descendants of this item."""
        count = 0
        for i in range(item.childCount()):
            child = item.child(i)
            child_type = child.data(0, Qt.UserRole + 1)
            if child_type == "layer":
                count += 1
            elif child_type == "group":
                count += self._count_descendant_layers(child)
        return count
    
    def _toggle_group_children(self, item: QTreeWidgetItem, checked: bool, emit_progress: bool):
        """Toggle all children of a group item, optionally emitting progress."""
        check_state = Qt.Checked if checked else Qt.Unchecked
        progress_count = [0]  # Use list for mutable closure
        
        def process_item(parent: QTreeWidgetItem):
            for i in range(parent.childCount()):
                child = parent.child(i)
                child.setCheckState(0, check_state)
                child_type = child.data(0, Qt.UserRole + 1)
                
                if child_type == "layer":
                    progress_count[0] += 1
                    if emit_progress:
                        self.batch_visibility_progress.emit(progress_count[0])
                        # Allow UI to update periodically
                        if progress_count[0] % 5 == 0:
                            QApplication.processEvents()
                elif child_type == "group":
                    process_item(child)
        
        process_item(item)
    
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
        
        # If multiple items are selected, offer batch visibility toggles
        selected = self.tree.selectedItems()
        if len(selected) > 1:
            # Collect only layer IDs from selection
            layer_ids = [it.data(0, Qt.UserRole) for it in selected if it.data(0, Qt.UserRole + 1) == "layer"]
            if layer_ids:
                menu.addSeparator()
                turn_on_action = menu.addAction("Turn on layers")
                turn_on_action.triggered.connect(lambda _, ids=layer_ids: self.check_layers(ids))

                turn_off_action = menu.addAction("Turn off layers")
                turn_off_action.triggered.connect(lambda _, ids=layer_ids: self.uncheck_layers(ids))

                menu.addSeparator()
                remove_action = menu.addAction("Remove")
                remove_action.triggered.connect(lambda _, items=selected: [self._remove_item(it) for it in items])

                menu.exec_(self.tree.mapToGlobal(position))
                return

        # If single item under cursor, show options for that item
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
    
    def check_layers(self, layer_ids: list[str]):
        """Check (show) layers by their IDs.
        
        Args:
            layer_ids: List of layer IDs to check
        """
        def find_and_check(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_check(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) in layer_ids:
                        parent.setCheckState(0, Qt.Checked)
                else:
                    for i in range(parent.childCount()):
                        find_and_check(parent.child(i))
        
        find_and_check()
    
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
    
    def toggle_layer_visibility(self, layer_id: str):
        """Toggle the visibility of a layer by its ID.
        
        Args:
            layer_id: The layer ID to toggle
        """
        def find_and_toggle(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    if find_and_toggle(self.tree.topLevelItem(i)):
                        return True
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) == layer_id:
                        # Toggle the check state
                        current_state = parent.checkState(0)
                        new_state = Qt.Unchecked if current_state == Qt.Checked else Qt.Checked
                        parent.setCheckState(0, new_state)
                        return True
                else:
                    for i in range(parent.childCount()):
                        if find_and_toggle(parent.child(i)):
                            return True
            return False
        
        find_and_toggle()


class LabeledLayerPanel(QWidget):
    """Panel showing labels grouped by object_id, with individual label entries."""
    
    # Signals
    layer_visibility_changed = pyqtSignal(str, bool)  # layer_id, visible
    zoom_to_layer_requested = pyqtSignal(str)  # layer_id
    zoom_to_label_requested = pyqtSignal(float, float)  # lon, lat - zoom to specific coordinates
    
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
        """Refresh the tree with current labels from the project.
        
        Args:
            project: The LabelProject containing images and labels
        """
        self.tree.blockSignals(True)
        self.tree.clear()
        
        # Group labels by object_id
        # object_id -> list of (label_id, image_name, image_path, lon, lat, class_name)
        object_groups: dict[str, list[tuple[int, str, str, float, float, str]]] = {}
        
        for image in project.images.values():
            if not image.labels:
                continue
            
            for label in image.labels:
                object_id = label.object_id
                if object_id not in object_groups:
                    object_groups[object_id] = []
                
                object_groups[object_id].append((
                    label.id,
                    image.name,
                    image.path,
                    label.lon,
                    label.lat,
                    label.class_name
                ))
        
        # Create tree items
        style = QApplication.style()
        
        for object_id, labels in object_groups.items():
            # Create group for this object_id
            group_item = QTreeWidgetItem()
            short_id = object_id[:8] + "..."  # Truncate UUID for display
            label_count = len(labels)
            group_item.setText(0, f"Object: {short_id} ({label_count})")
            group_item.setData(0, Qt.UserRole, object_id)
            group_item.setData(0, Qt.UserRole + 1, "group")
            group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
            group_item.setCheckState(0, Qt.Checked)
            group_item.setIcon(0, style.standardIcon(QStyle.SP_DirIcon))
            
            # Bold font for groups, different colors based on link status
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            if label_count > 1:
                group_item.setForeground(0, QColor(70, 130, 180))  # Steel blue for linked
            else:
                group_item.setForeground(0, QColor(100, 149, 237))  # Cornflower blue for single
            
            self.tree.addTopLevelItem(group_item)
            
            # Add each label as a child
            for label_id, image_name, file_path, lon, lat, class_name in labels:
                label_item = QTreeWidgetItem()
                label_item.setText(0, f"#{label_id}: {image_name} [{class_name}]")
                label_item.setData(0, Qt.UserRole, file_path)
                label_item.setData(0, Qt.UserRole + 1, "label")
                label_item.setData(0, Qt.UserRole + 2, label_id)
                label_item.setData(0, Qt.UserRole + 3, lon)
                label_item.setData(0, Qt.UserRole + 4, lat)
                label_item.setFlags(label_item.flags() | Qt.ItemIsUserCheckable)
                label_item.setCheckState(0, Qt.Checked)
                label_item.setToolTip(0, f"Label #{label_id} on {file_path}\nLon: {lon:.6f}, Lat: {lat:.6f}")
                label_item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
                group_item.addChild(label_item)
            
            group_item.setExpanded(True)
        
        self.tree.blockSignals(False)
    
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle item check state changes."""
        item_type = item.data(0, Qt.UserRole + 1)
        checked = item.checkState(0) == Qt.Checked
        
        if item_type == "label":
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
        
        if item_type == "label":
            # Get stored data
            file_path = item.data(0, Qt.UserRole)
            lon = item.data(0, Qt.UserRole + 3)
            lat = item.data(0, Qt.UserRole + 4)
            
            # Zoom to label (specific coordinates)
            zoom_label_action = menu.addAction("Zoom to Label")
            zoom_label_action.triggered.connect(lambda: self.zoom_to_label_requested.emit(lon, lat))
            
            # Zoom to layer
            layer_id = self._layer_id_map.get(file_path)
            if layer_id:
                zoom_layer_action = menu.addAction("Zoom to Layer")
                zoom_layer_action.triggered.connect(lambda: self.zoom_to_layer_requested.emit(layer_id))
        elif item_type == "group":
            # Zoom to first label in this group
            if item.childCount() > 0:
                first_child = item.child(0)
                lon = first_child.data(0, Qt.UserRole + 3)
                lat = first_child.data(0, Qt.UserRole + 4)
                zoom_label_action = menu.addAction("Zoom to Label")
                zoom_label_action.triggered.connect(lambda: self.zoom_to_label_requested.emit(lon, lat))
            
            menu.addSeparator()
            
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
        """Set the check state of labels for a file path without emitting signals.
        
        Args:
            file_path: The file path of the layer
            checked: True to check, False to uncheck
        """
        def find_and_set(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_set(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "label":
                    if parent.data(0, Qt.UserRole) == file_path:
                        self.tree.blockSignals(True)
                        parent.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                        self.tree.blockSignals(False)
                elif item_type == "group":
                    for i in range(parent.childCount()):
                        find_and_set(parent.child(i))
        
        find_and_set()
    
    def toggle_layer_checked(self, file_path: str):
        """Toggle the check state of labels for a file path.
        
        Args:
            file_path: The file path of the layer to toggle
        """
        def find_and_toggle(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_toggle(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "label":
                    if parent.data(0, Qt.UserRole) == file_path:
                        self.tree.blockSignals(True)
                        current_state = parent.checkState(0)
                        new_state = Qt.Unchecked if current_state == Qt.Checked else Qt.Checked
                        parent.setCheckState(0, new_state)
                        self.tree.blockSignals(False)
                elif item_type == "group":
                    for i in range(parent.childCount()):
                        find_and_toggle(parent.child(i))
        
        find_and_toggle()
    
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
    zoom_to_label_requested = pyqtSignal(float, float)  # lon, lat
    layer_removed = pyqtSignal(str)
    
    # Batch progress signals
    batch_visibility_started = pyqtSignal(int)  # total items
    batch_visibility_progress = pyqtSignal(int)  # current progress
    batch_visibility_finished = pyqtSignal()
    
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
        
        # Forward batch progress signals
        self.main_panel.batch_visibility_started.connect(self.batch_visibility_started)
        self.main_panel.batch_visibility_progress.connect(self.batch_visibility_progress)
        self.main_panel.batch_visibility_finished.connect(self.batch_visibility_finished)
        
        # Forward signals from labeled panel
        self.labeled_panel.layer_visibility_changed.connect(self._on_labeled_visibility_changed)
        self.labeled_panel.zoom_to_layer_requested.connect(self.zoom_to_layer_requested)
        self.labeled_panel.zoom_to_label_requested.connect(self.zoom_to_label_requested)
    
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
        
        # Also sync other labels on the same image in the labeled panel
        # (e.g., if image has 3 labels and user unchecks one, uncheck the others too)
        file_path = self._get_file_path_for_layer_id(layer_id)
        if file_path:
            self.labeled_panel.set_layer_checked(file_path, visible)
        
        self._syncing = False
    
    def _get_file_path_for_layer_id(self, layer_id: str) -> str | None:
        """Find file path by layer ID from the labeled panel's map."""
        for file_path, lid in self.labeled_panel._layer_id_map.items():
            if lid == layer_id:
                return file_path
        return None
    
    # Delegate methods to main panel
    def add_layer(self, layer_id: str, file_path: str, parent: QTreeWidgetItem = None, visible: bool = True):
        """Add a layer item to the main tree.
        
        Args:
            layer_id: Unique identifier for the layer
            file_path: Path to the GeoTIFF file
            parent: Optional parent group item
            visible: Whether the layer should be visible (checked) initially
        """
        self.main_panel.add_layer(layer_id, file_path, parent, visible)
        # Register mapping in labeled panel
        self.labeled_panel.set_layer_id_map(file_path, layer_id)
    
    def add_group(self, name: str, parent: QTreeWidgetItem = None, visible: bool = True):
        """Add a group to the main tree.
        
        Args:
            name: Display name for the group
            parent: Optional parent group item
            visible: Whether the group should be visible (checked) initially
        """
        return self.main_panel.add_group(name, parent, visible)
    
    def begin_batch_update(self):
        """Begin a batch update - suppresses signals and tree updates."""
        self.main_panel.begin_batch_update()
    
    def end_batch_update(self):
        """End a batch update - re-enables signals and refreshes the tree."""
        self.main_panel.end_batch_update()
    
    def uncheck_layers(self, layer_ids: list[str]):
        """Uncheck layers by their IDs in both panels."""
        self.main_panel.uncheck_layers(layer_ids)
        # Also update labeled panel
        for layer_id in layer_ids:
            file_path = self._get_file_path_for_layer_id(layer_id)
            if file_path:
                self.labeled_panel.set_layer_checked(file_path, False)
    
    def check_layers(self, layer_ids: list[str]):
        """Check layers by their IDs in both panels."""
        self.main_panel.check_layers(layer_ids)
        # Also update labeled panel
        for layer_id in layer_ids:
            file_path = self._get_file_path_for_layer_id(layer_id)
            if file_path:
                self.labeled_panel.set_layer_checked(file_path, True)
    
    def toggle_layer_visibility(self, layer_id: str):
        """Toggle the visibility of a layer by its ID in both panels."""
        self.main_panel.toggle_layer_visibility(layer_id)
        # Also update labeled panel
        file_path = self._get_file_path_for_layer_id(layer_id)
        if file_path:
            # We need to get the new state after toggle - check the main panel
            # The toggle already happened, so we sync the labeled panel
            self.labeled_panel.toggle_layer_checked(file_path)
    
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
