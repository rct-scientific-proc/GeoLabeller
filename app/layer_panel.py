"""Layer panel for managing loaded layers and groups."""
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal


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
            
            menu.addSeparator()
            remove_action = menu.addAction("Remove")
            remove_action.triggered.connect(lambda: self._remove_item(item))
        
        menu.exec_(self.tree.mapToGlobal(position))
    
    def _create_group(self):
        """Create a new group via dialog."""
        name, ok = QInputDialog.getText(self, "New Group", "Group name:")
        if ok and name:
            self.add_group(name)
    
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
