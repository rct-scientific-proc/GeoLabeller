"""Layer panel for managing loaded layers and groups."""
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal


class LayerPanel(QWidget):
    """Panel for managing layers and groups."""
    
    # Signals
    layer_visibility_changed = pyqtSignal(str, bool)  # layer_id, visible
    layers_reordered = pyqtSignal(list)  # list of layer_ids
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Tree widget for layers and groups
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Layers")
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # Connect signals
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        
        layout.addWidget(self.tree)
    
    def add_layer(self, layer_id: str, file_path: str):
        """Add a layer item to the tree."""
        item = QTreeWidgetItem()
        item.setText(0, os.path.basename(file_path))
        item.setData(0, Qt.UserRole, layer_id)
        item.setData(0, Qt.UserRole + 1, "layer")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        item.setToolTip(0, file_path)
        
        self.tree.addTopLevelItem(item)
    
    def add_group(self, name: str):
        """Add a group to the tree."""
        item = QTreeWidgetItem()
        item.setText(0, name)
        item.setData(0, Qt.UserRole + 1, "group")
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        
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
        
        # If item selected, show remove option
        item = self.tree.itemAt(position)
        if item:
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
        
        # Get parent and remove
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
